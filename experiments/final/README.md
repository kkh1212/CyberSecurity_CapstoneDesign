# Final MutedRAG Experiment Pipeline

This directory stages the controlled corpus from `/home/riceburger/workspace/capstone/mutedrag_corpus_draft` into the existing RAG ingest/query/eval flow.

Important wording: in the MutedRAG paper, the relevant guardrail is the model's own safety/alignment behavior. These scripts cannot turn that off inside an Ollama model. `EXTERNAL_GUARDRAIL_*` means the separate adapter in `src/external_guardrail.py`, not the model's built-in safety behavior.

## Smoke Test

```bash
cd /home/riceburger/workspace/capstone/testest
A_MAX_QUESTIONS=3 ./experiments/final/run_final_a.sh
B_MAX_QUESTIONS=3 B_RATES="0 0.10 1.0" ./experiments/final/run_final_b.sh
C_MAX_QUESTIONS=3 ./experiments/final/run_final_c.sh
```

## Full Runs

```bash
cd /home/riceburger/workspace/capstone/testest
./experiments/final/run_final_a.sh
./experiments/final/run_final_b.sh
./experiments/final/run_final_c.sh
```

Default model is `gemma3:12b`. Override it with `OLLAMA_MODEL` for model comparison.

```bash
OLLAMA_MODEL=qwen3:8b ./experiments/final/run_final_a.sh
OLLAMA_MODEL=gemma3:27b ./experiments/final/run_final_a.sh
```

## Outputs

- Staged corpus: `data/final_stage/<run_id>/<condition>/`
- Indexes: `outputs/final_indexes/<run_id>/<condition>/`
- Results: `experiments/results/<run_id>/<condition>/`
- Study B contamination summary: `experiments/results/<run_id>/final_b_summary.csv`

## Conditions

- A: paper-style baseline. Project detector/runtime sanitizer/external adapter are off; model safety behavior remains active implicitly. Compare `A_normal_only` vs `A_normal_muted_05` by default. Set `A_ATTACK_MODE=muted_all A_ATTACK_RATE=1.0 A_ATTACK_CONDITION=A_normal_muted` for the old 50% exposure setting.
- B: same defense-off setting as A, with attack document rate sweep.
- C: project defense evaluation. Ingest detector plus runtime detector/sanitizer are on; external adapter remains off.

Study B rates are interpreted as attack document counts. With 60 queries, `0.01`, `0.03`, `0.05`, and `0.10` stage about 1, 2, 3, and 6 attack files.

## Lakera External Guardrail

The final scripts default to the project defense-off setting, but they can run with the external Lakera adapter by setting these variables:

```bash
export EXTERNAL_GUARDRAIL_API_KEY="<your_lakera_key>"
FINAL_EXTERNAL_GUARDRAIL_ENABLED=true \
FINAL_EXTERNAL_GUARDRAIL_PROVIDER=lakera \
FINAL_EXTERNAL_GUARDRAIL_STAGES=context \
FINAL_EXTERNAL_GUARDRAIL_ACTION=block \
./experiments/final/run_final_a.sh
```

Use `FINAL_EXTERNAL_GUARDRAIL_ACTION=flag_only` if you want to measure Lakera flags without blocking the answer.

## Study A Attack Rate

Study A now defaults to about 5% attack chunks:

```bash
A_ATTACK_RATE=0.05 ./experiments/final/run_final_a.sh
```

For the old high-exposure setting, use:

```bash
A_ATTACK_MODE=muted_all A_ATTACK_RATE=1.0 A_ATTACK_CONDITION=A_normal_muted ./experiments/final/run_final_a.sh
```
