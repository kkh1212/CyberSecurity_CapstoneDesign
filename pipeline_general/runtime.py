from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT_DIR / "pipeline_general" / "logs"

_SRC_MODULE_RELOAD_ORDER = (
    "src.config",
    "src.ollama_client",
    "src.external_guardrail",
    "src.semantic_context_detector",
    "src.context_sanitizer",
    "src.runtime_guard",
    "src.retrievers",
    "src.reranker",
    "src.query_app",
)


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def _setdefault(name: str, value: str) -> None:
    if os.getenv(name) is None:
        os.environ[name] = value


def _set(name: str, value: str) -> None:
    os.environ[name] = value


def _mirror_guardrail_env() -> None:
    provider = os.getenv("GUARDRAIL_PROVIDER")
    api_url = os.getenv("GUARDRAIL_API_URL")
    api_key = os.getenv("GUARDRAIL_API_KEY")
    if provider and not os.getenv("EXTERNAL_GUARDRAIL_PROVIDER"):
        os.environ["EXTERNAL_GUARDRAIL_PROVIDER"] = provider
    if api_url and not os.getenv("EXTERNAL_GUARDRAIL_API_URL"):
        os.environ["EXTERNAL_GUARDRAIL_API_URL"] = api_url
    if api_key and not os.getenv("EXTERNAL_GUARDRAIL_API_KEY"):
        os.environ["EXTERNAL_GUARDRAIL_API_KEY"] = api_key


def _apply_common_env(documents_dir: str | None, return_context: bool) -> None:
    _setdefault("LLM_PROVIDER", "ollama")
    _setdefault("ANSWER_BACKEND", "ollama")
    _setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
    _setdefault("EXTERNAL_GUARDRAIL_ENABLED", "true")
    _setdefault("EXTERNAL_GUARDRAIL_STAGES", "context")
    _setdefault("EXTERNAL_GUARDRAIL_ACTION", "block")
    _setdefault("EXTERNAL_GUARDRAIL_FAIL_MODE", "open")
    _setdefault("ENABLE_DENSE", "true")
    _setdefault("ENABLE_RERANK", "false")
    _setdefault("DEBUG_CONTEXT_PREVIEW", "true" if return_context else "false")
    _setdefault("MUTEDRAG_ATTACK_EVAL", "false")
    if documents_dir:
        _set("RAW_DOCS_DIR", str(Path(documents_dir).expanduser()))
    _mirror_guardrail_env()
    _setdefault("EXTERNAL_GUARDRAIL_PROVIDER", "meta_prompt_guard")


def _apply_pipeline_env(*, secure: bool) -> None:
    # Keep the legacy runtime guard disabled in both modes so the comparison is:
    # general = RAG + external guardrail
    # secure  = RAG + semantic detector + context sanitizer + external guardrail
    _set("DETECTOR_ENABLED", "false")
    _set("RUNTIME_SANITIZER_ENABLED", "false")
    _set("RUNTIME_REQUERY_MAX_ATTEMPTS", "0")

    if secure:
        _set("SEMANTIC_DETECTOR_ENABLED", "true")
        _set("SEMANTIC_DETECTOR_ACTION", "log_only")
        _setdefault("SEMANTIC_DETECTOR_MODE", "improved")
        _setdefault("SEMANTIC_DETECTOR_BACKEND", "auto")
        _setdefault("SEMANTIC_DETECTOR_IMPROVED_THRESHOLD", "0.35")
        _setdefault("SEMANTIC_DETECTOR_WINDOW_ENABLED", "true")
        _setdefault("SEMANTIC_DETECTOR_WINDOW_RADIUS", "1")
        _setdefault("SEMANTIC_DETECTOR_VERDICTS", "muted_candidate,direct_candidate")
        _setdefault("SEMANTIC_DETECTOR_FAIL_MODE", "open")
        _set("CONTEXT_SANITIZER_ENABLED", "true")
        _setdefault("CONTEXT_SANITIZER_BACKEND", os.getenv("SEMANTIC_DETECTOR_BACKEND", "auto"))
        _setdefault("CONTEXT_SANITIZER_MIN_REMAINING_CHARS", "80")
        _setdefault("CONTEXT_SANITIZER_MIN_REMAINING_SENTENCES", "1")
        _setdefault("CONTEXT_SANITIZER_FAIL_MODE", "open")
    else:
        _set("SEMANTIC_DETECTOR_ENABLED", "false")
        _set("CONTEXT_SANITIZER_ENABLED", "false")


