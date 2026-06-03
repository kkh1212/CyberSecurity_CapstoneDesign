#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export OLLAMA_BASE_URL="${FAST_OLLAMA_BASE_URL:-http://172.27.208.1:11435}"
export OLLAMA_MODEL="${FAST_OLLAMA_MODEL:-gemma3:4b}"
export USE_VENV=true
export SKIP_PIP_INSTALL="${SKIP_PIP_INSTALL:-true}"
export SKIP_OLLAMA_PULL="${SKIP_OLLAMA_PULL:-true}"

mkdir -p logs
log_path="logs/acd_full_fast_$(date +%Y%m%d_%H%M%S).log"

nohup bash tools/run_acd_full_v5_rerank_off.sh > "$log_path" 2>&1 &
pid=$!

echo "PID=$pid LOG=$log_path"
echo "tail -f \"$log_path\""
