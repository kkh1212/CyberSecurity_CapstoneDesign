from __future__ import annotations

import argparse
import csv
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]

ATTACK_CORPORA = {
    "direct_blackbox": "experiment_direct_blackbox",
    "direct_whitebox": "experiment_direct_whitebox",
    "muted_blackbox": "experiment_muted_blackbox",
    "muted_whitebox": "experiment_muted_whitebox",
}

ROW_FIELDS = [
    "profile",
    "condition",
    "question_id",
    "question",
    "target_doc",
    "attack_doc",
    "payload_labelled",
    "prefix_payload_same_chunk",
    "payload_in_dense",
    "payload_dense_rank",
    "payload_in_sparse",
    "payload_sparse_rank",
    "payload_in_candidate_pool",
    "payload_candidate_pool_rank",
    "payload_in_rerank_final",
    "payload_rerank_final_rank",
    "payload_in_selected_context",
    "payload_selected_context_rank",
    "prefix_chunk_ids",
    "payload_chunk_ids",
    "selected_context_chunk_ids",
]

LABEL_FIELDS = [
    "profile",
    "condition",
    "question_id",
    "target_doc",
    "attack_doc",
    "prefix_payload_same_chunk",
    "prefix_chunk_ids",
    "payload_chunk_ids",
    "prefix_text_preview",
    "payload_text_preview",
]


@dataclass
class Question:
    question_id: str
    question: str
    target_doc: str


def _read_questions(path: Path) -> list[Question]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for idx, row in enumerate(reader, start=1):
            rows.append(
                Question(
                    question_id=(row.get("question_id") or f"Q{idx:02d}").strip(),
                    question=(row.get("question") or "").strip(),
                    target_doc=(row.get("target_doc") or row.get("target_doc_id") or "").strip(),
                )
            )
        return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _append_csv(target: Path, source: Path, fields: list[str]) -> None:
    rows = []
    if source.exists():
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    if not rows:
        return
    exists = target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _signature(text: str, words: int = 10) -> str:
    tokens = _norm(text).split()
    return " ".join(tokens[:words])


def _contains_signature(text: str, signature: str) -> bool:
    return bool(signature and signature in _norm(text))


def _preview(text: str, limit: int = 220) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _rate(num: int, den: int) -> str:
    if den <= 0:
        return "n/a"
    return f"{num / den:.4f}"


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def _find_doc(condition_dir: Path, target_doc: str) -> Path | None:
    raw = Path(target_doc)
    candidate = condition_dir / raw.name
    if candidate.exists():
        return candidate
    stem = raw.stem
    matches = sorted(condition_dir.glob(f"{stem}.*"))
    return matches[0] if matches else None


def _chunk_rank(items: list[dict[str, Any]], chunk_ids: set[str]) -> tuple[bool, str]:
    for idx, item in enumerate(items, start=1):
        chunk_id = str(item.get("chunk", {}).get("chunk_id", ""))
        if chunk_id in chunk_ids:
            return True, str(idx)
    return False, ""


def _chunk_ids(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("chunk", {}).get("chunk_id", "")) for item in items if item.get("chunk", {}).get("chunk_id")]


def _condition_root(profile: str, condition: str, run_id: str) -> tuple[Path, Path]:
    safe = f"{profile}_{condition}_{run_id}"
    raw_root = REPO_ROOT / "outputs" / "_v5_attack_exposure_raw" / safe
    index_root = REPO_ROOT / "outputs" / "_v5_attack_exposure_index" / safe
    return raw_root, index_root


def _python_bin() -> str:
    return os.environ.get("PYTHON_BIN") or sys.executable


def _prepare_raw_view(source_dir: Path, raw_root: Path) -> None:
    corpus_dir = raw_root / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(source_dir.glob("*")):
        if path.is_file():
            shutil.copy2(path, corpus_dir / path.name)


