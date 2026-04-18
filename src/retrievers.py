import pickle
import re
from pathlib import Path
from typing import Dict, List, Optional

import faiss
import numpy as np

from src.config import (
    DENSE_TOP_K,
    ENABLE_DENSE,
    RETRIEVAL_INCLUDE_FLAGGED_DEFAULT,
    RETRIEVAL_INCLUDE_QUARANTINED_DEFAULT,
    SPARSE_TOP_K,
    get_domain_index_dir,
    get_index_file_paths,
    list_domain_dirs,
)
from src.detector_pipeline import filter_retrieval_results, log_retrieval_filter_summary, merge_filter_summaries
from src.embedder import embed_texts
from src.query_analysis import build_query_profile, compact_text, normalize_text


def tokenize_text(text: str) -> List[str]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    base_tokens = re.findall(r"[가-힣]+|[a-zA-Z]+|\d+", normalized)

    tokens = list(base_tokens)
    for token in base_tokens:
        if re.fullmatch(r"[가-힣]+", token) and len(token) >= 2:
            tokens.extend(token[i : i + 2] for i in range(len(token) - 1))

    return tokens


def normalize_for_match(text: str) -> str:
    return normalize_text(text.lower())


def contains_term(text: str, term: str) -> bool:
    if not term:
        return False
    normalized_text = normalize_for_match(text)
    if term in normalized_text:
        return True
    return compact_text(term) in compact_text(normalized_text)


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


def load_resources(domain_name: str):
    paths = get_index_file_paths(get_domain_index_dir(domain_name))
    missing = [path for path in paths.values() if not path.exists()]
    if missing:
        missing_str = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(
            "RAG indexes are missing. Build them first with `python -m src.ingest_app`.\n"
            f"Missing: {missing_str}"
        )

    index = faiss.read_index(str(paths["faiss"]))

    with open(paths["chunks"], "rb") as file:
        chunks = pickle.load(file)

    with open(paths["bm25"], "rb") as file:
        bm25 = pickle.load(file)

    return index, chunks, bm25


