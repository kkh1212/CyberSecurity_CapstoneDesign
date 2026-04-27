#!/usr/bin/env bash
# =============================================================================
# run_exp.sh  —  MutedRAG 재현 실험 자동화 스크립트
#
# 실험 흐름:
#   1. inject.py  : benign + attack 파일로 staging dir 구성 (black-box: 타깃당 1개 악성 텍스트)
#   2. ingest     : 모드별 DETECTOR_ENABLED 설정으로 인덱싱
#   3. query loop : benign + attack 쿼리 실행
#   4. measure    : attack ASR 및 benign false-positive rate 계산
#
# 실험 모드:
#   mode_a  RUNTIME_DETECTOR_ENABLED=false  RUNTIME_SANITIZER_ENABLED=false
#           (ingest 시 DETECTOR_ENABLED=false → attack chunk 무조건 인덱싱)
#   mode_b  RUNTIME_DETECTOR_ENABLED=true   RUNTIME_SANITIZER_ENABLED=false
#           (ingest 시 DETECTOR_ENABLED=true  → attack chunk 일부 quarantine)
#
# 요구사항:
#   - Python 가상환경 활성화 상태 (source .venv/bin/activate)
#   - Ollama 실행 중 (ollama serve or systemd)
#   - jq 설치: sudo apt-get install -y jq
#
# 사용:
#   cd /path/to/project      # capstone/test/
#   source .venv/bin/activate
#   bash experiments/run_exp.sh
#   bash experiments/run_exp.sh --mode a     # mode_a만
#   bash experiments/run_exp.sh --mode b     # mode_b만
#   bash experiments/run_exp.sh --docker     # Docker 사용 (별도 설정 필요)
# =============================================================================

set -euo pipefail

# ─── 설정 ────────────────────────────────────────────────────────────────────

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPERIMENTS_DIR="${PROJECT_ROOT}/experiments"
QUERIES_JSON="${EXPERIMENTS_DIR}/queries.json"

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:12b}"

CONTAMINATION_RATE="${CONTAMINATION_RATE:-0.01}"
INJECT_STRATEGY="${INJECT_STRATEGY:-blackbox}"
ATTACK_TYPES="${ATTACK_TYPES:-01_직접인젝션}"
RANDOM_SEED="42"

STAGE_DIR="${PROJECT_ROOT}/data/exp_stage"
INDEX_DIR_A="${PROJECT_ROOT}/outputs/exp_indexes_mode_a"
INDEX_DIR_B="${PROJECT_ROOT}/outputs/exp_indexes_mode_b"

RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
RESULTS_BASE="${EXPERIMENTS_DIR}/results/${RUN_ID}"

USE_DOCKER=false
RUN_MODE_A=true
RUN_MODE_B=true

# ─── 인자 파싱 ────────────────────────────────────────────────────────────────

for arg in "$@"; do
  case "$arg" in
    --docker)   USE_DOCKER=true ;;
    --mode=a|--mode\ a) RUN_MODE_B=false ;;
    --mode=b|--mode\ b) RUN_MODE_A=false ;;
    --mode) shift ;;
    a) RUN_MODE_B=false ;;
    b) RUN_MODE_A=false ;;
  esac
done

# ─── 유틸리티 ────────────────────────────────────────────────────────────────

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' not found. Install: $2"
}

# jq 체크
require_cmd jq "sudo apt-get install -y jq"
require_cmd python "python3 가상환경을 활성화하세요"

# Ollama 연결 확인
check_ollama() {
  curl -sf "${OLLAMA_BASE_URL}/api/tags" >/dev/null 2>&1 \
    || die "Ollama에 연결할 수 없습니다: ${OLLAMA_BASE_URL}\n  ollama serve 실행 후 재시도하세요."
}

