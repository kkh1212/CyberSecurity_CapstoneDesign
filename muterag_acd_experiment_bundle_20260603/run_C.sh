#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/acd_env.sh
source "$ROOT_DIR/tools/acd_env.sh"

activate_venv
configure_model_and_guardrail
configure_detector_defaults
run_mode_settings C

export SEMANTIC_DETECTOR_VERDICTS="${SEMANTIC_DETECTOR_VERDICTS:-muted_candidate}"

STAMP="$(date '+%Y%m%d_%H%M%S')"
C_OUT="${C_OUTPUT_ROOT:-outputs/experiments_v5/C_${C_RUN_MODE}_${EXTERNAL_GUARDRAIL_PROVIDER}_${STAMP}}"
C_RUN_ID="${C_RUN_ID:-study_c2_${C_RUN_MODE}_${STAMP}}"
C_CONDITIONS="${C2_CONDITIONS:-normal_on,muted_blackbox_on,muted_whitebox_on}"
C_ACTIONS="${C2_SEMANTIC_ACTIONS:-off,log_only,drop_chunk}"
LATEST_FILE="outputs/latest_c.env"
: > "$LATEST_FILE"

echo "[C] started=$(date --iso-8601=seconds)"
echo "[C] run_mode=${C_RUN_MODE}"
echo "[C] provider=${EXTERNAL_GUARDRAIL_PROVIDER}"
echo "[C] answer_backend=${LLM_PROVIDER}"
echo "[C] answer_model=${LLM_MODEL}"
echo "[C] conditions=${C_CONDITIONS}"
echo "[C] actions=${C_ACTIONS}"
echo "[C] rerank=false"

args=(
  C2_RUN_ID="$C_RUN_ID"
  C2_RUN_MODE="$C_RUN_MODE"
  C2_EXPERIMENTS_ROOT=data/experiments_v5
  C2_QUESTIONS=data/experiments_v5/questions/v5_questions.csv
  C2_OUTPUT_ROOT="$C_OUT"
  C2_CONDITIONS="$C_CONDITIONS"
  C2_SEMANTIC_ACTIONS="$C_ACTIONS"
  C2_ENABLE_DENSE=true
  C2_ENABLE_RERANK=false
  C2_REBUILD_INDEX=true
  C2_MAX_QUESTIONS="$C_MAX_QUESTIONS"
)
if [[ -n "$C_SMOKE_CASES" ]]; then
  args+=(C2_SMOKE_CASES="$C_SMOKE_CASES")
fi

env "${args[@]}" bash experiment_C/experiments/run_study_c2_v5.sh

C_RUN_DIR="${C_OUT}/${C_RUN_ID}"
write_latest_var "$LATEST_FILE" "C_RUN_DIR" "$C_RUN_DIR"
write_latest_var "$LATEST_FILE" "C_DETECTED_CASES" "${C_RUN_DIR}/detected_cases.csv"
tar -czf "${C_RUN_DIR}_share.tgz" -C "$(dirname "$C_RUN_DIR")" "$(basename "$C_RUN_DIR")"

echo
echo "[C] run_dir=${C_RUN_DIR}"
echo "[C] summary=${C_RUN_DIR}/c2_summary.csv"
echo "[C] detected_cases=${C_RUN_DIR}/detected_cases.csv"
echo "[C] latest=${LATEST_FILE}"
echo "[C] complete=$(date --iso-8601=seconds)"
