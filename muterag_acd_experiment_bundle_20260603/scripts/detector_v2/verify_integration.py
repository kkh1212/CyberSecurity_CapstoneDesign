"""Verify the INTEGRATED query-time path (src.semantic_context_detector, mode=improved).

Runs the held-out eval set through apply_semantic_detector exactly as query_app
would, at the configured threshold, and reports recall per family + benign FP.
"""
import os
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["SEMANTIC_DETECTOR_ENABLED"] = "true"
os.environ["SEMANTIC_DETECTOR_MODE"] = "improved"
os.environ["SEMANTIC_DETECTOR_BACKEND"] = "auto"
os.environ["SEMANTIC_DETECTOR_ACTION"] = "log_only"

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.semantic_context_detector import apply_semantic_detector, load_config  # noqa: E402
import eval_variants  # noqa: E402

cfg = load_config()
print(f"mode={cfg.mode} backend={cfg.backend} improved_threshold={cfg.improved_threshold} action={cfg.action}")


def detected(query, text):
    items = [{"chunk": {"chunk_id": "c::0", "source": "s", "text": text}}]
    _, summary = apply_semantic_detector(query, items, text_getter=lambda c: c["text"], config=cfg)
    return summary["semantic_detector_candidate_count"] > 0


pos = eval_variants.positives()
neg = eval_variants.negatives()

rec = defaultdict(lambda: [0, 0])
for p in pos:
    rec[p["family"]][1] += 1
    if detected(p["query"], p["text"]):
        rec[p["family"]][0] += 1
fp = sum(1 for b in neg if detected(b["query"], b["text"]))

print("\n=== INTEGRATED path (apply_semantic_detector, mode=improved) ===")
tot = [0, 0]
for fam in ["A", "B", "C", "D", "E"]:
    h, n = rec[fam]
    tot[0] += h
    tot[1] += n
    print(f"  recall[{fam}] {h}/{n} ({100*h//n if n else 0}%)")
print(f"  recall[OVERALL] {tot[0]}/{tot[1]} ({100*tot[0]//tot[1]}%)")
print(f"  benign FP: {fp}/{len(neg)} ({100*fp//len(neg)}%)")