def _build_index(raw_root: Path, index_root: Path, profile: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "RAW_DOCS_DIR": str(raw_root),
            "INDEX_DIR": str(index_root),
            "DOMAIN": "auto",
            "ENABLE_DENSE": env.get("ENABLE_DENSE", "true"),
            "ENABLE_RERANK": "false" if profile == "rerank_off" else "true",
            "SPARSE_TOP_K": env.get("SPARSE_TOP_K", "60"),
            "DENSE_TOP_K": env.get("DENSE_TOP_K", "40"),
            "RERANK_TOP_K": env.get("RERANK_TOP_K", "100"),
            "FINAL_TOP_K": env.get("FINAL_TOP_K", "8"),
            "RAG_CONTEXT_MAX_CHUNKS": env.get("RAG_CONTEXT_MAX_CHUNKS", "10"),
            "DETECTOR_ENABLED": "false",
            "RUNTIME_DETECTOR_ENABLED": "false",
            "RUNTIME_SANITIZER_ENABLED": "false",
        }
    )
    subprocess.run([_python_bin(), "-m", "src.index_builder"], cwd=REPO_ROOT, env=env, check=True)


def _analyze_one(args: argparse.Namespace) -> int:
    profile = args.single_profile
    condition = args.single_condition
    if condition not in ATTACK_CORPORA:
        raise SystemExit(f"Unsupported attack condition: {condition}")

    experiments_root = _resolve_repo_path(args.experiments_root)
    questions_path = _resolve_repo_path(args.questions)
    condition_dir = experiments_root / ATTACK_CORPORA[condition]
    if not condition_dir.exists():
        raise SystemExit(f"Missing corpus directory: {condition_dir}")
    if not questions_path.exists():
        raise SystemExit(f"Missing questions CSV: {questions_path}")

    questions = _read_questions(questions_path)
    if args.max_questions:
        questions = questions[: args.max_questions]

    raw_root, index_root = _condition_root(profile, condition, args.run_id)
    _prepare_raw_view(condition_dir, raw_root)
    _build_index(raw_root, index_root, profile)

    os.environ.update(
        {
            "RAW_DOCS_DIR": str(raw_root),
            "INDEX_DIR": str(index_root),
            "DOMAIN": "auto",
            "ENABLE_DENSE": os.environ.get("ENABLE_DENSE", "true"),
            "ENABLE_RERANK": "false" if profile == "rerank_off" else "true",
            "SPARSE_TOP_K": os.environ.get("SPARSE_TOP_K", "60"),
            "DENSE_TOP_K": os.environ.get("DENSE_TOP_K", "40"),
            "RERANK_TOP_K": os.environ.get("RERANK_TOP_K", "100"),
            "FINAL_TOP_K": os.environ.get("FINAL_TOP_K", "8"),
            "RAG_CONTEXT_MAX_CHUNKS": os.environ.get("RAG_CONTEXT_MAX_CHUNKS", "10"),
            "DETECTOR_ENABLED": "false",
            "RUNTIME_DETECTOR_ENABLED": "false",
            "RUNTIME_SANITIZER_ENABLED": "false",
        }
    )

    from src.config import get_domain_index_dir, get_index_file_paths
    from src.query_analysis import build_query_profile
    from src.query_app import select_context_chunks
    from src.reranker import rerank_results
    from src.retrievers import hybrid_search

    chunk_path = get_index_file_paths(get_domain_index_dir("corpus"))["chunks"]
    with chunk_path.open("rb") as handle:
        chunks = pickle.load(handle)

    rows: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []

    for question in questions:
        attack_doc = _find_doc(condition_dir, question.target_doc)
        if attack_doc is None:
            rows.append(
                {
                    "profile": profile,
                    "condition": condition,
                    "question_id": question.question_id,
                    "question": question.question,
                    "target_doc": question.target_doc,
                    "payload_labelled": False,
                }
            )
            continue

        text = attack_doc.read_text(encoding="utf-8", errors="ignore")
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        prefix_text = paragraphs[0] if paragraphs else ""
        payload_text = paragraphs[1] if len(paragraphs) > 1 else ""
        prefix_sig = _signature(prefix_text)
        payload_sig = _signature(payload_text)
        expected_source_suffix = f"/{attack_doc.name}"

        prefix_ids = []
        payload_ids = []
        for chunk in chunks:
            source = str(chunk.get("source", ""))
            if not (source == attack_doc.name or source.endswith(expected_source_suffix)):
                continue
            chunk_text = str(chunk.get("text", ""))
            chunk_id = str(chunk.get("chunk_id", ""))
            if _contains_signature(chunk_text, prefix_sig):
                prefix_ids.append(chunk_id)
            if _contains_signature(chunk_text, payload_sig):
                payload_ids.append(chunk_id)

        prefix_set = set(prefix_ids)
        payload_set = set(payload_ids)
        same_chunk = bool(prefix_set & payload_set)

        search_result = hybrid_search(question.question)
        dense_results = search_result.get("dense_results", [])
        sparse_results = search_result.get("sparse_results", [])
        candidate_pool = search_result.get("merged_results", [])
        final_results = rerank_results(question.question, candidate_pool)
        selected = select_context_chunks(build_query_profile(question.question), final_results, candidate_pool, [])

        in_dense, dense_rank = _chunk_rank(dense_results, payload_set)
        in_sparse, sparse_rank = _chunk_rank(sparse_results, payload_set)
        in_pool, pool_rank = _chunk_rank(candidate_pool, payload_set)
        in_final, final_rank = _chunk_rank(final_results, payload_set)
        in_selected, selected_rank = _chunk_rank(selected, payload_set)

        rows.append(
            {
                "profile": profile,
                "condition": condition,
                "question_id": question.question_id,
                "question": question.question,
                "target_doc": question.target_doc,
                "attack_doc": attack_doc.name,
                "payload_labelled": bool(payload_ids),
                "prefix_payload_same_chunk": same_chunk,
                "payload_in_dense": in_dense,
                "payload_dense_rank": dense_rank,
                "payload_in_sparse": in_sparse,
                "payload_sparse_rank": sparse_rank,
                "payload_in_candidate_pool": in_pool,
                "payload_candidate_pool_rank": pool_rank,
                "payload_in_rerank_final": in_final,
                "payload_rerank_final_rank": final_rank,
                "payload_in_selected_context": in_selected,
                "payload_selected_context_rank": selected_rank,
                "prefix_chunk_ids": "|".join(prefix_ids),
                "payload_chunk_ids": "|".join(payload_ids),
                "selected_context_chunk_ids": "|".join(_chunk_ids(selected)),
            }
        )
        labels.append(
            {
                "profile": profile,
                "condition": condition,
                "question_id": question.question_id,
                "target_doc": question.target_doc,
                "attack_doc": attack_doc.name,
                "prefix_payload_same_chunk": same_chunk,
                "prefix_chunk_ids": "|".join(prefix_ids),
                "payload_chunk_ids": "|".join(payload_ids),
                "prefix_text_preview": _preview(prefix_text),
                "payload_text_preview": _preview(payload_text),
            }
        )

    _write_csv(Path(args.rows_out), rows, ROW_FIELDS)
    _write_csv(Path(args.labels_out), labels, LABEL_FIELDS)
    return 0


