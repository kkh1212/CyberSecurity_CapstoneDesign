from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .risk import classify_risk, compute_risk


@dataclass
class MutedRAGDetector:
    corpus_stats: Mapping[str, Any] | None = None
    weights: Mapping[str, float] | None = None
    profile: str | None = None

    def analyze(self, text: str) -> dict[str, Any]:
        score_dict = compute_risk(
            text,
            corpus_stats=self.corpus_stats,
            weights=self.weights,
            profile=self.profile,
        )
        return classify_risk(score_dict)


def analyze_chunk(
    text: str,
    corpus_stats: Mapping[str, Any] | None = None,
    weights: Mapping[str, float] | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    detector = MutedRAGDetector(corpus_stats=corpus_stats, weights=weights, profile=profile)
    return detector.analyze(text)
