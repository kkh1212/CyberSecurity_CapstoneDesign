from __future__ import annotations

import argparse
from pathlib import Path

from semantic_muterag_detector.calibration import (
    calibrate_thresholds,
    find_doc_by_id,
    read_questions,
)
from semantic_muterag_detector.classifier import classify_features
from semantic_muterag_detector.config import DetectorConfig, Thresholds
from semantic_muterag_detector.embeddings import EmbeddingModel
from semantic_muterag_detector.features import score_chunk
from semantic_muterag_detector.io_utils import read_text, write_csv, write_json


CORPUS_DIRS = {
    "normal": "experiment_normal",
    "direct": "experiment_direct",
    "muted": "experiment_muted",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the semantic MutedRAG detector on v3 corpora.")
    parser.add_argument("--experiments-root", type=Path, default=Path("data/experiments_v3"))
    parser.add_argument("--questions", type=Path, default=Path("data/experiments_v3/questions/v3_questions.csv"))
    parser.add_argument("--thresholds", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("outputs/semantic_muterag_detector/eval_v3"))
    parser.add_argument("--embedding-backend", default="auto", choices=["auto", "sentence-transformers", "st", "tfidf", "hashing"])
    parser.add_argument(
        "--corpus-types",
        default="normal,muted",
        help="Comma-separated subset of normal,direct,muted.",
    )
    return parser.parse_args()


def load_or_calibrate_thresholds(
    args: argparse.Namespace,
    questions,
    embedder: EmbeddingModel,
    config: DetectorConfig,
) -> Thresholds:
    if args.thresholds:
        return Thresholds.from_json(args.thresholds)

    normal_dir = args.experiments_root / CORPUS_DIRS["normal"]
    normal_features = []
    for question in questions:
        doc_path = find_doc_by_id(normal_dir, question.doc_id)
        normal_features.append(
            score_chunk(
                query=question.question,
                chunk_text=read_text(doc_path),
                question_id=question.qid,
                doc_id=question.doc_id,
                source_doc=doc_path.name,
                embedder=embedder,
                config=config,
            )
        )
    return calibrate_thresholds(normal_features)


def summarize(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["corpus_type"], []).append(row)

    summary = []
    for corpus_type, items in sorted(grouped.items()):
        total = len(items)
        muted_count = sum(1 for item in items if item["verdict"] == "muted_candidate")
        direct_count = sum(1 for item in items if item["verdict"] == "direct_candidate")
        attack_count = muted_count + direct_count
        suspect_count = sum(1 for item in items if item["verdict"] == "suspect")
        clean_count = sum(1 for item in items if item["verdict"] == "clean")
        is_attack = corpus_type in {"direct", "muted"}
        summary.append(
            {
                "corpus_type": corpus_type,
                "total": total,
                "clean_count": clean_count,
                "suspect_count": suspect_count,
                "direct_candidate_count": direct_count,
                "muted_candidate_count": muted_count,
                "attack_candidate_count": attack_count,
                "flagged_count": attack_count + suspect_count,
                "direct_candidate_rate": round(direct_count / total, 4) if total else 0.0,
                "muted_candidate_rate": round(muted_count / total, 4) if total else 0.0,
                "attack_candidate_rate": round(attack_count / total, 4) if total else 0.0,
                "flagged_rate": round((attack_count + suspect_count) / total, 4) if total else 0.0,
                "metric_name": "detection_rate" if is_attack else "false_positive_rate",
                "metric_value": round(attack_count / total, 4) if total else 0.0,
            }
        )
    return summary


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    questions = read_questions(args.questions)
    requested = [item.strip() for item in args.corpus_types.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(CORPUS_DIRS))
    if unknown:
        raise SystemExit(f"Unknown corpus types: {', '.join(unknown)}")

    config = DetectorConfig(embedding_backend=args.embedding_backend)
    embedder = EmbeddingModel(args.embedding_backend)
    thresholds = load_or_calibrate_thresholds(args, questions, embedder, config)

    rows = []
    for corpus_type in requested:
        corpus_dir = args.experiments_root / CORPUS_DIRS[corpus_type]
        for question in questions:
            doc_path = find_doc_by_id(corpus_dir, question.doc_id)
            features = score_chunk(
                query=question.question,
                chunk_text=read_text(doc_path),
                question_id=question.qid,
                doc_id=question.doc_id,
                source_doc=doc_path.name,
                embedder=embedder,
                config=config,
            )
            verdict = classify_features(features, thresholds)
            row = {
                "corpus_type": corpus_type,
                **features.to_row(),
                **verdict.to_row(),
            }
            rows.append(row)

    summary = summarize(rows)
    write_csv(args.out / "chunk_scores.csv", rows)
    write_csv(args.out / "summary.csv", summary)
    thresholds.write_json(args.out / "thresholds_used.json")
    write_json(
        args.out / "run_config.json",
        {
            "experiments_root": str(args.experiments_root),
            "questions": str(args.questions),
            "thresholds_input": str(args.thresholds) if args.thresholds else None,
            "embedding_backend_requested": args.embedding_backend,
            "embedding_backend_resolved": embedder.resolved_backend,
            "corpus_types": requested,
            "thresholds": thresholds.to_dict(),
        },
    )
    print(f"[eval] scores -> {args.out / 'chunk_scores.csv'}")
    print(f"[eval] summary -> {args.out / 'summary.csv'}")
    print(f"[eval] backend -> {embedder.resolved_backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
