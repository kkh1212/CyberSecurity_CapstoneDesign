#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/acd_env.sh
source "$ROOT_DIR/tools/acd_env.sh"

activate_venv
configure_model_and_guardrail
configure_detector_defaults
run_mode_settings D

if [[ -z "${A_ON_RUN_DIR:-}" && -f outputs/latest_a.env ]]; then
  # shellcheck disable=SC1091
  source outputs/latest_a.env
fi
if [[ -z "${A_ON_RUN_DIR:-}" || ! -d "$A_ON_RUN_DIR" ]]; then
  echo "[D direct] Set A_ON_RUN_DIR or run bash run_A.sh first." >&2
  exit 2
fi

STAMP="$(date '+%Y%m%d_%H%M%S')"
CASE_CSV="${D_A_BLOCKED_CASES:-outputs/d_inputs/a_direct_blocked_cases_${STAMP}.csv}"
D_OUT="${D_OUTPUT_ROOT:-outputs/experiments_v5/D_direct_${D_RUN_MODE}_${EXTERNAL_GUARDRAIL_PROVIDER}_${STAMP}}"
D_RUN_ID="${D_RUN_ID:-study_d_direct_${D_RUN_MODE}_${STAMP}}"
D_CONDITIONS_VALUE="${D_CONDITIONS:-direct_blackbox_on,direct_whitebox_on,direct_blackbox_ignore_on,direct_whitebox_ignore_on}"
LATEST_FILE="outputs/latest_d_direct.env"
: > "$LATEST_FILE"

echo "[D direct] exporting A blocked direct cases"
python scripts/export_a_blocked_cases_for_d.py \
  --run-dir "$A_ON_RUN_DIR" \
  --out "$CASE_CSV" \
  --max-per-condition "${D_DIRECT_MAX_PER_CONDITION:-0}"

if [[ "$(python - "$CASE_CSV" <<'PY'
import csv
import sys
with open(sys.argv[1], encoding="utf-8-sig", newline="") as f:
    print(sum(1 for _ in csv.DictReader(f)))
PY
)" == "0" ]]; then
  echo "[D direct] No A guardrail-blocked direct cases were exported; skipping D direct." >&2
  exit 0
fi

echo "[D direct] started=$(date --iso-8601=seconds)"
echo "[D direct] run_mode=${D_RUN_MODE}"
echo "[D direct] provider=${EXTERNAL_GUARDRAIL_PROVIDER}"
echo "[D direct] answer_backend=${LLM_PROVIDER}"
echo "[D direct] answer_model=${LLM_MODEL}"
echo "[D direct] a_on_run_dir=${A_ON_RUN_DIR}"
echo "[D direct] a_blocked_cases=${CASE_CSV}"
echo "[D direct] conditions=${D_CONDITIONS_VALUE}"

args=(
  D_RUN_ID="$D_RUN_ID"
  D_RUN_MODE="$D_RUN_MODE"
  D_EXPERIMENTS_ROOT=data/experiments_v5
  D_QUESTIONS=data/experiments_v5/questions/v5_questions.csv
  D_OUTPUT_ROOT="$D_OUT"
  D_CONDITIONS="$D_CONDITIONS_VALUE"
  D_A_BLOCKED_CASES="$CASE_CSV"
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
write_latest_var "$LATEST_FILE" "D_DIRECT_RUN_DIR" "$D_RUN_DIR"
write_latest_var "$LATEST_FILE" "D_A_BLOCKED_CASES" "$CASE_CSV"
tar -czf "${D_RUN_DIR}_share.tgz" -C "$(dirname "$D_RUN_DIR")" "$(basename "$D_RUN_DIR")"

echo
echo "[D direct] run_dir=${D_RUN_DIR}"
echo "[D direct] summary=${D_RUN_DIR}/d_summary.csv"
echo "[D direct] paired=${D_RUN_DIR}/paired_results.csv"
echo "[D direct] latest=${LATEST_FILE}"
echo "[D direct] complete=$(date --iso-8601=seconds)"
