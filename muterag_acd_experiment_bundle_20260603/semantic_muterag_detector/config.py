from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import json


@dataclass(frozen=True)
class DetectorConfig:
    """Runtime configuration for the semantic detector."""

    embedding_backend: str = "auto"
    min_sentence_chars: int = 12
    max_sentences: int = 24
    top_fraction: float = 0.30
    hashing_dim: int = 4096


@dataclass(frozen=True)
class Thresholds:
    """Decision thresholds.

    The defaults are conservative starting points. For experiments, prefer
    thresholds produced by calibration on the normal corpus.
    """

    anchor_min: float = 0.12
    gap_med: float = 0.12
    gap_high: float = 0.20
    break_med: float = 0.10
    break_high: float = 0.18
    isolation_med: float = 0.25
    isolation_high: float = 0.35
    prototype_med: float = 0.18
    prototype_high: float = 0.28
    redirect_med: float = 0.18
    redirect_high: float = 0.28
    refusal_med: float = 0.18
    refusal_high: float = 0.28
    calibration_note: str = "default"

    @classmethod
    def from_json(cls, path: str | Path) -> "Thresholds":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in data.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
