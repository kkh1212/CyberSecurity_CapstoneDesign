from __future__ import annotations

from .config import Thresholds
from .schemas import ChunkFeatures, Verdict


def _ratio(value: float, threshold: float) -> float:
    if threshold <= 0:
        return 0.0
    return max(0.0, min(1.0, value / threshold))


def classify_features(features: ChunkFeatures, thresholds: Thresholds | None = None) -> Verdict:
    """Classify one feature vector.

    The logic is intentionally structure-based:
    a MutedRAG candidate must contain a query anchor and a semantic split.
    """

    th = thresholds or Thresholds()
    anchor_high = features.anchor_score >= th.anchor_min
    gap_med = features.alignment_gap >= th.gap_med
    gap_high = features.alignment_gap >= th.gap_high
    break_med = features.semantic_break_score >= th.break_med
    break_high = features.semantic_break_score >= th.break_high
    isolation_med = features.payload_isolation >= th.isolation_med
    isolation_high = features.payload_isolation >= th.isolation_high
    prototype_med = features.prototype_similarity >= th.prototype_med
    prototype_high = features.prototype_similarity >= th.prototype_high
    redirect_high = features.redirect_similarity >= th.redirect_high
    refusal_high = features.refusal_similarity >= th.refusal_high
    redirect_anchor = features.anchor_score >= (th.anchor_min * 0.85)

    gap_component = _ratio(features.alignment_gap, th.gap_high)
    break_component = _ratio(features.semantic_break_score, th.break_high)
    isolation_component = _ratio(features.payload_isolation, th.isolation_high)
    muted_score = (
        0.45 * gap_component
        + 0.30 * max(break_component, _ratio(features.directional_drop_score, th.break_high))
        + 0.15 * isolation_component
        + 0.10 * _ratio(features.prototype_similarity, th.prototype_high)
    )
    if not anchor_high:
        muted_score *= 0.35

    semantic_votes = sum([gap_med, break_med, isolation_med])

    if refusal_high and anchor_high:
        verdict = "direct_candidate"
        reason = "refusal_prototype_high + query anchor"
    elif redirect_high and (semantic_votes >= 1 or redirect_anchor):
        verdict = "muted_candidate"
        if semantic_votes >= 1:
            reason = "redirect_prototype_high + semantic split support"
        else:
            reason = "redirect_prototype_high + loose query anchor"
    elif anchor_high and gap_high and isolation_high:
        verdict = "muted_candidate"
        reason = "anchor_high + gap_high + payload_isolation_high"
    elif anchor_high and semantic_votes >= 2:
        verdict = "suspect"
        reason = "anchor_high + multiple medium semantic split signals"
    else:
        verdict = "clean"
        reason = "semantic split conditions not met"

    return Verdict(
        verdict=verdict,
        muted_score=float(muted_score),
        reason=reason,
        anchor_high=anchor_high,
        gap_med=gap_med,
        gap_high=gap_high,
        break_med=break_med,
        break_high=break_high,
        isolation_med=isolation_med,
        isolation_high=isolation_high,
        prototype_med=prototype_med,
        prototype_high=prototype_high,
        redirect_high=redirect_high,
        refusal_high=refusal_high,
    )
