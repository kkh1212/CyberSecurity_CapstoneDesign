from __future__ import annotations

import re


_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|(?:\n\s*\n+)"
)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def split_english_sentences(
    text: str,
    *,
    min_chars: int = 12,
    max_sentences: int = 24,
) -> list[str]:
    """Split English prose into sentence-like units.

    This intentionally stays lightweight and deterministic. It avoids external
    NLP dependencies so the detector can run in the existing experiment image.
    """

    if not text:
        return []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_parts: list[str] = []
    for paragraph in re.split(r"\n\s*\n+", normalized):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        raw_parts.extend(_SENTENCE_BOUNDARY_RE.split(paragraph))

    sentences: list[str] = []
    for part in raw_parts:
        cleaned = normalize_whitespace(part)
        if len(cleaned) < min_chars:
            continue
        sentences.append(cleaned)
        if len(sentences) >= max_sentences:
            break

    if not sentences:
        fallback = normalize_whitespace(text)
        return [fallback] if fallback else []

    return sentences
