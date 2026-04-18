import pickle
import re
import sys
import time
from pathlib import Path
from typing import Dict, List

from src.config import ENABLE_RERANK, RAW_DOCS_DIR, get_domain_index_dir, get_index_file_paths, get_requested_domain, list_domain_dirs
from src.ollama_client import ask_ollama
from src.prompts import build_general_prompt, build_rag_prompt
from src.query_analysis import QueryProfile, build_query_profile, compact_text, normalize_text
from src.reranker import rerank_results
from src.retrievers import hybrid_search
from src.router import should_use_rag
from src.structured_qa import build_structured_answer


EXCLUDED_DOC_TOKENS = ("question", "questions", "질문예시", "예시질문")


def print_retrieval(dense_results, sparse_results, final_chunks):
    print("\n=== Dense Results ===")
    for item in dense_results:
        print(f"- {item['chunk']['chunk_id']} | {item['score']:.4f}")

    print("\n=== Sparse Results ===")
    for item in sparse_results:
        print(f"- {item['chunk']['chunk_id']} | {item['score']:.4f}")

    title = "Final Results" if not ENABLE_RERANK else "Final Rerank Results"
    print(f"\n=== {title} ===")
    for item in final_chunks:
        print(
            f"- {item['chunk']['chunk_id']} | "
            f"source={item['chunk']['source']} | "
            f"score={item['rerank_score']:.4f}"
        )


def print_sources(final_chunks):
    print("\n=== Sources ===")
    seen = set()
    for item in final_chunks:
        source = item["chunk"]["source"]
        if source in seen:
            continue
        print(f"- {source}")
        seen.add(source)


def print_structured_answer(result):
    chunk = result["chunk"]
    print("\n=== RAG Response ===")
    print(result["answer"])
    print("\n=== Sources ===")
    print(f"- {chunk['source']} ({chunk['chunk_id']})")


def print_debug_info(route: str, elapsed_seconds: float, structured_candidates_count: int = 0, selected_domain: str = "", merged_domains: List[str] | None = None):
    print("\n=== Debug ===")
    print(f"route={route}")
    if selected_domain:
        print(f"selected_domain={selected_domain}")
    if merged_domains:
        print(f"merged_domains={','.join(merged_domains)}")
    if structured_candidates_count:
        print(f"structured_candidates={structured_candidates_count}")
    print(f"elapsed_seconds={elapsed_seconds:.3f}")


def _list_source_docs():
    docs_dir = Path(RAW_DOCS_DIR)
    docs = []
    for pattern in ("*.txt", "*.pdf", "*.docx", "*.doc", "*.hwp", "*.hwpx"):
        docs.extend(
            path
            for path in docs_dir.rglob(pattern)
            if path.is_file() and len(path.relative_to(docs_dir).parts) >= 2
        )
    return sorted(path for path in docs if not any(token in path.stem.lower() for token in EXCLUDED_DOC_TOKENS))


def validate_index_ready():
    docs = _list_source_docs()
    domain_dirs = list_domain_dirs()
    requested_domain = get_requested_domain()

    if not docs or not domain_dirs:
        raise FileNotFoundError(f"No documents found in {RAW_DOCS_DIR}")

    if requested_domain:
        domain_dirs = [domain_dir for domain_dir in domain_dirs if domain_dir.name == requested_domain]
        docs = [doc for doc in docs if doc.relative_to(Path(RAW_DOCS_DIR)).parts[0] == requested_domain]
        if not domain_dirs:
            raise FileNotFoundError(f"Requested domain not found: {requested_domain}")
        if not docs:
            raise FileNotFoundError(f"No documents found for domain: {requested_domain}")

    latest_doc_mtime = max(path.stat().st_mtime for path in docs)
    index_files = []
    for domain_dir in domain_dirs:
        paths = get_index_file_paths(get_domain_index_dir(domain_dir.name))
        missing = [str(path) for path in paths.values() if not path.exists()]
        if missing:
            missing_str = ", ".join(missing)
            raise FileNotFoundError(
                "RAG indexes are missing. Run `python -m src.ingest_app` first.\n"
                f"Missing: {missing_str}"
            )
        index_files.extend(paths.values())

    oldest_index_mtime = min(path.stat().st_mtime for path in index_files)
    if latest_doc_mtime > oldest_index_mtime:
        raise RuntimeError(
            "Documents are newer than the current index. "
            "Run `python -m src.ingest_app` to rebuild embeddings."
        )


def read_query() -> str:
    print("\n질문 입력")
    print("> ", end="", flush=True)
    try:
        return sys.stdin.readline().strip()
    except KeyboardInterrupt:
        print()
        return ""


