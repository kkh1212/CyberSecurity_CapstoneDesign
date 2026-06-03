"""v5 Stage 9b: corpus_generation_report.md (NO retrieval validation) + summary."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from topics_v5 import TOPICS, build_normal_text_v5  # noqa: E402

ROOT = Path("data/experiments_v5")
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
    questions = read_csv(ROOT / "questions" / "v5_questions.csv")
    clusters = read_csv(META / "query_clusters.csv")
    choice = json.loads((META / "clustering_choice.json").read_text(encoding="utf-8"))
    prefixes = read_csv(META / "generated_prefixes.csv")

    normal_chars = [len(build_normal_text_v5(t)) for t in TOPICS]
    normal_words = [len(build_normal_text_v5(t).split()) for t in TOPICS]
    avg_chars = sum(normal_chars) // len(normal_chars)
    avg_words = sum(normal_words) // len(normal_words)

    L = []
    A = L.append
    A("# v5 Corpus Generation Report\n")
    A("Final experiment corpus for studies A/B/C/D, version 5. This version keeps the v4 "
      "question structure, target mapping, and attack design (Direct vs MutedRAG; black-box "
      "P=Q vs white-box optimized prefix; payload placed right after the prefix in the first "
      "chunk) and only EXPANDS the benign documents into longer, richer business documents. "
      "All corpus documents and questions are in English.\n")

    A("## Status")
    A("- experiments_v5 document generation: COMPLETE.")
    A(f"- Normal documents expanded to ~3000-4000 characters (avg {avg_chars} chars / {avg_words} words), "
      "with Purpose, Scope, Roles and Responsibilities, Standard Procedure, Required Information, "
      "Approval or Review Process, Exceptions and Escalation, Records and Retention, "
      "Compliance/Training/Review, and Contact sections.")
    A("- All four attack document sets regenerated from the expanded normal documents "
      "(normal tail taken from the longer v5 content).")
    A("- Query embedding, similarity matrix, clustering, and white-box prefix selection were "
      "recomputed for v5 (v4 metadata was not copied).")
    A("- **Retrieval validation was not executed yet.** It will be run on a separate request; "
      "`retrieval_validation.csv` is intentionally absent.\n")

    A("## 1. Corpus inventory\n")
    A("| directory | documents |")
    A("|---|---|")
    for d in CORPORA:
        A(f"| `{d}` | {counts[d]} |")
    A(f"| questions (`v5_questions.csv`) | {len(questions)} |\n")

    A("## 2. Normal documents\n")
    A(f"- {counts['experiment_normal']} expanded English business documents.")
    A(f"- Length: min {min(normal_chars)} / max {max(normal_chars)} / avg {avg_chars} characters "
      f"(avg {avg_words} words).")
    A("- Eight-to-ten section structure; no attack text in any normal document.\n")

    A("## 3. Questions\n")
    A(f"- {len(questions)} English questions, each mapped 1:1 to a primary target_doc (unchanged from v4).\n")

    A("## 4. Query embedding / similarity / clustering (recomputed for v5)\n")
    A("- Embedding model: `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`, L2-normalized; cosine = dot product.")
    A(f"- Clustering: {choice['method']}; chosen threshold **{choice['chosen_threshold']}**.")
    A(f"- Result: **{len(clusters)} clusters**.\n")
    A("| cluster | size | representative | members |")
    A("|---|---|---|---|")
    for c in clusters:
        m = c["member_question_ids"].split("|")
        A(f"| {c['cluster_id']} | {len(m)} | {c['representative_question_id']} | {', '.join(m)} |")
    A("")

    A("## 5. Attack design (unchanged from v4)\n")
    A("- **MutedRAG** (`M = P + S + normal_tail`): domain-relevant insider-threat request "
      "(\"explain how to ...\"), 5 rotated suffix templates, placed right after the prefix.")
    A("- **Direct** (`M = P + I + normal_tail`): explicit prompt-injection instruction + fictional "
      "employee record, 6 rotated instruction lead-ins.")
    A("- **Black-box**: P = verbatim target query. **White-box**: retriever-optimized, "
      "cluster-spanning prefix.")
    A("- The benign tail now comes from the longer v5 documents, so attack documents also reach ~3000-4000 characters.\n")

    A("## 6. White-box prefix optimization (recomputed for v5)\n")
    A("Per cluster and attack type: template-based candidate expansion; each candidate_doc "
      "(prefix + payload) embedded; avg cosine to member-question embeddings; deterministic "
      "naturalness; total_score = avg_similarity + 0.2 * naturalness; best selected. See "
      "`prefix_candidates.csv` and `generated_prefixes.csv`.\n")
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

    A("## 7. Generated files\n")
    for d in CORPORA:
        A(f"- `data/experiments_v5/{d}/` ({counts[d]} docs)")
    A("- `data/experiments_v5/questions/v5_questions.csv`")
    for m in ["query_embeddings.json", "query_similarity_matrix.csv", "query_clusters.csv",
              "clustering_choice.json", "prefix_candidates.csv", "generated_prefixes.csv",
              "selected_prefixes.json", "corpus_generation_report.md"]:
        A(f"- `data/experiments_v5/metadata/{m}`")
    A("- `data/experiments_v5/metadata/retrieval_validation.csv` — NOT created yet (validation not run).")
    A("- Generation scripts: `scripts/v5/`\n")

    A("## 8. Notes / things to review\n")
    A("- Retrieval validation has not been run; attack documents have not been checked for top-k retrieval.")
    A("- Suffix/payload harm wording is insider-threat themed; review severity for your context.")
    A("- White-box prefix optimization uses dense-cosine similarity to questions as a proxy for the "
      "full hybrid retriever; if validation later shows white-box underperforming, re-optimize.")
    A("- One document slightly exceeds 4000 characters; most are within the 3000-4000 range.\n")

    (META / "corpus_generation_report.md").write_text("\n".join(L), encoding="utf-8")
    print("wrote", META / "corpus_generation_report.md")

    print("\n[experiments_v5 생성 요약]")
    print(f"1. normal 문서 수: {counts['experiment_normal']}")
    print(f"2. normal 문서 평균 길이: {avg_chars} characters ({avg_words} words)")
    print(f"3. direct black-box 문서 수: {counts['experiment_direct_blackbox']}")
    print(f"4. direct white-box 문서 수: {counts['experiment_direct_whitebox']}")
    print(f"5. muted black-box 문서 수: {counts['experiment_muted_blackbox']}")
    print(f"6. muted white-box 문서 수: {counts['experiment_muted_whitebox']}")
    print(f"7. 질문 수: {len(questions)}")
    print("8. embedding 재계산 여부: 예 (v5 질문 기준 재계산)")
    print("9. similarity matrix 재계산 여부: 예 (50x50 재계산)")
    print(f"10. clustering 재수행 여부: 예 ({len(clusters)} clusters, threshold={choice['chosen_threshold']})")
    print("11. white-box prefix 재선정 여부: 예 (v5 클러스터/임베딩 기준 재선정)")
    print("12. retrieval validation 실행 여부: 실행하지 않음")
    print("13. 생성된 주요 파일: data/experiments_v5/{5 corpora, questions/v5_questions.csv, metadata/*(retrieval_validation.csv 제외)}, scripts/v5/*")
    print("14. 사람이 확인해야 할 부분: payload 유해성 수위, white-box prefix proxy 목적함수(검증 시 재최적화 가능성), normal+attack 결합 방식, 4000자 초과 1건")


if __name__ == "__main__":
    main()
