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
    return sum(1 for item in path.iterdir() if item.is_file())


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def main() -> int:
    experiments_root = Path(_env("C2_EXPERIMENTS_ROOT", "data/experiments_v3"))
    questions = Path(_env("C2_QUESTIONS", "data/experiments_v3/questions/v3_questions.csv"))
    output_root = Path(_env("C2_OUTPUT_ROOT", "outputs/experiments_v3/C2"))
    embedding_backend = _env("C2_EMBEDDING_BACKEND", "hashing")
    semantic_actions = [item.strip() for item in _env("C2_SEMANTIC_ACTIONS", "off,log_only,drop_chunk").split(",") if item.strip()]
    conditions = _env("C2_CONDITIONS", "normal_on,direct_on,muted_on")
    run_id = _env("C2_RUN_ID", f"study_c2_v3_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    quick = _env_bool("C2_QUICK", False)
    max_questions = _env("C2_MAX_QUESTIONS", "")
    rebuild_index = _env_bool("C2_REBUILD_INDEX", False)
    enable_dense = _env_bool("C2_ENABLE_DENSE", True)
    enable_rerank = _env_bool("C2_ENABLE_RERANK", True)
    expected_questions_raw = _env("C2_EXPECTED_QUESTIONS", "30").strip()
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

    source_question_count = _count_questions(questions)
    effective_question_count = source_question_count
    if quick and not max_questions:
        effective_question_count = min(5, source_question_count)
    if max_questions:
        effective_question_count = min(int(max_questions), source_question_count)

    corpus_counts = {
        "normal": _count_files(experiments_root / "experiment_normal"),
        "direct": _count_files(experiments_root / "experiment_direct"),
        "muted": _count_files(experiments_root / "experiment_muted"),
    }
    if expected_questions is not None:
        if source_question_count != expected_questions:
            raise RuntimeError(
                f"Study C2 expects {expected_questions} source questions, "
                f"but loaded {source_question_count} from {questions}."
            )
        wrong_corpora = {name: count for name, count in corpus_counts.items() if count != expected_questions}
        if wrong_corpora:
            raise RuntimeError(
                f"Study C2 expects {expected_questions} files per v3 corpus, "
                f"but found {wrong_corpora}."
            )

    run_dir = output_root / run_id
    calibration_dir = run_dir / "calibration"
    run_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "run_id": run_id,
        "study": "C2",
        "mode": "rag_guardrail_llm_with_semantic_detector_actions",
        "experiments_root": str(experiments_root),
        "questions": str(questions),
        "conditions": conditions,
        "semantic_actions": semantic_actions,
        "embedding_backend": embedding_backend,
        "quick": quick,
        "max_questions": max_questions,
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
    print(f"[study-c2] actions={','.join(semantic_actions)}", flush=True)
    print(f"[study-c2] conditions={conditions}", flush=True)
    print(f"[study-c2] source_questions={source_question_count}", flush=True)
    print(f"[study-c2] effective_questions={effective_question_count}", flush=True)
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
            child_env["SEMANTIC_DETECTOR_BACKEND"] = embedding_backend
            child_env["SEMANTIC_DETECTOR_VERDICTS"] = "direct_candidate,muted_candidate"
        child_env["RUNTIME_DETECTOR_ENABLED"] = "false"
        child_env["RUNTIME_SANITIZER_ENABLED"] = "false"

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
        if rebuild_index:
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
        "llm_called_count",
        "llm_called_rate",
        "normal_response_count",
        "normal_response_rate",
        "refusal_response_count",
        "refusal_response_rate",
        "target_doc_hit_count",
        "target_doc_hit_rate",
        "attack_context_count",
        "attack_context_rate",
        "avg_duration_sec",
    ]
    _write_csv(run_dir / "c2_summary.csv", combined_rows, fieldnames)
    (run_dir / "action_runs.json").write_text(
        json.dumps(action_runs, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "README_RESULTS.md").write_text(
        "# Study C2 v3 Results\n\n"
        f"- run_id: `{run_id}`\n"
        f"- summary: `c2_summary.csv`\n"
        f"- calibration: `{calibration_dir}`\n\n"
        "Compare `semantic_off` or `log_only` against `drop_chunk`.\n"
        "The key C2 mitigation signal is whether `context_guardrail_block_rate` decreases and "
        "`normal_response_rate`/`llm_called_rate` recover when `semantic_action=drop_chunk`.\n",
        encoding="utf-8",
    )
    print(f"[study-c2] complete summary={run_dir / 'c2_summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
