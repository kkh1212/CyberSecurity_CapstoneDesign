# Detector for RAG Pipeline

이 프로젝트는 문서 코퍼스를 대상으로 하는 **등록 단계 detector**와, 질의응답 시점의 **런타임 detector**를 함께 포함한 RAG 실험 코드베이스다.  
목표는 mutedRAG류 공격 문서 또는 질문-문맥 조합이 LLM을 거부/회피 쪽으로 유도하는 상황을 탐지하고, 인덱싱 또는 응답 단계에서 이를 제어하는 것이다.

## 핵심 구성

- **등록 단계 detector**
  - 문서를 chunk로 나눈 뒤 각 chunk를 검사한다.
  - 결과에 따라 `index / review / quarantine`으로 분류한다.
  - 인덱싱 시점에 고위험 chunk를 제외하거나 표시한다.
- **런타임 detector**
  - 사용자 질문과 검색된 context chunk를 함께 본다.
  - 질문 자체의 위험도, 질문-문맥 결합 위험도, context set 위험도, RBAC 위험도를 계산한다.
  - 결과에 따라 `allow / sanitize / requery / block` 동작을 수행한다.

## 디렉토리 구조

```text
.
├─ data/
│  └─ docs/                       # 원문 문서 코퍼스
├─ detector/
│  ├─ patterns.py                # 등록 단계 패턴 정의
│  ├─ scoring.py                 # 등록 단계 점수 계산
│  ├─ risk.py                    # 등록 단계 최종 risk/calibration
│  ├─ detector.py                # 등록 단계 public API
│  └─ runtime.py                 # 런타임 detector
├─ outputs/
│  └─ indexes/                   # 도메인별 인덱스 및 detector 산출물
├─ src/
│  ├─ chunking.py                # 문서 로드 및 chunk 분할
│  ├─ index_builder.py           # 인덱스 생성 진입점
│  ├─ detector_pipeline.py       # 등록 단계 detector 통합
│  ├─ retrievers.py              # dense/sparse/hybrid retrieval
│  ├─ runtime_guard.py           # 런타임 detector wrapper
│  ├─ query_app.py               # 질의응답 CLI
│  └─ config.py                  # 공통 설정
├─ evaluate_detector_eval_docs.py # 등록 단계 평가 요약
└─ evaluate_runtime_detector.py   # 런타임 detector 평가
```

## 1. 등록 단계 보안 로직

등록 단계는 **문서를 인덱싱하기 전에** chunk 단위로 detector를 실행하는 구조다.

### 처리 흐름

```text
문서 로드
-> chunk 분할
-> detector 실행
-> low / medium / high / critical 판정
-> index / review / quarantine 결정
-> index 저장 + detector artifact 저장
```

### 어디서 동작하나

- 진입점: `python -m src.ingest_app`
- 실제 인덱싱: [src/index_builder.py](src/index_builder.py)
- detector 통합: [src/detector_pipeline.py](src/detector_pipeline.py)

### detector가 계산하는 값

각 chunk마다 다음 항목이 계산된다.

- `instructionality_score`
  - 사실 전달 문서인지, 모델 행동을 바꾸려는 지시성 문구인지
- `refusal_inducing_score`
  - 거부/제한/민감정보 회피를 유도하는 표현이 있는지
- `outlier_score`
  - 정상 코퍼스에 비해 구조나 문체가 비정상적인지
- `base_risk`
- `adjusted_risk`
- `risk_level`
- `triggered_rules`

### 기본 정책

기본 설정은 [src/config.py](src/config.py)에 있다.

- `low` -> `index`
- `medium` -> `review`
- `high` -> `quarantine`
- `critical` -> `quarantine`

### 등록 단계 결과 파일

도메인별 인덱스 디렉토리 아래에 다음 파일이 생성된다.

- `faiss.index`
- `chunks_meta.pkl`
- `bm25.pkl`
- `detector_summary.json`
- `flagged_chunks.jsonl`
- `quarantine_chunks.jsonl`
- `detector_corpus_stats.json`

### 의미

- `index`
  - 정상 인덱싱
- `review`
  - 인덱싱은 되지만 flagged 상태로 저장
- `quarantine`
  - 인덱싱 대상에서 제외

## 2. retrieval 단계 보안 로직

retrieval은 등록 단계에서 저장된 detector metadata를 다시 참고한다.

