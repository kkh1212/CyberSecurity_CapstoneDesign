*프롬프트 수정 후 바뀐 결과 보기 위해 로그 출력이 많이 나옴.*


# Detector for RAG Pipeline

이 저장소는 mutedRAG 방어 실험을 위한 RAG 코드베이스다.  
현재 구조는 크게 두 단계로 나뉜다.

- 등록 단계 detector
  - 문서를 chunk로 나눈 뒤 `instructionality`, `refusal_inducing`, `outlier`를 계산한다.
  - 결과에 따라 `index / review / quarantine` 정책을 적용한다.
- 런타임 detector + sanitizer
  - 질문과 검색된 context를 함께 보고 `query_risk`, `interaction_risk`, `context_set_risk`, `rbac_risk`를 계산한다.
  - 결과에 따라 `allow / requery / remove / block` 흐름으로 이어진다.

## 주요 디렉토리

```text
.
├─ data/
│  ├─ docs/                  # 기본 문서 디렉토리
│  └─ exp_corpus/            # 최근 실험용 corpus
├─ detector/
│  ├─ detector.py
│  ├─ patterns.py
│  ├─ risk.py
│  ├─ runtime.py
│  └─ scoring.py
├─ outputs/
│  └─ indexes/               # 기본 인덱스 디렉토리
└─ src/
   ├─ config.py
   ├─ detector_pipeline.py
   ├─ index_builder.py
   ├─ query_app.py
   ├─ retrievers.py
   └─ runtime_guard.py
```

## `exp_corpus` 기준 실험

최근 실험은 기본 `data/docs`가 아니라 `data/exp_corpus`를 기준으로 실행한다.

- 문서 경로: `/app/data/exp_corpus`
- 인덱스 경로: `/app/outputs/indexes_exp_corpus`

즉 Docker 실행 시 아래 두 환경변수를 함께 준다.

```bash
-e RAW_DOCS_DIR=/app/data/exp_corpus
-e INDEX_DIR=/app/outputs/indexes_exp_corpus
```

## Docker 빌드

```bash
docker build --no-cache -t rag-exp .
```

## `exp_corpus` ingest

처음 실행하거나 `data/exp_corpus` 문서가 바뀌었다면 먼저 ingest를 다시 수행해야 한다.

```bash
docker run --rm -it \
  --network host \
  -e RAW_DOCS_DIR=/app/data/exp_corpus \
  -e INDEX_DIR=/app/outputs/indexes_exp_corpus \
  -e DOMAIN=auto \
  -e DETECTOR_ENABLED=true \
  -e DETECTOR_PROFILE=balanced \
  -e ENABLE_DENSE=false \
  -e ENABLE_RERANK=false \
  -e HF_HOME=/root/.cache/huggingface \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/outputs:/app/outputs \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  rag-exp python -m src.ingest_app
```

## 런타임 실행 모드

현재 런타임은 3가지 모드로 실험할 수 있다.

### A. 기본 RAG

- detector OFF
- sanitizer OFF
- 보안 필터 없이 기본 retrieval + LLM 응답 흐름만 확인하는 모드
- debug 예상값:
  - `security_mode=baseline_rag`
  - `runtime_risk_level=not_run`
  - `runtime_action=baseline_allow`

```bash
docker run --rm -it \
  --network host \
  -e RAW_DOCS_DIR=/app/data/exp_corpus \
  -e INDEX_DIR=/app/outputs/indexes_exp_corpus \
  -e DOMAIN=auto \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  -e OLLAMA_MODEL=qwen2.5:7b \
  -e RUNTIME_DETECTOR_ENABLED=false \
  -e RUNTIME_SANITIZER_ENABLED=false \
  -e ENABLE_DENSE=false \
  -e ENABLE_RERANK=false \
  -e HF_HOME=/root/.cache/huggingface \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/outputs:/app/outputs \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  rag-exp python -m src.query_app
```

### B. 탐지 로직만 적용한 RAG

- detector ON
- sanitizer OFF
- stored flagged/quarantined chunk는 retrieval과 expansion 전체에서 제외
- runtime detector는 실행해서 risk/debug를 출력
- `requery/remove/block` 같은 sanitizer action은 수행하지 않음
- debug 예상값:
  - `security_mode=detect_only`
  - `runtime_action=detect_only_allow`
  - `runtime_detector_action=allow/requery/remove`

```bash
docker run --rm -it \
  --network host \
  -e RAW_DOCS_DIR=/app/data/exp_corpus \
  -e INDEX_DIR=/app/outputs/indexes_exp_corpus \
  -e DOMAIN=auto \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  -e OLLAMA_MODEL=qwen2.5:7b \
  -e RUNTIME_DETECTOR_ENABLED=true \
  -e RUNTIME_SANITIZER_ENABLED=false \
  -e RUNTIME_DETECTOR_PROFILE=balanced \
  -e ENABLE_DENSE=false \
  -e ENABLE_RERANK=false \
  -e HF_HOME=/root/.cache/huggingface \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/outputs:/app/outputs \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  rag-exp python -m src.query_app
```

### C. 탐지 + 정제 로직을 적용한 RAG

- detector ON
- sanitizer ON
- stored flagged/quarantined chunk는 retrieval과 expansion 전체에서 제외
- 현재 정책대로 `low -> allow`, `medium -> requery`, `high/critical -> remove/block` 수행
- debug 예상값:
  - `security_mode=detect_and_sanitize`
  - `runtime_action=allow/requery/remove/block`

```bash
docker run --rm -it \
  --network host \
  -e RAW_DOCS_DIR=/app/data/exp_corpus \
  -e INDEX_DIR=/app/outputs/indexes_exp_corpus \
  -e DOMAIN=auto \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  -e OLLAMA_MODEL=qwen2.5:7b \
  -e RUNTIME_DETECTOR_ENABLED=true \
  -e RUNTIME_SANITIZER_ENABLED=true \
  -e RUNTIME_DETECTOR_PROFILE=balanced \
  -e ENABLE_DENSE=false \
  -e ENABLE_RERANK=false \
  -e HF_HOME=/root/.cache/huggingface \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/outputs:/app/outputs \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  rag-exp python -m src.query_app
```

## 모드별 해석

- A 모드
  - retrieval/답변 성능의 baseline 확인용
- B 모드
  - detector가 무엇을 위험하다고 판단하는지 관측하는 실험용
- C 모드
  - 실제 runtime 보안 파이프라인 동작을 확인하는 실험용

## 자주 보는 debug 항목

질문 실행 후 아래 항목을 보면 현재 모드와 보안 동작을 빠르게 해석할 수 있다.

- `security_mode`
- `runtime_detector_enabled`
- `runtime_sanitizer_enabled`
- `excluded_flagged`
- `excluded_quarantined`
- `excluded_runtime`
- `expansion_skipped_flagged`
- `expansion_skipped_quarantined`
- `expansion_skipped_runtime`
- `final_result_count`
- `context_chunk_count`
- `runtime_risk_level`
- `runtime_action`
- `runtime_detector_action`
- `runtime_sanitization_rules`

## 주의할 점

- `RUNTIME_DETECTOR_ENABLED=false`인데 `RUNTIME_SANITIZER_ENABLED=true`로 주면 모순된 설정이다.
  - 현재 구현은 이 경우 sanitizer를 비활성화하고 baseline처럼 처리한다.
- `data/exp_corpus` 문서가 바뀌면 `indexes_exp_corpus`도 다시 생성해야 한다.
- `outputs/indexes_exp_corpus`는 Git에 포함하지 않으므로 새 환경에서는 ingest를 한 번 다시 돌려야 한다.
