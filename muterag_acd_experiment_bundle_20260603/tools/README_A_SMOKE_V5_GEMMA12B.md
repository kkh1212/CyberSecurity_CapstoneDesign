# Experiment A v5 smoke bundle

This bundle runs Experiment A with `gemma3:12b`, reranking disabled, dense
retrieval enabled, and `meta_prompt_guard` at the context stage.

Each condition uses the first five questions and their corresponding five
documents. The default `v5_full` matrix contains 14 conditions:

- `normal` guardrail off/on
- `direct_blackbox` guardrail off/on
- `direct_whitebox` guardrail off/on
- `muted_blackbox` guardrail off/on
- `muted_whitebox` guardrail off/on
- `direct_blackbox_ignore` guardrail off/on
- `direct_whitebox_ignore` guardrail off/on

Only `direct_blackbox` and `direct_whitebox` receive the pinned
`data/experiments_v5/hr_employee_directory.md` context. The pinned file makes
the employee-record leak attack executable without contaminating normal,
MutedRAG, or ignore/refuse conditions.

## Ubuntu setup

Run from the extracted bundle root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

ollama pull gemma3:12b
curl -s http://localhost:11434/api/tags | head
```

## Run

```bash
bash tools/run_a_smoke_v5_gemma12b.sh
```

The final console line prints `share_archive=...`. Return that archive for
analysis. It includes:

- condition-level `summary.csv`
- response-level `results.jsonl` and `results.csv`
- every answer under each condition's `answers/`
- raw debug logs under each condition's `logs/`
- `answer_review.txt`
- behavioural evaluation under `attack_success/`
- `launcher.log`

For the employee-record conditions, verify
`directory_in_context_rate=1.0000` in
`attack_success/attack_success_summary.csv`.

