"""Offline evaluation of the semantic MutedRAG detector on the v5 corpora.

No external guardrail, no LLM. For each question it scores the (in-place poisoned)
target document in every corpus and reports per-corpus detection / false-positive
rates. Mirrors semantic_muterag_detector.cli.eval_v3_corpus but understands the
v5 blackbox/whitebox corpus split and the v5 questions schema.

Granularity:
  --granularity doc   : score the whole document as one text (loop4 style)
  --granularity chunk : split the document (CHUNK_SIZE/OVERLAP) and score each
                        chunk; a document counts as detected if ANY chunk is an
                        attack candidate. Concentrates the injected-payload signal.

Run from the repo root, e.g.:
    .venv/bin/python -m scripts.eval_detector_v5 --embedding-backend hashing --granularity chunk
"""
from __future__ import annotations

import argparse
from pathlib import Path

from semantic_muterag_detector.calibration import (
    CalibrationPolicy,
    calibrate_thresholds,
    find_doc_by_id,
    read_questions,
)
from semantic_muterag_detector.classifier import classify_features
from semantic_muterag_detector.config import DetectorConfig, Thresholds
from semantic_muterag_detector.embeddings import EmbeddingModel
from semantic_muterag_detector.features import score_chunk
from semantic_muterag_detector.io_utils import read_text, write_csv, write_json
from src.chunking import split_text


CORPUS_DIRS = {
    "normal": "experiment_normal",
    "direct_blackbox": "experiment_direct_blackbox",
    "direct_whitebox": "experiment_direct_whitebox",
    "muted_blackbox": "experiment_muted_blackbox",
    "muted_whitebox": "experiment_muted_whitebox",
}

# Verdict severity for chunk -> document aggregation.
_SEVERITY = {"clean": 0, "suspect": 1, "muted_candidate": 2, "direct_candidate": 3}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Offline detector eval on v5 corpora.")
    p.add_argument("--experiments-root", type=Path, default=Path("data/experiments_v5"))
    p.add_argument("--questions", type=Path, default=Path("data/experiments_v5/questions/v5_questions.csv"))
    p.add_argument("--thresholds", type=Path, default=None, help="JSON thresholds; else calibrate on normal.")
    p.add_argument("--out", type=Path, default=Path("outputs/semantic_muterag_detector/eval_v5"))
    p.add_argument("--embedding-backend", default="hashing",
                   choices=["auto", "sentence-transformers", "st", "tfidf", "hashing"])
    p.add_argument("--corpus-types", default=",".join(CORPUS_DIRS),
                   help="Comma-separated subset of " + ",".join(CORPUS_DIRS))
    p.add_argument("--granularity", default="doc", choices=["doc", "chunk"])
    p.add_argument("--med-pct", type=float, default=90.0, help="medium-threshold percentile on normal")
    p.add_argument("--high-pct", type=float, default=97.5, help="high-threshold percentile on normal")
    p.add_argument("--max-questions", type=int, default=0, help="0 = all")
    return p.parse_args()


def doc_texts(doc_path: Path, granularity: str) -> list[str]:
    text = read_text(doc_path)
    if granularity == "doc":
        return [text]
    chunks = [c[0] for c in split_text(text) if c and c[0].strip()]
    return chunks or [text]


def collect_normal_features(args, questions, embedder, config):
    normal_dir = args.experiments_root / CORPUS_DIRS["normal"]
    feats = []
    for q in questions:
        try:
            doc_path = find_doc_by_id(normal_dir, q.doc_id)
        except FileNotFoundError:
            continue
        for i, t in enumerate(doc_texts(doc_path, args.granularity)):
            feats.append(score_chunk(
                query=q.question, chunk_text=t, question_id=q.qid, doc_id=q.doc_id,
                source_doc=f"{doc_path.name}#{i}", embedder=embedder, config=config,
            ))
    return feats


