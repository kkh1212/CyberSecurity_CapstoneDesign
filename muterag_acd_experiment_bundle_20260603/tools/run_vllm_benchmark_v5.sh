#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODEL_MATRIX="${VLLM_MODEL_MATRIX:-tools/vllm_models_v5.tsv}"
ALIASES="${VLLM_MODEL_ALIASES:-qwen25_7b llama31_8b gemma3_12b_awq qwen25_14b_awq gpt_oss_20b}"
PORT="${VLLM_PORT:-8000}"
READY_TIMEOUT_SEC="${VLLM_READY_TIMEOUT_SEC:-1200}"
LOG_DIR="${VLLM_LOG_DIR:-logs/vllm}"
mkdir -p "$LOG_DIR"

timestamp_compact() {
  date '+%Y%m%d_%H%M%S'
}

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

lookup_model_id() {
  local alias="$1"
  awk -F '\t' -v alias="$alias" '
    $0 !~ /^#/ && NF >= 4 && $1 == alias { print $2; found=1 }
    END { if (!found) exit 1 }
  ' "$MODEL_MATRIX"
}

wait_for_vllm() {
  local alias="$1"
  local model_id="$2"
  local base_url="http://localhost:${PORT}/v1"
  local started
  started="$(date +%s)"
  while true; do
    if python tools/check_llm_endpoint.py --base-url "$base_url" --model "$model_id" --timeout 5 --probe-only >/dev/null 2>&1; then
      echo "[$(timestamp)] ${alias}: endpoint ready"
      return 0
    fi
    if (( $(date +%s) - started > READY_TIMEOUT_SEC )); then
      echo "[$(timestamp)] ${alias}: vLLM did not become ready within ${READY_TIMEOUT_SEC}s" >&2
      return 1
    fi
    sleep 5
  done
}

stop_pid() {
  local pid="${1:-}"
  if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" >/dev/null 2>&1 || true
  fi
}

for alias in $ALIASES; do
  model_id="$(lookup_model_id "$alias")" || {
    echo "Unknown alias: $alias" >&2
    exit 2
  }
  log_file="${LOG_DIR}/${alias}_$(timestamp_compact).log"
  echo
  echo "[$(timestamp)] ==== start vLLM ${alias} (${model_id}) ===="
  VLLM_MODEL_ALIAS="$alias" VLLM_PORT="$PORT" bash tools/serve_vllm_model_v5.sh "$alias" > "$log_file" 2>&1 &
  vllm_pid=$!
  trap 'stop_pid "$vllm_pid"' INT TERM EXIT

  if ! wait_for_vllm "$alias" "$model_id"; then
    echo "vLLM log: $log_file" >&2
    stop_pid "$vllm_pid"
    exit 1
  fi

  echo "[$(timestamp)] ==== run A/C/D ${RUN_MODE:-full} ${alias} ===="
  VLLM_MODEL_ALIAS="$alias" \
  VLLM_PORT="$PORT" \
  LLM_PROVIDER=vllm \
  LLM_BASE_URL="http://localhost:${PORT}/v1" \
  LLM_MODEL="$model_id" \
  bash tools/run_acd_full_v5_vllm.sh "$alias"

  echo "[$(timestamp)] ==== stop vLLM ${alias} ===="
  stop_pid "$vllm_pid"
  trap - INT TERM EXIT
  sleep "${VLLM_COOLDOWN_SEC:-10}"
done

echo
echo "[$(timestamp)] all vLLM benchmarks completed"
