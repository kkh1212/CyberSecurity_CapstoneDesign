#!/usr/bin/env bash
# =============================================================================
# Study C - Detector performance experiment
#
# Conditions:
#   C_normal_only   benign corpus only
#   C_normal_direct benign corpus + direct injection payloads
#   C_normal_muted  benign corpus + MutedRAG-style payloads
#
# Detector is enabled. Runtime sanitizer is disabled. The script measures both
# ingestion-time detector decisions and query-time ASR under detector-only mode.
#
# Defaults:
#   C_DIRECT_TYPES="01_직접인젝션"
#   C_MUTED_TYPES="02_간접_명시형"
#   C_QUERY_SET="attack"
#
# Usage:
#   ./experiments/run_study_c.sh
# =============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPERIMENTS_DIR="${PROJECT_ROOT}/experiments"
QUERIES_JSON="${EXPERIMENTS_DIR}/queries.json"

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:8b}"
RANDOM_SEED="${RANDOM_SEED:-42}"
C_DIRECT_TYPES="${C_DIRECT_TYPES:-01_직접인젝션}"
C_MUTED_TYPES="${C_MUTED_TYPES:-02_간접_명시형}"
C_QUERY_SET="${C_QUERY_SET:-attack}"

STUDY_ID="study_c_$(date +%Y%m%d_%H%M%S)"
RESULTS_ROOT="${EXPERIMENTS_DIR}/results/${STUDY_ID}"
STAGE_ROOT="${PROJECT_ROOT}/data/exp_stage_study_c"
INDEX_ROOT="${PROJECT_ROOT}/outputs/exp_study_c_indexes"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found. Install: $2"; }

require_cmd python "python3 가상환경을 활성화하세요"
require_cmd jq "sudo apt-get install -y jq"
curl -sf "${OLLAMA_BASE_URL}/api/tags" >/dev/null 2>&1 \
  || die "Ollama에 연결할 수 없습니다: ${OLLAMA_BASE_URL}"

run_python() {
  local envs=("${@}")
  local env_prefix=""
  local remaining=()
  local in_cmd=false
  for arg in "${envs[@]}"; do
    if [[ "${in_cmd}" == "true" ]]; then
      remaining+=("${arg}")
    elif [[ "${arg}" == "--cmd" ]]; then
      in_cmd=true
    else
      env_prefix="${env_prefix} ${arg}"
    fi
  done
  (cd "${PROJECT_ROOT}" && env ${env_prefix} "${remaining[@]}")
}

stage_condition() {
  local condition="$1"
  local stage_dir="$2"
  shift 2
  local inject_args=("$@")

  log "STEP 1 [${condition}]: staging corpus"
  rm -rf "${stage_dir}"
  (cd "${PROJECT_ROOT}" && python -m experiments.attack.inject \
    --strategy blackbox \
    --stage-dir "${stage_dir}" \
    --seed "${RANDOM_SEED}" \
    "${inject_args[@]}")
}

ingest_condition() {
  local condition="$1"
  local stage_dir="$2"
  local index_dir="$3"

  log "STEP 2 [${condition}]: ingest detector ON"
  rm -rf "${index_dir}"
  mkdir -p "${index_dir}"
  run_python \
    "RAW_DOCS_DIR=${stage_dir}" \
    "INDEX_DIR=${index_dir}" \
    "DETECTOR_ENABLED=true" \
    "DETECTOR_DEBUG=false" \
    "DETECTOR_PROFILE=balanced" \
    "ENABLE_DENSE=false" \
    "ENABLE_RERANK=false" \
    "DOMAIN=all" \
    --cmd python -m src.ingest_app
}

measure_condition() {
  local condition="$1"
  local index_dir="$2"
  local out_dir="$3"

  log "STEP 3 [${condition}]: measure detector"
  mkdir -p "${out_dir}"
  (cd "${PROJECT_ROOT}" && python -m experiments.eval.measure_detector \
    --index-dir "${index_dir}" \
    --out-dir "${out_dir}" \
    --condition "${condition}")
}

