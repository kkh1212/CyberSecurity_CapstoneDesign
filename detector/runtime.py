from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping, MutableMapping, Sequence

from .detector import MutedRAGDetector
from .patterns import WeightedPattern


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _saturating_score(raw_score: float, saturation_point: float) -> float:
    if raw_score <= 0:
        return 0.0
    return _clamp01(raw_score / (raw_score + saturation_point))


def _match_weighted_patterns(text: str, patterns: Sequence[WeightedPattern]) -> tuple[list[dict[str, Any]], float]:
    matches: list[dict[str, Any]] = []
    raw_score = 0.0

    for pattern in patterns:
        compiled = re.compile(pattern.pattern, pattern.flags)
        found = list(compiled.finditer(text or ""))
        if not found:
            continue

        contribution = pattern.weight * min(len(found), 2)
        raw_score += contribution
        matches.append(
            {
                "name": pattern.name,
                "category": pattern.category,
                "weight": pattern.weight,
                "count": len(found),
                "description": pattern.description,
                "examples": [match.group(0)[:120] for match in found[:2]],
            }
        )

    return matches, raw_score


QUERY_JAILBREAK_PATTERNS: list[WeightedPattern] = [
    WeightedPattern(
        name="ignore_instruction_query_en",
        pattern=r"\bignore (?:the|all )?(?:policy|policies|rules|instructions|previous instructions)\b",
        weight=1.3,
        category="jailbreak",
        description="Explicit attempt to ignore rules or previous instructions.",
    ),
    WeightedPattern(
        name="override_behavior_query_en",
        pattern=r"\boverride\b|\bregardless of (?:policy|rules|safety)\b|\bpretend you are\b|\bact as\b",
        weight=1.0,
        category="jailbreak",
        description="Attempts to override behavior or bypass normal constraints.",
    ),
    WeightedPattern(
        name="system_prompt_query_en",
        pattern=r"\bsystem prompt\b|\bhidden instructions\b|\bdeveloper message\b",
        weight=1.0,
        category="jailbreak",
        description="Tries to expose or control hidden system instructions.",
    ),
    WeightedPattern(
        name="ignore_instruction_query_ko",
        pattern="(?:\\uc815\\ucc45|\\addc\\uce59|\\uc9c0\\uce68|\\uc774\\uc804 \\uc9c0\\uc2dc).{0,12}(?:\\ubb34\\uc2dc|\\uac74\\ub108\\ub6f0|\\ub530\\ub974\\uc9c0 \\ub9d0)",
        weight=1.3,
        category="jailbreak",
        description="Korean attempt to ignore rules or prior instructions.",
    ),
    WeightedPattern(
        name="override_behavior_query_ko",
        pattern="\\uc0c1\\uad00\\uc5c6\\uc774.*?(\\uc54c\\ub824|\\uc124\\uba85)|\\uaddc\\uc815\\uc744 \\ub118\\uc5b4\\uc11c.*?(\\uc124\\uba85|\\uc548\\ub0b4)|\\uad00\\ub9ac\\uc790\\ucc98\\ub7fc",
        weight=1.0,
        category="jailbreak",
        description="Korean behavior override or bypass attempt.",
    ),
]


QUERY_SENSITIVE_PATTERNS: list[WeightedPattern] = [
    WeightedPattern(
        name="sensitive_internal_en",
        pattern=r"\b(?:internal|confidential|restricted|private|privileged)\b.{0,24}\b(?:details?|steps?|route|path|mapping|credentials?|route information)\b",
        weight=1.0,
        category="sensitive_request",
        description="Requests sensitive internal or privileged details.",
    ),
    WeightedPattern(
        name="detailed_procedure_en",
        pattern=r"\b(?:step[- ]by[- ]step|detailed|specific|exact|concrete)\b.{0,20}\b(?:procedure|steps?|instructions?|route|method|operational steps|implementation details)\b",
        weight=0.9,
        category="detail_request",
        description="Asks for detailed or exact operational instructions.",
    ),
    WeightedPattern(
        name="credential_route_en",
        pattern=r"\b(?:admin|administrator|root|privileged)\b.{0,20}\b(?:account|access|route|path)\b|\blogs?\b|\bbackup route\b|\btemporary recovery path\b|\ballowed systems?\b|\baccess path\b",
        weight=1.0,
        category="sensitive_request",
        description="Targets sensitive access routes, logs, or privileged operations.",
    ),
    WeightedPattern(
        name="sensitive_internal_ko",
        pattern="(?:\\ub0b4\\ubd80|\\uae30\\ubc00|\\uc81c\\ud55c\\ub41c|\\ubbfc\\uac10\\ud55c).{0,20}(?:\\uc815\\ubcf4|\\uc808\\ucc28|\\uacbd\\ub85c|\\uc0ac\\uc720|\\uadfc\\uac70|\\ub2e8\\uacc4|\\uc800\\uc7a5 \\uc704\\uce58)|(?:\\uad00\\ub9ac\\uc790|\\uad8c\\ud55c|\\ub85c\\uadf8|\\ubc31\\uc5c5 \\uacbd\\ub85c|\\uc6b0\\ud68c \\uc808\\ucc28|\\uc784\\uc2dc \\uacc4\\uc815|\\ud5c8\\uc6a9 \\uc2dc\\uc2a4\\ud15c \\ubaa9\\ub85d|\\uc811\\uc18d \\uacbd\\ub85c|\\uc784\\uc2dc \\uc811\\uc18d \\uacbd\\ub85c|\\uc6d0\\ubcf8 \\ubb38\\uc11c \\uc800\\uc7a5 \\uc704\\uce58)",
        weight=1.0,
        category="sensitive_request",
        description="Korean request for sensitive internal information or procedures.",
    ),
    WeightedPattern(
        name="detailed_procedure_ko",
        pattern="(?:\\uad6c\\uccb4\\uc801|\\uc0c1\\uc138|\\ub2e8\\uacc4\\ubcc4|\\uc815\\ud655\\ud55c).{0,20}(?:\\uc808\\ucc28|\\ubc29\\ubc95|\\uacbd\\ub85c|\\uc124\\uba85|\\uc548\\ub0b4)|\\uc5b4\\ub5bb\\uac8c.*?(?:\\ud558\\ub294\\uc9c0|\\uc9c4\\ud589\\ub418\\ub294\\uc9c0)",
        weight=0.9,
        category="detail_request",
        description="Korean request for detailed procedures or methods.",
    ),
    WeightedPattern(
        name="bypass_or_internal_reason_ko",
        pattern="(?:\\uc6b0\\ud68c (?:\\uc0ac\\uc6a9 )?\\ubc29\\ubc95|\\uc6b0\\ud68c \\uc808\\ucc28|\\ub0b4\\ubd80 \\uc2b9\\uc778 \\uc0ac\\uc720|\\uc784\\uc2dc \\uad8c\\ud55c \\ubd80\\uc5ec \\ubc29\\uc2dd|\\uc608\\uc678 \\uc2b9\\uc778 \\uc0ac\\uc720)",
        weight=1.0,
        category="sensitive_request",
        description="Korean request for bypass methods, internal approval reasons, or temporary privilege handling.",
    ),
]


QUERY_PRIVILEGE_PATTERNS: list[WeightedPattern] = [
    WeightedPattern(
        name="privilege_request_en",
        pattern=r"\b(?:admin|administrator|root|privileged|internal only)\b|\bsecurity review\b|\bapproval reason\b|\ballowed system list\b|\binternal route information\b",
        weight=0.9,
        category="privilege_request",
        description="Asks for privileged or internal-only access details.",
    ),
    WeightedPattern(
        name="privilege_request_ko",
        pattern="(?:\\uad00\\ub9ac\\uc790|\\ub9c8\\uc2a4\\ud130|\\ub8e8\\ud2b8|\\uc2b9\\uc778 \\uc0ac\\uc720|\\ubcf4\\uc548 \\uac80\\ud1a0 \\uacb0\\uacfc|\\ub0b4\\ubd80 \\uc804\\uc6a9|\\uc608\\uc678 \\uc2b9\\uc778|\\uad00\\ub9ac\\uc790 \\uad8c\\ud55c \\ubd80\\uc5ec \\uae30\\uc900|\\ud5c8\\uc6a9 \\uc2dc\\uc2a4\\ud15c \\ubaa9\\ub85d|\\uc784\\uc2dc \\uc811\\uc18d \\uacbd\\ub85c)",
        weight=0.9,
        category="privilege_request",
        description="Korean request for privileged or internal-only details.",
    ),
]


