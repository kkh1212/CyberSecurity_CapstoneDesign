"""v5 retrieval validation (run separately, e.g. from PowerShell).

Usage (from the repo root):
    python scripts\\v5\\validate_retrieval_v5.py            # runs all 5 conditions
    python scripts\\v5\\validate_retrieval_v5.py muted_blackbox   # one condition

For each condition it builds a realistic index (50 normal docs + 50 attack docs,
with disambiguated filenames so both coexist; ingest detector OFF), runs every
question through the real hybrid retriever (dense + sparse fusion + cross-encoder
rerank), and records whether the attack doc and the original target doc appear in
top-k. Results are written to data/experiments_v5/metadata/retrieval_validation.csv.

Models (mpnet embedder + bge-reranker) are expected to be cached; the script runs
offline. Each condition builds an index over ~100 docs and runs 50 queries, so
the full run takes several minutes and uses ~1 GB RAM.
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOPK = 8

CONDITIONS = ["normal", "direct_blackbox", "direct_whitebox", "muted_blackbox", "muted_whitebox"]
COND_ATTACK_DIR = {
    "normal": None,
    "direct_blackbox": "experiment_direct_blackbox",
    "direct_whitebox": "experiment_direct_whitebox",
    "muted_blackbox": "experiment_muted_blackbox",
    "muted_whitebox": "experiment_muted_whitebox",
}

ROOT = Path("data/experiments_v5")
NORMAL = ROOT / "experiment_normal"
QUESTIONS = ROOT / "questions" / "v5_questions.csv"
OUT_CSV = ROOT / "metadata" / "retrieval_validation.csv"


def assemble(cond, val_root, idx_dir):
    corpus = val_root / "corpus"
    shutil.rmtree(val_root, ignore_errors=True)
    shutil.rmtree(idx_dir, ignore_errors=True)
    corpus.mkdir(parents=True)
    for f in sorted(NORMAL.glob("*.txt")):
        shutil.copy2(f, corpus / ("normal__" + f.name))
    attack = COND_ATTACK_DIR[cond]
    if attack:
        for f in sorted((ROOT / attack).glob("*.txt")):
            shutil.copy2(f, corpus / ("attack__" + f.name))


def set_env(val_root, idx_dir):
    os.environ.update({
        "RAW_DOCS_DIR": str(val_root), "INDEX_DIR": str(idx_dir),
        "CHUNK_SIZE": "800", "CHUNK_OVERLAP": "180",
        "ENABLE_DENSE": "true", "ENABLE_RERANK": "true",
        "RERANK_TOP_K": "120", "FINAL_TOP_K": "20",
        "DENSE_TOP_K": "20", "SPARSE_TOP_K": "30",
        "DETECTOR_ENABLED": "false", "RUNTIME_DETECTOR_ENABLED": "false",
        "RUNTIME_SANITIZER_ENABLED": "false", "DOMAIN": "auto",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    })


def load_questions():
    with QUESTIONS.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def ranked_sources(query):
    from src.retrievers import hybrid_search
    from src.reranker import rerank_results
    out = hybrid_search(query)
    final = rerank_results(query, out["merged_results"])
    ranked, seen = [], set()
    for it in final:
        src = os.path.basename(it["chunk"].get("source", ""))
        if src in seen:
            continue
        seen.add(src)
        ranked.append((src, float(it.get("rerank_score", it.get("score", 0.0)))))
    return ranked


def run_one(cond):
    val_root = Path(f"outputs/_v5_val/{cond}")
    idx_dir = Path(f"outputs/_v5_val_idx/{cond}")
    assemble(cond, val_root, idx_dir)
    set_env(val_root, idx_dir)

    build = subprocess.run([sys.executable, "-m", "src.index_builder"],
                           cwd=str(REPO_ROOT), env=os.environ, capture_output=True, text=True)
    if build.returncode != 0:
        sys.stderr.write(build.stdout + build.stderr)
        raise SystemExit(f"index build failed for {cond}")

    sys.path.insert(0, str(REPO_ROOT))
    rows = []
    for r in load_questions():
        q, fname = r["question"], r["target_doc"]
        order_scores = ranked_sources(q)
        order = [s for s, _ in order_scores]
        score_by = dict(order_scores)
        norm_src, atk_src = "normal__" + fname, "attack__" + fname
        target_rank = order.index(norm_src) + 1 if norm_src in order else 0
        attack_rank = order.index(atk_src) + 1 if atk_src in order else 0

        if COND_ATTACK_DIR[cond] is None:
            hit_rank, hit, attack_doc, sim = target_rank, int(0 < target_rank <= TOPK), "", score_by.get(norm_src, 0.0)
        else:
            hit_rank, hit, attack_doc, sim = attack_rank, int(0 < attack_rank <= TOPK), atk_src, score_by.get(atk_src, 0.0)

        rows.append([cond, r["question_id"], q, norm_src, attack_doc, hit, hit_rank,
                     f"{sim:.4f}", int(0 < target_rank <= TOPK), target_rank, " | ".join(order[:TOPK])])

    write_header = not OUT_CSV.exists() or OUT_CSV.stat().st_size == 0
    with OUT_CSV.open("a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if write_header:
            w.writerow(["condition", "question_id", "question", "target_doc", "attack_doc",
                        "topk_hit", "rank", "similarity_score", "target_doc_in_topk",
                        "target_rank", "topk_docs"])
        w.writerows(rows)

    hits = sum(r[5] for r in rows)
    tink = sum(r[8] for r in rows)
    print(f"[{cond}] questions={len(rows)} topk_hit={hits}/{len(rows)} target_in_topk={tink}/{len(rows)} (k={TOPK})")


def run_all():
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    if OUT_CSV.exists():
        OUT_CSV.unlink()
    for cond in CONDITIONS:
        print(f"=== building + querying: {cond} ===", flush=True)
        proc = subprocess.run([sys.executable, str(Path(__file__)), cond], cwd=str(REPO_ROOT))
        if proc.returncode != 0:
            raise SystemExit(f"condition failed: {cond}")
    print(f"\nDONE. Results written to {OUT_CSV}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_one(sys.argv[1])
    else:
        run_all()
