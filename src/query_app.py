import os
import pickle
import re
import sys
import time
from pathlib import Path
from typing import Dict, List

from src.config import (
    DEBUG_CONTEXT_PREVIEW,
    ENABLE_DENSE,
    ENABLE_RERANK,
    RAW_DOCS_DIR,
    RUNTIME_REQUERY_MAX_ATTEMPTS,
    get_domain_index_dir,
    get_index_file_paths,
    get_requested_domain,
    list_domain_dirs,
)
from src.ollama_client import ask_ollama
from src.prompts import build_general_prompt, build_mutedrag_attack_prompt, build_rag_prompt
from src.query_analysis import FIELD_ALIASES, QueryProfile, build_query_profile, compact_text, normalize_text
from src.reranker import rerank_results
from src.retrievers import build_query_coverage_terms, chunk_coverage_stats, hybrid_search, source_coverage_stats
from src.router import should_use_rag
from src.runtime_guard import (
    apply_runtime_guard,
    build_runtime_fallback_message,
    runtime_detector_enabled,
    runtime_sanitizer_enabled,
    runtime_security_mode,
    summarize_runtime_guard,
)
from src.structured_qa import build_structured_answer
from src.chunking import POLICY_DOC_KEYWORDS
from src.detector_pipeline import filter_expansion_chunks

STRUCTURED_FRIENDLY_BLOCK_TYPES = {"table_row", "table", "text_section"}
POLICY_HEAVY_BLOCK_TYPES = {"clause_section", "paragraph", "page_text", "doc_text", "hwp_text", "hwpx_xml"}
NOTICE_DOC_KEYWORDS = ("안내", "절차", "기준", "faq", "q&a")


EXCLUDED_DOC_TOKENS = ("question", "questions", "질문예시", "예시질문")


