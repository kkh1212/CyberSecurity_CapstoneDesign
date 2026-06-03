#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs
log_path="logs/cd_external_$(date +%Y%m%d_%H%M%S).log"
nohup bash tools/run_cd_v5_external.sh > "$log_path" 2>&1 &
pid=$!
echo "PID=$pid LOG=$log_path"
echo "tail -f \"$log_path\""
