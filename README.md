# Study A RAG Guardrail Experiments

이 디렉토리는 Study A 실험을 재현하기 위한 실행 스크립트, 평가 코드, 공격 문서 코퍼스, 최종 실험 결과를 포함한다.

Study A의 목적은 기업 내부 문서 기반 RAG에서 corpus poisoning 형태의 direct injection 문서와 MutedRAG-style 문서가 정상 업무 질문의 최종 답변을 얼마나 자주 거부 또는 차단으로 유도하는지 측정하는 것이다.

이번 브랜치의 핵심 비교는 다음 두 조건이다.

- Guardrail OFF: 외부 guardrail 없이 RAG + LLM만 실행한다.
- Guardrail ON: selected context를 외부 guardrail, 예를 들어 Lakera Guard, 에 검사시킨 뒤 차단 또는 통과 여부를 기록한다.

우리 자체 detector/sanitizer는 Study A에서 사용하지 않는다. 실험 중에는 항상 아래 설정을 유지한다.

```bash
RUNTIME_DETECTOR_ENABLED=false
RUNTIME_SANITIZER_ENABLED=false
MUTEDRAG_ATTACK_EVAL=true
```

## 포함된 주요 파일

### 실험 문서 코퍼스

```text
data/exp_corpus/benign/
```

정상 문서 코퍼스다. `A_normal_only` 조건은 이 문서만 사용한다.

```text
data/exp_corpus/attack/01_직접인젝션/
```

명시적인 direct injection 문서 집합이다. `A_normal_direct` 조건에서 정상 문서와 함께 사용한다.

```text
data/exp_corpus/attack/05_순수서술형/
```

MutedRAG-style implicit refusal 문서 집합이다. 정상 업무 문서처럼 보이는 검색 prefix와 답변 거부를 유도하는 policy suffix를 결합한 corpus poisoning 변형이다. `A_normal_muted` 조건에서 사용한다.

정확히는 원 논문의 black-box `P = Q` 형식을 그대로 복제한 것은 아니며, 기업 업무 문서형 prefix와 implicit refusal suffix를 결합한 MutedRAG-style 변형이다.

### 실행 스크립트

```text
experiments/run_study_a.sh
```

Study A 기본 실행 스크립트다. guardrail provider, muted corpus type, query set, 최대 질문 수 등을 환경변수로 제어한다.

```text
experiments/run_study_a_full.sh
```

최종 재현용 실행 스크립트다. 기본 muted corpus는 `05_순수서술형`이며, guardrail OFF와 Lakera guardrail ON 조건을 비교 실행하는 용도로 사용한다.

```text
experiments/run_quick_test.sh
```

smoke test용 실행 스크립트다. 전체 실험 전에 일부 질문만 실행해 staging, ingest, query, metric 출력이 정상 동작하는지 확인한다.

### 평가 및 guardrail 코드

```text
experiments/attack/payloads.py
```

Study A에서 사용할 attack corpus type을 등록한다. `05_순수서술형` payload type이 여기에 추가되어 있다.

```text
src/external_guardrail.py
```

외부 guardrail 연동 레이어다. `off`, `mock`, `generic_http`, `lakera` provider를 지원한다. API key는 코드에 저장하지 않고 환경변수 또는 `.env`로 받는다.

```text
src/query_app.py
```

기존 RAG query 실행 흐름에 external guardrail hook을 연결한다. selected context가 만들어진 뒤 context guardrail 검사를 수행하고, block이면 LLM 호출을 생략한다.

```text
experiments/eval/measure_asr.py
```

Study A 결과를 평가한다. ASR, retrieval hit, context hit, I-ASR, guardrail flag/block rate, LLM 도달율을 계산한다.

### 최종 결과

```text
experiments/results/study_a_20260503_171732_guardrail_off/
experiments/results/study_a_20260503_171732_guardrail_lakera/
```

최종 분석에 사용한 Study A 결과다. 각각 guardrail OFF 조건과 Lakera guardrail ON 조건을 담고 있다.

## 사전 준비

프로젝트 루트에서 실행한다.

```bash
cd /path/to/rag-exp
```

Python 의존성을 설치한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

쉘 스크립트는 `jq`를 사용한다.

```bash
sudo apt-get update
sudo apt-get install -y jq
```

Ollama가 실행 중이어야 한다. 기본 모델은 `gemma3:12b`다.

```bash
ollama pull gemma3:12b
curl http://localhost:11434/api/tags
```

모델 파일은 Git에 포함되지 않는다. 실행 환경에 pull된 Ollama 모델을 사용한다. 다른 모델을 쓰려면 `OLLAMA_MODEL`로 바꾼다.

```bash
OLLAMA_MODEL="qwen2.5:7b" ./experiments/run_study_a.sh
```

