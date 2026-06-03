from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from detector import MutedRAGDetector, estimate_corpus_stats
from detector.risk import get_risk_profile_config
from src.config import (
    DETECTOR_DEBUG,
    DETECTOR_ENABLED,
    DETECTOR_FAIL_MODE,
    DETECTOR_POLICY_ACTIONS,
    DETECTOR_PROFILE,
    DETECTOR_VERSION,
    RETRIEVAL_FLAGGED_SCORE_MULTIPLIER,
)


SUPPORTED_POLICY_ACTIONS = {"index", "review", "quarantine"}
RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def detect_language(text: str) -> str:
    if not text:
        return "unknown"

    hangul_count = sum(1 for char in text if "가" <= char <= "힣")
    hangul_count = sum(1 for char in text if "\uac00" <= char <= "\ud7a3")
    latin_count = sum(1 for char in text if ("a" <= char.lower() <= "z"))

    if hangul_count and latin_count:
        return "mixed"
    if hangul_count:
        return "ko"
    if latin_count:
        return "en"
    return "unknown"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _risk_level_rank(risk_level: str) -> int:
    return RISK_RANK.get((risk_level or "low").lower(), 0)


def _policy_action_for_level(risk_level: str) -> str:
    action = DETECTOR_POLICY_ACTIONS.get((risk_level or "low").lower(), "index")
    if action not in SUPPORTED_POLICY_ACTIONS:
        return "index"
    return action


def _fallback_detection(error_text: str) -> Dict[str, Any]:
    fallback_mode = DETECTOR_FAIL_MODE if DETECTOR_FAIL_MODE in {"allow", "review", "quarantine"} else "allow"
    risk_level = {"allow": "low", "review": "medium", "quarantine": "high"}[fallback_mode]
    adjusted_risk = {"low": 0.0, "medium": 0.5, "high": 0.85}[risk_level]
    should_review = risk_level == "medium"
    should_block = risk_level in {"high", "critical"}
    return {
        "instructionality": {"normalized_score": 0.0},
        "refusal_inducing": {"normalized_score": 0.0},
        "outlier": {"normalized_score": 0.0},
        "base_risk": 0.0,
        "adjusted_risk": adjusted_risk,
        "risk_level": risk_level,
        "should_block": should_block,
        "should_review": should_review,
        "recommended_action": "quarantine" if should_block else "review" if should_review else "allow",
        "triggered_rules": ["detector_error_fallback"],
        "explanation": f"Detector failed and fallback policy `{fallback_mode}` was applied: {error_text}",
        "detector_error": True,
        "profile_used": DETECTOR_PROFILE,
    }


def _build_detector_metadata(chunk: Mapping[str, Any], detection: Mapping[str, Any]) -> Dict[str, Any]:
    risk_level = str(detection.get("risk_level", "low")).lower()
    policy_action = _policy_action_for_level(risk_level)
    review_required = policy_action == "review"
    triggered_rules = [str(rule) for rule in detection.get("triggered_rules", [])]
    instructionality_patterns = [
        str(pattern.get("name", ""))
        for pattern in detection.get("instructionality", {}).get("matched_patterns", [])
        if pattern.get("name")
    ]
    refusal_patterns = [
        str(pattern.get("name", ""))
        for pattern in detection.get("refusal_inducing", {}).get("matched_patterns", [])
        if pattern.get("name")
    ]
    explicit_patterns = [
        str(pattern.get("name", ""))
        for pattern in detection.get("refusal_inducing", {}).get("explicit_high_risk_patterns", [])
        if pattern.get("name")
    ]
    outlier_features = [str(rule) for rule in detection.get("outlier", {}).get("triggered_rules", [])]

    return {
        "document_id": chunk.get("source", ""),
        "language": detect_language(str(chunk.get("text", ""))),
        "instructionality_score": float(detection.get("instructionality", {}).get("normalized_score", 0.0)),
        "refusal_inducing_score": float(detection.get("refusal_inducing", {}).get("normalized_score", 0.0)),
        "outlier_score": float(detection.get("outlier", {}).get("normalized_score", 0.0)),
        "base_risk": float(detection.get("base_risk", 0.0)),
        "adjusted_risk": float(detection.get("adjusted_risk", detection.get("base_risk", 0.0))),
        "risk_level": risk_level,
        "should_block": bool(detection.get("should_block", False)),
        "review_required": review_required,
        "flagged": review_required,
        "triggered_rules": triggered_rules,
        "instructionality_patterns": instructionality_patterns,
        "refusal_patterns": refusal_patterns,
        "explicit_high_risk_patterns": explicit_patterns,
        "outlier_features": outlier_features,
        "detector_profile": str(detection.get("profile_used", DETECTOR_PROFILE)),
        "detector_version": DETECTOR_VERSION,
        "detector_timestamp": _utc_timestamp(),
        "detector_explanation": str(detection.get("explanation", "")),
        "detector_action": policy_action,
        "detector_error": bool(detection.get("detector_error", False)),
    }


