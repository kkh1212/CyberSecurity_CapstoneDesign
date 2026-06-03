# MuteRAG A/C/D Experiment Handoff

This bundle is a self-contained A/C/D experiment runtime for `data/experiments_v5`.
The intended knobs are the answer backend/model and the guardrail provider/model.

## Setup

Run from this bundle root on Linux or WSL.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

For Ollama:

```bash
ollama pull gemma3:4b
```

Make scripts executable if needed:

```bash
chmod +x run_A.sh run_C.sh run_D_muted_from_C.sh run_D_direct_from_A.sh run_ACD.sh tools/*.sh
```

## Main knobs

Answer model backend:

```bash
export ANSWER_BACKEND=ollama
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_MODEL=gemma3:4b
```

or:

```bash
export ANSWER_BACKEND=vllm
export LLM_BASE_URL=http://localhost:8000/v1
export LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
```

Guardrail:

```bash
export GUARDRAIL_PROVIDER=meta_prompt_guard
```

Supported answer backends:

- `ollama`: native Ollama `/api/generate`.
- `vllm`: OpenAI-compatible vLLM `/v1/chat/completions`.
- `openai_compatible`: any OpenAI-compatible server.
- `openai`: OpenAI-compatible API shape; set `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` as needed.

Supported guardrail providers:

- `meta_prompt_guard`: local regex/heuristic meta prompt guard, no API key.
- `llama_guard_stack`: local HTTP service from `tools/start_llama_guard_stack.sh`, using Hugging Face model IDs.
- `lakera`: requires `EXTERNAL_GUARDRAIL_API_KEY`.
- `generic_http`: requires `EXTERNAL_GUARDRAIL_API_URL`.
- `off`: disables external guardrail when the condition itself is guardrail-on; normally use A off conditions instead.

## Using Ollama

```bash
ollama pull gemma3:4b
ANSWER_BACKEND=ollama \
OLLAMA_MODEL=gemma3:4b \
GUARDRAIL_PROVIDER=meta_prompt_guard \
bash run_ACD.sh
```

## Using vLLM

Install and serve a model:

```bash
pip install vllm
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-7B-Instruct \
  --host 0.0.0.0 \
  --port 8000
```

Run A/C/D against that vLLM server:

```bash
ANSWER_BACKEND=vllm \
LLM_BASE_URL=http://localhost:8000/v1 \
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct \
GUARDRAIL_PROVIDER=meta_prompt_guard \
bash run_ACD.sh
```

This bundle also includes helper scripts:

```bash
bash tools/serve_vllm_model_v5.sh qwen25_7b
ANSWER_BACKEND=vllm \
LLM_BASE_URL=http://localhost:8000/v1 \
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct \
bash run_ACD.sh
```

## Using Hugging Face guardrail models

`llama_guard_stack` runs local Hugging Face models with `transformers`.
You can swap the guardrail model IDs with environment variables.

```bash
export PROMPT_GUARD_MODEL=meta-llama/Llama-Prompt-Guard-2-86M
export SAFETY_GUARD_MODEL=meta-llama/Llama-Guard-3-1B
bash tools/start_llama_guard_stack.sh
export GUARDRAIL_PROVIDER=llama_guard_stack
```

If the Hugging Face model is gated, log in first:

```bash
huggingface-cli login
```

Then run the experiments:

```bash
ANSWER_BACKEND=ollama \
OLLAMA_MODEL=gemma3:4b \
GUARDRAIL_PROVIDER=llama_guard_stack \
bash run_ACD.sh
```

For a remote Hugging Face Inference Endpoint or any custom guardrail HTTP server,
use `generic_http`. The endpoint should accept:

```json
{"text": "...", "stage": "context", "metadata": {}}
```

and return fields like:

```json
{"flagged": true, "blocked": true, "reason": "...", "categories": ["prompt_injection"]}
```

Example:

```bash
GUARDRAIL_PROVIDER=generic_http \
EXTERNAL_GUARDRAIL_API_URL=https://your-endpoint.example/check \
EXTERNAL_GUARDRAIL_API_KEY=... \
bash run_ACD.sh
```

For Lakera:

```bash
export GUARDRAIL_PROVIDER=lakera
export EXTERNAL_GUARDRAIL_API_KEY='...'
```

## Smoke run

Smoke is the default and uses 5 questions.

```bash
bash run_ACD.sh
```

## Full run

```bash
RUN_MODE=full bash run_ACD.sh
```

You can also run each study separately:

