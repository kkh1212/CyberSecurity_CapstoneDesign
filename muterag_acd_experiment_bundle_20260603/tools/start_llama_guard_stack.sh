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

export LLAMA_STACK_HOST="${LLAMA_STACK_HOST:-127.0.0.1}"
export LLAMA_STACK_PORT="${LLAMA_STACK_PORT:-8191}"
export PROMPT_GUARD_MODEL="${PROMPT_GUARD_MODEL:-meta-llama/Llama-Prompt-Guard-2-86M}"
export SAFETY_GUARD_MODEL="${SAFETY_GUARD_MODEL:-meta-llama/Llama-Guard-3-1B}"
export PROMPT_GUARD_DEVICE="${PROMPT_GUARD_DEVICE:-cpu}"
export SAFETY_GUARD_DEVICE="${SAFETY_GUARD_DEVICE:-auto}"

if curl -fsS "http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/health" >/dev/null 2>&1; then
  echo "[guard-stack] already running at http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}"
  curl -fsS "http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/health"
  echo
  exit 0
fi

STAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_FILE="logs/llama_guard_stack_${STAMP}.log"
PID_FILE="logs/llama_guard_stack.pid"

nohup python tools/serve_llama_guard_stack.py > "$LOG_FILE" 2>&1 &
PID=$!
printf '%s\n' "$PID" > "$PID_FILE"

echo "[guard-stack] starting pid=${PID}"
echo "[guard-stack] log=${LOG_FILE}"
echo "[guard-stack] prompt_guard_model=${PROMPT_GUARD_MODEL}"
echo "[guard-stack] safety_guard_model=${SAFETY_GUARD_MODEL}"
echo "[guard-stack] model download and initial load may take several minutes"

for _ in $(seq 1 180); do
  if curl -fsS "http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/health" >/dev/null 2>&1; then
    echo "[guard-stack] ready"
    curl -fsS "http://${LLAMA_STACK_HOST}:${LLAMA_STACK_PORT}/health"
    echo
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "[guard-stack] failed during startup; inspect ${LOG_FILE}" >&2
    tail -n 80 "$LOG_FILE" >&2 || true
    exit 1
  fi
  sleep 5
done

echo "[guard-stack] startup timed out; inspect ${LOG_FILE}" >&2
exit 1
