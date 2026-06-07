# General RAG Pipeline

Web-facing baseline pipeline:

```text
question -> RAG retrieval -> guardrail -> LLM response
```

Use from the existing web server:

```python
from pipeline_general import run_pipeline

result = run_pipeline("What is the approval process?", return_context=True)
```

Expected environment:

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:4b
EXTERNAL_GUARDRAIL_ENABLED=true
EXTERNAL_GUARDRAIL_PROVIDER=llama_guard_stack
EXTERNAL_GUARDRAIL_API_URL=http://127.0.0.1:8191/check
EXTERNAL_GUARDRAIL_STAGES=context
EXTERNAL_GUARDRAIL_ACTION=block
ENABLE_DENSE=true
ENABLE_RERANK=false
```

`GUARDRAIL_PROVIDER` and `GUARDRAIL_API_URL` are accepted as aliases and are
mirrored to the `EXTERNAL_GUARDRAIL_*` variables before the existing `src`
runtime is loaded.
