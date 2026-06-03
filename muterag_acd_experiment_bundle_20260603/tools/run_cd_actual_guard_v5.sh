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
export EXTERNAL_GUARDRAIL_PROVIDER="llama_guard_stack"
export EXTERNAL_GUARDRAIL_API_URL="${EXTERNAL_GUARDRAIL_API_URL:-http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/check}"
export EXTERNAL_GUARDRAIL_STAGES="${EXTERNAL_GUARDRAIL_STAGES:-context}"
export EXTERNAL_GUARDRAIL_ACTION="${EXTERNAL_GUARDRAIL_ACTION:-block}"
export EXTERNAL_GUARDRAIL_FAIL_MODE="${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}"
export EXTERNAL_GUARDRAIL_TIMEOUT_SEC="${EXTERNAL_GUARDRAIL_TIMEOUT_SEC:-300}"

export SEMANTIC_DETECTOR_MODE="${SEMANTIC_DETECTOR_MODE:-improved}"
export SEMANTIC_DETECTOR_BACKEND="${SEMANTIC_DETECTOR_BACKEND:-auto}"
export SEMANTIC_DETECTOR_IMPROVED_THRESHOLD="${SEMANTIC_DETECTOR_IMPROVED_THRESHOLD:-0.35}"
export SEMANTIC_DETECTOR_WINDOW_ENABLED="${SEMANTIC_DETECTOR_WINDOW_ENABLED:-true}"
export SEMANTIC_DETECTOR_WINDOW_RADIUS="${SEMANTIC_DETECTOR_WINDOW_RADIUS:-1}"
export SEMANTIC_DETECTOR_VERDICTS="${SEMANTIC_DETECTOR_VERDICTS:-muted_candidate}"

RUN_MODE="${CD_RUN_MODE:-smoke}"
case "$RUN_MODE" in
  smoke)
    MAX_QUESTIONS="${CD_MAX_QUESTIONS:-5}"
    SMOKE_CASES="${CD_SMOKE_CASES:-5}"
    C_CONDITIONS="${C2_CONDITIONS:-muted_whitebox_on}"
    D_CONDITIONS_VALUE="${D_CONDITIONS:-muted_whitebox_on}"
    ;;
  full)
    MAX_QUESTIONS="${CD_MAX_QUESTIONS:-50}"
    SMOKE_CASES=""
    C_CONDITIONS="${C2_CONDITIONS:-normal_on,muted_blackbox_on,muted_whitebox_on}"
    D_CONDITIONS_VALUE="${D_CONDITIONS:-muted_blackbox_on,muted_whitebox_on}"
    ;;
  *)
    echo "[CD actual guard] CD_RUN_MODE must be smoke or full" >&2
    exit 2
    ;;
esac

if ! curl -fsS "http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/health" >/dev/null; then
  echo "[CD actual guard] llama guard stack is not running." >&2
  echo "[CD actual guard] Start it first: bash tools/start_llama_guard_stack.sh" >&2
  exit 1
fi

STAMP="$(date '+%Y%m%d_%H%M%S')"
BASE_OUT="${CD_ACTUAL_GUARD_OUTPUT_ROOT:-outputs/experiments_v5/CD_actual_guard_${RUN_MODE}_${STAMP}}"
C_OUT="${BASE_OUT}/C"
D_OUT="${BASE_OUT}/D"
C_RUN_ID="study_c2_actual_guard_${RUN_MODE}_${STAMP}"
D_RUN_ID="study_d_actual_guard_${RUN_MODE}_${STAMP}"

echo "[CD actual guard] started=$(date --iso-8601=seconds)"
echo "[CD actual guard] model=${OLLAMA_MODEL}"
echo "[CD actual guard] guardrail_url=${EXTERNAL_GUARDRAIL_API_URL}"
echo "[CD actual guard] run_mode=${RUN_MODE}"
echo "[CD actual guard] rerank=false"
echo "[CD actual guard] C_conditions=${C_CONDITIONS}"
echo "[CD actual guard] C_actions=off,log_only,drop_chunk"
echo "[CD actual guard] questions_per_condition=${MAX_QUESTIONS}"
if [[ "$RUN_MODE" == "smoke" ]]; then
  echo "[CD actual guard] C_smoke_matrix=1 condition x 3 actions x 5 questions = 15 cases"
