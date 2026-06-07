from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from pipeline_general import run_pipeline as run_general_pipeline
from pipeline_secure import run_pipeline as run_secure_pipeline

try:
    from .resource_monitor import ResourceMonitor, summarize_samples
except ImportError:  # pragma: no cover - allows `python pipeline_overhead/run_benchmark.py`
    from resource_monitor import ResourceMonitor, summarize_samples


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = ROOT_DIR / "pipeline_overhead" / "benchmark_questions.yaml"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "pipeline_overhead" / "outputs"

TIMING_FIELDS = (
    "total_duration_sec",
    "retrieval_duration_sec",
    "detector_duration_sec",
    "sanitizer_duration_sec",
    "guardrail_duration_sec",
    "llm_duration_sec",
)


def _parse_simple_yaml(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- "):
            if current:
                rows.append(current)
            current = {}
            line = line[2:]
        if ":" not in line or current is None:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        current[key.strip()] = value
    if current:
        rows.append(current)
    return rows


def load_questions(path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return [dict(item) for item in data]
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]
    return _parse_simple_yaml(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    values = sorted(values)
    k = (len(values) - 1) * percentile
    lower = int(k)
    upper = min(lower + 1, len(values) - 1)
    frac = k - lower
    return values[lower] * (1 - frac) + values[upper] * frac


def _call_pipeline(
    *,
    pipeline_name: str,
    query: str,
    documents_dir: str | None,
    return_context: bool,
) -> dict[str, Any]:
    if pipeline_name == "general":
        return run_general_pipeline(query, documents_dir=documents_dir, return_context=return_context)
    if pipeline_name == "secure":
        return run_secure_pipeline(query, documents_dir=documents_dir, return_context=return_context)
    raise ValueError(f"unknown pipeline: {pipeline_name}")


def _run_one(
    *,
    monitor: ResourceMonitor,
    question: dict[str, str],
    repeat: int,
    pipeline_name: str,
    documents_dir: str | None,
    return_context: bool,
    warmup: bool,
) -> dict[str, Any]:
    monitor.set_context(
        pipeline=pipeline_name,
        question_id=question["id"],
        question_group=question["group"],
        repeat=repeat,
    )
    started = time.perf_counter()
    result = _call_pipeline(
        pipeline_name=pipeline_name,
        query=question["question"],
        documents_dir=documents_dir,
        return_context=return_context,
    )
    elapsed = time.perf_counter() - started
    monitor.set_context(pipeline="", question_id="", question_group="", repeat="")
    return {
        "warmup": warmup,
        "question_id": question["id"],
        "question_group": question["group"],
        "repeat": repeat,
        "pipeline": pipeline_name,
        "query": question["question"],
        "wall_duration_sec": round(elapsed, 6),
        "result": result,
    }


def _pair_key(row: dict[str, Any]) -> tuple[str, int]:
    return (str(row["question_id"]), int(row["repeat"]))


def _timing_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in results:
        timing = item["result"].get("timing", {})
        row = {
            "warmup": item["warmup"],
            "question_id": item["question_id"],
            "question_group": item["question_group"],
            "repeat": item["repeat"],
            "pipeline": item["pipeline"],
            "wall_duration_sec": item["wall_duration_sec"],
        }
        for field in TIMING_FIELDS:
            row[field] = timing.get(field)
        rows.append(row)
    return rows


def _group_summary(
    *,
    group: str,
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    resource_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    general_times = [float(g["result"]["timing"]["total_duration_sec"]) for g, _ in pairs]
    secure_times = [float(s["result"]["timing"]["total_duration_sec"]) for _, s in pairs]
    overheads = [s - g for g, s in zip(general_times, secure_times)]
    overhead_pct = [(s - g) / g * 100.0 for g, s in zip(general_times, secure_times) if g > 0]

    group_samples = [
        sample
        for sample in resource_samples
        if group == "overall" or sample.get("question_group") == group
    ]
    general_resource = summarize_samples([sample for sample in group_samples if sample.get("pipeline") == "general"])
    secure_resource = summarize_samples([sample for sample in group_samples if sample.get("pipeline") == "secure"])

    def delta(key: str) -> float | None:
        left = general_resource.get(key)
        right = secure_resource.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return right - left
        return None

    return {
        "question_group": group,
        "sample_count": len(pairs),
        "general_avg_sec": statistics.mean(general_times) if general_times else None,
        "secure_avg_sec": statistics.mean(secure_times) if secure_times else None,
        "general_p50_sec": _percentile(general_times, 0.50),
        "secure_p50_sec": _percentile(secure_times, 0.50),
        "general_p95_sec": _percentile(general_times, 0.95),
        "secure_p95_sec": _percentile(secure_times, 0.95),
        "general_max_sec": max(general_times) if general_times else None,
        "secure_max_sec": max(secure_times) if secure_times else None,
        "overhead_sec": statistics.mean(overheads) if overheads else None,
        "overhead_pct": statistics.mean(overhead_pct) if overhead_pct else None,
        "cpu_avg_delta": delta("cpu_avg"),
        "mem_max_delta": delta("mem_max_mb"),
        "gpu_avg_delta": delta("gpu_avg"),
        "gpu_mem_max_delta": delta("gpu_mem_max_mb"),
    }


def _build_summary(results: list[dict[str, Any]], samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    measured = [row for row in results if not row["warmup"]]
    by_key: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    groups_by_key: dict[tuple[str, int], str] = {}
    for row in measured:
        key = _pair_key(row)
        by_key.setdefault(key, {})[row["pipeline"]] = row
        groups_by_key[key] = row["question_group"]

    pairs_by_group: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {"overall": []}
    for key, pair in by_key.items():
        if "general" not in pair or "secure" not in pair:
            continue
        grouped_pair = (pair["general"], pair["secure"])
        group = groups_by_key[key]
        pairs_by_group.setdefault(group, []).append(grouped_pair)
        pairs_by_group["overall"].append(grouped_pair)

    rows = []
    for group in sorted([key for key in pairs_by_group if key != "overall"]) + ["overall"]:
        rows.append(_group_summary(group=group, pairs=pairs_by_group[group], resource_samples=samples))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure general vs secure RAG pipeline overhead.")
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--documents-dir", default=os.getenv("RAW_DOCS_DIR", ""))
    parser.add_argument("--repeats", type=int, default=int(os.getenv("PIPELINE_BENCH_REPEATS", "3")))
    parser.add_argument("--warmup", type=int, default=int(os.getenv("PIPELINE_BENCH_WARMUP", "2")))
    parser.add_argument("--sample-interval-sec", type=float, default=float(os.getenv("PIPELINE_BENCH_SAMPLE_INTERVAL_SEC", "0.5")))
    parser.add_argument("--no-context", action="store_true", help="Do not include context previews in pipeline results.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    questions = load_questions(Path(args.questions))
    if not questions:
        raise SystemExit("No questions loaded.")

    run_config = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "questions": str(Path(args.questions).resolve()),
        "documents_dir": args.documents_dir,
        "repeats": args.repeats,
        "warmup": args.warmup,
        "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "ollama_model": os.getenv("OLLAMA_MODEL", ""),
        "guardrail_provider": os.getenv("EXTERNAL_GUARDRAIL_PROVIDER", os.getenv("GUARDRAIL_PROVIDER", "")),
        "guardrail_api_url": os.getenv("EXTERNAL_GUARDRAIL_API_URL", os.getenv("GUARDRAIL_API_URL", "")),
        "enable_dense": os.getenv("ENABLE_DENSE", "true"),
        "enable_rerank": os.getenv("ENABLE_RERANK", "false"),
    }
    (output_dir / "run_config.json").write_text(json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8")

    monitor = ResourceMonitor(interval_sec=args.sample_interval_sec)
    monitor.start()
    results: list[dict[str, Any]] = []
    documents_dir = args.documents_dir or None
    return_context = not args.no_context

    try:
        warmup_questions = questions[: max(0, args.warmup)]
        for idx, question in enumerate(warmup_questions):
            for pipeline_name in ("general", "secure"):
                results.append(
                    _run_one(
                        monitor=monitor,
                        question=question,
                        repeat=-1 - idx,
                        pipeline_name=pipeline_name,
                        documents_dir=documents_dir,
                        return_context=return_context,
                        warmup=True,
                    )
                )

        for repeat in range(args.repeats):
            for index, question in enumerate(questions):
                order = ("general", "secure") if index % 2 == 0 else ("secure", "general")
                for pipeline_name in order:
                    print(f"[bench] repeat={repeat + 1}/{args.repeats} question={question['id']} pipeline={pipeline_name}", flush=True)
                    results.append(
                        _run_one(
                            monitor=monitor,
                            question=question,
                            repeat=repeat,
                            pipeline_name=pipeline_name,
                            documents_dir=documents_dir,
                            return_context=return_context,
                            warmup=False,
                        )
                    )
    finally:
        monitor.stop()

    _write_jsonl(output_dir / "per_query_results.jsonl", results)
    timing_rows = _timing_rows(results)
    _write_csv(
        output_dir / "phase_timings.csv",
        timing_rows,
        ["warmup", "question_id", "question_group", "repeat", "pipeline", "wall_duration_sec", *TIMING_FIELDS],
    )
    _write_csv(
        output_dir / "resource_samples.csv",
        monitor.samples,
        [
            "timestamp",
            "pipeline",
            "question_id",
            "question_group",
            "repeat",
            "cpu_pct",
            "rss_mb",
            "pipeline_cpu_pct",
            "pipeline_rss_mb",
            "ollama_cpu_pct",
            "ollama_rss_mb",
            "guardrail_cpu_pct",
            "guardrail_rss_mb",
            "vllm_cpu_pct",
            "vllm_rss_mb",
            "gpu_util_pct",
            "gpu_mem_mb",
            "gpu_monitor",
        ],
    )
    summary_rows = _build_summary(results, monitor.samples)
    _write_csv(
        output_dir / "overhead_summary.csv",
        summary_rows,
        [
            "question_group",
            "sample_count",
            "general_avg_sec",
            "secure_avg_sec",
            "general_p50_sec",
            "secure_p50_sec",
            "general_p95_sec",
            "secure_p95_sec",
            "general_max_sec",
            "secure_max_sec",
            "overhead_sec",
            "overhead_pct",
            "cpu_avg_delta",
            "mem_max_delta",
            "gpu_avg_delta",
            "gpu_mem_max_delta",
        ],
    )
    print(f"[bench] wrote {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
