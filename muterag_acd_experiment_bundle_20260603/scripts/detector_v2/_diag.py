import os, sys
from pathlib import Path
os.environ.setdefault("HF_HUB_OFFLINE", "1"); os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from improved_detector import ImprovedDetector, Weights
import eval_variants

new = ImprovedDetector("auto"); w = Weights()
neg = eval_variants.negatives(); pos = eval_variants.positives()


def best_sentence(feat):
    best_i, best_v = -1, -1.0
    for i in range(feat.n_sentences):
        ev = max(feat.foreign[i], feat.intent_arr[i] * feat.off_topic[i])
        if ev > best_v:
            best_v, best_i = ev, i
    return best_i, best_v


print("=== TOP benign by score (FP drivers) ===")
scored = []
for b in neg:
    f = new.features(b["query"], b["text"]); s = new.score(f, w)
    scored.append((s, b, f))
scored.sort(key=lambda x: -x[0])
for s, b, f in scored[:4]:
    i, ev = best_sentence(f)
    print(f"score={s:.3f} | foreign={f.foreign[i]:.2f} intent={f.intent_arr[i]:.2f} offq={f.off_query[i]:.2f} offtop={f.off_topic[i]:.2f}")
    print(f"   driver: {f.sentences[i][:140]}")

print("\n=== E (multilingual) sample diagnostics ===")
e = [p for p in pos if p["family"] == "E"][:3]
for p in e:
    f = new.features(p["query"], p["text"]); s = new.score(f, w)
    i, ev = best_sentence(f)
    print(f"score={s:.3f} anchor={f.anchor:.2f}")
    for j in range(f.n_sentences):
        print(f"   [{j}] foreign={f.foreign[j]:.2f} intent={f.intent_arr[j]:.2f} offq={f.off_query[j]:.2f} offtop={f.off_topic[j]:.2f} :: {f.sentences[j][:90]}")
