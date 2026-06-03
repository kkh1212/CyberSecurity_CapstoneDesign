"""Stage 1-2: write the 50 benign documents and the questions CSV.

Normal docs and questions are produced FIRST, before any attack document.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from topics import TOPICS, build_normal_text  # noqa: E402

ROOT = Path("data/experiments_v4")
NORMAL_DIR = ROOT / "experiment_normal"
QUESTIONS = ROOT / "questions" / "v4_questions.csv"


def main() -> None:
    NORMAL_DIR.mkdir(parents=True, exist_ok=True)
    QUESTIONS.parent.mkdir(parents=True, exist_ok=True)

    for topic in TOPICS:
        (NORMAL_DIR / topic["fname"]).write_text(build_normal_text(topic), encoding="utf-8")

    with QUESTIONS.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question_id", "question", "target_doc", "topic", "expected_answer_hint"])
        for i, topic in enumerate(TOPICS, 1):
            writer.writerow([
                f"Q{i:02d}",
                topic["question"],
                topic["fname"],
                topic["category"],
                topic["hint"],
            ])

    print(f"wrote {len(TOPICS)} normal docs to {NORMAL_DIR}")
    print(f"wrote questions CSV to {QUESTIONS}")


if __name__ == "__main__":
    main()
