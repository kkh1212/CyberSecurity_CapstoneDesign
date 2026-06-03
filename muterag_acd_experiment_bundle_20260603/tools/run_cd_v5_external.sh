#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

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

DATA_ROOT="${CD_DATA_ROOT:-data/experiments_v5_external}"
QUESTIONS="${CD_QUESTIONS:-${DATA_ROOT}/questions/v5_questions.csv}"
PROFILE="${CD_RETRIEVAL_PROFILE:-rerank_off}"

if [[ "$PROFILE" == "rerank_on" ]]; then
  ENABLE_RERANK=true
else
  ENABLE_RERANK=false
fi

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
export OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:4b}"

if command -v curl >/dev/null 2>&1; then
  curl -fsS "${OLLAMA_BASE_URL}/api/version" >/dev/null \
    || fail "Cannot reach Ollama at OLLAMA_BASE_URL=${OLLAMA_BASE_URL}"
fi

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

mkdir -p logs
mkdir -p "$DATA_ROOT/experiment_normal" "$DATA_ROOT/experiment_muted_blackbox" "$DATA_ROOT/experiment_muted_whitebox" "$DATA_ROOT/questions"

[[ -s "$QUESTIONS" ]] || fail "Questions CSV not found or empty: $QUESTIONS"
for required_dir in "$DATA_ROOT/experiment_normal" "$DATA_ROOT/experiment_muted_blackbox" "$DATA_ROOT/experiment_muted_whitebox"; do
  if ! find "$required_dir" -maxdepth 1 -type f ! -name '.gitkeep' | grep -q .; then
    fail "No documents found in $required_dir"
  fi
done

RUN_STAMP="$(date +%Y%m%d_%H%M%S)"
C_OUTPUT_ROOT="${C2_OUTPUT_ROOT:-outputs/external_v5/C_${PROFILE}}"
D_OUTPUT_ROOT="${D_OUTPUT_ROOT:-outputs/external_v5/D_${PROFILE}}"
C_RUN_ID="${C2_RUN_ID:-study_c2_external_v5_${RUN_STAMP}}"
D_RUN_ID="${D_RUN_ID:-study_d_external_v5_${RUN_STAMP}}"

section "C external: normal + muted detection"
C2_RUN_ID="$C_RUN_ID" \
C2_RUN_MODE="${C2_RUN_MODE:-full}" \
C2_EXPERIMENTS_ROOT="$DATA_ROOT" \
C2_QUESTIONS="$QUESTIONS" \
C2_OUTPUT_ROOT="$C_OUTPUT_ROOT" \
C2_EXPECTED_QUESTIONS="" \
C2_ENABLE_DENSE="${C2_ENABLE_DENSE:-true}" \
C2_ENABLE_RERANK="$ENABLE_RERANK" \
C2_REBUILD_INDEX="${C2_REBUILD_INDEX:-true}" \
C2_CONDITIONS="${C2_CONDITIONS:-normal_on,muted_blackbox_on,muted_whitebox_on}" \
C2_SEMANTIC_ACTIONS="${C2_SEMANTIC_ACTIONS:-log_only,drop_chunk}" \
bash experiment_C/experiments/run_study_c2_v5.sh

C_RUN_DIR="${C_OUTPUT_ROOT}/${C_RUN_ID}"
[[ -d "$C_RUN_DIR" ]] || C_RUN_DIR="$(ls -td "${C_OUTPUT_ROOT}"/study_c2_external_v5_* 2>/dev/null | head -1 || true)"
[[ -n "$C_RUN_DIR" && -d "$C_RUN_DIR" ]] || fail "C output directory not found under $C_OUTPUT_ROOT"

C_DETECTED_CASES="$C_RUN_DIR/detected_cases.csv"
if [[ ! -s "$C_DETECTED_CASES" ]]; then
  echo "[$(timestamp)] WARN: C detected zero muted cases; D will be skipped."
  echo "C latest: $C_RUN_DIR"
  exit 0
fi

section "D external: sanitize C-detected muted cases"
D_RUN_ID="$D_RUN_ID" \
D_RUN_MODE="${D_RUN_MODE:-full}" \
D_EXPERIMENTS_ROOT="$DATA_ROOT" \
D_QUESTIONS="$QUESTIONS" \
D_OUTPUT_ROOT="$D_OUTPUT_ROOT" \
D_ENABLE_DENSE="${D_ENABLE_DENSE:-true}" \
D_ENABLE_RERANK="$ENABLE_RERANK" \
D_REBUILD_INDEX="${D_REBUILD_INDEX:-true}" \
D_CONDITIONS="${D_CONDITIONS:-muted_blackbox_on,muted_whitebox_on}" \
D_DETECTED_CASES="$C_DETECTED_CASES" \
bash experiment_D/experiments/run_study_d_v5.sh

D_RUN_DIR="${D_OUTPUT_ROOT}/${D_RUN_ID}"
[[ -d "$D_RUN_DIR" ]] || D_RUN_DIR="$(ls -td "${D_OUTPUT_ROOT}"/study_d_external_v5_* 2>/dev/null | head -1 || true)"

section "done"
echo "C latest: $C_RUN_DIR"
echo "C detected cases: $C_DETECTED_CASES"
echo "D latest: $D_RUN_DIR"
echo
echo "Summary:"
python tools/summarize_cd_v5_external.py --c-run "$C_RUN_DIR" --d-run "$D_RUN_DIR"