fi

C_ARGS=(
  C2_RUN_ID="$C_RUN_ID"
  C2_RUN_MODE="$RUN_MODE"
  C2_EXPERIMENTS_ROOT=data/experiments_v5
  C2_QUESTIONS=data/experiments_v5/questions/v5_questions.csv
  C2_OUTPUT_ROOT="$C_OUT"
  C2_CONDITIONS="$C_CONDITIONS"
  C2_SEMANTIC_ACTIONS=off,log_only,drop_chunk
  C2_ENABLE_DENSE=true
  C2_ENABLE_RERANK=false
  C2_REBUILD_INDEX=true
  C2_MAX_QUESTIONS="$MAX_QUESTIONS"
)
if [[ -n "$SMOKE_CASES" ]]; then
  C_ARGS+=(C2_SMOKE_CASES="$SMOKE_CASES")
fi

echo
echo "[CD actual guard] C started=$(date --iso-8601=seconds)"
env "${C_ARGS[@]}" bash experiment_C/experiments/run_study_c2_v5.sh

C_RUN_DIR="${C_OUT}/${C_RUN_ID}"
C_DETECTED_CASES="${C_RUN_DIR}/detected_cases.csv"
[[ -f "$C_DETECTED_CASES" ]] || {
  echo "[CD actual guard] missing C manifest: ${C_DETECTED_CASES}" >&2
  exit 1
}

DETECTED_COUNT="$(
  python - "$C_DETECTED_CASES" <<'PY'
import csv
import sys

with open(sys.argv[1], encoding="utf-8-sig", newline="") as handle:
    print(sum(1 for _ in csv.DictReader(handle)))
PY
)"
echo "[CD actual guard] C complete=$(date --iso-8601=seconds)"
echo "[CD actual guard] C detected_cases=${DETECTED_COUNT}"

D_ARGS=(
  D_RUN_ID="$D_RUN_ID"
  D_RUN_MODE="$RUN_MODE"
  D_EXPERIMENTS_ROOT=data/experiments_v5
  D_QUESTIONS=data/experiments_v5/questions/v5_questions.csv
  D_OUTPUT_ROOT="$D_OUT"
  D_CONDITIONS="$D_CONDITIONS_VALUE"
  D_DETECTED_CASES="$C_DETECTED_CASES"
  D_ENABLE_DENSE=true
  D_ENABLE_RERANK=false
  D_REBUILD_INDEX=false
  D_MAX_QUESTIONS="$MAX_QUESTIONS"
)
if [[ -n "$SMOKE_CASES" ]]; then
  D_ARGS+=(D_SMOKE_CASES="$SMOKE_CASES")
fi

echo
echo "[CD actual guard] D started=$(date --iso-8601=seconds)"
echo "[CD actual guard] D_conditions=${D_CONDITIONS_VALUE}"
echo "[CD actual guard] D_executes_only_C_detected_cases=true"
env "${D_ARGS[@]}" bash experiment_D/experiments/run_study_d_v5.sh

D_RUN_DIR="${D_OUT}/${D_RUN_ID}"
[[ -d "$D_RUN_DIR" ]] || {
  echo "[CD actual guard] missing D output: ${D_RUN_DIR}" >&2
  exit 1
}

python tools/summarize_cd_v5_external.py --c-run "$C_RUN_DIR" --d-run "$D_RUN_DIR"
tar -czf "${BASE_OUT}_share.tgz" -C "$(dirname "$BASE_OUT")" "$(basename "$BASE_OUT")"

echo
echo "[CD actual guard] complete=$(date --iso-8601=seconds)"
echo "[CD actual guard] C_run_dir=${C_RUN_DIR}"
echo "[CD actual guard] C_summary=${C_RUN_DIR}/c2_summary.csv"
echo "[CD actual guard] C_detected_cases=${C_DETECTED_CASES}"
echo "[CD actual guard] D_run_dir=${D_RUN_DIR}"
echo "[CD actual guard] D_summary=${D_RUN_DIR}/d_summary.csv"
echo "[CD actual guard] D_pairs=${D_RUN_DIR}/paired_results.csv"
echo "[CD actual guard] share_archive=${BASE_OUT}_share.tgz"