def _reload_query_runtime() -> None:
    # src.config reads env at import time; reload the env-sensitive modules so
    # web callers can switch between the two pipeline modes in one process.
    for module_name in _SRC_MODULE_RELOAD_ORDER:
        module = sys.modules.get(module_name)
        if module is not None:
            importlib.reload(module)


def _format_guardrail_blocked(debug: dict[str, Any]) -> bool:
    return bool(
        debug.get("input_guardrail_blocked")
        or debug.get("context_guardrail_blocked")
        or debug.get("output_guardrail_blocked")
    )


def _blocked_by(debug: dict[str, Any]) -> list[str]:
    provider_blocked_by = debug.get("external_guardrail_blocked_by")
    if isinstance(provider_blocked_by, list) and provider_blocked_by:
        return [str(item) for item in provider_blocked_by]
    if isinstance(provider_blocked_by, str) and provider_blocked_by:
        return [provider_blocked_by]
    stages = []
    for stage in ("input", "context", "output"):
        if debug.get(f"{stage}_guardrail_blocked"):
            stages.append(stage)
    return stages


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"none", "null"}:
        return None
    if "," in text and not re.search(r"\s", text):
        return [part for part in text.split(",") if part]
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _split_sections(stdout_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"preamble": []}
    current = "preamble"
    for line in stdout_text.splitlines():
        match = re.match(r"^===\s*(.*?)\s*===$", line.strip())
        if match:
            current = match.group(1)
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _parse_debug(lines: list[str]) -> dict[str, Any]:
    debug: dict[str, Any] = {}
    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        debug[key] = _parse_scalar(value)
    return debug


def _parse_sources(lines: list[str]) -> list[str]:
    sources = []
    for line in lines:
        line = line.strip()
        if line.startswith("- "):
            sources.append(line[2:].strip())
        elif line and not line.startswith("("):
            sources.append(line)
    return sources


def _parse_answer(sections: dict[str, list[str]]) -> str:
    answer_lines = sections.get("RAG Response") or sections.get("General LLM Response") or []
    return "\n".join(line for line in answer_lines).strip()


def _parse_context_preview(debug: dict[str, Any]) -> str:
    previews: list[str] = []
    for key in sorted(debug):
        if key.startswith("selected_context_preview_"):
            value = debug.get(key)
            if value:
                previews.append(str(value))
    return "\n\n".join(previews)


def _make_security(debug: dict[str, Any], *, secure: bool) -> dict[str, Any]:
    return {
        "detector_enabled": bool(debug.get("semantic_detector_enabled", secure)),
        "candidate_count": int(debug.get("semantic_detector_candidate_count") or 0),
        "candidate_chunk_ids": debug.get("semantic_detector_candidate_chunk_ids") or [],
        "candidate_sources": debug.get("semantic_detector_candidate_sources") or [],
        "sanitizer_enabled": bool(debug.get("sanitizer_enabled", secure)),
        "sanitized_chunk_count": int(debug.get("sanitizer_sanitized_chunk_count") or 0),
        "removed_sentence_count": int(debug.get("sanitizer_removed_sentence_count") or 0),
        "dropped_chunk_count": int(debug.get("sanitizer_dropped_chunk_count") or 0),
        "sanitized_chunk_ids": debug.get("sanitizer_sanitized_chunk_ids") or [],
        "dropped_chunk_ids": debug.get("sanitizer_dropped_chunk_ids") or [],
        "reason": debug.get("sanitizer_reason") or debug.get("semantic_detector_reason") or "",
    }


