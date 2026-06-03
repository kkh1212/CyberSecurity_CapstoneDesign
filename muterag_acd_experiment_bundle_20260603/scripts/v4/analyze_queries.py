"""Stage 3: query embedding, similarity matrix, and clustering.

Uses the project's retriever embedding model (src.embedder.embed_texts), the
same model the dense retriever uses, so cluster structure reflects real
retrieval geometry. White-box prefixes are later built from these clusters.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from src.embedder import embed_texts  # noqa: E402
from topics import TOPICS  # noqa: E402

ROOT = Path("data/experiments_v4")
META = ROOT / "metadata"
QUESTIONS = ROOT / "questions" / "v4_questions.csv"

# The spec suggests 0.75/0.80/0.85, but those leave ~40 singletons here
# (questions are deliberately topic-distinct), which makes white-box clustering
# meaningless. The spec allows adjusting the threshold for cluster quality, so we
# search a wider grid and pick the one giving balanced, category-sized clusters.
PRIMARY_THRESHOLDS = [0.75, 0.80, 0.85]
CANDIDATE_THRESHOLDS = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]


def load_questions():
    rows = []
    with QUESTIONS.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(row)
    return rows


def agglomerative_average_linkage(sim: np.ndarray, threshold: float):
    """Merge clusters while max average inter-cluster similarity >= threshold."""
    n = sim.shape[0]
    clusters = [[i] for i in range(n)]
    while len(clusters) > 1:
        best, bi, bj = -1.0, -1, -1
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                pairs = [sim[i, j] for i in clusters[a] for j in clusters[b]]
                avg = float(np.mean(pairs))
                if avg > best:
                    best, bi, bj = avg, a, b
        if best < threshold:
            break
        clusters[bi].extend(clusters[bj])
        del clusters[bj]
    return clusters


def cluster_stats(clusters):
    sizes = sorted((len(c) for c in clusters), reverse=True)
    return {
        "n_clusters": len(clusters),
        "max_size": sizes[0] if sizes else 0,
        "singletons": sum(1 for s in sizes if s == 1),
        "sizes": sizes,
    }


def pick_threshold(sim):
    """Prefer a threshold with no giant cluster and not all singletons."""
    results = {}
    for t in CANDIDATE_THRESHOLDS:
        clusters = agglomerative_average_linkage(sim, t)
        results[t] = (clusters, cluster_stats(clusters))
    # Prefer balanced, category-sized clusters: few singletons, no giant cluster,
    # and a sensible number of clusters (~6-16).
    def score(item):
        t, (cl, st) = item
        penalty = 0.0
        penalty += st["singletons"] * 4.0          # avoid all-singletons
        if st["max_size"] > 12:                      # avoid giant clusters
            penalty += 50.0 * (st["max_size"] - 12)
        n = st["n_clusters"]
        if n < 6:
            penalty += 6.0 * (6 - n)
        elif n > 16:
            penalty += 3.0 * (n - 16)
        return penalty
    chosen_t = min(results.items(), key=score)[0]
    return chosen_t, results


def medoid(members, sim):
    if len(members) == 1:
        return members[0]
    best_i, best_s = members[0], -1.0
    for i in members:
        s = sum(sim[i, j] for j in members if j != i)
        if s > best_s:
            best_s, best_i = s, i
    return best_i


def main():
    META.mkdir(parents=True, exist_ok=True)
    rows = load_questions()
    qids = [r["question_id"] for r in rows]
    questions = [r["question"] for r in rows]
    target_docs = [r["target_doc"] for r in rows]
    kw_by_fname = {t["fname"]: t["keywords"] for t in TOPICS}

    emb = np.asarray(embed_texts(questions), dtype="float64")  # normalized
    sim = emb @ emb.T
    np.clip(sim, -1.0, 1.0, out=sim)

    # 3-1 embeddings
    emb_out = {qids[i]: emb[i].round(6).tolist() for i in range(len(qids))}
    (META / "query_embeddings.json").write_text(
        json.dumps({"model": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
                    "dim": int(emb.shape[1]), "embeddings": emb_out}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    # 3-2 similarity matrix
    with (META / "query_similarity_matrix.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["question_id"] + qids)
        for i in range(len(qids)):
            w.writerow([qids[i]] + [f"{sim[i, j]:.4f}" for j in range(len(qids))])

    # 3-3 clustering
    chosen_t, results = pick_threshold(sim)
    clusters, _ = results[chosen_t]
    clusters = sorted(clusters, key=lambda c: -len(c))

    with (META / "query_clusters.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cluster_id", "representative_question_id", "representative_question",
                    "member_question_ids", "member_questions", "target_docs", "topic_keywords"])
        for ci, members in enumerate(clusters, 1):
            rep = medoid(members, sim)
            kws = []
            for m in members:
                for k in kw_by_fname.get(target_docs[m], "").split(","):
                    k = k.strip()
                    if k and k not in kws:
                        kws.append(k)
            w.writerow([
                f"C{ci:02d}",
                qids[rep],
                questions[rep],
                "|".join(qids[m] for m in members),
                "|".join(questions[m] for m in members),
                "|".join(target_docs[m] for m in members),
                ", ".join(kws[:12]),
            ])

    stats_summary = {str(t): results[t][1] for t in CANDIDATE_THRESHOLDS}
    (META / "clustering_choice.json").write_text(
        json.dumps({"chosen_threshold": chosen_t, "per_threshold": stats_summary,
                    "method": "agglomerative average-linkage, cut at avg-sim >= threshold"},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"chosen_threshold={chosen_t}")
    for t in CANDIDATE_THRESHOLDS:
        print(f"  t={t}: {results[t][1]}")
    print(f"clusters={len(clusters)} sizes={[len(c) for c in clusters]}")


if __name__ == "__main__":
    main()
