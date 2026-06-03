#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODEL_MATRIX="${VLLM_MODEL_MATRIX:-tools/vllm_models_v5.tsv}"
MODEL_ALIAS="${1:-${VLLM_MODEL_ALIAS:-qwen25_14b_awq}}"
RUN_MODE="${RUN_MODE:-full}"
PORT="${VLLM_PORT:-8000}"
LLM_BASE_URL="${LLM_BASE_URL:-http://localhost:${PORT}/v1}"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

section() {
  echo
  echo "[$(timestamp)] ==== $* ===="
}

fail() {
  echo "[$(timestamp)] ERROR: $*" >&2
  exit 1
}

lookup_model() {
  awk -F '\t' -v alias="$MODEL_ALIAS" '
    $0 !~ /^#/ && NF >= 4 && $1 == alias { print $0; found=1 }
    END { if (!found) exit 1 }
  ' "$MODEL_MATRIX"
}

line="$(lookup_model)" || {
  echo "Unknown VLLM_MODEL_ALIAS: $MODEL_ALIAS" >&2
  awk -F '\t' '$0 !~ /^#/ && NF >= 4 { print "  " $1 " -> " $2 }' "$MODEL_MATRIX" >&2
  exit 2
}
IFS=$'\t' read -r _alias model_id _max_len _extra_args <<< "$line"

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export LLM_PROVIDER="${LLM_PROVIDER:-vllm}"
export LLM_BASE_URL
export LLM_MODEL="${LLM_MODEL:-$model_id}"
export LLM_TIMEOUT_SEC="${LLM_TIMEOUT_SEC:-300}"
export OLLAMA_MODEL="$LLM_MODEL"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"

export EXTERNAL_GUARDRAIL_PROVIDER="${EXTERNAL_GUARDRAIL_PROVIDER:-meta_prompt_guard}"
export EXTERNAL_GUARDRAIL_STAGES="${EXTERNAL_GUARDRAIL_STAGES:-context}"
export EXTERNAL_GUARDRAIL_ACTION="${EXTERNAL_GUARDRAIL_ACTION:-block}"
export EXTERNAL_GUARDRAIL_FAIL_MODE="${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}"

export SEMANTIC_DETECTOR_MODE="${SEMANTIC_DETECTOR_MODE:-improved}"
export SEMANTIC_DETECTOR_BACKEND="${SEMANTIC_DETECTOR_BACKEND:-auto}"
export SEMANTIC_DETECTOR_IMPROVED_THRESHOLD="${SEMANTIC_DETECTOR_IMPROVED_THRESHOLD:-0.35}"
export SEMANTIC_DETECTOR_WINDOW_ENABLED="${SEMANTIC_DETECTOR_WINDOW_ENABLED:-true}"
export SEMANTIC_DETECTOR_WINDOW_RADIUS="${SEMANTIC_DETECTOR_WINDOW_RADIUS:-1}"
export SEMANTIC_DETECTOR_VERDICTS="${SEMANTIC_DETECTOR_VERDICTS:-muted_candidate}"

if [[ "${SKIP_LLM_PROBE:-false}" =~ ^(0|false|no|off)$ ]]; then
  section "probe ${LLM_MODEL}"
  python tools/check_llm_endpoint.py --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" --timeout 30
fi

profile="rerank_off"
base_out="outputs/experiments_v5/vllm/${MODEL_ALIAS}"
mode_suffix="$RUN_MODE"

section "A ${RUN_MODE} (${MODEL_ALIAS})"
A_RUN_MODE="$RUN_MODE" \
A_EXPERIMENTS_ROOT=data/experiments_v5 \
A_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
A_OUTPUT_ROOT="${base_out}/A_${mode_suffix}_${profile}" \
A_ENABLE_RERANK=false \
A_ENABLE_DENSE=true \
A_REBUILD_INDEX=true \
bash experiment_A/run_experiment_A_v5.sh

A_RUN_DIR="$(ls -td "${base_out}/A_${mode_suffix}_${profile}"/v3_* 2>/dev/null | head -1 || true)"
[[ -n "$A_RUN_DIR" ]] || fail "A output directory not found"

section "A behavioural evaluation (${MODEL_ALIAS})"
python scripts/eval_attack_success_v5.py --run-dir "$A_RUN_DIR"

section "C ${RUN_MODE} muted only (${MODEL_ALIAS})"
C2_RUN_MODE="$RUN_MODE" \
C2_EXPERIMENTS_ROOT=data/experiments_v5 \
C2_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
C2_OUTPUT_ROOT="${base_out}/C_${mode_suffix}_${profile}" \
C2_ENABLE_RERANK=false \
C2_REBUILD_INDEX=true \
C2_CONDITIONS=normal_on,muted_blackbox_on,muted_whitebox_on \
bash experiment_C/experiments/run_study_c2_v5.sh

C_RUN_DIR="$(ls -td "${base_out}/C_${mode_suffix}_${profile}"/study_c2_v5_* 2>/dev/null | head -1 || true)"
[[ -n "$C_RUN_DIR" ]] || fail "C output directory not found"
C_DETECTED_CASES="$C_RUN_DIR/detected_cases.csv"
[[ -s "$C_DETECTED_CASES" ]] || fail "C detected cases not found or empty: $C_DETECTED_CASES"

section "D ${RUN_MODE} using ${C_DETECTED_CASES} (${MODEL_ALIAS})"
D_RUN_MODE="$RUN_MODE" \
D_EXPERIMENTS_ROOT=data/experiments_v5 \
D_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
D_OUTPUT_ROOT="${base_out}/D_${mode_suffix}_${profile}" \
D_ENABLE_RERANK=false \
D_REBUILD_INDEX=true \
D_CONDITIONS=muted_blackbox_on,muted_whitebox_on \
D_DETECTED_CASES="$C_DETECTED_CASES" \
bash experiment_D/experiments/run_study_d_v5.sh

section "done ${MODEL_ALIAS}"
echo "A latest: $A_RUN_DIR"
echo "C latest: $C_RUN_DIR"
echo "D latest: $(ls -td "${base_out}/D_${mode_suffix}_${profile}"/study_d_v5_* 2>/dev/null | head -1 || true)"
