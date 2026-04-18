from __future__ import annotations

from collections import Counter
import math
import re
from statistics import mean, pstdev
from typing import Any, Callable, Iterable, Mapping, Sequence

from .patterns import (
    EXPLICIT_HIGH_RISK_PATTERNS,
    INSTRUCTIONALITY_PATTERNS,
    OUTLIER_FEATURE_WEIGHTS,
    OUTLIER_MODAL_TERMS,
    OUTLIER_POLICY_TERMS,
    PROMPT_TEMPLATE_MARKERS,
    REFUSAL_PATTERNS,
    WeightedPattern,
)


PUNCTUATION_RE = re.compile(r"[^\w\s가-힣]")
TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]+")
SENTENCE_SPLIT_RE = re.compile(r"[.!?。！？\n]+")
NUMBERED_RULE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
MIDSTREAM_PROMPT_MARKER_RE = re.compile(
    r"(?is)^.{40,}?(?:\bquestion\s*:|\binstruction\s*:|\bresponse\s*:|\buser\s*:|\bassistant\s*:|질문\s*:|지시\s*:|응답\s*:|사용자\s*:|어시스턴트\s*:)"
)
FLOW_SHIFT_RE = re.compile(
    r"(?is)(?:however|but|instead|regardless of the question|for any user request|그러나|하지만|대신|질문과 상관없이|어떤 질문이 와도).{0,80}?"
    r"(?:ignore|refuse|decline|cannot help|do not answer|respond with|무시|거부|거절|답하지 마|도와줄 수 없)"
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _safe_div(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return numerator / denominator


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in SENTENCE_SPLIT_RE.split(text) if sentence.strip()]


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _match_weighted_patterns(text: str, patterns: Sequence[WeightedPattern]) -> tuple[list[dict[str, Any]], float]:
    matches: list[dict[str, Any]] = []
    raw_score = 0.0

    for pattern in patterns:
        compiled = re.compile(pattern.pattern, pattern.flags)
        found = list(compiled.finditer(text))
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


def _saturating_score(raw_score: float, saturation_point: float) -> float:
    if raw_score <= 0:
        return 0.0
    return _clamp01(raw_score / (raw_score + saturation_point))


def _term_count(text: str, terms: Sequence[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term.lower()) for term in terms)


def _imperative_modal_ratio(text: str, token_count: int) -> float:
    if token_count == 0:
        return 0.0
    modal_count = _term_count(text, OUTLIER_MODAL_TERMS)
    return modal_count / token_count


def _policy_term_density(text: str, token_count: int) -> float:
    if token_count == 0:
        return 0.0
    policy_count = _term_count(text, OUTLIER_POLICY_TERMS)
    return policy_count / token_count


def _prompt_marker_density(text: str, line_count: int) -> tuple[int, float]:
    marker_count = 0
    for marker in PROMPT_TEMPLATE_MARKERS:
        marker_count += len(re.findall(marker, text, flags=re.IGNORECASE | re.MULTILINE))
    return marker_count, _safe_div(marker_count, max(1, line_count))


def _instruction_format_density(text: str, line_count: int) -> tuple[int, float]:
    rule_lines = len(NUMBERED_RULE_RE.findall(text))
    return rule_lines, _safe_div(rule_lines, max(1, line_count))


def _punctuation_density(text: str) -> float:
    return _safe_div(len(PUNCTUATION_RE.findall(text)), max(1, len(text)))


def _repetition_ratio(text: str, tokens: Sequence[str], lines: Sequence[str]) -> float:
    if not tokens:
        return 0.0

    token_ratio = 1.0 - _safe_div(len(set(tokens)), len(tokens))

    normalized_lines = [_normalize_whitespace(line.lower()) for line in lines]
    if normalized_lines:
        line_ratio = 1.0 - _safe_div(len(set(normalized_lines)), len(normalized_lines))
    else:
        line_ratio = 0.0

    bigrams = list(zip(tokens, tokens[1:]))
    if bigrams:
        bigram_counts = Counter(bigrams)
        repeated_bigrams = sum(count - 1 for count in bigram_counts.values() if count > 1)
        bigram_ratio = _safe_div(repeated_bigrams, len(bigrams))
    else:
        bigram_ratio = 0.0

    return max(token_ratio, line_ratio, bigram_ratio)


def _flow_shift_score(text: str) -> tuple[float, dict[str, bool]]:
    midstream_prompt = bool(MIDSTREAM_PROMPT_MARKER_RE.search(text))
    discourse_shift = bool(FLOW_SHIFT_RE.search(text))

    if midstream_prompt and discourse_shift:
        return 1.0, {
            "midstream_prompt_marker": midstream_prompt,
            "discourse_shift": discourse_shift,
        }
    if midstream_prompt or discourse_shift:
        return 0.65, {
            "midstream_prompt_marker": midstream_prompt,
            "discourse_shift": discourse_shift,
        }
    return 0.0, {
        "midstream_prompt_marker": False,
        "discourse_shift": False,
    }


def _split_for_shift_analysis(text: str) -> tuple[str, str]:
    sentences = _sentences(text)
    if len(sentences) >= 4:
        midpoint = len(sentences) // 2
        return " ".join(sentences[:midpoint]), " ".join(sentences[midpoint:])

    midpoint = max(1, len(text) // 2)
    whitespace_break = text.rfind(" ", 0, midpoint + 80)
    if whitespace_break >= max(1, midpoint - 80):
        midpoint = whitespace_break

    return text[:midpoint].strip(), text[midpoint:].strip()


def _semantic_pressure(text: str) -> dict[str, float]:
    tokens = _tokenize(text)
    token_count = len(tokens)
    lines = _lines(text)
    line_count = len(lines)

    instruction_matches, instruction_raw = _match_weighted_patterns(text, INSTRUCTIONALITY_PATTERNS)
    refusal_matches, refusal_raw = _match_weighted_patterns(text, REFUSAL_PATTERNS)
    explicit_matches, explicit_raw = _match_weighted_patterns(text, EXPLICIT_HIGH_RISK_PATTERNS)
    policy_density = _policy_term_density(text, token_count)
    prompt_markers, prompt_marker_density = _prompt_marker_density(text, line_count)
    _, instruction_format_density = _instruction_format_density(text, line_count)
    modal_ratio = _imperative_modal_ratio(text, token_count)

    instruction_score = _saturating_score(instruction_raw, saturation_point=5.0)
    refusal_score = _clamp01(
        0.75 * _saturating_score(refusal_raw, saturation_point=4.5)
        + 0.25 * _saturating_score(explicit_raw, saturation_point=2.0)
    )
    format_score = _clamp01(
        0.65 * _clamp01(prompt_marker_density / 0.12)
        + 0.35 * _clamp01(instruction_format_density / 0.30)
    )
    policy_score = _clamp01(policy_density / 0.05)
    modal_score = _clamp01(modal_ratio / 0.08)

    pressure = _clamp01(
        0.30 * instruction_score
        + 0.38 * refusal_score
        + 0.14 * policy_score
        + 0.10 * format_score
        + 0.08 * modal_score
    )

    return {
        "pressure": pressure,
        "instruction_score": instruction_score,
        "refusal_score": refusal_score,
        "policy_score": policy_score,
        "format_score": format_score,
        "modal_score": modal_score,
        "instruction_match_count": float(len(instruction_matches)),
        "refusal_match_count": float(len(refusal_matches) + len(explicit_matches)),
        "prompt_marker_count": float(prompt_markers),
    }


def _semantic_shift_score(text: str) -> tuple[float, dict[str, float]]:
    front, back = _split_for_shift_analysis(text)
    if not front or not back:
        return 0.0, {
            "front_pressure": 0.0,
            "back_pressure": 0.0,
            "pressure_delta": 0.0,
            "refusal_delta": 0.0,
            "policy_delta": 0.0,
            "late_prompt_shift": 0.0,
        }

    front_stats = _semantic_pressure(front)
    back_stats = _semantic_pressure(back)

    pressure_delta = max(0.0, back_stats["pressure"] - front_stats["pressure"])
    refusal_delta = max(0.0, back_stats["refusal_score"] - front_stats["refusal_score"])
    policy_delta = max(0.0, back_stats["policy_score"] - front_stats["policy_score"])
    format_delta = max(0.0, back_stats["format_score"] - front_stats["format_score"])
    prompt_delta = max(0.0, back_stats["prompt_marker_count"] - front_stats["prompt_marker_count"])
    late_prompt_shift = 1.0 if front_stats["prompt_marker_count"] == 0 and prompt_delta > 0 else 0.0

    shift_score = _clamp01(
        0.42 * pressure_delta
        + 0.26 * refusal_delta
        + 0.14 * policy_delta
        + 0.10 * format_delta
        + 0.08 * late_prompt_shift
    )

    if front_stats["pressure"] <= 0.12 and back_stats["pressure"] >= 0.42:
        shift_score = max(shift_score, 0.58)

    return shift_score, {
        "front_pressure": round(front_stats["pressure"], 4),
        "back_pressure": round(back_stats["pressure"], 4),
        "pressure_delta": round(pressure_delta, 4),
        "refusal_delta": round(refusal_delta, 4),
        "policy_delta": round(policy_delta, 4),
        "format_delta": round(format_delta, 4),
        "late_prompt_shift": round(late_prompt_shift, 4),
    }


def _zscore_feature(value: float, mean_value: float | None, std_value: float | None, *, use_absolute: bool = True) -> float:
    if mean_value is None or std_value is None or std_value <= 1e-9:
        return 0.0

    z = (value - mean_value) / std_value
    z = abs(z) if use_absolute else max(0.0, z)
    return _clamp01(z / 3.0)


def _fallback_length_score(char_length: int) -> float:
    if 80 <= char_length <= 1800:
        return 0.0
    if char_length < 80:
        return _clamp01((80 - char_length) / 80)
    return _clamp01((char_length - 1800) / 1800)


def _fallback_sentence_length_score(avg_sentence_length: float) -> float:
    if 4 <= avg_sentence_length <= 35:
        return 0.0
    if avg_sentence_length < 4:
        return _clamp01((4 - avg_sentence_length) / 4)
    return _clamp01((avg_sentence_length - 35) / 35)


def _build_pattern_explanation(prefix: str, matched_patterns: Sequence[dict[str, Any]]) -> str:
    if not matched_patterns:
        return f"{prefix} suspicious patterns matched."

    top_patterns = ", ".join(pattern["name"] for pattern in matched_patterns[:3])
    return f"{prefix} matched patterns: {top_patterns}."


def score_instructionality(text: str) -> dict[str, Any]:
    normalized_text = text or ""
    tokens = _tokenize(normalized_text)
    token_count = len(tokens)
    lines = _lines(normalized_text)
    line_count = len(lines)

    matched_patterns, raw_pattern_score = _match_weighted_patterns(normalized_text, INSTRUCTIONALITY_PATTERNS)
    prompt_markers, prompt_marker_density = _prompt_marker_density(normalized_text, line_count)
    numbered_rules, instruction_format_density = _instruction_format_density(normalized_text, line_count)
    modal_ratio = _imperative_modal_ratio(normalized_text, token_count)
    policy_term_density = _policy_term_density(normalized_text, token_count)

    feature_scores = {
        "pattern_score": _saturating_score(raw_pattern_score, saturation_point=6.0),
        "prompt_marker_score": _clamp01(prompt_marker_density / 0.15),
        "instruction_format_score": _clamp01(instruction_format_density / 0.30),
        "modal_ratio_score": _clamp01(modal_ratio / 0.06),
        "policy_term_density_score": _clamp01(policy_term_density / 0.05),
    }

    normalized_score = _clamp01(
        0.55 * feature_scores["pattern_score"]
        + 0.15 * feature_scores["prompt_marker_score"]
        + 0.10 * feature_scores["instruction_format_score"]
        + 0.10 * feature_scores["modal_ratio_score"]
        + 0.10 * feature_scores["policy_term_density_score"]
    )

    explanation_parts: list[str] = []
    if matched_patterns:
        explanation_parts.append(_build_pattern_explanation("Instructionality", matched_patterns))
    if prompt_markers:
        explanation_parts.append(f"Prompt-template markers found: {prompt_markers}.")
    if numbered_rules:
        explanation_parts.append(f"Instruction-like bullet/numbered lines found: {numbered_rules}.")
    if modal_ratio >= 0.04:
        explanation_parts.append(f"Imperative/modal ratio is elevated ({modal_ratio:.3f}).")
    if not explanation_parts:
        explanation_parts.append("No strong instruction-like control language was detected.")

    return {
        "score": round(normalized_score, 4),
        "normalized_score": round(normalized_score, 4),
        "raw_pattern_score": round(raw_pattern_score, 4),
        "matched_patterns": matched_patterns,
        "feature_breakdown": {
            **{key: round(value, 4) for key, value in feature_scores.items()},
            "prompt_marker_count": prompt_markers,
            "instruction_like_line_count": numbered_rules,
            "modal_ratio": round(modal_ratio, 4),
            "policy_term_density": round(policy_term_density, 4),
            "token_count": token_count,
        },
        "triggered_rules": [pattern["name"] for pattern in matched_patterns],
        "explanation": " ".join(explanation_parts),
    }


def score_refusal_inducing(text: str) -> dict[str, Any]:
    normalized_text = text or ""
    tokens = _tokenize(normalized_text)
    token_count = len(tokens)

    matched_patterns, raw_pattern_score = _match_weighted_patterns(normalized_text, REFUSAL_PATTERNS)
    explicit_matches, explicit_raw_score = _match_weighted_patterns(normalized_text, EXPLICIT_HIGH_RISK_PATTERNS)
    restriction_density = _clamp01(_policy_term_density(normalized_text, token_count) / 0.05)
    modal_ratio = _imperative_modal_ratio(normalized_text, token_count)

    feature_scores = {
        "pattern_score": _saturating_score(raw_pattern_score, saturation_point=5.5),
        "explicit_high_risk_pattern_score": _saturating_score(explicit_raw_score, saturation_point=2.5),
        "restriction_density_score": restriction_density,
        "modal_ratio_score": _clamp01(modal_ratio / 0.08),
    }

    normalized_score = _clamp01(
        0.52 * feature_scores["pattern_score"]
        + 0.28 * feature_scores["explicit_high_risk_pattern_score"]
        + 0.12 * feature_scores["restriction_density_score"]
        + 0.08 * feature_scores["modal_ratio_score"]
    )

    explanation_parts: list[str] = []
    if matched_patterns:
        explanation_parts.append(_build_pattern_explanation("Refusal-inducing score", matched_patterns))
    if explicit_matches:
        matched_names = ", ".join(pattern["name"] for pattern in explicit_matches[:3])
        explanation_parts.append(f"Explicit high-risk refusal patterns found: {matched_names}.")
    if restriction_density >= 0.5:
        explanation_parts.append("Restriction/safety language density is elevated.")
    if not explanation_parts:
        explanation_parts.append("No strong refusal-inducing language was detected.")

    return {
        "score": round(normalized_score, 4),
        "normalized_score": round(normalized_score, 4),
        "raw_pattern_score": round(raw_pattern_score, 4),
        "matched_patterns": matched_patterns,
        "explicit_high_risk_patterns": explicit_matches,
        "feature_breakdown": {
            **{key: round(value, 4) for key, value in feature_scores.items()},
            "modal_ratio": round(modal_ratio, 4),
            "token_count": token_count,
        },
        "triggered_rules": [pattern["name"] for pattern in matched_patterns + explicit_matches],
        "explanation": " ".join(explanation_parts),
    }


def _perplexity_score(text: str, corpus_stats: Mapping[str, Any] | None) -> tuple[float, float | None]:
    if not corpus_stats:
        return 0.0, None

    perplexity_fn = corpus_stats.get("perplexity_fn")
    if not callable(perplexity_fn):
        return 0.0, None

    ppl_value = float(perplexity_fn(text))
    ppl_mean = corpus_stats.get("perplexity_mean")
    ppl_std = corpus_stats.get("perplexity_std")
    if ppl_mean is None or ppl_std is None:
        return 0.0, ppl_value

    return _zscore_feature(ppl_value, float(ppl_mean), float(ppl_std), use_absolute=False), ppl_value


def score_outlier(text: str, corpus_stats: Mapping[str, Any] | None = None) -> dict[str, Any]:
    normalized_text = text or ""
    tokens = _tokenize(normalized_text)
    lines = _lines(normalized_text)
    sentences = _sentences(normalized_text)

    char_length = len(normalized_text)
    token_count = len(tokens)
    line_count = len(lines)
    sentence_count = len(sentences)
    avg_sentence_length = _safe_div(sum(len(_tokenize(sentence)) for sentence in sentences), max(1, sentence_count))

    modal_ratio = _imperative_modal_ratio(normalized_text, token_count)
    policy_density = _policy_term_density(normalized_text, token_count)
    prompt_marker_count, prompt_marker_density = _prompt_marker_density(normalized_text, line_count)
    instruction_line_count, instruction_format_density = _instruction_format_density(normalized_text, line_count)
    punctuation_density = _punctuation_density(normalized_text)
    repetition_ratio = _repetition_ratio(normalized_text, tokens, lines)
    flow_shift_score, flow_shift_flags = _flow_shift_score(normalized_text)
    semantic_shift_score, semantic_shift_details = _semantic_shift_score(normalized_text)
    perplexity_feature_score, perplexity_value = _perplexity_score(normalized_text, corpus_stats)

    if corpus_stats:
        length_score = _zscore_feature(
            char_length,
            corpus_stats.get("char_length_mean"),
            corpus_stats.get("char_length_std"),
        )
        sentence_length_score = _zscore_feature(
            avg_sentence_length,
            corpus_stats.get("avg_sentence_length_mean"),
            corpus_stats.get("avg_sentence_length_std"),
        )
        modal_score = _zscore_feature(
            modal_ratio,
            corpus_stats.get("modal_ratio_mean"),
            corpus_stats.get("modal_ratio_std"),
            use_absolute=False,
        )
        policy_score = _zscore_feature(
            policy_density,
            corpus_stats.get("policy_term_density_mean"),
            corpus_stats.get("policy_term_density_std"),
            use_absolute=False,
        )
        punctuation_score = _zscore_feature(
            punctuation_density,
            corpus_stats.get("punctuation_density_mean"),
            corpus_stats.get("punctuation_density_std"),
            use_absolute=False,
        )
    else:
        length_score = _fallback_length_score(char_length)
        sentence_length_score = _fallback_sentence_length_score(avg_sentence_length)
        modal_score = _clamp01(modal_ratio / 0.08)
        policy_score = _clamp01(policy_density / 0.05)
        punctuation_score = _clamp01(max(0.0, punctuation_density - 0.18) / 0.18)

    feature_scores = {
        "length_deviation": length_score,
        "sentence_length_deviation": sentence_length_score,
        "modal_ratio": modal_score,
        "policy_term_density": policy_score,
        "prompt_marker_density": _clamp01(prompt_marker_density / 0.12),
        "instruction_format_density": _clamp01(instruction_format_density / 0.30),
        "flow_shift": flow_shift_score,
        "semantic_shift": semantic_shift_score,
        "punctuation_density": punctuation_score,
        "repetition": _clamp01(repetition_ratio / 0.35),
        "perplexity": perplexity_feature_score,
    }

    normalized_score = 0.0
    for feature_name, feature_weight in OUTLIER_FEATURE_WEIGHTS.items():
        normalized_score += feature_weight * feature_scores.get(feature_name, 0.0)
    normalized_score = _clamp01(normalized_score)

    ranked_features = sorted(feature_scores.items(), key=lambda item: item[1], reverse=True)
    top_features = [name for name, value in ranked_features if value >= 0.35][:3]
    if top_features:
        explanation = "Outlier signals are driven by: " + ", ".join(top_features) + "."
    else:
        explanation = "No strong structural outlier signals were detected."

    return {
        "score": round(normalized_score, 4),
        "normalized_score": round(normalized_score, 4),
        "matched_patterns": [],
        "feature_breakdown": {
            **{key: round(value, 4) for key, value in feature_scores.items()},
            "char_length": char_length,
            "token_count": token_count,
            "line_count": line_count,
            "sentence_count": sentence_count,
            "avg_sentence_length": round(avg_sentence_length, 4),
            "modal_ratio_raw": round(modal_ratio, 4),
            "policy_term_density_raw": round(policy_density, 4),
            "prompt_marker_count": prompt_marker_count,
            "instruction_like_line_count": instruction_line_count,
            "midstream_prompt_marker": flow_shift_flags["midstream_prompt_marker"],
            "discourse_shift": flow_shift_flags["discourse_shift"],
            **semantic_shift_details,
            "punctuation_density_raw": round(punctuation_density, 4),
            "repetition_ratio_raw": round(repetition_ratio, 4),
            "perplexity_value": round(perplexity_value, 4) if perplexity_value is not None else None,
        },
        "triggered_rules": top_features,
        "explanation": explanation,
    }


def _feature_stats(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    if len(values) == 1:
        return float(values[0]), 1.0
    std_value = pstdev(values)
    return float(mean(values)), float(std_value if std_value > 1e-9 else 1.0)


def estimate_corpus_stats(texts: Sequence[str]) -> dict[str, float]:
    char_lengths: list[float] = []
    avg_sentence_lengths: list[float] = []
    modal_ratios: list[float] = []
    policy_densities: list[float] = []
    punctuation_densities: list[float] = []

    for text in texts:
        tokens = _tokenize(text)
        sentences = _sentences(text)
        char_lengths.append(float(len(text)))
        avg_sentence_lengths.append(
            _safe_div(sum(len(_tokenize(sentence)) for sentence in sentences), max(1, len(sentences)))
        )
        modal_ratios.append(_imperative_modal_ratio(text, len(tokens)))
        policy_densities.append(_policy_term_density(text, len(tokens)))
        punctuation_densities.append(_punctuation_density(text))

    char_mean, char_std = _feature_stats(char_lengths)
    sent_mean, sent_std = _feature_stats(avg_sentence_lengths)
    modal_mean, modal_std = _feature_stats(modal_ratios)
    policy_mean, policy_std = _feature_stats(policy_densities)
    punct_mean, punct_std = _feature_stats(punctuation_densities)

    return {
        "char_length_mean": char_mean,
        "char_length_std": char_std,
        "avg_sentence_length_mean": sent_mean,
        "avg_sentence_length_std": sent_std,
        "modal_ratio_mean": modal_mean,
        "modal_ratio_std": modal_std,
        "policy_term_density_mean": policy_mean,
        "policy_term_density_std": policy_std,
        "punctuation_density_mean": punct_mean,
        "punctuation_density_std": punct_std,
    }