### 어디서 동작하나

- [src/retrievers.py](src/retrievers.py)
- [src/detector_pipeline.py](src/detector_pipeline.py)

### 기본 동작

- `high / critical` chunk는 retrieval 결과에서 제외
- `medium` chunk도 기본적으로 제외
- `low` chunk만 그대로 사용

옵션으로 medium을 포함할 수는 있지만, 기본값은 제외다.

## 3. 런타임 보안 로직

런타임 detector는 **질문 + 검색된 context 조합**을 보고 위험을 계산한다.  
즉 문서 자체가 아니라, **질문과 함께 들어갈 때 위험해지는 상황**을 잡는 것이 목적이다.

### 처리 흐름

```text
사용자 질문 입력
-> retrieval
-> context chunk 선택
-> runtime detector 실행
-> allow / sanitize / requery / block 결정
-> 안전한 context만 LLM에 전달
```

### 어디서 동작하나

- 런타임 detector 본체: [detector/runtime.py](detector/runtime.py)
- wrapper: [src/runtime_guard.py](src/runtime_guard.py)
- query 흐름 통합: [src/query_app.py](src/query_app.py)

### 런타임 detector가 계산하는 값

런타임 detector는 4개 점수를 계산한다.

- `query_risk`
  - 질문 자체가 jailbreak, prompt injection, 권한 우회, 상세 내부정보 요청인지
- `interaction_risk`
  - 질문과 특정 chunk가 결합될 때 위험해지는지
- `context_set_risk`
  - top-k 전체 문맥이 together prompt처럼 작동하는지
- `rbac_risk`
  - 사용자 역할/부서/직급 기준으로 권한 밖 문서를 노리는지

최종 risk는 이 4개를 가중합하고 calibration rule을 적용해 산출한다.

### 런타임 기본 동작

- `low`
  - 그대로 사용
- `medium`
  - 위험 chunk 제거 후 남은 context로 계속 진행
- `high`
  - 현재 context를 폐기하고 안전 fallback
- `critical`
  - 응답 차단

## 4. 실행 전 준비

### 로컬 Python 환경

Python 3.11 기준이다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Ollama

런타임 질의응답은 Ollama가 필요하다.

예시:

```bash
ollama pull qwen2.5:7b
curl http://localhost:11434/api/tags
```

## 5. 등록 단계 실행 방법

### 전체 코퍼스 인덱싱

```bash
export DETECTOR_ENABLED=true
export DETECTOR_DEBUG=true
export ENABLE_DENSE=false
export ENABLE_RERANK=false
python -m src.ingest_app
```

### 특정 도메인만 인덱싱

```bash
export DOMAIN="B. 인사총무 문서"
export DETECTOR_ENABLED=true
export DETECTOR_DEBUG=true
python -m src.ingest_app
```

### Docker로 실행

```bash
docker build --no-cache -t rag-exp .

docker run --rm -it \
  --network host \
  -e DOMAIN=auto \
  -e DETECTOR_ENABLED=true \
  -e DETECTOR_DEBUG=true \
  -e ENABLE_DENSE=false \
  -e ENABLE_RERANK=false \
  -e HF_HOME=/root/.cache/huggingface \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/outputs:/app/outputs \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  rag-exp python -m src.ingest_app
```

### 등록 단계에서 확인할 것

도메인별 인덱스 결과를 확인한다.

```bash
find outputs/indexes -name "detector_summary.json"
find outputs/indexes -name "flagged_chunks.jsonl"
find outputs/indexes -name "quarantine_chunks.jsonl"
```

## 6. 런타임 단계 실행 방법

### 질의응답 CLI 실행

```bash
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_MODEL=qwen2.5:7b
export RUNTIME_DETECTOR_ENABLED=true
export ENABLE_DENSE=false
export ENABLE_RERANK=false
python -m src.query_app
```

### Docker로 실행

```bash
docker run --rm -it \
  --network host \
  -e DOMAIN=auto \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  -e OLLAMA_MODEL=qwen2.5:7b \
  -e RUNTIME_DETECTOR_ENABLED=true \
  -e ENABLE_DENSE=false \
  -e ENABLE_RERANK=false \
  -e HF_HOME=/root/.cache/huggingface \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/outputs:/app/outputs \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  rag-exp python -m src.query_app
```

