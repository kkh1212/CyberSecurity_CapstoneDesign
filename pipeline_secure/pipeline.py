from __future__ import annotations

from pipeline_general.runtime import run_existing_query_pipeline


def run_pipeline(query: str, documents_dir: str | None = None, return_context: bool = True) -> dict:
    """Run the secure web-facing RAG pipeline.

    Flow: question -> RAG retrieval -> semantic detector -> context sanitizer
    -> guardrail -> LLM response.
    """

    return run_existing_query_pipeline(
        query=query,
        documents_dir=documents_dir,
        return_context=return_context,
        pipeline="secure",
        secure=True,
    )
