import pickle
import re
from math import log
from pathlib import Path
from typing import Dict, List, Optional

try:
    import faiss
except ModuleNotFoundError:  # pragma: no cover - optional in sparse-only environments
    faiss = None
try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - optional in sparse-only environments
    np = None

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
try:
    from src.embedder import embed_texts
except ModuleNotFoundError:  # pragma: no cover - optional in sparse-only environments
    embed_texts = None
from src.query_analysis import build_query_profile, compact_text, normalize_keyword, normalize_text


def tokenize_text(text: str) -> List[str]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    hangul_safe_pattern = r"[가-힣]+|[a-zA-Z]+|\d+"
    base_tokens = re.findall(r"[가-힣]+|[a-zA-Z]+|\d+", normalized)

    base_tokens = re.findall(f"[{chr(0xAC00)}-{chr(0xD7A3)}]+|[a-zA-Z]+|\\d+", normalized)
    tokens = list(base_tokens)
    for token in base_tokens:
        if re.fullmatch(r"[가-힣]+", token) and len(token) >= 2:
            tokens.extend(token[i : i + 2] for i in range(len(token) - 1))

    for token in base_tokens:
        if re.fullmatch(f"[{chr(0xAC00)}-{chr(0xD7A3)}]+", token) and len(token) >= 2:
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


QUERY_NOISE_TERMS = {
    "문서",
    "기준",
    "기준으로",
    "정리",
    "정리해줘",
    "알려줘",
    "알려줄래",
    "설명",
    "내용",
    "관련",
    "자세히",
    "상세",
    "어떻게",
    "무엇",
    "어떤",
    "해주세요",
    "해줘",
    "하는",
    "해야",
}

PROCEDURAL_FIELD_TERMS = {
    "신청",
    "신청서",
    "요청",
    "요청서",
    "승인",
    "절차",
    "검토",
    "제출",
    "자료",
    "서류",
    "방법",
    "단계",
    "순서",
    "과정",
    "확인",
    "등록",
    "검사",
    "반출",
    "발급",
    "기간",
    "기한",
    "목록",
    "주관",
}


