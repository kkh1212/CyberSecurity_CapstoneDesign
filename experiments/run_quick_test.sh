#!/usr/bin/env bash
# =============================================================================
# Quick smoke test for Study A — targets ~10 min total
#
# Differences from run_study_a.sh:
#   - A_MAX_QUESTIONS=3 (3 queries per condition, 9 LLM calls total)
#   - Skips ingest if index already exists (reuses prior run's index)
#   - Sources .env automatically
#
# Usage:
#   bash experiments/run_quick_test.sh
# =============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env from project root if present
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${PROJECT_ROOT}/.env"
    set +a
fi

EXPERIMENTS_DIR="${PROJECT_ROOT}/experiments"
QUERIES_JSON="${EXPERIMENTS_DIR}/queries.json"

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:12b}"
CONTAMINATION_RATE="${CONTAMINATION_RATE:-0.01}"
INJECT_STRATEGY="${INJECT_STRATEGY:-blackbox}"
RANDOM_SEED="${RANDOM_SEED:-42}"

A_DIRECT_TYPES="${A_DIRECT_TYPES:-01_직접인젝션}"
A_MUTED_TYPES="${A_MUTED_TYPES:-05_순수서술형}"
A_QUERY_SET="${A_QUERY_SET:-all}"
A_MAX_QUESTIONS="${A_MAX_QUESTIONS:-3}"   # quick: 3 queries per condition

EXTERNAL_GUARDRAIL_ENABLED="${EXTERNAL_GUARDRAIL_ENABLED:-false}"
EXTERNAL_GUARDRAIL_PROVIDER="${EXTERNAL_GUARDRAIL_PROVIDER:-off}"
EXTERNAL_GUARDRAIL_STAGES="${EXTERNAL_GUARDRAIL_STAGES:-context}"
EXTERNAL_GUARDRAIL_ACTION="${EXTERNAL_GUARDRAIL_ACTION:-flag_only}"
EXTERNAL_GUARDRAIL_FAIL_MODE="${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}"
EXTERNAL_GUARDRAIL_API_URL="${EXTERNAL_GUARDRAIL_API_URL:-}"
EXTERNAL_GUARDRAIL_API_KEY="${EXTERNAL_GUARDRAIL_API_KEY:-}"
EXTERNAL_GUARDRAIL_TIMEOUT_SEC="${EXTERNAL_GUARDRAIL_TIMEOUT_SEC:-10}"

if [[ "${EXTERNAL_GUARDRAIL_ENABLED,,}" == "true" || "${EXTERNAL_GUARDRAIL_ENABLED}" == "1" ]]; then
    GUARDRAIL_SUFFIX="guardrail_${EXTERNAL_GUARDRAIL_PROVIDER}"
else
    GUARDRAIL_SUFFIX="guardrail_off"
fi
GUARDRAIL_SUFFIX="$(echo "${GUARDRAIL_SUFFIX}" | tr -cd '[:alnum:]_-')"

STUDY_ID="quick_$(date +%Y%m%d_%H%M%S)_${GUARDRAIL_SUFFIX}"
RESULTS_ROOT="${EXPERIMENTS_DIR}/results/${STUDY_ID}"
STAGE_ROOT="${PROJECT_ROOT}/data/exp_stage_study_a"
INDEX_ROOT="${PROJECT_ROOT}/outputs/exp_study_a_indexes"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found."; }
require_cmd jq
require_cmd python
curl -sf "${OLLAMA_BASE_URL}/api/tags" >/dev/null 2>&1 \
    || die "Ollama에 연결할 수 없습니다: ${OLLAMA_BASE_URL}"

run_python() {
    local envs=("${@}")
    local env_prefix=()
    local remaining=()
    local in_cmd=false
    for arg in "${envs[@]}"; do
        if [[ "$in_cmd" == "true" ]]; then
            remaining+=("$arg")
        elif [[ "$arg" == "--cmd" ]]; then
            in_cmd=true
        else
            env_prefix+=("$arg")
        fi
    done
    (cd "${PROJECT_ROOT}" && env "${env_prefix[@]}" "${remaining[@]}")
}

stage_condition() {
    local condition="$1" stage_dir="$2"
    shift 2
    log "STAGE [${condition}]: corpus staging"
    rm -rf "${stage_dir}"
    (cd "${PROJECT_ROOT}" && python -m experiments.attack.inject \
        --rate "${CONTAMINATION_RATE}" \
        --strategy "${INJECT_STRATEGY}" \
        --stage-dir "${stage_dir}" \
        --seed "${RANDOM_SEED}" \
        "$@")
}

ingest_condition() {
    local condition="$1" stage_dir="$2" index_dir="$3"
    if [[ -f "${index_dir}/bm25.pkl" ]]; then
        log "INGEST [${condition}]: index exists — skipping (reusing ${index_dir})"
        return
    fi
    log "INGEST [${condition}]: building index"
    rm -rf "${index_dir}"
    mkdir -p "${index_dir}"
    run_python \
        "RAW_DOCS_DIR=${stage_dir}" \
        "INDEX_DIR=${index_dir}" \
        "DETECTOR_ENABLED=false" \
        "DETECTOR_DEBUG=false" \
        "ENABLE_DENSE=false" \
        "ENABLE_RERANK=false" \
        "DOMAIN=all" \
        --cmd python -m src.ingest_app
}

