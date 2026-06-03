#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

section() {
  echo
  echo "[$(timestamp)] ==== $* ===="
}

fail() {
  echo "[$(timestamp)] ERROR: $*" >&2
  exit 1
}

section "setup"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ "${USE_VENV:-true}" =~ ^(1|true|yes|on)$ ]]; then
  if [[ ! -d .venv ]]; then
    "$PYTHON_BIN" -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  if [[ ! "${SKIP_PIP_INSTALL:-false}" =~ ^(1|true|yes|on)$ ]]; then
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
  fi
fi

export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:12b}"

if [[ "${SKIP_OLLAMA_PULL:-false}" =~ ^(1|true|yes|on)$ ]]; then
  echo "[$(timestamp)] SKIP: ollama pull ${OLLAMA_MODEL}"
elif command -v ollama >/dev/null 2>&1; then
  section "ollama pull ${OLLAMA_MODEL}"
  ollama pull "$OLLAMA_MODEL"
else
  echo "[$(timestamp)] WARN: ollama CLI not found; assuming Ollama server already has ${OLLAMA_MODEL}"
fi

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

section "A full"
A_RUN_MODE=full \
A_EXPERIMENTS_ROOT=data/experiments_v5 \
A_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
A_ENABLE_RERANK=false \
A_ENABLE_DENSE=true \
A_REBUILD_INDEX=true \
bash experiment_A/run_experiment_A_v5.sh

section "C full normal + muted"
C2_RUN_MODE=full \
C2_EXPERIMENTS_ROOT=data/experiments_v5 \
C2_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
C2_ENABLE_RERANK=false \
C2_REBUILD_INDEX=true \
C2_CONDITIONS=normal_on,muted_blackbox_on,muted_whitebox_on \
bash experiment_C/experiments/run_study_c2_v5.sh

C_RUN_DIR="$(ls -td outputs/experiments_v5/C_rerank_off/study_c2_v5_* 2>/dev/null | head -1 || true)"
[[ -n "$C_RUN_DIR" ]] || fail "C output directory not found"
C_DETECTED_CASES="$C_RUN_DIR/detected_cases.csv"
[[ -s "$C_DETECTED_CASES" ]] || fail "C detected cases not found or empty: $C_DETECTED_CASES"

section "D full using ${C_DETECTED_CASES}"
D_RUN_MODE=full \
D_EXPERIMENTS_ROOT=data/experiments_v5 \
D_QUESTIONS=data/experiments_v5/questions/v5_questions.csv \
D_ENABLE_RERANK=false \
D_REBUILD_INDEX=true \
D_CONDITIONS=muted_blackbox_on,muted_whitebox_on \
D_DETECTED_CASES="$C_DETECTED_CASES" \
bash experiment_D/experiments/run_study_d_v5.sh

section "done"
echo "A latest: $(ls -td outputs/experiments_v5/A_rerank_off/v3_* 2>/dev/null | head -1 || true)"
echo "C latest: $C_RUN_DIR"
echo "D latest: $(ls -td outputs/experiments_v5/D_rerank_off/study_d_v5_* 2>/dev/null | head -1 || true)"
