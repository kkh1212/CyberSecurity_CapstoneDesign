# vLLM v5 Experiment Runner

This is an additive path. The existing Ollama scripts still work. These scripts run the same A/C/D v5 experiments against an OpenAI-compatible vLLM server.

## Models

The default 24GB VRAM benchmark set lives in `tools/vllm_models_v5.tsv`.

| Alias | Hugging Face model |
| --- | --- |
| `qwen25_7b` | `Qwen/Qwen2.5-7B-Instruct` |
| `llama31_8b` | `meta-llama/Llama-3.1-8B-Instruct` |
| `gemma3_12b_awq` | `pytorch/gemma-3-12b-it-AWQ-INT4` |
| `qwen25_14b_awq` | `Qwen/Qwen2.5-14B-Instruct-AWQ` |
| `gpt_oss_20b` | `openai/gpt-oss-20b` |

## GPU Server Setup

```bash
cd /data/workspace/ex2
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install vllm
```

`llama31_8b` may require Hugging Face login and Meta license approval.

```bash
huggingface-cli login
```

## Run One Model

Terminal 1:

```bash
source .venv/bin/activate
bash tools/serve_vllm_model_v5.sh qwen25_14b_awq
```

Terminal 2:

```bash
source .venv/bin/activate
RUN_MODE=smoke bash tools/run_acd_full_v5_vllm.sh qwen25_14b_awq
```

For full:

```bash
RUN_MODE=full bash tools/run_acd_full_v5_vllm.sh qwen25_14b_awq
```

Outputs are separated under:

```text
outputs/experiments_v5/vllm/<model_alias>/
```

## Run All Five Sequentially

Smoke first:

```bash
source .venv/bin/activate
RUN_MODE=smoke bash tools/run_vllm_benchmark_v5.sh
```

Full:

```bash
source .venv/bin/activate
RUN_MODE=full bash tools/run_vllm_benchmark_v5.sh
```

To run only selected models:

```bash
VLLM_MODEL_ALIASES="qwen25_7b qwen25_14b_awq gpt_oss_20b" \
RUN_MODE=full \
bash tools/run_vllm_benchmark_v5.sh
```

## 24GB OOM Knobs

If a model fails to load on a 24GB card, reduce context first:

```bash
VLLM_MAX_MODEL_LEN=4096 bash tools/serve_vllm_model_v5.sh qwen25_14b_awq
```

Then try higher memory use:

```bash
VLLM_GPU_MEMORY_UTILIZATION=0.95 bash tools/serve_vllm_model_v5.sh qwen25_14b_awq
```

You can pass extra vLLM flags without editing the model matrix:

```bash
VLLM_EXTRA_ARGS="--enforce-eager" bash tools/serve_vllm_model_v5.sh gpt_oss_20b
```