def _dedupe_terms(terms: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for term in terms:
        normalized = normalize_for_match(term)
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def _extract_phrase_tokens(text: str) -> List[str]:
    tokens: List[str] = []
    for raw_token in re.findall(f"[{chr(0xAC00)}-{chr(0xD7A3)}A-Za-z0-9]+", normalize_text(text)):
        token = normalize_for_match(normalize_keyword(raw_token))
        if len(token) < 2:
            continue
        tokens.append(token)
    return tokens


def _resolve_procedural_field_token(token: str) -> Optional[str]:
    for field in sorted(PROCEDURAL_FIELD_TERMS, key=len, reverse=True):
        if token == field or token.startswith(field):
            return field
    return None


def _specific_phrase_tokens(text: str) -> List[str]:
    specific = []
    for token in _extract_phrase_tokens(text):
        if token in QUERY_NOISE_TERMS or _resolve_procedural_field_token(token):
            continue
        specific.append(token)
    return specific


def build_query_coverage_terms(profile) -> Dict[str, List[str]]:
    query_tokens = _extract_phrase_tokens(profile.query)
    fallback_field_terms = []
    for token in query_tokens:
        procedural_field = _resolve_procedural_field_token(token)
        if procedural_field:
            fallback_field_terms.append(procedural_field)

    field_terms = _dedupe_terms([*profile.requested_fields, *fallback_field_terms])
    quoted_terms = _dedupe_terms(profile.quoted_terms)

    entity_terms: List[str] = []
    for term in [*profile.quoted_terms, *profile.entity_terms]:
        specific_tokens = _specific_phrase_tokens(term)
        normalized_term = normalize_for_match(term)
        if normalized_term and specific_tokens:
            entity_terms.append(normalized_term)
        entity_terms.extend(specific_tokens)
    entity_terms = [term for term in _dedupe_terms(entity_terms) if term not in field_terms]

    document_hints: List[str] = []
    for hint in profile.document_hints:
        specific_tokens = _specific_phrase_tokens(hint)
        normalized_hint = normalize_for_match(hint)
        if normalized_hint and specific_tokens:
            document_hints.append(normalized_hint)
        document_hints.extend(specific_tokens)
    document_hints = [term for term in _dedupe_terms(document_hints) if term not in field_terms]

    return {
        "entity_terms": entity_terms,
        "field_terms": field_terms,
        "document_hints": document_hints,
        "quoted_terms": quoted_terms,
    }


def chunk_coverage_stats(chunk: Dict, profile, coverage_terms: Optional[Dict[str, List[str]]] = None) -> Dict[str, object]:
    coverage_terms = coverage_terms or build_query_coverage_terms(profile)

    chunk_text = normalize_for_match(chunk.get("text", ""))
    source_text = normalize_for_match(chunk.get("source", ""))
    entity_title = normalize_for_match(chunk.get("entity_title", ""))
    clause_title = normalize_for_match(chunk.get("clause_title", ""))
    source_title_text = normalize_for_match(source_title(chunk.get("source", "")))
    title_texts = [source_title_text, entity_title, clause_title]
    body_texts = [chunk_text, source_text]

    matched_entities = set()
    matched_document_hints = set()
    matched_fields = set()
    matched_title_terms = set()

    for term in coverage_terms.get("entity_terms", []):
        if any(contains_term(text, term) for text in title_texts if text):
            matched_entities.add(term)
            matched_title_terms.add(term)
        elif any(contains_term(text, term) for text in body_texts if text):
            matched_entities.add(term)

    for term in coverage_terms.get("document_hints", []):
        if any(contains_term(text, term) for text in title_texts if text):
            matched_document_hints.add(term)
            matched_title_terms.add(term)
        elif any(contains_term(text, term) for text in body_texts if text):
            matched_document_hints.add(term)

    for term in coverage_terms.get("field_terms", []):
        if any(contains_term(text, term) for text in title_texts if text):
            matched_fields.add(term)
            matched_title_terms.add(term)
        elif any(contains_term(text, term) for text in body_texts if text):
            matched_fields.add(term)

    entity_coverage = len(matched_entities | matched_document_hints)
    field_coverage = len(matched_fields)
    title_coverage = len(matched_title_terms)

    return {
        "entity_terms": sorted(matched_entities),
        "document_hints": sorted(matched_document_hints),
        "field_terms": sorted(matched_fields),
        "title_terms": sorted(matched_title_terms),
        "entity_coverage": entity_coverage,
        "field_coverage": field_coverage,
        "title_coverage": title_coverage,
        "combined_coverage": entity_coverage > 0 and field_coverage > 0,
    }


def source_coverage_stats(items: List[Dict], profile, coverage_terms: Optional[Dict[str, List[str]]] = None) -> Dict[str, object]:
    coverage_terms = coverage_terms or build_query_coverage_terms(profile)

    matched_entities = set()
    matched_document_hints = set()
    matched_fields = set()
    matched_titles = set()

    for item in items:
        chunk = item.get("chunk", item)
        stats = chunk_coverage_stats(chunk, profile, coverage_terms)
        matched_entities.update(stats["entity_terms"])
        matched_document_hints.update(stats["document_hints"])
        matched_fields.update(stats["field_terms"])
        matched_titles.update(stats["title_terms"])

    entity_coverage = len(matched_entities | matched_document_hints)
    field_coverage = len(matched_fields)
    title_coverage = len(matched_titles)

    return {
        "entity_terms": sorted(matched_entities),
        "document_hints": sorted(matched_document_hints),
        "field_terms": sorted(matched_fields),
        "title_terms": sorted(matched_titles),
        "entity_coverage": entity_coverage,
        "field_coverage": field_coverage,
        "title_coverage": title_coverage,
        "combined_coverage": entity_coverage > 0 and field_coverage > 0,
    }


def build_sparse_fallback_index(chunks: List[Dict]) -> Dict[str, object]:
    tokenized_corpus = [tokenize_text(chunk.get("text", "")) for chunk in chunks]
    doc_freq: Dict[str, int] = {}
    doc_lengths: List[int] = []
    for tokens in tokenized_corpus:
        doc_lengths.append(len(tokens))
        for token in set(tokens):
            doc_freq[token] = doc_freq.get(token, 0) + 1

    avgdl = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 0.0
    return {
        "mode": "fallback_sparse",
        "tokenized_corpus": tokenized_corpus,
        "doc_freq": doc_freq,
        "doc_lengths": doc_lengths,
        "avgdl": avgdl,
    }


def _fallback_bm25_scores(index_data: Dict[str, object], query_tokens: List[str]) -> List[float]:
    tokenized_corpus = index_data.get("tokenized_corpus", [])
    doc_freq = index_data.get("doc_freq", {})
    doc_lengths = index_data.get("doc_lengths", [])
    avgdl = float(index_data.get("avgdl", 0.0) or 0.0)

    if not tokenized_corpus:
        return []

    k1 = 1.5
    b = 0.75
    total_docs = len(tokenized_corpus)
    unique_query_tokens = [token for token in dict.fromkeys(query_tokens) if token]
    scores: List[float] = []

    for doc_tokens, doc_length in zip(tokenized_corpus, doc_lengths):
        tf: Dict[str, int] = {}
        for token in doc_tokens:
            tf[token] = tf.get(token, 0) + 1

        score = 0.0
        for token in unique_query_tokens:
            freq = tf.get(token, 0)
            if freq == 0:
                continue
            df = int(doc_freq.get(token, 0))
            idf = log(1.0 + (total_docs - df + 0.5) / (df + 0.5))
            denominator = freq + k1 * (1 - b + b * (doc_length / avgdl if avgdl else 0.0))
            score += idf * ((freq * (k1 + 1)) / max(denominator, 1e-9))

        scores.append(score)

    return scores


def load_resources(domain_name: str):
    paths = get_index_file_paths(get_domain_index_dir(domain_name))
    required_paths = [paths["chunks"], paths["bm25"]]
    if ENABLE_DENSE:
        required_paths.append(paths["faiss"])
    missing = [path for path in required_paths if not path.exists()]
    if missing:
        missing_str = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(
            "RAG indexes are missing. Build them first with `python -m src.ingest_app`.\n"
            f"Missing: {missing_str}"
        )

    if ENABLE_DENSE:
        if faiss is None:
            raise ModuleNotFoundError(
                "faiss is required when ENABLE_DENSE=true. Install faiss or rerun with ENABLE_DENSE=false."
            )
        index = faiss.read_index(str(paths["faiss"]))
    else:
        index = None

    with open(paths["chunks"], "rb") as file:
        chunks = pickle.load(file)

    with open(paths["bm25"], "rb") as file:
        try:
            bm25 = pickle.load(file)
        except Exception:
            bm25 = build_sparse_fallback_index(chunks)

    return index, chunks, bm25


def dense_search(
    query,
    index,
    chunks,
    top_k=DENSE_TOP_K,
    include_flagged: bool = RETRIEVAL_INCLUDE_FLAGGED_DEFAULT,
    include_quarantined: bool = RETRIEVAL_INCLUDE_QUARANTINED_DEFAULT,
    exclude_chunk_ids: Optional[set[str]] = None,
    exclude_sources: Optional[set[str]] = None,
):
    if not chunks:
        return [], {"excluded_flagged": 0, "excluded_quarantined": 0, "excluded": []}
    if np is None or embed_texts is None:
        raise ModuleNotFoundError(
            "Dense retrieval requires numpy and embedding dependencies. "
            "Install them or rerun with ENABLE_DENSE=false."
        )

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
        exclude_chunk_ids=exclude_chunk_ids,
        exclude_sources=exclude_sources,
    )
    return filtered_results[:top_k], filter_summary


