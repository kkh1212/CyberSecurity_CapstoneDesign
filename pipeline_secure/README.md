# Secure RAG Pipeline

Web-facing protected pipeline:

```text
question -> RAG retrieval -> semantic detector -> context sanitizer -> guardrail -> LLM response
```

Use from the existing web server:

```python
from pipeline_secure import run_pipeline

result = run_pipeline("What is the approval process?", return_context=True)
```

The secure pipeline enables:

```bash
SEMANTIC_DETECTOR_ENABLED=true
SEMANTIC_DETECTOR_MODE=improved
SEMANTIC_DETECTOR_BACKEND=auto
SEMANTIC_DETECTOR_VERDICTS=muted_candidate,direct_candidate
CONTEXT_SANITIZER_ENABLED=true
```

Model and guardrail settings are still controlled by the same environment
variables used by the general pipeline.

`GUARDRAIL_PROVIDER` and `GUARDRAIL_API_URL` can also be used as short aliases.
