from __future__ import annotations

import argparse
from pathlib import Path

from semantic_muterag_detector.classifier import classify_features
from semantic_muterag_detector.config import DetectorConfig, Thresholds
from semantic_muterag_detector.embeddings import EmbeddingModel
from semantic_muterag_detector.features import score_chunk
from semantic_muterag_detector.io_utils import read_text, write_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score one or more text files with the semantic detector.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--files", nargs="+", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--embedding-backend", default="auto", choices=["auto", "sentence-transformers", "st", "tfidf", "hashing"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = DetectorConfig(embedding_backend=args.embedding_backend)
    embedder = EmbeddingModel(args.embedding_backend)
    thresholds = Thresholds.from_json(args.thresholds) if args.thresholds else Thresholds()

    rows = []
    for path in args.files:
        features = score_chunk(
            query=args.query,
            chunk_text=read_text(path),
            source_doc=path.name,
            embedder=embedder,
            config=config,
        )
        verdict = classify_features(features, thresholds)
        rows.append({**features.to_row(), **verdict.to_row()})

    if args.out:
        write_csv(args.out, rows)
        print(f"[score] rows -> {args.out}")
    else:
        for row in rows:
            print(
                f"{row['source_doc']}: verdict={row['verdict']} "
                f"score={float(row['muted_score']):.4f} "
                f"anchor={float(row['anchor_score']):.4f} "
                f"gap={float(row['alignment_gap']):.4f} "
                f"break={float(row['semantic_break_score']):.4f} "
                f"isolation={float(row['payload_isolation']):.4f}"
            )
    print(f"[score] backend -> {embedder.resolved_backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
