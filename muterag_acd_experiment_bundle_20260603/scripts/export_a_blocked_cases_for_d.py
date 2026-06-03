#!/usr/bin/env python3
"""Export A-study direct guardrail-blocked cases for Study D recovery."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DIRECT_CONDITIONS = {
    "direct_blackbox",
    "direct_whitebox",
    "direct_blackbox_ignore",
    "direct_whitebox_ignore",
}

OUTPUT_FIELDS = [
    "condition",
    "corpus_type",
    "question_id",
    "question",
    "target_doc",
    "target_doc_id",
    "target_filename",
    "source_docs",
    "source_chunk_ids",
    "candidate_chunk_ids",
    "candidate_sources",
    "semantic_detector_candidate_count",
    "semantic_detector_candidate_chunk_ids",
    "semantic_detector_candidate_sources",
    "semantic_detector_reason",
    "selected_context_text_preview",
    "answer_judgement",
    "answer_class_detail",
    "baseline_guardrail_blocked",
    "baseline_guardrail_blocked_by",
    "baseline_answer",
]


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _join(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "||".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _result_files(run_dir: Path) -> list[Path]:
    return sorted(path for path in run_dir.glob("*/results.jsonl") if path.is_file())


def _target_filename(row: dict[str, Any]) -> str:
    for key in ("target_filename", "target_doc", "target_file"):
        value = str(row.get(key, "") or "").strip()
        if value:
            return value
    sources = row.get("source_docs") or row.get("retrieved_sources")
    if isinstance(sources, list) and sources:
        return str(sources[0])
    return ""


def _is_blocked(row: dict[str, Any]) -> bool:
    return bool(
        _bool(row.get("context_guardrail_blocked"))
        or _bool(row.get("guardrail_blocked"))
        or _bool(row.get("external_guardrail_blocked"))
        or str(row.get("final_answer_source", "") or "") == "external_guardrail"
        or str(row.get("answer_class_detail", "") or "") == "guardrail_block"
    )


def _to_output_row(row: dict[str, Any]) -> dict[str, str]:
    target_filename = _target_filename(row)
    return {
        "condition": _join(row.get("condition")),
        "corpus_type": _join(row.get("corpus_type")),
        "question_id": _join(row.get("question_id")),
        "question": _join(row.get("question")),
        "target_doc": _join(row.get("target_doc") or target_filename),
        "target_doc_id": _join(row.get("target_doc_id")),
        "target_filename": target_filename,
        "source_docs": _join(row.get("source_docs")),
        "source_chunk_ids": _join(row.get("source_chunk_ids")),
        "candidate_chunk_ids": _join(row.get("candidate_chunk_ids")),
        "candidate_sources": _join(row.get("candidate_sources")),
        "semantic_detector_candidate_count": _join(row.get("semantic_detector_candidate_count", "")),
        "semantic_detector_candidate_chunk_ids": _join(row.get("semantic_detector_candidate_chunk_ids")),
        "semantic_detector_candidate_sources": _join(row.get("semantic_detector_candidate_sources")),
        "semantic_detector_reason": _join(row.get("semantic_detector_reason")),
        "selected_context_text_preview": _join(
            row.get("selected_context_text_preview") or row.get("selected_context_preview")
        ),
        "answer_judgement": _join(row.get("answer_judgement")),
        "answer_class_detail": _join(row.get("answer_class_detail")),
        "baseline_guardrail_blocked": "true",
        "baseline_guardrail_blocked_by": _join(row.get("external_guardrail_blocked_by")),
        "baseline_answer": _join(row.get("answer")),
    }


def export_cases(
    *,
    run_dir: Path,
    out_path: Path,
    conditions: set[str],
    max_per_condition: int,
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    per_condition = defaultdict(int)

    for path in _result_files(run_dir):
        for row in _read_jsonl(path):
            corpus_type = str(row.get("corpus_type", "") or "")
            guardrail_mode = str(row.get("guardrail_mode", "") or "")
            if corpus_type not in conditions:
                continue
            if guardrail_mode and guardrail_mode != "on":
                continue
            if not _is_blocked(row):
                continue
            if max_per_condition and per_condition[corpus_type] >= max_per_condition:
                continue
            selected.append(_to_output_row(row))
            per_condition[corpus_type] += 1

    selected.sort(key=lambda row: (row.get("corpus_type", ""), row.get("question_id", "")))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(selected)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path, help="Study A run directory containing */results.jsonl")
    parser.add_argument("--out", required=True, type=Path, help="Output CSV path for D_A_BLOCKED_CASES")
    parser.add_argument(
        "--conditions",
        default=",".join(sorted(DIRECT_CONDITIONS)),
        help="Comma-separated corpus_type names to export.",
    )
    parser.add_argument(
        "--max-per-condition",
        type=int,
        default=0,
        help="Optional cap per corpus_type; 0 means all.",
    )
    args = parser.parse_args()

    conditions = {token.strip() for token in args.conditions.split(",") if token.strip()}
    unknown = sorted(conditions - DIRECT_CONDITIONS)
    if unknown:
        raise SystemExit(f"Unknown direct corpus_type(s): {', '.join(unknown)}")
    if not args.run_dir.exists():
        raise SystemExit(f"Run directory not found: {args.run_dir}")

    selected = export_cases(
        run_dir=args.run_dir,
        out_path=args.out,
        conditions=conditions,
        max_per_condition=max(0, args.max_per_condition),
    )
    counts = Counter(row["corpus_type"] for row in selected)
    print(f"wrote: {args.out}")
    print(f"total: {len(selected)}")
    for condition in sorted(conditions):
        print(f"  {condition}: {counts.get(condition, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
