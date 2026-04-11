"""
CrossEncoder 리랭킹
출처: 조원 코드 src/reranker.py 적용
"""
from sentence_transformers import CrossEncoder

RERANK_MODEL_NAME = "BAAI/bge-reranker-base"

_reranker = None


def get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(RERANK_MODEL_NAME)
    return _reranker


def rerank_results(query: str, merged_results: list, rerank_top_k: int = 12, final_top_k: int = 5) -> list:
    candidates = merged_results[:rerank_top_k]
    if not candidates:
        return []

    model = get_reranker()
    pairs = [(query, item["chunk"]["text"]) for item in candidates]
    scores = model.predict(pairs)

    reranked = []
    for item, score in zip(candidates, scores):
        new_item = dict(item)
        new_item["rerank_score"] = float(score)
        reranked.append(new_item)

    reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
    return reranked[:final_top_k]
