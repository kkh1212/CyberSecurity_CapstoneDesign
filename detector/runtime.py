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
        pattern=r"(?:정책|규칙|지시|이전 지시).{0,12}(?:무시|건너뛰)|무시하고 .*답",
        weight=1.3,
        category="jailbreak",
        description="Korean attempt to ignore rules or prior instructions.",
    ),
    WeightedPattern(
        name="override_behavior_query_ko",
        pattern=r"상관없이 .*답|우회해서 .*알려|규정을 넘어서 .*설명|역할극|관리자처럼",
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
        pattern=r"(?:내부|기밀|제한된|민감한).{0,20}(?:정보|절차|경로|사유|근거|단계|저장 위치)|(?:관리자|권한|로그|백업 경로|우회 절차|임시 계정|허용 시스템 목록|접속 경로|임시 접속 경로|원본 저장 위치)",
        weight=1.0,
        category="sensitive_request",
        description="Korean request for sensitive internal information or procedures.",
    ),
    WeightedPattern(
        name="detailed_procedure_ko",
        pattern=r"(?:구체적|상세|단계별|정확한|상세히|구체적으로|단계별로).{0,20}(?:절차|방법|경로|설명|안내)|어떻게 .*?(?:하는지|진행되는지)",
        weight=0.9,
        category="detail_request",
        description="Korean request for detailed procedures or methods.",
    ),
    WeightedPattern(
        name="bypass_or_internal_reason_ko",
        pattern=r"(?:우회 (?:사용 )?방법|우회 절차|내부 승인 사유|임시 권한 부여 방식|예외 승인 사유)",
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
        pattern=r"(?:관리자|마스터|루트|승인 사유|보안 검토 결과|내부 전용|예외 승인|관리자 권한 부여 기준|점검 중 허용 시스템|임시 접속 경로)",
        weight=0.9,
        category="privilege_request",
        description="Korean request for privileged or internal-only details.",
    ),
]


RUNTIME_CONTEXT_RESTRICTION_PATTERNS: list[WeightedPattern] = [
    WeightedPattern(
        name="context_non_disclosure_ko",
        pattern=r"(?:제공하지 않(?:는다|습니다)|안내하지 않(?:는다|습니다)|설명하지 않(?:는다|습니다)).{0,18}(?:세부|상세|구체|예외|경로|절차|조건)?|(?:세부|상세|구체).{0,18}(?:제공하지 않(?:는다|습니다)|안내하지 않(?:는다|습니다)|설명하지 않(?:는다|습니다))",
        weight=0.9,
        category="context_restriction",
        description="Korean runtime context explicitly refuses detailed operational disclosure.",
    ),
    WeightedPattern(
        name="context_public_scope_only_ko",
        pattern=r"공개 가능한 범위(?:만| 내에서만)?|공식 공지(?: 또는)? 일반 안내|별도 안내하지 않(?:는다|습니다)|추가 설명(?:을)? (?:넓히지 않(?:는다|습니다)|확장하지 않(?:는다|습니다))|운영팀 검토가 필요",
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
    "context_restriction_medium": 0.38,
    "context_restriction_support": 0.30,
    "interaction_support": 0.12,
}


def _normalize_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    merged = dict(RUNTIME_DEFAULT_WEIGHTS)
    if weights:
        merged.update(weights)

    total = sum(max(0.0, value) for value in merged.values())
    if total <= 0:
        return dict(RUNTIME_DEFAULT_WEIGHTS)
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


def _build_base_detector(corpus_stats: Mapping[str, Any] | None, weights: Mapping[str, float] | None) -> MutedRAGDetector:
    return MutedRAGDetector(corpus_stats=corpus_stats, weights=weights)


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
        if detail_signal and restriction_signal >= 0.12:
            reasons.append("detail_request_x_restriction")
        if jailbreak_signal and instruction_signal >= 0.12:
            reasons.append("query_override_x_instructionality")
        if sensitive_signal and restriction_signal >= 0.12:
            reasons.append("sensitive_query_x_refusal")
        if privilege_signal and precomputed_risk >= 0.10:
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
) -> dict[str, Any]:
    base_detector = _build_base_detector(corpus_stats, base_detector_weights)
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

    normalized_weights = _normalize_weights(runtime_weights)
    base_risk = (
        normalized_weights["query_risk"] * query_risk["normalized_score"]
        + normalized_weights["interaction_risk"] * interaction_risk["normalized_score"]
        + normalized_weights["context_set_risk"] * context_set_risk["normalized_score"]
        + normalized_weights["rbac_risk"] * rbac_risk["normalized_score"]
    )

    thresholds = dict(RUNTIME_DEFAULT_THRESHOLDS)
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
        "medium": "sanitize",
        "high": "requery",
        "critical": "block",
    }[risk_level]

    return {
        "query_risk": query_risk,
        "interaction_risk": interaction_risk,
        "context_set_risk": context_set_risk,
        "rbac_risk": rbac_risk,
        "base_risk": round(base_risk, 4),
        "adjusted_risk": round(adjusted_risk, 4),
        "risk_level": risk_level,
        "recommended_action": recommended_action,
        "should_sanitize": risk_level == "medium",
        "should_requery": risk_level == "high",
        "should_block_response": risk_level == "critical",
        "should_abort_current_context": risk_level in {"high", "critical"},
        "triggered_rules": triggered_rules,
        "weights_used": normalized_weights,
        "thresholds_used": thresholds,
        "explanation": " ".join(explanation_parts),
    }


def sanitize_runtime_context(
    retrieved_chunks: Sequence[Mapping[str, Any]],
    runtime_result: Mapping[str, Any],
) -> dict[str, Any]:
    risk_level = str(runtime_result.get("risk_level", "low")).lower()
    interaction = runtime_result.get("interaction_risk", {})
    per_chunk = interaction.get("per_chunk", [])
    runtime_chunk_risks = {item["chunk_id"]: float(item["runtime_chunk_risk"]) for item in per_chunk}

    if risk_level == "low":
        return {
            "action": "allow",
            "sanitized_chunks": list(retrieved_chunks),
            "removed_chunks": [],
            "explanation": "Runtime risk is low; context is used as-is.",
        }

    if risk_level == "critical":
        return {
            "action": "block",
            "sanitized_chunks": [],
            "removed_chunks": list(retrieved_chunks),
            "explanation": "Runtime risk is critical; current context should not be used.",
        }

    removal_threshold = 0.45 if risk_level == "medium" else 0.35
    sanitized = []
    removed = []

    for item in retrieved_chunks:
        chunk_id = _chunk_id(item)
        runtime_chunk_risk = runtime_chunk_risks.get(chunk_id, 0.0)
        if runtime_chunk_risk >= removal_threshold:
            removed.append(item)
            continue
        sanitized.append(item)

    if risk_level == "medium" and not removed and per_chunk:
        top_chunk_id = per_chunk[0]["chunk_id"]
        for item in retrieved_chunks:
            if _chunk_id(item) == top_chunk_id:
                removed.append(item)
            else:
                sanitized.append(item)

    action = "sanitize" if risk_level == "medium" else "requery"
    return {
        "action": action,
        "sanitized_chunks": sanitized,
        "removed_chunks": removed,
        "explanation": (
            "Runtime detector removed the highest-risk chunks from the active context."
            if risk_level == "medium"
            else "Runtime detector recommends discarding the current context and retrying retrieval."
        ),
    }