```bash
RUN_MODE=full bash run_A.sh
RUN_MODE=full bash run_C.sh
RUN_MODE=full bash run_D_muted_from_C.sh
RUN_MODE=full bash run_D_direct_from_A.sh
```

## What each study does

### A

`run_A.sh` executes seven document types with guardrail off and guardrail on.

Document types:

- `normal`
- `direct_blackbox`
- `direct_whitebox`
- `muted_blackbox`
- `muted_whitebox`
- `direct_blackbox_ignore`
- `direct_whitebox_ignore`

The employee-leak direct corpora keep the HR directory pinned through:

```bash
A_PIN_CONDITIONS=direct_blackbox,direct_whitebox
```

Important outputs:

- `outputs/latest_a.env`
- `<A run>/summary.csv`
- `<A run>/results.jsonl` under each condition directory
- `<A run>/answer_review.txt`
- `<A run>/attack_success/attack_success_summary.csv`
- `<A run>/attack_success/attack_success_report.md`

Direct leak results are judged with `scripts/eval_attack_success_v5.py`.
MutedRAG success is reported as denial/refusal/block behavior.
The raw answers are preserved in `results.jsonl`, `results.csv`, `answers/`, and `answer_review.txt`.

### C

`run_C.sh` runs detector actions on:

- `normal_on`
- `muted_blackbox_on`
- `muted_whitebox_on`

Actions:

- `off`
- `log_only`
- `drop_chunk`

Important outputs:

- `outputs/latest_c.env`
- `<C run>/c2_summary.csv`
- `<C run>/detected_cases.csv`
- child runs under `<C run>/semantic_off`, `<C run>/log_only`, `<C run>/drop_chunk`

Use `c2_summary.csv` for:

- normal false-positive rate
- muted detection rate
- drop-chunk guardrail block reduction
- normal response / insufficient response rates

Raw answers are in each child run's `results.jsonl`, `results.csv`, `answers/`, and `answer_review.txt`.

### D muted recovery

`run_D_muted_from_C.sh` uses `C detected_cases.csv`.
It reruns only the C-detected muted cases, applies the sanitizer, then reports whether the final answer recovered.

Important outputs:

- `outputs/latest_d_muted.env`
- `<D muted run>/d_summary.csv`
- `<D muted run>/paired_results.csv`

Key columns:

- `sanitized_guardrail_blocked`
- `sanitized_answer_judgement`
- `insufficient_evidence`
- `recovered`
- `outcome_3way`
- `sanitized_answer`

### D direct recovery

`run_D_direct_from_A.sh` exports A guardrail-blocked direct cases first:

```bash
scripts/export_a_blocked_cases_for_d.py
```

Then it sanitizes direct prompt-injection wording and reruns the same question/context.

Important outputs:

- `outputs/latest_d_direct.env`
- `outputs/d_inputs/a_direct_blocked_cases_*.csv`
- `<D direct run>/d_summary.csv`
- `<D direct run>/paired_results.csv`

Key outcome:

- `still_guardrail_blocked`: guardrail still blocked after sanitization.
- `prompt_injection_success`: guardrail passed but the direct instruction was followed.
- `safe_normal_response`: guardrail passed, direct instruction was not followed, and the answer was normal.
- `insufficient_or_not_found`: answer passed but lacked enough evidence.
- `other_response`: ambiguous residual case.

## Common examples

Ollama + Meta prompt guard smoke:

```bash
ANSWER_BACKEND=ollama OLLAMA_MODEL=gemma3:4b GUARDRAIL_PROVIDER=meta_prompt_guard bash run_ACD.sh
```

vLLM + Meta prompt guard full:

```bash
ANSWER_BACKEND=vllm \
LLM_BASE_URL=http://localhost:8000/v1 \
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct \
GUARDRAIL_PROVIDER=meta_prompt_guard \
RUN_MODE=full \
bash run_ACD.sh
```

vLLM + Hugging Face guard stack full:

```bash
bash tools/start_llama_guard_stack.sh
ANSWER_BACKEND=vllm \
LLM_BASE_URL=http://localhost:8000/v1 \
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct \
GUARDRAIL_PROVIDER=llama_guard_stack \
RUN_MODE=full \
bash run_ACD.sh
```

Lakera full:

```bash
export EXTERNAL_GUARDRAIL_API_KEY='...'
OLLAMA_MODEL=gemma3:4b GUARDRAIL_PROVIDER=lakera RUN_MODE=full bash run_ACD.sh
```
