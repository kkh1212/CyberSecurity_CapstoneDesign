from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _run(cmd: list[str], cwd: Path = PROJECT_ROOT) -> None:
    print("[run]", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd), check=True)


def main() -> int:
    experiments_root = Path(_env("C_EXPERIMENTS_ROOT", "data/experiments_v3"))
    questions = Path(_env("C_QUESTIONS", "data/experiments_v3/questions/v3_questions.csv"))
    output_root = Path(_env("C_OUTPUT_ROOT", "outputs/experiments_v3/C"))
    embedding_backend = _env("C_EMBEDDING_BACKEND", "hashing")
    corpus_types = _env("C_CORPUS_TYPES", "normal,direct,muted")
    detector_profile = _env("C_LEGACY_DETECTOR_PROFILE", "balanced")
    run_legacy_baseline = _env("C_RUN_LEGACY_BASELINE", "false").lower() in {"1", "true", "yes"}
    run_id = _env("C_RUN_ID", f"study_c_v3_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    run_dir = output_root / run_id
    calibration_dir = run_dir / "calibration"
    eval_dir = run_dir / "detector_eval"
    reports_dir = run_dir / "reports"
    run_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "run_id": run_id,
        "study": "C",
        "mode": "detector_only",
        "experiments_root": str(experiments_root),
        "questions": str(questions),
        "embedding_backend": embedding_backend,
        "corpus_types": corpus_types,
        "output_root": str(output_root),
        "calibration_dir": str(calibration_dir),
        "eval_dir": str(eval_dir),
        "reports_dir": str(reports_dir),
        "legacy_detector_profile": detector_profile,
        "run_legacy_baseline": run_legacy_baseline,
        "strict_attack_verdicts": ["direct_candidate", "muted_candidate"],
        "suspect_policy": "log_only",
    }
    (run_dir / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"[study-c] run_dir={run_dir}", flush=True)
    print(f"[study-c] experiments_root={experiments_root}", flush=True)
    print(f"[study-c] questions={questions}", flush=True)
    print(f"[study-c] embedding_backend={embedding_backend}", flush=True)
    print(f"[study-c] corpus_types={corpus_types}", flush=True)

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
    _run(
        [
            sys.executable,
            "-m",
            "semantic_muterag_detector.cli.eval_v3_corpus",
            "--experiments-root",
            str(experiments_root),
            "--questions",
            str(questions),
            "--thresholds",
            str(calibration_dir / "thresholds.json"),
            "--embedding-backend",
            embedding_backend,
            "--corpus-types",
            corpus_types,
            "--out",
            str(eval_dir),
        ]
    )
    if run_legacy_baseline:
        _run(
            [
                sys.executable,
                "-m",
                "semantic_muterag_detector.cli.eval_legacy_detector",
                "--experiments-root",
                str(experiments_root),
                "--corpus-types",
                corpus_types,
                "--profile",
                detector_profile,
                "--out",
                str(reports_dir / "legacy_detector_baseline.csv"),
            ]
        )
    _run(
        [
            sys.executable,
            "-m",
            "semantic_muterag_detector.cli.write_eval_reports",
            "--eval-dir",
            str(eval_dir),
            "--reports-dir",
            str(reports_dir),
            "--loop-root",
            str(run_dir),
            "--normal-dir",
            str(experiments_root / "experiment_normal"),
            "--direct-dir",
            str(experiments_root / "experiment_direct"),
            "--muted-dir",
            str(experiments_root / "experiment_muted"),
            "--questions",
            str(questions),
        ]
    )

    print("[study-c] complete", flush=True)
    print(f"[study-c] summary={reports_dir / 'detector_eval_summary.md'}", flush=True)
    print(f"[study-c] results={reports_dir / 'detector_eval_results.csv'}", flush=True)
    print(f"[study-c] failures={reports_dir / 'detector_eval_failures.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
