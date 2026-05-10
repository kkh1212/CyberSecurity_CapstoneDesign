#!/usr/bin/env bash
set -uo pipefail

if [[ "$#" -eq 0 ]]; then
    echo "Usage: $0 <command> [args...]" >&2
    echo "Example: FINAL_CORPUS_DIR=... $0 ./experiments/final/run_final_b.sh" >&2
    exit 2
fi

start_ts="$(date '+%Y-%m-%d %H:%M:%S')"
echo "[notify] started at ${start_ts}: $*"

"$@"
status=$?

end_ts="$(date '+%Y-%m-%d %H:%M:%S')"
if [[ "${status}" -eq 0 ]]; then
    message="MutedRAG experiment finished successfully at ${end_ts}"
else
    message="MutedRAG experiment failed with exit code ${status} at ${end_ts}"
fi

echo
echo "[notify] ${message}"

# Terminal bell. This works in many terminals even when Windows audio bridging is not available.
printf '\a'

# Best-effort Windows sound from WSL. Failure is ignored because experiments may run in plain Linux.
if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command \
        "[console]::beep(880,350); Start-Sleep -Milliseconds 120; [console]::beep(1040,350); Start-Sleep -Milliseconds 120; [console]::beep(1320,500)" \
        >/dev/null 2>&1 || true
fi

exit "${status}"
