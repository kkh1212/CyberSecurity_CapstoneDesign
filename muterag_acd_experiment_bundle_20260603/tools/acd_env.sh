#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

activate_venv() {
  cd "$ROOT_DIR"
  if [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  mkdir -p logs outputs outputs/experiments_v5 outputs/d_inputs
  export HF_HOME="${HF_HOME:-$ROOT_DIR/.cache/huggingface}"
  mkdir -p "$HF_HOME"
}

configure_model_and_guardrail() {
  export ANSWER_BACKEND="${ANSWER_BACKEND:-${LLM_PROVIDER:-ollama}}"
  export ANSWER_BACKEND="$(printf '%s' "$ANSWER_BACKEND" | tr '[:upper:]' '[:lower:]')"

  case "$ANSWER_BACKEND" in
    ollama|"")
      export LLM_PROVIDER="ollama"
      export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
      export OLLAMA_MODEL="${OLLAMA_MODEL:-${ANSWER_MODEL:-gemma3:4b}}"
      export LLM_MODEL="${LLM_MODEL:-$OLLAMA_MODEL}"
      ;;
    vllm|openai|openai_compatible)
      export LLM_PROVIDER="$ANSWER_BACKEND"
      export LLM_BASE_URL="${LLM_BASE_URL:-${VLLM_BASE_URL:-http://localhost:8000/v1}}"
      export LLM_MODEL="${LLM_MODEL:-${ANSWER_MODEL:-${VLLM_MODEL:-}}}"
      if [[ -z "$LLM_MODEL" ]]; then
        echo "[acd] LLM_MODEL or ANSWER_MODEL is required for ANSWER_BACKEND=${ANSWER_BACKEND}." >&2
        exit 2
      fi
      export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
      # The lower-level runner still records OLLAMA_MODEL in run_config; mirror
      # the actual OpenAI-compatible model there for easier result comparison.
      export OLLAMA_MODEL="${OLLAMA_MODEL:-$LLM_MODEL}"
      ;;
    *)
      echo "[acd] Unsupported ANSWER_BACKEND=${ANSWER_BACKEND}; use ollama, vllm, openai, or openai_compatible." >&2
      exit 2
      ;;
  esac

  export LLM_API_KEY="${LLM_API_KEY:-${OPENAI_API_KEY:-}}"
  export LLM_TIMEOUT_SEC="${LLM_TIMEOUT_SEC:-180}"
  export LLM_TEMPERATURE="${LLM_TEMPERATURE:-0}"
  export LLM_TOP_P="${LLM_TOP_P:-0.9}"

  export EXTERNAL_GUARDRAIL_PROVIDER="${GUARDRAIL_PROVIDER:-${EXTERNAL_GUARDRAIL_PROVIDER:-meta_prompt_guard}}"
  export EXTERNAL_GUARDRAIL_STAGES="${EXTERNAL_GUARDRAIL_STAGES:-context}"
  export EXTERNAL_GUARDRAIL_ACTION="${EXTERNAL_GUARDRAIL_ACTION:-block}"
  export EXTERNAL_GUARDRAIL_FAIL_MODE="${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}"
  export EXTERNAL_GUARDRAIL_TIMEOUT_SEC="${EXTERNAL_GUARDRAIL_TIMEOUT_SEC:-300}"

  export LLAMA_STACK_HOST="${LLAMA_STACK_HOST:-127.0.0.1}"
  export LLAMA_STACK_PORT="${LLAMA_STACK_PORT:-8191}"
  export PROMPT_GUARD_MODEL="${PROMPT_GUARD_MODEL:-meta-llama/Llama-Prompt-Guard-2-86M}"
  export SAFETY_GUARD_MODEL="${SAFETY_GUARD_MODEL:-meta-llama/Llama-Guard-3-1B}"
  if [[ "$EXTERNAL_GUARDRAIL_PROVIDER" == "llama_guard_stack" ]]; then
    export EXTERNAL_GUARDRAIL_API_URL="${EXTERNAL_GUARDRAIL_API_URL:-http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/check}"
  else
    export EXTERNAL_GUARDRAIL_API_URL="${EXTERNAL_GUARDRAIL_API_URL:-}"
  fi

  if [[ "$EXTERNAL_GUARDRAIL_PROVIDER" == "lakera" && -z "${EXTERNAL_GUARDRAIL_API_KEY:-}" ]]; then
    echo "[acd] EXTERNAL_GUARDRAIL_API_KEY is required for lakera." >&2
    exit 2
  fi
  if [[ "$EXTERNAL_GUARDRAIL_PROVIDER" == "generic_http" && -z "${EXTERNAL_GUARDRAIL_API_URL:-}" ]]; then
    echo "[acd] EXTERNAL_GUARDRAIL_API_URL is required for generic_http." >&2
    exit 2
  fi
  if [[ "$EXTERNAL_GUARDRAIL_PROVIDER" == "llama_guard_stack" ]]; then
    if ! curl -fsS "http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/health" >/dev/null; then
      echo "[acd] llama_guard_stack is not running." >&2
      echo "[acd] Start it first: bash tools/start_llama_guard_stack.sh" >&2
      exit 2
    fi
  fi
}

configure_detector_defaults() {
  export SEMANTIC_DETECTOR_MODE="${SEMANTIC_DETECTOR_MODE:-improved}"
  export SEMANTIC_DETECTOR_BACKEND="${SEMANTIC_DETECTOR_BACKEND:-auto}"
  export SEMANTIC_DETECTOR_IMPROVED_THRESHOLD="${SEMANTIC_DETECTOR_IMPROVED_THRESHOLD:-0.35}"
  export SEMANTIC_DETECTOR_WINDOW_ENABLED="${SEMANTIC_DETECTOR_WINDOW_ENABLED:-true}"
  export SEMANTIC_DETECTOR_WINDOW_RADIUS="${SEMANTIC_DETECTOR_WINDOW_RADIUS:-1}"
}

run_mode_settings() {
  local prefix="$1"
  local mode="${RUN_MODE:-smoke}"
  case "$mode" in
    smoke)
      printf -v "${prefix}_RUN_MODE" "%s" "smoke"
      printf -v "${prefix}_MAX_QUESTIONS" "%s" "${MAX_QUESTIONS:-5}"
      printf -v "${prefix}_SMOKE_CASES" "%s" "${SMOKE_CASES:-5}"
      ;;
    full)
      printf -v "${prefix}_RUN_MODE" "%s" "full"
      printf -v "${prefix}_MAX_QUESTIONS" "%s" "${MAX_QUESTIONS:-50}"
      printf -v "${prefix}_SMOKE_CASES" "%s" ""
      ;;
    *)
      echo "[acd] RUN_MODE must be smoke or full." >&2
      exit 2
      ;;
  esac
}

write_latest_var() {
  local file="$1"
  local name="$2"
  local value="$3"
  printf '%s=%q\n' "$name" "$value" >> "$file"
}