def _count_matches(text: str, terms: List[str]) -> int:
    return sum(1 for term in terms if contains_term(text, term))


def compute_keyword_bonus(query: str, chunk: Dict) -> float:
    profile = build_query_profile(query)
    coverage_terms = build_query_coverage_terms(profile)
    chunk_text = normalize_for_match(chunk.get("text", ""))
    source_text = normalize_for_match(chunk.get("source", ""))
    entity_title = normalize_for_match(chunk.get("entity_title", ""))
    clause_title = normalize_for_match(chunk.get("clause_title", ""))
    source_title_text = normalize_for_match(source_title(chunk.get("source", "")))
    source_prefix = source_tag(chunk.get("source", ""))
    compact_query = compact_text(profile.query)

    entity_terms = coverage_terms["entity_terms"]
    document_hints = coverage_terms["document_hints"]
    field_terms = coverage_terms["field_terms"]
    quoted_terms = coverage_terms["quoted_terms"]
    coverage_stats = chunk_coverage_stats(chunk, profile, coverage_terms)

    score = 0.0

    for term in quoted_terms:
        if contains_term(source_title_text, term):
            score += 190.0
        elif contains_term(entity_title, term):
            score += 170.0
        elif contains_term(chunk_text, term):
            score += 120.0

    entity_hits = 0
    source_title_entity_hits = 0
    for term in entity_terms:
        if contains_term(source_title_text, term):
            score += 125.0
            entity_hits += 1
            source_title_entity_hits += 1
        elif contains_term(entity_title, term):
            score += 95.0
            entity_hits += 1
        elif contains_term(clause_title, term):
            score += 88.0
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
        elif contains_term(chunk_text, term) or contains_term(entity_title, term) or contains_term(clause_title, term):
            score += 90.0
            document_hits += 1

    if source_title_text and compact_text(source_title_text) in compact_query:
        score += 240.0

    if source_prefix and re.search(rf"(?<![a-z]){re.escape(source_prefix)}(?![a-z])", profile.query.lower()):
        score += 145.0

    field_hits = 0
    for term in field_terms:
        if contains_term(entity_title, term) or contains_term(clause_title, term):
            score += 20.0
            field_hits += 1
        elif contains_term(chunk_text, term):
            score += 14.0
            field_hits += 1

    entity_coverage = int(coverage_stats["entity_coverage"])
    field_coverage = int(coverage_stats["field_coverage"])
    title_coverage = int(coverage_stats["title_coverage"])

    if entity_hits >= 2:
        score += 35.0
    if source_title_entity_hits and field_hits:
        score += 40.0 * min(source_title_entity_hits, 2)
    if field_hits == len(field_terms) and field_hits > 0:
        score += 45.0
    if entity_hits >= 2 and field_hits:
        score += 55.0
    if coverage_stats["combined_coverage"]:
        score += 48.0 + (10.0 * min(entity_coverage, field_coverage))
    if title_coverage and coverage_stats["combined_coverage"]:
        score += 18.0
    if profile.compare_requested and entity_hits:
        score += 24.0
    if profile.procedure_requested and field_coverage and not entity_coverage:
        score -= 22.0
    if profile.procedure_requested and len(entity_terms) >= 3 and entity_hits == 1:
        score -= 10.0

    block_type = chunk.get("block_type")
    if block_type == "table_row":
        score += 32.0
    elif block_type == "text_section":
        score += 16.0
    elif block_type == "clause_section":
        score += 28.0
    elif block_type == "table":
        score += 8.0

    if (entity_terms or document_hints) and not (entity_coverage or document_hits):
        score -= 25.0

    return score


