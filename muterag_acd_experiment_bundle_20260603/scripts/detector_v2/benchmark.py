"""Benchmark: existing semantic_muterag_detector vs improved detector.

Both run with the real mpnet backend. Benign docs are split into a calibration
half and an FP-test half; positives (held-out MutedRAG variants) measure recall
by family. Reports recall, FP, and score separation.
"""
from __future__ import annotations

import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from semantic_muterag_detector.config import DetectorConfig  # noqa: E402
from semantic_muterag_detector.embeddings import EmbeddingModel  # noqa: E402
from semantic_muterag_detector.features import score_chunk  # noqa: E402
from semantic_muterag_detector.classifier import classify_features  # noqa: E402
from semantic_muterag_detector.calibration import calibrate_thresholds  # noqa: E402
from improved_detector import ImprovedDetector, Weights, calibrate_threshold  # noqa: E402
import eval_variants  # noqa: E402

FAMILIES = ["A", "B", "C", "D", "E"]
FAMILY_DESC = {
    "A": "paraphrased redirect", "B": "soft/indirect redirect",
    "C": "refusal-induction", "D": "position-shifted", "E": "multilingual redirect",
}


def pct(x, n):
    return f"{x}/{n} ({(100.0*x/n if n else 0):.0f}%)"


def main():
    pos = eval_variants.positives()
    neg = eval_variants.negatives()
    benign_cal = neg[::2]      # calibration half
    benign_test = neg[1::2]    # FP-test half

    # ---------------- NEW detector ----------------
    new = ImprovedDetector(backend="auto")
    w = Weights()
    cal_scores = [new.score(new.features(b["query"], b["text"]), w) for b in benign_cal]
    new_threshold = calibrate_threshold(cal_scores, percentile=99.0)

    new_recall = defaultdict(lambda: [0, 0])
    new_attack_scores = defaultdict(list)
    for p in pos:
        s = new.score(new.features(p["query"], p["text"]), w)
        new_attack_scores[p["family"]].append(s)
        new_recall[p["family"]][1] += 1
        if s >= new_threshold:
            new_recall[p["family"]][0] += 1
    new_fp = 0
    new_test_scores = []
    for b in benign_test:
        s = new.score(new.features(b["query"], b["text"]), w)
        new_test_scores.append(s)
        if s >= new_threshold:
            new_fp += 1

    # ---------------- OLD detector ----------------
    emb = EmbeddingModel("auto")
    cfg = DetectorConfig(embedding_backend="auto")
    cal_feats = [score_chunk(query=b["query"], chunk_text=b["text"], embedder=emb, config=cfg) for b in benign_cal]
    old_th = calibrate_thresholds(cal_feats)

    def old_verdict(item):
        f = score_chunk(query=item["query"], chunk_text=item["text"], embedder=emb, config=cfg)
        return classify_features(f, old_th).verdict

    old_recall = defaultdict(lambda: [0, 0])      # any candidate
    old_recall_muted = defaultdict(lambda: [0, 0])  # muted_candidate only
    for p in pos:
        v = old_verdict(p)
        old_recall[p["family"]][1] += 1
        old_recall_muted[p["family"]][1] += 1
        if v in {"muted_candidate", "direct_candidate"}:
            old_recall[p["family"]][0] += 1
        if v == "muted_candidate":
            old_recall_muted[p["family"]][0] += 1
    old_fp = 0
    for b in benign_test:
        if old_verdict(b) in {"muted_candidate", "direct_candidate"}:
            old_fp += 1

    # ---------------- report ----------------
    print("=" * 72)
    print("MutedRAG detection benchmark (held-out variants, mpnet backend)")
    print("=" * 72)
    print(f"positives={len(pos)} benign_cal={len(benign_cal)} benign_test={len(benign_test)}")
    print(f"\nNEW improved detector: threshold(p99 benign-cal)={new_threshold:.4f}")
    print(f"  benign-test FP: {pct(new_fp, len(benign_test))}")
    print(f"  benign score: mean={statistics.mean(new_test_scores):.4f} max={max(new_test_scores):.4f}")
    tot = [0, 0]
    for fam in FAMILIES:
        hit, n = new_recall[fam]
        tot[0] += hit
        tot[1] += n
        amean = statistics.mean(new_attack_scores[fam])
        print(f"  recall[{fam} {FAMILY_DESC[fam]:<22}] {pct(hit, n)}  (attack score mean={amean:.4f})")
    print(f"  recall[OVERALL] {pct(tot[0], tot[1])}")

    print(f"\nOLD semantic_muterag_detector (mpnet, calibrated):")
    print(f"  benign-test FP: {pct(old_fp, len(benign_test))}")
    tot2 = [0, 0]
    totm = [0, 0]
    for fam in FAMILIES:
        hit, n = old_recall[fam]
        hm, _ = old_recall_muted[fam]
        tot2[0] += hit
        tot2[1] += n
        totm[0] += hm
        totm[1] += n
        print(f"  recall[{fam} {FAMILY_DESC[fam]:<22}] any={pct(hit, n)}  muted_only={pct(hm, n)}")
    print(f"  recall[OVERALL] any={pct(tot2[0], tot2[1])}  muted_only={pct(totm[0], totm[1])}")


if __name__ == "__main__":
    main()
