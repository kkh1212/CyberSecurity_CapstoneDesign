from __future__ import annotations

import numpy as np

from .config import DetectorConfig
from .embeddings import EmbeddingModel, centroid, cosine
from .prototypes import ATTACK_INTENT_PROTOTYPES, REDIRECTION_PROTOTYPES, REFUSAL_PROTOTYPES
from .schemas import ChunkFeatures
from .sentence_splitter import split_english_sentences


def _percentile(values: np.ndarray, percentile: float) -> float:
    if values.size == 0:
        return 0.0
    return float(np.percentile(values, percentile))


def _top_bottom_count(sentence_count: int, fraction: float) -> int:
    if sentence_count <= 1:
        return 1
    return max(1, min(sentence_count // 2, int(round(sentence_count * fraction))))


def score_chunk(
    *,
    query: str,
    chunk_text: str,
    question_id: str = "",
    doc_id: str = "",
    source_doc: str = "",
    embedder: EmbeddingModel | None = None,
    config: DetectorConfig | None = None,
) -> ChunkFeatures:
    """Compute query-conditioned semantic split features for one chunk/document."""

    cfg = config or DetectorConfig()
    model = embedder or EmbeddingModel(cfg.embedding_backend)
    sentences = split_english_sentences(
        chunk_text,
        min_chars=cfg.min_sentence_chars,
        max_sentences=cfg.max_sentences,
    )

    if not sentences:
        return ChunkFeatures(
            question_id=question_id,
            doc_id=doc_id,
            source_doc=source_doc,
            sentence_count=0,
            anchor_score=0.0,
            alignment_gap=0.0,
            alignment_spread=0.0,
            semantic_break_score=0.0,
            directional_drop_score=0.0,
            payload_isolation=0.0,
            prototype_similarity=0.0,
            redirect_similarity=0.0,
            refusal_similarity=0.0,
            off_query_prototype_score=0.0,
            mean_similarity=0.0,
            min_similarity=0.0,
            max_similarity=0.0,
            p20_similarity=0.0,
            p80_similarity=0.0,
            max_break_index=-1,
            similarities=[],
            sentences=[],
        )

    vectors = model.embed([query, *sentences, *ATTACK_INTENT_PROTOTYPES])
    query_vector = vectors[0]
    sentence_vectors = vectors[1 : 1 + len(sentences)]
    prototype_vectors = vectors[1 + len(sentences) :]

    similarities = np.asarray(
        [cosine(vector, query_vector) for vector in sentence_vectors],
        dtype="float32",
    )

    p20 = _percentile(similarities, 20)
    p80 = _percentile(similarities, 80)
    anchor_score = float(np.max(similarities)) if similarities.size else 0.0
    min_similarity = float(np.min(similarities)) if similarities.size else 0.0
    max_similarity = float(np.max(similarities)) if similarities.size else 0.0
    mean_similarity = float(np.mean(similarities)) if similarities.size else 0.0
    alignment_gap = max(0.0, p80 - p20)
    alignment_spread = max(0.0, max_similarity - min_similarity)

    semantic_break_score = 0.0
    directional_drop_score = 0.0
    max_break_index = -1
    if len(sentences) >= 2:
        for idx in range(len(sentences) - 1):
            adjacent_distance = 1.0 - cosine(sentence_vectors[idx], sentence_vectors[idx + 1])
            similarity_delta = abs(float(similarities[idx]) - float(similarities[idx + 1]))
            transition_score = max(0.0, adjacent_distance) * similarity_delta
            directional_score = max(0.0, adjacent_distance) * max(
                0.0,
                float(similarities[idx]) - float(similarities[idx + 1]),
            )
            if transition_score > semantic_break_score:
                semantic_break_score = transition_score
                max_break_index = idx
            directional_drop_score = max(directional_drop_score, directional_score)

    payload_isolation = 0.0
    if len(sentences) >= 2:
        count = _top_bottom_count(len(sentences), cfg.top_fraction)
        order = np.argsort(similarities)
        payload_indexes = order[:count]
        anchor_indexes = order[-count:]
        anchor_center = centroid(sentence_vectors[anchor_indexes])
        payload_center = centroid(sentence_vectors[payload_indexes])
        payload_isolation = max(0.0, 1.0 - cosine(anchor_center, payload_center))

    prototype_similarity = 0.0
    redirect_similarity = 0.0
    refusal_similarity = 0.0
    off_query_prototype_score = 0.0
    if prototype_vectors.size and sentence_vectors.size:
        redirect_count = len(REDIRECTION_PROTOTYPES)
        redirect_vectors = prototype_vectors[:redirect_count]
        refusal_vectors = prototype_vectors[redirect_count : redirect_count + len(REFUSAL_PROTOTYPES)]
        max_proto_by_sentence: list[float] = []
        for idx, sentence_vector in enumerate(sentence_vectors):
            proto_sim = max(cosine(sentence_vector, proto_vector) for proto_vector in prototype_vectors)
            redir_sim = max(cosine(sentence_vector, proto_vector) for proto_vector in redirect_vectors)
            refuse_sim = max(cosine(sentence_vector, proto_vector) for proto_vector in refusal_vectors)
            max_proto_by_sentence.append(proto_sim)
            redirect_similarity = max(redirect_similarity, redir_sim)
            refusal_similarity = max(refusal_similarity, refuse_sim)
            query_gap = max(0.0, anchor_score - float(similarities[idx]))
            off_query_prototype_score = max(off_query_prototype_score, proto_sim * query_gap)
        prototype_similarity = max(max_proto_by_sentence) if max_proto_by_sentence else 0.0

    return ChunkFeatures(
        question_id=question_id,
        doc_id=doc_id,
        source_doc=source_doc,
        sentence_count=len(sentences),
        anchor_score=anchor_score,
        alignment_gap=alignment_gap,
        alignment_spread=alignment_spread,
        semantic_break_score=float(semantic_break_score),
        directional_drop_score=float(directional_drop_score),
        payload_isolation=float(payload_isolation),
        prototype_similarity=float(prototype_similarity),
        redirect_similarity=float(redirect_similarity),
        refusal_similarity=float(refusal_similarity),
        off_query_prototype_score=float(off_query_prototype_score),
        mean_similarity=mean_similarity,
        min_similarity=min_similarity,
        max_similarity=max_similarity,
        p20_similarity=p20,
        p80_similarity=p80,
        max_break_index=max_break_index,
        similarities=[float(value) for value in similarities],
        sentences=sentences,
    )
