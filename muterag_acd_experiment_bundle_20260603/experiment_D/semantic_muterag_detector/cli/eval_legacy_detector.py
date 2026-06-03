from __future__ import annotations

import argparse
import csv
from pathlib import Path

from detector.detector import analyze_chunk


CORPUS_DIRS = {
    "normal": "experiment_normal",
    "direct": "experiment_direct",
    "muted": "experiment_muted",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the legacy detector on v3 corpora.")
    parser.add_argument("--experiments-root", type=Path, default=Path("data/experiments_v3"))
    parser.add_argument("--out", type=Path, default=Path("reports/legacy_detector_baseline.csv"))
    parser.add_argument(
        "--corpus-types",
        default="normal,direct,muted",
        help="Comma-separated subset of normal,direct,muted.",
    )
    parser.add_argument("--profile", default="balanced")
    return parser.parse_args()


def _prediction(risk_level: str, action: str) -> str:
    if action in {"review", "quarantine", "block"}:
        return "attack"
    if risk_level.lower() in {"medium", "high", "critical"}:
        return "attack"
    return "benign"


def _outcome(label: str, prediction: str) -> str:
    if label == "attack" and prediction == "attack":
        return "TP"
    if label == "attack":
        return "FN"
    if prediction == "attack":
        return "FP"
    return "TN"


def main() -> int:
    args = parse_args()
    requested = [item.strip() for item in args.corpus_types.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(CORPUS_DIRS))
    if unknown:
        raise SystemExit(f"Unknown corpus types: {', '.join(unknown)}")

    rows: list[dict[str, str]] = []
    for corpus_type in requested:
        folder = args.experiments_root / CORPUS_DIRS[corpus_type]
        if not folder.exists():
            raise SystemExit(f"Corpus directory not found: {folder}")
        for path in sorted(folder.glob("*.txt")):
            text = path.read_text(encoding="utf-8-sig")
            analysis = analyze_chunk(text, profile=args.profile)
            risk_level = str(getattr(analysis, "risk_level", ""))
            action = str(getattr(analysis, "recommended_action", ""))
            prediction = _prediction(risk_level, action)
            label = "attack" if corpus_type in {"direct", "muted"} else "benign"
            rows.append(
                {
                    "corpus_type": corpus_type,
                    "source_doc": path.name,
                    "label": label,
                    "risk_level": risk_level,
                    "adjusted_risk": f"{float(getattr(analysis, 'adjusted_risk', 0.0)):.6f}",
                    "recommended_action": action,
                    "prediction": prediction,
                    "outcome": _outcome(label, prediction),
                }
            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[legacy] baseline -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
