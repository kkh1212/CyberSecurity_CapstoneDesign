# Pipeline Overhead Measurement

This runner compares:

```text
general = RAG + guardrail
secure  = RAG + semantic detector + context sanitizer + guardrail
```

It does not start or depend on the web server. It imports the two web-facing
pipeline functions directly and writes benchmark artifacts to
`pipeline_overhead/outputs/`.

## Smoke

```bash
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_MODEL=qwen3:4b
export EXTERNAL_GUARDRAIL_ENABLED=true
export EXTERNAL_GUARDRAIL_PROVIDER=llama_guard_stack
export EXTERNAL_GUARDRAIL_API_URL=http://127.0.0.1:8191/check
export EXTERNAL_GUARDRAIL_STAGES=context
export EXTERNAL_GUARDRAIL_ACTION=block
export ENABLE_DENSE=true
export ENABLE_RERANK=false

python -m pipeline_overhead.run_benchmark \
  --questions pipeline_overhead/benchmark_questions.yaml \
  --repeats 1 \
  --warmup 0 \
  --output-dir pipeline_overhead/outputs/smoke
```

## Full

```bash
python -m pipeline_overhead.run_benchmark \
  --questions pipeline_overhead/benchmark_questions.yaml \
  --repeats 3 \
  --warmup 2 \
  --output-dir pipeline_overhead/outputs/full
```

Output files:

- `per_query_results.jsonl`: raw answers, sources, context previews, debug fields
- `phase_timings.csv`: per-call timing fields
- `resource_samples.csv`: CPU/RAM/GPU samples, including detected Ollama,
  guardrail, and vLLM helper processes when `psutil` can see them
- `overhead_summary.csv`: presentation-ready group summary
- `run_config.json`: model, guardrail, retrieval settings