def mutedrag_attack_eval_enabled() -> bool:
    raw = os.getenv("MUTEDRAG_ATTACK_EVAL")
    return bool(raw and raw.strip().lower() in {"1", "true", "yes", "on"})


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
        display_score = float(item.get("rerank_score", item.get("score", 0.0)))
        print(
            f"- {item['chunk']['chunk_id']} | "
            f"source={item['chunk']['source']} | "
            f"score={display_score:.4f}"
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


def print_debug_info(
    route: str,
    elapsed_seconds: float,
    structured_candidates_count: int = 0,
    selected_domain: str = "",
    merged_domains: List[str] | None = None,
    retrieval_filter_summary: Dict | None = None,
    runtime_guard_summary: Dict | None = None,
    expansion_filter_summary: Dict | None = None,
    security_mode: str = "",
    runtime_detector_flag: bool | None = None,
    runtime_sanitizer_flag: bool | None = None,
    final_result_count: int = 0,
    context_chunk_count: int = 0,
    selected_context_items: List[Dict] | None = None,
    query_coverage_terms: Dict | None = None,
    source_coverage_summary: List[str] | None = None,
    final_result_coverage_summary: List[str] | None = None,
    context_coverage_summary: List[str] | None = None,
):
    print("\n=== Debug ===")
    print(f"route={route}")
    if security_mode:
        print(f"security_mode={security_mode}")
    if runtime_detector_flag is not None:
        print(f"runtime_detector_enabled={str(runtime_detector_flag).lower()}")
    if runtime_sanitizer_flag is not None:
        print(f"runtime_sanitizer_enabled={str(runtime_sanitizer_flag).lower()}")
    if selected_domain:
        print(f"selected_domain={selected_domain}")
    if merged_domains:
        print(f"merged_domains={','.join(merged_domains)}")
    if structured_candidates_count:
        print(f"structured_candidates={structured_candidates_count}")
    if final_result_count:
        print(f"final_result_count={final_result_count}")
    if context_chunk_count:
        print(f"context_chunk_count={context_chunk_count}")
    if retrieval_filter_summary:
        excluded_flagged = retrieval_filter_summary.get("excluded_flagged", 0)
        excluded_quarantined = retrieval_filter_summary.get("excluded_quarantined", 0)
        excluded_runtime = retrieval_filter_summary.get("excluded_runtime", 0)
        if excluded_flagged or excluded_quarantined or excluded_runtime:
            print(f"excluded_flagged={excluded_flagged}")
            print(f"excluded_quarantined={excluded_quarantined}")
            print(f"excluded_runtime={excluded_runtime}")
    if expansion_filter_summary:
        expansion_skipped_flagged = int(expansion_filter_summary.get("expansion_skipped_flagged", 0))
        expansion_skipped_quarantined = int(expansion_filter_summary.get("expansion_skipped_quarantined", 0))
        expansion_skipped_runtime = int(expansion_filter_summary.get("expansion_skipped_runtime", 0))
        if expansion_skipped_flagged or expansion_skipped_quarantined or expansion_skipped_runtime:
            print(f"expansion_skipped_flagged={expansion_skipped_flagged}")
            print(f"expansion_skipped_quarantined={expansion_skipped_quarantined}")
            print(f"expansion_skipped_runtime={expansion_skipped_runtime}")
    if runtime_guard_summary:
        runtime_warning = runtime_guard_summary.get("runtime_configuration_warning", "")
        if runtime_warning:
            print(f"runtime_configuration_warning={runtime_warning}")
        print(f"runtime_risk_level={runtime_guard_summary.get('runtime_risk_level', 'low')}")
        print(f"runtime_action={runtime_guard_summary.get('runtime_action', 'allow')}")
        detector_action = runtime_guard_summary.get("runtime_detector_action")
        if detector_action and detector_action != runtime_guard_summary.get("runtime_action", "allow"):
            print(f"runtime_detector_action={detector_action}")
        print(f"runtime_adjusted_risk={runtime_guard_summary.get('runtime_adjusted_risk', 0.0):.4f}")
        runtime_profile = runtime_guard_summary.get("runtime_profile")
        if runtime_profile:
            print(f"runtime_profile={runtime_profile}")
        runtime_requery_attempt = int(runtime_guard_summary.get("runtime_requery_attempt", 0))
        if runtime_requery_attempt:
            print(f"runtime_requery_attempt={runtime_requery_attempt}")
        removed_chunk_count = int(runtime_guard_summary.get("removed_chunk_count", 0))
        if removed_chunk_count:
            print(f"runtime_removed_chunks={removed_chunk_count}")
        removed_chunk_ids = runtime_guard_summary.get("runtime_removed_chunk_ids", [])
        if removed_chunk_ids:
            print(f"runtime_removed_chunk_ids={','.join(removed_chunk_ids)}")
        triggered_rules = runtime_guard_summary.get("runtime_triggered_rules", [])
        if triggered_rules:
            print(f"runtime_rules={','.join(triggered_rules)}")
        sanitization_rules = runtime_guard_summary.get("runtime_sanitization_rules", [])
        if sanitization_rules:
            print(f"runtime_sanitization_rules={','.join(sanitization_rules)}")
        requery_sources = runtime_guard_summary.get("runtime_requery_sources", [])
        if requery_sources:
            print(f"runtime_requery_sources={','.join(requery_sources)}")
        exclusion_strategy = runtime_guard_summary.get("runtime_exclusion_strategy")
        if exclusion_strategy:
            print(f"runtime_exclusion_strategy={exclusion_strategy}")
        requery_failure_reason = runtime_guard_summary.get("runtime_requery_failure_reason")
        if requery_failure_reason:
            print(f"runtime_requery_failure_reason={requery_failure_reason}")
        remove_failure_reason = runtime_guard_summary.get("runtime_remove_failure_reason")
        if remove_failure_reason:
            print(f"runtime_remove_failure_reason={remove_failure_reason}")
    if selected_context_items:
        selected_chunk_ids = [item["chunk"].get("chunk_id", "") for item in selected_context_items if item.get("chunk")]
        selected_sources: List[str] = []
        selected_block_types: List[str] = []
        for item in selected_context_items:
            chunk = item.get("chunk", {})
            source = chunk.get("source", "")
            block_type = chunk.get("block_type", "")
            if source and source not in selected_sources:
                selected_sources.append(source)
            if block_type and block_type not in selected_block_types:
                selected_block_types.append(block_type)

        if selected_chunk_ids:
            print(f"selected_context_chunk_ids={','.join(selected_chunk_ids)}")
        if selected_sources:
            print(f"selected_context_sources={','.join(selected_sources)}")
        if selected_block_types:
            print(f"selected_context_block_types={','.join(selected_block_types)}")

        if DEBUG_CONTEXT_PREVIEW:
            if query_coverage_terms:
                entity_terms = query_coverage_terms.get("entity_terms", [])
                field_terms = query_coverage_terms.get("field_terms", [])
                document_hints = query_coverage_terms.get("document_hints", [])
                if entity_terms:
                    print(f"query_entity_terms={','.join(entity_terms[:8])}")
                if field_terms:
                    print(f"query_requested_fields={','.join(field_terms[:8])}")
                if document_hints:
                    print(f"query_document_hints={','.join(document_hints[:8])}")
            for idx, summary in enumerate(source_coverage_summary or [], start=1):
                print(f"source_coverage_summary_{idx}={summary}")
            for idx, summary in enumerate(final_result_coverage_summary or [], start=1):
                print(f"final_result_coverage_summary_{idx}={summary}")
            for idx, summary in enumerate(context_coverage_summary or [], start=1):
                print(f"context_coverage_summary_{idx}={summary}")
            for idx, item in enumerate(selected_context_items, start=1):
                preview = re.sub(r"\s+", " ", chunk_content_text(item["chunk"]))[:240].strip()
                print(f"selected_context_preview_{idx}={preview}")
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
        required_paths = [paths["chunks"], paths["bm25"]]
        if ENABLE_DENSE:
            required_paths.append(paths["faiss"])
        missing = [str(path) for path in required_paths if not path.exists()]
        if missing:
            missing_str = ", ".join(missing)
            raise FileNotFoundError(
                "RAG indexes are missing. Run `python -m src.ingest_app` first.\n"
                f"Missing: {missing_str}"
            )
        index_files.extend(required_paths)

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
    coverage_terms = build_query_coverage_terms(profile)
    title = source_title(source)
    compact_title = compact_text(title)
    compact_query = compact_text(profile.query)
    score = 0.0

    if compact_title and compact_title in compact_query:
        score += 320.0

    for term in coverage_terms["quoted_terms"] + coverage_terms["document_hints"] + coverage_terms["entity_terms"]:
        if not term:
            continue
        compact_term = compact_text(term)
        if compact_term and compact_term in compact_title:
            score += 130.0

    tag = source_tag(source)
    if tag and re.search(rf"(?<![a-z]){re.escape(tag)}(?![a-z])", profile.query.lower()):
        score += 170.0

    return score


def source_is_policy_like(source: str) -> bool:
    stem = Path(source).stem
    return any(keyword in stem for keyword in POLICY_DOC_KEYWORDS)


def source_is_notice_like(source: str) -> bool:
    stem = Path(source).stem.lower()
    return any(keyword in stem for keyword in NOTICE_DOC_KEYWORDS)


def should_use_global_structured_candidates(profile: QueryProfile, source_rankings: List[Dict]) -> bool:
    if not profile.exact_lookup:
        return False

    if len(source_rankings) < 2:
        return False

    top_score = source_rankings[0]["score"]
    next_score = source_rankings[1]["score"]
    if top_score <= 0:
        return False

    return next_score >= top_score * 0.92


def should_attempt_structured_route(profile: QueryProfile, final_chunks: List[Dict], structured_candidates: List[Dict]) -> bool:
    if not profile.exact_lookup:
        return False

    if (
        profile.compare_requested
        or profile.procedure_requested
        or profile.multi_document_requested
        or profile.synthesis_requested
    ):
        return False

    if not structured_candidates:
        return False

    top_source = final_chunks[0]["chunk"]["source"] if final_chunks else ""
    if top_source and source_is_policy_like(top_source):
        return False

    if not top_source:
        return True

    same_source_candidates = [chunk for chunk in structured_candidates if chunk.get("source") == top_source]
    if not same_source_candidates:
        return False

    friendly_count = sum(
        1 for chunk in same_source_candidates if chunk.get("block_type") in STRUCTURED_FRIENDLY_BLOCK_TYPES
    )
    policy_count = sum(
        1 for chunk in same_source_candidates if chunk.get("block_type") in POLICY_HEAVY_BLOCK_TYPES
    )

    if friendly_count == 0 and policy_count > 0:
        return False

    return True


def chunk_position_key(item: Dict) -> tuple:
    chunk = item["chunk"]
    return (
        chunk.get("block_index", 10**9),
        chunk.get("sub_block_index", 10**9),
        chunk.get("row_index", 10**9),
        chunk.get("start", 10**9),
    )


def anchor_group_key(item: Dict) -> tuple:
    chunk = item["chunk"]
    source = chunk.get("source", "")
    block_type = chunk.get("block_type", "")
    block_index = chunk.get("block_index")
    clause_title = chunk.get("clause_title", "")

    if block_type == "table_row":
        return (source, "table_group", block_index)
    if block_type == "clause_section" and clause_title:
        return (source, "clause_group", clause_title)
    return (source, block_type, block_index, chunk.get("sub_block_index"))


def select_anchor_items(ranked_items: List[Dict], max_anchors: int) -> List[Dict]:
    anchors: List[Dict] = []
    seen_groups = set()

    for item in ranked_items:
        group_key = anchor_group_key(item)
        if group_key in seen_groups and len(anchors) < max_anchors:
            continue
        anchors.append(item)
        seen_groups.add(group_key)
        if len(anchors) >= max_anchors:
            break

    return anchors


def related_context_indexes(ordered_items: List[Dict], anchor_index: int) -> List[int]:
    if anchor_index < 0 or anchor_index >= len(ordered_items):
        return []

    anchor_chunk = ordered_items[anchor_index]["chunk"]
    source = anchor_chunk.get("source", "")
    block_type = anchor_chunk.get("block_type")
    block_index = anchor_chunk.get("block_index")
    clause_title = anchor_chunk.get("clause_title", "")
    entity_title = normalize_text(anchor_chunk.get("entity_title", ""))

    scored_indexes: List[tuple[float, int]] = []
    for idx, item in enumerate(ordered_items):
        if idx == anchor_index:
            continue
        chunk = item["chunk"]
        if chunk.get("source") != source:
            continue

        distance = abs(idx - anchor_index)
        if distance > 5:
            continue

        candidate_block_type = chunk.get("block_type")
        score = 0.0

        if block_index is not None and chunk.get("block_index") == block_index:
            score += 55.0

        if block_type == "table_row":
            if candidate_block_type == "table":
                score += 80.0
            elif candidate_block_type in {"text_section", "clause_section"}:
                score += 48.0
            elif candidate_block_type == "table_row":
                score += 18.0
        elif block_type == "clause_section":
            if candidate_block_type == "text_section":
                score += 58.0
            elif candidate_block_type == "clause_section":
                score += 30.0
            elif candidate_block_type == "table":
                score += 16.0
        elif block_type == "text_section":
            if candidate_block_type == "clause_section":
                score += 34.0
            elif candidate_block_type == "table":
                score += 22.0
            elif candidate_block_type == "text_section":
                score += 16.0

        if clause_title and chunk.get("clause_title") == clause_title:
            score += 30.0
        if entity_title and normalize_text(chunk.get("entity_title", "")) == entity_title:
            score += 12.0

        score -= distance * 6.0
        if score > 0:
            scored_indexes.append((score, idx))

    scored_indexes.sort(key=lambda item: item[0], reverse=True)
    prioritized = [anchor_index]
    seen = {anchor_index}

    for _, idx in scored_indexes:
        if idx in seen:
            continue
        prioritized.append(idx)
        seen.add(idx)

    for offset in (-1, 1, -2, 2, -3, 3):
        idx = anchor_index + offset
        if 0 <= idx < len(ordered_items) and idx not in seen and ordered_items[idx]["chunk"].get("source") == source:
            prioritized.append(idx)
            seen.add(idx)

    return prioritized


def build_source_anchor_context(items: List[Dict], max_anchors: int, max_total: int) -> List[Dict]:
    if not items:
        return []

    ranked_items = dedupe_chunk_items(
        sorted(
            items,
            key=lambda item: (
                item.get("rerank_score", item.get("score", 0.0)),
                -chunk_position_key(item)[0],
            ),
            reverse=True,
        ),
        max_per_source=max_total,
    )
    anchors = select_anchor_items(ranked_items, max_anchors)
    if not anchors:
        return []

    ordered_items = sort_chunks_for_context(dedupe_chunk_items(items, max_per_source=max_total * 3))
    index_by_chunk_id = {item["chunk"]["chunk_id"]: idx for idx, item in enumerate(ordered_items)}

    chosen: List[Dict] = []
    seen = set()
    for anchor in anchors:
        chunk_id = anchor["chunk"]["chunk_id"]
        anchor_index = index_by_chunk_id.get(chunk_id)
        if anchor_index is None:
            continue

        for neighbor_index in related_context_indexes(ordered_items, anchor_index):
            candidate = ordered_items[neighbor_index]
            candidate_id = candidate["chunk"]["chunk_id"]
            if candidate_id in seen:
                continue
            add_reason = "anchor" if neighbor_index == anchor_index else "neighbor"
            chosen.append({**candidate, "context_add_reason": add_reason})
            seen.add(candidate_id)
            if len(chosen) >= max_total:
                break
        if len(chosen) >= max_total:
            break

    chosen.sort(key=chunk_position_key)
    return dedupe_chunk_items(chosen, max_per_source=max_total)[:max_total]


def build_multisource_context(source_rankings: List[Dict], max_sources: int, max_per_source: int) -> List[Dict]:
    combined: List[Dict] = []
    for ranking in source_rankings[:max_sources]:
        contextual_items = build_source_anchor_context(
            ranking["items"],
            max_anchors=min(2, max_per_source),
            max_total=max_per_source,
        )
        combined.extend(contextual_items)

    return dedupe_chunk_items(combined, max_per_source=max_per_source)[: max_sources * max_per_source]


def score_source_hint_match(hint: str, source: str) -> float:
    normalized_hint = normalize_text(hint)
    compact_hint = compact_text(hint)
    title = source_title(source)
    compact_title = compact_text(title)
    normalized_source = normalize_text(source)

    score = 0.0
    if compact_hint and compact_hint == compact_title:
        score += 300.0
    elif compact_hint and compact_hint in compact_title:
        score += 220.0
    elif compact_title and compact_title in compact_hint:
        score += 180.0

    if normalized_hint and normalized_hint in normalized_source:
        score += 120.0

    hint_tokens = [token for token in re.findall(r"[가-힣A-Za-z0-9]+", normalized_hint) if len(token) >= 2]
    token_hits = sum(1 for token in hint_tokens if token in title or token in normalized_source)
    score += 25.0 * token_hits

    return score


def resolve_requested_sources(profile: QueryProfile, all_index_chunks: List[Dict]) -> List[str]:
    coverage_terms = build_query_coverage_terms(profile)
    if not coverage_terms["document_hints"]:
        return []

    sources = sorted({chunk.get("source", "") for chunk in all_index_chunks if chunk.get("source")})
    resolved: List[str] = []
    seen = set()

    for hint in coverage_terms["document_hints"]:
        ranked_sources = sorted(
            ((source, score_source_hint_match(hint, source)) for source in sources),
            key=lambda item: item[1],
            reverse=True,
        )
        for source, score in ranked_sources:
            if score <= 0:
                continue
            if source in seen:
                continue
            resolved.append(source)
            seen.add(source)
            break

    return resolved


def rank_sources(profile: QueryProfile, merged_results: List[Dict]) -> List[Dict]:
    grouped: Dict[str, List[Dict]] = {}
    for item in merged_results:
        grouped.setdefault(item["chunk"]["source"], []).append(item)

    coverage_terms = build_query_coverage_terms(profile)
    rankings = []
    for source, items in grouped.items():
        items = sorted(items, key=lambda item: item["score"], reverse=True)
        combined_text = normalize_text(" ".join(item["chunk"].get("text", "") for item in items[:4]))
        source_score = 0.0
        for idx, item in enumerate(items[:4]):
            source_score += item["score"] / (idx + 1)

        source_score += score_source_name(profile, source)

        for hint in coverage_terms["document_hints"]:
            if hint and hint in combined_text:
                source_score += 180.0
            if hint and hint in source:
                source_score += 220.0

        for term in coverage_terms["entity_terms"]:
            if term and term in combined_text:
                source_score += 35.0

        if profile.compare_entities:
            coverage = sum(1 for entity in profile.compare_entities if entity and entity in combined_text)
            source_score += 60.0 * coverage

        source_coverage = source_coverage_stats(items[:8], profile, coverage_terms)
        if source_coverage["combined_coverage"]:
            source_score += 60.0 + (12.0 * min(source_coverage["entity_coverage"], source_coverage["field_coverage"]))
        elif source_coverage["field_coverage"] and not source_coverage["entity_coverage"]:
            source_score -= 28.0
        if source_coverage["title_coverage"]:
            source_score += 15.0

        rankings.append({"source": source, "items": items, "score": source_score})

    rankings.sort(key=lambda item: item["score"], reverse=True)
    return rankings


def sort_chunks_for_context(items: List[Dict]) -> List[Dict]:
    return sorted(
        items,
        key=lambda item: (
            item["chunk"].get("block_index", 10**9),
            item["chunk"].get("sub_block_index", 10**9),
            item["chunk"].get("row_index", 10**9),
            item["chunk"].get("start", 10**9),
        ),
    )


def chunk_content_text(chunk: Dict) -> str:
    text = chunk.get("text", "")
    if "\n" in text:
        first_line, remainder = text.split("\n", 1)
        if first_line.startswith("[SOURCE="):
            return remainder.strip()
    return text.strip()


def chunks_are_near_duplicates(left: Dict, right: Dict) -> bool:
    if left.get("chunk_id") == right.get("chunk_id"):
        return True
    if left.get("source") != right.get("source"):
        return False

    left_text = compact_text(chunk_content_text(left))
    right_text = compact_text(chunk_content_text(right))
    if not left_text or not right_text:
        return False

    if left_text == right_text:
        return True

    shorter, longer = sorted((left_text, right_text), key=len)
    if len(shorter) >= 120 and shorter in longer:
        return True

    if left.get("block_index") == right.get("block_index"):
        left_start, left_end = left.get("start", 0), left.get("end", 0)
        right_start, right_end = right.get("start", 0), right.get("end", 0)
        overlap = min(left_end, right_end) - max(left_start, right_start)
        shortest_span = max(1, min(left_end - left_start, right_end - right_start))
        if overlap > 0 and overlap / shortest_span >= 0.7:
            return True

    return False


def dedupe_chunk_items(items: List[Dict], max_per_source: int | None = None) -> List[Dict]:
    deduped: List[Dict] = []
    per_source_counts: Dict[str, int] = {}

    for item in items:
        chunk = item["chunk"]
        source = chunk.get("source", "")
        if max_per_source is not None and per_source_counts.get(source, 0) >= max_per_source:
            continue
        if any(chunks_are_near_duplicates(chunk, existing["chunk"]) for existing in deduped):
            continue
        deduped.append(item)
        per_source_counts[source] = per_source_counts.get(source, 0) + 1

    return deduped


def dedupe_raw_chunks(chunks: List[Dict], max_per_source: int | None = None) -> List[Dict]:
    deduped: List[Dict] = []
    per_source_counts: Dict[str, int] = {}

    for chunk in chunks:
        source = chunk.get("source", "")
        if max_per_source is not None and per_source_counts.get(source, 0) >= max_per_source:
            continue
        if any(chunks_are_near_duplicates(chunk, existing) for existing in deduped):
            continue
        deduped.append(chunk)
        per_source_counts[source] = per_source_counts.get(source, 0) + 1

    return deduped


def chunk_profile_terms(chunk: Dict, profile: QueryProfile) -> set[str]:
    coverage = chunk_coverage_stats(chunk, profile)
    return set(coverage["entity_terms"]) | set(coverage["document_hints"])


def count_profile_field_hits(chunk: Dict, profile: QueryProfile, coverage_terms: Dict | None = None) -> int:
    coverage = chunk_coverage_stats(chunk, profile, coverage_terms)
    return int(coverage["field_coverage"])


def context_profile_coverage(items: List[Dict], profile: QueryProfile) -> tuple[int, int]:
    coverage = source_coverage_stats(items, profile)
    return int(coverage["entity_coverage"]), int(coverage["field_coverage"])


def should_add_support_context(primary_items: List[Dict], profile: QueryProfile) -> bool:
    if not primary_items:
        return True

    block_types = {item["chunk"].get("block_type") for item in primary_items if item.get("chunk")}
    matched_terms, matched_fields = context_profile_coverage(primary_items, profile)
    total_context_chars = sum(len(chunk_content_text(item["chunk"])) for item in primary_items)

    if len(block_types) <= 1:
        return True

    coverage_terms = build_query_coverage_terms(profile)
    if coverage_terms["entity_terms"] and matched_terms < min(3, len(coverage_terms["entity_terms"])):
        return True

    if coverage_terms["field_terms"] and matched_fields < min(2, len(coverage_terms["field_terms"])):
        return True

    if profile.procedure_requested and len(primary_items) <= 2:
        return True

    if profile.procedure_requested and total_context_chars < 800:
        return True

    return False


def format_coverage_summary(label: str, coverage: Dict[str, object], add_reason: str = "", item_count: int | None = None) -> str:
    parts = [
        f"{label}",
        f"entity_coverage={coverage.get('entity_coverage', 0)}",
        f"field_coverage={coverage.get('field_coverage', 0)}",
        f"title_coverage={coverage.get('title_coverage', 0)}",
    ]
    entity_terms = ",".join(list(coverage.get("entity_terms", []))[:4])
    field_terms = ",".join(list(coverage.get("field_terms", []))[:4])
    if entity_terms:
        parts.append(f"entities={entity_terms}")
    if field_terms:
        parts.append(f"fields={field_terms}")
    if item_count is not None:
        parts.append(f"candidate_count={item_count}")
    if add_reason:
        parts.append(f"add_reason={add_reason}")
    return "|".join(parts)


def build_source_debug_summaries(source_rankings: List[Dict], profile: QueryProfile, limit: int = 3) -> List[str]:
    summaries: List[str] = []
    coverage_terms = build_query_coverage_terms(profile)
    for ranking in source_rankings[:limit]:
        coverage = source_coverage_stats(ranking["items"][:8], profile, coverage_terms)
        summaries.append(
            format_coverage_summary(
                label=f"source={ranking['source']}",
                coverage=coverage,
                add_reason="source_rank_debug",
                item_count=len(ranking["items"]),
            )
        )
    return summaries


def build_item_coverage_summaries(items: List[Dict], profile: QueryProfile) -> List[str]:
    summaries: List[str] = []
    coverage_terms = build_query_coverage_terms(profile)
    for item in items:
        chunk = item["chunk"]
        coverage = chunk_coverage_stats(chunk, profile, coverage_terms)
        add_reason = item.get("context_add_reason", item.get("retrieval_type", "selected"))
        summaries.append(
            format_coverage_summary(
                label=f"chunk_id={chunk.get('chunk_id', '')}",
                coverage=coverage,
                add_reason=add_reason,
            )
        )
    return summaries


def annotate_context_items(items: List[Dict], reason: str) -> List[Dict]:
    annotated: List[Dict] = []
    for item in items:
        existing_reason = item.get("context_add_reason", "")
        context_reason = reason if not existing_reason else f"{reason}:{existing_reason}"
        annotated.append({**item, "context_add_reason": context_reason})
    return annotated


def supplement_source_context(
    profile: QueryProfile,
    selected_items: List[Dict],
    source_items: List[Dict],
    max_total: int,
) -> List[Dict]:
    if len(selected_items) >= max_total:
        return selected_items[:max_total]

    coverage_terms = build_query_coverage_terms(profile)
    selected_ids = {item["chunk"]["chunk_id"] for item in selected_items}
    supplement_pool = filter_items_for_profile(source_items, profile)
    if len(supplement_pool) <= len(selected_items):
        supplement_pool = source_items

    candidates = []
    for item in build_source_anchor_context(supplement_pool, max_anchors=2, max_total=max_total):
        chunk_id = item["chunk"]["chunk_id"]
        if chunk_id in selected_ids:
            continue
        coverage = chunk_coverage_stats(item["chunk"], profile, coverage_terms)
        candidates.append(
            (
                int(coverage["combined_coverage"]),
                int(coverage["field_coverage"]),
                int(coverage["entity_coverage"]),
                item.get("rerank_score", item.get("score", 0.0)),
                item,
            )
        )

    candidates.sort(reverse=True)
    supplemented = list(selected_items)
    for _, _, _, _, item in candidates:
        supplemented.append({**item, "context_add_reason": "source_internal_support"})
        if len(supplemented) >= max_total:
            break

    return dedupe_chunk_items(supplemented, max_per_source=max_total)[:max_total]


def with_rerank_fallback(item: Dict) -> Dict:
    normalized = dict(item)
    normalized["rerank_score"] = float(normalized.get("rerank_score", normalized.get("score", 0.0)))
    return normalized


def normalize_ranked_items(items: List[Dict]) -> List[Dict]:
    return [with_rerank_fallback(item) for item in items]


def ensure_source_coverage(
    items: List[Dict],
    candidate_items: List[Dict],
    requested_sources: List[str],
    max_total: int,
) -> List[Dict]:
    if not requested_sources:
        return normalize_ranked_items(items[:max_total])

    selected = dedupe_chunk_items(normalize_ranked_items(items), max_per_source=max_total)[:max_total]
    source_counts: Dict[str, int] = {}
    for item in selected:
        source = item["chunk"].get("source", "")
        source_counts[source] = source_counts.get(source, 0) + 1

    candidate_by_source: Dict[str, List[Dict]] = {}
    for item in normalize_ranked_items(candidate_items):
        source = item["chunk"].get("source", "")
        if source not in requested_sources:
            continue
        candidate_by_source.setdefault(source, []).append(item)

    for source in requested_sources:
        if source_counts.get(source, 0) > 0:
            continue
        source_candidates = dedupe_chunk_items(candidate_by_source.get(source, []), max_per_source=2)
        if not source_candidates:
            continue

        replacement = source_candidates[0]
        if len(selected) < max_total:
            selected.append(replacement)
        else:
            replaced = False
            for idx in range(len(selected) - 1, -1, -1):
                existing_source = selected[idx]["chunk"].get("source", "")
                if source_counts.get(existing_source, 0) <= 1:
                    continue
                source_counts[existing_source] -= 1
                selected[idx] = replacement
                replaced = True
                break
            if not replaced:
                continue
        source_counts[source] = source_counts.get(source, 0) + 1

    return normalize_ranked_items(dedupe_chunk_items(selected, max_per_source=max_total)[:max_total])


def build_evidence_line(context_chunks: List[Dict]) -> str:
    evidence_parts: List[str] = []
    seen_sources = set()

    for item in context_chunks:
        chunk = item["chunk"]
        source = chunk.get("source", "")
        if not source or source in seen_sources:
            continue

        clause_title = chunk.get("clause_title")
        if clause_title:
            evidence_parts.append(f"{source} [{clause_title}]")
        else:
            evidence_parts.append(source)
        seen_sources.add(source)

        if len(evidence_parts) >= 2:
            break

    if not evidence_parts:
        return ""

    return "근거: " + ", ".join(evidence_parts)


def count_field_hits(chunk: Dict, requested_fields: List[str]) -> int:
    if not requested_fields:
        return 0

    haystacks = [
        normalize_text(chunk.get("text", "")),
        normalize_text(chunk.get("clause_title", "")),
        normalize_text(chunk.get("entity_title", "")),
    ]
    compact_haystacks = [compact_text(text) for text in haystacks if text]

    hits = 0
    for field in requested_fields:
        aliases = [field, *FIELD_ALIASES.get(field, [])]
        matched = False
        for alias in aliases:
            normalized_alias = normalize_text(alias)
            compact_alias = compact_text(alias)
            if any(normalized_alias in text for text in haystacks if text) or any(compact_alias in text for text in compact_haystacks):
                matched = True
                break
        if matched:
            hits += 1
    return hits


def normalize_rag_response_text(response_text: str, context_chunks: List[Dict]) -> str:
    text = (response_text or "").strip()
    if not text:
        evidence_line = build_evidence_line(context_chunks)
        return evidence_line if evidence_line else ""

    replacements = [
        ("이라는 조건이 Applies.", "입니다."),
        ("이라는 조건이 Applies", "입니다"),
        ("라는 조건이 Applies.", "입니다."),
        ("라는 조건이 Applies", "입니다"),
        ("조건이 Applies.", "조건입니다."),
        ("조건이 Applies", "조건입니다"),
        ("Applies.", "적용됩니다."),
        ("Applies", "적용됩니다"),
    ]
    for before, after in replacements:
        text = text.replace(before, after)

    text = re.sub(r"\n\s*근거\s*:.*$", "", text, flags=re.S)
    text = re.sub(r"[\u4e00-\u9fff].*$", "", text, flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    evidence_line = build_evidence_line(context_chunks)
    if evidence_line:
        text = f"{text}\n\n{evidence_line}"

    return text


def score_chunk_for_profile(profile: QueryProfile, chunk: Dict) -> float:
    text = normalize_text(chunk.get("text", ""))
    source = chunk.get("source", "")
    entity_title = normalize_text(chunk.get("entity_title", ""))
    clause_title = normalize_text(chunk.get("clause_title", ""))
    coverage_terms = build_query_coverage_terms(profile)
    coverage_stats = chunk_coverage_stats(chunk, profile, coverage_terms)
    score = score_source_name(profile, source)

    for term in coverage_terms["quoted_terms"] + coverage_terms["document_hints"] + coverage_terms["entity_terms"]:
        if not term:
            continue
        compact_term = compact_text(term)
        if compact_term and compact_term in compact_text(entity_title):
            score += 120.0
        elif compact_term and compact_term in compact_text(clause_title):
            score += 95.0
        elif compact_term and compact_term in compact_text(text):
            score += 70.0

    score += 34.0 * int(coverage_stats["field_coverage"])
    if coverage_stats["combined_coverage"]:
        score += 55.0
    elif profile.procedure_requested and coverage_stats["field_coverage"] and not coverage_stats["entity_coverage"]:
        score -= 22.0

    block_type = chunk.get("block_type")
    if block_type == "table_row":
        score += 45.0
    elif block_type == "text_section":
        score += 30.0
    elif block_type == "clause_section":
        score += 34.0
    elif block_type == "paragraph":
        score += 18.0

    if clause_title and (
        profile.procedure_requested
        or profile.compare_requested
        or profile.multi_document_requested
        or profile.synthesis_requested
    ):
        if any(token in clause_title for token in ("제1조", "목적", "총칙", "정의", "적용범위")):
            score -= 85.0

    return score


def expand_results_for_top_sources(profile: QueryProfile, merged_results: List[Dict], all_index_chunks: List[Dict], requested_sources: List[str] | None = None) -> List[Dict]:
    source_rankings = rank_sources(profile, merged_results)
    requested_sources = requested_sources or []
    if not source_rankings and not requested_sources:
        return merged_results

    max_sources = 1 if profile.summary_requested and not profile.multi_document_requested else 2
    if profile.multi_document_requested or profile.compare_requested or profile.synthesis_requested:
        max_sources = max(max_sources, min(3, max(2, len(requested_sources))))

    selected_sources: List[str] = []
    for source in requested_sources:
        if source not in selected_sources:
            selected_sources.append(source)
    for ranking in source_rankings:
        if ranking["source"] not in selected_sources:
            selected_sources.append(ranking["source"])
        if len(selected_sources) >= max_sources:
            break

    source_rank_map = {ranking["source"]: ranking for ranking in source_rankings}
    expanded: List[Dict] = []
    seen = set()

    for source_rank, source in enumerate(selected_sources[:max_sources]):
        ranking = source_rank_map.get(source, {"source": source, "items": [], "score": score_source_name(profile, source)})
        source_chunks = [chunk for chunk in all_index_chunks if chunk.get("source") == source]
        source_chunks.sort(key=lambda chunk: score_chunk_for_profile(profile, chunk), reverse=True)

        for chunk_rank, chunk in enumerate(source_chunks[:12]):
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
        if item["chunk"].get("source") not in selected_sources[:max_sources]:
            continue
        expanded.append(item)
        seen.add(chunk_id)

    expanded.sort(key=lambda item: item["score"], reverse=True)
    deduped = dedupe_chunk_items(expanded, max_per_source=10)
    return deduped or merged_results


def chunk_matches_profile(chunk: Dict, profile: QueryProfile) -> bool:
    coverage = chunk_coverage_stats(chunk, profile)
    return bool(
        coverage["entity_coverage"]
        or coverage["field_coverage"]
        or coverage["title_coverage"]
    )


def filter_items_for_profile(items: List[Dict], profile: QueryProfile) -> List[Dict]:
    matched = [item for item in items if chunk_matches_profile(item["chunk"], profile)]
    return matched or items


def select_context_chunks(
    profile: QueryProfile,
    final_chunks: List[Dict],
    merged_results: List[Dict],
    requested_sources: List[str] | None = None,
) -> List[Dict]:
    if not final_chunks:
        return []

    source_rankings = rank_sources(profile, merged_results)
    requested_sources = requested_sources or []

    if profile.compare_requested and profile.compare_entities:
        chosen = []
        seen = set()
        for entity in profile.compare_entities:
            for item in merged_results:
                text = normalize_text(item["chunk"].get("text", ""))
                title = normalize_text(item["chunk"].get("entity_title", ""))
                clause_title = normalize_text(item["chunk"].get("clause_title", ""))
                if entity in text or entity in title or entity in clause_title:
                    chunk_id = item["chunk"]["chunk_id"]
                    if chunk_id not in seen:
                        chosen.append(item)
                        seen.add(chunk_id)
                    break
        if chosen:
            return dedupe_chunk_items(chosen, max_per_source=4)[:6]

    if (profile.compare_requested or profile.multi_document_requested or profile.synthesis_requested) and source_rankings:
        preferred_rankings = [ranking for ranking in source_rankings if ranking["source"] in requested_sources]
        other_rankings = [ranking for ranking in source_rankings if ranking["source"] not in requested_sources]
        ordered_rankings = []
        for ranking in preferred_rankings + other_rankings:
            ordered_rankings.append({**ranking, "items": filter_items_for_profile(ranking["items"], profile)})
        max_sources = 3 if profile.multi_document_requested else 2
        multisource = build_multisource_context(ordered_rankings, max_sources=max_sources, max_per_source=3)
        if multisource:
            return multisource

    if profile.procedure_requested and source_rankings:
        preferred_rankings = [ranking for ranking in source_rankings if ranking["source"] in requested_sources]
        primary_ranking = preferred_rankings[0] if preferred_rankings else source_rankings[0]
        primary_items = filter_items_for_profile(primary_ranking["items"], profile)
        primary = build_source_anchor_context(primary_items, max_anchors=3, max_total=8)
        if should_add_support_context(primary, profile):
            primary = supplement_source_context(profile, primary, primary_ranking["items"], max_total=8)
        support_rankings = [ranking for ranking in source_rankings if ranking["source"] != primary_ranking["source"]]
        if support_rankings and (
            profile.multi_document_requested
            or requested_sources
            or should_add_support_context(primary, profile)
        ):
            filtered_support_rankings = [
                {**ranking, "items": filter_items_for_profile(ranking["items"], profile)}
                for ranking in support_rankings
            ]
            support_max_sources = 2 if profile.multi_document_requested else 1
            support = build_multisource_context(filtered_support_rankings, max_sources=support_max_sources, max_per_source=2)
            if support:
                support = annotate_context_items(support, "support_source")
                return dedupe_chunk_items(primary + support, max_per_source=8)[:10]
        if primary:
            return primary

    if profile.summary_requested and source_rankings and not profile.multi_document_requested:
        best_source = source_rankings[0]
        max_total = 8 if source_is_notice_like(best_source["source"]) else 6
        return build_source_anchor_context(best_source["items"], max_anchors=2, max_total=max_total)

    if profile.exact_lookup and source_rankings:
        best_source_items = source_rankings[0]["items"]
        ranked_items = sorted(
            best_source_items,
            key=lambda item: (
                count_profile_field_hits(item["chunk"], profile),
                item.get("rerank_score", item.get("score", 0.0)),
            ),
            reverse=True,
        )
        entity_filtered = []
        for item in ranked_items:
            text = normalize_text(item["chunk"].get("text", ""))
            title = normalize_text(item["chunk"].get("entity_title", ""))
            clause_title = normalize_text(item["chunk"].get("clause_title", ""))
            if not profile.entity_terms or any(term in text or term in title or term in clause_title for term in profile.entity_terms):
                entity_filtered.append(item)
        selected = entity_filtered[:8] or ranked_items[:8]
        return dedupe_chunk_items(selected, max_per_source=8)[:8]

    top_source = final_chunks[0]["chunk"]["source"]
    top_ranking = next((ranking for ranking in source_rankings if ranking["source"] == top_source), None)
    source_pool = top_ranking["items"] if top_ranking else [item for item in final_chunks if item["chunk"]["source"] == top_source]
    source_pool = filter_items_for_profile(source_pool, profile)
    max_total = 8 if source_is_notice_like(top_source) else 6
    source_filtered = build_source_anchor_context(source_pool, max_anchors=3 if source_is_notice_like(top_source) else 2, max_total=max_total)
    if should_add_support_context(source_filtered, profile):
        source_filtered = supplement_source_context(
            profile,
            source_filtered,
            top_ranking["items"] if top_ranking else source_pool,
            max_total=max_total,
        )

    if len(source_rankings) > 1 and (
        source_rankings[1]["score"] >= source_rankings[0]["score"] * 0.92
        or should_add_support_context(source_filtered, profile)
    ):
        total_context_chars = sum(len(chunk_content_text(item["chunk"])) for item in source_filtered)
        if total_context_chars < 1200:
            support_pool = filter_items_for_profile(source_rankings[1]["items"], profile)
            support_items = build_source_anchor_context(support_pool, max_anchors=1, max_total=2)
            support_items = annotate_context_items(support_items, "support_source")
            source_filtered = dedupe_chunk_items(source_filtered + support_items, max_per_source=max_total)

    if source_filtered:
        return source_filtered[:max_total]

    fallback_total = 8 if source_is_notice_like(top_source) else 6
    return dedupe_chunk_items(final_chunks, max_per_source=fallback_total)[:fallback_total]


def collect_structured_candidates(profile: QueryProfile, final_chunks: List[Dict], merged_results: List[Dict]) -> List[Dict]:
    if not profile.exact_lookup:
        return []

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

    return dedupe_raw_chunks(candidates, max_per_source=10)


def collect_global_structured_candidates(profile: QueryProfile, all_index_chunks: List[Dict]) -> List[Dict]:
    if not profile.exact_lookup:
        return []

    filtered = []
    seen = set()

    preferred_block_types = {"table_row", "text_section", "table", "clause_section"}
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

    return dedupe_raw_chunks(filtered, max_per_source=12)


def run_query(query: str):
    validate_index_ready()
    started_at = time.perf_counter()

    requested_domain = get_requested_domain()
    profile = build_query_profile(query)
    security_mode = runtime_security_mode()
    runtime_detector_flag = runtime_detector_enabled()
    runtime_sanitizer_flag = runtime_sanitizer_enabled()
    include_flagged = security_mode == "baseline_rag"
    include_quarantined = security_mode == "baseline_rag"
    excluded_chunk_ids: set[str] = set()
    excluded_sources: set[str] = set()
    requery_attempt = 0

    while True:
        query_coverage_terms = build_query_coverage_terms(profile)
        search_output = hybrid_search(
            query,
            preferred_domain=requested_domain,
            include_flagged=include_flagged,
            include_quarantined=include_quarantined,
            exclude_chunk_ids=excluded_chunk_ids,
            exclude_sources=excluded_sources,
        )
        selected_domain = search_output.get("selected_domain") or ""
        merged_domains = search_output.get("merged_domains", [])
        retrieval_filter_summary = search_output.get("filter_summary", {})
        all_index_chunks = load_all_index_chunks(merged_domains or ([selected_domain] if selected_domain else []))
        all_index_chunks, expansion_filter_summary = filter_expansion_chunks(
            all_index_chunks,
            include_flagged=include_flagged,
            include_quarantined=include_quarantined,
            exclude_chunk_ids=excluded_chunk_ids,
            exclude_sources=excluded_sources,
        )
        dense_results = search_output["dense_results"]
        sparse_results = search_output["sparse_results"]
        merged_results = search_output["merged_results"]
        source_rankings = rank_sources(profile, merged_results)
        source_coverage_summary = build_source_debug_summaries(source_rankings, profile)
        requested_sources = resolve_requested_sources(profile, all_index_chunks)

        use_rag = should_use_rag(query, dense_results, sparse_results)
        if not use_rag:
            break

        attack_eval_mode = mutedrag_attack_eval_enabled()
        if attack_eval_mode:
            document_first_results = merged_results
        else:
            document_first_results = expand_results_for_top_sources(profile, merged_results, all_index_chunks, requested_sources)
        document_first_results = dedupe_chunk_items(document_first_results, max_per_source=10)
        expanded_source_rankings = rank_sources(profile, document_first_results)
        source_coverage_summary = build_source_debug_summaries(expanded_source_rankings, profile)
        final_chunks = rerank_results(query, document_first_results)
        final_chunks = dedupe_chunk_items(final_chunks, max_per_source=4)
        if profile.multi_document_requested or profile.compare_requested or profile.synthesis_requested:
            final_chunks = ensure_source_coverage(
                final_chunks,
                document_first_results,
                requested_sources,
                max_total=4,
            )

        if not final_chunks:
            break

        final_result_coverage_summary = build_item_coverage_summaries(final_chunks, profile)

        print_retrieval(dense_results, sparse_results, final_chunks)
        structured_candidates = collect_structured_candidates(profile, final_chunks, document_first_results)
        if (
            (profile.entity_terms or profile.document_hints or profile.requested_fields)
            and should_use_global_structured_candidates(profile, source_rankings)
        ):
            global_structured_candidates = collect_global_structured_candidates(profile, all_index_chunks)
            structured_candidates = dedupe_raw_chunks(global_structured_candidates + structured_candidates, max_per_source=12)

        structured_result = None
        if not attack_eval_mode and should_attempt_structured_route(profile, final_chunks, structured_candidates):
            structured_result = build_structured_answer(query, structured_candidates)
        if structured_result:
            print_structured_answer(structured_result)
            print_debug_info(
                route="structured",
                elapsed_seconds=time.perf_counter() - started_at,
                structured_candidates_count=len(structured_candidates),
                selected_domain=selected_domain,
                merged_domains=merged_domains,
                retrieval_filter_summary=retrieval_filter_summary,
                expansion_filter_summary=expansion_filter_summary,
                security_mode=security_mode,
                runtime_detector_flag=runtime_detector_flag,
                runtime_sanitizer_flag=runtime_sanitizer_flag,
                final_result_count=len(final_chunks),
                query_coverage_terms=query_coverage_terms,
                source_coverage_summary=source_coverage_summary,
                final_result_coverage_summary=final_result_coverage_summary,
            )
            return

        if attack_eval_mode:
            context_chunks = final_chunks[:5]
        else:
            context_chunks = select_context_chunks(profile, final_chunks, document_first_results, requested_sources)
        context_chunks = dedupe_chunk_items(context_chunks, max_per_source=8)[:5 if attack_eval_mode else 8]
        context_coverage_summary = build_item_coverage_summaries(context_chunks, profile)
        guard_result = apply_runtime_guard(query, context_chunks, prior_requery_attempts=requery_attempt)
        runtime_guard_summary = summarize_runtime_guard(guard_result)
        runtime_guard_summary["runtime_requery_attempt"] = requery_attempt
        runtime_result = guard_result["runtime_result"]
        sanitization = guard_result["sanitization"]

        if sanitization.get("action") in {"block", "fallback"}:
            print("\n=== RAG Response ===")
            print(build_runtime_fallback_message(runtime_result) or "?꾩옱 寃??臾몃㎘? ?덉쟾?섏? ?딆븘 ?듬???蹂대쪟?⑸땲??")
            print_debug_info(
                route="runtime_block" if sanitization.get("action") == "block" else "runtime_fallback",
                elapsed_seconds=time.perf_counter() - started_at,
                structured_candidates_count=len(structured_candidates),
                selected_domain=selected_domain,
                merged_domains=merged_domains,
                retrieval_filter_summary=retrieval_filter_summary,
                expansion_filter_summary=expansion_filter_summary,
                security_mode=security_mode,
                runtime_detector_flag=runtime_detector_flag,
                runtime_sanitizer_flag=runtime_sanitizer_flag,
                final_result_count=len(final_chunks),
                context_chunk_count=len(context_chunks),
                runtime_guard_summary=runtime_guard_summary,
                selected_context_items=context_chunks,
                query_coverage_terms=query_coverage_terms,
                source_coverage_summary=source_coverage_summary,
                final_result_coverage_summary=final_result_coverage_summary,
                context_coverage_summary=context_coverage_summary,
            )
            return

        if sanitization.get("action") == "requery":
            exclusion_filters = sanitization.get("exclusion_filters", {})
            next_chunk_ids = {chunk_id for chunk_id in exclusion_filters.get("chunk_ids", []) if chunk_id}
            next_sources = {source for source in exclusion_filters.get("sources", []) if source}
            next_chunk_ids -= excluded_chunk_ids
            next_sources -= excluded_sources

            if requery_attempt < RUNTIME_REQUERY_MAX_ATTEMPTS and (next_chunk_ids or next_sources):
                excluded_chunk_ids.update(next_chunk_ids)
                excluded_sources.update(next_sources)
                requery_attempt += 1
                runtime_guard_summary["runtime_requery_chunk_ids"] = sorted(excluded_chunk_ids)
                runtime_guard_summary["runtime_requery_sources"] = sorted(excluded_sources)
                continue

            runtime_guard_summary["runtime_requery_failure_reason"] = (
                "max_attempts_reached"
                if requery_attempt >= RUNTIME_REQUERY_MAX_ATTEMPTS
                else "no_new_exclusions"
            )

            print("\n=== RAG Response ===")
            print(build_runtime_fallback_message(runtime_result) or "현재 검색 문맥은 안전하지 않아 답변을 보류합니다.")
            print("\n=== Sources ===")
            print("(runtime guard blocked current context)")
            print_debug_info(
                route="runtime_requery_fallback",
                elapsed_seconds=time.perf_counter() - started_at,
                structured_candidates_count=len(structured_candidates),
                selected_domain=selected_domain,
                merged_domains=merged_domains,
                retrieval_filter_summary=retrieval_filter_summary,
                expansion_filter_summary=expansion_filter_summary,
                security_mode=security_mode,
                runtime_detector_flag=runtime_detector_flag,
                runtime_sanitizer_flag=runtime_sanitizer_flag,
                final_result_count=len(final_chunks),
                context_chunk_count=len(context_chunks),
                runtime_guard_summary=runtime_guard_summary,
                selected_context_items=context_chunks,
                query_coverage_terms=query_coverage_terms,
                source_coverage_summary=source_coverage_summary,
                final_result_coverage_summary=final_result_coverage_summary,
                context_coverage_summary=context_coverage_summary,
            )
            return

        if sanitization.get("action") == "remove":
            context_chunks = dedupe_chunk_items(list(sanitization.get("sanitized_context", [])), max_per_source=8)[:8]
            if not context_chunks:
                print("\n=== RAG Response ===")
                print(build_runtime_fallback_message(runtime_result) or "현재 검색 문맥은 안전하지 않아 답변을 보류합니다.")
                print_debug_info(
                    route="runtime_remove_fallback",
                    elapsed_seconds=time.perf_counter() - started_at,
                    structured_candidates_count=len(structured_candidates),
                    selected_domain=selected_domain,
                    merged_domains=merged_domains,
                    retrieval_filter_summary=retrieval_filter_summary,
                    expansion_filter_summary=expansion_filter_summary,
                    security_mode=security_mode,
                    runtime_detector_flag=runtime_detector_flag,
                    runtime_sanitizer_flag=runtime_sanitizer_flag,
                    final_result_count=len(final_chunks),
                    context_chunk_count=len(context_chunks),
                    runtime_guard_summary=runtime_guard_summary,
                    selected_context_items=context_chunks,
                    query_coverage_terms=query_coverage_terms,
                    source_coverage_summary=source_coverage_summary,
                    final_result_coverage_summary=final_result_coverage_summary,
                    context_coverage_summary=context_coverage_summary,
                )
                return

            post_remove_guard = apply_runtime_guard(query, context_chunks, prior_requery_attempts=requery_attempt)
            post_remove_summary = summarize_runtime_guard(post_remove_guard)
            post_remove_summary["runtime_requery_attempt"] = requery_attempt
            post_remove_summary["runtime_removed_chunk_ids"] = sanitization.get("removed_chunk_ids", [])
            post_remove_summary["runtime_sanitization_rules"] = (
                list(runtime_guard_summary.get("runtime_sanitization_rules", []))
                + [rule for rule in post_remove_summary.get("runtime_sanitization_rules", []) if rule not in runtime_guard_summary.get("runtime_sanitization_rules", [])]
            )
            post_remove_result = post_remove_guard["runtime_result"]
            post_remove_sanitization = post_remove_guard["sanitization"]

            if post_remove_sanitization.get("action") in {"block", "fallback", "remove"}:
                print("\n=== RAG Response ===")
                print(build_runtime_fallback_message(post_remove_result) or "?꾩옱 寃??臾몃㎘? ?덉쟾?섏? ?딆븘 ?듬???蹂대쪟?⑸땲??")
                print_debug_info(
                    route="runtime_remove_fallback",
                    elapsed_seconds=time.perf_counter() - started_at,
                    structured_candidates_count=len(structured_candidates),
                    selected_domain=selected_domain,
                    merged_domains=merged_domains,
                    retrieval_filter_summary=retrieval_filter_summary,
                    expansion_filter_summary=expansion_filter_summary,
                    security_mode=security_mode,
                    runtime_detector_flag=runtime_detector_flag,
                    runtime_sanitizer_flag=runtime_sanitizer_flag,
                    final_result_count=len(final_chunks),
                    context_chunk_count=len(context_chunks),
                    runtime_guard_summary=post_remove_summary,
                    selected_context_items=context_chunks,
                    query_coverage_terms=query_coverage_terms,
                    source_coverage_summary=source_coverage_summary,
                    final_result_coverage_summary=final_result_coverage_summary,
                    context_coverage_summary=context_coverage_summary,
                )
                return

            if post_remove_sanitization.get("action") == "requery":
                exclusion_filters = post_remove_sanitization.get("exclusion_filters", {})
                next_chunk_ids = {chunk_id for chunk_id in exclusion_filters.get("chunk_ids", []) if chunk_id}
                next_sources = {source for source in exclusion_filters.get("sources", []) if source}
                next_chunk_ids -= excluded_chunk_ids
                next_sources -= excluded_sources

                if requery_attempt < RUNTIME_REQUERY_MAX_ATTEMPTS and (next_chunk_ids or next_sources):
                    excluded_chunk_ids.update(next_chunk_ids)
                    excluded_sources.update(next_sources)
                    requery_attempt += 1
                    post_remove_summary["runtime_requery_chunk_ids"] = sorted(excluded_chunk_ids)
                    post_remove_summary["runtime_requery_sources"] = sorted(excluded_sources)
                    continue

                post_remove_summary["runtime_requery_failure_reason"] = (
                    "max_attempts_reached"
                    if requery_attempt >= RUNTIME_REQUERY_MAX_ATTEMPTS
                    else "no_new_exclusions"
                )

                print("\n=== RAG Response ===")
                print(build_runtime_fallback_message(post_remove_result) or "?꾩옱 寃??臾몃㎘? ?덉쟾?섏? ?딆븘 ?듬???蹂대쪟?⑸땲??")
                print("\n=== Sources ===")
                print("(runtime guard blocked current context)")
                print_debug_info(
                    route="runtime_requery_fallback",
                    elapsed_seconds=time.perf_counter() - started_at,
                    structured_candidates_count=len(structured_candidates),
                    selected_domain=selected_domain,
                    merged_domains=merged_domains,
                    retrieval_filter_summary=retrieval_filter_summary,
                    expansion_filter_summary=expansion_filter_summary,
                    security_mode=security_mode,
                    runtime_detector_flag=runtime_detector_flag,
                    runtime_sanitizer_flag=runtime_sanitizer_flag,
                    final_result_count=len(final_chunks),
                    context_chunk_count=len(context_chunks),
                    runtime_guard_summary=post_remove_summary,
                    selected_context_items=context_chunks,
                    query_coverage_terms=query_coverage_terms,
                    source_coverage_summary=source_coverage_summary,
                    final_result_coverage_summary=final_result_coverage_summary,
                    context_coverage_summary=context_coverage_summary,
                )
                return

            runtime_guard_summary = post_remove_summary
            runtime_result = post_remove_result

        if attack_eval_mode:
            prompt = build_mutedrag_attack_prompt(query, context_chunks)
        else:
            prompt = build_rag_prompt(query, context_chunks, profile)
        response = ask_ollama(prompt)
        response_text = response.get("response", "")
        if attack_eval_mode:
            normalized_response = response_text.strip()
        else:
            normalized_response = normalize_rag_response_text(response_text, context_chunks)

        print("\n=== RAG Response ===")
        print(normalized_response)
        print_sources(context_chunks)
        print_debug_info(
            route="llm",
            elapsed_seconds=time.perf_counter() - started_at,
            structured_candidates_count=len(structured_candidates),
            selected_domain=selected_domain,
            merged_domains=merged_domains,
            retrieval_filter_summary=retrieval_filter_summary,
            expansion_filter_summary=expansion_filter_summary,
            security_mode=security_mode,
            runtime_detector_flag=runtime_detector_flag,
            runtime_sanitizer_flag=runtime_sanitizer_flag,
            final_result_count=len(final_chunks),
            context_chunk_count=len(context_chunks),
            runtime_guard_summary=runtime_guard_summary,
            selected_context_items=context_chunks,
            query_coverage_terms=query_coverage_terms,
            source_coverage_summary=source_coverage_summary,
            final_result_coverage_summary=final_result_coverage_summary,
            context_coverage_summary=context_coverage_summary,
        )
        return

    prompt = build_general_prompt(query)
    response = ask_ollama(prompt)

    print("\n=== General LLM Response ===")
    print(response.get("response", ""))
    print_debug_info(
        route="general",
        elapsed_seconds=time.perf_counter() - started_at,
        selected_domain=selected_domain,
        merged_domains=merged_domains,
        retrieval_filter_summary=retrieval_filter_summary,
        security_mode=security_mode,
        runtime_detector_flag=runtime_detector_flag,
        runtime_sanitizer_flag=runtime_sanitizer_flag,
    )


def main():
    query = read_query()
    if not query:
        print("질문이 비어 있습니다.")
        return
    run_query(query)


def cli_main():
    main()


if __name__ == "__main__":
    cli_main()
