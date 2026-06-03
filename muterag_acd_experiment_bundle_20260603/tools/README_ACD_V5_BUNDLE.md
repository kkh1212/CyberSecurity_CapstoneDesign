# MutedRAG v5 A/C/D Runner

This bundle is for running the v5 full experiments with rerank off and Ollama.

## Setup

From the extracted project root on Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
ollama pull gemma3:12b
```

If Ollama is on another host, set `OLLAMA_BASE_URL` before running.

## 10-Second Preflight

These commands start each full experiment and stop after 10 seconds. Exit code `124` from `timeout` means it reached the 10-second limit without an immediate crash.

```bash
bash tools/preflight_10s_v5.sh a
bash tools/preflight_10s_v5.sh c
bash tools/preflight_10s_v5.sh d
```

`D` needs a C full `detected_cases.csv`. If C has not finished yet, D preflight will skip unless you pass `D_DETECTED_CASES=/path/to/detected_cases.csv`.

## Overnight Full Run

```bash
mkdir -p logs
nohup bash tools/run_acd_full_v5_rerank_off.sh > "logs/acd_full_$(date +%Y%m%d_%H%M%S).log" 2>&1 &
echo "PID=$!"
```

The runner executes:

1. A full, rerank off
2. C full, rerank off, `normal_on,muted_blackbox_on,muted_whitebox_on`
3. D full, rerank off, using the C full `detected_cases.csv`

Check progress:

```bash
tail -f logs/acd_full_*.log
```