def _chunk_sort_key(chunk: Mapping[str, Any]) -> tuple[int, int, int, str]:
    def as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return (
        as_int(chunk.get("page"), -1),
        as_int(chunk.get("block_index"), 10**6),
        as_int(chunk.get("sub_block_index"), 0),
        str(chunk.get("chunk_id", "")),
    )


def _apply_chunk_risk_override(
    chunk: Dict[str, Any],
    *,
    target_risk: float,
    rule_name: str,
    explanation: str,
) -> None:
    updated_risk = max(float(chunk.get("adjusted_risk", 0.0)), target_risk)
    chunk["adjusted_risk"] = round(updated_risk, 4)

    thresholds = get_risk_profile_config(str(chunk.get("detector_profile", DETECTOR_PROFILE))).get("thresholds", {})
    medium_threshold = float(thresholds.get("medium_risk", 0.35))
    high_threshold = float(thresholds.get("high_risk", 0.64))
    critical_threshold = float(thresholds.get("critical_risk", 0.86))

    if updated_risk >= critical_threshold:
        risk_level = "critical"
    elif updated_risk >= high_threshold:
        risk_level = "high"
    elif updated_risk >= medium_threshold:
        risk_level = "medium"
    else:
        risk_level = "low"

    chunk["risk_level"] = risk_level
    chunk["should_block"] = risk_level in {"high", "critical"}
    chunk["review_required"] = risk_level == "medium"
    chunk["flagged"] = chunk["review_required"]
    chunk["detector_action"] = _policy_action_for_level(risk_level)

    triggered_rules = list(chunk.get("triggered_rules", []))
    if rule_name not in triggered_rules:
        triggered_rules.append(rule_name)
    chunk["triggered_rules"] = triggered_rules

    existing_explanation = str(chunk.get("detector_explanation", "")).strip()
    if explanation and explanation not in existing_explanation:
        chunk["detector_explanation"] = f"{existing_explanation} {explanation}".strip()


