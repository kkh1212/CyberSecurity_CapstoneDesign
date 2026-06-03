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

STAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_FILE="logs/a_smoke_v5_gemma12b_${STAMP}.log"
OUT_ROOT="outputs/experiments_v5/A_smoke_rerank_off_gemma3_12b"

export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:12b}"
export EXTERNAL_GUARDRAIL_PROVIDER="${EXTERNAL_GUARDRAIL_PROVIDER:-meta_prompt_guard}"
export EXTERNAL_GUARDRAIL_STAGES="${EXTERNAL_GUARDRAIL_STAGES:-context}"
export EXTERNAL_GUARDRAIL_ACTION="${EXTERNAL_GUARDRAIL_ACTION:-block}"
export EXTERNAL_GUARDRAIL_FAIL_MODE="${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}"
export A_PIN_CONDITIONS="${A_PIN_CONDITIONS:-direct_blackbox,direct_whitebox}"

{
  echo "[A smoke] started=$(date --iso-8601=seconds)"
  echo "[A smoke] model=${OLLAMA_MODEL}"
  echo "[A smoke] guardrail=${EXTERNAL_GUARDRAIL_PROVIDER}/${EXTERNAL_GUARDRAIL_STAGES}/${EXTERNAL_GUARDRAIL_ACTION}"
  echo "[A smoke] conditions=v5_full"
  echo "[A smoke] questions_per_condition=5"
  echo "[A smoke] pinned_conditions=${A_PIN_CONDITIONS}"
  echo "[A smoke] output_root=${OUT_ROOT}"
} | tee "$LOG_FILE"

A_RUN_MODE=smoke \
A_EXPERIMENTS_ROOT=data/experiments_v5 \
A_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
A_OUTPUT_ROOT="$OUT_ROOT" \
A_CONDITIONS=v5_full \
A_ENABLE_DENSE=true \
A_ENABLE_RERANK=false \
A_REBUILD_INDEX=true \
A_SMOKE_CASES=5 \
A_MAX_QUESTIONS=5 \
bash experiment_A/run_experiment_A_v5.sh 2>&1 | tee -a "$LOG_FILE"

RUN_DIR="$(ls -td "${OUT_ROOT}"/v3_* | head -1)"

{
  echo
  echo "[A smoke] evaluating behavioural outcomes"
} | tee -a "$LOG_FILE"
python scripts/eval_attack_success_v5.py --run-dir "$RUN_DIR" 2>&1 | tee -a "$LOG_FILE"

cp "$LOG_FILE" "$RUN_DIR/launcher.log"

SHARE_ARCHIVE="${RUN_DIR}_share.tgz"
tar -czf "$SHARE_ARCHIVE" -C "$(dirname "$RUN_DIR")" "$(basename "$RUN_DIR")"

{
  echo
  echo "[A smoke] complete"
  echo "[A smoke] run_dir=${RUN_DIR}"
  echo "[A smoke] summary=${RUN_DIR}/summary.csv"
  echo "[A smoke] behavioural_summary=${RUN_DIR}/attack_success/attack_success_summary.csv"
  echo "[A smoke] human_report=${RUN_DIR}/attack_success/attack_success_report.md"
  echo "[A smoke] full_answer_review=${RUN_DIR}/answer_review.txt"
  echo "[A smoke] share_archive=${SHARE_ARCHIVE}"
} | tee -a "$LOG_FILE"
