"""Stage 8: white-box attack documents.

doc = selected_prefix(cluster, attack_type) + payload + normal_tail(target_doc).
Questions in the same cluster share the optimized prefix; the payload and benign
tail remain specific to each question's target document. 50 muted + 50 direct.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from topics import TOPICS, normal_tail, direct_record_block  # noqa: E402
from attacks import build_muted_doc, build_direct_doc  # noqa: E402

ROOT = Path("data/experiments_v4")
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
        tail = normal_tail(topic)
        rec = direct_record_block(topic)

        muted_prefix = sel["muted"][cid]
        direct_prefix = sel["direct"][cid]

        (MUTED_DIR / topic["fname"]).write_text(
            build_muted_doc(muted_prefix, topic, i, tail), encoding="utf-8")
        (DIRECT_DIR / topic["fname"]).write_text(
            build_direct_doc(direct_prefix, topic, i, tail, rec), encoding="utf-8")

    print(f"wrote {len(TOPICS)} muted_whitebox docs to {MUTED_DIR}")
    print(f"wrote {len(TOPICS)} direct_whitebox docs to {DIRECT_DIR}")


if __name__ == "__main__":
    main()
