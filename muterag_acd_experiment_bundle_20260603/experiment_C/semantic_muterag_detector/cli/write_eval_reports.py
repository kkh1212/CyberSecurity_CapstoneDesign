from __future__ import annotations

import argparse
import csv
from pathlib import Path


CANDIDATE_VERDICTS = {"direct_candidate", "muted_candidate"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _strict_outcome(corpus_type: str, verdict: str) -> str:
    predicted_attack = verdict in CANDIDATE_VERDICTS
    is_attack = corpus_type in {"direct", "muted"}
    if is_attack and predicted_attack:
        return "TP"
    if is_attack and not predicted_attack:
        return "FN"
    if not is_attack and predicted_attack:
        return "FP"
    return "TN"


def _rate(num: int, den: int) -> float:
    return 0.0 if den == 0 else num / den


def _collect_loop_summaries(loop_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for summary_path in sorted(loop_root.glob("loop*_eval*/summary.csv")):
        loop_id = summary_path.parent.name
        for row in _read_csv(summary_path):
            out = {"loop_id": loop_id}
            out.update(row)
            rows.append(out)
    return rows


def _format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Write detector evaluation reports.")
    parser.add_argument("--eval-dir", required=True)
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--loop-root", default="outputs/semantic_muterag_detector")
    parser.add_argument("--normal-dir", default="data/experiments_v3/experiment_normal")
    parser.add_argument("--direct-dir", default="data/experiments_v3/experiment_direct")
    parser.add_argument("--muted-dir", default="data/experiments_v3/experiment_muted")
    parser.add_argument("--questions", default="data/experiments_v3/questions/v3_questions.csv")
    args = parser.parse_args()

    eval_dir = Path(args.eval_dir)
    reports_dir = Path(args.reports_dir)
    rows = _read_csv(eval_dir / "chunk_scores.csv")
    summary_rows = _read_csv(eval_dir / "summary.csv")
    loop_rows = _collect_loop_summaries(Path(args.loop_root))

    result_rows: list[dict[str, str]] = []
    counts = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    by_corpus: dict[str, dict[str, int]] = {}
    for row in rows:
        corpus = row["corpus_type"]
        verdict = row["verdict"]
        outcome = _strict_outcome(corpus, verdict)
        counts[outcome] += 1
        by_corpus.setdefault(corpus, {"total": 0, "candidate": 0, "flagged": 0})
        by_corpus[corpus]["total"] += 1
        if verdict in CANDIDATE_VERDICTS:
            by_corpus[corpus]["candidate"] += 1
        if verdict != "clean":
            by_corpus[corpus]["flagged"] += 1
        result_rows.append(
            {
                "corpus_type": corpus,
                "question_id": row["question_id"],
                "doc_id": row["doc_id"],
                "source_doc": row["source_doc"],
                "label": "attack" if corpus in {"direct", "muted"} else "benign",
                "verdict": verdict,
                "strict_prediction": "attack" if verdict in CANDIDATE_VERDICTS else "benign",
                "strict_outcome": outcome,
                "anchor_score": row["anchor_score"],
                "alignment_gap": row["alignment_gap"],
                "semantic_break_score": row["semantic_break_score"],
                "payload_isolation": row["payload_isolation"],
                "prototype_similarity": row.get("prototype_similarity", ""),
                "redirect_similarity": row.get("redirect_similarity", ""),
                "refusal_similarity": row.get("refusal_similarity", ""),
                "muted_score": row["muted_score"],
                "reason": row["reason"],
            }
        )

    result_fields = [
        "corpus_type",
        "question_id",
        "doc_id",
        "source_doc",
        "label",
        "verdict",
        "strict_prediction",
        "strict_outcome",
        "anchor_score",
        "alignment_gap",
        "semantic_break_score",
        "payload_isolation",
        "prototype_similarity",
        "redirect_similarity",
        "refusal_similarity",
        "muted_score",
        "reason",
    ]
    _write_csv(reports_dir / "detector_eval_results.csv", result_rows, result_fields)

    tp, fp, tn, fn = counts["TP"], counts["FP"], counts["TN"], counts["FN"]
    attack_total = tp + fn
    benign_total = tn + fp
    detection_rate = _rate(tp, attack_total)
    false_positive_rate = _rate(fp, benign_total)

    summary_lines = [
        "# Detector Evaluation Summary",
        "",
        "## Data",
        f"- normal_dir: `{args.normal_dir}`",
        f"- direct_dir: `{args.direct_dir}`",
        f"- muted_dir: `{args.muted_dir}`",
        f"- questions: `{args.questions}`",
        f"- final_eval_dir: `{eval_dir}`",
        "",
        "## Final Strict Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| TP | {tp} |",
        f"| FP | {fp} |",
        f"| TN | {tn} |",
        f"| FN | {fn} |",
        f"| Detection Rate | {_format_pct(detection_rate)} |",
        f"| False Positive Rate | {_format_pct(false_positive_rate)} |",
        "",
        "Strict prediction treats only `direct_candidate` and `muted_candidate` as attack.",
        "`suspect` is logged for review but is not counted as a blocking detection.",
        "",
        "## Final Metrics By Corpus",
        "",
        "| Corpus | Total | Candidate Count | Candidate Rate | Flagged Count | Flagged Rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for corpus in ["normal", "direct", "muted"]:
        item = by_corpus.get(corpus, {"total": 0, "candidate": 0, "flagged": 0})
        summary_lines.append(
            f"| {corpus} | {item['total']} | {item['candidate']} | "
            f"{_format_pct(_rate(item['candidate'], item['total']))} | "
            f"{item['flagged']} | {_format_pct(_rate(item['flagged'], item['total']))} |"
        )

    summary_lines.extend(
        [
            "",
            "## Iteration Summary",
            "",
            "| Loop | Corpus | Candidate Rate | Flagged Rate | Metric |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in loop_rows:
        summary_lines.append(
            f"| {row['loop_id']} | {row['corpus_type']} | "
            f"{_format_pct(float(row.get('attack_candidate_rate', row.get('muted_candidate_rate', '0')) or 0.0))} | "
            f"{_format_pct(float(row.get('flagged_rate', '0') or 0.0))} | "
            f"{_format_pct(float(row.get('metric_value', '0') or 0.0))} |"
        )

    legacy_path = reports_dir / "legacy_detector_baseline.csv"
    if legacy_path.exists():
        legacy_rows = _read_csv(legacy_path)
        legacy_counts = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
        for row in legacy_rows:
            legacy_counts[row["outcome"]] += 1
        legacy_tp = legacy_counts["TP"]
        legacy_fp = legacy_counts["FP"]
        legacy_tn = legacy_counts["TN"]
        legacy_fn = legacy_counts["FN"]
        summary_lines.extend(
            [
                "",
                "## Legacy Detector Baseline",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| TP | {legacy_tp} |",
                f"| FP | {legacy_fp} |",
                f"| TN | {legacy_tn} |",
                f"| FN | {legacy_fn} |",
                f"| Detection Rate | {_format_pct(_rate(legacy_tp, legacy_tp + legacy_fn))} |",
                f"| False Positive Rate | {_format_pct(_rate(legacy_fp, legacy_fp + legacy_tn))} |",
            ]
        )

    summary_lines.extend(
        [
            "",
            "## Notes",
            "- The legacy pattern detector was measured separately and allowed all v3 direct/muted documents.",
            "- The final semantic detector uses hashed lexical embeddings, query-anchor similarity, semantic split scores, and generic redirection/refusal prototype similarity.",
            "- No source corpus document is modified by this evaluation.",
        ]
    )
    (reports_dir / "detector_eval_summary.md").parent.mkdir(parents=True, exist_ok=True)
    (reports_dir / "detector_eval_summary.md").write_text(
        "\n".join(summary_lines) + "\n",
        encoding="utf-8-sig",
    )

    fp_rows = [r for r in result_rows if r["strict_outcome"] == "FP"]
    fn_rows = [r for r in result_rows if r["strict_outcome"] == "FN"]
    suspect_rows = [r for r in result_rows if r["verdict"] == "suspect"]

    failure_lines = [
        "# Detector Evaluation Failures",
        "",
        "## Strict False Positives",
    ]
    if fp_rows:
        for r in fp_rows:
            failure_lines.append(f"- {r['corpus_type']} {r['question_id']} {r['source_doc']}: {r['reason']}")
    else:
        failure_lines.append("- None")

    failure_lines.extend(["", "## Strict False Negatives"])
    if fn_rows:
        for r in fn_rows:
            failure_lines.append(f"- {r['corpus_type']} {r['question_id']} {r['source_doc']}: {r['reason']}")
    else:
        failure_lines.append("- None")

    failure_lines.extend(["", "## Non-Blocking Suspect Cases"])
    if suspect_rows:
        for r in suspect_rows:
            failure_lines.append(f"- {r['corpus_type']} {r['question_id']} {r['source_doc']}: {r['reason']}")
    else:
        failure_lines.append("- None")

    (reports_dir / "detector_eval_failures.md").write_text(
        "\n".join(failure_lines) + "\n",
        encoding="utf-8-sig",
    )

    print(f"[report] summary -> {reports_dir / 'detector_eval_summary.md'}")
    print(f"[report] results -> {reports_dir / 'detector_eval_results.csv'}")
    print(f"[report] failures -> {reports_dir / 'detector_eval_failures.md'}")


if __name__ == "__main__":
    main()
