#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

target="${1:-all}"
timeout_sec="${PREFLIGHT_TIMEOUT_SEC:-10}"

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:12b}"
export EXTERNAL_GUARDRAIL_PROVIDER="${EXTERNAL_GUARDRAIL_PROVIDER:-meta_prompt_guard}"
export EXTERNAL_GUARDRAIL_STAGES="${EXTERNAL_GUARDRAIL_STAGES:-context}"
export EXTERNAL_GUARDRAIL_ACTION="${EXTERNAL_GUARDRAIL_ACTION:-block}"
export EXTERNAL_GUARDRAIL_FAIL_MODE="${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}"
export SEMANTIC_DETECTOR_MODE="${SEMANTIC_DETECTOR_MODE:-improved}"
export SEMANTIC_DETECTOR_BACKEND="${SEMANTIC_DETECTOR_BACKEND:-auto}"
export SEMANTIC_DETECTOR_IMPROVED_THRESHOLD="${SEMANTIC_DETECTOR_IMPROVED_THRESHOLD:-0.35}"
export SEMANTIC_DETECTOR_WINDOW_ENABLED="${SEMANTIC_DETECTOR_WINDOW_ENABLED:-true}"
export SEMANTIC_DETECTOR_WINDOW_RADIUS="${SEMANTIC_DETECTOR_WINDOW_RADIUS:-1}"
export SEMANTIC_DETECTOR_VERDICTS="${SEMANTIC_DETECTOR_VERDICTS:-muted_candidate}"

run_for_10s() {
  local name="$1"
  shift
  echo
  echo "==== ${name}: running for ${timeout_sec}s ===="
  set +e
  timeout "${timeout_sec}s" "$@"
  local rc=$?
  set -e
  if [[ "$rc" -eq 124 ]]; then
    echo "[OK] ${name}: reached ${timeout_sec}s without an immediate crash"
    return 0
  fi
  if [[ "$rc" -eq 0 ]]; then
    echo "[OK] ${name}: finished within ${timeout_sec}s"
    return 0
  fi
  echo "[FAIL] ${name}: exited with code ${rc}" >&2
  return "$rc"
}

run_a() {
  run_for_10s "A full preflight" bash -lc '
    A_RUN_MODE=full \
    A_EXPERIMENTS_ROOT=data/experiments_v5 \
    A_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
    A_ENABLE_RERANK=false \
    A_ENABLE_DENSE=true \
    A_REBUILD_INDEX=true \
    bash experiment_A/run_experiment_A_v5.sh
  '
}

run_c() {
  run_for_10s "C full preflight" bash -lc '
    C2_RUN_MODE=full \
    C2_EXPERIMENTS_ROOT=data/experiments_v5 \
    C2_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
    C2_ENABLE_RERANK=false \
    C2_REBUILD_INDEX=true \
    C2_CONDITIONS=normal_on,muted_blackbox_on,muted_whitebox_on \
    bash experiment_C/experiments/run_study_c2_v5.sh
  '
}

run_d() {
  local detected="${D_DETECTED_CASES:-}"
  if [[ -z "$detected" ]]; then
    detected="$(ls -td outputs/experiments_v5/C_rerank_off/study_c2_v5_* 2>/dev/null | head -1 || true)"
    if [[ -n "$detected" ]]; then
      detected="${detected}/detected_cases.csv"
    fi
  fi
  if [[ -z "$detected" || ! -s "$detected" ]]; then
    echo "[SKIP] D full preflight: set D_DETECTED_CASES or run C full first" >&2
    return 0
  fi
  D_DETECTED_CASES="$detected" run_for_10s "D full preflight" bash -lc '
    D_RUN_MODE=full \
    D_EXPERIMENTS_ROOT=data/experiments_v5 \
    D_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
    D_ENABLE_RERANK=false \
    D_REBUILD_INDEX=true \
    D_CONDITIONS=muted_blackbox_on,muted_whitebox_on \
    D_DETECTED_CASES="$D_DETECTED_CASES" \
    bash experiment_D/experiments/run_study_d_v5.sh
  '
}

case "$target" in
  a|A) run_a ;;
  c|C) run_c ;;
  d|D) run_d ;;
  all) run_a; run_c; run_d ;;
  *)
    echo "Usage: $0 [a|c|d|all]" >&2
    exit 2
    ;;
esac