query_filter() {
  if [[ "${C_QUERY_SET}" == "all" ]]; then
    echo '.benign + .attack'
  else
    echo ".${C_QUERY_SET}"
  fi
}

run_condition_queries() {
  local condition="$1"
  local stage_dir="$2"
  local index_dir="$3"
  local out_dir="$4"
  local filter
  filter="$(query_filter)"

  mkdir -p "${out_dir}"
  local n_queries
  n_queries=$(jq "(${filter}) | length" "${QUERIES_JSON}")
  [[ "${n_queries}" -gt 0 ]] || die "No queries found: ${C_QUERY_SET}"

  log "STEP 4 [${condition}]: ${C_QUERY_SET} queries (${n_queries})"
  local common_envs=(
    "RAW_DOCS_DIR=${stage_dir}"
    "INDEX_DIR=${index_dir}"
    "RUNTIME_DETECTOR_ENABLED=true"
    "RUNTIME_SANITIZER_ENABLED=false"
    "ENABLE_DENSE=false"
    "ENABLE_RERANK=false"
    "SPARSE_TOP_K=30"
    "RERANK_TOP_K=30"
    "FINAL_TOP_K=5"
    "OLLAMA_BASE_URL=${OLLAMA_BASE_URL}"
    "OLLAMA_MODEL=${OLLAMA_MODEL}"
  )

  for i in $(seq 1 "${n_queries}"); do
    local query idx_pad outfile
    query=$(jq -r "(${filter})[$((i-1))].text" "${QUERIES_JSON}")
    idx_pad=$(printf "%02d" "${i}")
    outfile="${out_dir}/mode_b_attack_${idx_pad}.txt"
    log "  [${condition}] Q${idx_pad}: ${query:0:50}..."
    echo "${query}" | run_python "${common_envs[@]}" "MUTEDRAG_ATTACK_EVAL=true" --cmd python -m src.query_app \
      > "${outfile}" 2>&1 || true
  done
}

measure_asr_condition() {
  local condition="$1"
  local out_dir="$2"
  log "STEP 5 [${condition}]: measure ASR"
  (cd "${PROJECT_ROOT}" && python -m experiments.eval.measure_asr \
    --results-dir "${out_dir}" \
    --queries "${QUERIES_JSON}")
}

append_summary() {
  local condition="$1"
  local out_dir="$2"
  local combined="${RESULTS_ROOT}/study_c_summary.csv"

  if [[ ! -f "${combined}" ]]; then
    echo "condition,unit,total,TP,FP,TN,FN,precision,recall,fpr,accuracy,f1" > "${combined}"
  fi

  for file in "${out_dir}/detector_chunk_summary.csv" "${out_dir}/detector_document_summary.csv"; do
    tail -n +2 "${file}" >> "${combined}"
  done
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
  measure_condition "${condition}" "${index_dir}" "${out_dir}"
  run_condition_queries "${condition}" "${stage_dir}" "${index_dir}" "${out_dir}"
  measure_asr_condition "${condition}" "${out_dir}"
  append_summary "${condition}" "${out_dir}"
}

main() {
  log "=== Study C 시작 ==="
  log "RESULTS_ROOT=${RESULTS_ROOT}"
  log "OLLAMA_MODEL=${OLLAMA_MODEL}"
  log "C_DIRECT_TYPES=${C_DIRECT_TYPES}"
  log "C_MUTED_TYPES=${C_MUTED_TYPES}"
  log "C_QUERY_SET=${C_QUERY_SET}"

  mkdir -p "${RESULTS_ROOT}" "${STAGE_ROOT}" "${INDEX_ROOT}"

  run_condition "C_normal_only" --no-attack

  read -r -a direct_types <<< "${C_DIRECT_TYPES}"
  run_condition "C_normal_direct" --types "${direct_types[@]}"

  read -r -a muted_types <<< "${C_MUTED_TYPES}"
  run_condition "C_normal_muted" --types "${muted_types[@]}"

  log ""
  log "=== Study C 완료 ==="
  log "결과 디렉토리: ${RESULTS_ROOT}"
  log "통합 요약: ${RESULTS_ROOT}/study_c_summary.csv"
}

main "$@"
