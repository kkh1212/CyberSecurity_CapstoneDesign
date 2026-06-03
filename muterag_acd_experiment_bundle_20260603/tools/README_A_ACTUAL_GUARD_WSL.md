# Experiment A with Meta guard models on WSL

This patch keeps the existing Experiment A v5 corpus, retrieval pipeline,
rerank-off profile, HR-directory pinning, and Ollama answer model. It replaces
the regex-only guardrail baseline with a local model-based stack:

- `meta-llama/Llama-Prompt-Guard-2-86M`
- `meta-llama/Llama-Guard-3-1B`

The guard models stay loaded in one local HTTP process. Experiment A continues
to launch one query process per question and sends each original retrieved
chunk to that service independently.

## 1. Apply the patch

From Windows PowerShell:

```powershell
scp .\study_a_actual_guard_patch_20260601.tgz ubuntu@10.0.10.87:/data/workspace/ex0531/
```

From WSL:

```bash
cd /data/workspace/ex0531
tar -xzf study_a_actual_guard_patch_20260601.tgz
chmod +x tools/start_llama_guard_stack.sh
chmod +x tools/run_a_actual_guard_v5.sh
chmod +x tools/run_a_actual_guard_smoke_v5.sh
chmod +x tools/run_a_actual_guard_full_v5.sh
```

## 2. Prepare the existing environment

```bash
cd /data/workspace/ex0531
source .venv/bin/activate

python - <<'PY'
import torch
print("torch:", torch.__version__, "cuda:", torch.cuda.is_available())
PY

pip install -U "transformers>=4.46,<5" huggingface_hub safetensors
ollama pull gemma3:4b
curl http://localhost:11434/api/tags
```

Do not reinstall `torch` unless the import check fails. A blanket reinstall can
download several gigabytes of CUDA wheels. If the import check fails, install a
PyTorch build that matches the WSL GPU driver before continuing.

Open both model pages in a browser and accept the Meta access terms:

- https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M
- https://huggingface.co/meta-llama/Llama-Guard-3-1B

Then authenticate once in WSL:

```bash
hf auth login
```

## 3. Start the guardrail service

Prompt Guard defaults to CPU because it is small. Llama Guard uses CUDA when
WSL can access a GPU and falls back to CPU otherwise.

```bash
cd /data/workspace/ex0531
source .venv/bin/activate

bash tools/start_llama_guard_stack.sh
```

Check the service:

```bash
curl -s http://127.0.0.1:8191/health
```

If GPU memory is tight, force both guard models onto CPU:

```bash
PROMPT_GUARD_DEVICE=cpu SAFETY_GUARD_DEVICE=cpu \
  bash tools/start_llama_guard_stack.sh
```

## 4. Run the 70-case smoke test

```bash
cd /data/workspace/ex0531
source .venv/bin/activate
mkdir -p logs

OLLAMA_MODEL=gemma3:4b \
nohup bash tools/run_a_actual_guard_smoke_v5.sh \
  > logs/a_actual_guard_smoke.log 2>&1 &

echo $!
tail -f logs/a_actual_guard_smoke.log
```

The matrix is:

```text
7 corpora x (guardrail off + stack on) x 5 questions = 70 cases
```

## 5. Run the 700-case full test

Start this only after checking smoke-test false positives and `blocked_by`
values:

```bash
cd /data/workspace/ex0531
source .venv/bin/activate
mkdir -p logs

OLLAMA_MODEL=gemma3:4b \
nohup bash tools/run_a_actual_guard_full_v5.sh \
  > logs/a_actual_guard_full.log 2>&1 &

echo $!
tail -f logs/a_actual_guard_full.log
```

The matrix is:

```text
7 corpora x (guardrail off + stack on) x 50 questions = 700 cases
```

Each completed group creates a sibling `_share.tgz` archive under:

```text
outputs/experiments_v5/A_actual_guard_<mode>_<timestamp>/
```
