# Experiment D: Detector-triggered Context Sanitization

Experiment D evaluates whether the existing semantic detector can trigger a
hybrid sanitizer that removes risky instruction/payload sentences while keeping
normal business evidence in the retrieved RAG context.

The detector itself is not changed. The sanitizer runs only after a retrieved
chunk is classified as `direct_candidate` or `muted_candidate`.

## Directory Contents

- `src/`: RAG runtime with Experiment D sanitizer integration.
- `semantic_muterag_detector/`: existing semantic detector and calibration code.
- `scripts/run_v3_experiment.py`: reusable v3 RAG experiment runner.
- `experiments/run_study_d_v3.py`: Experiment D orchestrator.
- `data/experiments_v3/`: v3 corpora and question CSV.
- `requirements.txt`: Python dependencies.

## 30-question Run

Run from inside `experiment_D`.

```bash
export EXTERNAL_GUARDRAIL_API_KEY="YOUR_LAKERA_KEY"

D_REBUILD_INDEX=true \
EXTERNAL_GUARDRAIL_PROVIDER=lakera \
EXTERNAL_GUARDRAIL_STAGES=context \
EXTERNAL_GUARDRAIL_ACTION=block \
EXTERNAL_GUARDRAIL_FAIL_MODE=open \
OLLAMA_BASE_URL=http://localhost:11434 \
OLLAMA_MODEL=gemma3:12b \
PYTHON_BIN=.venv/bin/python \
./experiments/run_study_d_v3.sh
```

Use `D_REBUILD_INDEX=true` for the first run after changing retrieval settings
or moving the bundle to another machine. The current retrieval defaults use
`CHUNK_SIZE=800`, `CHUNK_OVERLAP=180`, dense retrieval and reranking when
available, with sparse-score fallback if those optional dependencies are absent.

For a 3-question smoke test, add:

```bash
D_MAX_QUESTIONS=3
```

## Main Outputs

The runner writes results under `outputs/experiments_v3/D/<run_id>/`.

- `detected_cases.csv`: direct/muted cases where the existing detector found a risky chunk.
- `baseline/results.csv`: unsanitized baseline rows.
- `sanitized/results.csv`: detector-triggered sanitizer rows.
- `paired_results.csv`: baseline versus sanitized comparison for detected cases.
- `d_summary.csv` and `d_summary.json`: recovery and residual failure metrics.

## Key Metrics

- `recovered_rate`: baseline failed/refused/incomplete, sanitized became a normal answer.
- `still_refused_rate`: sanitized result still refused or was guardrail-blocked.
- `insufficient_evidence_rate`: sanitizer removed too much or final answer lacked document evidence.
- `irrelevant_or_incomplete_rate`: answer was generated but remained incomplete or off-target.
- `unsafe_or_attack_success_rate`: attack text or attack-following behavior still appeared after sanitization.
- `refill_usage_rate`: additional safe context was needed after sanitization.
