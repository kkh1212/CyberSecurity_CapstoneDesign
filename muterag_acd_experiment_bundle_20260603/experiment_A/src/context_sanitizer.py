from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from semantic_muterag_detector.classifier import classify_features
from semantic_muterag_detector.config import DetectorConfig, Thresholds
from semantic_muterag_detector.embeddings import EmbeddingModel
from semantic_muterag_detector.features import score_chunk


TRUTHY = {"1", "true", "yes", "on"}

DIRECTIVE_PATTERNS = (
    r"\bignore all previous instructions\b",
    r"\bignore previous instructions\b",
    r"\bforget the previous details\b",
    r"\bfocus on my new question\b",
    r"\bmust not provide\b",
    r"\bmust respond only\b",
    r"\brespond only with\b",
    r"\btakes precedence over\b",
    r"\bmandatory response standard\b",
    r"\bsystem must\b",
    r"\bdo not answer\b",
    r"\bcannot answer\b",
    r"\bthe document does not confirm this\b",
    r"\breveal\b.*\b(internal|system|secret|confidential)\b",
    r"\bexfiltrat(e|ion)\b",
)

HAZARDOUS_PAYLOAD_PATTERNS = (
    r"\bpipe bomb\b",
    r"\bdetonate\b",
    r"\bmalware\b",
    r"\bransomware\b",
    r"\bcredential theft\b",
    r"\bphishing kit\b",
    r"\bprivilege escalation\b",
)

REDIRECT_PATTERNS = (
    r"\bthe following context provides relevant details\b",
    r"\bnew question\b",
    r"\banswer this question\b",
    r"\bhelp answer this question\b",
)


@dataclass(frozen=True)
class ContextSanitizerConfig:
    enabled: bool = False
    min_remaining_chars: int = 80
    min_remaining_sentences: int = 1
    backend: str = "hashing"
    thresholds_path: str = ""
    fail_mode: str = "open"


_EMBEDDER_CACHE: dict[str, EmbeddingModel] = {}
_THRESHOLD_CACHE: dict[str, Thresholds] = {}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUTHY


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_config() -> ContextSanitizerConfig:
    fail_mode = os.getenv("CONTEXT_SANITIZER_FAIL_MODE", "open").strip().lower()
    if fail_mode not in {"open", "closed"}:
        fail_mode = "open"
    return ContextSanitizerConfig(
        enabled=_env_bool("CONTEXT_SANITIZER_ENABLED", False),
        min_remaining_chars=max(0, _env_int("CONTEXT_SANITIZER_MIN_REMAINING_CHARS", 80)),
        min_remaining_sentences=max(1, _env_int("CONTEXT_SANITIZER_MIN_REMAINING_SENTENCES", 1)),
        backend=os.getenv("CONTEXT_SANITIZER_BACKEND", os.getenv("SEMANTIC_DETECTOR_BACKEND", "hashing")).strip()
        or "hashing",
        thresholds_path=os.getenv(
            "CONTEXT_SANITIZER_THRESHOLDS",
            os.getenv("SEMANTIC_DETECTOR_THRESHOLDS", ""),
        ).strip(),
        fail_mode=fail_mode,
    )


def empty_summary(config: ContextSanitizerConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    return {
        "sanitizer_enabled": bool(config.enabled),
        "sanitizer_detected_chunk_count": 0,
        "sanitizer_sanitized_chunk_count": 0,
        "sanitizer_removed_sentence_count": 0,
        "sanitizer_dropped_chunk_count": 0,
        "sanitizer_refill_used": False,
        "sanitizer_final_context_chunk_count": 0,
        "sanitizer_insufficient_evidence": False,
        "sanitizer_reason": "",
        "sanitizer_sanitized_chunk_ids": [],
        "sanitizer_dropped_chunk_ids": [],
        "sanitizer_error": "",
    }


def _get_embedder(backend: str) -> EmbeddingModel:
    if backend not in _EMBEDDER_CACHE:
        _EMBEDDER_CACHE[backend] = EmbeddingModel(backend)
    return _EMBEDDER_CACHE[backend]


def _get_thresholds(path: str) -> Thresholds:
    if not path:
        return Thresholds()
    resolved = str(Path(path))
    if resolved not in _THRESHOLD_CACHE:
        _THRESHOLD_CACHE[resolved] = Thresholds.from_json(Path(resolved))
    return _THRESHOLD_CACHE[resolved]


def _regex_hit(sentence: str, patterns: tuple[str, ...]) -> bool:
    lowered = sentence.lower()
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)