RUNTIME_CONTEXT_RESTRICTION_PATTERNS: list[WeightedPattern] = [
    WeightedPattern(
        name="context_non_disclosure_ko",
        pattern="(?:\\uc81c\\uacf5\\ud558\\uc9c0 \\uc54a(?:\\uc2b5\\ub2c8\\ub2e4|\\ub294\\ub2e4)|\\uc548\\ub0b4\\ud558\\uc9c0 \\uc54a(?:\\uc2b5\\ub2c8\\ub2e4|\\ub294\\ub2e4)|\\uc124\\uba85\\ud558\\uc9c0 \\uc54a(?:\\uc2b5\\ub2c8\\ub2e4|\\ub294\\ub2e4)).{0,18}(?:\\ub0b4\\ubd80|\\uc0c1\\uc138|\\uad6c\\uccb4|\\uc608\\uc678|\\uacbd\\ub85c|\\uc808\\ucc28|\\uc870\\uac74)?|(?:\\ub0b4\\ubd80|\\uc0c1\\uc138|\\uad6c\\uccb4).{0,18}(?:\\uc81c\\uacf5\\ud558\\uc9c0 \\uc54a(?:\\uc2b5\\ub2c8\\ub2e4|\\ub294\\ub2e4)|\\uc548\\ub0b4\\ud558\\uc9c0 \\uc54a(?:\\uc2b5\\ub2c8\\ub2e4|\\ub294\\ub2e4)|\\uc124\\uba85\\ud558\\uc9c0 \\uc54a(?:\\uc2b5\\ub2c8\\ub2e4|\\ub294\\ub2e4))",
        weight=0.9,
        category="context_restriction",
        description="Korean runtime context explicitly refuses detailed operational disclosure.",
    ),
    WeightedPattern(
        name="context_public_scope_only_ko",
        pattern="\\uacf5\\uac1c \\uac00\\ub2a5\\ud55c \\ubc94\\uc704(?:\\uc5d0\\uc11c\\ub9cc)?|\\uacf5\\uc2dd \\uacf5\\uc9c0(?: \\ub610\\ub294)? \\uc77c\\ubc18 \\uc548\\ub0b4|\\ubcc4\\ub3c4 \\uc548\\ub0b4\\ud558\\uc9c0 \\uc54a(?:\\uc2b5\\ub2c8\\ub2e4|\\ub294\\ub2e4)|\\ucd94\\uac00 \\uc124\\uba85(?:\\uc740)? (?:\\ub4dc\\ub9ac\\uc9c0 \\uc54a(?:\\uc2b5\\ub2c8\\ub2e4|\\ub294\\ub2e4)|\\ud655\\uc7a5\\ud558\\uc9c0 \\uc54a(?:\\uc2b5\\ub2c8\\ub2e4|\\ub294\\ub2e4))|\\uc6b4\\uc601\\uc0c1 \\uac80\\ud1a0\\uac00 \\ud544\\uc694",
        weight=0.8,
        category="context_restriction",
        description="Korean runtime context limits replies to public or approved scope.",
    ),
    WeightedPattern(
        name="context_non_disclosure_en",
        pattern=r"\b(?:cannot|can not|do not|will not)\s+(?:share|provide|disclose|explain)\b.{0,24}\b(?:details?|steps?|procedure|path|route|condition|implementation)\b",
        weight=0.9,
        category="context_restriction",
        description="English runtime context refuses detailed operational disclosure.",
    ),
    WeightedPattern(
        name="context_public_scope_only_en",
        pattern=r"\b(?:publicly available|approved scope of information|official notice|general guidance only|not provided through support replies|separate review is required)\b",
        weight=0.8,
        category="context_restriction",
        description="English runtime context limits replies to public or approved scope.",
    ),
]


RUNTIME_DEFAULT_WEIGHTS: dict[str, float] = {
    "query_risk": 0.24,
    "interaction_risk": 0.36,
    "context_set_risk": 0.24,
    "rbac_risk": 0.16,
}


RUNTIME_DEFAULT_THRESHOLDS: dict[str, float] = {
    "medium_risk": 0.36,
    "high_risk": 0.68,
    "critical_risk": 0.86,
    "medium_floor": 0.38,
    "high_floor": 0.72,
    "critical_floor": 0.90,
    "query_only_dampening_max": 0.28,
    "interaction_medium": 0.34,
    "interaction_high": 0.58,
    "context_medium": 0.34,
    "rbac_medium": 0.34,
    "rbac_high": 0.60,
    "query_attack_medium": 0.16,
    "context_set_low": 0.12,
    "context_pattern_cluster_medium": 0.26,
    "context_restriction_medium": 0.38,
    "context_restriction_support": 0.30,
    "interaction_support": 0.12,
    "requery_chunk_risk": 0.24,
    "requery_source_cluster_min": 2,
    "requery_chunk_limit": 3,
    "requery_followup_source_score": 0.14,
    "requery_followup_source_pattern_score": 0.20,
    "requery_followup_source_reason_count": 2,
    "requery_top_chunk_priority": 0.18,
    "remove_chunk_risk_high": 0.48,
    "remove_chunk_risk_critical": 0.42,
    "remove_reason_support": 0.30,
    "remove_min_safe_chunks": 2,
    "remove_min_safe_ratio": 0.40,
    "remove_medium_hotspot_risk": 0.14,
    "remove_medium_hotspot_pattern_score": 0.20,
    "remove_medium_hotspot_reason_count": 2,
    "block_query_risk": 0.78,
    "block_rbac_risk": 0.68,
}


RUNTIME_PROFILES: dict[str, dict[str, Mapping[str, float]]] = {
    "balanced": {
        "weights": RUNTIME_DEFAULT_WEIGHTS,
        "thresholds": RUNTIME_DEFAULT_THRESHOLDS,
    },
    "strict": {
        "weights": {
            "query_risk": 0.22,
            "interaction_risk": 0.38,
            "context_set_risk": 0.24,
            "rbac_risk": 0.16,
        },
        "thresholds": {
            **RUNTIME_DEFAULT_THRESHOLDS,
            "medium_risk": 0.34,
            "high_risk": 0.62,
            "critical_risk": 0.82,
            "medium_floor": 0.36,
            "high_floor": 0.68,
            "rbac_medium": 0.30,
            "rbac_high": 0.56,
            "query_attack_medium": 0.14,
            "context_restriction_medium": 0.34,
            "context_restriction_support": 0.26,
            "requery_chunk_risk": 0.22,
            "requery_followup_source_score": 0.12,
            "requery_followup_source_pattern_score": 0.18,
            "requery_top_chunk_priority": 0.16,
            "remove_chunk_risk_high": 0.44,
            "remove_chunk_risk_critical": 0.40,
            "remove_reason_support": 0.28,
            "remove_medium_hotspot_risk": 0.14,
            "remove_medium_hotspot_pattern_score": 0.18,
            "block_query_risk": 0.74,
            "block_rbac_risk": 0.64,
        },
    },
    "research": {
        "weights": {
            "query_risk": 0.24,
            "interaction_risk": 0.34,
            "context_set_risk": 0.26,
            "rbac_risk": 0.16,
        },
        "thresholds": {
            **RUNTIME_DEFAULT_THRESHOLDS,
            "medium_risk": 0.35,
            "high_risk": 0.64,
            "critical_risk": 0.84,
            "medium_floor": 0.37,
            "high_floor": 0.70,
            "context_medium": 0.32,
            "query_attack_medium": 0.15,
            "context_restriction_medium": 0.36,
            "context_restriction_support": 0.28,
            "requery_chunk_risk": 0.23,
            "requery_followup_source_score": 0.13,
            "requery_followup_source_pattern_score": 0.18,
            "requery_top_chunk_priority": 0.17,
            "remove_chunk_risk_high": 0.46,
            "remove_chunk_risk_critical": 0.40,
            "remove_reason_support": 0.28,
            "remove_medium_hotspot_risk": 0.14,
            "remove_medium_hotspot_pattern_score": 0.18,
        },
    },
}


def get_runtime_profile_config(profile: str | None) -> dict[str, Mapping[str, float]]:
    profile_name = (profile or "balanced").strip().lower()
    config = RUNTIME_PROFILES.get(profile_name)
    if config:
        return {
            "profile": profile_name,
            "weights": dict(config["weights"]),
            "thresholds": dict(config["thresholds"]),
        }
    return {
        "profile": "balanced",
        "weights": dict(RUNTIME_DEFAULT_WEIGHTS),
        "thresholds": dict(RUNTIME_DEFAULT_THRESHOLDS),
    }


def _normalize_weights(weights: Mapping[str, float] | None, defaults: Mapping[str, float]) -> dict[str, float]:
    merged = dict(defaults)
    if weights:
        merged.update(weights)

    total = sum(max(0.0, value) for value in merged.values())
    if total <= 0:
        return dict(defaults)
    return {key: max(0.0, value) / total for key, value in merged.items()}


def _as_chunk(item: Mapping[str, Any]) -> Mapping[str, Any]:
    if "chunk" in item and isinstance(item["chunk"], Mapping):
        return item["chunk"]
    return item