Lakera Guard를 사용할 경우 API key를 환경변수나 `.env`에 둔다. `.env`는 Git에 올리지 않는다.

```bash
export EXTERNAL_GUARDRAIL_API_KEY="<lakera-api-key>"
```

## 빠른 동작 확인

전체 실험 전에 smoke test를 실행한다.

```bash
OLLAMA_MODEL="gemma3:12b" \
A_QUERY_SET=attack \
A_MAX_QUESTIONS=3 \
./experiments/run_quick_test.sh
```

이 명령은 일부 질문만 사용해 다음 흐름을 확인한다.

1. 정상 문서와 공격 문서를 staging한다.
2. RAG index를 생성한다.
3. 동일 질문을 각 조건에 실행한다.
4. 외부 guardrail 필드가 결과에 기록되는지 확인한다.
5. `asr_detail.csv`, `asr_summary.csv`, `guardrail_summary.csv`를 생성한다.

## Guardrail OFF 실험

외부 guardrail 없이 기본 RAG + LLM 조건을 실행한다.

```bash
EXTERNAL_GUARDRAIL_ENABLED=false \
OLLAMA_MODEL="gemma3:12b" \
A_MUTED_TYPES="05_순수서술형" \
./experiments/run_study_a.sh
```

실행 중 구성되는 조건은 다음과 같다.

- `A_normal_only`: benign corpus만 사용
- `A_normal_direct`: benign corpus + `01_직접인젝션`
- `A_normal_muted`: benign corpus + `05_순수서술형`

결과는 아래 형식의 디렉토리에 저장된다.

```text
experiments/results/study_a_YYYYMMDD_HHMMSS_guardrail_off/
```

## Lakera Guardrail ON 실험

selected context를 Lakera Guard에 검사시키고, block이면 LLM 호출을 하지 않는다.

```bash
EXTERNAL_GUARDRAIL_ENABLED=true \
EXTERNAL_GUARDRAIL_PROVIDER=lakera \
EXTERNAL_GUARDRAIL_STAGES=context \
EXTERNAL_GUARDRAIL_ACTION=block \
OLLAMA_MODEL="gemma3:12b" \
A_MUTED_TYPES="05_순수서술형" \
./experiments/run_study_a.sh
```

이 조건에서 guardrail이 차단하면 다음 필드가 결과에 기록된다.

```text
context_guardrail_blocked=true
llm_called=false
final_answer_source=external_guardrail
```

결과는 아래 형식의 디렉토리에 저장된다.

```text
experiments/results/study_a_YYYYMMDD_HHMMSS_guardrail_lakera/
```

## 최종 재현 실행

최종 비교 실험을 한 번에 재현하려면 다음 명령을 사용한다.

```bash
OLLAMA_MODEL="gemma3:12b" \
./experiments/run_study_a_full.sh
```

이 스크립트는 다음 순서로 진행된다.

1. `data/exp_corpus/benign`에서 정상 문서를 staging한다.
2. `01_직접인젝션`과 `05_순수서술형` 공격 문서를 조건별로 합성한다.
3. `A_normal_only`, `A_normal_direct`, `A_normal_muted` corpus를 만든다.
4. detector/sanitizer를 OFF로 둔 상태에서 index를 생성한다.
5. 동일한 attack query set을 각 조건에 실행한다.
6. guardrail OFF와 Lakera guardrail ON 결과를 분리 저장한다.
7. ASR 및 guardrail summary를 생성한다.

## Docker 실행 예시

서버에서 Docker 이미지 `rag-exp`를 사용하는 경우:

```bash
docker run --rm -it --network host \
  -w /app \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  -e OLLAMA_MODEL=gemma3:12b \
  -e RUNTIME_DETECTOR_ENABLED=false \
  -e RUNTIME_SANITIZER_ENABLED=false \
  -e MUTEDRAG_ATTACK_EVAL=true \
  -e EXTERNAL_GUARDRAIL_ENABLED=true \
  -e EXTERNAL_GUARDRAIL_PROVIDER=lakera \
  -e EXTERNAL_GUARDRAIL_STAGES=context \
  -e EXTERNAL_GUARDRAIL_ACTION=block \
  -e EXTERNAL_GUARDRAIL_API_KEY="$EXTERNAL_GUARDRAIL_API_KEY" \
  -v "$(pwd):/app" \
  rag-exp ./experiments/run_study_a.sh
```

Guardrail OFF로 실행하려면 `EXTERNAL_GUARDRAIL_ENABLED=false`로 바꾼다.

## 결과 파일

각 condition 디렉토리에는 다음 파일이 생성된다.

```text
asr_detail.csv
asr_detail.json
asr_summary.csv
guardrail_summary.csv
guardrail_summary.json
mode_a_attack_*.txt
```

