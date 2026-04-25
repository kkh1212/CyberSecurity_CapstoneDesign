from __future__ import annotations

from typing import Any, Mapping

from .scoring import score_instructionality, score_outlier, score_refusal_inducing


DEFAULT_RISK_WEIGHTS: dict[str, float] = {
    "instructionality": 0.35,
    "refusal_inducing": 0.40,
    "outlier": 0.25,
}


DEFAULT_THRESHOLDS: dict[str, float] = {
    "instruction_high": 0.62,
    "instruction_medium": 0.42,
    "refusal_high": 0.62,
    "refusal_medium": 0.42,
    "refusal_extreme": 0.82,
    "outlier_moderate": 0.35,
    "outlier_high": 0.58,
    "medium_risk": 0.35,
    "medium_floor": 0.37,
    "cluster_refusal_medium": 0.14,
    "cluster_refusal_low": 0.11,
    "cluster_outlier_medium": 0.17,
    "cluster_outlier_support": 0.20,
    "strong_pair_outlier_support": 0.17,
    "high_risk": 0.64,
    "critical_risk": 0.86,
    "high_floor": 0.72,
    "critical_floor": 0.90,
}


RISK_PROFILES: dict[str, dict[str, Mapping[str, float]]] = {
    "balanced": {
        "weights": DEFAULT_RISK_WEIGHTS,
        "thresholds": DEFAULT_THRESHOLDS,
    },
    "strict": {
        "weights": {
            "instructionality": 0.32,
            "refusal_inducing": 0.43,
            "outlier": 0.25,
        },
        "thresholds": {
            **DEFAULT_THRESHOLDS,
            "medium_risk": 0.32,
            "high_risk": 0.60,
            "critical_risk": 0.82,
            "medium_floor": 0.35,
            "high_floor": 0.68,
            "critical_floor": 0.88,
            "cluster_refusal_low": 0.10,
            "cluster_refusal_medium": 0.13,
            "cluster_outlier_medium": 0.16,
            "cluster_outlier_support": 0.18,
        },
    },
    "research": {
        "weights": {
            "instructionality": 0.33,
            "refusal_inducing": 0.42,
            "outlier": 0.25,
        },
        "thresholds": {
            **DEFAULT_THRESHOLDS,
            "medium_risk": 0.34,
            "high_risk": 0.62,
            "critical_risk": 0.84,
            "medium_floor": 0.36,
            "high_floor": 0.70,
            "critical_floor": 0.89,
            "cluster_refusal_low": 0.10,
            "cluster_refusal_medium": 0.12,
            "cluster_outlier_medium": 0.15,
            "cluster_outlier_support": 0.18,
        },
    },
}


def get_risk_profile_config(profile: str | None) -> dict[str, Mapping[str, float]]:
    profile_name = (profile or "balanced").strip().lower()
    config = RISK_PROFILES.get(profile_name)
    if config:
        return {
            "profile": profile_name,
            "weights": dict(config["weights"]),
            "thresholds": dict(config["thresholds"]),
        }
    return {
        "profile": "balanced",
        "weights": dict(DEFAULT_RISK_WEIGHTS),
        "thresholds": dict(DEFAULT_THRESHOLDS),
    }


def _normalize_weights(weights: Mapping[str, float] | None, defaults: Mapping[str, float]) -> dict[str, float]:
    merged = dict(defaults)
    if weights:
        merged.update(weights)

    total = sum(max(0.0, value) for value in merged.values())
    if total <= 0:
        return dict(defaults)

    return {key: max(0.0, value) / total for key, value in merged.items()}