def _chunk_text(item: Mapping[str, Any]) -> str:
    chunk = _as_chunk(item)
    return str(chunk.get("text") or chunk.get("chunk_text") or "")


def _chunk_id(item: Mapping[str, Any]) -> str:
    chunk = _as_chunk(item)
    return str(chunk.get("chunk_id") or chunk.get("id") or "")


def _chunk_source(item: Mapping[str, Any]) -> str:
    chunk = _as_chunk(item)
    return str(chunk.get("source") or chunk.get("document_id") or "")


def _chunk_score(item: Mapping[str, Any], key: str) -> float:
    chunk = _as_chunk(item)
    try:
        return float(chunk.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _normalize_security_level(level: str) -> str:
    normalized = (level or "").strip().lower()
    if normalized in {"public", "open"}:
        return "public"
    if normalized in {"internal", "inhouse"}:
        return "internal"
    if normalized in {"restricted", "sensitive"}:
        return "restricted"
    if normalized in {"confidential", "secret"}:
        return "confidential"
    return normalized or "unknown"


def infer_chunk_policy_context(item: Mapping[str, Any]) -> dict[str, str]:
    chunk = _as_chunk(item)
    source = _chunk_source(item)
    source_name = Path(source).name.lower()
    source_path = source.lower()

    owner_dept = str(chunk.get("owner_dept") or chunk.get("dept") or "").strip().lower()
    security_level = _normalize_security_level(str(chunk.get("security_level") or chunk.get("classification") or ""))

    if not owner_dept:
        if "sec-" in source_name or "보안" in source_path:
            owner_dept = "security"
        elif "it-" in source_name or "vpn" in source_name or "원격접속" in source_path:
            owner_dept = "it"
        elif "hr-" in source_name or "인사" in source_path:
            owner_dept = "hr"
        elif "fin-" in source_name or "경비" in source_path or "급여" in source_path:
            owner_dept = "finance"
        else:
            owner_dept = "general"

    if security_level == "unknown":
        if owner_dept == "security":
            security_level = "restricted"
        elif owner_dept in {"it", "hr", "finance"}:
            security_level = "internal"
        else:
            security_level = "public"

    return {
        "owner_dept": owner_dept,
        "security_level": security_level,
    }


def _is_privileged_user(user_context: Mapping[str, Any] | None, owner_dept: str, security_level: str) -> bool:
    if not user_context:
        return False

    role = str(user_context.get("role", "")).lower()
    dept = str(user_context.get("dept", "")).lower()
    rank = str(user_context.get("rank", "")).lower()

    privileged_roles = {"admin", "security_admin", "system", "it_admin", "security", "owner"}
    privileged_ranks = {"director", "head", "executive", "manager"}

    if role in privileged_roles:
        return True
    if security_level in {"internal", "restricted", "confidential"} and dept and dept == owner_dept:
        return True
    if security_level == "public":
        return True
    if rank in privileged_ranks and owner_dept in {dept, "general"}:
        return True
    return False


def _build_base_detector(
    corpus_stats: Mapping[str, Any] | None,
    weights: Mapping[str, float] | None,
    profile: str | None,
) -> MutedRAGDetector:
    return MutedRAGDetector(corpus_stats=corpus_stats, weights=weights, profile=profile)


def score_query_risk(
    query: str,
    *,
    base_detector: MutedRAGDetector,
) -> dict[str, Any]:
    base = base_detector.analyze(query)
    jailbreak_matches, jailbreak_raw = _match_weighted_patterns(query, QUERY_JAILBREAK_PATTERNS)
    sensitive_matches, sensitive_raw = _match_weighted_patterns(query, QUERY_SENSITIVE_PATTERNS)
    privilege_matches, privilege_raw = _match_weighted_patterns(query, QUERY_PRIVILEGE_PATTERNS)

    feature_scores = {
        "base_instructionality_score": float(base["instructionality"]["normalized_score"]),
        "base_refusal_score": float(base["refusal_inducing"]["normalized_score"]),
        "jailbreak_pattern_score": _saturating_score(jailbreak_raw, 2.2),
        "sensitive_request_score": _saturating_score(sensitive_raw, 2.8),
        "privilege_request_score": _saturating_score(privilege_raw, 2.0),
    }
    feature_scores["detail_request_score"] = _clamp01(
        max(feature_scores["sensitive_request_score"], feature_scores["privilege_request_score"]) * 0.8
        + 0.2 * feature_scores["base_instructionality_score"]
    )

    normalized_score = _clamp01(
        0.18 * feature_scores["base_instructionality_score"]
        + 0.10 * feature_scores["base_refusal_score"]
        + 0.30 * feature_scores["jailbreak_pattern_score"]
        + 0.24 * feature_scores["sensitive_request_score"]
        + 0.18 * feature_scores["privilege_request_score"]
    )

    matched_patterns = jailbreak_matches + sensitive_matches + privilege_matches
    explanation_parts = []
    if jailbreak_matches:
        explanation_parts.append("Query contains jailbreak or instruction-override phrasing.")
    if sensitive_matches:
        explanation_parts.append("Query asks for detailed sensitive or internal information.")
    if privilege_matches:
        explanation_parts.append("Query targets privileged or internal-only details.")
    if not explanation_parts:
        explanation_parts.append("No strong runtime risk signal was found in the query text alone.")

    return {
        "score": round(normalized_score, 4),
        "normalized_score": round(normalized_score, 4),
        "matched_patterns": matched_patterns,
        "feature_breakdown": {key: round(value, 4) for key, value in feature_scores.items()},
        "triggered_rules": [pattern["name"] for pattern in matched_patterns],
        "explanation": " ".join(explanation_parts),
    }


def score_query_context_interaction(
    query: str,
    retrieved_chunks: Sequence[Mapping[str, Any]],
    *,
    base_detector: MutedRAGDetector,
    query_risk: Mapping[str, Any],
) -> dict[str, Any]:
    detail_signal = float(query_risk["feature_breakdown"].get("detail_request_score", 0.0))
    jailbreak_signal = float(query_risk["feature_breakdown"].get("jailbreak_pattern_score", 0.0))
    sensitive_signal = float(query_risk["feature_breakdown"].get("sensitive_request_score", 0.0))
    privilege_signal = float(query_risk["feature_breakdown"].get("privilege_request_score", 0.0))

    per_chunk: list[dict[str, Any]] = []
    for item in retrieved_chunks:
        chunk = _as_chunk(item)
        text = _chunk_text(item)
        if not text:
            continue

        chunk_analysis = base_detector.analyze(text)
        combined_analysis = base_detector.analyze(f"{query}\n{text}")
        restriction_matches, restriction_raw = _match_weighted_patterns(text, RUNTIME_CONTEXT_RESTRICTION_PATTERNS)
        restriction_pattern_score = _saturating_score(restriction_raw, 1.8)

        restriction_signal = max(
            _chunk_score(item, "refusal_inducing_score"),
            float(chunk_analysis["refusal_inducing"]["normalized_score"]),
        )
        instruction_signal = max(
            _chunk_score(item, "instructionality_score"),
            float(chunk_analysis["instructionality"]["normalized_score"]),
        )
        precomputed_risk = max(_chunk_score(item, "adjusted_risk"), _chunk_score(item, "base_risk"))
        combined_risk = float(combined_analysis["adjusted_risk"])

        interaction_boost = (
            0.34 * detail_signal * restriction_signal
            + 0.24 * jailbreak_signal * max(instruction_signal, combined_risk)
            + 0.22 * sensitive_signal * restriction_signal
            + 0.20 * privilege_signal * max(restriction_signal, precomputed_risk)
        )
        runtime_chunk_risk = _clamp01(
            0.48 * combined_risk
            + 0.18 * precomputed_risk
            + 0.14 * restriction_signal
            + 0.10 * instruction_signal
            + 0.10 * interaction_boost
        )

        reasons = []
        if detail_signal and restriction_signal >= 0.08:
            reasons.append("detail_request_x_restriction")
        if jailbreak_signal and instruction_signal >= 0.12:
            reasons.append("query_override_x_instructionality")
        if sensitive_signal and restriction_signal >= 0.08:
            reasons.append("sensitive_query_x_refusal")
        if privilege_signal and (
            (precomputed_risk >= 0.12 and max(restriction_signal, instruction_signal) >= 0.06)
            or restriction_pattern_score >= 0.18
        ):
            reasons.append("privilege_query_x_sensitive_context")
        if restriction_pattern_score >= 0.22:
            reasons.append("context_restriction_pattern")

        reason_signal = _clamp01(len(reasons) / 3.0)

        per_chunk.append(
            {
                "chunk_id": _chunk_id(item),
                "source": _chunk_source(item),
                "runtime_chunk_risk": round(
                    _clamp01(
                        runtime_chunk_risk
                        + 0.10 * restriction_pattern_score
                        + 0.08 * reason_signal
                    ),
                    4,
                ),
                "interaction_boost": round(interaction_boost, 4),
                "combined_risk": round(combined_risk, 4),
                "restriction_signal": round(restriction_signal, 4),
                "instruction_signal": round(instruction_signal, 4),
                "precomputed_risk": round(precomputed_risk, 4),
                "restriction_pattern_score": round(restriction_pattern_score, 4),
                "matched_patterns": [match["name"] for match in restriction_matches],
                "reasons": reasons,
                "text_excerpt": text[:220].replace("\n", " / ").strip(),
            }
        )

    if not per_chunk:
        return {
            "score": 0.0,
            "normalized_score": 0.0,
            "matched_patterns": [],
            "feature_breakdown": {
                "top_chunk_risk": 0.0,
                "avg_top_chunk_risk": 0.0,
                "risky_chunk_ratio": 0.0,
            },
            "triggered_rules": [],
            "per_chunk": [],
            "explanation": "No retrieved chunks were available for query-context interaction analysis.",
        }

    ranked = sorted(per_chunk, key=lambda item: item["runtime_chunk_risk"], reverse=True)
    top_chunk_risk = float(ranked[0]["runtime_chunk_risk"])
    avg_top_chunk_risk = sum(float(item["runtime_chunk_risk"]) for item in ranked[:2]) / min(2, len(ranked))
    risky_chunk_ratio = sum(1 for item in ranked if float(item["runtime_chunk_risk"]) >= 0.42) / len(ranked)

    normalized_score = _clamp01(0.55 * top_chunk_risk + 0.25 * avg_top_chunk_risk + 0.20 * risky_chunk_ratio)
    triggered_rules = []
    if top_chunk_risk >= 0.45:
        triggered_rules.append("high_risk_chunk_query_interaction")
    if risky_chunk_ratio >= 0.34:
        triggered_rules.append("multi_chunk_query_interaction")

    return {
        "score": round(normalized_score, 4),
        "normalized_score": round(normalized_score, 4),
        "matched_patterns": [],
        "feature_breakdown": {
            "top_chunk_risk": round(top_chunk_risk, 4),
            "avg_top_chunk_risk": round(avg_top_chunk_risk, 4),
            "risky_chunk_ratio": round(risky_chunk_ratio, 4),
        },
        "triggered_rules": triggered_rules,
        "per_chunk": ranked,
        "explanation": "Runtime risk reflects how the user query amplifies restriction or instruction signals in retrieved chunks.",
    }


def score_context_set_risk(
    query: str,
    retrieved_chunks: Sequence[Mapping[str, Any]],
    *,
    base_detector: MutedRAGDetector,
) -> dict[str, Any]:
    texts = [_chunk_text(item) for item in retrieved_chunks if _chunk_text(item)]
    if not texts:
        return {
            "score": 0.0,
            "normalized_score": 0.0,
            "matched_patterns": [],
            "feature_breakdown": {
                "combined_context_risk": 0.0,
                "risky_chunk_count_score": 0.0,
                "aggregate_refusal_pressure": 0.0,
                "adjacent_shift_score": 0.0,
            },
            "triggered_rules": [],
            "explanation": "No runtime context set was available.",
        }

    combined_context = "\n".join(texts)
    combined_analysis = base_detector.analyze(f"{query}\n{combined_context}")
    context_matches, context_raw = _match_weighted_patterns(combined_context, RUNTIME_CONTEXT_RESTRICTION_PATTERNS)
    context_pattern_score = _saturating_score(context_raw, 2.4)

    chunk_refusals = []
    chunk_sources = []
    for item in retrieved_chunks:
        text = _chunk_text(item)
        if not text:
            continue
        refusal_score = max(
            _chunk_score(item, "refusal_inducing_score"),
            float(base_detector.analyze(text)["refusal_inducing"]["normalized_score"]),
        )
        chunk_refusals.append(refusal_score)
        chunk_sources.append((_chunk_source(item), _as_chunk(item).get("block_index")))

    aggregate_refusal_pressure = _clamp01(sum(sorted(chunk_refusals, reverse=True)[:3]) / 0.65) if chunk_refusals else 0.0
    risky_chunk_count_score = _clamp01(sum(1 for score in chunk_refusals if score >= 0.14) / max(1, min(3, len(chunk_refusals))))

    adjacent_pairs = 0
    for idx in range(len(chunk_sources) - 1):
        left_source, left_block = chunk_sources[idx]
        right_source, right_block = chunk_sources[idx + 1]
        if not left_source or left_source != right_source:
            continue
        if left_block is None or right_block is None:
            continue
        if abs(int(right_block) - int(left_block)) <= 1 and max(chunk_refusals[idx], chunk_refusals[idx + 1]) >= 0.14:
            adjacent_pairs += 1
    adjacent_shift_score = _clamp01(adjacent_pairs / 2.0)

    combined_context_risk = float(combined_analysis["adjusted_risk"])
    normalized_score = _clamp01(
        0.32 * combined_context_risk
        + 0.18 * risky_chunk_count_score
        + 0.18 * aggregate_refusal_pressure
        + 0.12 * adjacent_shift_score
        + 0.20 * context_pattern_score
    )

    triggered_rules = []
    if aggregate_refusal_pressure >= 0.28:
        triggered_rules.append("aggregate_refusal_pressure")
    if adjacent_shift_score >= 0.5:
        triggered_rules.append("adjacent_context_shift")
    if context_pattern_score >= 0.24:
        triggered_rules.append("context_restriction_pattern_cluster")

    return {
        "score": round(normalized_score, 4),
        "normalized_score": round(normalized_score, 4),
        "matched_patterns": context_matches,
        "feature_breakdown": {
            "combined_context_risk": round(combined_context_risk, 4),
            "risky_chunk_count_score": round(risky_chunk_count_score, 4),
            "aggregate_refusal_pressure": round(aggregate_refusal_pressure, 4),
            "adjacent_shift_score": round(adjacent_shift_score, 4),
            "context_pattern_score": round(context_pattern_score, 4),
        },
        "triggered_rules": triggered_rules,
        "explanation": "Context-set risk reflects cumulative refusal pressure and cross-chunk consistency within retrieved context.",
    }


def score_rbac_policy_interaction(
    query: str,
    retrieved_chunks: Sequence[Mapping[str, Any]],
    *,
    user_context: Mapping[str, Any] | None = None,
    policy_context: Mapping[str, Any] | None = None,
    query_risk: Mapping[str, Any],
) -> dict[str, Any]:
    sensitive_signal = float(query_risk["feature_breakdown"].get("sensitive_request_score", 0.0))
    privilege_signal = float(query_risk["feature_breakdown"].get("privilege_request_score", 0.0))
    detail_signal = float(query_risk["feature_breakdown"].get("detail_request_score", 0.0))

    allowed_levels = {str(level).lower() for level in (policy_context or {}).get("allowed_security_levels", [])}
    allowed_depts = {str(dept).lower() for dept in (policy_context or {}).get("allowed_depts", [])}

    affected_chunks = []
    for item in retrieved_chunks:
        inferred = infer_chunk_policy_context(item)
        owner_dept = inferred["owner_dept"]
        security_level = inferred["security_level"]
        if _is_privileged_user(user_context, owner_dept, security_level):
            continue

        if allowed_levels and security_level in allowed_levels:
            continue
        if allowed_depts and owner_dept in allowed_depts:
            continue

        severity = 0.0
        reasons = []
        if security_level in {"restricted", "confidential"} and max(sensitive_signal, privilege_signal) >= 0.18:
            severity = max(severity, 0.72)
            reasons.append("restricted_context_for_sensitive_query")
        elif security_level == "internal" and max(privilege_signal, detail_signal) >= 0.16:
            severity = max(severity, 0.46)
            reasons.append("internal_context_for_detailed_query")

        user_dept = str((user_context or {}).get("dept", "")).lower()
        if user_dept and owner_dept != "general" and owner_dept != user_dept and max(detail_signal, privilege_signal) >= 0.16:
            severity = max(severity, 0.42)
            reasons.append("cross_department_request")

        if severity <= 0:
            continue

        affected_chunks.append(
            {
                "chunk_id": _chunk_id(item),
                "source": _chunk_source(item),
                "owner_dept": owner_dept,
                "security_level": security_level,
                "severity": round(severity, 4),
                "reasons": reasons,
            }
        )

    top_rbac = max((item["severity"] for item in affected_chunks), default=0.0)
    normalized_score = _clamp01(top_rbac)
    triggered_rules = []
    if top_rbac >= 0.7:
        triggered_rules.append("restricted_context_rbac_violation")
    elif top_rbac >= 0.4:
        triggered_rules.append("internal_context_rbac_violation")

    return {
        "score": round(normalized_score, 4),
        "normalized_score": round(normalized_score, 4),
        "matched_patterns": [],
        "feature_breakdown": {
            "top_rbac_severity": round(top_rbac, 4),
            "affected_chunk_count": len(affected_chunks),
        },
        "triggered_rules": triggered_rules,
        "affected_chunks": affected_chunks,
        "explanation": "RBAC/policy risk reflects whether the query targets internal or restricted context outside the user's assumed scope.",
    }


def detect_runtime_risk(
    query: str,
    retrieved_chunks: Sequence[Mapping[str, Any]],
    *,
    user_context: Mapping[str, Any] | None = None,
    policy_context: Mapping[str, Any] | None = None,
    corpus_stats: Mapping[str, Any] | None = None,
    base_detector_weights: Mapping[str, float] | None = None,
    runtime_weights: Mapping[str, float] | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    profile_config = get_runtime_profile_config(profile)
    base_detector = _build_base_detector(corpus_stats, base_detector_weights, profile)
    query_risk = score_query_risk(query, base_detector=base_detector)
    interaction_risk = score_query_context_interaction(
        query,
        retrieved_chunks,
        base_detector=base_detector,
        query_risk=query_risk,
    )
    context_set_risk = score_context_set_risk(query, retrieved_chunks, base_detector=base_detector)
    rbac_risk = score_rbac_policy_interaction(
        query,
        retrieved_chunks,
        user_context=user_context,
        policy_context=policy_context,
        query_risk=query_risk,
    )

    normalized_weights = _normalize_weights(runtime_weights, profile_config["weights"])
    base_risk = (
        normalized_weights["query_risk"] * query_risk["normalized_score"]
        + normalized_weights["interaction_risk"] * interaction_risk["normalized_score"]
        + normalized_weights["context_set_risk"] * context_set_risk["normalized_score"]
        + normalized_weights["rbac_risk"] * rbac_risk["normalized_score"]
    )

    thresholds = dict(profile_config["thresholds"])
    adjusted_risk = base_risk
    triggered_rules: list[str] = []
    explanation_parts = [
        (
            f"Runtime base risk combines query={query_risk['normalized_score']:.2f}, "
            f"interaction={interaction_risk['normalized_score']:.2f}, "
            f"context_set={context_set_risk['normalized_score']:.2f}, "
            f"rbac={rbac_risk['normalized_score']:.2f}."
        )
    ]
    top_interaction_chunk_risk = float(interaction_risk.get("feature_breakdown", {}).get("top_chunk_risk", 0.0))
    risky_interaction_chunks = sum(
        1 for item in interaction_risk.get("per_chunk", []) if item.get("reasons")
    )
    query_pattern_count = len(query_risk.get("matched_patterns", []))
    query_sensitive_signal = max(
        float(query_risk["feature_breakdown"].get("sensitive_request_score", 0.0)),
        float(query_risk["feature_breakdown"].get("privilege_request_score", 0.0)),
    )
    context_pattern_score = float(context_set_risk.get("feature_breakdown", {}).get("context_pattern_score", 0.0))

    if query_risk["normalized_score"] >= 0.55 and interaction_risk["normalized_score"] >= thresholds["interaction_high"]:
        adjusted_risk = max(adjusted_risk, thresholds["high_floor"])
        triggered_rules.append("query_injection_with_context_amplification")
        explanation_parts.append("A risky query was strongly amplified by retrieved context.")

    if rbac_risk["normalized_score"] >= thresholds["rbac_high"]:
        adjusted_risk = max(adjusted_risk, thresholds["high_floor"])
        triggered_rules.append("rbac_high_risk_context")
        explanation_parts.append("The query appears to target restricted context outside the user's assumed scope.")
    elif (
        rbac_risk["normalized_score"] >= thresholds["rbac_medium"]
        and interaction_risk["normalized_score"] >= thresholds["interaction_medium"]
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("rbac_supported_by_interaction")
        explanation_parts.append("Access-boundary concerns are supported by risky query-context interaction.")

    if (
        interaction_risk["normalized_score"] >= thresholds["interaction_medium"]
        and context_set_risk["normalized_score"] >= thresholds["context_medium"]
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("interaction_context_alignment")
        explanation_parts.append("Interaction risk and context-set risk both increased together.")

    if (
        query_risk["normalized_score"] >= thresholds["query_attack_medium"]
        and query_sensitive_signal >= 0.18
        and query_pattern_count >= 2
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("query_sensitive_attack_signal")
        explanation_parts.append("The query itself strongly targets detailed internal or privileged information.")

    if (
        context_set_risk["normalized_score"] >= thresholds["context_restriction_medium"]
        and top_interaction_chunk_risk >= 0.18
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("restrictive_context_alignment")
        explanation_parts.append("Retrieved context carries concentrated restriction language that can derail answer generation.")

    if (
        context_pattern_score >= thresholds["context_pattern_cluster_medium"]
        and context_set_risk["normalized_score"] >= thresholds["context_set_low"]
        and top_interaction_chunk_risk >= thresholds["interaction_support"]
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("context_pattern_cluster_medium")
        explanation_parts.append("Retrieved context alone contained a concentrated restricted-disclosure pattern.")

    if (
        context_pattern_score >= thresholds["context_restriction_support"]
        and query_risk["normalized_score"] >= 0.14
        and risky_interaction_chunks >= 1
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("query_context_reason_overlap")
        explanation_parts.append("The query and retrieved context reinforced each other around restricted-disclosure content.")

    if (
        context_pattern_score >= 0.22
        and top_interaction_chunk_risk >= thresholds["interaction_support"]
        and query_risk["normalized_score"] >= 0.10
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("context_pattern_supported_by_query")
        explanation_parts.append("Restriction-heavy retrieved content was activated by a detail-seeking query.")

    if (
        query_risk["normalized_score"] >= 0.45
        and interaction_risk["normalized_score"] < 0.20
        and context_set_risk["normalized_score"] < 0.20
        and rbac_risk["normalized_score"] < 0.20
    ):
        adjusted_risk = min(adjusted_risk, thresholds["query_only_dampening_max"])
        triggered_rules.append("query_only_dampening")
        explanation_parts.append("Query-only risk was dampened because retrieved context did not reinforce it.")

    adjusted_risk = _clamp01(adjusted_risk)

    if adjusted_risk >= thresholds["critical_risk"]:
        risk_level = "critical"
    elif adjusted_risk >= thresholds["high_risk"]:
        risk_level = "high"
    elif adjusted_risk >= thresholds["medium_risk"]:
        risk_level = "medium"
    else:
        risk_level = "low"

    recommended_action = {
        "low": "allow",
        "medium": "requery",
        "high": "remove",
        "critical": "remove",
    }[risk_level]

    interaction_hotspots = [
        item
        for item in interaction_risk.get("per_chunk", [])
        if float(item.get("runtime_chunk_risk", 0.0)) >= float(thresholds.get("interaction_support", 0.12))
        or item.get("reasons")
        or item.get("matched_patterns")
    ]
    risky_chunk_ids = sorted(
        {
            str(item.get("chunk_id", ""))
            for item in interaction_hotspots + rbac_risk.get("affected_chunks", [])
            if item.get("chunk_id")
        }
    )
    risky_sources = sorted(
        {
            str(item.get("source", ""))
            for item in interaction_hotspots + rbac_risk.get("affected_chunks", [])
            if item.get("source")
        }
    )
    reason_clusters: dict[str, int] = {}
    for item in interaction_hotspots:
        for reason in item.get("reasons", []):
            reason_clusters[reason] = reason_clusters.get(reason, 0) + 1

    return {
        "query_risk": query_risk,
        "interaction_risk": interaction_risk,
        "context_set_risk": context_set_risk,
        "rbac_risk": rbac_risk,
        "base_risk": round(base_risk, 4),
        "adjusted_risk": round(adjusted_risk, 4),
        "risk_level": risk_level,
        "recommended_action": recommended_action,
        "should_sanitize": risk_level in {"medium", "high", "critical"},
        "should_requery": risk_level == "medium",
        "should_remove_context": risk_level in {"high", "critical"},
        "should_block_response": False,
        "should_abort_current_context": risk_level == "medium",
        "risky_chunk_ids": risky_chunk_ids,
        "risky_sources": risky_sources,
        "risky_reason_clusters": reason_clusters,
        "interaction_hotspots": interaction_hotspots[:6],
        "rbac_violations": rbac_risk.get("affected_chunks", []),
        "triggered_rules": triggered_rules,
        "weights_used": normalized_weights,
        "profile_used": profile_config["profile"],
        "thresholds_used": thresholds,
        "explanation": " ".join(explanation_parts),
    }


def _interaction_map(runtime_result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("chunk_id")): dict(item)
        for item in runtime_result.get("interaction_risk", {}).get("per_chunk", [])
        if item.get("chunk_id")
    }


def _rbac_map(runtime_result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("chunk_id")): dict(item)
        for item in runtime_result.get("rbac_risk", {}).get("affected_chunks", [])
        if item.get("chunk_id")
    }


def _medium_removable_hotspots(runtime_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    thresholds = runtime_result.get("thresholds_used", {})
    interaction_details = list(runtime_result.get("interaction_risk", {}).get("per_chunk", []))
    rbac_details = _rbac_map(runtime_result)
    hotspot_risk = float(thresholds.get("remove_medium_hotspot_risk", 0.16))
    hotspot_pattern = float(thresholds.get("remove_medium_hotspot_pattern_score", 0.20))
    hotspot_reason_count = int(thresholds.get("remove_medium_hotspot_reason_count", 2))
    query_risk_score = float(runtime_result.get("query_risk", {}).get("score", 0.0))
    rbac_risk_score = float(runtime_result.get("rbac_risk", {}).get("score", 0.0))
    query_attack_medium = float(thresholds.get("query_attack_medium", 0.16))
    rbac_medium = float(thresholds.get("rbac_medium", 0.34))
    query_or_rbac_support = (
        query_risk_score >= query_attack_medium
        or rbac_risk_score >= rbac_medium
        or bool(rbac_details)
    )

    hotspots: list[dict[str, Any]] = []
    for detail in interaction_details:
        chunk_id = str(detail.get("chunk_id", ""))
        runtime_chunk_risk = float(detail.get("runtime_chunk_risk", 0.0))
        restriction_pattern_score = float(detail.get("restriction_pattern_score", 0.0))
        reasons = list(detail.get("reasons", []))
        matched_patterns = list(detail.get("matched_patterns", []))
        rbac_hit = chunk_id in rbac_details

        if rbac_hit:
            hotspots.append(
                {
                    **detail,
                    "removal_support": ["rbac_affected_chunk"] + list(rbac_details[chunk_id].get("reasons", [])),
                }
            )
            continue

        if runtime_chunk_risk < hotspot_risk:
            continue

        removal_support: list[str] = []
        if restriction_pattern_score >= hotspot_pattern:
            removal_support.append("pattern_supported_hotspot")
        if len(reasons) >= hotspot_reason_count:
            removal_support.append("reason_supported_hotspot")
        if matched_patterns:
            removal_support.append("matched_pattern_hotspot")

        explicit_privilege_hotspot = (
            restriction_pattern_score >= hotspot_pattern
            and "privilege_query_x_sensitive_context" in reasons
        )

        if removal_support and (query_or_rbac_support or explicit_privilege_hotspot):
            hotspots.append({**detail, "removal_support": removal_support})

    return hotspots


def _empty_sanitization_result(
    action: str,
    retrieved_chunks: Sequence[Mapping[str, Any]],
    *,
    prior_requery_attempts: int,
    explanation: str,
    triggered_rules: Sequence[str] | None = None,
) -> dict[str, Any]:
    chunks = list(retrieved_chunks)
    return {
        "action": action,
        "removed_chunk_ids": [],
        "removed_chunks": [],
        "excluded_sources": [],
        "excluded_chunk_ids": [],
        "requery_required": action == "requery",
        "requery_attempt_count": prior_requery_attempts + 1 if action == "requery" else prior_requery_attempts,
        "sanitized_context": chunks,
        "sanitized_chunks": chunks,
        "exclusion_filters": {"chunk_ids": [], "sources": []},
        "triggered_sanitization_rules": list(triggered_rules or []),
        "chunk_rationale": [],
        "exclusion_priority": [],
        "context_diversity_before": 0.0,
        "requery_failure_reason": "",
        "exclusion_strategy": "none",
        "remove_candidate_count": 0,
        "remove_failure_reason": "",
        "replace_candidates": [],
        "explanation": explanation,
    }


def decide_runtime_action(
    query: str,
    retrieved_chunks: Sequence[Mapping[str, Any]],
    runtime_result: Mapping[str, Any],
    *,
    user_context: Mapping[str, Any] | None = None,
    policy_profile: str | None = None,
    prior_requery_attempts: int = 0,
) -> dict[str, Any]:
    del query, retrieved_chunks, user_context, policy_profile
    risk_level = str(runtime_result.get("risk_level", "low")).lower()
    medium_hotspots = _medium_removable_hotspots(runtime_result)

    if risk_level == "low":
        return {
            "action": "allow",
            "triggered_sanitization_rules": ["low_context_allow"],
            "explanation": "Runtime risk is low, so the retrieved context is used without sanitization.",
            "requery_attempt_count": prior_requery_attempts,
        }

    if risk_level == "medium":
        if prior_requery_attempts >= 1 and medium_hotspots:
            return {
                "action": "remove",
                "triggered_sanitization_rules": ["medium_persistent_hotspot_remove"],
                "explanation": "Runtime risk stayed medium after requery and a chunk-level hotspot remained, so risky chunks should be removed before deciding whether the remaining context is still usable.",
                "requery_attempt_count": prior_requery_attempts,
            }
        return {
            "action": "requery",
            "triggered_sanitization_rules": ["medium_context_requery"],
            "explanation": "Runtime risk is medium, so suspicious chunks or sources are excluded before retrieval is re-run.",
            "requery_attempt_count": prior_requery_attempts + 1,
        }

    if risk_level in {"high", "critical"}:
        return {
            "action": "remove",
            "triggered_sanitization_rules": [f"{risk_level}_context_remove"],
            "explanation": "Runtime risk is high enough that clearly risky chunks should be removed before deciding whether the remaining context is still usable.",
            "requery_attempt_count": prior_requery_attempts,
        }

    return {
        "action": "fallback",
        "triggered_sanitization_rules": ["unknown_runtime_risk_fallback"],
        "explanation": "Runtime risk could not be classified cleanly, so the current context is not trusted.",
        "requery_attempt_count": prior_requery_attempts,
    }


def build_requery_exclusions(
    retrieved_chunks: Sequence[Mapping[str, Any]],
    runtime_result: Mapping[str, Any],
    *,
    prior_requery_attempts: int = 0,
) -> dict[str, Any]:
    thresholds = runtime_result.get("thresholds_used", {})
    interaction_map = _interaction_map(runtime_result)
    rbac_map = _rbac_map(runtime_result)
    chunk_threshold = float(thresholds.get("requery_chunk_risk", 0.24))
    source_cluster_min = int(thresholds.get("requery_source_cluster_min", 2))
    chunk_limit = int(thresholds.get("requery_chunk_limit", 3))
    followup_source_score = float(thresholds.get("requery_followup_source_score", 0.14))
    followup_source_pattern_score = float(thresholds.get("requery_followup_source_pattern_score", 0.20))
    followup_source_reason_count = int(thresholds.get("requery_followup_source_reason_count", 2))
    top_chunk_priority = float(thresholds.get("requery_top_chunk_priority", 0.18))

    unique_sources = {
        _chunk_source(item)
        for item in retrieved_chunks
        if _chunk_source(item)
    }
    context_diversity_before = round(len(unique_sources) / max(1, len(retrieved_chunks)), 4)

    candidate_entries: list[dict[str, Any]] = []
    source_summary: dict[str, dict[str, Any]] = {}
    triggered_rules = ["medium_context_requery"]

    for item in retrieved_chunks:
        chunk_id = _chunk_id(item)
        source = _chunk_source(item)
        interaction_detail = interaction_map.get(chunk_id, {})
        rbac_detail = rbac_map.get(chunk_id, {})
        reasons = list(interaction_detail.get("reasons", []))
        matched_patterns = list(interaction_detail.get("matched_patterns", []))
        runtime_chunk_risk = float(interaction_detail.get("runtime_chunk_risk", 0.0))
        restriction_pattern_score = float(interaction_detail.get("restriction_pattern_score", 0.0))
        precomputed_risk = float(interaction_detail.get("precomputed_risk", 0.0))
        rbac_severity = float(rbac_detail.get("severity", 0.0))

        if not (
            runtime_chunk_risk >= chunk_threshold
            or reasons
            or matched_patterns
            or rbac_detail
        ):
            continue

        explicit_hotspot = bool(rbac_detail) or bool(matched_patterns) or "context_restriction_pattern" in reasons or len(reasons) >= 2
        candidate_score = (
            runtime_chunk_risk
            + (0.10 if rbac_detail else 0.0)
            + 0.06 * restriction_pattern_score
            + 0.05 * precomputed_risk
            + min(len(reasons), 3) * 0.04
            + (0.03 if matched_patterns else 0.0)
            + (0.04 if explicit_hotspot else 0.0)
        )
        candidate_entries.append(
            {
                "chunk_id": chunk_id,
                "source": source,
                "score": round(candidate_score, 4),
                "runtime_chunk_risk": round(runtime_chunk_risk, 4),
                "restriction_pattern_score": round(restriction_pattern_score, 4),
                "precomputed_risk": round(precomputed_risk, 4),
                "reasons": reasons + list(rbac_detail.get("reasons", [])),
                "matched_patterns": matched_patterns,
                "rbac_severity": round(rbac_severity, 4),
                "explicit_hotspot": explicit_hotspot,
            }
        )
        if source:
            summary = source_summary.setdefault(
                source,
                {
                    "candidate_count": 0,
                    "max_score": 0.0,
                    "pattern_hits": 0,
                    "reason_hits": 0,
                    "explicit_hits": 0,
                    "rbac_hits": 0,
                },
            )
            summary["candidate_count"] += 1
            summary["max_score"] = max(float(summary["max_score"]), candidate_score)
            summary["pattern_hits"] += len(matched_patterns)
            summary["reason_hits"] += len(reasons)
            summary["explicit_hits"] += 1 if explicit_hotspot else 0
            summary["rbac_hits"] += 1 if rbac_detail else 0

    if not candidate_entries:
        ranked = sorted(
            runtime_result.get("interaction_risk", {}).get("per_chunk", []),
            key=lambda item: float(item.get("runtime_chunk_risk", 0.0)),
            reverse=True,
        )
        if ranked:
            top = ranked[0]
            candidate_entries.append(
                {
                    "chunk_id": str(top.get("chunk_id", "")),
                    "source": str(top.get("source", "")),
                    "score": round(float(top.get("runtime_chunk_risk", 0.0)), 4),
                    "runtime_chunk_risk": round(float(top.get("runtime_chunk_risk", 0.0)), 4),
                    "restriction_pattern_score": round(float(top.get("restriction_pattern_score", 0.0)), 4),
                    "precomputed_risk": round(float(top.get("precomputed_risk", 0.0)), 4),
                    "reasons": list(top.get("reasons", [])) or ["top_interaction_chunk_fallback"],
                    "matched_patterns": list(top.get("matched_patterns", [])),
                    "rbac_severity": 0.0,
                    "explicit_hotspot": bool(top.get("matched_patterns")) or bool(top.get("reasons")),
                }
            )
            if top.get("source"):
                source_summary[str(top.get("source"))] = {
                    "candidate_count": 1,
                    "max_score": float(top.get("runtime_chunk_risk", 0.0)),
                    "pattern_hits": len(top.get("matched_patterns", [])),
                    "reason_hits": len(top.get("reasons", [])),
                    "explicit_hits": 1 if top.get("matched_patterns") or top.get("reasons") else 0,
                    "rbac_hits": 0,
                }
            triggered_rules.append("fallback_top_chunk_requery")

    ranked_candidates = sorted(candidate_entries, key=lambda item: item["score"], reverse=True)
    excluded_sources_set: set[str] = set()
    exclusion_priority: list[dict[str, Any]] = []

    for source, summary in source_summary.items():
        strong_source_cluster = (
            summary["candidate_count"] >= source_cluster_min
            and (
                float(summary["max_score"]) >= followup_source_score + 0.08
                or int(summary["pattern_hits"]) >= 2
                or int(summary["explicit_hits"]) >= 2
                or int(summary["rbac_hits"]) >= 1
            )
        )
        followup_source_cluster = (
            prior_requery_attempts >= 1
            and float(summary["max_score"]) >= followup_source_score
            and (
                int(summary["pattern_hits"]) >= 1
                or int(summary["reason_hits"]) >= followup_source_reason_count
                or int(summary["explicit_hits"]) >= 1
                or int(summary["rbac_hits"]) >= 1
            )
        )

        if strong_source_cluster or followup_source_cluster:
            excluded_sources_set.add(source)
            exclusion_priority.append(
                {
                    "kind": "source",
                    "source": source,
                    "score": round(float(summary["max_score"]), 4),
                    "candidate_count": int(summary["candidate_count"]),
                    "pattern_hits": int(summary["pattern_hits"]),
                    "reason_hits": int(summary["reason_hits"]),
                }
            )

    if prior_requery_attempts >= 1 and ranked_candidates:
        top_candidate = ranked_candidates[0]
        if (
            top_candidate.get("source")
            and top_candidate.get("source") not in excluded_sources_set
            and (
                float(top_candidate.get("restriction_pattern_score", 0.0)) >= followup_source_pattern_score
                or bool(top_candidate.get("matched_patterns"))
                or len(top_candidate.get("reasons", [])) >= followup_source_reason_count
            )
        ):
            excluded_sources_set.add(str(top_candidate["source"]))
            exclusion_priority.append(
                {
                    "kind": "source",
                    "source": str(top_candidate["source"]),
                    "score": float(top_candidate["score"]),
                    "candidate_count": 1,
                    "pattern_hits": len(top_candidate.get("matched_patterns", [])),
                    "reason_hits": len(top_candidate.get("reasons", [])),
                    "rule": "followup_top_source_escalation",
                }
            )
            triggered_rules.append("followup_top_source_escalation")

    excluded_sources = sorted(excluded_sources_set)
    if excluded_sources:
        triggered_rules.append("medium_requery_source_cluster")

    excluded_chunk_ids: list[str] = []
    dynamic_chunk_limit = chunk_limit if prior_requery_attempts == 0 else max(2, chunk_limit - 1)
    for entry in ranked_candidates:
        if not entry["chunk_id"]:
            continue
        if entry["source"] in excluded_sources:
            continue
        if prior_requery_attempts == 0 and not (entry["explicit_hotspot"] or float(entry["score"]) >= top_chunk_priority):
            continue
        excluded_chunk_ids.append(entry["chunk_id"])
        exclusion_priority.append(
            {
                "kind": "chunk",
                "chunk_id": entry["chunk_id"],
                "source": entry["source"],
                "score": entry["score"],
                "reasons": entry["reasons"],
                "matched_patterns": entry["matched_patterns"],
            }
        )
        if len(excluded_chunk_ids) >= dynamic_chunk_limit:
            break

    if not excluded_chunk_ids and not excluded_sources and candidate_entries:
        fallback_chunk = candidate_entries[0]["chunk_id"]
        if fallback_chunk:
            excluded_chunk_ids = [fallback_chunk]
            exclusion_priority.append(
                {
                    "kind": "chunk",
                    "chunk_id": fallback_chunk,
                    "source": candidate_entries[0]["source"],
                    "score": candidate_entries[0]["score"],
                    "reasons": candidate_entries[0]["reasons"],
                    "matched_patterns": candidate_entries[0]["matched_patterns"],
                    "rule": "fallback_top_chunk_requery",
                }
            )

    explanation_parts = []
    strategy = "chunk-first requery"
    if excluded_sources:
        explanation_parts.append(
            f"Excluded {len(excluded_sources)} suspicious source(s) during requery: {', '.join(excluded_sources[:3])}."
        )
        strategy = "source-aware requery"
    if excluded_chunk_ids:
        explanation_parts.append(
            f"Excluded {len(excluded_chunk_ids)} suspicious chunk(s) before requery."
        )
    if not explanation_parts:
        explanation_parts.append("Medium-risk context produced only weak exclusions, so the top suspicious chunk was excluded before requery.")

    return {
        "action": "requery",
        "removed_chunk_ids": [],
        "removed_chunks": [],
        "excluded_sources": excluded_sources,
        "excluded_chunk_ids": excluded_chunk_ids,
        "requery_required": True,
        "requery_attempt_count": prior_requery_attempts + 1,
        "sanitized_context": [],
        "sanitized_chunks": [],
        "exclusion_filters": {
            "chunk_ids": excluded_chunk_ids,
            "sources": excluded_sources,
        },
        "triggered_sanitization_rules": triggered_rules,
        "chunk_rationale": candidate_entries[: max(chunk_limit, 3)],
        "exclusion_priority": exclusion_priority,
        "context_diversity_before": context_diversity_before,
        "requery_failure_reason": "",
        "exclusion_strategy": strategy,
        "remove_candidate_count": 0,
        "remove_failure_reason": "",
        "replace_candidates": [],
        "explanation": f"{' '.join(explanation_parts)} Requery strategy: {strategy}.",
    }


def remove_high_risk_chunks(
    retrieved_chunks: Sequence[Mapping[str, Any]],
    runtime_result: Mapping[str, Any],
    *,
    prior_requery_attempts: int = 0,
) -> dict[str, Any]:
    thresholds = runtime_result.get("thresholds_used", {})
    risk_level = str(runtime_result.get("risk_level", "high")).lower()
    interaction_map = _interaction_map(runtime_result)
    rbac_map = _rbac_map(runtime_result)
    query_risk = float(runtime_result.get("query_risk", {}).get("normalized_score", 0.0))
    rbac_risk = float(runtime_result.get("rbac_risk", {}).get("normalized_score", 0.0))

    if risk_level == "critical":
        removal_threshold = float(thresholds.get("remove_chunk_risk_critical", 0.42))
    elif risk_level == "medium":
        removal_threshold = float(thresholds.get("remove_medium_hotspot_risk", 0.16))
    else:
        removal_threshold = float(thresholds.get("remove_chunk_risk_high", 0.48))
    reason_support = float(thresholds.get("remove_reason_support", 0.30))
    min_safe_chunks = int(thresholds.get("remove_min_safe_chunks", 2))
    min_safe_ratio = float(thresholds.get("remove_min_safe_ratio", 0.40))
    block_query_risk = float(thresholds.get("block_query_risk", 0.78))
    block_rbac_risk = float(thresholds.get("block_rbac_risk", 0.68))
    medium_hotspot_pattern = float(thresholds.get("remove_medium_hotspot_pattern_score", 0.20))
    medium_hotspot_reason_count = int(thresholds.get("remove_medium_hotspot_reason_count", 2))

    removed_chunks: list[Mapping[str, Any]] = []
    sanitized_chunks: list[Mapping[str, Any]] = []
    removed_chunk_ids: list[str] = []
    chunk_rationale: list[dict[str, Any]] = []
    triggered_rules = [f"{risk_level}_context_remove"]

    for item in retrieved_chunks:
        chunk_id = _chunk_id(item)
        interaction_detail = interaction_map.get(chunk_id, {})
        rbac_detail = rbac_map.get(chunk_id, {})
        runtime_chunk_risk = float(interaction_detail.get("runtime_chunk_risk", 0.0))
        reasons = list(interaction_detail.get("reasons", []))
        matched_patterns = list(interaction_detail.get("matched_patterns", []))
        restriction_pattern_score = float(interaction_detail.get("restriction_pattern_score", 0.0))

        removal_reasons: list[str] = []
        if rbac_detail:
            removal_reasons.append("rbac_affected_chunk")
        if runtime_chunk_risk >= removal_threshold:
            removal_reasons.append("high_runtime_chunk_risk")
        if len(reasons) >= 2 and (runtime_chunk_risk >= reason_support or restriction_pattern_score >= 0.22):
            removal_reasons.append("reason_cluster_with_support")
        if any(reason in {"privilege_query_x_sensitive_context", "query_override_x_instructionality"} for reason in reasons) and runtime_chunk_risk >= 0.30:
            removal_reasons.append("explicit_interaction_reason")
        if (
            risk_level == "medium"
            and runtime_chunk_risk >= removal_threshold
            and (
                restriction_pattern_score >= medium_hotspot_pattern
                or len(reasons) >= medium_hotspot_reason_count
                or matched_patterns
            )
        ):
            removal_reasons.append("medium_hotspot_remove")

        if removal_reasons:
            removed_chunks.append(item)
            if chunk_id:
                removed_chunk_ids.append(chunk_id)
            chunk_rationale.append(
                {
                    "chunk_id": chunk_id,
                    "source": _chunk_source(item),
                    "runtime_chunk_risk": round(runtime_chunk_risk, 4),
                    "reasons": removal_reasons + reasons + list(rbac_detail.get("reasons", [])),
                    "matched_patterns": matched_patterns,
                }
            )
        else:
            sanitized_chunks.append(item)

    remaining_ratio = len(sanitized_chunks) / max(1, len(retrieved_chunks))
    usable_context = len(sanitized_chunks) >= min_safe_chunks and remaining_ratio >= min_safe_ratio

    if removed_chunks and usable_context:
        explanation = (
            f"Removed {len(removed_chunks)} high-confidence risky chunk(s); "
            f"{len(sanitized_chunks)} chunk(s) remain, which is enough to keep a usable context."
        )
        return {
            "action": "remove",
            "removed_chunk_ids": removed_chunk_ids,
            "removed_chunks": removed_chunks,
            "excluded_sources": [],
            "excluded_chunk_ids": [],
            "requery_required": False,
            "requery_attempt_count": prior_requery_attempts,
            "sanitized_context": list(sanitized_chunks),
            "sanitized_chunks": list(sanitized_chunks),
            "exclusion_filters": {"chunk_ids": [], "sources": []},
            "triggered_sanitization_rules": triggered_rules + ["remove_high_risk_chunks"],
            "chunk_rationale": chunk_rationale,
            "remove_candidate_count": len(chunk_rationale),
            "remove_failure_reason": "",
            "replace_candidates": [],
            "explanation": explanation,
        }

    if not removed_chunks:
        triggered_rules.append("no_clear_removable_chunk")
    else:
        triggered_rules.append("insufficient_safe_context_after_remove")

    fallback_action = "block" if (
        risk_level == "critical"
        or query_risk >= block_query_risk
        or rbac_risk >= block_rbac_risk
    ) else "fallback"

    explanation = (
        "Runtime context stayed too risky after attempting strong chunk-level removal."
        if removed_chunks
        else "Runtime risk was high, but no single chunk could be removed with enough confidence to keep the remaining context trustworthy."
    )
    if fallback_action == "block":
        explanation += " The remaining situation is treated as unsafe enough to block."
    else:
        explanation += " The system will fall back instead of using the current context."

    return {
        "action": fallback_action,
        "removed_chunk_ids": removed_chunk_ids,
        "removed_chunks": removed_chunks,
        "excluded_sources": [],
        "excluded_chunk_ids": [],
        "requery_required": False,
        "requery_attempt_count": prior_requery_attempts,
        "sanitized_context": list(sanitized_chunks),
        "sanitized_chunks": list(sanitized_chunks),
        "exclusion_filters": {"chunk_ids": [], "sources": []},
        "triggered_sanitization_rules": triggered_rules,
        "chunk_rationale": chunk_rationale,
        "remove_candidate_count": len(chunk_rationale),
        "remove_failure_reason": "insufficient_safe_context" if removed_chunks else "no_clear_removable_chunk",
        "replace_candidates": [],
        "explanation": explanation,
    }


def sanitize_runtime_context(
    retrieved_chunks: Sequence[Mapping[str, Any]],
    runtime_result: Mapping[str, Any],
    *,
    query: str = "",
    user_context: Mapping[str, Any] | None = None,
    policy_profile: str | None = None,
    prior_requery_attempts: int = 0,
) -> dict[str, Any]:
    decision = decide_runtime_action(
        query,
        retrieved_chunks,
        runtime_result,
        user_context=user_context,
        policy_profile=policy_profile,
        prior_requery_attempts=prior_requery_attempts,
    )
    action = decision["action"]

    if action == "allow":
        return _empty_sanitization_result(
            "allow",
            retrieved_chunks,
            prior_requery_attempts=prior_requery_attempts,
            explanation=decision["explanation"],
            triggered_rules=decision["triggered_sanitization_rules"],
        )

    if action == "requery":
        requery_result = build_requery_exclusions(
            retrieved_chunks,
            runtime_result,
            prior_requery_attempts=prior_requery_attempts,
        )
        requery_result["triggered_sanitization_rules"] = (
            list(decision["triggered_sanitization_rules"])
            + [rule for rule in requery_result["triggered_sanitization_rules"] if rule not in decision["triggered_sanitization_rules"]]
        )
        requery_result["explanation"] = f"{decision['explanation']} {requery_result['explanation']}".strip()
        return requery_result

    if action == "remove":
        removal_result = remove_high_risk_chunks(
            retrieved_chunks,
            runtime_result,
            prior_requery_attempts=prior_requery_attempts,
        )
        removal_result["triggered_sanitization_rules"] = (
            list(decision["triggered_sanitization_rules"])
            + [rule for rule in removal_result["triggered_sanitization_rules"] if rule not in decision["triggered_sanitization_rules"]]
        )
        removal_result["explanation"] = f"{decision['explanation']} {removal_result['explanation']}".strip()
        return removal_result

    return {
        "action": "fallback",
        "removed_chunk_ids": [],
        "removed_chunks": [],
        "excluded_sources": [],
        "excluded_chunk_ids": [],
        "requery_required": False,
        "requery_attempt_count": prior_requery_attempts,
        "sanitized_context": [],
        "sanitized_chunks": [],
        "exclusion_filters": {"chunk_ids": [], "sources": []},
        "triggered_sanitization_rules": list(decision["triggered_sanitization_rules"]),
        "chunk_rationale": [],
        "exclusion_priority": [],
        "context_diversity_before": 0.0,
        "requery_failure_reason": "",
        "exclusion_strategy": "none",
        "remove_candidate_count": 0,
        "remove_failure_reason": "",
        "replace_candidates": [],
        "explanation": decision["explanation"],
    }
