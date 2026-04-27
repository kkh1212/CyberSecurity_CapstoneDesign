#!/usr/bin/env bash
# Run MutedRAG experiments across document-level prompt-injection payload types.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE_ARGS=("$@")
# no default: pass-through to run_exp.sh which runs both A and B when no mode flag given

PAYLOAD_TYPES=(
  "01_직접인젝션"
  "02_간접_명시형"
  "03_간접_혼합형"
  "04_다국어혼합"
)

cd "${PROJECT_ROOT}"

for payload_type in "${PAYLOAD_TYPES[@]}"; do
  echo
  echo "=== MutedRAG payload type: ${payload_type} ==="
  ATTACK_TYPES="${payload_type}" ./experiments/run_exp.sh "${MODE_ARGS[@]}"
done
