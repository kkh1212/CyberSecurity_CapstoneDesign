from sentence_transformers import SentenceTransformer

from src.config import EMBED_MODEL_NAME


_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBED_MODEL_NAME)
    return _embedder


def embed_texts(texts):
    model = get_embedder()

    if isinstance(texts, str):
        texts = [texts]

    if not isinstance(texts, list):
        raise TypeError(f"embed_texts expects str or list[str], got {type(texts)}")

    cleaned = []
    for text in texts:
        if text is None:
            text = ""
        if not isinstance(text, str):
            text = str(text)
        cleaned.append(text)

    embeddings = model.encode(
        cleaned,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return embeddings