def score_sparse_exact_candidate(query: str, chunk: Dict, raw_score: float) -> float:
    profile = build_query_profile(query)
    coverage_terms = build_query_coverage_terms(profile)
    text = normalize_for_match(chunk.get("text", ""))
    source = normalize_for_match(chunk.get("source", ""))
    entity_title = normalize_for_match(chunk.get("entity_title", ""))
    clause_title = normalize_for_match(chunk.get("clause_title", ""))
    source_title_text = normalize_for_match(source_title(chunk.get("source", "")))

    entity_terms = coverage_terms["entity_terms"]
    document_hints = coverage_terms["document_hints"]
    field_terms = coverage_terms["field_terms"]
    quoted_terms = coverage_terms["quoted_terms"]
    coverage_stats = chunk_coverage_stats(chunk, profile, coverage_terms)

    source_title_hits = _count_matches(source_title_text, entity_terms + document_hints + quoted_terms)
    entity_title_hits = _count_matches(entity_title, entity_terms + quoted_terms)
    clause_title_hits = _count_matches(clause_title, entity_terms + document_hints + quoted_terms)
    text_hits = _count_matches(text, entity_terms + document_hints)
    source_hits = _count_matches(source, document_hints)
    field_hits = int(coverage_stats["field_coverage"])

    hit_count = source_title_hits + entity_title_hits + clause_title_hits + text_hits + source_hits + field_hits
    if hit_count == 0:
        return 0.0

    score = 22.0
    score += 52.0 * source_title_hits
    score += 34.0 * entity_title_hits
    score += 30.0 * clause_title_hits
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

    entity_coverage = int(coverage_stats["entity_coverage"])
    title_coverage = int(coverage_stats["title_coverage"])
    if source_title_hits and (entity_title_hits or clause_title_hits or text_hits):
        score += 18.0
    if source_title_hits + entity_title_hits + clause_title_hits >= 2:
        score += 16.0
    if coverage_stats["combined_coverage"]:
        score += 22.0 + (8.0 * min(entity_coverage, field_hits))
    if profile.procedure_requested and field_hits and title_coverage:
        score += 14.0
    if profile.procedure_requested and field_hits and not entity_coverage and text_hits <= 1:
        score -= 16.0
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
    exclude_chunk_ids: Optional[set[str]] = None,
    exclude_sources: Optional[set[str]] = None,
):
    profile = build_query_profile(query)
    tokens = tokenize_text(query)
    if hasattr(bm25, "get_scores"):
        scores = bm25.get_scores(tokens)
    else:
        scores = _fallback_bm25_scores(bm25, tokens)

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
        exclude_chunk_ids=exclude_chunk_ids,
        exclude_sources=exclude_sources,
    )
    filtered_results.sort(key=lambda item: item["score"], reverse=True)
    return filtered_results[: max(top_k * 3, 24)], filter_summary


