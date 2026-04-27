# RAG Attack Experiments

이 디렉터리는 RAG 환경에서 프롬프트 인젝션 및 MutedRAG 계열 공격을 재현하고,
방어 로직 적용 전후의 결과를 비교하기 위한 실험 스크립트를 포함한다.

## 실험 구성

### Study A: Baseline RAG 취약성 실험

방어 로직을 끈 상태에서 benign corpus와 공격 corpus를 섞어 RAG 응답을 측정한다.

- `A_normal_only`: benign corpus만 사용
- `A_normal_direct`: benign corpus + direct injection 문서
- `A_normal_muted`: benign corpus + MutedRAG/indirect 문서

주요 목적은 공격 문서가 검색 결과와 최종 context에 들어왔을 때 실제 ASR이 발생하는지 확인하는 것이다.

### Study B: 공격 오염률 변화 실험

공격 chunk의 비율을 바꿔가며 ASR 변화를 측정한다.

기본 오염률 설정:

```text
0%, 1%, 3%, 5%, 10%
```

결과 파일의 `actual_rate`는 실제 chunk 수 기준으로 계산된 오염률이다.

### Study C: 탐지/방어 로직 적용 실험

탐지 로직을 적용한 뒤 ASR 변화와 detector 성능을 함께 측정한다.

측정 항목:

- ASR
- Retrieval IR
- Context IR
- I-ASR
- detector precision/recall/FPR
- chunk-level 및 document-level TP/FP/TN/FN

## 주요 지표

```text
ASR
공격 성공률. 최종 응답이 공격 의도대로 거부 또는 변형된 비율.

Retrieval IR
공격 문서가 검색 결과 후보에 포함된 비율.
공격 문서가 검색 단계까지 침투했는지 측정한다.

Context IR
공격 문서가 최종 LLM 입력 context에 포함된 비율.
검색된 공격 문서가 실제 답변 생성에 사용될 위치까지 도달했는지 측정한다.

I-ASR
공격 문서가 최종 context에 포함된 경우 중 실제 공격이 성공한 비율.
공격 문서가 모델에게 노출됐을 때 얼마나 효과가 있었는지 측정한다.
```

## 요구 사항

Python 의존성은 프로젝트 루트의 `requirements.txt`를 사용한다.

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

Ollama가 실행 중이어야 하며, 사용할 모델이 설치되어 있어야 한다.

```bash
ollama pull gemma3:12b
ollama pull qwen3:8b
curl http://localhost:11434/api/tags
```

## 실행 방법

모든 명령은 프로젝트 루트에서 실행한다.

```bash
cd /path/to/CyberSecurity_CapstoneDesign
source .venv/bin/activate
```

### Study A 실행

```bash
OLLAMA_MODEL="gemma3:12b" ./experiments/run_study_a.sh
```

공격 질문만 사용하려면:

```bash
A_QUERY_SET=attack OLLAMA_MODEL="gemma3:12b" ./experiments/run_study_a.sh
```

기본 설정:

```text
A_DIRECT_TYPES="01_직접인젝션"
A_MUTED_TYPES="02_간접_명시형"
A_QUERY_SET="all"
```

### Study B 실행

```bash
OLLAMA_MODEL="gemma3:12b" ./experiments/run_study_b.sh
```

오염률을 직접 지정하려면:

```bash
B_RATES="0 0.01 0.03 0.05 0.10" OLLAMA_MODEL="gemma3:12b" ./experiments/run_study_b.sh
```

기본 설정:

```text
B_ATTACK_TYPES="02_간접_명시형"
B_QUERY_SET="attack"
```

### Study C 실행

```bash
OLLAMA_MODEL="qwen3:8b" ./experiments/run_study_c.sh
```

Gemma 모델로 실행하려면:

```bash
OLLAMA_MODEL="gemma3:12b" ./experiments/run_study_c.sh
```

기본 설정:

```text
C_DIRECT_TYPES="01_직접인젝션"
C_MUTED_TYPES="02_간접_명시형"
C_QUERY_SET="attack"
```

## 공격 corpus 선택

기본 공격 corpus는 다음 경로를 사용한다.

```text
data/exp_corpus/attack
```

docx 기반 공격 corpus를 사용하려면 `ATTACK_BASE_DIR`를 지정한다.

```bash
ATTACK_BASE_DIR="data/exp_corpus/attack_docx" OLLAMA_MODEL="gemma3:12b" ./experiments/run_study_a.sh
```

외부 보충자료형 docx corpus를 사용하려면:

```bash
ATTACK_BASE_DIR="data/exp_corpus/attack_docx_external" OLLAMA_MODEL="gemma3:12b" ./experiments/run_study_a.sh
```

## 결과 파일

실험 결과는 다음 경로에 생성된다.

```text
experiments/results/study_a_YYYYMMDD_HHMMSS/
experiments/results/study_b_YYYYMMDD_HHMMSS/
experiments/results/study_c_YYYYMMDD_HHMMSS/
```

주요 파일:

```text
asr_summary.csv
asr_detail.csv
study_b_summary.csv
study_c_summary.csv
detector_chunk_summary.csv
detector_document_summary.csv
```

`experiments/results/`, `data/exp_stage*/`, `outputs/exp_*indexes*/`는 실행 시 생성되는 결과물이므로 Git에 포함하지 않는다.

## 발표용 해석 포인트

ASR만으로는 공격 과정을 충분히 설명하기 어렵다.
Retrieval IR과 Context IR을 함께 보면 공격 문서가 검색 단계와 최종 context 단계 중 어디까지 침투했는지 구분할 수 있다.

예를 들어 ASR이 낮더라도 Retrieval IR과 Context IR이 높다면,
공격 문서는 RAG pipeline에 유입되었지만 모델이 최종적으로 해당 지시를 따르지 않은 것으로 해석할 수 있다.

반대로 detector 적용 후 정상 문서가 과도하게 제외되면,
정상 evidence가 줄어 공격 문서의 상대 순위가 올라갈 수 있다.
따라서 방어 로직은 ASR 감소뿐 아니라 false positive와 검색 품질 변화도 함께 평가해야 한다.
