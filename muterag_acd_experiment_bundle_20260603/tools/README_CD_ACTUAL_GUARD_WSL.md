# Experiment C to D with Meta guard models on WSL

This patch adds a C to D chained runner for the v5 corpus, rerank-off retrieval,
Ollama answers, and the local Meta guard model stack:

- `meta-llama/Llama-Prompt-Guard-2-86M`
- `meta-llama/Llama-Guard-3-1B`

Study C runs semantic detector actions and exports `detected_cases.csv`.
Study D then executes only those detected `(condition, question_id)` pairs,
compares the unmodified baseline with sentence-level sanitization, and records
whether normal question answering availability was restored.

## Apply the incremental patch

Extract this patch on top of the existing v5 Experiment A directory:

```bash
cd /data/workspace/ex0531
tar -xzf muterag_cd_actual_guard_patch_20260602.tgz
chmod +x tools/start_llama_guard_stack.sh
chmod +x tools/run_cd_actual_guard_v5.sh
chmod +x tools/run_cd_actual_guard_smoke_v5.sh
chmod +x tools/run_cd_actual_guard_full_v5.sh
chmod +x experiment_C/experiments/run_study_c2_v5.sh
chmod +x experiment_D/experiments/run_study_d_v5.sh
```

## Prepare models

```bash
cd /data/workspace/ex0531
source .venv/bin/activate
pip install -U "transformers>=4.46,<5" huggingface_hub safetensors
ollama pull gemma3:4b
hf auth login
bash tools/start_llama_guard_stack.sh
curl -s http://127.0.0.1:8191/health
```

Accept the Meta model terms before the first guard-stack startup:

- https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M
- https://huggingface.co/meta-llama/Llama-Guard-3-1B

## Smoke run

```bash
cd /data/workspace/ex0531
source .venv/bin/activate
mkdir -p logs

OLLAMA_MODEL=gemma3:4b \
nohup bash tools/run_cd_actual_guard_smoke_v5.sh \
  > logs/cd_actual_guard_smoke.log 2>&1 &

echo $!
tail -f logs/cd_actual_guard_smoke.log
```

C smoke runs exactly:

```text
muted_whitebox_on x (off, log_only, drop_chunk) x 5 questions = 15 cases
```

D then runs only C-detected questions, once without sanitizer and once with
sentence-level sanitization.

## Full run

Start full only after reviewing smoke false positives and recovery:

```bash
cd /data/workspace/ex0531
source .venv/bin/activate
mkdir -p logs

OLLAMA_MODEL=gemma3:4b \
nohup bash tools/run_cd_actual_guard_full_v5.sh \
  > logs/cd_actual_guard_full.log 2>&1 &

echo $!
tail -f logs/cd_actual_guard_full.log
```

C full runs:

```text
(normal_on, muted_blackbox_on, muted_whitebox_on)
x (off, log_only, drop_chunk)
x 50 questions
= 450 cases
```

D full again executes only the muted cases detected by C `log_only`. The final
archive is written beside the output directory as `CD_actual_guard_*_share.tgz`.
