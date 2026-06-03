from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class QuestionRecord:
    qid: str
    doc_id: str
    filename: str
    question: str


@dataclass(frozen=True)
class ChunkFeatures:
    question_id: str
    doc_id: str
    source_doc: str
    sentence_count: int
    anchor_score: float
    alignment_gap: float
    alignment_spread: float
    semantic_break_score: float
    directional_drop_score: float
    payload_isolation: float
    prototype_similarity: float
    redirect_similarity: float
    refusal_similarity: float
    off_query_prototype_score: float
    mean_similarity: float
    min_similarity: float
    max_similarity: float
    p20_similarity: float
    p80_similarity: float
    max_break_index: int
    similarities: list[float] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["similarities"] = "|".join(f"{value:.4f}" for value in self.similarities)
        row["sentences"] = " || ".join(self.sentences)
        return row


@dataclass(frozen=True)
class Verdict:
    verdict: str
    muted_score: float
    reason: str
    anchor_high: bool
    gap_med: bool
    gap_high: bool
    break_med: bool
    break_high: bool
    isolation_med: bool
    isolation_high: bool
    prototype_med: bool
    prototype_high: bool
    redirect_high: bool
    refusal_high: bool

    def to_row(self) -> dict[str, Any]:
        return asdict(self)
