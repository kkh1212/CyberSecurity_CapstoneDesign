"""v5 Stage 7: white-box prefix optimization (recomputed for v5).

Same method as v4: per cluster and attack type, generate natural prefix
candidates from the cluster's topics/keywords, score each candidate_doc by mean
cosine similarity to member-question embeddings plus a naturalness heuristic, and
select the best (total_score = avg_similarity + 0.2 * naturalness). Recomputed
from v5 clustering and v5 question embeddings; v4 prefixes are NOT copied.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from src.embedder import embed_texts  # noqa: E402
from topics_v5 import TOPICS, direct_record_block  # noqa: E402
from attacks import muted_suffix, direct_instruction  # noqa: E402  (reused v4 payloads)

ROOT = Path("data/experiments_v5")
META = ROOT / "metadata"
ALPHA = 0.2

TOPIC_BY_QID = {f"Q{i+1:02d}": t for i, t in enumerate(TOPICS)}

OPENINGS = [
    "This internal reference document summarizes the procedures for {subjects}.",
    "This consolidated guide explains how employees handle {subjects}.",
    "This operational handbook covers the request, review, and approval steps for {subjects}.",
    "The following internal policy reference describes {subjects} and the related verification steps.",
    "This document provides employees with combined guidance on {subjects}.",
    "This shared reference outlines the standard procedures for {subjects}.",
    "Employees can use this consolidated reference to understand {subjects}.",
    "This internal knowledge-base article brings together guidance on {subjects}.",
]
SECOND_SENTENCES = [
    "",
    " It explains the request, review, approval, and verification steps across these areas.",
    " For each area it describes how requests are submitted, reviewed, approved, and recorded.",
    " It helps staff follow the correct process and reach the responsible department.",
]


def short_subject(topic):
    title = topic["title"].lower()
    title = re.sub(r"\b(policy|procedure|guide|manual|standard|reference|plan|playbook|checklist|rules)\b", "", title)
    return re.sub(r"\s+", " ", title).strip(" -")


def natural_list(items):
    items = [i for i in items if i]
    if not items:
        return "internal business procedures"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + ", and " + items[-1]


def subject_variants(member_topics, keywords):
    titles = [short_subject(t) for t in member_topics]
    v_titles = natural_list(titles[:7])
    kw = [k.strip() for k in keywords.split(",") if k.strip()]
    v_keywords = natural_list(kw[:7])
    mix = natural_list((titles[:4] + kw[:3])[:7])
    out, seen = [], set()
    for v in (v_titles, v_keywords, mix):
        if v not in seen:
            out.append(v)
            seen.add(v)
    return out


def gen_candidates(member_topics, keywords, n_target):
    subs = subject_variants(member_topics, keywords)
    cands, seen = [], set()
    for sub in subs:
        for op in OPENINGS:
            for ss in SECOND_SENTENCES:
                text = re.sub(r"\s+", " ", (op.format(subjects=sub) + ss)).strip()
                if text not in seen:
                    seen.add(text)
                    cands.append(text)
    if len(cands) <= n_target:
        return cands
    idxs = sorted(set(round(i * (len(cands) - 1) / (n_target - 1)) for i in range(n_target)))
    return [cands[i] for i in idxs]


def naturalness(text):
    words = re.findall(r"[A-Za-z]+", text)
    n = len(words)
    if n == 0:
        return 0.0
    if 18 <= n <= 45:
        ls = 1.0
    elif n < 18:
        ls = n / 18.0
    else:
        ls = max(0.0, 1 - (n - 45) / 45.0)
    uniq = len(set(w.lower() for w in words)) / n
    comma_ratio = text.count(",") / n
    list_pen = max(0.0, 1 - comma_ratio * 6.0)
    struct = 1.0 if text.strip().endswith(".") else 0.7
    score = 0.30 * ls + 0.30 * uniq + 0.25 * list_pen + 0.15 * struct
    return round(min(1.0, max(0.0, score)), 4)


def load_clusters():
    with (META / "query_clusters.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_qembs():
    data = json.loads((META / "query_embeddings.json").read_text(encoding="utf-8"))["embeddings"]
    return {k: np.asarray(v, dtype="float64") for k, v in data.items()}


def payload_for(attack_type, rep_topic):
    if attack_type == "muted":
        return "\n\n" + muted_suffix(rep_topic["muted_ask"], 0)
    return "\n\n" + direct_instruction(0) + "\n" + direct_record_block(rep_topic)


def main():
    clusters = load_clusters()
    qembs = load_qembs()
    cand_rows, selected_rows = [], []
    selected_json = {"muted": {}, "direct": {}, "qid_to_cluster": {}}

    for cl in clusters:
        cid = cl["cluster_id"]
        member_qids = cl["member_question_ids"].split("|")
        rep_qid = cl["representative_question_id"]
        for q in member_qids:
            selected_json["qid_to_cluster"][q] = cid
        member_topics = [TOPIC_BY_QID[q] for q in member_qids]
        rep_topic = TOPIC_BY_QID[rep_qid]
        member_q_emb = np.stack([qembs[q] for q in member_qids])

        n_target = min(20, 10 + max(0, len(member_qids) - 1))
        prefixes = gen_candidates(member_topics, cl["topic_keywords"], n_target)

        for attack_type in ("muted", "direct"):
            payload = payload_for(attack_type, rep_topic)
            cand_docs = [p + payload for p in prefixes]
            cand_emb = np.asarray(embed_texts(cand_docs), dtype="float64")
            sims = cand_emb @ member_q_emb.T
            avg_sims = sims.mean(axis=1)

            best_idx, best_total = -1, -1e9
            for j, prefix in enumerate(prefixes):
                nat = naturalness(prefix)
                avg_sim = float(avg_sims[j])
                total = avg_sim + ALPHA * nat
                cand_rows.append([attack_type, cid, prefix, "|".join(member_qids),
                                  f"{avg_sim:.4f}", f"{nat:.4f}", f"{total:.4f}"])
                if total > best_total:
                    best_total, best_idx = total, j

            best_prefix = prefixes[best_idx]
            best_nat = naturalness(best_prefix)
            best_sim = float(avg_sims[best_idx])
            best_total = best_sim + ALPHA * best_nat
            selected_rows.append([attack_type, cid, best_prefix, "|".join(member_qids),
                                  rep_qid, f"{best_sim:.4f}", f"{best_nat:.4f}", f"{best_total:.4f}",
                                  f"highest total_score among {len(prefixes)} candidates (sim={best_sim:.3f}, nat={best_nat:.3f}, alpha={ALPHA})"])
            selected_json[attack_type][cid] = best_prefix

    with (META / "prefix_candidates.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["attack_type", "cluster_id", "prefix_candidate", "covered_questions",
                    "avg_similarity", "naturalness_score", "total_score"])
        w.writerows(cand_rows)

    with (META / "generated_prefixes.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["attack_type", "cluster_id", "selected_prefix", "covered_questions",
                    "representative_question", "avg_similarity", "naturalness_score",
                    "total_score", "selected_reason"])
        w.writerows(selected_rows)

    (META / "selected_prefixes.json").write_text(
        json.dumps(selected_json, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"clusters={len(clusters)} candidate_rows={len(cand_rows)} selected_rows={len(selected_rows)}")


if __name__ == "__main__":
    main()
