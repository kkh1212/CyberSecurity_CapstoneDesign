from __future__ import annotations

from .runtime import run_existing_query_pipeline


def run_pipeline(query: str, documents_dir: str | None = None, return_context: bool = True) -> dict:
    """Run the baseline web-facing RAG pipeline.

    Flow: question -> RAG retrieval -> guardrail -> LLM response.
    Detector and sanitizer are disabled here by design.
    """

    return run_existing_query_pipeline(
        query=query,
        documents_dir=documents_dir,
        return_context=return_context,
        pipeline="general",
        secure=False,
    )
