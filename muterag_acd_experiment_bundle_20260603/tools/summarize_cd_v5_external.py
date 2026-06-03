#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def latest(pattern: str) -> Path | None:
    paths = [path for path in Path().glob(pattern) if path.is_dir()]
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def summarize_c(run_dir: Path) -> None:
    summary_path = run_dir / "c2_summary.csv"
    if not summary_path.exists():
        print(f"C summary not found: {summary_path}")
        return
    df = pd.read_csv(summary_path)
    log_only = df[df["semantic_action"] == "log_only"].copy()
    print("C")
    if log_only.empty:
        print(f"- no log_only rows in {summary_path}")
        return
    for _, row in log_only.iterrows():
        condition = row.get("condition", "")
        total = int(row.get("total_questions", 0) or 0)
        count = int(row.get("semantic_candidate_count", 0) or 0)
        rate = float(row.get("semantic_candidate_rate", 0.0) or 0.0)
        label = "false_positive" if str(row.get("corpus_type", "")) == "normal" else "detection"
        print(f"- {condition}: {label} {count}/{total} = {pct(rate)}")
    detected = run_dir / "detected_cases.csv"
    if detected.exists():
        rows = pd.read_csv(detected)
        print(f"- detected_cases: {len(rows)} rows ({detected})")


def summarize_d(run_dir: Path | None) -> None:
    if run_dir is None:
        return
    summary_path = run_dir / "d_summary.csv"
    if not summary_path.exists():
        print(f"D summary not found: {summary_path}")
        return
    row = pd.read_csv(summary_path).iloc[0]
    total = int(row.get("total_cases", 0) or 0)
    print("D")
    for count_col, rate_col, label in [
        ("recovered_count", "recovered_rate", "recovered"),
        ("still_refused_count", "still_refused_rate", "still_refused"),
        ("insufficient_evidence_count", "insufficient_evidence_rate", "insufficient_evidence"),
        ("irrelevant_or_incomplete_count", "irrelevant_or_incomplete_rate", "irrelevant_or_incomplete"),
        ("unsafe_or_attack_success_count", "unsafe_or_attack_success_rate", "unsafe_or_attack_success"),
    ]:
        count = int(row.get(count_col, 0) or 0)
        rate = float(row.get(rate_col, 0.0) or 0.0)
        print(f"- {label}: {count}/{total} = {pct(rate)}")
    paired_path = run_dir / "paired_results.csv"
    if paired_path.exists():
        paired = pd.read_csv(paired_path)
        normal_after = (paired["sanitized_answer_judgement"] == "normal_response").sum()
        print(f"- final_normal_after_sanitization: {normal_after}/{len(paired)} = {pct(normal_after / len(paired) if len(paired) else 0)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize external C/D v5 experiment outputs.")
    parser.add_argument("--c-run", type=Path, default=None)
    parser.add_argument("--d-run", type=Path, default=None)
    args = parser.parse_args()

    c_run = args.c_run or latest("outputs/external_v5/C_*/*")
    d_run = args.d_run or latest("outputs/external_v5/D_*/*")
    if c_run is None:
        print("No C run found.")
        return 1
    summarize_c(c_run)
    summarize_d(d_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
