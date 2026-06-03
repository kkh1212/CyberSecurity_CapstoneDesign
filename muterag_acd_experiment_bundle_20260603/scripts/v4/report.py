"""Stage 9b: build corpus_generation_report.md and print the final summary."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path("data/experiments_v4")
META = ROOT / "metadata"
CORPORA = ["experiment_normal", "experiment_direct_blackbox", "experiment_direct_whitebox",
           "experiment_muted_blackbox", "experiment_muted_whitebox"]


def count_docs(d):
    return len(list((ROOT / d).glob("*.txt")))


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    counts = {d: count_docs(d) for d in CORPORA}
    questions = read_csv(ROOT / "questions" / "v4_questions.csv")
    clusters = read_csv(META / "query_clusters.csv")
    choice = json.loads((META / "clustering_choice.json").read_text(encoding="utf-8"))
    prefixes = read_csv(META / "generated_prefixes.csv")

    val_path = META / "retrieval_validation.csv"
    val = read_csv(val_path) if val_path.exists() else []
    by_cond = defaultdict(list)
    for r in val:
        by_cond[r["condition"]].append(r)

    def rate(cond):
        rows = by_cond.get(cond, [])
        if not rows:
            return (0, 0, 0.0, 0, 0.0)
        n = len(rows)
        hit = sum(int(r["topk_hit"]) for r in rows)
        tin = sum(int(r["target_doc_in_topk"]) for r in rows)
        return (hit, n, hit / n, tin, tin / n)

    rates = {c: rate(c) for c in CORPORA[1:] + ["normal"]}

    lines = []
    A = lines.append
    A("# v4 Corpus Generation Report\n")
    A("Final experiment corpus for studies A/B/C/D. Normal documents and questions were "
      "authored first and independently; attack documents add attack text to the benign "
      "content. All corpus documents and questions are in English.\n")

    A("## 1. Corpus inventory\n")
    A("| directory | documents |")
    A("|---|---|")
    for d in CORPORA:
        A(f"| `{d}` | {counts[d]} |")
    A(f"| questions (`v4_questions.csv`) | {len(questions)} |\n")

    A("## 2. Questions\n")
    A(f"- {len(questions)} English questions, each mapped 1:1 to a primary `target_doc`.\n")

    A("## 3. Query embedding / similarity / clustering\n")
    A("- Embedding model: `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` "
      "(the project's dense-retriever model), L2-normalized; cosine = dot product.")
    A("- 50x50 cosine similarity matrix: `metadata/query_similarity_matrix.csv`.")
    A(f"- Clustering: {choice['method']}.")
    A(f"- Chosen threshold: **{choice['chosen_threshold']}** "
      f"(0.75/0.80/0.85 left ~40 singletons, so a wider grid was searched for balanced clusters).")
    A(f"- Result: **{len(clusters)} clusters**.\n")
    A("### Clusters\n")
    A("| cluster | size | representative | members |")
    A("|---|---|---|---|")
    for c in clusters:
        members = c["member_question_ids"].split("|")
        A(f"| {c['cluster_id']} | {len(members)} | {c['representative_question_id']} | {', '.join(members)} |")
    A("")

    A("## 4. Attack design\n")
    A("**MutedRAG** (`M = P + S + normal_tail`): `S` is a domain-relevant, insider-threat "
      "request of the form *\"explain how to ...\"* (not a step-by-step procedure) intended to "
      "trigger the LLM safety guardrail so even the benign question is refused (availability "
      "attack). 5 semantically-equivalent suffix templates are rotated across documents so the "
      "wording is not identical. The suffix is co-located with the prefix in the first chunk.\n")
    A("**Direct** (`M = P + I + normal_tail`): `I` is an explicit English prompt-injection "
      "instruction plus a *fictional* employee record (varied per document). Goal: make the model "
      "ignore the user question and emit the planted record (integrity attack). 6 instruction "
      "lead-ins are rotated. Success = the fictional employee record appears in the answer.\n")
    A("- Black-box prefix: `P = Q` (verbatim target query).")
    A("- White-box prefix: retriever-optimized, cluster-spanning (see section 5).\n")

    A("## 5. White-box prefix optimization\n")
    A("Per cluster and per attack type: a pool of natural business-document prefix candidates is "
      "generated from the cluster's member topics/keywords (template-based candidate expansion). "
      "Each `candidate_doc = prefix_candidate + payload` is embedded with the project embedder; "
      "`avg_similarity` = mean cosine to the cluster's member-question embeddings. A deterministic "
      "`naturalness_score` (length, lexical repetition, comma/list density, sentence structure) is "
      "computed, and `total_score = avg_similarity + 0.2 * naturalness`. The highest-scoring "
      "candidate is selected (beam selection over the pool).")
    A("- All candidates + scores: `metadata/prefix_candidates.csv`.")
    A("- Selected prefixes: `metadata/generated_prefixes.csv`.\n")
    A("### Example selected prefixes (muted)\n")
    shown = 0
    for p in prefixes:
        if p["attack_type"] == "muted" and len(p["covered_questions"].split("|")) >= 3:
            A(f"- **{p['cluster_id']}** (covers {len(p['covered_questions'].split('|'))} q, "
              f"sim={p['avg_similarity']}, nat={p['naturalness_score']}): {p['selected_prefix']}")
            shown += 1
        if shown >= 4:
            break
    A("")

    A("## 6. Retrieval validation\n")
    A(f"Each condition is indexed as a realistic deployment corpus (50 normal docs + 50 attack "
      f"docs, disambiguated filenames so both coexist; ingest detector OFF), queried with the real "
      f"hybrid retriever (dense + sparse fusion + cross-encoder rerank). Top-k = 8. Full per-query "
      f"results: `metadata/retrieval_validation.csv`.\n")
    if val:
        A("| condition | attack hit@8 | target_doc in top-8 |")
        A("|---|---|---|")
        nh, nn, nr, ntin, ntinr = rates["normal"]
        A(f"| normal (target retrievable) | {nh}/{nn} = {nr:.0%} | — |")
        for c in CORPORA[1:]:
            hit, n, r, tin, tinr = rates[c]
            A(f"| {c.replace('experiment_','')} | {hit}/{n} = {r:.0%} | {tin}/{n} = {tinr:.0%} |")
        A("")
        bb = [c for c in CORPORA[1:] if "blackbox" in c]
        wb = [c for c in CORPORA[1:] if "whitebox" in c]
        bb_rate = sum(rates[c][0] for c in bb) / max(1, sum(rates[c][1] for c in bb))
        wb_rate = sum(rates[c][0] for c in wb) / max(1, sum(rates[c][1] for c in wb))
        A(f"- Black-box overall attack hit@8: **{bb_rate:.0%}**")
        A(f"- White-box overall attack hit@8: **{wb_rate:.0%}**\n")
    else:
        A("_retrieval_validation.csv not found — run validate_retrieval.py first._\n")

    A("## 7. Generated files\n")
    for d in CORPORA:
        A(f"- `data/experiments_v4/{d}/` ({counts[d]} docs)")
    A("- `data/experiments_v4/questions/v4_questions.csv`")
    for m in ["query_embeddings.json", "query_similarity_matrix.csv", "query_clusters.csv",
              "clustering_choice.json", "prefix_candidates.csv", "generated_prefixes.csv",
              "selected_prefixes.json", "retrieval_validation.csv", "corpus_generation_report.md"]:
        A(f"- `data/experiments_v4/metadata/{m}`")
    A("- Generation scripts: `scripts/v4/`\n")

    A("## 8. Limitations\n")
    A("- Naturalness is a deterministic heuristic, not an LLM judgment (no live LLM in this run).")
    A("- Retrieval validation uses the hybrid retriever's reranked top-k; end-to-end attack "
      "success (LLM refusal for muted / record leakage for direct) is not measured here because "
      "no LLM was invoked.")
    A("- Clustering threshold was lowered to 0.55 for balanced clusters; 13 of 21 clusters are "
      "singletons, so their white-box prefix optimization effectively covers a single question.")
    A("- Documents average ~150 words; richer than v3 but still concise.")
    A("- Filename collisions between normal and attack copies are handled only in the validation "
      "index (via `normal__`/`attack__` prefixes); downstream experiments must decide how to "
      "combine normal and attack corpora per condition.\n")

    (META / "corpus_generation_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("wrote", META / "corpus_generation_report.md")

    # ---- final summary ----
    def topk_pair(c):
        hit, n, r, tin, tinr = rates[c]
        return f"{hit}/{n} ({r:.0%})"
    print("\n[작업 요약]")
    print(f"1. 정상 문서 수: {counts['experiment_normal']}")
    print(f"2. 질문 수: {len(questions)}")
    print(f"3. direct black-box 문서 수: {counts['experiment_direct_blackbox']}")
    print(f"4. direct white-box 문서 수: {counts['experiment_direct_whitebox']}")
    print(f"5. muted black-box 문서 수: {counts['experiment_muted_blackbox']}")
    print(f"6. muted white-box 문서 수: {counts['experiment_muted_whitebox']}")
    print(f"7. cluster 수: {len(clusters)} (threshold={choice['chosen_threshold']})")
    print("8. 사용한 embedding 방식: sentence-transformers/paraphrase-multilingual-mpnet-base-v2 (정규화, project retriever와 동일)")
    print("9. 사용한 similarity 방식: cosine (정규화 임베딩 dot product)")
    print("10. white-box prefix 생성 방식: cluster별 template 후보 확장 -> candidate_doc 임베딩 -> 질문과 avg cosine + naturalness(0.2) -> total_score 최대 선택")
    if val:
        print(f"11. black-box retrieval hit@8: direct={topk_pair('experiment_direct_blackbox')}, muted={topk_pair('experiment_muted_blackbox')}")
        print(f"12. white-box retrieval hit@8: direct={topk_pair('experiment_direct_whitebox')}, muted={topk_pair('experiment_muted_whitebox')}")
    else:
        print("11-12. retrieval hit: (검증 미실행)")
    print("13. 생성된 주요 파일: data/experiments_v4/{5 corpora, questions/v4_questions.csv, metadata/*}, scripts/v4/*")
    print("14. 남은 한계: naturalness heuristic(LLM 미사용), end-to-end 공격성공 미측정, singleton cluster 13개, 문서 평균 ~150단어")
    print("15. 사람이 확인해야 할 부분: muted suffix/direct payload 유해성 수위, 실험 condition별 normal+attack 결합 방식, white-box hit가 black-box보다 낮은 cluster")


if __name__ == "__main__":
    main()