# Python 실행 래퍼 (local 또는 Docker)
run_python() {
  local envs=("${@}")
  # 마지막 인자가 실행 명령
  if [[ "$USE_DOCKER" == "true" ]]; then
    local docker_envs=()
    local cmd_start=0
    for i in "${!envs[@]}"; do
      if [[ "${envs[$i]}" == "--cmd" ]]; then
        cmd_start=$((i+1))
        break
      fi
      docker_envs+=("-e" "${envs[$i]}")
    done
    docker run --rm -i \
      --network host \
      "${docker_envs[@]}" \
      -e HF_HOME=/root/.cache/huggingface \
      -v "${PROJECT_ROOT}/data:/app/data" \
      -v "${PROJECT_ROOT}/outputs:/app/outputs" \
      -v "${HOME}/.cache/huggingface:/root/.cache/huggingface" \
      rag-exp "${envs[@]:${cmd_start}}"
  else
    # local 실행: 환경변수를 export 후 python 실행
    local env_prefix=""
    local remaining=()
    local in_cmd=false
    for arg in "${envs[@]}"; do
      if [[ "$in_cmd" == "true" ]]; then
        remaining+=("$arg")
      elif [[ "$arg" == "--cmd" ]]; then
        in_cmd=true
      else
        env_prefix="${env_prefix} ${arg}"
      fi
    done
    (cd "${PROJECT_ROOT}" && env ${env_prefix} "${remaining[@]}")
  fi
}

# ─── STEP 1: inject ───────────────────────────────────────────────────────────

do_inject() {
  log "STEP 1: Attack 파일 주입 (strategy=${INJECT_STRATEGY}, rate=${CONTAMINATION_RATE}, seed=${RANDOM_SEED})"
  local attack_types=()
  read -r -a attack_types <<< "${ATTACK_TYPES}"
  (cd "${PROJECT_ROOT}" && python -m experiments.attack.inject \
    --rate "${CONTAMINATION_RATE}" \
    --strategy "${INJECT_STRATEGY}" \
    --stage-dir "${STAGE_DIR}" \
    --seed "${RANDOM_SEED}" \
    --types "${attack_types[@]}")
  log "Staging dir: ${STAGE_DIR}"
}

# ─── STEP 2: ingest ──────────────────────────────────────────────────────────

do_ingest() {
  local mode="$1"          # a or b
  local index_dir="$2"
  local detector_enabled="$3"   # true or false

  log "STEP 2 [${mode}]: 인덱싱 (DETECTOR_ENABLED=${detector_enabled}, INDEX_DIR=${index_dir})"

  rm -rf "${index_dir}"
  mkdir -p "${index_dir}"

  run_python \
    "RAW_DOCS_DIR=${STAGE_DIR}" \
    "INDEX_DIR=${index_dir}" \
    "DETECTOR_ENABLED=${detector_enabled}" \
    "DETECTOR_DEBUG=false" \
    "ENABLE_DENSE=false" \
    "ENABLE_RERANK=false" \
    "DOMAIN=all" \
    --cmd python -m src.ingest_app

  log "인덱싱 완료 → ${index_dir}"
}

# ─── STEP 3: query loop ──────────────────────────────────────────────────────

run_queries() {
  local mode="$1"          # mode_a or mode_b
  local index_dir="$2"
  local runtime_detector="$3"   # true or false
  local runtime_sanitizer="$4"  # true or false
  local out_dir="$5"

  mkdir -p "${out_dir}"
  log "STEP 3 [${mode}]: 쿼리 실행 (RUNTIME_DETECTOR=${runtime_detector}, RUNTIME_SANITIZER=${runtime_sanitizer})"

  local common_envs=(
    "RAW_DOCS_DIR=${STAGE_DIR}"
    "INDEX_DIR=${index_dir}"
    "RUNTIME_DETECTOR_ENABLED=${runtime_detector}"
    "RUNTIME_SANITIZER_ENABLED=${runtime_sanitizer}"
    "ENABLE_DENSE=false"
    "ENABLE_RERANK=false"
    "SPARSE_TOP_K=30"
    "RERANK_TOP_K=30"
    "FINAL_TOP_K=5"
    "OLLAMA_BASE_URL=${OLLAMA_BASE_URL}"
    "OLLAMA_MODEL=${OLLAMA_MODEL}"
  )

  # 정상 쿼리: false positive 측정용. 실제 RAG 경로를 그대로 사용한다.
  local n_benign
  n_benign=$(jq '.benign | length' "${QUERIES_JSON}")
  for i in $(seq 1 "${n_benign}"); do
    local query idx_pad outfile
    query=$(jq -r ".benign[$((i-1))].text" "${QUERIES_JSON}")
    idx_pad=$(printf "%02d" "$i")
    outfile="${out_dir}/${mode}_benign_${idx_pad}.txt"

    log "  [${mode}] benign Q${idx_pad}: ${query:0:50}..."
    echo "${query}" | run_python "${common_envs[@]}" "MUTEDRAG_ATTACK_EVAL=false" --cmd python -m src.query_app \
      > "${outfile}" 2>&1 || true
  done

  # 공격 대상 쿼리
  local n_attack
  n_attack=$(jq '.attack | length' "${QUERIES_JSON}")
  for i in $(seq 1 "${n_attack}"); do
    local query idx_pad outfile
    query=$(jq -r ".attack[$((i-1))].text" "${QUERIES_JSON}")
    idx_pad=$(printf "%02d" "$i")
    outfile="${out_dir}/${mode}_attack_${idx_pad}.txt"

    log "  [${mode}] attack Q${idx_pad}: ${query:0:50}..."
    echo "${query}" | run_python "${common_envs[@]}" "MUTEDRAG_ATTACK_EVAL=true" --cmd python -m src.query_app \
      > "${outfile}" 2>&1 || true
  done

  log "쿼리 결과 저장: ${out_dir} ($(ls "${out_dir}"/*.txt 2>/dev/null | wc -l) 파일)"
}

