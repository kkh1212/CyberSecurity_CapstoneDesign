"""Improved, structure-first MutedRAG detector (research prototype).

Design goals (broad coverage of the MutedRAG *type*, not our corpus phrasing):

  candidate = (query/cluster anchor present)
              AND (a segment that is foreign: far from BOTH the query and the
                   document's own benign topic)
              AND (that region carries muting intent: redirect / refusal-induction
                   / priority-override), measured against a DIVERSIFIED concept
                   bank rather than fixed strings.

Key wording-invariant signal: `foreign_payload` = max over sentences of
min(off_query, off_topic), where the topic centroid is trimmed so the payload
does not pollute it. This catches paraphrased / unseen muting triggers and the
harmful ask, while ignoring benign low-query sections (e.g. a Contact section)
that are still on the document's topic.

Embeddings use the project's real semantic model (mpnet) via EmbeddingModel("auto").
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from semantic_muterag_detector.embeddings import EmbeddingModel, cosine, centroid  # noqa: E402
from semantic_muterag_detector.sentence_splitter import split_english_sentences  # noqa: E402
from intent_seeds import SEED_GROUPS  # noqa: E402


@dataclass
class Weights:
    structure_bonus: float = 0.10     # small bonus from break/isolation
    anchor_min: float = 0.18          # gate; below this, score is damped
    anchor_damp: float = 0.5


@dataclass
class DocFeatures:
    n_sentences: int
    anchor: float
    foreign_payload: float
    intent: float
    semantic_break: float
    payload_isolation: float
    # per-sentence arrays (parallel to sentences)
    off_query: list = None
    off_topic: list = None
    foreign: list = None
    intent_arr: list = None
    sentences: list = None
    foreign_sentence: str = ""
    intent_sentence: str = ""


class ImprovedDetector:
    def __init__(self, backend: str = "auto"):
        self.embedder = EmbeddingModel(backend)
        self.seed_groups = SEED_GROUPS
        self.seed_phrases: list[str] = [phrase for phrases in SEED_GROUPS.values() for phrase in phrases]
        # one concept centroid per muting-trigger group
        self.group_centroids: dict[str, np.ndarray] = {}
        for name, phrases in SEED_GROUPS.items():
            vecs = self.embedder.embed(phrases)
            self.group_centroids[name] = centroid(vecs)

    def _intent_of(self, sentence_vec: np.ndarray) -> float:
        return max(cosine(sentence_vec, c) for c in self.group_centroids.values())

    @staticmethod
    def _sentences(text: str) -> list[str]:
        # Split into clauses and drop heading/short fragments so section titles
        # ("Scope", "Endpoint Operations") are not scored as payload. Splitting
        # on ':' / ';' isolates a redirect clause from a trailing topical ask.
        out: list[str] = []
        for raw in split_english_sentences(text, min_chars=8, max_sentences=40):
            for part in re.split(r"[:;]\s+", raw):
                part = part.strip()
                if len(part.split()) >= 4:
                    out.append(part)
        return out[:24]

    def features(self, query: str, text: str) -> DocFeatures:
        sentences = self._sentences(text)
        if not sentences:
            return DocFeatures(0, 0.0, 0.0, 0.0, 0.0, 0.0)

        # Embed query, clauses, and intent seeds together so TF-IDF fallback
        # creates one shared vector space. Sentence-transformer/hash backends
        # remain stable as before.
        vecs = self.embedder.embed([query, *sentences, *self.seed_phrases])
        qv = vecs[0]
        sv = vecs[1:1 + len(sentences)]
        seed_vectors = vecs[1 + len(sentences):]
        local_centroids = []
        offset = 0
        for phrases in self.seed_groups.values():
            count = len(phrases)
            local_centroids.append(centroid(seed_vectors[offset:offset + count]))
            offset += count
        sims = np.array([cosine(v, qv) for v in sv], dtype="float32")
        anchor = float(sims.max())

        # trimmed topic centroid: drop the lowest-query-sim fraction so the
        # (off-query) payload does not define the "topic".
        n = len(sentences)
        keep = max(1, n - max(1, n // 5))
        keep_idx = np.argsort(sims)[-keep:]
        topic_centroid = centroid(sv[keep_idx])

        foreign_vals, intent_vals, offtopic_vals = [], [], []
        for i, v in enumerate(sv):
            off_query = 1.0 - float(sims[i])
            off_topic = 1.0 - cosine(v, topic_centroid)
            foreign_vals.append(min(off_query, off_topic))
            intent_vals.append(max(cosine(v, c) for c in local_centroids) if local_centroids else self._intent_of(v))
            offtopic_vals.append(off_topic)
        foreign_vals = np.array(foreign_vals, dtype="float32")
        intent_vals = np.array(intent_vals, dtype="float32")
        offtopic_vals = np.array(offtopic_vals, dtype="float32")
        foreign_payload = float(foreign_vals.max())
        intent = float(intent_vals.max())

        # adjacent semantic break weighted by query-alignment drop
        semantic_break = 0.0
        for i in range(n - 1):
            dist = max(0.0, 1.0 - cosine(sv[i], sv[i + 1]))
            delta = abs(float(sims[i]) - float(sims[i + 1]))
            semantic_break = max(semantic_break, dist * delta)

        # payload vs anchor cluster isolation
        payload_isolation = 0.0
        if n >= 2:
            order = np.argsort(sims)
            cnt = max(1, min(n // 2, int(round(n * 0.3))))
            low_c = centroid(sv[order[:cnt]])
            high_c = centroid(sv[order[-cnt:]])
            payload_isolation = max(0.0, 1.0 - cosine(low_c, high_c))

        off_query = [1.0 - float(s) for s in sims]
        return DocFeatures(
            n_sentences=n,
            anchor=anchor,
            foreign_payload=foreign_payload,
            intent=intent,
            semantic_break=float(semantic_break),
            payload_isolation=float(payload_isolation),
            off_query=off_query,
            off_topic=[float(v) for v in offtopic_vals],
            foreign=[float(v) for v in foreign_vals],
            intent_arr=[float(v) for v in intent_vals],
            sentences=sentences,
            foreign_sentence=sentences[int(foreign_vals.argmax())],
            intent_sentence=sentences[int(intent_vals.argmax())],
        )

    def score(self, feat: DocFeatures, w: Weights | None = None) -> float:
        """Per-sentence, off-query-gated evidence.

        A sentence contributes if it is foreign (off query AND off topic) or if
        it carries muting intent AND is off-query. The off-query gating on intent
        suppresses benign policy prose ("employees must report ...") which is
        instruction-like but on-topic / on-query.
        """
        w = w or Weights()
        if not feat.sentences:
            return 0.0
        best = 0.0
        for i in range(feat.n_sentences):
            # intent counts only when the sentence is off the document's topic
            # (a real muting trigger is a meta-instruction, not on-subject prose).
            intent_eff = feat.intent_arr[i] * feat.off_topic[i]
            evidence = intent_eff
            best = max(best, evidence)
        structure = w.structure_bonus * max(feat.semantic_break, feat.payload_isolation)
        gate = 1.0 if feat.anchor >= w.anchor_min else w.anchor_damp
        return float(gate * (best + structure))


def calibrate_threshold(benign_scores, percentile: float = 99.0) -> float:
    arr = np.sort(np.asarray(benign_scores, dtype="float32"))
    if arr.size == 0:
        return 0.0
    idx = (arr.size - 1) * (percentile / 100.0)
    lo = int(idx)
    hi = min(lo + 1, arr.size - 1)
    frac = idx - lo
    return float(arr[lo] * (1 - frac) + arr[hi] * frac)