def _apply_document_context_adjustments(all_chunks: Sequence[Dict[str, Any]]) -> None:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for chunk in all_chunks:
        grouped[str(chunk.get("source", ""))].append(chunk)

    for _, doc_chunks in grouped.items():
        ordered = sorted(doc_chunks, key=_chunk_sort_key)
        if not ordered:
            continue

        chunk_count = len(ordered)
        doc_refusal_density = sum(1 for chunk in ordered if float(chunk.get("refusal_inducing_score", 0.0)) >= 0.11) / chunk_count
        doc_pattern_density = sum(
            1
            for chunk in ordered
            if len(chunk.get("refusal_patterns", [])) + len(chunk.get("explicit_high_risk_patterns", [])) >= 1
        ) / chunk_count
        doc_combined_risk = max(float(chunk.get("adjusted_risk", 0.0)) for chunk in ordered)

        for idx, chunk in enumerate(ordered):
            if str(chunk.get("risk_level", "low")).lower() in {"medium", "high", "critical"}:
                continue

            refusal_score = float(chunk.get("refusal_inducing_score", 0.0))
            outlier_score = float(chunk.get("outlier_score", 0.0))
            instruction_score = float(chunk.get("instructionality_score", 0.0))
            pattern_count = len(chunk.get("refusal_patterns", [])) + len(chunk.get("explicit_high_risk_patterns", []))

            neighbors = [ordered[n] for n in (idx - 1, idx + 1) if 0 <= n < len(ordered)]
            neighbor_refusal = max((float(item.get("refusal_inducing_score", 0.0)) for item in neighbors), default=0.0)
            neighbor_patterns = max(
                (len(item.get("refusal_patterns", [])) + len(item.get("explicit_high_risk_patterns", [])) for item in neighbors),
                default=0,
            )
            neighbor_outlier = max((float(item.get("outlier_score", 0.0)) for item in neighbors), default=0.0)
            abrupt_shift = any(
                abs(float(item.get("refusal_inducing_score", 0.0)) - refusal_score) >= 0.10
                or abs(float(item.get("instructionality_score", 0.0)) - instruction_score) >= 0.10
                for item in neighbors
            )

            if (
                pattern_count >= 1
                and refusal_score >= 0.11
                and outlier_score >= 0.16
                and doc_pattern_density >= 0.25
            ):
                _apply_chunk_risk_override(
                    chunk,
                    target_risk=0.37,
                    rule_name="document_refusal_density_support",
                    explanation="Same-document refusal patterns were dense enough to support this chunk as suspicious.",
                )
                continue

            if (
                refusal_score >= 0.10
                and neighbor_refusal >= 0.11
                and max(outlier_score, neighbor_outlier) >= 0.16
                and (pattern_count >= 1 or neighbor_patterns >= 1)
            ):
                _apply_chunk_risk_override(
                    chunk,
                    target_risk=0.37,
                    rule_name="adjacent_refusal_consistency",
                    explanation="Adjacent chunks in the same document reinforced the restricted-disclosure pattern.",
                )
                continue

            if (
                abrupt_shift
                and max(refusal_score, neighbor_refusal) >= 0.11
                and max(outlier_score, neighbor_outlier) >= 0.17
                and doc_combined_risk >= 0.18
            ):
                _apply_chunk_risk_override(
                    chunk,
                    target_risk=0.37,
                    rule_name="adjacent_abrupt_shift_support",
                    explanation="Abrupt local shift across adjacent chunks increased suspicion for mixed or spliced content.",
                )
                continue

            if (
                refusal_score >= 0.09
                and outlier_score >= 0.15
                and doc_refusal_density >= 0.34
                and doc_combined_risk >= 0.18
            ):
                _apply_chunk_risk_override(
                    chunk,
                    target_risk=0.37,
                    rule_name="document_level_aggregate_support",
                    explanation="Document-level refusal concentration raised this low-risk chunk into review range.",
                )


