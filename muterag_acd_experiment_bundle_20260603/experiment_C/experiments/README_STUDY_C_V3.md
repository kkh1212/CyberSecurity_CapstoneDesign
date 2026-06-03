# Study C v3 Detector Evaluation

Study C v3 evaluates the standalone semantic detector on the fixed v3 corpus.
It does not modify source documents and does not call Ollama or an external
guardrail. This is the C1 detector-only stage for measuring false positives and
detection rate before mitigation experiments.

## Data

- Normal documents: `data/experiments_v3/experiment_normal`
- Direct attack documents: `data/experiments_v3/experiment_direct`
- MutedRAG documents: `data/experiments_v3/experiment_muted`
- Questions: `data/experiments_v3/questions/v3_questions.csv`

## Run On Windows

```powershell
python -m experiments.run_study_c_v3
```

Normal + MutedRAG only:

```powershell
$env:C_CORPUS_TYPES="normal,muted"
python -m experiments.run_study_c_v3
Remove-Item Env:C_CORPUS_TYPES
```

## Run On Linux Server

```bash
chmod +x experiments/run_study_c_v3.sh
PYTHON_BIN=python3 ./experiments/run_study_c_v3.sh
```

Normal + MutedRAG only:

```bash
C_CORPUS_TYPES=normal,muted PYTHON_BIN=python3 ./experiments/run_study_c_v3.sh
```

## Environment Variables

- `C_EXPERIMENTS_ROOT`: default `data/experiments_v3`
- `C_QUESTIONS`: default `data/experiments_v3/questions/v3_questions.csv`
- `C_OUTPUT_ROOT`: default `outputs/experiments_v3/C`
- `C_EMBEDDING_BACKEND`: default `hashing`
- `C_CORPUS_TYPES`: default `normal,direct,muted`
- `C_RUN_ID`: optional fixed run id
- `C_RUN_LEGACY_BASELINE`: default `false`; set `true` to also evaluate the old `detector/` package

`hashing` is the default backend because it needs no model download and is
stable for the current v3 experiment setup. Use `sentence-transformers` only
when the runtime has the model available.

## Output

Each run creates:

```text
outputs/experiments_v3/C/{run_id}/
  run_config.json
  calibration/
    thresholds.json
    normal_feature_scores.csv
  detector_eval/
    chunk_scores.csv
    summary.csv
    thresholds_used.json
  reports/
    detector_eval_summary.md
    detector_eval_results.csv
    detector_eval_failures.md
    legacy_detector_baseline.csv
```

Strict detection treats only `direct_candidate` and `muted_candidate` as attack.
`suspect` is logged for review and should not be counted as a blocking decision
in C1.