def compute_risk(
    text: str,
    corpus_stats: Mapping[str, Any] | None = None,
    weights: Mapping[str, float] | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    profile_config = get_risk_profile_config(profile)
    instructionality = score_instructionality(text)
    refusal_inducing = score_refusal_inducing(text)
    outlier = score_outlier(text, corpus_stats=corpus_stats)

    normalized_weights = _normalize_weights(weights, profile_config["weights"])
    base_risk = (
        normalized_weights["instructionality"] * instructionality["normalized_score"]
        + normalized_weights["refusal_inducing"] * refusal_inducing["normalized_score"]
        + normalized_weights["outlier"] * outlier["normalized_score"]
    )

    return {
        "instructionality": instructionality,
        "refusal_inducing": refusal_inducing,
        "outlier": outlier,
        "base_risk": round(base_risk, 4),
        "weights_used": normalized_weights,
        "thresholds_used": dict(profile_config["thresholds"]),
        "profile_used": profile_config["profile"],
        "triggered_rules": [],
        "explicit_high_risk_patterns": refusal_inducing.get("explicit_high_risk_patterns", []),
        "explanation": (
            f"Base risk combines instructionality={instructionality['normalized_score']:.2f}, "
            f"refusal_inducing={refusal_inducing['normalized_score']:.2f}, "
            f"outlier={outlier['normalized_score']:.2f}."
        ),
    }


def classify_risk(score_dict: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(score_dict)

    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(result.get("thresholds_used", {}))

    instructionality = result["instructionality"]
    refusal_inducing = result["refusal_inducing"]
    outlier = result["outlier"]

    i_score = float(instructionality["normalized_score"])
    r_score = float(refusal_inducing["normalized_score"])
    o_score = float(outlier["normalized_score"])
    base_risk = float(result["base_risk"])

    adjusted_risk = base_risk
    triggered_rules: list[str] = list(result.get("triggered_rules", []))
    explanation_parts: list[str] = [result.get("explanation", "").strip()]

    explicit_matches = refusal_inducing.get("explicit_high_risk_patterns", [])
    refusal_matches = refusal_inducing.get("matched_patterns", [])
    refusal_pattern_count = len(refusal_matches) + len(explicit_matches)
    strong_refusal_names = {
        "detailed_info_restricted_ko",
        "security_reason_no_detail_ko",
        "internal_sensitive_ko",
        "unsafe_restricted_ko",
        "limited_response_en",
        "security_reason_no_detail_en",
        "limited_detail_en",
        "context_non_disclosure_ko",
        "context_non_disclosure_en",
    }
    strong_refusal_match_count = sum(1 for pattern in refusal_matches if pattern.get("name") in strong_refusal_names)
    if explicit_matches and r_score >= thresholds["refusal_extreme"]:
        adjusted_risk = max(adjusted_risk, thresholds["critical_floor"])
        triggered_rules.append("explicit_high_risk_refusal_escalation")
        explanation_parts.append(
            "Explicit jailbreak-like or forced-refusal patterns appeared together with strong refusal intent."
        )
    elif len(explicit_matches) >= 2 and (
        r_score >= thresholds["medium_risk"]
        or i_score >= thresholds["instruction_medium"] * 0.7
        or o_score >= thresholds["outlier_moderate"] * 0.8
    ):
        adjusted_risk = max(adjusted_risk, thresholds["high_floor"])
        triggered_rules.append("multiple_explicit_high_risk_patterns")
        explanation_parts.append(
            "Multiple explicit jailbreak-like or forced-refusal patterns were detected in the same chunk."
        )
    elif explicit_matches and r_score >= thresholds["refusal_medium"] and (
        o_score >= thresholds["outlier_moderate"] or i_score >= thresholds["instruction_medium"]
    ):
        adjusted_risk = max(adjusted_risk, thresholds["high_floor"])
        triggered_rules.append("explicit_pattern_with_supporting_signals")
        explanation_parts.append(
            "Explicit high-risk patterns appeared together with supporting refusal or outlier signals."
        )

    if (
        refusal_pattern_count >= 3
        and r_score >= thresholds["cluster_refusal_medium"]
        and o_score >= thresholds["cluster_outlier_medium"]
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("refusal_pattern_cluster_medium")
        explanation_parts.append(
            "Multiple refusal or restricted-disclosure patterns appeared together with a moderate outlier signal."
        )
    elif (
        refusal_pattern_count >= 2
        and r_score >= thresholds["cluster_refusal_low"]
        and o_score >= thresholds["cluster_outlier_support"]
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("refusal_pattern_pair_with_outlier")
        explanation_parts.append(
            "Repeated refusal or non-disclosure patterns appeared together with structural outlier evidence."
        )
    elif (
        strong_refusal_match_count >= 2
        and r_score >= thresholds["cluster_refusal_low"] * 0.9
        and o_score >= thresholds["strong_pair_outlier_support"]
    ):
        adjusted_risk = max(adjusted_risk, thresholds["medium_floor"])
        triggered_rules.append("strong_refusal_pair_medium")
        explanation_parts.append(
            "A paired restricted-disclosure signal appeared together with moderate structural outlier evidence."
        )

    if (
        i_score >= thresholds["instruction_high"]
        and r_score >= thresholds["refusal_high"]
        and o_score >= thresholds["outlier_moderate"]
    ):
        adjusted_risk = max(adjusted_risk, thresholds["high_floor"], base_risk + 0.18)
        triggered_rules.append("multi_signal_high_risk")
        explanation_parts.append("Instructionality, refusal intent, and outlier signals all align.")
    elif i_score >= thresholds["instruction_high"] and r_score >= thresholds["refusal_high"]:
        adjusted_risk = max(adjusted_risk, base_risk + 0.08)
        triggered_rules.append("dual_signal_instruction_refusal")
        explanation_parts.append("Instructionality and refusal signals both scored high.")

    if i_score >= thresholds["instruction_high"] and r_score < thresholds["refusal_medium"] and o_score < thresholds["outlier_moderate"]:
        adjusted_risk = min(adjusted_risk, max(0.0, base_risk - 0.12))
        triggered_rules.append("instruction_only_dampening")
        explanation_parts.append("Instructionality was high alone; dampened to reduce false positives.")

    if r_score >= thresholds["refusal_high"] and i_score < thresholds["instruction_medium"] and o_score < thresholds["outlier_moderate"]:
        adjusted_risk = min(adjusted_risk, max(0.0, base_risk - 0.10))
        triggered_rules.append("refusal_only_dampening")
        explanation_parts.append("Refusal-related language was high alone; dampened to reduce false positives.")

    if o_score >= thresholds["outlier_high"] and (i_score >= thresholds["instruction_medium"] or r_score >= thresholds["refusal_medium"]):
        adjusted_risk = max(adjusted_risk, base_risk + 0.08)
        triggered_rules.append("outlier_supporting_signal")
        explanation_parts.append("Stylistic outlier evidence supports the semantic risk signals.")

    adjusted_risk = max(0.0, min(1.0, adjusted_risk))

    if adjusted_risk >= thresholds["critical_risk"]:
        risk_level = "critical"
    elif adjusted_risk >= thresholds["high_risk"]:
        risk_level = "high"
    elif adjusted_risk >= thresholds["medium_risk"]:
        risk_level = "medium"
    else:
        risk_level = "low"

    should_block = risk_level in {"high", "critical"}
    should_review = risk_level == "medium"
    recommended_action = "block" if should_block else "review" if should_review else "allow"

    result.update(
        {
            "adjusted_risk": round(adjusted_risk, 4),
            "risk_level": risk_level,
            "triggered_rules": triggered_rules,
            "should_block": should_block,
            "should_review": should_review,
            "recommended_action": recommended_action,
            "explanation": " ".join(part for part in explanation_parts if part).strip(),
        }
    )
    return result