# ─── STEP 4: measure ASR ─────────────────────────────────────────────────────

do_measure() {
  local results_dir="$1"
  local index_dir_b="$2"

  log "STEP 4: ASR 계산"
  python -m experiments.eval.measure_asr \
    --results-dir "${results_dir}" \
    --queries "${QUERIES_JSON}" \
    ${index_dir_b:+--index-dir "${index_dir_b}"}
}

# ─── 메인 ────────────────────────────────────────────────────────────────────

main() {
  log "=== MutedRAG 실험 시작 ==="
  log "RUN_ID: ${RUN_ID}"
  log "PROJECT_ROOT: ${PROJECT_ROOT}"
  log "USE_DOCKER: ${USE_DOCKER}"
  log "RUN_MODE_A: ${RUN_MODE_A}, RUN_MODE_B: ${RUN_MODE_B}"

  check_ollama

  mkdir -p "${RESULTS_BASE}"

  # STEP 1: 공통 staging (mode A·B 동일한 staging dir 사용)
  do_inject

  # ── MODE A ──────────────────────────────────────────────────────────────────
  if [[ "${RUN_MODE_A}" == "true" ]]; then
    log ""
    log "=== MODE A: 무방어 baseline (detector 전면 비활성) ==="
    do_ingest "a" "${INDEX_DIR_A}" "false"

    RESULTS_DIR_A="${RESULTS_BASE}"
    run_queries "mode_a" "${INDEX_DIR_A}" "false" "false" "${RESULTS_DIR_A}"
  fi

  # ── MODE B ──────────────────────────────────────────────────────────────────
  if [[ "${RUN_MODE_B}" == "true" ]]; then
    log ""
    log "=== MODE B: 방어 (ingest detector + runtime detector) ==="
    do_ingest "b" "${INDEX_DIR_B}" "true"

    RESULTS_DIR_B="${RESULTS_BASE}"
    run_queries "mode_b" "${INDEX_DIR_B}" "true" "false" "${RESULTS_DIR_B}"
  fi

  # STEP 4: ASR 측정 (수집된 결과 전체 대상)
  log ""
  INDEX_DIR_FOR_QC=""
  [[ "${RUN_MODE_B}" == "true" ]] && INDEX_DIR_FOR_QC="${INDEX_DIR_B}"
  do_measure "${RESULTS_BASE}" "${INDEX_DIR_FOR_QC}"

  log ""
  log "=== 실험 완료 ==="
  log "결과 디렉토리: ${RESULTS_BASE}"
  log "  asr_detail.csv      - 쿼리별 DoS 판정 상세"
  log "  asr_summary.csv     - 모드별 ASR 요약"
  [[ "${RUN_MODE_B}" == "true" ]] && log "  quarantine_summary.json - 인덱싱 단계 quarantine 수 (mode B)"
  return 0
}

main "$@"
