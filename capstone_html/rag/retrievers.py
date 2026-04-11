"""
FAISS + BM25 하이브리드 검색
출처: 조원 코드 src/retrievers.py 기반
변경: 단일 도메인 구조로 단순화, query_analysis 의존성 제거, RRF 병합 방식 적용
"""
import re
from typing import List, Dict, Optional, Tuple

import faiss
import numpy as np

from rag.embedder import embed_texts

DENSE_TOP_K = 10
SPARSE_TOP_K = 24
RRF_K = 60  # Reciprocal Rank Fusion 상수


def tokenize_text(text: str) -> List[str]:
    """BM25용 토크나이저 (한국어 바이그램 + 영숫자)"""
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    base_tokens = re.findall(r"[가-힣]+|[a-zA-Z]+|\d+", normalized)
    tokens = list(base_tokens)
    for token in base_tokens:
        if re.fullmatch(r"[가-힣]+", token) and len(token) >= 2:
            tokens.extend(token[i:i + 2] for i in range(len(token) - 1))
    return tokens


def dense_search(query: str, index: faiss.Index, chunks: List[Dict],
                 top_k: int = DENSE_TOP_K) -> List[Dict]:
    """FAISS 벡터 검색"""
    q_emb = np.asarray(embed_texts(query), dtype="float32")
    scores, indices = index.search(q_emb, top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        results.append({
            "retrieval_type": "dense",
            "score": float(score),
            "chunk": chunks[idx],
        })
    return results


def sparse_search(query: str, bm25, chunks: List[Dict],
                  top_k: int = SPARSE_TOP_K) -> List[Dict]:
    """BM25 키워드 검색"""
    tokens = tokenize_text(query)
    scores = bm25.get_scores(tokens)

    indexed = [(float(score), i) for i, score in enumerate(scores) if score > 0]
    indexed.sort(key=lambda x: x[0], reverse=True)

    results = []
    for score, idx in indexed[:top_k]:
        results.append({
            "retrieval_type": "sparse",
            "score": score,
            "chunk": chunks[idx],
        })
    return results


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

    # RRF 점수 계산
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

    # 정렬
    merged = [
        {
            "retrieval_type": "hybrid",
            "score": score,
            "chunk": chunk_map[cid],
        }
        for cid, score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    ]

    return dense_results, sparse_results, merged
