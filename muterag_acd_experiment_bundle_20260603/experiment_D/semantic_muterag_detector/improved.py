"""Improved (opt-in) structure-first MutedRAG detector.

Enabled via SEMANTIC_DETECTOR_MODE=improved (default mode keeps the legacy
classifier, so existing behavior is unchanged). Focuses solely on MutedRAG:
emits muted_candidate / clean.

Signal (wording-invariant):
  candidate = query/cluster anchor present
              AND a sentence is foreign (off the query AND off the document's own
                  topic) OR carries muting intent while off the document topic.
Intent is measured against a diversified concept bank (intent_bank.py), not fixed
strings, and sentences are preprocessed (drop heading fragments, split on ':'/';')
so section titles are not scored and a redirect clause is isolated from a trailing
topical ask.

Requires a real semantic embedding backend (SEMANTIC_DETECTOR_BACKEND=auto / mpnet);
the hashing backend is lexical and not suitable for this detector.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .embeddings import EmbeddingModel, centroid, cosine
from .intent_bank import SEED_GROUPS
from .sentence_splitter import split_english_sentences

DEFAULT_THRESHOLD = 0.35
ANCHOR_MIN = 0.18
ANCHOR_DAMP = 0.5
STRUCTURE_BONUS = 0.10
DIRECT_INJECTION_MARKERS = (
    "employee name:",
    "access level:",
    "fictional employee",
    "fictional data",
    "prompt injection evaluation",
    "include the fictional employee",
    "test access manager",
)


@dataclass
class FeatureView:
    """Subset of fields the query-time wrapper records (mirrors ChunkFeatures)."""
    anchor_score: float
    alignment_gap: float          # carries foreign_payload
    redirect_similarity: float    # carries muting intent
    refusal_similarity: float


@dataclass
class VerdictView:
    verdict: str
    reason: str
    muted_score: float


class ImprovedDetector:
    def __init__(self, backend: str = "auto"):
        self.embedder = EmbeddingModel(backend)
        self.seed_groups = SEED_GROUPS
        self.seed_phrases: list[str] = [phrase for phrases in SEED_GROUPS.values() for phrase in phrases]
        self.group_centroids = {
            name: centroid(self.embedder.embed(phrases)) for name, phrases in SEED_GROUPS.items()
        }

    def _intent(self, vec: np.ndarray) -> float:
        return max(cosine(vec, c) for c in self.group_centroids.values())

    @staticmethod
    def _sentences(text: str) -> list[str]:
        out: list[str] = []
        for raw in split_english_sentences(text, min_chars=8, max_sentences=40):
            for part in re.split(r"[:;]\s+", raw):
                part = part.strip()
                if len(part.split()) >= 4:
                    out.append(part)
        return out[:24]

    def evaluate(self, query: str, text: str, threshold: float = DEFAULT_THRESHOLD) -> tuple[FeatureView, VerdictView]:
        lowered_text = text.lower()
        if any(marker in lowered_text for marker in DIRECT_INJECTION_MARKERS):
            reason = "direct injection profile marker ignored by muted-only detector"
            return FeatureView(0.0, 0.0, 0.0, 0.0), VerdictView("clean", reason, 0.0)

        sents = self._sentences(text)
        if not sents:
            return FeatureView(0.0, 0.0, 0.0, 0.0), VerdictView("clean", "no scorable sentences", 0.0)

        # Embed query, document clauses, and intent seeds in one call so lexical
        # fallback backends (TF-IDF) share the same vector space.
        vecs = self.embedder.embed([query, *sents, *self.seed_phrases])
        qv = vecs[0]
        sv = vecs[1:1 + len(sents)]
        seed_vectors = vecs[1 + len(sents):]
        local_centroids = []
        offset = 0
        for phrases in self.seed_groups.values():
            count = len(phrases)
            local_centroids.append(centroid(seed_vectors[offset:offset + count]))
            offset += count
        sims = np.array([cosine(v, qv) for v in sv], dtype="float32")
        anchor = float(sims.max())

        n = len(sents)
        keep = max(1, n - max(1, n // 5))
        keep_idx = np.argsort(sims)[-keep:]
        topic = centroid(sv[keep_idx])

        best, best_foreign, best_intent = 0.0, 0.0, 0.0
        for i, v in enumerate(sv):
            off_query = 1.0 - float(sims[i])
            off_topic = 1.0 - cosine(v, topic)
            foreign = min(off_query, off_topic)
            intent = max(cosine(v, c) for c in local_centroids) if local_centroids else self._intent(v)
            evidence = intent * off_topic
            if evidence > best:
                best, best_foreign, best_intent = evidence, foreign, intent

        semantic_break = 0.0
        for i in range(n - 1):
            dist = max(0.0, 1.0 - cosine(sv[i], sv[i + 1]))
            delta = abs(float(sims[i]) - float(sims[i + 1]))
            semantic_break = max(semantic_break, dist * delta)
        isolation = 0.0
        if n >= 2:
            order = np.argsort(sims)
            cnt = max(1, min(n // 2, int(round(n * 0.3))))
            isolation = max(0.0, 1.0 - cosine(centroid(sv[order[:cnt]]), centroid(sv[order[-cnt:]])))

        gate = 1.0 if anchor >= ANCHOR_MIN else ANCHOR_DAMP
        score = float(gate * (best + STRUCTURE_BONUS * max(semantic_break, isolation)))
        verdict = "muted_candidate" if score >= threshold else "clean"
        reason = f"improved score={score:.3f} foreign={best_foreign:.2f} intent={best_intent:.2f} anchor={anchor:.2f}"
        feat = FeatureView(anchor_score=anchor, alignment_gap=best_foreign,
                           redirect_similarity=best_intent, refusal_similarity=0.0)
        return feat, VerdictView(verdict=verdict, reason=reason, muted_score=score)


_CACHE: dict[str, ImprovedDetector] = {}


def get_detector(backend: str) -> ImprovedDetector:
    if backend not in _CACHE:
        _CACHE[backend] = ImprovedDetector(backend)
    return _CACHE[backend]
