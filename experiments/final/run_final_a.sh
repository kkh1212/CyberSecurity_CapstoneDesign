#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${PROJECT_ROOT}/.env"
    set +a
fi

FINAL_CORPUS_DIR="${FINAL_CORPUS_DIR:-${PROJECT_ROOT}/data/exp_corpus/final_strong_v4}"
TS="$(date +%Y%m%d_%H%M%S)"
RUN_ID="${RUN_ID:-final_a_${TS}}"
RESULTS_ROOT="${PROJECT_ROOT}/experiments/results/${RUN_ID}"
STAGE_ROOT="${PROJECT_ROOT}/data/final_stage/${RUN_ID}"
INDEX_ROOT="${PROJECT_ROOT}/outputs/final_indexes/${RUN_ID}"
QUERIES_JSON="${RESULTS_ROOT}/queries_final_experiment.json"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:12b}"
A_MAX_QUESTIONS="${A_MAX_QUESTIONS:-0}"
A_ATTACK_MODE="${A_ATTACK_MODE:-rate}"
A_ATTACK_RATE="${A_ATTACK_RATE:-0.05}"
A_ATTACK_CONDITION="${A_ATTACK_CONDITION:-A_normal_muted_05}"
FINAL_TOP_K="${FINAL_TOP_K:-5}"
SPARSE_TOP_K="${SPARSE_TOP_K:-30}"
RANDOM_SEED="${RANDOM_SEED:-42}"
FINAL_EXTERNAL_GUARDRAIL_ENABLED="${FINAL_EXTERNAL_GUARDRAIL_ENABLED:-${EXTERNAL_GUARDRAIL_ENABLED:-true}}"
FINAL_EXTERNAL_GUARDRAIL_PROVIDER="${FINAL_EXTERNAL_GUARDRAIL_PROVIDER:-${EXTERNAL_GUARDRAIL_PROVIDER:-meta_prompt_guard}}"
FINAL_EXTERNAL_GUARDRAIL_STAGES="${FINAL_EXTERNAL_GUARDRAIL_STAGES:-${EXTERNAL_GUARDRAIL_STAGES:-context}}"
FINAL_EXTERNAL_GUARDRAIL_ACTION="${FINAL_EXTERNAL_GUARDRAIL_ACTION:-${EXTERNAL_GUARDRAIL_ACTION:-block}}"
FINAL_EXTERNAL_GUARDRAIL_FAIL_MODE="${FINAL_EXTERNAL_GUARDRAIL_FAIL_MODE:-${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}}"
FINAL_EXTERNAL_GUARDRAIL_API_KEY="${FINAL_EXTERNAL_GUARDRAIL_API_KEY:-${EXTERNAL_GUARDRAIL_API_KEY:-}}"
FINAL_EXTERNAL_GUARDRAIL_TIMEOUT_SEC="${FINAL_EXTERNAL_GUARDRAIL_TIMEOUT_SEC:-${EXTERNAL_GUARDRAIL_TIMEOUT_SEC:-10}}"
FINAL_EXTERNAL_GUARDRAIL_MODEL="${FINAL_EXTERNAL_GUARDRAIL_MODEL:-${EXTERNAL_GUARDRAIL_MODEL:-meta-llama/Prompt-Guard-86M}}"
FINAL_EXTERNAL_GUARDRAIL_THRESHOLD="${FINAL_EXTERNAL_GUARDRAIL_THRESHOLD:-${EXTERNAL_GUARDRAIL_THRESHOLD:-0.75}}"
FINAL_EXTERNAL_GUARDRAIL_MAX_CHARS="${FINAL_EXTERNAL_GUARDRAIL_MAX_CHARS:-${EXTERNAL_GUARDRAIL_MAX_CHARS:-8000}}"
CAPSTONE_VENV_PYTHON="${PROJECT_ROOT}/../test/.venv/bin/python"
if [[ -x "${CAPSTONE_VENV_PYTHON}" ]]; then
    PYTHON_BIN="${PYTHON_BIN:-${CAPSTONE_VENV_PYTHON}}"