def load_all_index_chunks(domains: List[str]) -> List[Dict]:
    chunks = []
    for domain in domains:
        paths = get_index_file_paths(get_domain_index_dir(domain))
        with open(paths["chunks"], "rb") as file:
            chunks.extend(pickle.load(file))
    return chunks


def source_title(source: str) -> str:
    stem = Path(source).stem
    match = re.match(r"^[A-Za-z]{2,5}-\d{3}_(.+)$", stem)
    if match:
        stem = match.group(1)
    stem = stem.replace("_", " ").replace("-", " ")
    return normalize_text(stem)


def source_tag(source: str) -> str:
    stem = Path(source).stem
    if "_" in stem:
        prefix = stem.split("_", 1)[0]
        return prefix.split("-", 1)[0].lower()
    return ""


def score_source_name(profile: QueryProfile, source: str) -> float:
    title = source_title(source)
    compact_title = compact_text(title)
    compact_query = compact_text(profile.query)
    score = 0.0

    if compact_title and compact_title in compact_query:
        score += 320.0

    for term in profile.quoted_terms + profile.document_hints + profile.entity_terms:
        if not term:
            continue
        compact_term = compact_text(term)
        if compact_term and compact_term in compact_title:
            score += 130.0

    tag = source_tag(source)
    if tag and re.search(rf"(?<![a-z]){re.escape(tag)}(?![a-z])", profile.query.lower()):
        score += 170.0

    return score


def rank_sources(profile: QueryProfile, merged_results: List[Dict]) -> List[Dict]:
    grouped: Dict[str, List[Dict]] = {}
    for item in merged_results:
        grouped.setdefault(item["chunk"]["source"], []).append(item)

    rankings = []
    for source, items in grouped.items():
        items = sorted(items, key=lambda item: item["score"], reverse=True)
        combined_text = normalize_text(" ".join(item["chunk"].get("text", "") for item in items[:4]))
        source_score = 0.0
        for idx, item in enumerate(items[:4]):
            source_score += item["score"] / (idx + 1)

        source_score += score_source_name(profile, source)

        for hint in profile.document_hints:
            if hint and hint in combined_text:
                source_score += 180.0
            if hint and hint in source:
                source_score += 220.0

        for term in profile.entity_terms:
            if term and term in combined_text:
                source_score += 35.0

        if profile.compare_entities:
            coverage = sum(1 for entity in profile.compare_entities if entity and entity in combined_text)
            source_score += 60.0 * coverage

        rankings.append({"source": source, "items": items, "score": source_score})

    rankings.sort(key=lambda item: item["score"], reverse=True)
    return rankings


def sort_chunks_for_context(items: List[Dict]) -> List[Dict]:
    return sorted(
        items,
        key=lambda item: (
            item["chunk"].get("block_index", 10**9),
            item["chunk"].get("row_index", 10**9),
            item["chunk"].get("start", 10**9),
        ),
    )


def score_chunk_for_profile(profile: QueryProfile, chunk: Dict) -> float:
    text = normalize_text(chunk.get("text", ""))
    source = chunk.get("source", "")
    entity_title = normalize_text(chunk.get("entity_title", ""))
    score = score_source_name(profile, source)

    for term in profile.quoted_terms + profile.document_hints + profile.entity_terms:
        if not term:
            continue
        compact_term = compact_text(term)
        if compact_term and compact_term in compact_text(entity_title):
            score += 120.0
        elif compact_term and compact_term in compact_text(text):
            score += 70.0

    for field in profile.requested_fields:
        if field and field in text:
            score += 22.0

    block_type = chunk.get("block_type")
    if block_type == "table_row":
        score += 45.0
    elif block_type == "text_section":
        score += 30.0
    elif block_type == "paragraph":
        score += 18.0

    return score


def expand_results_for_top_sources(profile: QueryProfile, merged_results: List[Dict], all_index_chunks: List[Dict]) -> List[Dict]:
    source_rankings = rank_sources(profile, merged_results)
    if not source_rankings:
        return merged_results

    max_sources = 1 if profile.summary_requested else 2
    selected_sources = [ranking["source"] for ranking in source_rankings[:max_sources]]
    expanded: List[Dict] = []
    seen = set()

    for source_rank, ranking in enumerate(source_rankings[:max_sources]):
        source_chunks = [chunk for chunk in all_index_chunks if chunk.get("source") == ranking["source"]]
        source_chunks.sort(key=lambda chunk: score_chunk_for_profile(profile, chunk), reverse=True)

        for chunk_rank, chunk in enumerate(source_chunks[:8]):
            chunk_id = chunk["chunk_id"]
            if chunk_id in seen:
                continue
            expanded.append(
                {
                    "retrieval_type": "source_expansion",
                    "score": ranking["score"] + score_chunk_for_profile(profile, chunk) - (chunk_rank * 2.0) - (source_rank * 5.0),
                    "chunk": chunk,
                }
            )
            seen.add(chunk_id)

    for item in merged_results:
        chunk_id = item["chunk"]["chunk_id"]
        if chunk_id in seen:
            continue
        if item["chunk"].get("source") not in selected_sources:
            continue
        expanded.append(item)
        seen.add(chunk_id)

    expanded.sort(key=lambda item: item["score"], reverse=True)
    return expanded or merged_results


