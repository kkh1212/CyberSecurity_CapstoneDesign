#!/usr/bin/env bash
set -Eeuo pipefail

echo "[ACD] Run A"
bash run_A.sh

echo
echo "[ACD] Run C"
bash run_C.sh

echo
echo "[ACD] Run D muted recovery from C detections"
bash run_D_muted_from_C.sh

echo
echo "[ACD] Run D direct recovery from A blocked direct cases"
bash run_D_direct_from_A.sh

echo
echo "[ACD] complete"