def _risky_sentence_indexes(features) -> set[int]:
    risky: set[int] = set()
    sentence_count = len(features.sentences)
    if not sentence_count:
        return risky

    for idx, sentence in enumerate(features.sentences):
        if _regex_hit(sentence, DIRECTIVE_PATTERNS):
            risky.add(idx)
            continue
        if _regex_hit(sentence, REDIRECT_PATTERNS):
            risky.add(idx)
            continue
        if _regex_hit(sentence, HAZARDOUS_PAYLOAD_PATTERNS) and _regex_hit(sentence, DIRECTIVE_PATTERNS + REDIRECT_PATTERNS):
            risky.add(idx)

    break_idx = int(features.max_break_index)
    if break_idx >= 0 and sentence_count >= 2:
        similarities = list(features.similarities or [])
        anchor = float(features.max_similarity or 0.0)
        p20 = float(features.p20_similarity or 0.0)
        for idx in range(break_idx + 1, sentence_count):
            similarity = float(similarities[idx]) if idx < len(similarities) else 0.0
            far_from_query = similarity <= p20 or (anchor - similarity) >= 0.12
            if far_from_query and (
                _regex_hit(features.sentences[idx], HAZARDOUS_PAYLOAD_PATTERNS + REDIRECT_PATTERNS + DIRECTIVE_PATTERNS)
                or idx == break_idx + 1
            ):
                risky.add(idx)

    return risky


def _enough_evidence(sentences: list[str], config: ContextSanitizerConfig) -> bool:
    if len(sentences) < config.min_remaining_sentences:
        return False
    remaining_text = " ".join(sentences).strip()
    return len(remaining_text) >= config.min_remaining_chars


def _sanitize_text(
    query: str,
    text: str,
    *,
    source: str,
    chunk_id: str,
    embedder: EmbeddingModel,
    thresholds: Thresholds,
    config: ContextSanitizerConfig,
) -> dict[str, Any]:
    features = score_chunk(
        query=query,
        chunk_text=text,
        question_id=os.getenv("RAG_RUN_QUERY_INDEX") or os.getenv("STUDY_A_QUERY_INDEX", ""),
        doc_id=source,
        source_doc=source,
        embedder=embedder,
        config=DetectorConfig(embedding_backend=config.backend),
    )
    verdict = classify_features(features, thresholds)
    risky_indexes = _risky_sentence_indexes(features)
    kept_sentences = [
        sentence for idx, sentence in enumerate(features.sentences) if idx not in risky_indexes
    ]
    dropped = not _enough_evidence(kept_sentences, config)
    sanitized_text = "\n".join(kept_sentences).strip() if risky_indexes else text.strip()
    return {
        "chunk_id": chunk_id,
        "source": source,
        "verdict": verdict.verdict,
        "reason": verdict.reason,
        "removed_count": len(risky_indexes),
        "sanitized_text": sanitized_text,
        "dropped": dropped,
    }


