from __future__ import annotations

import argparse
import json
import pickle
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

from detector import MutedRAGDetector
from src.config import DETECTOR_POLICY_ACTIONS, RAW_DOCS_DIR, get_domain_index_dir


EVAL_DOMAIN_NAME = "Z. 평가용_추가문서"
DEFAULT_OUTPUT_MD = Path("outputs") / "detector_eval_summary.md"
DEFAULT_OUTPUT_JSON = Path("outputs") / "detector_eval_summary.json"
DEFAULT_BASELINE_JSON = Path("outputs") / "detector_eval_summary_before_feature_update.json"
SUPPORTED_ACTIONS = {"index", "review", "quarantine"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def load_pickle(path: Path) -> Any:
    with path.open("rb") as file:
        return pickle.load(file)


def parse_manifest(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}

    manifest: Dict[str, Dict[str, str]] = {}
    current_file: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_file = line[3:].strip()
            manifest[current_file] = {}
            continue
        if not current_file or not line.startswith("- ") or ":" not in line:
            continue

        key, value = line[2:].split(":", 1)
        manifest[current_file][key.strip()] = value.strip()

    return manifest


def iter_eval_documents(eval_dir: Path) -> List[Path]:
    return sorted(path for path in eval_dir.glob("*.txt") if path.is_file())


def load_chunk_records(index_dir: Path) -> List[Dict[str, Any]]:
    indexed_records = load_pickle(index_dir / "chunks_meta.pkl") if (index_dir / "chunks_meta.pkl").exists() else []
    review_records = load_jsonl(index_dir / "flagged_chunks.jsonl")
    quarantine_records = load_jsonl(index_dir / "quarantine_chunks.jsonl")
    return [*indexed_records, *review_records, *quarantine_records]


def build_detector(index_dir: Path) -> MutedRAGDetector:
    corpus_stats_path = index_dir / "detector_corpus_stats.json"
    corpus_stats = load_json(corpus_stats_path) if corpus_stats_path.exists() else None
    return MutedRAGDetector(corpus_stats=corpus_stats)


def policy_action_for_level(risk_level: str) -> str:
    action = DETECTOR_POLICY_ACTIONS.get((risk_level or "low").lower(), "index")
    return action if action in SUPPORTED_ACTIONS else "index"


def rescore_chunk(record: Dict[str, Any], detector: MutedRAGDetector) -> Dict[str, Any]:
    text = record.get("text") or record.get("chunk_text") or ""
    analysis = detector.analyze(text)
    risk_level = str(analysis.get("risk_level", "low")).lower()
    detector_action = policy_action_for_level(risk_level)

    return {
        "chunk_id": record.get("chunk_id", ""),
        "file_name": record.get("file_name") or record.get("source", "").replace("\\", "/").split("/")[-1],
        "source": record.get("source", ""),
        "stored_detector_action": record.get("detector_action", "index"),
        "detector_action": detector_action,
        "risk_level": risk_level,
        "instructionality_score": float(analysis["instructionality"]["normalized_score"]),
        "refusal_inducing_score": float(analysis["refusal_inducing"]["normalized_score"]),
        "outlier_score": float(analysis["outlier"]["normalized_score"]),
        "base_risk": float(analysis["base_risk"]),
        "adjusted_risk": float(analysis["adjusted_risk"]),
        "instructionality_patterns": [pattern["name"] for pattern in analysis["instructionality"]["matched_patterns"]],
        "refusal_patterns": [pattern["name"] for pattern in analysis["refusal_inducing"]["matched_patterns"]],
        "explicit_high_risk_patterns": [
            pattern["name"] for pattern in analysis["refusal_inducing"]["explicit_high_risk_patterns"]
        ],
        "outlier_features": list(analysis["outlier"].get("triggered_rules", [])),
        "triggered_rules": list(analysis.get("triggered_rules", [])),
        "instructionality_breakdown": analysis["instructionality"].get("feature_breakdown", {}),
        "refusal_breakdown": analysis["refusal_inducing"].get("feature_breakdown", {}),
        "outlier_breakdown": analysis["outlier"].get("feature_breakdown", {}),
        "chunk_text_excerpt": text[:320].replace("\n", " / ").strip(),
        "detector_explanation": analysis.get("explanation", ""),
    }


def aggregate_chunks(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    per_file: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "chunk_total": 0,
            "indexed_chunks": 0,
            "flagged_chunks": 0,
            "quarantined_chunks": 0,
            "risk_counts": Counter(),
            "instructionality_sum": 0.0,
            "refusal_sum": 0.0,
            "outlier_sum": 0.0,
            "base_risk_sum": 0.0,
            "adjusted_risk_sum": 0.0,
            "instructionality_max": 0.0,
            "refusal_max": 0.0,
            "outlier_max": 0.0,
            "base_risk_max": 0.0,
            "adjusted_risk_max": 0.0,
            "instructionality_patterns": Counter(),
            "refusal_patterns": Counter(),
            "explicit_patterns": Counter(),
            "outlier_features": Counter(),
            "triggered_rules": Counter(),
            "highest_risk_chunk": None,
        }
    )

    for record in records:
        file_name = record["file_name"]
        bucket = per_file[file_name]
        bucket["chunk_total"] += 1

        action = record.get("detector_action", "index")
        if action == "quarantine":
            bucket["quarantined_chunks"] += 1
        elif action == "review":
            bucket["flagged_chunks"] += 1
            bucket["indexed_chunks"] += 1
        else:
            bucket["indexed_chunks"] += 1

        risk_level = str(record.get("risk_level", "low")).lower()
        bucket["risk_counts"][risk_level] += 1

        for key, sum_key, max_key in (
            ("instructionality_score", "instructionality_sum", "instructionality_max"),
            ("refusal_inducing_score", "refusal_sum", "refusal_max"),
            ("outlier_score", "outlier_sum", "outlier_max"),
            ("base_risk", "base_risk_sum", "base_risk_max"),
            ("adjusted_risk", "adjusted_risk_sum", "adjusted_risk_max"),
        ):
            value = float(record.get(key, 0.0))
            bucket[sum_key] += value
            bucket[max_key] = max(bucket[max_key], value)

        for name in record.get("instructionality_patterns", []):
            bucket["instructionality_patterns"][name] += 1
        for name in record.get("refusal_patterns", []):
            bucket["refusal_patterns"][name] += 1
        for name in record.get("explicit_high_risk_patterns", []):
            bucket["explicit_patterns"][name] += 1
        for name in record.get("outlier_features", []):
            bucket["outlier_features"][name] += 1
        for name in record.get("triggered_rules", []):
            bucket["triggered_rules"][name] += 1

        highest = bucket["highest_risk_chunk"]
        if highest is None or float(record.get("adjusted_risk", 0.0)) > float(highest.get("adjusted_risk", 0.0)):
            bucket["highest_risk_chunk"] = record

    return per_file


