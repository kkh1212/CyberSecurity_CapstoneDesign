#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/acd_env.sh
source "$ROOT_DIR/tools/acd_env.sh"

activate_venv
configure_model_and_guardrail
configure_detector_defaults
run_mode_settings A

export A_PIN_CONDITIONS="${A_PIN_CONDITIONS:-direct_blackbox,direct_whitebox}"

STAMP="$(date '+%Y%m%d_%H%M%S')"
BASE_OUT="${A_OUTPUT_BASE:-outputs/experiments_v5/A_${A_RUN_MODE}_${EXTERNAL_GUARDRAIL_PROVIDER}_${STAMP}}"
RUN_GROUPS="${A_RUN_GROUPS:-off,on}"

COMMON_OFF="normal_off,direct_blackbox_off,direct_whitebox_off,muted_blackbox_off,muted_whitebox_off,direct_blackbox_ignore_off,direct_whitebox_ignore_off"
GUARDRAIL_ON="normal_on,direct_blackbox_on,direct_whitebox_on,muted_blackbox_on,muted_whitebox_on,direct_blackbox_ignore_on,direct_whitebox_ignore_on"
LATEST_FILE="outputs/latest_a.env"
: > "$LATEST_FILE"

run_group() {
  local label="$1"
  local conditions="$2"
  local out_root="${BASE_OUT}/${label}"

  echo
  echo "[A] group=${label}"
  echo "[A] provider=${EXTERNAL_GUARDRAIL_PROVIDER}"
  echo "[A] answer_backend=${LLM_PROVIDER}"
  echo "[A] answer_model=${LLM_MODEL}"
  echo "[A] output_root=${out_root}"

  local args=(
    A_RUN_MODE="$A_RUN_MODE"
    A_EXPERIMENTS_ROOT=data/experiments_v5
    A_QUESTIONS=data/experiments_v5/questions/v5_questions.csv
    A_OUTPUT_ROOT="$out_root"
    A_CONDITIONS="$conditions"
    A_ENABLE_DENSE=true
    A_ENABLE_RERANK=false
    A_REBUILD_INDEX=true
    A_MAX_QUESTIONS="$A_MAX_QUESTIONS"
  )
  if [[ -n "$A_SMOKE_CASES" ]]; then
    args+=(A_SMOKE_CASES="$A_SMOKE_CASES")
  fi

  env "${args[@]}" bash experiment_A/run_experiment_A_v5.sh

  local run_dir
  run_dir="$(ls -td "${out_root}"/v3_* | head -1)"
  python scripts/eval_attack_success_v5.py --run-dir "$run_dir"
  tar -czf "${run_dir}_share.tgz" -C "$(dirname "$run_dir")" "$(basename "$run_dir")"

  if [[ "$label" == "off" ]]; then
    write_latest_var "$LATEST_FILE" "A_OFF_RUN_DIR" "$run_dir"
  else
    write_latest_var "$LATEST_FILE" "A_ON_RUN_DIR" "$run_dir"
  fi

  echo "[A] run_dir=${run_dir}"
  echo "[A] summary=${run_dir}/summary.csv"
  echo "[A] behavioral_summary=${run_dir}/attack_success/attack_success_summary.csv"
  echo "[A] answer_review=${run_dir}/answer_review.txt"
}

echo "[A] started=$(date --iso-8601=seconds)"
echo "[A] run_mode=${A_RUN_MODE}"
echo "[A] groups=${RUN_GROUPS}"
echo "[A] answer_backend=${LLM_PROVIDER}"
echo "[A] answer_model=${LLM_MODEL}"
echo "[A] pinned_conditions=${A_PIN_CONDITIONS}"
echo "[A] rerank=false"

[[ ",${RUN_GROUPS}," == *",off,"* ]] && run_group "off" "$COMMON_OFF"
[[ ",${RUN_GROUPS}," == *",on,"* ]] && run_group "on" "$GUARDRAIL_ON"

echo
echo "[A] latest=${LATEST_FILE}"
echo "[A] complete=$(date --iso-8601=seconds)"