def chunk_matches_profile(chunk: Dict, profile: QueryProfile) -> bool:
    text = normalize_text(chunk.get("text", ""))
    source = normalize_text(chunk.get("source", ""))
    entity_title = normalize_text(chunk.get("entity_title", ""))
    haystacks = [text, source, entity_title]

    def matches(term: str) -> bool:
        compact_term = compact_text(term)
        return any(term in haystack or compact_term in compact_text(haystack) for haystack in haystacks if haystack)

    if profile.document_hints and any(matches(term) for term in profile.document_hints):
        return True

    if profile.entity_terms and any(matches(term) for term in profile.entity_terms):
        return True

    return False


def select_context_chunks(profile: QueryProfile, final_chunks: List[Dict], merged_results: List[Dict]) -> List[Dict]:
    if not final_chunks:
        return []

    source_rankings = rank_sources(profile, merged_results)

    if profile.compare_requested and profile.compare_entities:
        chosen = []
        seen = set()
        for entity in profile.compare_entities:
            for item in merged_results:
                text = normalize_text(item["chunk"].get("text", ""))
                title = normalize_text(item["chunk"].get("entity_title", ""))
                if entity in text or entity in title:
                    chunk_id = item["chunk"]["chunk_id"]
                    if chunk_id not in seen:
                        chosen.append(item)
                        seen.add(chunk_id)
                    break
        if chosen:
            return chosen[:6]

    if profile.summary_requested and source_rankings:
        best_source = source_rankings[0]
        return sort_chunks_for_context(best_source["items"])[:6]

    if profile.exact_lookup and source_rankings:
        best_source_items = source_rankings[0]["items"]
        entity_filtered = []
        for item in best_source_items:
            text = normalize_text(item["chunk"].get("text", ""))
            title = normalize_text(item["chunk"].get("entity_title", ""))
            if not profile.entity_terms or any(term in text or term in title for term in profile.entity_terms):
                entity_filtered.append(item)
        return entity_filtered[:6] or best_source_items[:6]

    top_source = final_chunks[0]["chunk"]["source"]
    source_filtered = [item for item in final_chunks if item["chunk"]["source"] == top_source]
    return source_filtered or final_chunks


def collect_structured_candidates(profile: QueryProfile, final_chunks: List[Dict], merged_results: List[Dict]) -> List[Dict]:
    candidates = []
    seen = set()

    source_rankings = rank_sources(profile, merged_results)
    for ranking in source_rankings[:2]:
        for item in ranking["items"][:8]:
            chunk_id = item["chunk"]["chunk_id"]
            if chunk_id in seen:
                continue
            candidates.append(item["chunk"])
            seen.add(chunk_id)

    for item in final_chunks:
        chunk_id = item["chunk"]["chunk_id"]
        if chunk_id in seen:
            continue
        candidates.append(item["chunk"])
        seen.add(chunk_id)

    for item in merged_results[:16]:
        chunk_id = item["chunk"]["chunk_id"]
        if chunk_id in seen:
            continue
        candidates.append(item["chunk"])
        seen.add(chunk_id)

    return candidates


def collect_global_structured_candidates(profile: QueryProfile, all_index_chunks: List[Dict]) -> List[Dict]:
    filtered = []
    seen = set()

    preferred_block_types = {"table_row", "text_section", "table"}
    for chunk in all_index_chunks:
        if chunk.get("block_type") not in preferred_block_types:
            continue
        if not chunk_matches_profile(chunk, profile):
            continue
        chunk_id = chunk.get("chunk_id")
        if chunk_id in seen:
            continue
        filtered.append(chunk)
        seen.add(chunk_id)

    return filtered


