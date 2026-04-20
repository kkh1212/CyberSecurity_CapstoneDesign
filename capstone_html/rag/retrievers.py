"""
FAISS + BM25 하이브리드 검색 (업그레이드: 키워드 보너스 스코어링 + QueryProfile 통합)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import faiss
import numpy as np

from rag.embedder import embed_texts
from rag.query_analysis import build_query_profile, compact_text, normalize_text

DENSE_TOP_K = 10
SPARSE_TOP_K = 24
RRF_K = 60


# ─── 토크나이저 ──────────────────────────────────────────────────────
def tokenize_text(text: str) -> List[str]:
    """BM25용 토크나이저 (한국어 바이그램 + 영숫자)"""
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


def _count_matches(text: str, terms: List[str]) -> int:
    return sum(1 for term in terms if contains_term(text, term))


# ─── 키워드 보너스 스코어링 (업그레이드) ─────────────────────────────
def compute_keyword_bonus(query: str, chunk: Dict) -> float:
    """쿼리 프로파일 기반 키워드 보너스 점수 계산"""
    profile = build_query_profile(query)
    chunk_text = normalize_for_match(chunk.get("text", ""))
    source_text = normalize_for_match(chunk.get("source", ""))
    entity_title_text = normalize_for_match(chunk.get("entity_title", ""))
    source_title_text = normalize_for_match(source_title(chunk.get("source", "")))
    compact_query = compact_text(profile.query)

    entity_terms = [normalize_for_match(t) for t in profile.entity_terms]
    document_hints = [normalize_for_match(t) for t in profile.document_hints]
    field_terms = [normalize_for_match(f) for f in profile.requested_fields]
    quoted_terms = [normalize_for_match(t) for t in profile.quoted_terms]

    score = 0.0

    # 인용 검색어
    for term in quoted_terms:
        if contains_term(source_title_text, term):
            score += 190.0
        elif contains_term(entity_title_text, term):
            score += 170.0
        elif contains_term(chunk_text, term):
            score += 120.0

    # 엔티티 검색어
    entity_hits = 0
    for term in entity_terms:
        if contains_term(source_title_text, term):
            score += 125.0
            entity_hits += 1
        elif contains_term(entity_title_text, term):
            score += 95.0
            entity_hits += 1
        elif contains_term(chunk_text, term):
            score += 50.0
            entity_hits += 1
        elif contains_term(source_text, term):
            score += 28.0

    # 문서 힌트
    document_hits = 0
    for term in document_hints:
        if contains_term(source_title_text, term) or contains_term(source_text, term):
            score += 130.0
            document_hits += 1
        elif contains_term(chunk_text, term) or contains_term(entity_title_text, term):
            score += 90.0
            document_hits += 1

    # 소스 제목 완전 매칭
    if source_title_text and compact_text(source_title_text) in compact_query:
        score += 240.0

    # 필드 검색어
    field_hits = 0
    for term in field_terms:
        if contains_term(chunk_text, term):
            score += 14.0
            field_hits += 1

    # 복합 적중 보너스
    if entity_hits >= 2:
        score += 35.0
    if field_hits == len(field_terms) and field_hits > 0:
        score += 45.0
    if profile.compare_requested and entity_hits:
        score += 24.0

    # 블록 타입 보너스
    block_type = chunk.get("block_type")
    if block_type == "table_row":
        score += 32.0
    elif block_type == "clause_section":
        score += 28.0
    elif block_type == "text_section":
        score += 16.0
    elif block_type == "table":
        score += 8.0

    # 미매칭 패널티
    if (entity_terms or document_hints) and not (entity_hits or document_hits):
        score -= 25.0

    return score


# ─── FAISS 벡터 검색 ─────────────────────────────────────────────────
def dense_search(query: str, index: faiss.Index, chunks: List[Dict],
                 top_k: int = DENSE_TOP_K) -> List[Dict]:
    if not chunks:
        return []
    q_emb = np.asarray(embed_texts(query), dtype="float32")
    search_k = min(max(top_k * 5, top_k), len(chunks))
    scores, indices = index.search(q_emb, search_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        results.append({
            "retrieval_type": "dense",
            "score": float(score),
            "chunk": chunks[idx],
        })
    return results[:top_k]


# ─── BM25 키워드 검색 (키워드 보너스 포함) ───────────────────────────
def sparse_search(query: str, bm25, chunks: List[Dict],
                  top_k: int = SPARSE_TOP_K) -> List[Dict]:
    tokens = tokenize_text(query)
    scores = bm25.get_scores(tokens)

    rescored = []
    for idx, raw_score in enumerate(scores):
        chunk = chunks[idx]
        total_score = float(raw_score) + compute_keyword_bonus(query, chunk)
        rescored.append({
            "retrieval_type": "sparse",
            "score": total_score,
            "raw_score": float(raw_score),
            "chunk": chunk,
        })

    rescored.sort(key=lambda x: x["score"], reverse=True)
    return rescored[:top_k]


# ─── RRF 병합 ────────────────────────────────────────────────────────
def _rrf_score(rank: int, k: int = RRF_K) -> float:
    return 1.0 / (k + rank + 1)


def hybrid_search(query: str, index: faiss.Index, chunks: List[Dict],
                  bm25) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    FAISS + BM25 하이브리드 검색 → RRF 병합
    반환: (dense_results, sparse_results, merged_results)
    """
    dense_results  = dense_search(query, index, chunks)
    sparse_results = sparse_search(query, bm25, chunks)

    rrf_scores: Dict[str, float] = {}
    chunk_map: Dict[str, Dict] = {}

    for rank, item in enumerate(dense_results):
        cid = item["chunk"]["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + _rrf_score(rank)
        chunk_map[cid] = item["chunk"]

    for rank, item in enumerate(sparse_results):
        cid = item["chunk"]["chunk_id"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + _rrf_score(rank)
        chunk_map[cid] = item["chunk"]

    merged = [
        {
            "retrieval_type": "hybrid",
            "score": score,
            "chunk": chunk_map[cid],
        }
        for cid, score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    ]

    return dense_results, sparse_results, merged
