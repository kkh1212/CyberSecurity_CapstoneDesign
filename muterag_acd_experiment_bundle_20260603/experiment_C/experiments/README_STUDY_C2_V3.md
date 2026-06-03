# Study C2 v3 RAG + Guardrail + Semantic Detector

Study C2 runs the full v3 RAG path with external guardrail and LLM enabled.
It compares semantic detector actions before the external context guardrail:

- `off`: no semantic detector
- `log_only`: detector records verdicts, but does not change context
- `drop_chunk`: selected context chunks with `direct_candidate` or `muted_candidate` are removed before guardrail evaluation

The goal is to test whether semantic mitigation reduces guardrail-induced DoS
and restores usable answers.

## Default Matrix

By default the runner executes:

```text
conditions: normal_on,direct_on,muted_on
semantic actions: off,log_only,drop_chunk
questions: all 30 v3 questions
corpus size: 30 files each for normal/direct/muted
```

This means every condition has external guardrail ON. The legacy detector and
sanitizer remain OFF:

```text
RUNTIME_DETECTOR_ENABLED=false
RUNTIME_SANITIZER_ENABLED=false
```

## Run On Linux Server

Start Ollama separately, then run:

```bash
cd /data/workspace/exA
chmod +x experiments/run_study_c2_v3.sh

EXTERNAL_GUARDRAIL_PROVIDER=lakera \
EXTERNAL_GUARDRAIL_API_KEY="$EXTERNAL_GUARDRAIL_API_KEY" \
EXTERNAL_GUARDRAIL_STAGES=context \
EXTERNAL_GUARDRAIL_ACTION=block \
C2_REBUILD_INDEX=true \
OLLAMA_BASE_URL=http://localhost:11434 \
OLLAMA_MODEL=gemma3:12b \
PYTHON_BIN=python3 \
./experiments/run_study_c2_v3.sh
```

Quick smoke test:

```bash
C2_QUICK=true \
C2_SEMANTIC_ACTIONS=off,drop_chunk \
EXTERNAL_GUARDRAIL_PROVIDER=lakera \
EXTERNAL_GUARDRAIL_API_KEY="$EXTERNAL_GUARDRAIL_API_KEY" \
OLLAMA_BASE_URL=http://localhost:11434 \
OLLAMA_MODEL=gemma3:12b \
PYTHON_BIN=python3 \
./experiments/run_study_c2_v3.sh
```

## Environment Variables

- `C2_CONDITIONS`: default `normal_on,direct_on,muted_on`
- `C2_SEMANTIC_ACTIONS`: default `off,log_only,drop_chunk`
- `C2_QUICK`: default `false`; when true, first 5 questions are used
- `C2_MAX_QUESTIONS`: optional numeric limit
- `C2_EXPECTED_QUESTIONS`: default `30`; validates that the v3 question file
  and each v3 corpus contain 30 items before the run starts. Set to an empty
  value only for ad hoc development runs.
- `C2_OUTPUT_ROOT`: default `outputs/experiments_v3/C2`
- `C2_EMBEDDING_BACKEND`: default `hashing`
- `C2_REBUILD_INDEX`: default `false`; set `true` for the first run after
  changing retrieval settings or moving the experiment bundle to another
  machine.
- `C2_ENABLE_DENSE`: default `true`; falls back to sparse retrieval if dense
  dependencies or FAISS index files are unavailable.
- `C2_ENABLE_RERANK`: default `true`; falls back to original retrieval scores
  if the cross-encoder dependency is unavailable.
- `OLLAMA_BASE_URL`
- `OLLAMA_MODEL`
- `EXTERNAL_GUARDRAIL_PROVIDER`
- `EXTERNAL_GUARDRAIL_API_KEY`
- `EXTERNAL_GUARDRAIL_STAGES`
- `EXTERNAL_GUARDRAIL_ACTION`

## Output

Each run creates:

```text
outputs/experiments_v3/C2/{run_id}/
  run_config.json
  calibration/
    thresholds.json
  semantic_off/{child_run}/
  log_only/{child_run}/
  drop_chunk/{child_run}/
  c2_summary.csv
  action_runs.json
  README_RESULTS.md
```

Check the combined summary:

```bash
R=$(ls -td outputs/experiments_v3/C2/* | head -1)
cat "$R/c2_summary.csv"
```

Check the recorded corpus/question counts:

```bash
R=$(ls -td outputs/experiments_v3/C2/* | head -1)
cat "$R/run_config.json"
```

For answer-level review:

```bash
cat "$R/drop_chunk"/*/answer_review.txt
cat "$R/semantic_off"/*/answer_review.txt
```

## Main Interpretation

Compare `semantic_off` or `log_only` against `drop_chunk`.

Key expected mitigation signals:

- `context_guardrail_block_rate` decreases for `muted_on`
- `llm_called_rate` increases for `muted_on`
- `normal_response_rate` increases for `muted_on`
- normal false blocking should not increase

`suspect` remains log-only. Only `direct_candidate` and `muted_candidate` are
removed in `drop_chunk` mode.