run_queries() {
    local condition="$1" stage_dir="$2" index_dir="$3" out_dir="$4"
    mkdir -p "${out_dir}"

    local query_filter
    if [[ "${A_QUERY_SET}" == "all" ]]; then
        query_filter='.benign + .attack'
    else
        query_filter=".${A_QUERY_SET}"
    fi

    local n_queries
    n_queries=$(jq "(${query_filter}) | length" "${QUERIES_JSON}")
    [[ "${n_queries}" -gt 0 ]] || die "No queries: ${A_QUERY_SET}"
    if [[ "${A_MAX_QUESTIONS}" =~ ^[0-9]+$ ]] && [[ "${A_MAX_QUESTIONS}" -gt 0 ]] && [[ "${A_MAX_QUESTIONS}" -lt "${n_queries}" ]]; then
        n_queries="${A_MAX_QUESTIONS}"
    fi

    log "QUERY [${condition}]: ${n_queries} questions"
    local common_envs=(
        "RAW_DOCS_DIR=${stage_dir}"
        "INDEX_DIR=${index_dir}"
        "RUNTIME_DETECTOR_ENABLED=false"
        "RUNTIME_SANITIZER_ENABLED=false"
        "ENABLE_DENSE=false"
        "ENABLE_RERANK=false"
        "SPARSE_TOP_K=30"
        "RERANK_TOP_K=30"
        "FINAL_TOP_K=5"
        "OLLAMA_BASE_URL=${OLLAMA_BASE_URL}"
        "OLLAMA_MODEL=${OLLAMA_MODEL}"
        "STUDY_A_CONDITION=${condition}"
        "STUDY_A_QUERY_SET=${A_QUERY_SET}"
        "EXTERNAL_GUARDRAIL_ENABLED=${EXTERNAL_GUARDRAIL_ENABLED}"
        "EXTERNAL_GUARDRAIL_PROVIDER=${EXTERNAL_GUARDRAIL_PROVIDER}"
        "EXTERNAL_GUARDRAIL_STAGES=${EXTERNAL_GUARDRAIL_STAGES}"
        "EXTERNAL_GUARDRAIL_ACTION=${EXTERNAL_GUARDRAIL_ACTION}"
        "EXTERNAL_GUARDRAIL_FAIL_MODE=${EXTERNAL_GUARDRAIL_FAIL_MODE}"
        "EXTERNAL_GUARDRAIL_API_URL=${EXTERNAL_GUARDRAIL_API_URL}"
        "EXTERNAL_GUARDRAIL_API_KEY=${EXTERNAL_GUARDRAIL_API_KEY}"
        "EXTERNAL_GUARDRAIL_TIMEOUT_SEC=${EXTERNAL_GUARDRAIL_TIMEOUT_SEC}"
    )

    for i in $(seq 1 "${n_queries}"); do
        local query idx_pad outfile
        query=$(jq -r "(${query_filter})[$((i-1))].text" "${QUERIES_JSON}")
        idx_pad=$(printf "%02d" "$i")
        outfile="${out_dir}/mode_a_attack_${idx_pad}.txt"
        log "  Q${idx_pad}: ${query:0:50}..."
        echo "${query}" | run_python "${common_envs[@]}" \
            "MUTEDRAG_ATTACK_EVAL=true" \
            "STUDY_A_QUERY_INDEX=${idx_pad}" \
            --cmd python -m src.query_app \
            > "${outfile}" 2>&1 || true
    done
}

measure_condition() {
    local condition="$1" out_dir="$2"
    log "MEASURE [${condition}]"
    (cd "${PROJECT_ROOT}" && python -m experiments.eval.measure_asr \
        --results-dir "${out_dir}" \
        --queries "${QUERIES_JSON}")
}

run_condition() {
    local condition="$1"
    shift
    local stage_dir="${STAGE_ROOT}/${condition}"
    local index_dir="${INDEX_ROOT}/${condition}"
    local out_dir="${RESULTS_ROOT}/${condition}"

    log ""
    log "=== ${condition} ==="
    stage_condition "${condition}" "${stage_dir}" "$@"
    ingest_condition "${condition}" "${stage_dir}" "${index_dir}"
    run_queries "${condition}" "${stage_dir}" "${index_dir}" "${out_dir}"
    measure_condition "${condition}" "${out_dir}"
}

main() {
    log "=== Quick Test 시작 (3 queries × 3 conditions) ==="
    log "RESULTS_ROOT=${RESULTS_ROOT}"
    log "OLLAMA_MODEL=${OLLAMA_MODEL}"
    log "A_MUTED_TYPES=${A_MUTED_TYPES}"
    log "EXTERNAL_GUARDRAIL_ENABLED=${EXTERNAL_GUARDRAIL_ENABLED}"
    log "EXTERNAL_GUARDRAIL_PROVIDER=${EXTERNAL_GUARDRAIL_PROVIDER}"

    mkdir -p "${RESULTS_ROOT}" "${STAGE_ROOT}" "${INDEX_ROOT}"

    run_condition "A_normal_only"  --no-attack

    read -r -a direct_types <<< "${A_DIRECT_TYPES}"
    run_condition "A_normal_direct" --types "${direct_types[@]}"

    read -r -a muted_types <<< "${A_MUTED_TYPES}"
    run_condition "A_normal_muted"  --types "${muted_types[@]}"

    log ""
    log "=== Quick Test 완료 ==="
    log "결과: ${RESULTS_ROOT}"
}

main "$@"
