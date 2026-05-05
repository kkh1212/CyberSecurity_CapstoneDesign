#!/usr/bin/env bash
# =============================================================================
# Study A Full — guardrail OFF + ON 결과를 한 번 실행으로 생성
#
# 구조:
#   Phase 1: 3개 조건 corpus staging + ingest (인덱스 1회 빌드)
#   Phase 2: 쿼리 실행 (guardrail OFF)  → results/.../study_a_*_guardrail_off/
#   Phase 3: 쿼리 실행 (guardrail ON)   → results/.../study_a_*_guardrail_lakera/
#
# Usage:
#   bash experiments/run_study_a_full.sh
#   A_MAX_QUESTIONS=5 bash experiments/run_study_a_full.sh  # 쿼리 수 제한
# =============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env
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
A_MAX_QUESTIONS="${A_MAX_QUESTIONS:-0}"

# Guardrail ON settings (from .env)
EXTERNAL_GUARDRAIL_ENABLED="${EXTERNAL_GUARDRAIL_ENABLED:-false}"
EXTERNAL_GUARDRAIL_PROVIDER="${EXTERNAL_GUARDRAIL_PROVIDER:-off}"
EXTERNAL_GUARDRAIL_STAGES="${EXTERNAL_GUARDRAIL_STAGES:-context}"
EXTERNAL_GUARDRAIL_ACTION="${EXTERNAL_GUARDRAIL_ACTION:-block}"
EXTERNAL_GUARDRAIL_FAIL_MODE="${EXTERNAL_GUARDRAIL_FAIL_MODE:-open}"
EXTERNAL_GUARDRAIL_API_URL="${EXTERNAL_GUARDRAIL_API_URL:-}"
EXTERNAL_GUARDRAIL_API_KEY="${EXTERNAL_GUARDRAIL_API_KEY:-}"
EXTERNAL_GUARDRAIL_TIMEOUT_SEC="${EXTERNAL_GUARDRAIL_TIMEOUT_SEC:-10}"

TS="$(date +%Y%m%d_%H%M%S)"
RESULTS_OFF="${EXPERIMENTS_DIR}/results/study_a_${TS}_guardrail_off"
RESULTS_ON="${EXPERIMENTS_DIR}/results/study_a_${TS}_guardrail_${EXTERNAL_GUARDRAIL_PROVIDER}"

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
    local envs=("${@}") env_prefix=() remaining=() in_cmd=false
    for arg in "${envs[@]}"; do
        if [[ "$in_cmd" == "true" ]]; then remaining+=("$arg")
        elif [[ "$arg" == "--cmd" ]]; then in_cmd=true
        else env_prefix+=("$arg")
        fi
    done
    (cd "${PROJECT_ROOT}" && env "${env_prefix[@]}" "${remaining[@]}")
}

# ── Phase 1 helpers ──────────────────────────────────────────────────────────

stage_condition() {
    local condition="$1" stage_dir="$2"; shift 2
    log "STAGE [${condition}]"
    rm -rf "${stage_dir}"
    (cd "${PROJECT_ROOT}" && python -m experiments.attack.inject \
        --rate "${CONTAMINATION_RATE}" --strategy "${INJECT_STRATEGY}" \
        --stage-dir "${stage_dir}" --seed "${RANDOM_SEED}" "$@")
}

ingest_condition() {
    local condition="$1" stage_dir="$2" index_dir="$3"
    log "INGEST [${condition}]: building index"
    rm -rf "${index_dir}"; mkdir -p "${index_dir}"
    run_python \
        "RAW_DOCS_DIR=${stage_dir}" "INDEX_DIR=${index_dir}" \
        "DETECTOR_ENABLED=false" "DETECTOR_DEBUG=false" \
        "ENABLE_DENSE=false" "ENABLE_RERANK=false" "DOMAIN=all" \
        --cmd python -m src.ingest_app
}

# ── Phase 2/3 helper ─────────────────────────────────────────────────────────

