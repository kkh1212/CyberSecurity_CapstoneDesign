#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/acd_env.sh
source "$ROOT_DIR/tools/acd_env.sh"

activate_venv
configure_model_and_guardrail
configure_detector_defaults
run_mode_settings D

if [[ -z "${D_DETECTED_CASES:-}" && -f outputs/latest_c.env ]]; then
  # shellcheck disable=SC1091
  source outputs/latest_c.env
  D_DETECTED_CASES="${C_DETECTED_CASES:-}"
fi
if [[ -z "${D_DETECTED_CASES:-}" || ! -f "$D_DETECTED_CASES" ]]; then
  echo "[D muted] Set D_DETECTED_CASES or run bash run_C.sh first." >&2
  exit 2
fi

STAMP="$(date '+%Y%m%d_%H%M%S')"
D_OUT="${D_OUTPUT_ROOT:-outputs/experiments_v5/D_muted_${D_RUN_MODE}_${EXTERNAL_GUARDRAIL_PROVIDER}_${STAMP}}"
D_RUN_ID="${D_RUN_ID:-study_d_muted_${D_RUN_MODE}_${STAMP}}"
D_CONDITIONS_VALUE="${D_CONDITIONS:-muted_blackbox_on,muted_whitebox_on}"
LATEST_FILE="outputs/latest_d_muted.env"
: > "$LATEST_FILE"

echo "[D muted] started=$(date --iso-8601=seconds)"
echo "[D muted] run_mode=${D_RUN_MODE}"
echo "[D muted] provider=${EXTERNAL_GUARDRAIL_PROVIDER}"
echo "[D muted] answer_backend=${LLM_PROVIDER}"
echo "[D muted] answer_model=${LLM_MODEL}"
echo "[D muted] detected_cases=${D_DETECTED_CASES}"
echo "[D muted] conditions=${D_CONDITIONS_VALUE}"

args=(
  D_RUN_ID="$D_RUN_ID"
  D_RUN_MODE="$D_RUN_MODE"
  D_EXPERIMENTS_ROOT=data/experiments_v5
  D_QUESTIONS=data/experiments_v5/questions/v5_questions.csv
  D_OUTPUT_ROOT="$D_OUT"
  D_CONDITIONS="$D_CONDITIONS_VALUE"
  D_DETECTED_CASES="$D_DETECTED_CASES"
  D_ENABLE_DENSE=true
  D_ENABLE_RERANK=false
  D_REBUILD_INDEX=false
  D_MAX_QUESTIONS="$D_MAX_QUESTIONS"
)
if [[ -n "$D_SMOKE_CASES" ]]; then
  args+=(D_SMOKE_CASES="$D_SMOKE_CASES")
fi

env "${args[@]}" bash experiment_D/experiments/run_study_d_v5.sh

D_RUN_DIR="${D_OUT}/${D_RUN_ID}"
write_latest_var "$LATEST_FILE" "D_MUTED_RUN_DIR" "$D_RUN_DIR"
tar -czf "${D_RUN_DIR}_share.tgz" -C "$(dirname "$D_RUN_DIR")" "$(basename "$D_RUN_DIR")"

echo
echo "[D muted] run_dir=${D_RUN_DIR}"
echo "[D muted] summary=${D_RUN_DIR}/d_summary.csv"
echo "[D muted] paired=${D_RUN_DIR}/paired_results.csv"
echo "[D muted] latest=${LATEST_FILE}"
echo "[D muted] complete=$(date --iso-8601=seconds)"
