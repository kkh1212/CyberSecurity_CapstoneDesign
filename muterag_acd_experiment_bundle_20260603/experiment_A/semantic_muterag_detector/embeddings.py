from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from collections import Counter
from typing import Iterable

import numpy as np


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


@dataclass
class EmbeddingModel:
    """Small adapter around the repo embedder with a local TF-IDF fallback."""

    backend: str = "auto"
    resolved_backend: str | None = None
    hashing_dim: int = 4096

    def embed(self, texts: Iterable[str]) -> np.ndarray:
        cleaned = ["" if text is None else str(text) for text in texts]
        if not cleaned:
            return np.zeros((0, 0), dtype="float32")

        backend = self.backend.strip().lower()
        if backend in {"hash", "hashing", "hashed"}:
            self.resolved_backend = "hashing"
            return self._embed_hashing(cleaned, self.hashing_dim)

        if backend in {"auto", "st", "sentence-transformers", "sentence_transformers"}:
            try:
                from src.embedder import embed_texts

                vectors = np.asarray(embed_texts(cleaned), dtype="float32")
                self.resolved_backend = "sentence-transformers"
                return _l2_normalize(vectors)
            except Exception:
                if backend != "auto":
                    raise

        self.resolved_backend = "tfidf"
        return self._embed_tfidf(cleaned)

    @staticmethod
    def _embed_hashing(texts: list[str], dim: int) -> np.ndarray:
        dim = max(128, int(dim or 4096))
        matrix = np.zeros((len(texts), dim), dtype="float32")
        for row_idx, text in enumerate(texts):
            tokens = _tokenize_with_bigrams(text)
            counts = Counter(tokens)
            for token, count in counts.items():
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                bucket = int.from_bytes(digest, "big") % dim
                sign = 1.0 if digest[0] & 1 else -1.0
                matrix[row_idx, bucket] += sign * (1.0 + math.log(float(count)))
        return _l2_normalize(matrix)

    @staticmethod
    def _embed_tfidf(texts: list[str]) -> np.ndarray:
        if all(not text.strip() for text in texts):
            return np.zeros((len(texts), 1), dtype="float32")

        tokenized = [_tokenize_with_bigrams(text) for text in texts]
        vocabulary = sorted({token for tokens in tokenized for token in tokens}) or ["__empty__"]
        index = {token: idx for idx, token in enumerate(vocabulary)}
        document_frequency = Counter()
        for tokens in tokenized:
            document_frequency.update(set(tokens))

        matrix = np.zeros((len(texts), len(vocabulary)), dtype="float32")
        total_docs = max(1, len(texts))
        for row_idx, tokens in enumerate(tokenized):
            counts = Counter(tokens)
            if not counts:
                continue
            total_terms = sum(counts.values())
            for token, count in counts.items():
                col_idx = index[token]
                tf = count / total_terms
                idf = math.log((1 + total_docs) / (1 + document_frequency[token])) + 1.0
                matrix[row_idx, col_idx] = float(tf * idf)
        return _l2_normalize(matrix)


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    if left.size == 0 or right.size == 0:
        return 0.0
    value = float(np.dot(left, right))
    if math.isnan(value):
        return 0.0
    return max(-1.0, min(1.0, value))


def _tokenize_with_bigrams(text: str) -> list[str]:
    tokens = re.findall(r"\b[a-z][a-z0-9_-]+\b", (text or "").lower())
    bigrams = [f"{left}__{right}" for left, right in zip(tokens, tokens[1:])]
    return tokens + bigrams


def centroid(vectors: np.ndarray) -> np.ndarray:
    if vectors.size == 0:
        return np.zeros((0,), dtype="float32")
    center = np.mean(vectors, axis=0)
    norm = float(np.linalg.norm(center))
    if norm == 0:
        return center
    return center / norm