주요 파일의 의미:

- `asr_detail.csv`: 질문별 상세 결과. retrieval hit, context hit, refusal 여부, guardrail flag/block 여부를 포함한다.
- `asr_summary.csv`: ASR, retrieval IR, context IR, I-ASR 요약이다.
- `guardrail_summary.csv`: external guardrail 차단율, LLM 도달율, 최종 거부율 요약이다.
- `mode_a_attack_*.txt`: 각 질문 실행의 원본 출력 로그다.

## 주요 지표

- `ASR`: 사용자가 정상 업무 질문에 대해 정상 답변을 받지 못한 비율이다.
- `Retrieval IR`: 공격 문서가 retrieval 후보에 포함된 비율이다.
- `Context IR`: 공격 문서가 실제 selected context에 포함된 비율이다.
- `I-ASR`: 공격 문서가 selected context에 포함된 경우 중 최종 공격 효과가 발생한 비율이다.
- `context_guardrail_block_rate`: 외부 guardrail이 selected context를 차단한 비율이다.
- `llm_called_rate`: guardrail 통과 후 LLM까지 도달한 비율이다.
- `final_refusal_rate`: 최종 답변이 거부 또는 차단으로 끝난 비율이다.

해석할 때는 `guardrail block`과 `LLM refusal`을 분리해서 본다. direct injection이 guardrail에 의해 많이 차단되는 것은 방어 관점에서는 탐지 성공이지만, 사용자가 정상 업무 답변을 받지 못한다는 점에서는 가용성 손실이기도 하다.

## 최종 실험 결과 요약

최종 분석에 사용한 결과는 다음 두 디렉토리다.

```text
experiments/results/study_a_20260503_171732_guardrail_off/
experiments/results/study_a_20260503_171732_guardrail_lakera/
```

핵심 요약:

| 조건 | ASR OFF | ASR ON | Lakera 차단율 | LLM 도달율 | I-ASR OFF to ON |
| --- | ---: | ---: | ---: | ---: | ---: |
| Benign only | 0.0% | 28.6% | 28.6% | 71.4% | n/a |
| Direct injection | 54.3% | 74.3% | 74.3% | 25.7% | 76.0% to 96.0% |
| Muted 05 | 17.1% | 37.1% | 22.9% | 77.1% | 26.1% to 43.5% |

이 결과는 Lakera Guard가 direct injection은 정상 baseline보다 훨씬 더 많이 차단하지만, `05_순수서술형` MutedRAG-style 문서는 benign baseline과 비슷하거나 더 낮은 수준으로 차단했음을 보여준다. 따라서 본 실험 조건에서는 상용 context guardrail이 노골적인 direct injection과 문서형 implicit refusal poisoning을 다르게 처리한다는 점을 확인할 수 있다.

단, 이 결과는 특정 corpus, 질문 세트, chunking, retrieval 설정, 모델, guardrail provider에 대한 결과다. 일반화하려면 다른 모델, 다른 guardrail, 다른 muted corpus 변형으로 반복 실험이 필요하다.

## Git에 포함할 파일

실험 재현을 위해 Git에 포함하는 핵심 파일:

```text
data/exp_corpus/attack/05_순수서술형/
experiments/README.md
experiments/attack/payloads.py
experiments/eval/measure_asr.py
experiments/run_quick_test.sh
experiments/run_study_a.sh
experiments/run_study_a_full.sh
src/external_guardrail.py
src/query_app.py
experiments/results/study_a_20260503_171732_guardrail_off/
experiments/results/study_a_20260503_171732_guardrail_lakera/
```

Git에 포함하지 않는 파일:

```text
.env
.claude/
__pycache__/
outputs/
data/experiments/
data/exp_stage*/
outputs/exp_*indexes*/
*.tgz
```

## 커밋 및 푸시

최종 결과 디렉토리는 `.gitignore`에 의해 기본적으로 무시될 수 있으므로, 결과까지 올릴 경우 `-f`로 추가한다.

```powershell
git add experiments/README.md
git add -f experiments/results/study_a_20260503_171732_guardrail_off
git add -f experiments/results/study_a_20260503_171732_guardrail_lakera
```

staged 파일을 확인한다.

```powershell
git diff --cached --name-status
git diff --cached --name-only | Select-String -Pattern "outputs|\.env|__pycache__|data/experiments|scripts|\.claude"
```

두 번째 명령에서 아무것도 나오지 않으면 의도하지 않은 로컬 산출물이 섞이지 않은 상태로 볼 수 있다.

커밋한다.

```powershell
git commit -m "Add Study A external guardrail experiment pipeline"
```

브랜치를 push한다.

```powershell
git push -u origin studyA-experiments
```
