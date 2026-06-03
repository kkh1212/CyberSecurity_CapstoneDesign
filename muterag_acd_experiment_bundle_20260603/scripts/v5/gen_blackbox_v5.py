"""v5 Stage 6: black-box attack documents (P = Q). Identical attack method to v4
(build_muted_doc / build_direct_doc reused from scripts/v4/attacks.py); only the
benign tail is the longer v5 content. 50 direct_blackbox + 50 muted_blackbox.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from topics_v5 import TOPICS, normal_tail_v5, direct_record_block  # noqa: E402
from attacks import build_muted_doc, build_direct_doc  # noqa: E402  (reused v4 attack builders)

ROOT = Path("data/experiments_v5")
MUTED_DIR = ROOT / "experiment_muted_blackbox"
DIRECT_DIR = ROOT / "experiment_direct_blackbox"


def main():
    MUTED_DIR.mkdir(parents=True, exist_ok=True)
    DIRECT_DIR.mkdir(parents=True, exist_ok=True)

    for i, topic in enumerate(TOPICS):
        prefix = topic["question"]            # P = Q (verbatim)
        tail = normal_tail_v5(topic)
        rec = direct_record_block(topic)
        (MUTED_DIR / topic["fname"]).write_text(build_muted_doc(prefix, topic, i, tail), encoding="utf-8")
        (DIRECT_DIR / topic["fname"]).write_text(build_direct_doc(prefix, topic, i, tail, rec), encoding="utf-8")

    print(f"wrote {len(TOPICS)} muted_blackbox docs to {MUTED_DIR}")
    print(f"wrote {len(TOPICS)} direct_blackbox docs to {DIRECT_DIR}")


if __name__ == "__main__":
    main()
