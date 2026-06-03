from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
V5_CORPORA = {
    "normal": "experiment_normal",
    "direct_blackbox": "experiment_direct_blackbox",
    "direct_whitebox": "experiment_direct_whitebox",
    "muted_blackbox": "experiment_muted_blackbox",
    "muted_whitebox": "experiment_muted_whitebox",
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print("[run]", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env, check=True)


def _latest_child_run(out_dir: Path) -> Path:
    candidates = [path for path in out_dir.iterdir() if path.is_dir()]
    if not candidates:
        raise RuntimeError(f"No child run found in {out_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _count_questions(path: Path) -> int:
    return sum(1 for row in _read_csv(path) if row.get("question", "").strip())


def _count_files(path: Path) -> int:
    return sum(1 for item in path.iterdir() if item.is_file()) if path.exists() else 0


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _selected_corpora(conditions: str) -> set[str]:
    raw = conditions.strip().lower()
    if raw in {"all", "v5_all"}:
        return set(V5_CORPORA)
    selected: set[str] = set()
    for part in raw.split(","):
        token = part.strip().lower().replace("-", "_")
        if not token:
            continue
        if token in V5_CORPORA:
            selected.add(token)
            continue
        for suffix in ("_guardrail_off", "_guardrail_on", "_off", "_on"):
            if token.endswith(suffix):
                corpus = token[: -len(suffix)]
                if corpus in V5_CORPORA:
                    selected.add(corpus)
                break
    return selected or set(V5_CORPORA)


def _preflight(experiments_root: Path, questions: Path, conditions: str) -> None:
    missing = []
    if not questions.exists():
        missing.append(str(questions))
    selected = _selected_corpora(conditions)
    selected.add("normal")  # Calibration always needs the normal corpus.
    for corpus_name in sorted(selected):
        dirname = V5_CORPORA[corpus_name]
        path = experiments_root / dirname
        if not path.exists():
            missing.append(str(path))
    if missing:
        expected_dirs = [f"    {V5_CORPORA[name]}/" for name in sorted(selected)]
        layout = "\n".join(
            [
                "Required experiments_v5 layout for the selected conditions:",
                f"  {experiments_root}/",
                *expected_dirs,
                "    questions/v5_questions.csv",
            ]
        )
        raise RuntimeError("Missing v5 corpus inputs:\n  - " + "\n  - ".join(missing) + "\n\n" + layout)


def _load_condition_rows(child_run: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for condition_dir in sorted(path for path in child_run.iterdir() if path.is_dir()):
        results_path = condition_dir / "results.csv"
        if results_path.exists():
            rows.extend(_read_csv(results_path))
    return rows


def _jsonl_write(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _detected_manifest_rows(child_run: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _load_condition_rows(child_run)
    detected: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    for row in rows:
        if row.get("corpus_type") not in {"muted_blackbox", "muted_whitebox"}:
            continue
        try:
            candidate_count = int(row.get("semantic_detector_candidate_count", "0") or 0)
        except ValueError:
            candidate_count = 0
        if candidate_count <= 0:
            continue
        manifest = {
            "condition": row.get("condition", ""),
            "corpus_type": row.get("corpus_type", ""),
            "question_id": row.get("question_id", ""),
            "question": row.get("question", ""),
            "target_doc": row.get("target_filename", ""),
            "target_doc_id": row.get("target_doc_id", ""),
            "source_docs": row.get("source_docs", ""),
            "source_chunk_ids": row.get("source_chunk_ids", ""),
            "candidate_chunk_ids": row.get("semantic_detector_candidate_chunk_ids", ""),
            "candidate_sources": row.get("semantic_detector_candidate_sources", ""),
            "semantic_detector_reason": row.get("semantic_detector_reason", ""),
            "selected_context_text_preview": row.get("selected_context_text_preview", ""),
            "answer_class_detail": row.get("answer_class_detail", ""),
        }
        detected.append(manifest)
        for chunk_id in [item.strip() for item in manifest["candidate_chunk_ids"].split("|") if item.strip()]:
            chunks.append(
                {
                    "condition": manifest["condition"],
                    "question_id": manifest["question_id"],
                    "corpus_type": manifest["corpus_type"],
                    "candidate_chunk_id": chunk_id,
                    "candidate_sources": manifest["candidate_sources"],
                    "semantic_detector_reason": manifest["semantic_detector_reason"],
                    "selected_context_text_preview": manifest["selected_context_text_preview"],
                }
            )
    return detected, chunks


def main() -> int:
    run_mode = _env("C2_RUN_MODE", "full").strip().lower()
    if run_mode not in {"smoke", "full"}:
        run_mode = "full"
    experiments_root = Path(_env("C2_EXPERIMENTS_ROOT", "data/experiments_v5"))
    questions = Path(_env("C2_QUESTIONS", "data/experiments_v5/questions/v5_questions.csv"))
    embedding_backend = _env("C2_EMBEDDING_BACKEND", _env("SEMANTIC_DETECTOR_BACKEND", "auto"))
    semantic_actions = [item.strip() for item in _env("C2_SEMANTIC_ACTIONS", "off,log_only,drop_chunk").split(",") if item.strip()]
    conditions = _env("C2_CONDITIONS", "normal_on,muted_blackbox_on,muted_whitebox_on")
    run_id = _env("C2_RUN_ID", f"study_c2_v5_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    quick = _env_bool("C2_QUICK", False)
    max_questions = _env("C2_MAX_QUESTIONS", "5" if run_mode == "smoke" else "")
    smoke_cases = _env("C2_SMOKE_CASES", "5" if run_mode == "smoke" else "")
    rebuild_index = _env_bool("C2_REBUILD_INDEX", run_mode == "smoke")
    enable_dense = _env_bool("C2_ENABLE_DENSE", True)
    enable_rerank = _env_bool("C2_ENABLE_RERANK", True)
    retrieval_profile = _env("RETRIEVAL_PROFILE", "rerank_on" if enable_rerank else "rerank_off")
    output_default = f"outputs/experiments_v5/C_smoke_{retrieval_profile}" if run_mode == "smoke" else f"outputs/experiments_v5/C_{retrieval_profile}"
    output_root = Path(_env("C2_OUTPUT_ROOT", output_default))
    expected_questions_raw = _env("C2_EXPECTED_QUESTIONS", "").strip()
    expected_questions = int(expected_questions_raw) if expected_questions_raw else None

    ollama_base_url = _env("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = _env("OLLAMA_MODEL", "qwen2.5:7b")
    guardrail_provider = _env("EXTERNAL_GUARDRAIL_PROVIDER", "lakera")
    guardrail_stages = _env("EXTERNAL_GUARDRAIL_STAGES", "context")
    guardrail_action = _env("EXTERNAL_GUARDRAIL_ACTION", "block")
    guardrail_fail_mode = _env("EXTERNAL_GUARDRAIL_FAIL_MODE", "open")
    guardrail_api_url = _env("EXTERNAL_GUARDRAIL_API_URL", "")
    guardrail_api_key = _env("EXTERNAL_GUARDRAIL_API_KEY", "")
    guardrail_timeout = _env("EXTERNAL_GUARDRAIL_TIMEOUT_SEC", "10")

    _preflight(experiments_root, questions, conditions)
    source_question_count = _count_questions(questions)
    effective_question_count = source_question_count
    if quick and not max_questions:
        effective_question_count = min(5, source_question_count)
    if max_questions:
        effective_question_count = min(int(max_questions), source_question_count)

    corpus_counts = {name: _count_files(experiments_root / dirname) for name, dirname in V5_CORPORA.items()}
    if expected_questions is not None:
        if source_question_count != expected_questions:
            raise RuntimeError(
                f"Study C2 expects {expected_questions} source questions, "
                f"but loaded {source_question_count} from {questions}."
            )
        required_corpora = _selected_corpora(conditions)
        required_corpora.add("normal")
        wrong_corpora = {name: corpus_counts[name] for name in required_corpora if corpus_counts[name] != expected_questions}
        if wrong_corpora:
            raise RuntimeError(
                f"Study C2 expects {expected_questions} files per v5 corpus, "
                f"but found {wrong_corpora}."
            )

    run_dir = output_root / run_id
    calibration_dir = run_dir / "calibration"
    run_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "run_id": run_id,
        "study": "C2",
        "mode": "rag_guardrail_llm_with_detector_v2_actions",
        "run_mode": run_mode,
        "experiments_root": str(experiments_root),
        "questions": str(questions),
        "conditions": conditions,
        "semantic_actions": semantic_actions,
        "embedding_backend": embedding_backend,
        "quick": quick,
        "max_questions": max_questions,
        "smoke_cases": smoke_cases,
        "retrieval_profile": retrieval_profile,
        "enable_dense": enable_dense,
        "enable_rerank": enable_rerank,
        "expected_questions": expected_questions,
        "source_question_count": source_question_count,
        "effective_question_count": effective_question_count,
        "corpus_counts": corpus_counts,
        "ollama_base_url": ollama_base_url,
        "ollama_model": ollama_model,
        "guardrail_provider": guardrail_provider,
        "guardrail_stages": guardrail_stages,
        "guardrail_action": guardrail_action,
        "guardrail_fail_mode": guardrail_fail_mode,
        "guardrail_api_key": "***redacted***" if guardrail_api_key else "",
    }
    (run_dir / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"[study-c2] run_dir={run_dir}", flush=True)
    print(f"[study-c2] run_mode={run_mode}", flush=True)
    print(f"[study-c2] actions={','.join(semantic_actions)}", flush=True)
    print(f"[study-c2] conditions={conditions}", flush=True)
    print(f"[study-c2] retrieval_profile={retrieval_profile}", flush=True)
    print(f"[study-c2] source_questions={source_question_count}", flush=True)
    print(f"[study-c2] effective_questions={effective_question_count}", flush=True)
    print(f"[study-c2] smoke_cases={smoke_cases or 'all'}", flush=True)
    print(f"[study-c2] corpus_counts={corpus_counts}", flush=True)

    _run(
        [
            sys.executable,
            "-m",
            "semantic_muterag_detector.cli.calibrate_thresholds",
            "--normal-dir",
            str(experiments_root / "experiment_normal"),
            "--questions",
            str(questions),
            "--embedding-backend",
            embedding_backend,
            "--out",
            str(calibration_dir),
        ]
    )
    thresholds_path = calibration_dir / "thresholds.json"

    combined_rows: list[dict[str, Any]] = []
    action_runs: list[dict[str, str]] = []
    for semantic_action in semantic_actions:
        action_name = "semantic_off" if semantic_action == "off" else semantic_action
        action_out = run_dir / action_name
        child_env = os.environ.copy()
        if semantic_action == "off":
            child_env["SEMANTIC_DETECTOR_ENABLED"] = "false"
            child_env["SEMANTIC_DETECTOR_ACTION"] = "off"
        else:
            child_env["SEMANTIC_DETECTOR_ENABLED"] = "true"
            child_env["SEMANTIC_DETECTOR_ACTION"] = semantic_action
            child_env["SEMANTIC_DETECTOR_THRESHOLDS"] = str(thresholds_path)
            child_env["SEMANTIC_DETECTOR_MODE"] = _env("SEMANTIC_DETECTOR_MODE", "improved")
            child_env["SEMANTIC_DETECTOR_BACKEND"] = embedding_backend
            child_env["SEMANTIC_DETECTOR_IMPROVED_THRESHOLD"] = _env("SEMANTIC_DETECTOR_IMPROVED_THRESHOLD", "0.35")
            child_env["SEMANTIC_DETECTOR_WINDOW_ENABLED"] = _env("SEMANTIC_DETECTOR_WINDOW_ENABLED", "true")
            child_env["SEMANTIC_DETECTOR_WINDOW_RADIUS"] = _env("SEMANTIC_DETECTOR_WINDOW_RADIUS", "1")
            child_env["SEMANTIC_DETECTOR_VERDICTS"] = _env("SEMANTIC_DETECTOR_VERDICTS", "muted_candidate")
        child_env["RUNTIME_DETECTOR_ENABLED"] = "false"
        child_env["RUNTIME_SANITIZER_ENABLED"] = "false"
        child_env["RETRIEVAL_PROFILE"] = retrieval_profile

        cmd = [
            sys.executable,
            "scripts/run_v3_experiment.py",
            "--experiments-root",
            str(experiments_root),
            "--questions",
            str(questions),
            "--out",
            str(action_out),
            "--conditions",
            conditions,
            "--ollama-base-url",
            ollama_base_url,
            "--ollama-model",
            ollama_model,
            "--guardrail-provider",
            guardrail_provider,
            "--guardrail-stages",
            guardrail_stages,
            "--guardrail-action",
            guardrail_action,
            "--guardrail-fail-mode",
            guardrail_fail_mode,
            "--guardrail-api-url",
            guardrail_api_url,
            "--guardrail-api-key",
            guardrail_api_key,
            "--guardrail-timeout-sec",
            guardrail_timeout,
        ]
        if quick:
            cmd.append("--quick")
        if max_questions:
            cmd.extend(["--max-questions", max_questions])
        cmd.extend(["--run-mode", run_mode])
        if smoke_cases:
            cmd.extend(["--smoke-cases", smoke_cases])
        if rebuild_index and not action_runs:
            cmd.append("--rebuild-index")
        cmd.append("--enable-dense" if enable_dense else "--no-enable-dense")
        cmd.append("--enable-rerank" if enable_rerank else "--no-enable-rerank")

        _run(cmd, env=child_env)
        child_run = _latest_child_run(action_out)
        action_runs.append({"semantic_action": semantic_action, "run_dir": str(child_run)})
        for row in _read_csv(child_run / "summary.csv"):
            combined = {"semantic_action": semantic_action, "child_run_dir": str(child_run)}
            combined.update(row)
            combined_rows.append(combined)

    fieldnames = [
        "semantic_action",
        "child_run_dir",
        "run_id",
        "condition",
        "corpus_type",
        "guardrail_mode",
        "total_questions",
        "success_count",
        "error_count",
        "semantic_candidate_count",
        "semantic_candidate_rate",
        "semantic_drop_count",
        "semantic_drop_rate",
        "semantic_block_count",
        "semantic_block_rate",
        "context_guardrail_flag_count",
        "context_guardrail_flag_rate",
        "context_guardrail_block_count",
        "context_guardrail_block_rate",
        "prompt_guard_block_count",
        "prompt_guard_block_rate",
        "safety_guard_block_count",
        "safety_guard_block_rate",
        "both_guard_block_count",
        "both_guard_block_rate",
        "llm_called_count",
        "llm_called_rate",
        "normal_response_count",
        "normal_response_rate",
        "refusal_response_count",
        "refusal_response_rate",
        "insufficient_count",
        "insufficient_rate",
        "final_dos_count",
        "final_dos_rate",
        "direct_injection_success_count",
        "direct_injection_success_rate",
        "direct_refusal_or_block_count",
        "direct_refusal_or_block_rate",
        "muted_dos_success_count",
        "muted_dos_success_rate",
        "normal_guardrail_block_baseline_rate",
        "adjusted_muted_dos_rate",
        "guardrail_overblocking_warning",
        "restored_normal_answer_count",
        "restored_normal_answer_rate",
        "target_doc_hit_count",
        "target_doc_hit_rate",
        "attack_context_count",
        "attack_context_rate",
        "avg_duration_sec",
    ]
    _write_csv(run_dir / "c2_summary.csv", combined_rows, fieldnames)
    log_only_run = next((Path(item["run_dir"]) for item in action_runs if item["semantic_action"] == "log_only"), None)
    if log_only_run is not None and log_only_run.exists():
        detected_rows, detected_chunks = _detected_manifest_rows(log_only_run)
        detected_fields = [
            "condition",
            "corpus_type",
            "question_id",
            "question",
            "target_doc",
            "target_doc_id",
            "source_docs",
            "source_chunk_ids",
            "candidate_chunk_ids",
            "candidate_sources",
            "semantic_detector_reason",
            "selected_context_text_preview",
            "answer_class_detail",
        ]
        _write_csv(run_dir / "detected_cases.csv", detected_rows, detected_fields)
        _jsonl_write(run_dir / "detected_chunks.jsonl", detected_chunks)
    (run_dir / "action_runs.json").write_text(
        json.dumps(action_runs, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "README_RESULTS.md").write_text(
        "# Study C2 v5 Results\n\n"
        f"- run_id: `{run_id}`\n"
        f"- retrieval_profile: `{retrieval_profile}`\n"
        f"- summary: `c2_summary.csv`\n"
        f"- detected cases for D: `detected_cases.csv`\n"
        f"- calibration: `{calibration_dir}`\n\n"
        "Default conditions include `normal_on` for detector false-positive measurement and "
        "`muted_blackbox_on,muted_whitebox_on` for MutedRAG detection/mitigation.\n"
        "`detected_cases.csv` intentionally exports muted detections for Study D; normal false positives should be read from `c2_summary.csv`.\n\n"
        "Compare `semantic_off` or `log_only` against `drop_chunk`.\n"
        "`rerank_off` is the primary attack-exposure profile. `rerank_on` should be read as a reranker exposure/removal profile.\n"
        "`adjusted_muted_dos_rate` subtracts the normal guardrail block baseline so Lakera overblocking is visible.\n"
        "The key C2 mitigation signal is whether `context_guardrail_block_rate` decreases and "
        "`normal_response_rate`/`llm_called_rate` recover when `semantic_action=drop_chunk`.\n",
        encoding="utf-8",
    )
    print(f"[study-c2] complete summary={run_dir / 'c2_summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
