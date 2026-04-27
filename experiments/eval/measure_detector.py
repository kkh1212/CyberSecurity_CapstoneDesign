"""
Detector TP/FP/TN/FN evaluator for Study C.

Prediction:
  positive = detector action is review or quarantine.

Ground truth:
  attack = staged document filename contains one of the experiment attack suffixes.

Outputs:
  detector_chunk_summary.csv
  detector_document_summary.csv
  detector_document_detail.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ATTACK_DOC_RE = re.compile(
    r"__(direct|indirect_explicit|indirect_mixed|multilingual)(?:__mutedrag_dup\d+)?\.(?:txt|docx)$"
)


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def precision(self) -> float | None:
        denom = self.tp + self.fp
        return self.tp / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.tp + self.fn
        return self.tp / denom if denom else None

    @property
    def fpr(self) -> float | None:
        denom = self.fp + self.tn
        return self.fp / denom if denom else None

    @property
    def accuracy(self) -> float | None:
        return (self.tp + self.tn) / self.total if self.total else None

    @property
    def f1(self) -> float | None:
        precision = self.precision
        recall = self.recall
        if precision is None or recall is None or precision + recall == 0:
            return None
        return 2 * precision * recall / (precision + recall)


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.1f}%"


def is_attack_doc(document_id: str) -> bool:
    return bool(ATTACK_DOC_RE.search(Path(document_id).name))


def iter_summary_files(index_dir: Path) -> Iterable[Path]:
    yield from sorted(index_dir.rglob("detector_summary.json"))


def read_summary(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def update_confusion(confusion: Confusion, truth_attack: bool, predicted_positive: bool, count: int = 1) -> None:
    if truth_attack and predicted_positive:
        confusion.tp += count
    elif truth_attack and not predicted_positive:
        confusion.fn += count
    elif not truth_attack and predicted_positive:
        confusion.fp += count
    else:
        confusion.tn += count


def write_summary_csv(path: Path, condition: str, unit: str, confusion: Confusion) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "condition",
                "unit",
                "total",
                "TP",
                "FP",
                "TN",
                "FN",
                "precision",
                "recall",
                "fpr",
                "accuracy",
                "f1",
            ]
        )
        writer.writerow(
            [
                condition,
                unit,
                confusion.total,
                confusion.tp,
                confusion.fp,
                confusion.tn,
                confusion.fn,
                pct(confusion.precision),
                pct(confusion.recall),
                pct(confusion.fpr),
                pct(confusion.accuracy),
                pct(confusion.f1),
            ]
        )


def evaluate(index_dir: Path, out_dir: Path, condition: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_confusion = Confusion()
    doc_confusion = Confusion()
    detail_rows = []

    for summary_path in iter_summary_files(index_dir):
        summary = read_summary(summary_path)
        domain = str(summary.get("domain", summary_path.parent.name))
        for doc in summary.get("document_summaries", []):
            document_id = str(doc.get("document_id", ""))
            total_chunks = int(doc.get("total_chunks", 0) or 0)
            review_chunks = int(doc.get("review_required", 0) or 0)
            quarantine_chunks = int(doc.get("quarantined", 0) or 0)
            positive_chunks = review_chunks + quarantine_chunks
            negative_chunks = max(0, total_chunks - positive_chunks)

            truth_attack = is_attack_doc(document_id)
            predicted_doc_positive = positive_chunks > 0

            update_confusion(chunk_confusion, truth_attack, True, positive_chunks)
            update_confusion(chunk_confusion, truth_attack, False, negative_chunks)
            update_confusion(doc_confusion, truth_attack, predicted_doc_positive)

            detail_rows.append(
                {
                    "condition": condition,
                    "domain": domain,
                    "document_id": document_id,
                    "truth": "attack" if truth_attack else "benign",
                    "predicted": "positive" if predicted_doc_positive else "negative",
                    "total_chunks": total_chunks,
                    "positive_chunks": positive_chunks,
                    "review_chunks": review_chunks,
                    "quarantine_chunks": quarantine_chunks,
                    "highest_risk": doc.get("highest_risk", ""),
                }
            )

    with (out_dir / "detector_document_detail.csv").open("w", newline="", encoding="utf-8") as file:
        fieldnames = [
            "condition",
            "domain",
            "document_id",
            "truth",
            "predicted",
            "total_chunks",
            "positive_chunks",
            "review_chunks",
            "quarantine_chunks",
            "highest_risk",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(detail_rows)

    write_summary_csv(out_dir / "detector_chunk_summary.csv", condition, "chunk", chunk_confusion)
    write_summary_csv(out_dir / "detector_document_summary.csv", condition, "document", doc_confusion)

    print(f"[measure_detector] {condition}")
    print(
        "  chunk   "
        f"TP={chunk_confusion.tp} FP={chunk_confusion.fp} "
        f"TN={chunk_confusion.tn} FN={chunk_confusion.fn} "
        f"precision={pct(chunk_confusion.precision)} recall={pct(chunk_confusion.recall)} "
        f"FPR={pct(chunk_confusion.fpr)}"
    )
    print(
        "  document "
        f"TP={doc_confusion.tp} FP={doc_confusion.fp} "
        f"TN={doc_confusion.tn} FN={doc_confusion.fn} "
        f"precision={pct(doc_confusion.precision)} recall={pct(doc_confusion.recall)} "
        f"FPR={pct(doc_confusion.fpr)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure detector TP/FP/TN/FN.")
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--condition", required=True)
    args = parser.parse_args()

    evaluate(args.index_dir, args.out_dir, args.condition)


if __name__ == "__main__":
    main()
