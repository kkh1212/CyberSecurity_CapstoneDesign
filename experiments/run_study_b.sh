#!/usr/bin/env bash
# =============================================================================
# Study B - Attack chunk contamination experiment
#
# Conditions:
#   Same benign corpus + MutedRAG-style attack payloads at different chunk ratios.
#
# Security logic is disabled in every condition.
#
# Defaults:
#   B_RATES="0 0.01 0.03 0.05 0.10"
#   B_ATTACK_TYPES="02_간접_명시형"
#   B_QUERY_SET="attack"  # target business questions paired with payloads
#
# Usage:
#   OLLAMA_MODEL="gemma3:12b" ./experiments/run_study_b.sh
#   B_QUERY_SET="all" ./experiments/run_study_b.sh
# =============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPERIMENTS_DIR="${PROJECT_ROOT}/experiments"
QUERIES_JSON="${EXPERIMENTS_DIR}/queries.json"

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:12b}"
INJECT_STRATEGY="${INJECT_STRATEGY:-rate}"
RANDOM_SEED="${RANDOM_SEED:-42}"

B_RATES="${B_RATES:-0 0.01 0.03 0.05 0.10}"
B_ATTACK_TYPES="${B_ATTACK_TYPES:-02_간접_명시형}"
B_QUERY_SET="${B_QUERY_SET:-attack}"

STUDY_ID="study_b_$(date +%Y%m%d_%H%M%S)"
RESULTS_ROOT="${EXPERIMENTS_DIR}/results/${STUDY_ID}"
STAGE_ROOT="${PROJECT_ROOT}/data/exp_stage_study_b"
INDEX_ROOT="${PROJECT_ROOT}/outputs/exp_study_b_indexes"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found. Install: $2"; }

require_cmd jq "sudo apt-get install -y jq"
require_cmd python "python3 가상환경을 활성화하세요"
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

rate_label() {
  local rate="$1"
  python - "$rate" <<'PY'
import sys
rate = float(sys.argv[1])
print(f"{int(round(rate * 100)):02d}pct")
PY
}

query_filter() {
  if [[ "${B_QUERY_SET}" == "all" ]]; then
    echo '.benign + .attack'
  else
    echo ".${B_QUERY_SET}"
  fi
}

stage_condition() {
  local condition="$1"
  local rate="$2"
  local stage_dir="$3"
  shift 3
  local attack_types=("$@")

  log "STEP 1 [${condition}]: staging corpus (target rate=${rate})"
  rm -rf "${stage_dir}"
  if [[ "${rate}" == "0" || "${rate}" == "0.0" || "${rate}" == "0.00" ]]; then
    (cd "${PROJECT_ROOT}" && python -m experiments.attack.inject \
      --no-attack \
      --stage-dir "${stage_dir}" \
      --seed "${RANDOM_SEED}")
  else
    (cd "${PROJECT_ROOT}" && python -m experiments.attack.inject \
      --rate "${rate}" \
      --strategy "${INJECT_STRATEGY}" \
      --types "${attack_types[@]}" \
      --stage-dir "${stage_dir}" \
      --seed "${RANDOM_SEED}")
  fi
}

ingest_condition() {
  local condition="$1"
  local stage_dir="$2"
  local index_dir="$3"

  log "STEP 2 [${condition}]: ingest detector OFF"
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
  [[ "${n_queries}" -gt 0 ]] || die "No queries found: ${B_QUERY_SET}"

  log "STEP 3 [${condition}]: ${B_QUERY_SET} queries (${n_queries})"
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
  )

  for i in $(seq 1 "${n_queries}"); do
    local query idx_pad outfile
    query=$(jq -r "(${filter})[$((i-1))].text" "${QUERIES_JSON}")
    idx_pad=$(printf "%02d" "${i}")
    outfile="${out_dir}/mode_a_attack_${idx_pad}.txt"
    log "  [${condition}] Q${idx_pad}: ${query:0:50}..."
    echo "${query}" | run_python "${common_envs[@]}" "MUTEDRAG_ATTACK_EVAL=true" --cmd python -m src.query_app \
      > "${outfile}" 2>&1 || true
  done
}

measure_condition() {
  local condition="$1"
  local out_dir="$2"
  log "STEP 4 [${condition}]: measure"
  (cd "${PROJECT_ROOT}" && python -m experiments.eval.measure_asr \
    --results-dir "${out_dir}" \
    --queries "${QUERIES_JSON}")
}

append_summary() {
  local condition="$1"
  local rate="$2"
  local stage_dir="$3"
  local out_dir="$4"
  local combined="${RESULTS_ROOT}/study_b_summary.csv"

  if [[ ! -f "${combined}" ]]; then
    echo "condition,target_rate,actual_rate,attack_files,attack_chunks,ASR,retrieval_IR,context_IR,I_ASR" > "${combined}"
  fi

  python - "$condition" "$rate" "$stage_dir/inject_summary.json" "$out_dir/asr_summary.csv" "$combined" <<'PY'
import csv, json, sys
condition, target_rate, inject_path, asr_path, combined_path = sys.argv[1:]
with open(inject_path, encoding="utf-8") as f:
    inject = json.load(f)
with open(asr_path, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
row = rows[0] if rows else {}
with open(combined_path, "a", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow([
        condition,
        target_rate,
        inject.get("rate_actual", ""),
        inject.get("attack_files", ""),
        inject.get("attack_chunks", ""),
        row.get("asr_pct", ""),
        row.get("retrieval_ir_pct", ""),
        row.get("context_ir_pct", ""),
        row.get("i_asr_pct", ""),
    ])
PY
}

run_condition() {
  local rate="$1"
  local label
  label="$(rate_label "${rate}")"
  local condition="B_muted_${label}"
  local stage_dir="${STAGE_ROOT}/${condition}"
  local index_dir="${INDEX_ROOT}/${condition}"
  local out_dir="${RESULTS_ROOT}/${condition}"
  read -r -a attack_types <<< "${B_ATTACK_TYPES}"

  log ""
  log "=== ${condition} ==="
  stage_condition "${condition}" "${rate}" "${stage_dir}" "${attack_types[@]}"
  ingest_condition "${condition}" "${stage_dir}" "${index_dir}"
  run_condition_queries "${condition}" "${stage_dir}" "${index_dir}" "${out_dir}"
  measure_condition "${condition}" "${out_dir}"
  append_summary "${condition}" "${rate}" "${stage_dir}" "${out_dir}"
}

main() {
  log "=== Study B 시작 ==="
  log "RESULTS_ROOT=${RESULTS_ROOT}"
  log "OLLAMA_MODEL=${OLLAMA_MODEL}"
  log "B_RATES=${B_RATES}"
  log "B_ATTACK_TYPES=${B_ATTACK_TYPES}"
  log "B_QUERY_SET=${B_QUERY_SET}"

  mkdir -p "${RESULTS_ROOT}" "${STAGE_ROOT}" "${INDEX_ROOT}"

  for rate in ${B_RATES}; do
    run_condition "${rate}"
  done

  log ""
  log "=== Study B 완료 ==="
  log "결과 디렉토리: ${RESULTS_ROOT}"
  log "통합 요약: ${RESULTS_ROOT}/study_b_summary.csv"
}

main "$@"
