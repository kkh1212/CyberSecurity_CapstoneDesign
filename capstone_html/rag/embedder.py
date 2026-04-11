"""
임베딩 모델 싱글톤 래퍼
출처: 조원 코드 src/embedder.py 적용
"""
from sentence_transformers import SentenceTransformer

EMBED_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"

_embedder = None


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBED_MODEL_NAME)
    return _embedder


def embed_texts(texts):
    model = get_embedder()
    if isinstance(texts, str):
        texts = [texts]
    cleaned = [str(t) if t is not None else "" for t in texts]
    return model.encode(
        cleaned,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