def hybrid_search_domain(
    query,
    domain_name: str,
    include_flagged: bool = RETRIEVAL_INCLUDE_FLAGGED_DEFAULT,
    include_quarantined: bool = RETRIEVAL_INCLUDE_QUARANTINED_DEFAULT,
    exclude_chunk_ids: Optional[set[str]] = None,
    exclude_sources: Optional[set[str]] = None,
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
                exclude_chunk_ids=exclude_chunk_ids,
                exclude_sources=exclude_sources,
            )
        except Exception as exc:
            print(f"[WARN] Dense retrieval failed, falling back to sparse-only search: {exc}")

    sparse_results, sparse_filter_summary = sparse_search(
        query,
        bm25,
        chunks,
        include_flagged=include_flagged,
        include_quarantined=include_quarantined,
        exclude_chunk_ids=exclude_chunk_ids,
        exclude_sources=exclude_sources,
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
    coverage_terms = build_query_coverage_terms(profile)
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
        clause_title = normalize_for_match(chunk.get("clause_title", ""))
        source = normalize_for_match(chunk.get("source", ""))
        source_title_text = normalize_for_match(source_title(chunk.get("source", "")))
        coverage_stats = chunk_coverage_stats(chunk, profile, coverage_terms)
        for term in coverage_terms["entity_terms"] + coverage_terms["document_hints"]:
            if contains_term(source_title_text, term):
                score += 42.0
            elif contains_term(title, term) or contains_term(clause_title, term):
                score += 32.0
            elif contains_term(text, term) or contains_term(source, term):
                score += 18.0

        tag = source_tag(chunk.get("source", ""))
        if tag and re.search(rf"(?<![a-z]){re.escape(tag)}(?![a-z])", profile.query.lower()):
            score += 45.0
        if coverage_stats["combined_coverage"]:
            score += 36.0
        elif coverage_stats["field_coverage"] and not coverage_stats["entity_coverage"]:
            score -= 14.0

    return score


def hybrid_search(
    query,
    preferred_domain: Optional[str] = None,
    include_flagged: bool = RETRIEVAL_INCLUDE_FLAGGED_DEFAULT,
    include_quarantined: bool = RETRIEVAL_INCLUDE_QUARANTINED_DEFAULT,
    exclude_chunk_ids: Optional[set[str]] = None,
    exclude_sources: Optional[set[str]] = None,
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
                exclude_chunk_ids=exclude_chunk_ids,
                exclude_sources=exclude_sources,
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