else
    PYTHON_BIN="${PYTHON_BIN:-python}"
fi

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found"; }
require_cmd jq
require_cmd "${PYTHON_BIN}"
curl -sf "${OLLAMA_BASE_URL}/api/tags" >/dev/null 2>&1 || die "Ollama unavailable: ${OLLAMA_BASE_URL}"
# shellcheck source=/dev/null
source "${PROJECT_ROOT}/experiments/final/final_common.sh"

run_python() {
    local env_prefix=() remaining=() in_cmd=false
    for arg in "$@"; do
        if [[ "${in_cmd}" == "true" ]]; then remaining+=("$arg")
        elif [[ "$arg" == "--cmd" ]]; then in_cmd=true
        else env_prefix+=("$arg")
        fi
    done
    (cd "${PROJECT_ROOT}" && env "${env_prefix[@]}" "${remaining[@]}")
}

prepare_condition() {
    local condition="$1" mode="$2" rate="$3"
    stage_fixed_condition "${condition}" "${mode}" "${rate}" "${STAGE_ROOT}/${condition}" "${QUERIES_JSON}"
}

ingest_condition() {
    local condition="$1"
    log "INGEST ${condition}"
    rm -rf "${INDEX_ROOT}/${condition}"
    mkdir -p "${INDEX_ROOT}/${condition}"
    run_python \
        "RAW_DOCS_DIR=${STAGE_ROOT}/${condition}" \
        "INDEX_DIR=${INDEX_ROOT}/${condition}" \
        "DETECTOR_ENABLED=false" \
        "DETECTOR_DEBUG=false" \
        "ENABLE_DENSE=false" \
        "ENABLE_RERANK=false" \
        "DOMAIN=all" \
        --cmd "${PYTHON_BIN}" -m src.ingest_app
}

run_queries() {
    local condition="$1"
    local out_dir="${RESULTS_ROOT}/${condition}"
    mkdir -p "${out_dir}"
    local n_queries
    n_queries="$(jq '.attack | length' "${QUERIES_JSON}")"
    if [[ "${A_MAX_QUESTIONS}" =~ ^[0-9]+$ ]] && [[ "${A_MAX_QUESTIONS}" -gt 0 ]] && [[ "${A_MAX_QUESTIONS}" -lt "${n_queries}" ]]; then
        n_queries="${A_MAX_QUESTIONS}"
    fi
    log "QUERY ${condition} n=${n_queries} defense=off model_guardrail=implicit"
    local common_envs=(
        "RAW_DOCS_DIR=${STAGE_ROOT}/${condition}"
        "INDEX_DIR=${INDEX_ROOT}/${condition}"
        "RUNTIME_DETECTOR_ENABLED=false"
        "RUNTIME_SANITIZER_ENABLED=false"
        "EXTERNAL_GUARDRAIL_ENABLED=${FINAL_EXTERNAL_GUARDRAIL_ENABLED}"
        "EXTERNAL_GUARDRAIL_PROVIDER=${FINAL_EXTERNAL_GUARDRAIL_PROVIDER}"
        "EXTERNAL_GUARDRAIL_STAGES=${FINAL_EXTERNAL_GUARDRAIL_STAGES}"
        "EXTERNAL_GUARDRAIL_ACTION=${FINAL_EXTERNAL_GUARDRAIL_ACTION}"
        "EXTERNAL_GUARDRAIL_FAIL_MODE=${FINAL_EXTERNAL_GUARDRAIL_FAIL_MODE}"
        "EXTERNAL_GUARDRAIL_API_KEY=${FINAL_EXTERNAL_GUARDRAIL_API_KEY}"
        "EXTERNAL_GUARDRAIL_TIMEOUT_SEC=${FINAL_EXTERNAL_GUARDRAIL_TIMEOUT_SEC}"
        "EXTERNAL_GUARDRAIL_MODEL=${FINAL_EXTERNAL_GUARDRAIL_MODEL}"
        "EXTERNAL_GUARDRAIL_THRESHOLD=${FINAL_EXTERNAL_GUARDRAIL_THRESHOLD}"
        "EXTERNAL_GUARDRAIL_MAX_CHARS=${FINAL_EXTERNAL_GUARDRAIL_MAX_CHARS}"
        "ENABLE_DENSE=false"
        "ENABLE_RERANK=false"
        "SPARSE_TOP_K=${SPARSE_TOP_K}"
        "RERANK_TOP_K=${SPARSE_TOP_K}"
        "FINAL_TOP_K=${FINAL_TOP_K}"
        "OLLAMA_BASE_URL=${OLLAMA_BASE_URL}"
        "OLLAMA_MODEL=${OLLAMA_MODEL}"
        "MUTEDRAG_ATTACK_EVAL=true"
    )
    for i in $(seq 1 "${n_queries}"); do
        local idx query outfile
        idx="$(printf "%02d" "$i")"
        query="$(jq -r ".attack[$((i-1))].text" "${QUERIES_JSON}")"
        outfile="${out_dir}/mode_a_attack_${idx}.txt"
        log "  Q${idx}: ${query:0:64}"
        echo "${query}" | run_python "${common_envs[@]}" --cmd "${PYTHON_BIN}" -m src.query_app > "${outfile}" 2>&1 || true
    done
}

