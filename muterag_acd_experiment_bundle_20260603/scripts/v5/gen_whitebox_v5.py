"""v5 Stage 8: white-box attack documents.

doc = selected_prefix(cluster, attack_type) + payload + normal_tail_v5(target).
Same method as v4; uses v5 clustering/prefixes and the longer v5 benign tail.
50 muted_whitebox + 50 direct_whitebox.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from topics_v5 import TOPICS, normal_tail_v5, direct_record_block  # noqa: E402
from attacks import build_muted_doc, build_direct_doc  # noqa: E402

ROOT = Path("data/experiments_v5")
META = ROOT / "metadata"
MUTED_DIR = ROOT / "experiment_muted_whitebox"
DIRECT_DIR = ROOT / "experiment_direct_whitebox"


def main():
    MUTED_DIR.mkdir(parents=True, exist_ok=True)
    DIRECT_DIR.mkdir(parents=True, exist_ok=True)
    sel = json.loads((META / "selected_prefixes.json").read_text(encoding="utf-8"))
    q2c = sel["qid_to_cluster"]

    for i, topic in enumerate(TOPICS):
        qid = f"Q{i+1:02d}"
        cid = q2c[qid]
        tail = normal_tail_v5(topic)
        rec = direct_record_block(topic)
        (MUTED_DIR / topic["fname"]).write_text(build_muted_doc(sel["muted"][cid], topic, i, tail), encoding="utf-8")
        (DIRECT_DIR / topic["fname"]).write_text(build_direct_doc(sel["direct"][cid], topic, i, tail, rec), encoding="utf-8")

    print(f"wrote {len(TOPICS)} muted_whitebox docs to {MUTED_DIR}")
    print(f"wrote {len(TOPICS)} direct_whitebox docs to {DIRECT_DIR}")


if __name__ == "__main__":
    main()
