import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from src.query_analysis import (
    FIELD_ALIASES,
    QueryProfile,
    build_query_profile,
    canonicalize_label,
    compact_text,
    normalize_text,
    preferred_output_label,
)


ALL_LABELS = sorted(
    {label for canonical, aliases in FIELD_ALIASES.items() for label in [canonical, *aliases]},
    key=len,
    reverse=True,
)
LABEL_PATTERN = re.compile("(" + "|".join(re.escape(label) for label in ALL_LABELS) + r")\s*:")
TITLE_FIELDS = ("교과목명", "프로그램명", "사업명")


@dataclass
class StructuredCandidate:
    chunk: Dict
    values: Dict[str, str]
    title: str
    raw_text: str
    score: float


INFO_FIELD_PRIORITY = [
    "교과목번호",
    "교강사명",
    "이수영역",
    "시간",
    "장소",
    "일정",
    "문의처",
    "요청 마감일",
    "참여 인원",
    "운영 기간",
    "지원 금액",
    "총 인원",
    "학생 선발 인원",
    "인솔자 인원",
    "주요 활동",
]


def extract_label_value_pairs_from_text(text: str) -> Dict[str, str]:
    extracted: Dict[str, str] = {}
    matches = list(LABEL_PATTERN.finditer(text))
    if not matches:
        return extracted

    for idx, match in enumerate(matches):
        raw_label = match.group(1)
        canonical = canonicalize_label(raw_label)
        if not canonical:
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        value = normalize_text(text[start:end].strip(" -:\n\t;"))
        if value and canonical not in extracted:
            extracted[canonical] = value
    return extracted


def parse_structured_row(text: str) -> Optional[Dict[str, str]]:
    row = extract_label_value_pairs_from_text(text)
    return row or None


def extract_structured_rows(chunks: Iterable[Dict]) -> List[StructuredCandidate]:
    candidates: List[StructuredCandidate] = []
    for chunk in chunks:
        lines = [chunk.get("text", "")]
        if chunk.get("block_type") != "table_row":
            lines = chunk.get("text", "").splitlines()

        for line in lines:
            if ":" not in line:
                continue
            values = parse_structured_row(line)
            if not values:
                continue
            title = values.get("교과목명") or values.get("프로그램명") or chunk.get("entity_title", "")
            candidates.append(
                StructuredCandidate(
                    chunk=chunk,
                    values=values,
                    title=normalize_text(title),
                    raw_text=normalize_text(line),
                    score=0.0,
                )
            )
    return candidates


def split_labeled_sections(text: str) -> List[str]:
    lines = [line.rstrip() for line in text.splitlines()]
    sections: List[List[str]] = []
    current: List[str] = []

    def flush():
        nonlocal current
        if current:
            sections.append(current)
            current = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue

        clean = stripped.lstrip("-").strip()
        starts_new = clean.startswith(("프로그램명", "사업명", "교과목명:")) or bool(re.match(r"^\d+\.\s", stripped))
        if starts_new:
            flush()
        current.append(stripped)

    flush()
    return ["\n".join(section) for section in sections]


def extract_section_candidates(chunks: Iterable[Dict]) -> List[StructuredCandidate]:
    candidates: List[StructuredCandidate] = []
    for chunk in chunks:
        for section in split_labeled_sections(chunk.get("text", "")):
            values = extract_label_value_pairs_from_text(section)
            if not values:
                continue
            title = values.get("프로그램명") or values.get("교과목명") or chunk.get("entity_title", "")
            candidates.append(
                StructuredCandidate(
                    chunk=chunk,
                    values=values,
                    title=normalize_text(title),
                    raw_text=normalize_text(section),
                    score=0.0,
                )
            )
    return candidates


def match_terms(text: str, terms: Iterable[str]) -> List[str]:
    normalized = normalize_text(text)
    compact_normalized = compact_text(text)
    matched: List[str] = []
    for term in terms:
        if not term:
            continue
        if term in normalized or compact_text(term) in compact_normalized:
            matched.append(term)
    return matched


