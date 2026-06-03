# Semantic MutedRAG Detector

This package is a standalone detector for English paper-form MutedRAG
documents. It does not modify the legacy `detector/` package.

The detector avoids keyword lists such as `ignore` or specific harmful terms.
Instead, it scores query-conditioned semantic structure:

1. `anchor_score`: whether the chunk contains a sentence close to the user query.
2. `alignment_gap`: whether some sentences are close to the query while others are far.
3. `semantic_break_score`: whether the chunk has an abrupt topic transition.
4. `payload_isolation`: whether query-aligned sentences and off-query sentences are far apart.
5. `prototype_similarity`: similarity to generic redirection/refusal intent prototypes.

The expected MutedRAG shape is:

```text
query-aligned anchor + off-query payload
```

## Calibrate on normal corpus

```bash
python -m semantic_muterag_detector.cli.calibrate_thresholds \
  --normal-dir data/experiments_v3/experiment_normal \
  --questions data/experiments_v3/questions/v3_questions.csv \
  --out outputs/semantic_muterag_detector/calibration
```

## Evaluate v3 normal/muted corpora

```bash
python -m semantic_muterag_detector.cli.eval_v3_corpus \
  --experiments-root data/experiments_v3 \
  --questions data/experiments_v3/questions/v3_questions.csv \
  --thresholds outputs/semantic_muterag_detector/calibration/thresholds.json \
  --out outputs/semantic_muterag_detector/eval_v3
```

Use `--embedding-backend tfidf` for a no-download smoke test. Use
`--embedding-backend auto` or `sentence-transformers` for the normal semantic
embedding path.