### RBAC 포함 테스트

사용자 역할/부서/직급을 같이 넣을 수 있다.

```bash
export RUNTIME_USER_ROLE=staff
export RUNTIME_USER_DEPT=support
export RUNTIME_USER_RANK=staff
python -m src.query_app
```

### 런타임 디버그 출력에서 볼 항목

질문 실행 후 아래 값이 보이면 runtime guard가 실제로 동작한 것이다.

- `runtime_risk_level`
- `runtime_action`
- `runtime_adjusted_risk`
- `runtime_rules`
- `runtime_removed_chunks`

## 7. 평가 스크립트

### 등록 단계 평가

평가용 Z 문서를 기준으로 detector 결과를 요약한다.

```bash
python evaluate_detector_eval_docs.py
```

출력:

- `outputs/detector_eval_summary.md`
- `outputs/detector_eval_summary.json`

### 런타임 평가

사전 정의된 query/context 케이스를 기반으로 runtime detector를 평가한다.

```bash
python evaluate_runtime_detector.py
```

출력:

- `outputs/runtime_detector_eval.md`
- `outputs/runtime_detector_eval.json`

## 8. 설정값

주요 설정은 [src/config.py](src/config.py)와 runtime guard에서 읽는다.

### 등록 단계 / retrieval

- `DETECTOR_ENABLED`
- `DETECTOR_DEBUG`
- `DETECTOR_FAIL_MODE`
- `DETECTOR_ACTION_LOW`
- `DETECTOR_ACTION_MEDIUM`
- `DETECTOR_ACTION_HIGH`
- `DETECTOR_ACTION_CRITICAL`
- `INCLUDE_FLAGGED`
- `INCLUDE_QUARANTINED`
- `RETRIEVAL_FLAGGED_SCORE_MULTIPLIER`

### 런타임 단계

- `RUNTIME_DETECTOR_ENABLED`
- `RUNTIME_USER_ROLE`
- `RUNTIME_USER_DEPT`
- `RUNTIME_USER_RANK`
- `OLLAMA_BASE_URL`
- `OLLAMA_MODEL`

## 9. 운영상 주의사항

### 문서나 코드가 바뀌면 재인덱싱이 필요하다

다음 경우에는 `src.ingest_app`를 다시 실행해야 한다.

- `data/docs` 아래 원문 문서가 바뀐 경우
- chunking 로직이 바뀐 경우
- 등록 단계 detector 로직이 바뀐 경우
- embedding/index 저장 구조가 바뀐 경우

반대로, 문서와 인덱스가 그대로라면 매 질문마다 재인덱싱할 필요는 없다.

### 런타임 detector는 질의응답 시점마다 동작한다

`src.query_app`를 실행할 때마다:

- 질문
- 검색 결과
- 사용자 컨텍스트

를 기반으로 risk를 다시 계산한다.

즉 등록 단계 detector는 정적 검사, 런타임 detector는 동적 검사다.

## 10. 현재 구현 수준과 한계

현재 detector는 rule/heuristic 기반 MVP다.

장점:
- explainable output 유지
- 등록 단계와 런타임 단계가 분리되어 있음
- chunk 단위 및 query-context 단위 모두 방어 가능
- benign 문서에 대한 오탐을 비교적 낮게 유지하도록 설계됨

한계:
- semantic understanding보다 pattern/structure/heuristic 의존도가 높음
- 아주 자연스럽고 우회적인 공격은 놓칠 수 있음
- chunk 경계에 따라 mixed/spliced 공격 신호가 약해질 수 있음
- `high -> requery`는 현재 context 폐기와 fallback 중심으로 연결되어 있으며, 완전한 대체 retrieval loop는 아직 제한적임

## 11. 빠른 실행 순서

처음부터 한 번 확인하려면 아래 순서로 실행하면 된다.

1. 인덱싱

```bash
python -m src.ingest_app
```

2. 등록 단계 결과 확인

```bash
python evaluate_detector_eval_docs.py
```

3. 런타임 질의응답 실행

```bash
python -m src.query_app
```

4. 런타임 평가

```bash
python evaluate_runtime_detector.py
```

이 순서대로 돌리면
- 등록 단계 detector
- retrieval 단계 필터링
- 런타임 detector
- 평가 리포트
까지 한 번에 확인할 수 있다.