def main():
    validate_index_ready()
    started_at = time.perf_counter()

    query = input("질문 입력: ").strip()
    if not query:
        print("질문이 비어 있습니다.")
        return

    requested_domain = get_requested_domain()
    profile = build_query_profile(query)
    search_output = hybrid_search(query, preferred_domain=requested_domain)
    selected_domain = search_output.get("selected_domain") or ""
    merged_domains = search_output.get("merged_domains", [])
    all_index_chunks = load_all_index_chunks(merged_domains or ([selected_domain] if selected_domain else []))
    dense_results = search_output["dense_results"]
    sparse_results = search_output["sparse_results"]
    merged_results = search_output["merged_results"]

    use_rag = should_use_rag(query, dense_results, sparse_results)

    if use_rag:
        final_chunks = rerank_results(query, merged_results)
        if final_chunks:
            print_retrieval(dense_results, sparse_results, final_chunks)
            structured_candidates = collect_structured_candidates(profile, final_chunks, merged_results)
            if profile.entity_terms or profile.document_hints:
                global_structured_candidates = collect_global_structured_candidates(profile, all_index_chunks)
                structured_candidates = global_structured_candidates + structured_candidates
            structured_result = build_structured_answer(query, structured_candidates)
            if structured_result:
                print_structured_answer(structured_result)
                print_debug_info(
                    route="structured",
                    elapsed_seconds=time.perf_counter() - started_at,
                    structured_candidates_count=len(structured_candidates),
                    selected_domain=selected_domain,
                    merged_domains=merged_domains,
                )
                return

            context_chunks = select_context_chunks(profile, final_chunks, merged_results)
            prompt = build_rag_prompt(query, context_chunks)
            response = ask_ollama(prompt)

            print("\n=== RAG Response ===")
            print(response.get("response", ""))
            print_sources(context_chunks)
            print_debug_info(
                route="llm",
                elapsed_seconds=time.perf_counter() - started_at,
                structured_candidates_count=len(structured_candidates),
                selected_domain=selected_domain,
                merged_domains=merged_domains,
            )
            return

    prompt = build_general_prompt(query)
    response = ask_ollama(prompt)

    print("\n=== General LLM Response ===")
    print(response.get("response", ""))
    print_debug_info(route="general", elapsed_seconds=time.perf_counter() - started_at, selected_domain=selected_domain, merged_domains=merged_domains)


def run_query(query: str):
    validate_index_ready()
    started_at = time.perf_counter()

    requested_domain = get_requested_domain()
    profile = build_query_profile(query)
    search_output = hybrid_search(query, preferred_domain=requested_domain)
    selected_domain = search_output.get("selected_domain") or ""
    merged_domains = search_output.get("merged_domains", [])
    all_index_chunks = load_all_index_chunks(merged_domains or ([selected_domain] if selected_domain else []))
    dense_results = search_output["dense_results"]
    sparse_results = search_output["sparse_results"]
    merged_results = search_output["merged_results"]

    use_rag = should_use_rag(query, dense_results, sparse_results)

    if use_rag:
        document_first_results = expand_results_for_top_sources(profile, merged_results, all_index_chunks)
        final_chunks = rerank_results(query, document_first_results)
        if final_chunks:
            print_retrieval(dense_results, sparse_results, final_chunks)
            structured_candidates = collect_structured_candidates(profile, final_chunks, document_first_results)
            if profile.entity_terms or profile.document_hints:
                global_structured_candidates = collect_global_structured_candidates(profile, all_index_chunks)
                structured_candidates = global_structured_candidates + structured_candidates
            structured_result = build_structured_answer(query, structured_candidates)
            if structured_result:
                print_structured_answer(structured_result)
                print_debug_info(
                    route="structured",
                    elapsed_seconds=time.perf_counter() - started_at,
                    structured_candidates_count=len(structured_candidates),
                    selected_domain=selected_domain,
                    merged_domains=merged_domains,
                )
                return

            context_chunks = select_context_chunks(profile, final_chunks, document_first_results)
            prompt = build_rag_prompt(query, context_chunks)
            response = ask_ollama(prompt)

            print("\n=== RAG Response ===")
            print(response.get("response", ""))
            print_sources(context_chunks)
            print_debug_info(
                route="llm",
                elapsed_seconds=time.perf_counter() - started_at,
                structured_candidates_count=len(structured_candidates),
                selected_domain=selected_domain,
                merged_domains=merged_domains,
            )
            return

    prompt = build_general_prompt(query)
    response = ask_ollama(prompt)

    print("\n=== General LLM Response ===")
    print(response.get("response", ""))
    print_debug_info(route="general", elapsed_seconds=time.perf_counter() - started_at, selected_domain=selected_domain, merged_domains=merged_domains)


def cli_main():
    query = read_query()
    if not query:
        print("질문이 비어 있습니다.")
        return
    run_query(query)


if __name__ == "__main__":
    cli_main()