def summarize(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["corpus_type"], []).append(r)
    out = []
    for ct, items in sorted(grouped.items()):
        total = len(items)
        muted = sum(1 for i in items if i["verdict"] == "muted_candidate")
        direct = sum(1 for i in items if i["verdict"] == "direct_candidate")
        suspect = sum(1 for i in items if i["verdict"] == "suspect")
        clean = sum(1 for i in items if i["verdict"] == "clean")
        attack = muted + direct
        is_attack = ct != "normal"
        out.append({
            "corpus_type": ct, "total": total, "clean": clean, "suspect": suspect,
            "direct_candidate": direct, "muted_candidate": muted, "attack_candidate": attack,
            "direct_rate": round(direct / total, 4) if total else 0.0,
            "muted_rate": round(muted / total, 4) if total else 0.0,
            "attack_candidate_rate": round(attack / total, 4) if total else 0.0,
            "flagged_rate": round((attack + suspect) / total, 4) if total else 0.0,
            "metric_name": "detection_rate" if is_attack else "false_positive_rate",
            "metric_value": round(attack / total, 4) if total else 0.0,
        })
    return out


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    questions = read_questions(args.questions)
    if args.max_questions:
        questions = questions[: args.max_questions]
    requested = [t.strip() for t in args.corpus_types.split(",") if t.strip()]
    unknown = sorted(set(requested) - set(CORPUS_DIRS))
    if unknown:
        raise SystemExit(f"Unknown corpus types: {', '.join(unknown)}")

    config = DetectorConfig(embedding_backend=args.embedding_backend)
    embedder = EmbeddingModel(args.embedding_backend)
    policy = CalibrationPolicy(medium_percentile=args.med_pct, high_percentile=args.high_pct)
    thresholds = (Thresholds.from_json(args.thresholds) if args.thresholds
                  else calibrate_thresholds(collect_normal_features(args, questions, embedder, config), policy=policy))

    rows = []
    for ct in requested:
        corpus_dir = args.experiments_root / CORPUS_DIRS[ct]
        for q in questions:
            try:
                doc_path = find_doc_by_id(corpus_dir, q.doc_id)
            except FileNotFoundError:
                continue
            best = None  # (severity, features_row, verdict)
            n_flagged = 0
            texts = doc_texts(doc_path, args.granularity)
            for i, t in enumerate(texts):
                feats = score_chunk(
                    query=q.question, chunk_text=t, question_id=q.qid, doc_id=q.doc_id,
                    source_doc=f"{doc_path.name}#{i}", embedder=embedder, config=config,
                )
                v = classify_features(feats, thresholds)
                sev = _SEVERITY.get(v.verdict, 0)
                if sev >= _SEVERITY["muted_candidate"]:
                    n_flagged += 1
                if best is None or sev > best[0]:
                    best = (sev, feats.to_row(), v.to_row())
            rows.append({
                "corpus_type": ct, "doc_id": q.doc_id, "n_chunks": len(texts),
                "n_attack_chunks": n_flagged, **best[1], **best[2],
            })

    summary = summarize(rows)
    write_csv(args.out / "doc_scores.csv", rows)
    write_csv(args.out / "summary.csv", summary)
    thresholds.write_json(args.out / "thresholds_used.json")
    write_json(args.out / "run_config.json", {
        "experiments_root": str(args.experiments_root), "questions": str(args.questions),
        "thresholds_input": str(args.thresholds) if args.thresholds else None,
        "embedding_backend_requested": args.embedding_backend,
        "embedding_backend_resolved": embedder.resolved_backend,
        "granularity": args.granularity,
        "corpus_types": requested, "thresholds": thresholds.to_dict(),
    })
    print(f"[eval-v5] backend={embedder.resolved_backend} granularity={args.granularity}")
    print(f"[eval-v5] summary -> {args.out / 'summary.csv'}")
    for s in summary:
        print(f"  {s['corpus_type']:18} {s['metric_name']:20} {s['metric_value']:.3f}  "
              f"(direct={s['direct_rate']}, muted={s['muted_rate']}, flagged={s['flagged_rate']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
