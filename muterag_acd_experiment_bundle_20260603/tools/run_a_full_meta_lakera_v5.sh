#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

COMPARE_GROUPS="${A_COMPARE_GROUPS:-common_off,meta_on,lakera_on}"

if [[ ",${COMPARE_GROUPS}," == *",lakera_on,"* && -z "${EXTERNAL_GUARDRAIL_API_KEY:-}" ]]; then
  echo "[A full comparison] EXTERNAL_GUARDRAIL_API_KEY is missing."
  echo "[A full comparison] Export the Lakera API key in this shell before starting the runner."
  exit 1
fi

mkdir -p logs
export HF_HOME="${HF_HOME:-$ROOT_DIR/.cache/huggingface}"
mkdir -p "$HF_HOME"

STAMP="$(date '+%Y%m%d_%H%M%S')"
BASE_OUT="${A_COMPARE_OUTPUT_ROOT:-outputs/experiments_v5/A_full_compare_${STAMP}}"

export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:12b}"
export EXTERNAL_GUARDRAIL_STAGES="${EXTERNAL_GUARDRAIL_STAGES:-context}"
export EXTERNAL_GUARDRAIL_ACTION="${EXTERNAL_GUARDRAIL_ACTION:-block}"
export EXTERNAL_GUARDRAIL_FAIL_MODE="${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}"
export EXTERNAL_GUARDRAIL_TIMEOUT_SEC="${EXTERNAL_GUARDRAIL_TIMEOUT_SEC:-30}"
export A_PIN_CONDITIONS="${A_PIN_CONDITIONS:-direct_blackbox,direct_whitebox}"

COMMON_OFF="normal_off,direct_blackbox_off,direct_whitebox_off,muted_blackbox_off,muted_whitebox_off,direct_blackbox_ignore_off,direct_whitebox_ignore_off"
GUARDRAIL_ON="normal_on,direct_blackbox_on,direct_whitebox_on,muted_blackbox_on,muted_whitebox_on,direct_blackbox_ignore_on,direct_whitebox_ignore_on"

run_group() {
  local label="$1"
  local provider="$2"
  local conditions="$3"
  local out_root="${BASE_OUT}/${label}"

  echo
  echo "[A full comparison] group=${label}"
  echo "[A full comparison] provider=${provider}"
  echo "[A full comparison] output_root=${out_root}"
  echo "[A full comparison] started=$(date --iso-8601=seconds)"

  export EXTERNAL_GUARDRAIL_PROVIDER="$provider"

  A_RUN_MODE=full \
  A_EXPERIMENTS_ROOT=data/experiments_v5 \
  A_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
  A_OUTPUT_ROOT="$out_root" \
  A_CONDITIONS="$conditions" \
  A_ENABLE_DENSE=true \
  A_ENABLE_RERANK=false \
  A_REBUILD_INDEX=true \
  A_MAX_QUESTIONS=50 \
  EXTERNAL_GUARDRAIL_PROVIDER="$provider" \
  bash experiment_A/run_experiment_A_v5.sh

  local run_dir
  run_dir="$(ls -td "${out_root}"/v3_* | head -1)"

  python - "$run_dir" "$provider" "$OLLAMA_MODEL" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
expected_provider = sys.argv[2]
expected_model = sys.argv[3]
config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
actual_provider = config.get("guardrail_provider")
actual_model = config.get("ollama_model")
if actual_provider != expected_provider:
    raise SystemExit(
        f"[A full comparison] provider mismatch: expected={expected_provider} actual={actual_provider}"
    )
if actual_model != expected_model:
    raise SystemExit(
        f"[A full comparison] model mismatch: expected={expected_model} actual={actual_model}"
    )
print(f"[A full comparison] verified provider={actual_provider} model={actual_model}")
PY

  python scripts/eval_attack_success_v5.py --run-dir "$run_dir"
  tar -czf "${run_dir}_share.tgz" -C "$(dirname "$run_dir")" "$(basename "$run_dir")"

  echo "[A full comparison] group_complete=${label}"
  echo "[A full comparison] run_dir=${run_dir}"
  echo "[A full comparison] behavioural_summary=${run_dir}/attack_success/attack_success_summary.csv"
  echo "[A full comparison] share_archive=${run_dir}_share.tgz"
}

echo "[A full comparison] started=$(date --iso-8601=seconds)"
echo "[A full comparison] model=${OLLAMA_MODEL}"
echo "[A full comparison] base_output=${BASE_OUT}"
echo "[A full comparison] matrix=${COMPARE_GROUPS}"
echo "[A full comparison] questions_per_corpus=50"
echo "[A full comparison] rerank=false"

[[ ",${COMPARE_GROUPS}," == *",common_off,"* ]] && run_group "common_off" "meta_prompt_guard" "$COMMON_OFF"
[[ ",${COMPARE_GROUPS}," == *",meta_on,"* ]] && run_group "meta_on" "meta_prompt_guard" "$GUARDRAIL_ON"
[[ ",${COMPARE_GROUPS}," == *",lakera_on,"* ]] && run_group "lakera_on" "lakera" "$GUARDRAIL_ON"

echo
echo "[A full comparison] complete=$(date --iso-8601=seconds)"
echo "[A full comparison] base_output=${BASE_OUT}"