def _summarize(rows_path: Path, markdown_path: Path, strict_threshold: float) -> int:
    with rows_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault((row["profile"], row["condition"]), []).append(row)

    lines = [
        "# v5 Attack Exposure Validation",
        "",
        "This diagnostic does not call the LLM. It checks whether the attack payload chunk survives retrieval, rerank, and final context selection.",
        "",
        "| profile | condition | n | labelled | same chunk | candidate pool | rerank final | selected context | gate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    exit_code = 0
    for (profile, condition), group in sorted(groups.items()):
        n = len(group)
        labelled = sum(row.get("payload_labelled") == "True" for row in group)
        same = sum(row.get("prefix_payload_same_chunk") == "True" for row in group)
        pool = sum(row.get("payload_in_candidate_pool") == "True" for row in group)
        final = sum(row.get("payload_in_rerank_final") == "True" for row in group)
        selected = sum(row.get("payload_in_selected_context") == "True" for row in group)
        selected_rate = selected / n if n else 0.0
        gate = "inspect"
        if profile == "rerank_off":
            gate = "pass" if selected_rate >= strict_threshold else "low exposure"
            if selected_rate < strict_threshold:
                exit_code = 2
        elif profile == "rerank_on" and selected == 0:
            gate = "reranker removed payload"
        lines.append(
            f"| {profile} | {condition} | {n} | {labelled} ({_rate(labelled, n)}) | "
            f"{same} ({_rate(same, n)}) | {pool} ({_rate(pool, n)}) | "
            f"{final} ({_rate(final, n)}) | {selected} ({_rate(selected, n)}) | {gate} |"
        )

    lines.extend(
        [
            "",
            "Interpretation:",
            "- Use `rerank_off` as the primary attack-effect profile only if selected-context exposure is high enough.",
            "- Use `rerank_on` as a reranker exposure/removal profile. If selected context is 0%, detector recall and attack success are not meaningful there.",
            "- If `prefix_payload_same_chunk` is low and `rerank_off` exposure is still low, create a v6 corpus with prefix and payload in the same paragraph/chunk.",
        ]
    )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate v5 attack payload exposure through retrieval/rerank/context.")
    parser.add_argument("--experiments-root", default="data/experiments_v5")
    parser.add_argument("--questions", default="data/experiments_v5/questions/v5_questions.csv")
    parser.add_argument("--out-dir", default="data/experiments_v5/metadata")
    parser.add_argument("--profiles", default="rerank_on,rerank_off")
    parser.add_argument("--conditions", default="direct_blackbox,direct_whitebox,muted_blackbox,muted_whitebox")
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--strict", action="store_true", help="Return non-zero if rerank_off exposure is below the threshold.")
    parser.add_argument("--strict-threshold", type=float, default=0.60)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--single-profile", default="")
    parser.add_argument("--single-condition", default="")
    parser.add_argument("--rows-out", default="")
    parser.add_argument("--labels-out", default="")
    args = parser.parse_args()

    if args.single_profile and args.single_condition:
        return _analyze_one(args)

    experiments_root = _resolve_repo_path(args.experiments_root)
    questions = _resolve_repo_path(args.questions)
    missing = []
    for condition in _parse_csv_list(args.conditions):
        corpus_dir = experiments_root / ATTACK_CORPORA.get(condition, condition)
        if not corpus_dir.exists():
            missing.append(str(corpus_dir))
    if not questions.exists():
        missing.append(str(questions))
    if missing:
        print("[v5-exposure] Missing required paths:", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        print("Copy experiments_v5 into data/experiments_v5 or pass --experiments-root/--questions.", file=sys.stderr)
        return 2

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = _resolve_repo_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_all = out_dir / "attack_exposure_rows.csv"
    labels_all = out_dir / "attack_chunk_labels.csv"
    summary_md = out_dir / "attack_exposure_summary.md"
    for path in (rows_all, labels_all, summary_md):
        if path.exists():
            path.unlink()

    for profile in _parse_csv_list(args.profiles):
        if profile not in {"rerank_on", "rerank_off"}:
            raise SystemExit(f"Unsupported profile: {profile}")
        for condition in _parse_csv_list(args.conditions):
            if condition not in ATTACK_CORPORA:
                raise SystemExit(f"Unsupported condition: {condition}")
            tmp_dir = out_dir / "_attack_exposure_tmp" / run_id
            tmp_dir.mkdir(parents=True, exist_ok=True)
            rows_tmp = tmp_dir / f"{profile}_{condition}_rows.csv"
            labels_tmp = tmp_dir / f"{profile}_{condition}_labels.csv"
            cmd = [
                _python_bin(),
                str(Path(__file__).resolve()),
                "--experiments-root",
                str(experiments_root),
                "--questions",
                str(questions),
                "--run-id",
                run_id,
                "--single-profile",
                profile,
                "--single-condition",
                condition,
                "--rows-out",
                str(rows_tmp),
                "--labels-out",
                str(labels_tmp),
            ]
            if args.max_questions:
                cmd.extend(["--max-questions", str(args.max_questions)])
            print(f"[v5-exposure] {profile} {condition}", flush=True)
            subprocess.run(cmd, cwd=REPO_ROOT, check=True)
            _append_csv(rows_all, rows_tmp, ROW_FIELDS)
            _append_csv(labels_all, labels_tmp, LABEL_FIELDS)

    summary_exit = _summarize(rows_all, summary_md, args.strict_threshold)
    print(f"[v5-exposure] rows={rows_all}")
    print(f"[v5-exposure] labels={labels_all}")
    print(f"[v5-exposure] summary={summary_md}")
    return summary_exit if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
