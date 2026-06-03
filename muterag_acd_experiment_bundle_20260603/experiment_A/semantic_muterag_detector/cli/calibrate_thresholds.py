from __future__ import annotations

import argparse
from pathlib import Path

from semantic_muterag_detector.calibration import (
    calibrate_thresholds,
    find_doc_by_id,
    read_questions,
)
from semantic_muterag_detector.config import DetectorConfig
from semantic_muterag_detector.embeddings import EmbeddingModel
from semantic_muterag_detector.features import score_chunk
from semantic_muterag_detector.io_utils import read_text, write_csv, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate semantic MutedRAG detector thresholds.")
    parser.add_argument("--normal-dir", type=Path, default=Path("data/experiments_v3/experiment_normal"))
    parser.add_argument("--questions", type=Path, default=Path("data/experiments_v3/questions/v3_questions.csv"))
    parser.add_argument("--out", type=Path, default=Path("outputs/semantic_muterag_detector/calibration"))
    parser.add_argument("--embedding-backend", default="auto", choices=["auto", "sentence-transformers", "st", "tfidf", "hashing"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    questions = read_questions(args.questions)
    config = DetectorConfig(embedding_backend=args.embedding_backend)
    embedder = EmbeddingModel(args.embedding_backend)

    features = []
    rows = []
    for question in questions:
        doc_path = find_doc_by_id(args.normal_dir, question.doc_id)
        item = score_chunk(
            query=question.question,
            chunk_text=read_text(doc_path),
            question_id=question.qid,
            doc_id=question.doc_id,
            source_doc=doc_path.name,
            embedder=embedder,
            config=config,
        )
        features.append(item)
        rows.append(item.to_row())

    thresholds = calibrate_thresholds(features)
    thresholds.write_json(args.out / "thresholds.json")
    write_csv(args.out / "normal_feature_scores.csv", rows)
    write_json(
        args.out / "calibration_summary.json",
        {
            "normal_dir": str(args.normal_dir),
            "questions": str(args.questions),
            "embedding_backend_requested": args.embedding_backend,
            "embedding_backend_resolved": embedder.resolved_backend,
            "question_count": len(questions),
            "thresholds": thresholds.to_dict(),
        },
    )
    print(f"[calibrate] thresholds -> {args.out / 'thresholds.json'}")
    print(f"[calibrate] scores -> {args.out / 'normal_feature_scores.csv'}")
    print(f"[calibrate] backend -> {embedder.resolved_backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
