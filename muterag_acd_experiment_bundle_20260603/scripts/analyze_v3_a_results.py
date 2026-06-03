#!/usr/bin/env python
"""Summarize Experiment A v3 result directories.

The runner already writes raw per-condition files. This script creates a
compact analysis folder for reporting and later metric work without modifying
the original run outputs.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


CONDITIONS = [
    "normal_guardrail_off",
    "normal_guardrail_on",
    "direct_guardrail_off",
    "direct_guardrail_on",
    "muted_guardrail_off",
    "muted_guardrail_on",
]


def as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def condition_metrics(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        result_path = run_dir / condition / "results.csv"
        if not result_path.exists():
            continue
        result_rows = read_csv(result_path)
        total = len(result_rows)
        if total == 0:
            continue

        def count_if(predicate) -> int:
            return sum(1 for row in result_rows if predicate(row))

        guardrail_block = count_if(lambda row: as_bool(row.get("context_guardrail_blocked")))
        guardrail_flag = count_if(lambda row: as_bool(row.get("context_guardrail_flagged")))
        llm_called = count_if(lambda row: as_bool(row.get("llm_called")))
        llm_refusal = count_if(lambda row: row.get("answer_class_detail") == "llm_refusal")
        normal_answer = count_if(lambda row: row.get("answer_judgement") == "normal_response")
        refusal_response = count_if(lambda row: row.get("answer_judgement") == "refusal_response")
        other_response = count_if(lambda row: row.get("answer_judgement") == "other_response")
        target_doc_hit = count_if(lambda row: as_bool(row.get("target_doc_hit")))
        attack_context = count_if(lambda row: as_bool(row.get("attack_context_detected")))
        errors = count_if(lambda row: as_bool(row.get("error")))
        semantic_candidate = count_if(lambda row: int(as_float(row.get("semantic_detector_candidate_count"))) > 0)
        semantic_drop = count_if(lambda row: int(as_float(row.get("semantic_detector_dropped_count"))) > 0)

        corpus_type, _, guardrail_mode = condition.partition("_guardrail_")
        rows.append(
            {
                "condition": condition,
                "corpus_type": corpus_type,
                "guardrail_mode": guardrail_mode,
                "total": total,
                "error_count": errors,
                "target_doc_hit_count": target_doc_hit,
                "target_doc_hit_rate": round(target_doc_hit / total, 4),
                "attack_context_count": attack_context,
                "attack_context_rate": round(attack_context / total, 4),
                "guardrail_flag_count": guardrail_flag,
                "guardrail_flag_rate": round(guardrail_flag / total, 4),
                "guardrail_block_count": guardrail_block,
                "guardrail_block_rate": round(guardrail_block / total, 4),
                "llm_called_count": llm_called,
                "llm_called_rate": round(llm_called / total, 4),
                "llm_refusal_count": llm_refusal,
                "llm_refusal_rate": round(llm_refusal / total, 4),
                "final_refusal_or_block_count": refusal_response,
                "final_refusal_or_block_rate": round(refusal_response / total, 4),
                "normal_answer_count": normal_answer,
                "normal_answer_rate": round(normal_answer / total, 4),
                "other_response_count": other_response,
                "other_response_rate": round(other_response / total, 4),
                "semantic_candidate_count": semantic_candidate,
                "semantic_candidate_rate": round(semantic_candidate / total, 4),
                "semantic_drop_count": semantic_drop,
                "semantic_drop_rate": round(semantic_drop / total, 4),
            }
        )
    return rows


def build_question_matrix(run_dir: Path) -> list[dict[str, Any]]:
    by_question: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    metadata: dict[str, dict[str, str]] = {}
    for condition in CONDITIONS:
        result_path = run_dir / condition / "results.csv"
        if not result_path.exists():
            continue
        for row in read_csv(result_path):
            qid = row.get("question_id", "")
            by_question[qid][condition] = row
            metadata.setdefault(
                qid,
                {
                    "question_id": qid,
                    "question": row.get("question", ""),
                    "target_doc_id": row.get("target_doc_id", ""),
                    "target_filename": row.get("target_filename", ""),
                },
            )

    matrix: list[dict[str, Any]] = []
    for qid in sorted(by_question):
        row: dict[str, Any] = dict(metadata.get(qid, {"question_id": qid}))
        for condition in CONDITIONS:
            item = by_question[qid].get(condition, {})
            row[f"{condition}_judgement"] = item.get("answer_judgement", "")
            row[f"{condition}_detail"] = item.get("answer_class_detail", "")
            row[f"{condition}_target_hit"] = item.get("target_doc_hit", "")
            row[f"{condition}_attack_context"] = item.get("attack_context_detected", "")
            row[f"{condition}_guardrail_block"] = item.get("context_guardrail_blocked", "")
            row[f"{condition}_llm_called"] = item.get("llm_called", "")
        matrix.append(row)
    return matrix


def notable_cases(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        result_path = run_dir / condition / "results.csv"
        if not result_path.exists():
            continue
        corpus_type = condition.split("_guardrail_", 1)[0]
        for row in read_csv(result_path):
            reasons: list[str] = []
            if row.get("error") in {"True", "true", "1"}:
                reasons.append("query_error")
            if not as_bool(row.get("target_doc_hit")):
                reasons.append("target_doc_miss")
            if corpus_type in {"direct", "muted"} and not as_bool(row.get("attack_context_detected")):
                reasons.append("attack_context_miss")
            if condition == "normal_guardrail_on" and as_bool(row.get("context_guardrail_blocked")):
                reasons.append("normal_guardrail_false_block")
            if condition == "muted_guardrail_off" and row.get("answer_judgement") != "normal_response":
                reasons.append("muted_off_non_normal")
            if condition == "direct_guardrail_off" and row.get("answer_judgement") == "normal_response":
                reasons.append("direct_off_normal_escape")
            if condition.endswith("_guardrail_on") and row.get("answer_class_detail") == "llm_refusal":
                reasons.append("guardrail_on_llm_refusal")
            if not reasons:
                continue
            rows.append(
                {
                    "condition": condition,
                    "question_id": row.get("question_id", ""),
                    "target_doc_id": row.get("target_doc_id", ""),
                    "target_filename": row.get("target_filename", ""),
                    "reasons": ";".join(reasons),
                    "answer_judgement": row.get("answer_judgement", ""),
                    "answer_class_detail": row.get("answer_class_detail", ""),
                    "target_doc_hit": row.get("target_doc_hit", ""),
                    "attack_context_detected": row.get("attack_context_detected", ""),
                    "context_guardrail_blocked": row.get("context_guardrail_blocked", ""),
                    "llm_called": row.get("llm_called", ""),
                    "answer_preview": (row.get("answer", "") or "").replace("\n", " ")[:500],
                }
            )
    return rows


def metric_lookup(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["condition"]: row for row in rows}


def write_report(run_dir: Path, out_dir: Path, metrics: list[dict[str, Any]], notable: list[dict[str, Any]]) -> None:
    lookup = metric_lookup(metrics)
    config_path = run_dir / "run_config.json"
    run_config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

    def rate(condition: str, field: str) -> float:
        return as_float(lookup.get(condition, {}).get(field))

    lines = [
        "# Experiment A v3 Analysis",
        "",
        "## Run",
        f"- run_dir: `{run_dir}`",
        f"- run_id: `{run_config.get('run_id', run_dir.name)}`",
        f"- question_count: `{run_config.get('question_count', '')}`",
        f"- ollama_model: `{run_config.get('ollama_model', '')}`",
        f"- guardrail_provider: `{run_config.get('guardrail_provider', '')}`",
        "",
        "## Core Rates",
        "",
        "| Condition | Normal Answer | Final Refusal/Block | LLM Refusal | Guardrail Block | Attack Ctx | Target Hit |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics:
        lines.append(
            "| {condition} | {normal} | {final} | {llm} | {guard} | {attack} | {target} |".format(
                condition=row["condition"],
                normal=pct(as_float(row["normal_answer_rate"])),
                final=pct(as_float(row["final_refusal_or_block_rate"])),
                llm=pct(as_float(row["llm_refusal_rate"])),
                guard=pct(as_float(row["guardrail_block_rate"])),
                attack=pct(as_float(row["attack_context_rate"])),
                target=pct(as_float(row["target_doc_hit_rate"])),
            )
        )

    normal_fp = rate("normal_guardrail_on", "guardrail_block_rate")
    direct_off_llm = rate("direct_guardrail_off", "llm_refusal_rate")
    direct_on_block = rate("direct_guardrail_on", "guardrail_block_rate")
    muted_off_llm = rate("muted_guardrail_off", "llm_refusal_rate")
    muted_on_block = rate("muted_guardrail_on", "guardrail_block_rate")
    muted_dos_lift = rate("muted_guardrail_on", "final_refusal_or_block_rate") - rate(
        "muted_guardrail_off", "final_refusal_or_block_rate"
    )

    lines.extend(
        [
            "",
            "## Derived Interpretation Metrics",
            "",
            f"- Normal guardrail false-block rate: **{pct(normal_fp)}**",
            f"- Direct OFF LLM-refusal rate: **{pct(direct_off_llm)}**",
            f"- Direct ON guardrail-block rate: **{pct(direct_on_block)}**",
            f"- Muted OFF LLM-refusal rate: **{pct(muted_off_llm)}**",
            f"- Muted ON guardrail-block rate: **{pct(muted_on_block)}**",
            f"- Muted guardrail-induced DoS lift: **{pct(muted_dos_lift)}**",
            "",
            "## Notable Cases",
            f"- Count: `{len(notable)}`",
            "",
        ]
    )
    for row in notable[:40]:
        lines.append(
            f"- `{row['condition']}` `{row['question_id']}` `{row['reasons']}`: "
            f"{row['answer_judgement']}/{row['answer_class_detail']}"
        )
    if len(notable) > 40:
        lines.append(f"- ... {len(notable) - 40} more rows in `a_v3_notable_cases.csv`")

    (out_dir / "a_v3_analysis_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    run_dir = args.run_dir
    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")
    out_dir = args.out or run_dir / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = condition_metrics(run_dir)
    matrix = build_question_matrix(run_dir)
    notable = notable_cases(run_dir)

    metric_fields = [
        "condition",
        "corpus_type",
        "guardrail_mode",
        "total",
        "error_count",
        "target_doc_hit_count",
        "target_doc_hit_rate",
        "attack_context_count",
        "attack_context_rate",
        "guardrail_flag_count",
        "guardrail_flag_rate",
        "guardrail_block_count",
        "guardrail_block_rate",
        "llm_called_count",
        "llm_called_rate",
        "llm_refusal_count",
        "llm_refusal_rate",
        "final_refusal_or_block_count",
        "final_refusal_or_block_rate",
        "normal_answer_count",
        "normal_answer_rate",
        "other_response_count",
        "other_response_rate",
        "semantic_candidate_count",
        "semantic_candidate_rate",
        "semantic_drop_count",
        "semantic_drop_rate",
    ]
    matrix_fields = list(matrix[0].keys()) if matrix else ["question_id"]
    notable_fields = [
        "condition",
        "question_id",
        "target_doc_id",
        "target_filename",
        "reasons",
        "answer_judgement",
        "answer_class_detail",
        "target_doc_hit",
        "attack_context_detected",
        "context_guardrail_blocked",
        "llm_called",
        "answer_preview",
    ]

    write_csv(out_dir / "a_v3_condition_metrics.csv", metrics, metric_fields)
    write_csv(out_dir / "a_v3_question_matrix.csv", matrix, matrix_fields)
    write_csv(out_dir / "a_v3_notable_cases.csv", notable, notable_fields)
    write_report(run_dir, out_dir, metrics, notable)
    print(f"[analysis] metrics -> {out_dir / 'a_v3_condition_metrics.csv'}")
    print(f"[analysis] matrix -> {out_dir / 'a_v3_question_matrix.csv'}")
    print(f"[analysis] notable -> {out_dir / 'a_v3_notable_cases.csv'}")
    print(f"[analysis] report -> {out_dir / 'a_v3_analysis_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
