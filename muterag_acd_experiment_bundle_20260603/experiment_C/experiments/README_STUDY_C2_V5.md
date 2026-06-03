# Experiment C v5

Experiment C evaluates the MutedRAG semantic detector on the v5 corpus. The default detector is the improved detector-v2 path and it only treats `muted_candidate` as the semantic detection target.

By default, v5 C runs `normal_on,muted_blackbox_on,muted_whitebox_on`. The normal condition is included to measure detector false positives in the same run. Direct conditions can still be supplied manually with `C2_CONDITIONS`, but direct attacks are not the main semantic-detector target.

## Smoke

Smoke uses the first 5 questions and matching documents. It is only for checking that indexing, retrieval, detector logging, and result writing work.

```bash
C2_RUN_MODE=smoke \
C2_ENABLE_RERANK=false \
SEMANTIC_DETECTOR_MODE=improved \
SEMANTIC_DETECTOR_BACKEND=auto \
./experiments/run_study_c2_v5.sh
```

Output: `outputs/experiments_v5/C_smoke_rerank_off`

## Full

Full runs the 50-question experiment.

```bash
C2_RUN_MODE=full \
C2_ENABLE_RERANK=false \
SEMANTIC_DETECTOR_MODE=improved \
SEMANTIC_DETECTOR_BACKEND=auto \
./experiments/run_study_c2_v5.sh
```

Output: `outputs/experiments_v5/C_rerank_off`

Default retrieval settings in the wrapper are dense ON, rerank ON, `SPARSE_TOP_K=60`, `DENSE_TOP_K=40`, `RERANK_TOP_K=100`, `FINAL_TOP_K=8`, and `RAG_CONTEXT_MAX_CHUNKS=10`.
Use `C2_ENABLE_RERANK=false` for the primary detector-evaluation profile. Use rerank ON only as a separate reranker-removal profile. Output roots are split automatically:

- smoke rerank OFF: `outputs/experiments_v5/C_smoke_rerank_off`
- smoke rerank ON: `outputs/experiments_v5/C_smoke_rerank_on`
- full rerank OFF: `outputs/experiments_v5/C_rerank_off`
- full rerank ON: `outputs/experiments_v5/C_rerank_on`

Run retrieval-only exposure validation before full C:

```bash
python scripts/v5/validate_attack_exposure_v5.py \
  --experiments-root data/experiments_v5 \
  --questions data/experiments_v5/questions/v5_questions.csv \
  --profiles rerank_on,rerank_off
```

If the active `python` is not the project environment, prefix with `PYTHON_BIN=.venv/bin/python` on Linux/WSL or activate the Windows venv first.
Detector recall should be interpreted only for cases where the payload reaches selected context.

## Results For D

The C runner writes `detected_cases.csv` and `detected_chunks.jsonl` from the `log_only` action. By default these contain detected muted cases from `muted_blackbox` and `muted_whitebox`; normal false positives should be read from `c2_summary.csv`.

D can consume the manifest with:

```bash
D_DETECTED_CASES=outputs/experiments_v5/C_rerank_off/<run_id>/detected_cases.csv
```

Direct attacks are not the semantic detector target here. They are measured as integrity attacks through injected fictional employee record detection.
`c2_summary.csv` includes normal guardrail block baseline and `adjusted_muted_dos_rate` so Lakera overblocking is not mistaken for muted attack success.