def normalize_entity_key(text: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", normalize_text(text).lower())


def is_exact_entity_match(profile: QueryProfile, candidate: StructuredCandidate) -> bool:
    if not profile.entity_terms:
        return False

    candidate_titles = [
        candidate.title,
        candidate.chunk.get("entity_title", ""),
        candidate.values.get("교과목명", ""),
        candidate.values.get("프로그램명", ""),
    ]
    normalized_titles = {normalize_entity_key(title) for title in candidate_titles if title}

    for entity in profile.entity_terms:
        normalized_entity = normalize_entity_key(entity)
        if normalized_entity and normalized_entity in normalized_titles:
            return True
    return False


def score_candidate(profile: QueryProfile, candidate: StructuredCandidate) -> float:
    text = normalize_text(candidate.raw_text)
    title = normalize_text(candidate.title)
    source = normalize_text(candidate.chunk.get("source", ""))
    entity_title = normalize_text(candidate.chunk.get("entity_title", ""))

    entity_hits = set(match_terms(title, profile.entity_terms))
    entity_hits.update(match_terms(entity_title, profile.entity_terms))
    entity_hits.update(match_terms(text, profile.entity_terms))

    document_hits = set(match_terms(title, profile.document_hints))
    document_hits.update(match_terms(source, profile.document_hints))
    document_hits.update(match_terms(text, profile.document_hints))

    if (profile.entity_terms or profile.document_hints) and not entity_hits and not document_hits:
        return -1.0

    score = 0.0
    if is_exact_entity_match(profile, candidate):
        score += 240.0
    score += 120.0 * len(document_hits)
    score += 100.0 * len(match_terms(title, profile.entity_terms))
    score += 80.0 * len(match_terms(entity_title, profile.entity_terms))
    score += 45.0 * len(match_terms(text, profile.entity_terms))

    available_fields = [field for field in profile.requested_fields if field in candidate.values]
    score += 35.0 * len(available_fields)
    if len(available_fields) == len(profile.requested_fields):
        score += 80.0

    if candidate.chunk.get("block_type") == "table_row":
        score += 35.0
    elif candidate.chunk.get("block_type") == "table":
        score += 10.0
    else:
        score += 15.0

    if title:
        score += 10.0

    return score


def rank_candidates(profile: QueryProfile, chunks: Iterable[Dict]) -> List[StructuredCandidate]:
    combined = extract_structured_rows(chunks) + extract_section_candidates(chunks)
    ranked: List[StructuredCandidate] = []
    seen = set()

    for candidate in combined:
        key = (
            candidate.chunk.get("chunk_id"),
            tuple(sorted(candidate.values.items())),
        )
        if key in seen:
            continue
        seen.add(key)
        candidate.score = score_candidate(profile, candidate)
        if candidate.score > 0:
            ranked.append(candidate)

    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked


def format_fields(profile: QueryProfile, values: Dict[str, str]) -> str:
    lines = []
    for field in profile.requested_fields:
        if field not in values:
            continue
        label = preferred_output_label(profile.query, field)
        lines.append(f"{label}: {values[field]}")
    return "\n".join(lines)


def build_compare_answer(profile: QueryProfile, candidates: List[StructuredCandidate]) -> Optional[Dict[str, object]]:
    if len(profile.compare_entities) < 2:
        return None

    matched_sections = []
    for entity in profile.compare_entities:
        best: Optional[StructuredCandidate] = None
        for candidate in candidates:
            title = normalize_text(candidate.title)
            text = normalize_text(candidate.raw_text)
            if entity not in title and entity not in text:
                continue
            if best is None or candidate.score > best.score:
                best = candidate
        if best is not None:
            matched_sections.append((entity, best))

    if len(matched_sections) < 2:
        return None

    answer_lines: List[str] = []
    for entity, candidate in matched_sections:
        title = candidate.title or entity
        answer_lines.append(title)
        formatted = format_fields(profile, candidate.values)
        if formatted:
            answer_lines.extend(f"- {line}" for line in formatted.splitlines())

    return {
        "answer": "\n".join(answer_lines),
        "chunk": matched_sections[0][1].chunk,
        "row": matched_sections[0][1].values,
    }


def build_single_answer(profile: QueryProfile, candidates: List[StructuredCandidate]) -> Optional[Dict[str, object]]:
    if not candidates:
        return None

    candidates = [candidate for candidate in candidates if any(field in candidate.values for field in profile.requested_fields)]
    if not candidates:
        return None

    candidates.sort(
        key=lambda candidate: (
            sum(field in candidate.values for field in profile.requested_fields),
            candidate.score,
        ),
        reverse=True,
    )
    best = candidates[0]
    answer = format_fields(profile, best.values)
    if not answer:
        return None

    return {
        "answer": answer,
        "chunk": best.chunk,
        "row": best.values,
    }


def build_info_answer(profile: QueryProfile, candidates: List[StructuredCandidate]) -> Optional[Dict[str, object]]:
    if profile.requested_fields or not profile.entity_terms:
        return None

    exact_matches = [candidate for candidate in candidates if is_exact_entity_match(profile, candidate)]
    if not exact_matches:
        return None

    best = exact_matches[0]
    ordered_fields = [field for field in INFO_FIELD_PRIORITY if field in best.values]
    if not ordered_fields:
        return None

    lines = [f"{field}: {best.values[field]}" for field in ordered_fields[:6]]
    return {
        "answer": "\n".join(lines),
        "chunk": best.chunk,
        "row": best.values,
    }


def build_structured_answer(query: str, chunks: Iterable[Dict]) -> Optional[Dict[str, object]]:
    profile = build_query_profile(query)

    candidates = rank_candidates(profile, list(chunks))
    if profile.info_requested and not profile.requested_fields:
        info_answer = build_info_answer(profile, candidates)
        if info_answer:
            return info_answer

    if not profile.requested_fields:
        return None

    if profile.compare_requested:
        compare_answer = build_compare_answer(profile, candidates)
        if compare_answer:
            return compare_answer

    return build_single_answer(profile, candidates)
