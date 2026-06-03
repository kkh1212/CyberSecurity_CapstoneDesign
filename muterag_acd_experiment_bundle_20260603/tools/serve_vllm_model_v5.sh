#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODEL_MATRIX="${VLLM_MODEL_MATRIX:-tools/vllm_models_v5.tsv}"
MODEL_ALIAS="${1:-${VLLM_MODEL_ALIAS:-qwen25_14b_awq}}"
HOST="${VLLM_HOST:-0.0.0.0}"
PORT="${VLLM_PORT:-8000}"
GPU_UTIL="${VLLM_GPU_MEMORY_UTILIZATION:-0.90}"

lookup_model() {
  awk -F '\t' -v alias="$MODEL_ALIAS" '
    $0 !~ /^#/ && NF >= 4 && $1 == alias { print $0; found=1 }
    END { if (!found) exit 1 }
  ' "$MODEL_MATRIX"
}

line="$(lookup_model)" || {
  echo "Unknown VLLM_MODEL_ALIAS: $MODEL_ALIAS" >&2
  echo "Available aliases:" >&2
  awk -F '\t' '$0 !~ /^#/ && NF >= 4 { print "  " $1 " -> " $2 }' "$MODEL_MATRIX" >&2
  exit 2
}

IFS=$'\t' read -r alias model_id default_max_len extra_args <<< "$line"
max_len="${VLLM_MAX_MODEL_LEN:-$default_max_len}"

read -r -a extra_array <<< "$extra_args"
if [[ -n "${VLLM_EXTRA_ARGS:-}" ]]; then
  read -r -a override_extra_array <<< "$VLLM_EXTRA_ARGS"
else
  override_extra_array=()
fi

echo "[vLLM] alias=$alias"
echo "[vLLM] model=$model_id"
echo "[vLLM] host=$HOST port=$PORT max_model_len=$max_len gpu_memory_utilization=$GPU_UTIL"

exec "${VLLM_BIN:-vllm}" serve "$model_id" \
  --host "$HOST" \
  --port "$PORT" \
  --max-model-len "$max_len" \
  --gpu-memory-utilization "$GPU_UTIL" \
  "${extra_array[@]}" \
  "${override_extra_array[@]}"
