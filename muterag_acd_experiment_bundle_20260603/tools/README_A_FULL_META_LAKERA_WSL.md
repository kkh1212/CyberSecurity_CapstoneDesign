# Experiment A full comparison on WSL

This portable bundle runs the Experiment A v5 full comparison with:

- one shared guardrail-OFF baseline
- Meta Prompt Guard ON
- Lakera ON
- Ollama `gemma3:12b`
- dense retrieval ON
- reranking OFF
- 50 questions per corpus

The matrix contains 21 groups of 50 cases: 7 corpora OFF once, 7 corpora with
Meta Prompt Guard ON, and 7 corpora with Lakera ON.

## Extract on WSL

Copy the archive from the Windows drive into WSL and extract it anywhere:

```bash
mkdir -p ~/muterag-a-full
cd ~/muterag-a-full
tar -xzf /mnt/c/path/to/muterag_a_full_meta_lakera_v5_20260531.tgz
cd muterag_a_full_meta_lakera_v5_20260531
```

All experiment paths are resolved relative to the extracted project root.

## Prepare Python and Ollama

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

curl http://localhost:11434/api/tags
ollama pull gemma3:12b
```

If Ollama runs on Windows and WSL cannot reach `localhost:11434`, use the
Windows host address as `OLLAMA_BASE_URL`.

## Enter and verify the Lakera API key

Enter the key in the same WSL terminal that will launch the experiment:

```bash
read -r -s -p "Lakera API key: " EXTERNAL_GUARDRAIL_API_KEY
echo
export EXTERNAL_GUARDRAIL_API_KEY
[[ -n "$EXTERNAL_GUARDRAIL_API_KEY" ]] && echo "Lakera API key is set" || echo "Lakera API key is missing"
```

The following command intentionally prints the full secret. Run it only when
needed and avoid screenshots, shared terminals, and copied logs:

```bash
printf 'Lakera API key: %s\n' "$EXTERNAL_GUARDRAIL_API_KEY"
```

Clear the terminal after visually checking it:

```bash
clear
```

## Run in the background

```bash
mkdir -p logs
nohup bash tools/run_a_full_meta_lakera_v5.sh \
  > logs/a_full_meta_lakera_nohup.log 2>&1 &

echo $!
tail -f logs/a_full_meta_lakera_nohup.log
```

Press `Ctrl+C` to stop following the log. The background experiment continues.

## Check progress and results

```bash
tail -n 60 logs/a_full_meta_lakera_nohup.log
grep -n "group_complete\|complete=" logs/a_full_meta_lakera_nohup.log
```

Each completed group writes an `attack_success/attack_success_summary.csv` and
a sibling `_share.tgz` archive under:

```text
outputs/experiments_v5/A_full_compare_<timestamp>/
```

After completion, remove the key from the current shell:

```bash
unset EXTERNAL_GUARDRAIL_API_KEY
```