measure_condition() {
    local condition="$1"
    log "MEASURE ${condition}"
    (cd "${PROJECT_ROOT}" && "${PYTHON_BIN}" -m experiments.eval.measure_asr \
        --results-dir "${RESULTS_ROOT}/${condition}" \
        --queries "${QUERIES_JSON}")
}

write_run_metadata() {
    cat > "${RESULTS_ROOT}/run_metadata.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "experiment": "A",
  "ollama_model": "${OLLAMA_MODEL}",
  "ollama_base_url": "${OLLAMA_BASE_URL}",
  "final_corpus_dir": "${FINAL_CORPUS_DIR}",
  "attack_mode": "${A_ATTACK_MODE}",
  "attack_rate": "${A_ATTACK_RATE}",
  "attack_condition": "${A_ATTACK_CONDITION}",
  "max_questions": "${A_MAX_QUESTIONS}",
  "final_top_k": "${FINAL_TOP_K}",
  "sparse_top_k": "${SPARSE_TOP_K}",
  "enable_dense": "false",
  "enable_rerank": "false",
  "mutedrag_attack_eval": "true",
  "external_guardrail_enabled": "${FINAL_EXTERNAL_GUARDRAIL_ENABLED}",
  "external_guardrail_provider": "${FINAL_EXTERNAL_GUARDRAIL_PROVIDER}",
  "external_guardrail_model": "${FINAL_EXTERNAL_GUARDRAIL_MODEL}",
  "external_guardrail_threshold": "${FINAL_EXTERNAL_GUARDRAIL_THRESHOLD}",
  "created_at": "$(date -Iseconds)"
}
EOF
}

main() {
    mkdir -p "${RESULTS_ROOT}" "${STAGE_ROOT}" "${INDEX_ROOT}"
    write_run_metadata
    log "RUN_ID=${RUN_ID} MODEL=${OLLAMA_MODEL} A_ATTACK_MODE=${A_ATTACK_MODE} A_ATTACK_RATE=${A_ATTACK_RATE}"
    prepare_condition A_normal_only normal_only 0
    ingest_condition A_normal_only
    run_queries A_normal_only
    measure_condition A_normal_only

    prepare_condition "${A_ATTACK_CONDITION}" "${A_ATTACK_MODE}" "${A_ATTACK_RATE}"
    ingest_condition "${A_ATTACK_CONDITION}"
    run_queries "${A_ATTACK_CONDITION}"
    measure_condition "${A_ATTACK_CONDITION}"

    log "DONE ${RESULTS_ROOT}"
}

main "$@"
