"""v5 Stage 1-2: write the 50 EXPANDED benign documents and v5_questions.csv.

Normal docs and questions are produced first. Questions and target mapping are
identical to v4 (reused from topics_v5, which reuses v4 metadata); only the
document bodies are longer.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from topics_v5 import TOPICS, build_normal_text_v5  # noqa: E402

ROOT = Path("data/experiments_v5")
NORMAL_DIR = ROOT / "experiment_normal"
QUESTIONS = ROOT / "questions" / "v5_questions.csv"


def main() -> None:
    NORMAL_DIR.mkdir(parents=True, exist_ok=True)
    QUESTIONS.parent.mkdir(parents=True, exist_ok=True)

    for topic in TOPICS:
        (NORMAL_DIR / topic["fname"]).write_text(build_normal_text_v5(topic), encoding="utf-8")

    with QUESTIONS.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question_id", "question", "target_doc", "topic", "expected_answer_hint"])
        for i, topic in enumerate(TOPICS, 1):
            writer.writerow([f"Q{i:02d}", topic["question"], topic["fname"], topic["category"], topic["hint"]])

    print(f"wrote {len(TOPICS)} expanded normal docs to {NORMAL_DIR}")
    print(f"wrote questions CSV to {QUESTIONS}")


if __name__ == "__main__":
    main()