def summarize_rescored_domain(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    risk_counts = Counter()
    action_counts = Counter()
    total_chunks = 0

    for record in records:
        total_chunks += 1
        risk_counts[str(record.get("risk_level", "low")).lower()] += 1

        action = record.get("detector_action", "index")
        if action not in SUPPORTED_ACTIONS:
            action = "index"
        action_counts[action] += 1

    return {
        "total_chunks": total_chunks,
        "risk_counts": {level: risk_counts.get(level, 0) for level in ("low", "medium", "high", "critical")},
        "action_counts": {action: action_counts.get(action, 0) for action in ("index", "review", "quarantine")},
    }


def determine_highest_risk(risk_counts: Counter) -> str:
    for level in ("critical", "high", "medium", "low"):
        if risk_counts.get(level, 0):
            return level
    return "unknown"


def mean_value(sum_value: float, count: int) -> float:
    return round(sum_value / count, 4) if count else 0.0


def top_items(counter: Counter, limit: int = 8) -> List[List[Any]]:
    return [[name, count] for name, count in counter.most_common(limit)]


def compare_with_baseline(documents: List[Dict[str, Any]], baseline_data: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not baseline_data:
        return None

    baseline_docs = {doc["file_name"]: doc for doc in baseline_data.get("documents", [])}
    comparisons = []

    for doc in documents:
        baseline = baseline_docs.get(doc["file_name"])
        if not baseline:
            continue

        comparisons.append(
            {
                "file_name": doc["file_name"],
                "previous_highest_risk": baseline.get("highest_risk"),
                "current_highest_risk": doc["highest_risk"],
                "previous_max_adjusted_risk": baseline.get("max_adjusted_risk", 0.0),
                "current_max_adjusted_risk": doc["max_adjusted_risk"],
                "delta_max_adjusted_risk": round(
                    float(doc["max_adjusted_risk"]) - float(baseline.get("max_adjusted_risk", 0.0)),
                    4,
                ),
            }
        )

    return {
        "previous_domain_risk_counts": baseline_data.get("domain_summary", {}).get("risk_counts", {}),
        "current_domain_risk_counts": {},
        "documents": comparisons,
    }


def collect_eval_results(eval_dir: Path, baseline_json: Path | None = None) -> Dict[str, Any]:
    domain_index_dir = get_domain_index_dir(EVAL_DOMAIN_NAME)
    summary_path = domain_index_dir / "detector_summary.json"
    review_path = domain_index_dir / "flagged_chunks.jsonl"
    quarantine_path = domain_index_dir / "quarantine_chunks.jsonl"
    chunks_path = domain_index_dir / "chunks_meta.pkl"
    manifest_path = eval_dir / "_generated_eval_manifest.md"

    stored_summary = load_json(summary_path) if summary_path.exists() else {}
    manifest = parse_manifest(manifest_path)
    detector = build_detector(domain_index_dir)
    chunk_records = [rescore_chunk(record, detector) for record in load_chunk_records(domain_index_dir)]
    per_file = aggregate_chunks(chunk_records)
    rescored_summary = summarize_rescored_domain(chunk_records)

    documents = []
    for path in iter_eval_documents(eval_dir):
        file_name = path.name
        bucket = per_file.get(
            file_name,
            {
                "chunk_total": 0,
                "indexed_chunks": 0,
                "flagged_chunks": 0,
                "quarantined_chunks": 0,
                "risk_counts": Counter(),
                "instructionality_sum": 0.0,
                "refusal_sum": 0.0,
                "outlier_sum": 0.0,
                "base_risk_sum": 0.0,
                "adjusted_risk_sum": 0.0,
                "instructionality_max": 0.0,
                "refusal_max": 0.0,
                "outlier_max": 0.0,
                "base_risk_max": 0.0,
                "adjusted_risk_max": 0.0,
                "instructionality_patterns": Counter(),
                "refusal_patterns": Counter(),
                "explicit_patterns": Counter(),
                "outlier_features": Counter(),
                "triggered_rules": Counter(),
                "highest_risk_chunk": None,
            },
        )

        chunk_total = bucket["chunk_total"]
        risk_counts = {level: bucket["risk_counts"].get(level, 0) for level in ("low", "medium", "high", "critical")}
        highest_chunk = bucket["highest_risk_chunk"] or {}
        documents.append(
            {
                "file_name": file_name,
                "highest_risk": determine_highest_risk(bucket["risk_counts"]),
                "chunk_total": chunk_total,
                "indexed_chunks": bucket["indexed_chunks"],
                "flagged_chunks": bucket["flagged_chunks"],
                "quarantined_chunks": bucket["quarantined_chunks"],
                "risk_counts": risk_counts,
                "instructionality_max": round(bucket["instructionality_max"], 4),
                "instructionality_avg": mean_value(bucket["instructionality_sum"], chunk_total),
                "refusal_inducing_max": round(bucket["refusal_max"], 4),
                "refusal_inducing_avg": mean_value(bucket["refusal_sum"], chunk_total),
                "outlier_max": round(bucket["outlier_max"], 4),
                "outlier_avg": mean_value(bucket["outlier_sum"], chunk_total),
                "base_risk_max": round(bucket["base_risk_max"], 4),
                "base_risk_avg": mean_value(bucket["base_risk_sum"], chunk_total),
                "max_adjusted_risk": round(bucket["adjusted_risk_max"], 4),
                "avg_adjusted_risk": mean_value(bucket["adjusted_risk_sum"], chunk_total),
                "top_instructionality_patterns": top_items(bucket["instructionality_patterns"]),
                "top_refusal_patterns": top_items(bucket["refusal_patterns"]),
                "top_explicit_patterns": top_items(bucket["explicit_patterns"]),
                "top_outlier_features": top_items(bucket["outlier_features"]),
                "top_triggered_rules": top_items(bucket["triggered_rules"]),
                "highest_risk_chunk": {
                    "chunk_id": highest_chunk.get("chunk_id", ""),
                    "risk_level": highest_chunk.get("risk_level", "unknown"),
                    "instructionality_score": highest_chunk.get("instructionality_score", 0.0),
                    "refusal_inducing_score": highest_chunk.get("refusal_inducing_score", 0.0),
                    "outlier_score": highest_chunk.get("outlier_score", 0.0),
                    "base_risk": highest_chunk.get("base_risk", 0.0),
                    "adjusted_risk": highest_chunk.get("adjusted_risk", 0.0),
                    "instructionality_patterns": highest_chunk.get("instructionality_patterns", []),
                    "refusal_patterns": highest_chunk.get("refusal_patterns", []),
                    "explicit_high_risk_patterns": highest_chunk.get("explicit_high_risk_patterns", []),
                    "triggered_rules": highest_chunk.get("triggered_rules", []),
                    "chunk_text_excerpt": highest_chunk.get("chunk_text_excerpt", ""),
                    "detector_explanation": highest_chunk.get("detector_explanation", ""),
                },
                "manifest": manifest.get(file_name, {}),
            }
        )

    baseline_data = load_json(baseline_json) if baseline_json and baseline_json.exists() else None
    comparison = compare_with_baseline(documents, baseline_data)
    if comparison is not None:
        comparison["current_domain_risk_counts"] = rescored_summary.get("risk_counts", {})

    return {
        "eval_domain": EVAL_DOMAIN_NAME,
        "eval_dir": str(eval_dir),
        "index_dir": str(domain_index_dir),
        "summary_path": str(summary_path),
        "review_path": str(review_path),
        "quarantine_path": str(quarantine_path),
        "chunks_path": str(chunks_path),
        "stored_domain_summary": stored_summary,
        "domain_summary": rescored_summary,
        "documents": documents,
        "comparison": comparison,
    }


def render_markdown(result: Dict[str, Any]) -> str:
    lines = [
        "# Detector Evaluation Summary",
        "",
        f"- evaluation_domain: `{result['eval_domain']}`",
        f"- detector_summary: `{result['summary_path']}`",
        f"- flagged_chunks: `{result['review_path']}`",
        f"- quarantine_chunks: `{result['quarantine_path']}`",
        "",
        "## Domain Summary",
        "",
        f"- total_chunks: {result['domain_summary'].get('total_chunks', 0)}",
        f"- risk_counts: {result['domain_summary'].get('risk_counts', {})}",
        f"- action_counts: {result['domain_summary'].get('action_counts', {})}",
        "",
        "## Stored Ingest Summary",
        "",
        f"- stored_risk_counts: {result.get('stored_domain_summary', {}).get('risk_counts', {})}",
        f"- stored_action_counts: {result.get('stored_domain_summary', {}).get('action_counts', {})}",
        "",
    ]

    comparison = result.get("comparison")
    if comparison:
        lines.extend(
            [
                "## Baseline Comparison",
                "",
                f"- previous_domain_risk_counts: {comparison.get('previous_domain_risk_counts', {})}",
                f"- current_domain_risk_counts: {comparison.get('current_domain_risk_counts', {})}",
                "",
            ]
        )

    lines.extend(["## Document Results", ""])

    for doc in result["documents"]:
        lines.extend(
            [
                f"### {doc['file_name']}",
                f"- highest_risk: {doc['highest_risk']}",
                f"- chunk_total: {doc['chunk_total']}",
                f"- indexed_chunks: {doc['indexed_chunks']}",
                f"- flagged_chunks: {doc['flagged_chunks']}",
                f"- quarantined_chunks: {doc['quarantined_chunks']}",
                f"- risk_counts: {doc['risk_counts']}",
                f"- instructionality_max/avg: {doc['instructionality_max']} / {doc['instructionality_avg']}",
                f"- refusal_inducing_max/avg: {doc['refusal_inducing_max']} / {doc['refusal_inducing_avg']}",
                f"- outlier_max/avg: {doc['outlier_max']} / {doc['outlier_avg']}",
                f"- base_risk_max/avg: {doc['base_risk_max']} / {doc['base_risk_avg']}",
                f"- adjusted_risk_max/avg: {doc['max_adjusted_risk']} / {doc['avg_adjusted_risk']}",
            ]
        )

        if doc["manifest"]:
            manifest_items = ", ".join(f"{key}={value}" for key, value in doc["manifest"].items())
            lines.append(f"- manifest: {manifest_items}")

        if doc["top_instructionality_patterns"]:
            lines.append(f"- top_instructionality_patterns: {doc['top_instructionality_patterns']}")
        if doc["top_refusal_patterns"]:
            lines.append(f"- top_refusal_patterns: {doc['top_refusal_patterns']}")
        if doc["top_explicit_patterns"]:
            lines.append(f"- top_explicit_patterns: {doc['top_explicit_patterns']}")
        if doc["top_outlier_features"]:
            lines.append(f"- top_outlier_features: {doc['top_outlier_features']}")
        if doc["top_triggered_rules"]:
            lines.append(f"- top_triggered_rules: {doc['top_triggered_rules']}")

        highest = doc["highest_risk_chunk"]
        lines.extend(
            [
                "- highest_risk_chunk:",
                f"  - chunk_id: {highest['chunk_id']}",
                f"  - risk_level: {highest['risk_level']}",
                f"  - I/R/O: {highest['instructionality_score']} / {highest['refusal_inducing_score']} / {highest['outlier_score']}",
                f"  - base/adjusted: {highest['base_risk']} / {highest['adjusted_risk']}",
                f"  - instructionality_patterns: {highest['instructionality_patterns']}",
                f"  - refusal_patterns: {highest['refusal_patterns']}",
                f"  - explicit_patterns: {highest['explicit_high_risk_patterns']}",
                f"  - triggered_rules: {highest['triggered_rules']}",
                f"  - explanation: {highest['detector_explanation']}",
                f"  - excerpt: {highest['chunk_text_excerpt']}",
                "",
            ]
        )

    if comparison and comparison.get("documents"):
        lines.extend(["## Per-Document Delta", ""])
        for item in comparison["documents"]:
            lines.append(
                f"- {item['file_name']}: {item['previous_highest_risk']} -> {item['current_highest_risk']}, "
                f"max_adjusted_risk {item['previous_max_adjusted_risk']} -> {item['current_max_adjusted_risk']} "
                f"(delta={item['delta_max_adjusted_risk']})"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize detector outputs for evaluation documents.")
    parser.add_argument("--eval-dir", default=str(Path(RAW_DOCS_DIR) / EVAL_DOMAIN_NAME))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--baseline-json", default=str(DEFAULT_BASELINE_JSON))
    args = parser.parse_args()

    baseline_json = Path(args.baseline_json) if args.baseline_json else None
    result = collect_eval_results(Path(args.eval_dir), baseline_json=baseline_json)
    markdown = render_markdown(result)

    output_md = Path(args.output_md)
    output_json = Path(args.output_json)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    output_md.write_text(markdown, encoding="utf-8")
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(markdown)
    print("")
    print(f"[saved] {output_md}")
    print(f"[saved] {output_json}")


if __name__ == "__main__":
    main()
