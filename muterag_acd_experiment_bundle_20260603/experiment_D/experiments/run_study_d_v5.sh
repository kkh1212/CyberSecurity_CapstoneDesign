#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$EXPERIMENT_ROOT/.." && pwd)"
ORIGINAL_PWD="$(pwd)"

resolve_input_path() {
  local path="$1"
  if [[ "$path" = /* || "$path" =~ ^[A-Za-z]: ]]; then
    printf '%s\n' "$path"
    return
  fi
  for base in "$ORIGINAL_PWD" "$PROJECT_ROOT" "$EXPERIMENT_ROOT"; do
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

: "${D_RUN_MODE:=full}"
: "${D_EXPERIMENTS_ROOT:=data/experiments_v5}"
: "${D_QUESTIONS:=data/experiments_v5/questions/v5_questions.csv}"
if [[ "${D_ENABLE_RERANK:-true}" =~ ^(0|false|no|off)$ ]]; then
  D_RETRIEVAL_PROFILE="rerank_off"
else
  D_RETRIEVAL_PROFILE="rerank_on"
fi
if [[ -z "${D_OUTPUT_ROOT:-}" ]]; then
  if [[ "$D_RUN_MODE" == "smoke" ]]; then
    D_OUTPUT_ROOT="outputs/experiments_v5/D_smoke_${D_RETRIEVAL_PROFILE}"
  else
    D_OUTPUT_ROOT="outputs/experiments_v5/D_${D_RETRIEVAL_PROFILE}"
  fi
fi
: "${SEMANTIC_DETECTOR_MODE:=improved}"
: "${SEMANTIC_DETECTOR_BACKEND:=auto}"
D_EXPERIMENTS_ROOT="$(resolve_input_path "$D_EXPERIMENTS_ROOT")"
D_QUESTIONS="$(resolve_input_path "$D_QUESTIONS")"
D_OUTPUT_ROOT="$(resolve_output_path "$D_OUTPUT_ROOT")"
if [[ -n "${D_DETECTED_CASES:-}" ]]; then
  D_DETECTED_CASES="$(resolve_input_path "$D_DETECTED_CASES")"
  export D_DETECTED_CASES
fi
if [[ -n "${D_A_BLOCKED_CASES:-}" ]]; then
  D_A_BLOCKED_CASES="$(resolve_input_path "$D_A_BLOCKED_CASES")"
  export D_A_BLOCKED_CASES
fi
if [[ -n "${D_LEAK_TARGETS:-}" ]]; then
  D_LEAK_TARGETS="$(resolve_input_path "$D_LEAK_TARGETS")"
  export D_LEAK_TARGETS
fi

export PYTHONPATH="$EXPERIMENT_ROOT:$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export SPARSE_TOP_K="${D_SPARSE_TOP_K:-${SPARSE_TOP_K:-60}}"
export DENSE_TOP_K="${D_DENSE_TOP_K:-${DENSE_TOP_K:-40}}"
export RERANK_TOP_K="${D_RERANK_TOP_K:-${RERANK_TOP_K:-100}}"
export FINAL_TOP_K="${D_FINAL_TOP_K:-${FINAL_TOP_K:-8}}"
export RAG_CONTEXT_MAX_CHUNKS="${D_CONTEXT_MAX_CHUNKS:-${RAG_CONTEXT_MAX_CHUNKS:-10}}"
export RETRIEVAL_PROFILE="$D_RETRIEVAL_PROFILE"

export D_RUN_MODE D_EXPERIMENTS_ROOT D_QUESTIONS D_OUTPUT_ROOT
export SEMANTIC_DETECTOR_MODE SEMANTIC_DETECTOR_BACKEND

python "$SCRIPT_DIR/run_study_d_v5.py"
