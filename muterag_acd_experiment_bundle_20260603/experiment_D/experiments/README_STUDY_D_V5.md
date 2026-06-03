# Experiment D v5

Experiment D applies the sanitizer after semantic detection. It is designed to test recovery from detected MutedRAG cases, not to create a new detector.

## Smoke

Use C smoke detections first, then pass the manifest:

```bash
D_RUN_MODE=smoke \
D_ENABLE_RERANK=false \
D_DETECTED_CASES=outputs/experiments_v5/C_smoke_rerank_off/<run_id>/detected_cases.csv \
SEMANTIC_DETECTOR_MODE=improved \
SEMANTIC_DETECTOR_BACKEND=auto \
./experiments/run_study_d_v5.sh
```

Output: `outputs/experiments_v5/D_smoke_rerank_off`

## Full

Use C full detections:

```bash
D_RUN_MODE=full \
D_ENABLE_RERANK=false \
D_DETECTED_CASES=outputs/experiments_v5/C_rerank_off/<run_id>/detected_cases.csv \
SEMANTIC_DETECTOR_MODE=improved \
SEMANTIC_DETECTOR_BACKEND=auto \
./experiments/run_study_d_v5.sh
```

Output: `outputs/experiments_v5/D_rerank_off`

Default retrieval settings in the wrapper are dense ON, rerank ON, `SPARSE_TOP_K=60`, `DENSE_TOP_K=40`, `RERANK_TOP_K=100`, `FINAL_TOP_K=8`, and `RAG_CONTEXT_MAX_CHUNKS=10`.
Use `D_ENABLE_RERANK=false` when consuming C rerank-off detections. Output roots are split automatically:

- smoke rerank OFF: `outputs/experiments_v5/D_smoke_rerank_off`
- smoke rerank ON: `outputs/experiments_v5/D_smoke_rerank_on`
- full rerank OFF: `outputs/experiments_v5/D_rerank_off`
- full rerank ON: `outputs/experiments_v5/D_rerank_on`

D should use the same retrieval profile as the C manifest that produced `detected_cases.csv`.

## Metrics

The summary separates detected cases by baseline outcome. Use `recovery_rate_on_failed_detected` as the main sanitizer recovery metric because C may detect risky muted chunks even when the baseline answer was already normal. Use `preserved_rate` and `degraded_rate` to check whether sanitizer preserved those baseline-normal detected cases.

End-to-end fields such as `baseline_failed_total_rate`, `sanitized_failed_total_rate`, and `end_to_end_dos_delta_rate` describe the full muted condition before and after sanitization.

The summary also includes remaining refusal/block, insufficient evidence, irrelevant or incomplete answers, unsafe attack success, detected risky chunks, sanitized chunks, final context chunks, and refill usage.

The default D conditions are `muted_blackbox_on,muted_whitebox_on`. Direct conditions can be supplied manually with `D_CONDITIONS`, but direct is not the primary D recovery target.
