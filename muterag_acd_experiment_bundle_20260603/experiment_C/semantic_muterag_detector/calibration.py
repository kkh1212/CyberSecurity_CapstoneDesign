from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path
from statistics import quantiles
from typing import Iterable

from .config import Thresholds
from .schemas import ChunkFeatures, QuestionRecord


@dataclass(frozen=True)
class CalibrationPolicy:
    medium_percentile: float = 90.0
    high_percentile: float = 97.5
    anchor_percentile: float = 5.0
    anchor_scale: float = 0.80
    gap_med_floor: float = 0.12
    gap_high_floor: float = 0.20
    break_med_floor: float = 0.10
    break_high_floor: float = 0.18
    isolation_med_floor: float = 0.25
    isolation_high_floor: float = 0.35
    prototype_med_floor: float = 0.18
    prototype_high_floor: float = 0.28


def percentile(values: Iterable[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * (q / 100.0)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def calibrate_thresholds(
    normal_features: list[ChunkFeatures],
    *,
    policy: CalibrationPolicy | None = None,
) -> Thresholds:
    cfg = policy or CalibrationPolicy()
    anchors = [item.anchor_score for item in normal_features]
    gaps = [item.alignment_gap for item in normal_features]
    breaks = [item.semantic_break_score for item in normal_features]
    isolations = [item.payload_isolation for item in normal_features]
    prototypes = [item.prototype_similarity for item in normal_features]
    redirects = [item.redirect_similarity for item in normal_features]
    refusals = [item.refusal_similarity for item in normal_features]

    anchor_min = max(0.02, percentile(anchors, cfg.anchor_percentile) * cfg.anchor_scale)
    return Thresholds(
        anchor_min=anchor_min,
        gap_med=max(cfg.gap_med_floor, percentile(gaps, cfg.medium_percentile)),
        gap_high=max(cfg.gap_high_floor, percentile(gaps, cfg.high_percentile)),
        break_med=max(cfg.break_med_floor, percentile(breaks, cfg.medium_percentile)),
        break_high=max(cfg.break_high_floor, percentile(breaks, cfg.high_percentile)),
        isolation_med=max(cfg.isolation_med_floor, percentile(isolations, cfg.medium_percentile)),
        isolation_high=max(cfg.isolation_high_floor, percentile(isolations, cfg.high_percentile)),
        prototype_med=max(cfg.prototype_med_floor, percentile(prototypes, cfg.medium_percentile)),
        prototype_high=max(cfg.prototype_high_floor, percentile(prototypes, cfg.high_percentile)),
        redirect_med=max(cfg.prototype_med_floor, percentile(redirects, cfg.medium_percentile)),
        redirect_high=max(cfg.prototype_high_floor, percentile(redirects, cfg.high_percentile)),
        refusal_med=max(cfg.prototype_med_floor, percentile(refusals, cfg.medium_percentile)),
        refusal_high=max(cfg.prototype_high_floor, percentile(refusals, cfg.high_percentile)),
        calibration_note=(
            f"normal p{cfg.medium_percentile:.0f}/p{cfg.high_percentile:.0f}; "
            f"n={len(normal_features)}"
        ),
    )


def read_questions(path: str | Path) -> list[QuestionRecord]:
    rows: list[QuestionRecord] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fp:
        for row in csv.DictReader(fp):
            filename = (
                row.get("filename", "")
                or row.get("target_doc", "")
                or row.get("target_filename", "")
            ).strip()
            doc_id = (
                row.get("doc_id", "")
                or row.get("target_doc_id", "")
                or Path(filename).stem
            ).strip()
            rows.append(
                QuestionRecord(
                    qid=(row.get("qid", "") or row.get("question_id", "")).strip(),
                    doc_id=doc_id,
                    filename=filename,
                    question=row.get("question", "").strip(),
                )
            )
    return rows


def find_doc_by_id(corpus_dir: str | Path, doc_id: str) -> Path:
    root = Path(corpus_dir)
    matches = sorted(root.glob(f"{doc_id}_*.txt"))
    if not matches:
        matches = sorted(path for path in root.glob("*.txt") if path.stem == doc_id)
    if not matches:
        raise FileNotFoundError(f"No document for doc_id={doc_id!r} in {root}")
    return matches[0]
