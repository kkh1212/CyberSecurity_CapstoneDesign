from sentence_transformers import CrossEncoder
from src.config import ENABLE_RERANK, FINAL_TOP_K, RERANK_MODEL_NAME, RERANK_TOP_K

_reranker = None

def get_reranker():
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(RERANK_MODEL_NAME)
    return _reranker

def rerank_results(query, merged_results, rerank_top_k=RERANK_TOP_K, final_top_k=FINAL_TOP_K):
    candidates = merged_results[:rerank_top_k]
    if not candidates:
        return []

    if not ENABLE_RERANK:
        trimmed = []
        for item in candidates[:final_top_k]:
            new_item = dict(item)
            new_item["rerank_score"] = float(item.get("score", 0.0))
            trimmed.append(new_item)
        return trimmed

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