def _install_phase_timers(query_app_module: Any) -> dict[str, float]:
    phase_times = {
        "retrieval_duration_sec": 0.0,
        "detector_duration_sec": 0.0,
        "sanitizer_duration_sec": 0.0,
        "guardrail_duration_sec": 0.0,
        "llm_duration_sec": 0.0,
    }

    def wrap(attr_name: str, phase_key: str) -> None:
        original = getattr(query_app_module, attr_name, None)
        if original is None or getattr(original, "_pipeline_timed", False):
            return

        def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                phase_times[phase_key] += time.perf_counter() - started

        timed._pipeline_timed = True  # type: ignore[attr-defined]
        setattr(query_app_module, attr_name, timed)

    wrap("hybrid_search", "retrieval_duration_sec")
    wrap("rerank_results", "retrieval_duration_sec")
    wrap("apply_semantic_detector", "detector_duration_sec")
    wrap("apply_context_sanitizer", "sanitizer_duration_sec")
    wrap("check_external_guardrail", "guardrail_duration_sec")
    wrap("ask_ollama", "llm_duration_sec")
    return phase_times


def _make_timing(total_duration_sec: float, debug: dict[str, Any], phase_times: dict[str, float]) -> dict[str, Any]:
    elapsed = debug.get("elapsed_seconds")
    total = float(elapsed) if isinstance(elapsed, (int, float)) else total_duration_sec
    return {
        "total_duration_sec": round(total, 6),
        "retrieval_duration_sec": round(phase_times.get("retrieval_duration_sec", 0.0), 6),
        "detector_duration_sec": round(phase_times.get("detector_duration_sec", 0.0), 6),
        "sanitizer_duration_sec": round(phase_times.get("sanitizer_duration_sec", 0.0), 6),
        "guardrail_duration_sec": round(phase_times.get("guardrail_duration_sec", 0.0), 6),
        "llm_duration_sec": round(phase_times.get("llm_duration_sec", 0.0), 6),
    }


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_existing_query_pipeline(
    *,
    query: str,
    documents_dir: str | None,
    return_context: bool,
    pipeline: str,
    secure: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _apply_common_env(documents_dir, return_context)
    _apply_pipeline_env(secure=secure)
    _reload_query_runtime()

    stdout_buffer = io.StringIO()
    error = ""
    phase_times = {
        "retrieval_duration_sec": 0.0,
        "detector_duration_sec": 0.0,
        "sanitizer_duration_sec": 0.0,
        "guardrail_duration_sec": 0.0,
        "llm_duration_sec": 0.0,
    }
    try:
        query_app = importlib.import_module("src.query_app")
        phase_times = _install_phase_timers(query_app)
        with contextlib.redirect_stdout(stdout_buffer):
            query_app.run_query(query)
    except Exception:
        error = traceback.format_exc()
    duration = time.perf_counter() - started

    stdout_text = stdout_buffer.getvalue()
    sections = _split_sections(stdout_text)
    debug = _parse_debug(sections.get("Debug", []))
    answer = _parse_answer(sections)
    sources = _parse_sources(sections.get("Sources", []))
    context_preview = _parse_context_preview(debug) if return_context else ""
    blocked = _format_guardrail_blocked(debug)
    blocked_by = _blocked_by(debug)

    result: dict[str, Any] = {
        "query": query,
        "answer": answer if not error else "",
        "pipeline": pipeline,
        "sources": sources,
        "context_preview": context_preview,
        "guardrail": {
            "enabled": bool(debug.get("external_guardrail_enabled", _truthy(os.getenv("EXTERNAL_GUARDRAIL_ENABLED")))),
            "provider": debug.get("external_guardrail_provider", os.getenv("EXTERNAL_GUARDRAIL_PROVIDER", "")),
            "blocked": blocked,
            "blocked_by": blocked_by,
            "reason": debug.get("external_guardrail_reason", ""),
            "categories": debug.get("external_guardrail_categories", []),
            "error": debug.get("external_guardrail_error", ""),
        },
        "security": _make_security(debug, secure=secure),
        "timing": _make_timing(duration, debug, phase_times),
        "debug": debug,
        "raw_stdout": stdout_text,
        "error": error,
    }
    _append_jsonl(Path(os.getenv("PIPELINE_QUERY_LOG", LOG_DIR / "queries.jsonl")), result)
    return result