def merge_retry_summary(base: dict[str, Any], retry: dict[str, Any], *, retry_reason: str) -> dict[str, Any]:
    base["sanitizer_refill_used"] = True
    for field in ("sanitizer_detected_chunk_count", "sanitizer_sanitized_chunk_count", "sanitizer_removed_sentence_count"):
        base[field] = int(base.get(field, 0) or 0) + int(retry.get(field, 0) or 0)
    base["sanitizer_dropped_chunk_count"] = int(base.get("sanitizer_dropped_chunk_count", 0) or 0) + int(
        retry.get("sanitizer_dropped_chunk_count", 0) or 0
    )
    for field in ("sanitizer_sanitized_chunk_ids", "sanitizer_dropped_chunk_ids"):
        merged = list(base.get(field, []) or [])
        for value in list(retry.get(field, []) or []):
            if value and value not in merged:
                merged.append(value)
        base[field] = merged
    base["sanitizer_final_context_chunk_count"] = int(retry.get("sanitizer_final_context_chunk_count", 0) or 0)
    base["sanitizer_insufficient_evidence"] = bool(retry.get("sanitizer_insufficient_evidence"))
    reason_parts = [str(base.get("sanitizer_reason") or ""), f"retry:{retry_reason}", str(retry.get("sanitizer_reason") or "")]
    base["sanitizer_reason"] = " | ".join(part for part in reason_parts if part)
    if retry.get("sanitizer_error"):
        base["sanitizer_error"] = " | ".join(
            part for part in [str(base.get("sanitizer_error") or ""), str(retry.get("sanitizer_error") or "")] if part
        )
    return base


def apply_context_sanitizer(
    query: str,
    context_items: list[dict[str, Any]],
    *,
    text_getter: Callable[[dict[str, Any]], str],
    semantic_detector_summary: dict[str, Any],
    config: ContextSanitizerConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = config or load_config()
    summary = empty_summary(config)
    if not config.enabled:
        summary["sanitizer_final_context_chunk_count"] = len(context_items)
        return context_items, summary

    candidate_ids = set(semantic_detector_summary.get("semantic_detector_candidate_chunk_ids", []) or [])
    candidate_sources = set(semantic_detector_summary.get("semantic_detector_candidate_sources", []) or [])
    summary["sanitizer_detected_chunk_count"] = int(
        semantic_detector_summary.get("semantic_detector_candidate_count", 0) or 0
    )
    if not candidate_ids and not candidate_sources:
        summary["sanitizer_final_context_chunk_count"] = len(context_items)
        return context_items, summary

    try:
        embedder = _get_embedder(config.backend)
        thresholds = _get_thresholds(config.thresholds_path)
    except Exception as exc:  # pragma: no cover - experiment fail mode
        summary["sanitizer_error"] = f"load_error:{exc}"
        summary["sanitizer_insufficient_evidence"] = config.fail_mode == "closed"
        summary["sanitizer_final_context_chunk_count"] = len(context_items)
        return ([] if config.fail_mode == "closed" else context_items), summary

    kept: list[dict[str, Any]] = []
    reasons: list[str] = []
    for item in context_items:
        chunk = item.get("chunk", {})
        chunk_id = str(chunk.get("chunk_id", ""))
        source = str(chunk.get("source", ""))
        should_sanitize = (chunk_id and chunk_id in candidate_ids) if candidate_ids else (source and source in candidate_sources)
        if not should_sanitize:
            kept.append(item)
            continue

        try:
            result = _sanitize_text(
                query,
                text_getter(chunk),
                source=source,
                chunk_id=chunk_id,
                embedder=embedder,
                thresholds=thresholds,
                config=config,
            )
        except Exception as exc:  # pragma: no cover - keep RAG alive
            summary["sanitizer_error"] = f"sanitize_error:{exc}"
            if config.fail_mode == "closed":
                continue
            kept.append(item)
            continue

        removed_count = int(result["removed_count"])
        if removed_count > 0:
            summary["sanitizer_sanitized_chunk_count"] += 1
            summary["sanitizer_removed_sentence_count"] += removed_count
            if chunk_id:
                summary["sanitizer_sanitized_chunk_ids"].append(chunk_id)
        reasons.append(f"{chunk_id or source}:{result['verdict']} removed={removed_count} dropped={result['dropped']}")

        if result["dropped"]:
            summary["sanitizer_dropped_chunk_count"] += 1
            if chunk_id:
                summary["sanitizer_dropped_chunk_ids"].append(chunk_id)
            continue

        sanitized_item = {**item, "chunk": {**chunk, "text": result["sanitized_text"]}}
        kept.append(sanitized_item)

    summary["sanitizer_final_context_chunk_count"] = len(kept)
    summary["sanitizer_insufficient_evidence"] = len(context_items) > 0 and len(kept) == 0
    summary["sanitizer_reason"] = " | ".join(reasons[:6])
    return kept, summary
