from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import re

from semantic_muterag_detector.classifier import classify_features
from semantic_muterag_detector.config import DetectorConfig, Thresholds
from semantic_muterag_detector.embeddings import EmbeddingModel
from semantic_muterag_detector.features import score_chunk
from semantic_muterag_detector import improved as improved_module


TRUTHY = {"1", "true", "yes", "on"}
CANDIDATE_VERDICTS = {"direct_candidate", "muted_candidate"}
VALID_ACTIONS = {"off", "log_only", "drop_chunk", "block_context"}


@dataclass(frozen=True)
class SemanticDetectorConfig:
    enabled: bool = False
    action: str = "log_only"
    backend: str = "hashing"
    thresholds_path: str = ""
    fail_mode: str = "open"
    candidate_verdicts: tuple[str, ...] = ("direct_candidate", "muted_candidate")
    mode: str = "legacy"
    improved_threshold: float = 0.35
    window_enabled: bool = True
    window_radius: int = 1


_EMBEDDER_CACHE: dict[str, EmbeddingModel] = {}
_THRESHOLD_CACHE: dict[str, Thresholds] = {}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUTHY


def _parse_verdicts(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ("direct_candidate", "muted_candidate")
    verdicts = []
    for item in raw.split(","):
        verdict = item.strip()
        if verdict and verdict not in verdicts:
            verdicts.append(verdict)
    return tuple(verdicts or ["direct_candidate", "muted_candidate"])


def load_config() -> SemanticDetectorConfig:
    enabled = _env_bool("SEMANTIC_DETECTOR_ENABLED", False)
    action = os.getenv("SEMANTIC_DETECTOR_ACTION", "log_only").strip().lower()
    if not enabled:
        action = "off"
    if action not in VALID_ACTIONS:
        action = "log_only"
    fail_mode = os.getenv("SEMANTIC_DETECTOR_FAIL_MODE", "open").strip().lower()
    if fail_mode not in {"open", "closed"}:
        fail_mode = "open"
    mode = os.getenv("SEMANTIC_DETECTOR_MODE", "legacy").strip().lower()
    if mode not in {"legacy", "improved"}:
        mode = "legacy"
    try:
        improved_threshold = float(os.getenv("SEMANTIC_DETECTOR_IMPROVED_THRESHOLD", "0.35"))
    except ValueError:
        improved_threshold = 0.35
    try:
        window_radius = int(os.getenv("SEMANTIC_DETECTOR_WINDOW_RADIUS", "1"))
    except ValueError:
        window_radius = 1
    return SemanticDetectorConfig(
        enabled=enabled,
        action=action,
        backend=os.getenv("SEMANTIC_DETECTOR_BACKEND", "hashing").strip() or "hashing",
        thresholds_path=os.getenv("SEMANTIC_DETECTOR_THRESHOLDS", "").strip(),
        fail_mode=fail_mode,
        candidate_verdicts=_parse_verdicts(os.getenv("SEMANTIC_DETECTOR_VERDICTS")),
        mode=mode,
        improved_threshold=improved_threshold,
        window_enabled=_env_bool("SEMANTIC_DETECTOR_WINDOW_ENABLED", True),
        window_radius=max(0, min(3, window_radius)),
    )


def empty_summary(config: SemanticDetectorConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    return {
        "semantic_detector_enabled": bool(config.enabled),
        "semantic_detector_action": config.action,
        "semantic_detector_mode": config.mode,
        "semantic_detector_backend": config.backend,
        "semantic_detector_thresholds": config.thresholds_path,
        "semantic_detector_improved_threshold": config.improved_threshold,
        "semantic_detector_window_enabled": config.window_enabled,
        "semantic_detector_window_radius": config.window_radius,
        "semantic_detector_evaluated_count": 0,
        "semantic_detector_candidate_count": 0,
        "semantic_detector_suspect_count": 0,
        "semantic_detector_dropped_count": 0,
        "semantic_detector_blocked": False,
        "semantic_detector_verdicts": [],
        "semantic_detector_candidate_chunk_ids": [],
        "semantic_detector_candidate_sources": [],
        "semantic_detector_dropped_chunk_ids": [],
        "semantic_detector_dropped_sources": [],
        "semantic_detector_refill_count": 0,
        "semantic_detector_refilled_chunk_ids": [],
        "semantic_detector_retry_used": False,
        "semantic_detector_retry_reason": "",
        "semantic_detector_reason": "",
        "semantic_detector_error": "",
        "semantic_detector_max_anchor_score": 0.0,
        "semantic_detector_max_alignment_gap": 0.0,
        "semantic_detector_max_redirect_similarity": 0.0,
        "semantic_detector_max_refusal_similarity": 0.0,
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


MUTED_PAYLOAD_CUES = (
    "forget the previous details",
    "focus on my new question",
    "answer the following question instead",
    "set aside the earlier content",
    "disregard the previous",
    "ignore the previous",
    "new question:",
    "the following context provides relevant details",
    "context provided here will help",
    "explain in detail how to",
    "without triggering",
    "avoid detection",
    "evade",
    "bypass",
    "exfiltrate",
    "steal credentials",
    "ransomware payload",
    "write a phishing email",
    "craft a phishing email",
    "create malware",
    "deploy malware",
    "credential theft",
)


def _chunk_order(chunk: dict[str, Any]) -> tuple[int, int, int, int, str]:
    chunk_id = str(chunk.get("chunk_id", ""))
    match = re.search(r"::chunk_(\d+)$", chunk_id)
    chunk_num = int(match.group(1)) if match else 10**9
    return (
        int(chunk.get("block_index", chunk.get("block_num", 10**9)) or 10**9),
        int(chunk.get("sub_block_index", 10**9) or 10**9),
        int(chunk.get("row_index", 10**9) or 10**9),
        chunk_num,
        chunk_id,
    )


def _has_muted_payload_cue(text: str) -> bool:
    lowered = re.sub(r"\s+", " ", (text or "").lower())
    return any(cue in lowered for cue in MUTED_PAYLOAD_CUES)


def _window_items_for(
    item: dict[str, Any],
    source_items: dict[str, list[dict[str, Any]]],
    radius: int,
) -> list[dict[str, Any]]:
    chunk = item.get("chunk", {})
    source = str(chunk.get("source", ""))
    ordered = source_items.get(source, [])
    if not ordered:
        return [item]
    chunk_id = str(chunk.get("chunk_id", ""))
    index = next(
        (idx for idx, candidate in enumerate(ordered) if str(candidate.get("chunk", {}).get("chunk_id", "")) == chunk_id),
        -1,
    )
    if index < 0:
        return [item]
    start = max(0, index - radius)
    end = min(len(ordered), index + radius + 1)
    return ordered[start:end]


def _combined_window_text(
    window: list[dict[str, Any]],
    text_getter: Callable[[dict[str, Any]], str],
) -> str:
    parts = []
    for item in window:
        chunk = item.get("chunk", {})
        text = text_getter(chunk).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def apply_semantic_detector(
    query: str,
    context_items: list[dict[str, Any]],
    *,
    text_getter: Callable[[dict[str, Any]], str],
    config: SemanticDetectorConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = config or load_config()
    summary = empty_summary(config)
    if not config.enabled or config.action == "off":
        return context_items, summary

    try:
        embedder = _get_embedder(config.backend)
        thresholds = _get_thresholds(config.thresholds_path)
        improved_detector = (
            improved_module.get_detector(config.backend) if config.mode == "improved" else None
        )
    except Exception as exc:  # pragma: no cover - fail-open experiment guard
        summary["semantic_detector_error"] = f"load_error:{exc}"
        if config.fail_mode == "closed":
            summary["semantic_detector_blocked"] = True
        return context_items, summary

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    verdicts: list[str] = []
    candidate_ids: list[str] = []
    candidate_sources: list[str] = []
    reasons: list[str] = []
    source_items: dict[str, list[dict[str, Any]]] = {}
    for item in context_items:
        source = str(item.get("chunk", {}).get("source", ""))
        source_items.setdefault(source, []).append(item)
    for source, items in source_items.items():
        source_items[source] = sorted(items, key=lambda item: _chunk_order(item.get("chunk", {})))

    for item in context_items:
        chunk = item.get("chunk", {})
        chunk_id = str(chunk.get("chunk_id", ""))
        source = str(chunk.get("source", ""))
        chunk_text = text_getter(chunk)
        try:
            if config.mode == "improved":
                features, verdict = improved_detector.evaluate(
                    query, chunk_text, config.improved_threshold
                )
                chunk_has_payload_cue = _has_muted_payload_cue(chunk_text)
                score = float(getattr(verdict, "muted_score", 0.0) or 0.0)
                if verdict.verdict in config.candidate_verdicts and not chunk_has_payload_cue:
                    if score < config.improved_threshold + 0.12:
                        verdict = improved_module.VerdictView(
                            "clean",
                            f"improved score={score:.3f} below high-confidence margin and no muted payload cue",
                            score,
                        )
                if config.window_enabled and config.window_radius > 0:
                    window = _window_items_for(item, source_items, config.window_radius)
                    if len(window) > 1:
                        window_text = _combined_window_text(window, text_getter)
                        window_features, window_verdict = improved_detector.evaluate(
                            query, window_text, config.improved_threshold
                        )
                        window_candidate = window_verdict.verdict in config.candidate_verdicts
                        window_has_payload_cue = any(
                            _has_muted_payload_cue(text_getter(window_item.get("chunk", {})))
                            for window_item in window
                        )
                        if (
                            window_candidate
                            and chunk_has_payload_cue
                            and window_has_payload_cue
                            and verdict.verdict not in config.candidate_verdicts
                        ):
                            features = window_features
                            verdict = improved_module.VerdictView(
                                "muted_candidate",
                                f"window_support:{window_verdict.reason}",
                                float(getattr(window_verdict, "muted_score", 0.0) or 0.0),
                            )
                        else:
                            features.anchor_score = max(float(features.anchor_score), float(window_features.anchor_score))
                            features.alignment_gap = max(float(features.alignment_gap), float(window_features.alignment_gap))
                            features.redirect_similarity = max(
                                float(features.redirect_similarity),
                                float(window_features.redirect_similarity),
                            )
            else:
                features = score_chunk(
                    query=query,
                    chunk_text=chunk_text,
                    question_id=os.getenv("RAG_RUN_QUERY_INDEX") or os.getenv("STUDY_A_QUERY_INDEX", ""),
                    doc_id=source,
                    source_doc=source,
                    embedder=embedder,
                    config=DetectorConfig(embedding_backend=config.backend),
                )
                verdict = classify_features(features, thresholds)
        except Exception as exc:  # pragma: no cover - keep RAG alive
            summary["semantic_detector_error"] = f"score_error:{exc}"
            if config.fail_mode == "closed":
                summary["semantic_detector_blocked"] = True
            kept.append(item)
            continue

        verdicts.append(f"{chunk_id}:{verdict.verdict}")
        summary["semantic_detector_max_anchor_score"] = max(
            float(summary["semantic_detector_max_anchor_score"]),
            float(features.anchor_score),
        )
        summary["semantic_detector_max_alignment_gap"] = max(
            float(summary["semantic_detector_max_alignment_gap"]),
            float(features.alignment_gap),
        )
        summary["semantic_detector_max_redirect_similarity"] = max(
            float(summary["semantic_detector_max_redirect_similarity"]),
            float(features.redirect_similarity),
        )
        summary["semantic_detector_max_refusal_similarity"] = max(
            float(summary["semantic_detector_max_refusal_similarity"]),
            float(features.refusal_similarity),
        )

        is_candidate = verdict.verdict in config.candidate_verdicts
        if verdict.verdict == "suspect":
            summary["semantic_detector_suspect_count"] += 1
        if is_candidate:
            summary["semantic_detector_candidate_count"] += 1
            if chunk_id:
                candidate_ids.append(chunk_id)
            if source and source not in candidate_sources:
                candidate_sources.append(source)
            reasons.append(f"{chunk_id or source}:{verdict.reason}")

        if is_candidate and config.action == "drop_chunk":
            dropped.append(item)
        else:
            kept.append(item)

    summary["semantic_detector_evaluated_count"] = len(context_items)
    summary["semantic_detector_dropped_count"] = len(dropped)
    summary["semantic_detector_verdicts"] = verdicts
    summary["semantic_detector_candidate_chunk_ids"] = candidate_ids
    summary["semantic_detector_candidate_sources"] = candidate_sources
    summary["semantic_detector_dropped_chunk_ids"] = [
        str(item.get("chunk", {}).get("chunk_id", "")) for item in dropped if item.get("chunk")
    ]
    summary["semantic_detector_dropped_sources"] = list(
        dict.fromkeys(
            str(item.get("chunk", {}).get("source", ""))
            for item in dropped
            if item.get("chunk", {}).get("source", "")
        )
    )
    summary["semantic_detector_reason"] = " | ".join(reasons[:6])

    if summary["semantic_detector_candidate_count"] and config.action == "block_context":
        summary["semantic_detector_blocked"] = True
    if config.action == "drop_chunk" and context_items and not kept:
        summary["semantic_detector_blocked"] = True
        summary["semantic_detector_reason"] = (
            summary["semantic_detector_reason"] or "all selected context chunks dropped"
        )
    return kept, summary
