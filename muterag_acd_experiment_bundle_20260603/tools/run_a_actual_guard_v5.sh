#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

mkdir -p logs
export HF_HOME="${HF_HOME:-$ROOT_DIR/.cache/huggingface}"
mkdir -p "$HF_HOME"

export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:4b}"
export LLAMA_STACK_HOST="${LLAMA_STACK_HOST:-127.0.0.1}"
export LLAMA_STACK_PORT="${LLAMA_STACK_PORT:-8191}"
export EXTERNAL_GUARDRAIL_API_URL="${EXTERNAL_GUARDRAIL_API_URL:-http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/check}"
export EXTERNAL_GUARDRAIL_STAGES="${EXTERNAL_GUARDRAIL_STAGES:-context}"
export EXTERNAL_GUARDRAIL_ACTION="${EXTERNAL_GUARDRAIL_ACTION:-block}"
export EXTERNAL_GUARDRAIL_FAIL_MODE="${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}"
export EXTERNAL_GUARDRAIL_TIMEOUT_SEC="${EXTERNAL_GUARDRAIL_TIMEOUT_SEC:-300}"
export A_PIN_CONDITIONS="${A_PIN_CONDITIONS:-direct_blackbox,direct_whitebox}"

RUN_MODE="${A_RUN_MODE:-smoke}"
case "$RUN_MODE" in
  smoke)
    MAX_QUESTIONS="${A_MAX_QUESTIONS:-5}"
    SMOKE_CASES="${A_SMOKE_CASES:-5}"
    ;;
  full)
    MAX_QUESTIONS="${A_MAX_QUESTIONS:-50}"
    SMOKE_CASES=""
    ;;
  *)
    echo "[A actual guard] A_RUN_MODE must be smoke or full" >&2
    exit 2
    ;;
esac

if ! curl -fsS "http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/health" >/dev/null; then
  echo "[A actual guard] llama guard stack is not running." >&2
  echo "[A actual guard] Start it first: bash tools/start_llama_guard_stack.sh" >&2
  exit 1
fi

STAMP="$(date '+%Y%m%d_%H%M%S')"
BASE_OUT="${A_ACTUAL_GUARD_OUTPUT_ROOT:-outputs/experiments_v5/A_actual_guard_${RUN_MODE}_${STAMP}}"
COMPARE_GROUPS="${A_ACTUAL_GUARD_GROUPS:-common_off,stack_on}"

COMMON_OFF="normal_off,direct_blackbox_off,direct_whitebox_off,muted_blackbox_off,muted_whitebox_off,direct_blackbox_ignore_off,direct_whitebox_ignore_off"
STACK_ON="normal_on,direct_blackbox_on,direct_whitebox_on,muted_blackbox_on,muted_whitebox_on,direct_blackbox_ignore_on,direct_whitebox_ignore_on"

run_group() {
  local label="$1"
  local provider="$2"
  local conditions="$3"
  local out_root="${BASE_OUT}/${label}"

  echo
  echo "[A actual guard] group=${label}"
  echo "[A actual guard] provider=${provider}"
  echo "[A actual guard] output_root=${out_root}"
  echo "[A actual guard] started=$(date --iso-8601=seconds)"

  local args=(
    A_RUN_MODE="$RUN_MODE"
    A_EXPERIMENTS_ROOT=data/experiments_v5
    A_QUESTIONS=data/experiments_v5/questions/v5_questions.csv
    A_OUTPUT_ROOT="$out_root"
    A_CONDITIONS="$conditions"
    A_ENABLE_DENSE=true
    A_ENABLE_RERANK=false
    A_REBUILD_INDEX=true
    A_MAX_QUESTIONS="$MAX_QUESTIONS"
    EXTERNAL_GUARDRAIL_PROVIDER="$provider"
  )
  if [[ -n "$SMOKE_CASES" ]]; then
    args+=(A_SMOKE_CASES="$SMOKE_CASES")
  fi
  env "${args[@]}" bash experiment_A/run_experiment_A_v5.sh

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
    raise SystemExit(f"provider mismatch: expected={expected_provider} actual={actual_provider}")
if actual_model != expected_model:
    raise SystemExit(f"model mismatch: expected={expected_model} actual={actual_model}")
print(f"[A actual guard] verified provider={actual_provider} model={actual_model}")
PY

  python scripts/eval_attack_success_v5.py --run-dir "$run_dir"
  tar -czf "${run_dir}_share.tgz" -C "$(dirname "$run_dir")" "$(basename "$run_dir")"

  echo "[A actual guard] group_complete=${label}"
  echo "[A actual guard] run_dir=${run_dir}"
  echo "[A actual guard] behavioural_summary=${run_dir}/attack_success/attack_success_summary.csv"
  echo "[A actual guard] share_archive=${run_dir}_share.tgz"
}

echo "[A actual guard] started=$(date --iso-8601=seconds)"
echo "[A actual guard] model=${OLLAMA_MODEL}"
echo "[A actual guard] guardrail_url=${EXTERNAL_GUARDRAIL_API_URL}"
echo "[A actual guard] run_mode=${RUN_MODE}"
echo "[A actual guard] matrix=${COMPARE_GROUPS}"
echo "[A actual guard] questions_per_corpus=${MAX_QUESTIONS}"
echo "[A actual guard] rerank=false"
echo "[A actual guard] pinned_conditions=${A_PIN_CONDITIONS}"

[[ ",${COMPARE_GROUPS}," == *",common_off,"* ]] && run_group "common_off" "llama_guard_stack" "$COMMON_OFF"
[[ ",${COMPARE_GROUPS}," == *",stack_on,"* ]] && run_group "stack_on" "llama_guard_stack" "$STACK_ON"

echo
echo "[A actual guard] complete=$(date --iso-8601=seconds)"
echo "[A actual guard] base_output=${BASE_OUT}"
