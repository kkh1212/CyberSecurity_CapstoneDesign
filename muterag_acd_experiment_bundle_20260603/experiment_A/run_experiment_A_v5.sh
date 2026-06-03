#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ORIGINAL_PWD="$(pwd)"

resolve_input_path() {
  local path="$1"
  if [[ "$path" = /* || "$path" =~ ^[A-Za-z]: ]]; then
    printf '%s\n' "$path"
    return
  fi
  for base in "$ORIGINAL_PWD" "$PROJECT_ROOT" "$SCRIPT_DIR"; do
    if [[ -e "$base/$path" ]]; then
      printf '%s/%s\n' "$(cd "$base" && pwd -P)" "$path"
      return
    fi
  done
  printf '%s/%s\n' "$PROJECT_ROOT" "$path"
}

resolve_output_path() {
  local path="$1"
  if [[ "$path" = /* || "$path" =~ ^[A-Za-z]: ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$PROJECT_ROOT" "$path"
  fi
}

: "${A_RUN_MODE:=full}"
: "${A_EXPERIMENTS_ROOT:=data/experiments_v5}"
: "${A_QUESTIONS:=data/experiments_v5/questions/v5_questions.csv}"
if [[ "${A_ENABLE_RERANK:-true}" =~ ^(0|false|no|off)$ ]]; then
  A_RETRIEVAL_PROFILE="rerank_off"
else
  A_RETRIEVAL_PROFILE="rerank_on"
fi
if [[ -z "${A_OUTPUT_ROOT:-}" ]]; then
  if [[ "$A_RUN_MODE" == "smoke" ]]; then
    A_OUTPUT_ROOT="outputs/experiments_v5/A_smoke_${A_RETRIEVAL_PROFILE}"
  else
    A_OUTPUT_ROOT="outputs/experiments_v5/A_${A_RETRIEVAL_PROFILE}"
  fi
fi
A_EXPERIMENTS_ROOT="$(resolve_input_path "$A_EXPERIMENTS_ROOT")"
A_QUESTIONS="$(resolve_input_path "$A_QUESTIONS")"
A_OUTPUT_ROOT="$(resolve_output_path "$A_OUTPUT_ROOT")"

export PYTHONPATH="$SCRIPT_DIR:$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export SPARSE_TOP_K="${A_SPARSE_TOP_K:-${SPARSE_TOP_K:-60}}"
export DENSE_TOP_K="${A_DENSE_TOP_K:-${DENSE_TOP_K:-40}}"
export RERANK_TOP_K="${A_RERANK_TOP_K:-${RERANK_TOP_K:-100}}"
export FINAL_TOP_K="${A_FINAL_TOP_K:-${FINAL_TOP_K:-8}}"
export RAG_CONTEXT_MAX_CHUNKS="${A_CONTEXT_MAX_CHUNKS:-${RAG_CONTEXT_MAX_CHUNKS:-10}}"
export RETRIEVAL_PROFILE="$A_RETRIEVAL_PROFILE"

args=(
  "$SCRIPT_DIR/scripts/run_v3_experiment.py"
  "--experiments-root" "$A_EXPERIMENTS_ROOT"
  "--questions" "$A_QUESTIONS"
  "--out" "$A_OUTPUT_ROOT"
  "--conditions" "${A_CONDITIONS:-v5_full}"
  "--run-mode" "$A_RUN_MODE"
)

if [[ -n "${A_MAX_QUESTIONS:-}" ]]; then
  args+=("--max-questions" "$A_MAX_QUESTIONS")
fi
if [[ -n "${A_SMOKE_CASES:-}" ]]; then
  args+=("--smoke-cases" "$A_SMOKE_CASES")
fi
if [[ "${A_REBUILD_INDEX:-}" =~ ^(1|true|yes|on)$ ]]; then
  args+=("--rebuild-index")
fi
if [[ "${A_ENABLE_DENSE:-true}" =~ ^(0|false|no|off)$ ]]; then
  args+=("--no-enable-dense")
else
  args+=("--enable-dense")
fi
if [[ "${A_ENABLE_RERANK:-true}" =~ ^(0|false|no|off)$ ]]; then
  args+=("--no-enable-rerank")
else
  args+=("--enable-rerank")
fi

python "${args[@]}"