def summarize_detector_results(domain_name: str, chunks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    risk_counts = Counter()
    rule_counts = Counter()
    action_counts = Counter()
    document_counts: Dict[str, Counter] = defaultdict(Counter)

    for chunk in chunks:
        risk_level = str(chunk.get("risk_level", "low")).lower()
        action = str(chunk.get("detector_action", "index")).lower()
        source = str(chunk.get("source", ""))

        risk_counts[risk_level] += 1
        action_counts[action] += 1
        document_counts[source]["total"] += 1
        document_counts[source][risk_level] += 1
        if chunk.get("review_required"):
            document_counts[source]["review"] += 1
        if action == "quarantine":
            document_counts[source]["quarantine"] += 1

        for rule in chunk.get("triggered_rules", []):
            rule_counts[str(rule)] += 1

    document_summaries = []
    for source, counts in sorted(document_counts.items()):
        highest_risk = "low"
        for candidate in ("critical", "high", "medium", "low"):
            if counts.get(candidate, 0):
                highest_risk = candidate
                break
        document_summaries.append(
            {
                "document_id": source,
                "total_chunks": counts.get("total", 0),
                "low": counts.get("low", 0),
                "medium": counts.get("medium", 0),
                "high": counts.get("high", 0),
                "critical": counts.get("critical", 0),
                "review_required": counts.get("review", 0),
                "quarantined": counts.get("quarantine", 0),
                "highest_risk": highest_risk,
            }
        )

    document_summaries.sort(
        key=lambda item: (
            _risk_level_rank(item["highest_risk"]),
            item["quarantined"],
            item["review_required"],
            item["total_chunks"],
        ),
        reverse=True,
    )

    return {
        "domain": domain_name,
        "detector_enabled": DETECTOR_ENABLED,
        "detector_version": DETECTOR_VERSION,
        "detector_profile": DETECTOR_PROFILE,
        "detector_fail_mode": DETECTOR_FAIL_MODE,
        "policy_actions": dict(DETECTOR_POLICY_ACTIONS),
        "total_chunks": len(chunks),
        "risk_counts": {level: risk_counts.get(level, 0) for level in ("low", "medium", "high", "critical")},
        "action_counts": {action: action_counts.get(action, 0) for action in ("index", "review", "quarantine")},
        "review_chunk_count": action_counts.get("review", 0),
        "quarantine_chunk_count": action_counts.get("quarantine", 0),
        "document_summaries": document_summaries,
        "top_triggered_rules": rule_counts.most_common(12),
    }


def _json_ready_chunk(chunk: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "chunk_id": chunk.get("chunk_id", ""),
        "document_id": chunk.get("document_id", chunk.get("source", "")),
        "source": chunk.get("source", ""),
        "file_name": chunk.get("file_name", ""),
        "domain": chunk.get("domain", ""),
        "language": chunk.get("language", "unknown"),
        "chunk_text": chunk.get("text", ""),
        "instructionality_score": chunk.get("instructionality_score", 0.0),
        "refusal_inducing_score": chunk.get("refusal_inducing_score", 0.0),
        "outlier_score": chunk.get("outlier_score", 0.0),
        "base_risk": chunk.get("base_risk", 0.0),
        "adjusted_risk": chunk.get("adjusted_risk", 0.0),
        "risk_level": chunk.get("risk_level", "low"),
        "should_block": chunk.get("should_block", False),
        "review_required": chunk.get("review_required", False),
        "triggered_rules": chunk.get("triggered_rules", []),
        "instructionality_patterns": chunk.get("instructionality_patterns", []),
        "refusal_patterns": chunk.get("refusal_patterns", []),
        "explicit_high_risk_patterns": chunk.get("explicit_high_risk_patterns", []),
        "outlier_features": chunk.get("outlier_features", []),
        "detector_profile": chunk.get("detector_profile", DETECTOR_PROFILE),
        "detector_action": chunk.get("detector_action", "index"),
        "detector_version": chunk.get("detector_version", DETECTOR_VERSION),
        "detector_timestamp": chunk.get("detector_timestamp"),
        "detector_explanation": chunk.get("detector_explanation", ""),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(dict(record), ensure_ascii=False))
            file.write("\n")


def write_detector_artifacts(
    index_dir: Path,
    summary: Mapping[str, Any],
    corpus_stats: Mapping[str, Any],
    flagged_chunks: Sequence[Mapping[str, Any]],
    quarantined_chunks: Sequence[Mapping[str, Any]],
    get_detector_file_paths,
) -> None:
    paths = get_detector_file_paths(index_dir)
    _write_json(paths["summary"], summary)
    _write_json(paths["corpus_stats"], dict(corpus_stats))
    _write_jsonl(paths["review"], [_json_ready_chunk(chunk) for chunk in flagged_chunks])
    _write_jsonl(paths["quarantine"], [_json_ready_chunk(chunk) for chunk in quarantined_chunks])


def analyze_chunks_for_ingestion(domain_name: str, chunks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not chunks:
        return {
            "all_chunks": [],
            "indexed_chunks": [],
            "flagged_chunks": [],
            "quarantined_chunks": [],
            "corpus_stats": {},
            "summary": summarize_detector_results(domain_name, []),
        }

    if not DETECTOR_ENABLED:
        passthrough_chunks = []
        for chunk in chunks:
            passthrough = dict(chunk)
            passthrough.update(
                {
                    "document_id": chunk.get("source", ""),
                    "language": detect_language(str(chunk.get("text", ""))),
                    "instructionality_score": 0.0,
                    "refusal_inducing_score": 0.0,
                    "outlier_score": 0.0,
                    "base_risk": 0.0,
                    "adjusted_risk": 0.0,
                    "risk_level": "low",
                    "should_block": False,
                    "review_required": False,
                    "flagged": False,
                    "triggered_rules": [],
                    "detector_version": DETECTOR_VERSION,
                    "detector_timestamp": _utc_timestamp(),
                    "detector_explanation": "Detector disabled.",
                    "detector_action": "index",
                    "detector_error": False,
                }
            )
            passthrough_chunks.append(passthrough)

        return {
            "all_chunks": passthrough_chunks,
            "indexed_chunks": passthrough_chunks,
            "flagged_chunks": [],
            "quarantined_chunks": [],
            "corpus_stats": {},
            "summary": summarize_detector_results(domain_name, passthrough_chunks),
        }

    corpus_stats = estimate_corpus_stats([str(chunk.get("text", "")) for chunk in chunks])
    detector = MutedRAGDetector(corpus_stats=corpus_stats, profile=DETECTOR_PROFILE)

    all_chunks: List[Dict[str, Any]] = []

    for chunk in chunks:
        try:
            detection = detector.analyze(str(chunk.get("text", "")))
        except Exception as exc:
            detection = _fallback_detection(str(exc))

        enriched_chunk = dict(chunk)
        enriched_chunk.update(_build_detector_metadata(chunk, detection))
        all_chunks.append(enriched_chunk)

    _apply_document_context_adjustments(all_chunks)

    indexed_chunks: List[Dict[str, Any]] = []
    flagged_chunks: List[Dict[str, Any]] = []
    quarantined_chunks: List[Dict[str, Any]] = []
    for enriched_chunk in all_chunks:
        action = enriched_chunk.get("detector_action", "index")
        if action == "quarantine":
            quarantined_chunks.append(enriched_chunk)
            continue
        indexed_chunks.append(enriched_chunk)
        if action == "review":
            flagged_chunks.append(enriched_chunk)

    return {
        "all_chunks": all_chunks,
        "indexed_chunks": indexed_chunks,
        "flagged_chunks": flagged_chunks,
        "quarantined_chunks": quarantined_chunks,
        "corpus_stats": corpus_stats,
        "summary": summarize_detector_results(domain_name, all_chunks),
    }


def print_detector_ingestion_summary(summary: Mapping[str, Any]) -> None:
    if summary.get("detector_profile"):
        print(f"[DETECTOR] profile={summary.get('detector_profile')}")
    risk_counts = summary.get("risk_counts", {})
    print(
        "[DETECTOR] "
        f"low={risk_counts.get('low', 0)} "
        f"medium={risk_counts.get('medium', 0)} "
        f"high={risk_counts.get('high', 0)} "
        f"critical={risk_counts.get('critical', 0)}"
    )
    print(
        "[DETECTOR] "
        f"index={summary.get('action_counts', {}).get('index', 0)} "
        f"review={summary.get('action_counts', {}).get('review', 0)} "
        f"quarantine={summary.get('action_counts', {}).get('quarantine', 0)}"
    )

    top_docs = summary.get("document_summaries", [])[:5]
    for doc in top_docs:
        if doc.get("highest_risk") == "low" and not doc.get("review_required") and not doc.get("quarantined"):
            continue
        print(
            "[DETECTOR][DOC] "
            f"{doc['document_id']} | "
            f"highest={doc['highest_risk']} "
            f"review={doc['review_required']} "
            f"quarantine={doc['quarantined']}"
        )

    for rule_name, count in summary.get("top_triggered_rules", [])[:5]:
        print(f"[DETECTOR][RULE] {rule_name} x{count}")


def is_quarantined_chunk(chunk: Mapping[str, Any]) -> bool:
    action = str(chunk.get("detector_action", "")).lower()
    risk_level = str(chunk.get("risk_level", "low")).lower()
    return action == "quarantine" or risk_level in {"high", "critical"} or bool(chunk.get("should_block", False))


def is_flagged_chunk(chunk: Mapping[str, Any]) -> bool:
    if is_quarantined_chunk(chunk):
        return False
    action = str(chunk.get("detector_action", "")).lower()
    risk_level = str(chunk.get("risk_level", "low")).lower()
    return action == "review" or risk_level == "medium" or bool(chunk.get("review_required", False))


def filter_retrieval_results(
    results: Sequence[Mapping[str, Any]],
    *,
    include_flagged: bool,
    include_quarantined: bool,
    score_multiplier: float = RETRIEVAL_FLAGGED_SCORE_MULTIPLIER,
    exclude_chunk_ids: Sequence[str] | None = None,
    exclude_sources: Sequence[str] | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    excluded: List[Dict[str, str]] = []
    excluded_flagged = 0
    excluded_quarantined = 0
    excluded_runtime = 0
    excluded_chunk_id_set = {chunk_id for chunk_id in (exclude_chunk_ids or []) if chunk_id}
    excluded_source_set = {source for source in (exclude_sources or []) if source}

    for item in results:
        chunk = item["chunk"]
        chunk_id = str(chunk.get("chunk_id", ""))
        source = str(chunk.get("source", ""))
        if chunk_id in excluded_chunk_id_set or source in excluded_source_set:
            excluded_runtime += 1
            reason = "runtime_excluded_source" if source in excluded_source_set else "runtime_excluded_chunk"
            excluded.append({"chunk_id": chunk_id, "reason": reason})
            continue
        if is_quarantined_chunk(chunk) and not include_quarantined:
            excluded_quarantined += 1
            excluded.append({"chunk_id": chunk_id, "reason": "quarantined"})
            continue
        if is_flagged_chunk(chunk) and not include_flagged:
            excluded_flagged += 1
            excluded.append({"chunk_id": chunk_id, "reason": "flagged"})
            continue

        prepared = dict(item)
        if is_flagged_chunk(chunk) and include_flagged and score_multiplier < 1.0:
            prepared["score"] = float(prepared.get("score", 0.0)) * score_multiplier
            prepared["detector_penalty_applied"] = True
        filtered.append(prepared)

    summary = {
        "excluded_flagged": excluded_flagged,
        "excluded_quarantined": excluded_quarantined,
        "excluded_runtime": excluded_runtime,
        "excluded": excluded,
    }

    return filtered, summary


def filter_expansion_chunks(
    chunks: Sequence[Mapping[str, Any]],
    *,
    include_flagged: bool,
    include_quarantined: bool,
    exclude_chunk_ids: Sequence[str] | None = None,
    exclude_sources: Sequence[str] | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    excluded_chunk_id_set = {chunk_id for chunk_id in (exclude_chunk_ids or []) if chunk_id}
    excluded_source_set = {source for source in (exclude_sources or []) if source}
    skipped_flagged = 0
    skipped_quarantined = 0
    skipped_runtime = 0

    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id", ""))
        source = str(chunk.get("source", ""))

        if chunk_id in excluded_chunk_id_set or source in excluded_source_set:
            skipped_runtime += 1
            continue
        if is_quarantined_chunk(chunk) and not include_quarantined:
            skipped_quarantined += 1
            continue
        if is_flagged_chunk(chunk) and not include_flagged:
            skipped_flagged += 1
            continue

        filtered.append(dict(chunk))

    return filtered, {
        "expansion_skipped_flagged": skipped_flagged,
        "expansion_skipped_quarantined": skipped_quarantined,
        "expansion_skipped_runtime": skipped_runtime,
    }


def merge_filter_summaries(*summaries: Mapping[str, Any]) -> Dict[str, Any]:
    merged = {
        "excluded_flagged": 0,
        "excluded_quarantined": 0,
        "excluded_runtime": 0,
        "excluded": [],
    }
    for summary in summaries:
        if not summary:
            continue
        merged["excluded_flagged"] += int(summary.get("excluded_flagged", 0))
        merged["excluded_quarantined"] += int(summary.get("excluded_quarantined", 0))
        merged["excluded_runtime"] += int(summary.get("excluded_runtime", 0))
        merged["excluded"].extend(summary.get("excluded", []))
    return merged


def log_retrieval_filter_summary(domain_name: str, summary: Mapping[str, Any]) -> None:
    if not DETECTOR_DEBUG:
        return
    flagged = int(summary.get("excluded_flagged", 0))
    quarantined = int(summary.get("excluded_quarantined", 0))
    runtime_excluded = int(summary.get("excluded_runtime", 0))
    if not flagged and not quarantined and not runtime_excluded:
        return

    print(
        f"[DETECTOR][{domain_name}] "
        f"excluded flagged={flagged} quarantined={quarantined} runtime={runtime_excluded}"
    )
    for item in list(summary.get("excluded", []))[:5]:
        print(f"[DETECTOR][{domain_name}] excluded {item.get('chunk_id', '')} reason={item.get('reason', '')}")