run_queries() {
    local condition="$1" stage_dir="$2" index_dir="$3" out_dir="$4"
    local guardrail_enabled="$5" guardrail_provider="$6"
    mkdir -p "${out_dir}"

    local query_filter
    [[ "${A_QUERY_SET}" == "all" ]] && query_filter='.benign + .attack' || query_filter=".${A_QUERY_SET}"

    local n_queries
    n_queries=$(jq "(${query_filter}) | length" "${QUERIES_JSON}")
    [[ "${n_queries}" -gt 0 ]] || die "No queries: ${A_QUERY_SET}"
    if [[ "${A_MAX_QUESTIONS}" =~ ^[0-9]+$ ]] && [[ "${A_MAX_QUESTIONS}" -gt 0 ]] && [[ "${A_MAX_QUESTIONS}" -lt "${n_queries}" ]]; then
        n_queries="${A_MAX_QUESTIONS}"
    fi

    log "QUERY [${condition}] guardrail=${guardrail_provider} (${n_queries}쿼리)"
    local common_envs=(
        "RAW_DOCS_DIR=${stage_dir}" "INDEX_DIR=${index_dir}"
        "RUNTIME_DETECTOR_ENABLED=false" "RUNTIME_SANITIZER_ENABLED=false"
        "ENABLE_DENSE=false" "ENABLE_RERANK=false"
        "SPARSE_TOP_K=30" "RERANK_TOP_K=30" "FINAL_TOP_K=5"
        "OLLAMA_BASE_URL=${OLLAMA_BASE_URL}" "OLLAMA_MODEL=${OLLAMA_MODEL}"
        "STUDY_A_CONDITION=${condition}" "STUDY_A_QUERY_SET=${A_QUERY_SET}"
        "EXTERNAL_GUARDRAIL_ENABLED=${guardrail_enabled}"
        "EXTERNAL_GUARDRAIL_PROVIDER=${guardrail_provider}"
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
            "MUTEDRAG_ATTACK_EVAL=true" "STUDY_A_QUERY_INDEX=${idx_pad}" \
            --cmd python -m src.query_app > "${outfile}" 2>&1 || true
    done
}

measure_condition() {
    local condition="$1" out_dir="$2"
    log "MEASURE [${condition}]"
    (cd "${PROJECT_ROOT}" && python -m experiments.eval.measure_asr \
        --results-dir "${out_dir}" --queries "${QUERIES_JSON}")
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    log "=== Study A Full 시작 ==="
    log "OLLAMA_MODEL=${OLLAMA_MODEL}"
    log "A_MUTED_TYPES=${A_MUTED_TYPES} / A_MAX_QUESTIONS=${A_MAX_QUESTIONS}"
    log "결과(OFF): ${RESULTS_OFF}"
    log "결과(ON):  ${RESULTS_ON}"

    mkdir -p "${RESULTS_OFF}" "${RESULTS_ON}" "${STAGE_ROOT}" "${INDEX_ROOT}"

    read -r -a direct_types <<< "${A_DIRECT_TYPES}"
    read -r -a muted_types  <<< "${A_MUTED_TYPES}"

    # ── Phase 1: Staging + Ingest (인덱스 1회) ──────────────────────────────
    log ""; log "=== Phase 1: Ingest ==="
    stage_condition "A_normal_only"   "${STAGE_ROOT}/A_normal_only"   --no-attack
    ingest_condition "A_normal_only"  "${STAGE_ROOT}/A_normal_only"   "${INDEX_ROOT}/A_normal_only"

    stage_condition "A_normal_direct" "${STAGE_ROOT}/A_normal_direct" --types "${direct_types[@]}"
    ingest_condition "A_normal_direct" "${STAGE_ROOT}/A_normal_direct" "${INDEX_ROOT}/A_normal_direct"

    stage_condition "A_normal_muted"  "${STAGE_ROOT}/A_normal_muted"  --types "${muted_types[@]}"
    ingest_condition "A_normal_muted" "${STAGE_ROOT}/A_normal_muted"  "${INDEX_ROOT}/A_normal_muted"

    # ── Phase 2: 쿼리 (guardrail OFF) ───────────────────────────────────────
    log ""; log "=== Phase 2: Queries — guardrail OFF ==="
    for cond in A_normal_only A_normal_direct A_normal_muted; do
        run_queries "${cond}" "${STAGE_ROOT}/${cond}" "${INDEX_ROOT}/${cond}" \
            "${RESULTS_OFF}/${cond}" "false" "off"
        measure_condition "${cond}" "${RESULTS_OFF}/${cond}"
    done

    # ── Phase 3: 쿼리 (guardrail ON) ────────────────────────────────────────
    log ""; log "=== Phase 3: Queries — guardrail ON (${EXTERNAL_GUARDRAIL_PROVIDER}) ==="
    for cond in A_normal_only A_normal_direct A_normal_muted; do
        run_queries "${cond}" "${STAGE_ROOT}/${cond}" "${INDEX_ROOT}/${cond}" \
            "${RESULTS_ON}/${cond}" "${EXTERNAL_GUARDRAIL_ENABLED}" "${EXTERNAL_GUARDRAIL_PROVIDER}"
        measure_condition "${cond}" "${RESULTS_ON}/${cond}"
    done

    log ""; log "=== 완료 ==="
    log "Guardrail OFF: ${RESULTS_OFF}"
    log "Guardrail ON:  ${RESULTS_ON}"
}

main "$@"