def dense_search(
    query,
    index,
    chunks,
    top_k=DENSE_TOP_K,
    include_flagged: bool = RETRIEVAL_INCLUDE_FLAGGED_DEFAULT,
    include_quarantined: bool = RETRIEVAL_INCLUDE_QUARANTINED_DEFAULT,
):
    if not chunks:
        return [], {"excluded_flagged": 0, "excluded_quarantined": 0, "excluded": []}

    q_emb = np.asarray(embed_texts(query), dtype="float32")
    search_k = min(max(top_k * 5, top_k), len(chunks))
    scores, indices = index.search(q_emb, search_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        results.append(
            {
                "retrieval_type": "dense",
                "score": float(score),
                "chunk": chunks[idx],
            }
        )

    filtered_results, filter_summary = filter_retrieval_results(
        results,
        include_flagged=include_flagged,
        include_quarantined=include_quarantined,
    )
    return filtered_results[:top_k], filter_summary


def _count_matches(text: str, terms: List[str]) -> int:
    return sum(1 for term in terms if contains_term(text, term))


def compute_keyword_bonus(query: str, chunk: Dict) -> float:
    profile = build_query_profile(query)
    chunk_text = normalize_for_match(chunk.get("text", ""))
    source_text = normalize_for_match(chunk.get("source", ""))
    entity_title = normalize_for_match(chunk.get("entity_title", ""))
    source_title_text = normalize_for_match(source_title(chunk.get("source", "")))
    source_prefix = source_tag(chunk.get("source", ""))
    compact_query = compact_text(profile.query)

    entity_terms = [normalize_for_match(term) for term in profile.entity_terms]
    document_hints = [normalize_for_match(term) for term in profile.document_hints]
    field_terms = [normalize_for_match(field) for field in profile.requested_fields]
    quoted_terms = [normalize_for_match(term) for term in profile.quoted_terms]

    score = 0.0

    for term in quoted_terms:
        if contains_term(source_title_text, term):
            score += 190.0
        elif contains_term(entity_title, term):
            score += 170.0
        elif contains_term(chunk_text, term):
            score += 120.0

    entity_hits = 0
    for term in entity_terms:
        if contains_term(source_title_text, term):
            score += 125.0
            entity_hits += 1
        elif contains_term(entity_title, term):
            score += 95.0
            entity_hits += 1
        elif contains_term(chunk_text, term):
            score += 50.0
            entity_hits += 1
        elif contains_term(source_text, term):
            score += 28.0

    document_hits = 0
    for term in document_hints:
        if contains_term(source_title_text, term) or contains_term(source_text, term):
            score += 130.0
            document_hits += 1
        elif contains_term(chunk_text, term) or contains_term(entity_title, term):
            score += 90.0
            document_hits += 1

    if source_title_text and compact_text(source_title_text) in compact_query:
        score += 240.0

    if source_prefix and re.search(rf"(?<![a-z]){re.escape(source_prefix)}(?![a-z])", profile.query.lower()):
        score += 145.0

    field_hits = 0
    for term in field_terms:
        if contains_term(chunk_text, term):
            score += 14.0
            field_hits += 1

    if entity_hits >= 2:
        score += 35.0
    if field_hits == len(field_terms) and field_hits > 0:
        score += 45.0
    if profile.compare_requested and entity_hits:
        score += 24.0

    block_type = chunk.get("block_type")
    if block_type == "table_row":
        score += 32.0
    elif block_type == "text_section":
        score += 16.0
    elif block_type == "clause_section":
        score += 28.0
    elif block_type == "table":
        score += 8.0

    if (entity_terms or document_hints) and not (entity_hits or document_hits):
        score -= 25.0

    return score


def score_sparse_exact_candidate(query: str, chunk: Dict, raw_score: float) -> float:
    profile = build_query_profile(query)
    text = normalize_for_match(chunk.get("text", ""))
    source = normalize_for_match(chunk.get("source", ""))
    entity_title = normalize_for_match(chunk.get("entity_title", ""))
    source_title_text = normalize_for_match(source_title(chunk.get("source", "")))

    entity_terms = [normalize_for_match(term) for term in profile.entity_terms]
    document_hints = [normalize_for_match(term) for term in profile.document_hints]
    field_terms = [normalize_for_match(field) for field in profile.requested_fields]
    quoted_terms = [normalize_for_match(term) for term in profile.quoted_terms]

    source_title_hits = _count_matches(source_title_text, entity_terms + document_hints + quoted_terms)
    entity_title_hits = _count_matches(entity_title, entity_terms + quoted_terms)
    text_hits = _count_matches(text, entity_terms + document_hints)
    source_hits = _count_matches(source, document_hints)
    field_hits = _count_matches(text, field_terms)

    hit_count = source_title_hits + entity_title_hits + text_hits + source_hits + field_hits
    if hit_count == 0:
        return 0.0

    score = 22.0
    score += 52.0 * source_title_hits
    score += 34.0 * entity_title_hits
    score += 18.0 * text_hits
    score += 20.0 * source_hits
    score += 8.0 * field_hits
    score += min(float(raw_score), 80.0) * 0.45

    block_type = chunk.get("block_type")
    if block_type == "table_row":
        score += 16.0
    elif block_type == "text_section":
        score += 10.0
    elif block_type == "clause_section":
        score += 14.0

    if source_title_hits and (entity_title_hits or text_hits):
        score += 18.0
    if profile.compare_requested and text_hits:
        score += 10.0

    return score


def sparse_search(
    query,
    bm25,
    chunks,
    top_k=SPARSE_TOP_K,
    include_flagged: bool = RETRIEVAL_INCLUDE_FLAGGED_DEFAULT,
    include_quarantined: bool = RETRIEVAL_INCLUDE_QUARANTINED_DEFAULT,
):
    profile = build_query_profile(query)
    tokens = tokenize_text(query)
    scores = bm25.get_scores(tokens)

    rescored = []
    for idx, raw_score in enumerate(scores):
        chunk = chunks[idx]
        total_score = float(raw_score) + compute_keyword_bonus(query, chunk)
        rescored.append(
            {
                "retrieval_type": "sparse",
                "score": total_score,
                "raw_score": float(raw_score),
                "chunk": chunk,
            }
        )

    rescored.sort(key=lambda item: item["score"], reverse=True)

    exact_terms = [normalize_for_match(term) for term in (profile.quoted_terms or profile.entity_terms or profile.document_hints)]
    seen = {item["chunk"]["chunk_id"] for item in rescored[:top_k]}
    per_source_counts: Dict[str, int] = {}
    bonus_matches = []

    if exact_terms:
        exact_candidates = []
        for idx, chunk in enumerate(chunks):
            source_name = chunk.get("source", "")
            raw_score = float(scores[idx])
            exact_score = score_sparse_exact_candidate(query, chunk, raw_score)
            if exact_score <= 0:
                continue
            exact_candidates.append(
                {
                    "retrieval_type": "sparse_exact",
                    "score": exact_score,
                    "raw_score": raw_score,
                    "chunk": chunk,
                    "source_name": source_name,
                }
            )

        exact_candidates.sort(key=lambda item: item["score"], reverse=True)
        for item in exact_candidates:
            chunk_id = item["chunk"]["chunk_id"]
            source_name = item.pop("source_name")
            if chunk_id in seen:
                continue
            if per_source_counts.get(source_name, 0) >= 4:
                continue
            bonus_matches.append(item)
            seen.add(chunk_id)
            per_source_counts[source_name] = per_source_counts.get(source_name, 0) + 1
            if len(bonus_matches) >= top_k * 2:
                break

    combined = rescored[: max(top_k * 5, 24)] + bonus_matches
    combined.sort(key=lambda item: item["score"], reverse=True)
    filtered_results, filter_summary = filter_retrieval_results(
        combined,
        include_flagged=include_flagged,
        include_quarantined=include_quarantined,
    )
    filtered_results.sort(key=lambda item: item["score"], reverse=True)
    return filtered_results[: max(top_k * 3, 24)], filter_summary


def hybrid_search_domain(
    query,
    domain_name: str,
    include_flagged: bool = RETRIEVAL_INCLUDE_FLAGGED_DEFAULT,
    include_quarantined: bool = RETRIEVAL_INCLUDE_QUARANTINED_DEFAULT,
):
    index, chunks, bm25 = load_resources(domain_name)

    dense_results = []
    dense_filter_summary = {"excluded_flagged": 0, "excluded_quarantined": 0, "excluded": []}
    if ENABLE_DENSE:
        try:
            dense_results, dense_filter_summary = dense_search(
                query,
                index,
                chunks,
                include_flagged=include_flagged,
                include_quarantined=include_quarantined,
            )
        except Exception as exc:
            print(f"[WARN] Dense retrieval failed, falling back to sparse-only search: {exc}")

    sparse_results, sparse_filter_summary = sparse_search(
        query,
        bm25,
        chunks,
        include_flagged=include_flagged,
        include_quarantined=include_quarantined,
    )

    merged = []
    seen = set()

    for item in dense_results + sparse_results:
        chunk_id = item["chunk"]["chunk_id"]
        if chunk_id in seen:
            continue
        merged.append(item)
        seen.add(chunk_id)

    merged.sort(key=lambda item: item["score"], reverse=True)
    filter_summary = merge_filter_summaries(dense_filter_summary, sparse_filter_summary)
    log_retrieval_filter_summary(domain_name, filter_summary)

    return {
        "domain": domain_name,
        "dense_results": dense_results,
        "sparse_results": sparse_results,
        "merged_results": merged,
        "filter_summary": filter_summary,
    }


def rank_domain_results(query: str, domain_result: Dict) -> float:
    profile = build_query_profile(query)
    sparse = domain_result["sparse_results"]
    merged = domain_result["merged_results"]

    score = 0.0
    if sparse:
        score += sparse[0]["score"]
        score += sum(item["score"] / (idx + 2) for idx, item in enumerate(sparse[:3]))

    for item in merged[:5]:
        chunk = item["chunk"]
        text = normalize_for_match(chunk.get("text", ""))
        title = normalize_for_match(chunk.get("entity_title", ""))
        source = normalize_for_match(chunk.get("source", ""))
        source_title_text = normalize_for_match(source_title(chunk.get("source", "")))
        for term in profile.entity_terms + profile.document_hints:
            if contains_term(source_title_text, term):
                score += 42.0
            elif contains_term(title, term):
                score += 32.0
            elif contains_term(text, term) or contains_term(source, term):
                score += 18.0

        tag = source_tag(chunk.get("source", ""))
        if tag and re.search(rf"(?<![a-z]){re.escape(tag)}(?![a-z])", profile.query.lower()):
            score += 45.0

    return score


def hybrid_search(
    query,
    preferred_domain: Optional[str] = None,
    include_flagged: bool = RETRIEVAL_INCLUDE_FLAGGED_DEFAULT,
    include_quarantined: bool = RETRIEVAL_INCLUDE_QUARANTINED_DEFAULT,
):
    domain_names = [path.name for path in list_domain_dirs()]
    if preferred_domain and preferred_domain in domain_names:
        domain_names = [preferred_domain]

    domain_results = []
    for domain_name in domain_names:
        try:
            result = hybrid_search_domain(
                query,
                domain_name,
                include_flagged=include_flagged,
                include_quarantined=include_quarantined,
            )
            result["domain_score"] = rank_domain_results(query, result)
            domain_results.append(result)
        except FileNotFoundError:
            continue

    domain_results.sort(key=lambda item: item.get("domain_score", 0.0), reverse=True)

    domains_to_merge = domain_results[:1]
    if len(domain_results) >= 2:
        top_score = domain_results[0].get("domain_score", 0.0)
        second_score = domain_results[1].get("domain_score", 0.0)
        if top_score <= 0.0 or second_score >= top_score * 0.95:
            domains_to_merge = domain_results[:2]

    merged = []
    seen = set()
    for domain_result in domains_to_merge:
        for item in domain_result["merged_results"]:
            chunk_id = item["chunk"]["chunk_id"]
            if chunk_id in seen:
                continue
            merged.append(item)
            seen.add(chunk_id)

    merged.sort(key=lambda item: item["score"], reverse=True)
    filter_summary = merge_filter_summaries(*(item.get("filter_summary", {}) for item in domains_to_merge))

    best = domain_results[0] if domain_results else {"dense_results": [], "sparse_results": [], "merged_results": [], "domain": None, "domain_score": 0.0}
    return {
        "selected_domain": best.get("domain"),
        "domain_score": best.get("domain_score", 0.0),
        "merged_domains": [item.get("domain") for item in domains_to_merge],
        "domain_results": domain_results,
        "dense_results": best.get("dense_results", []),
        "sparse_results": best.get("sparse_results", []),
        "merged_results": merged,
        "filter_summary": filter_summary,
    }
