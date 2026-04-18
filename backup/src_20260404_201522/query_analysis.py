import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


FIELD_ALIASES: Dict[str, List[str]] = {
    "연번": ["연번"],
    "캠퍼스": ["캠퍼스"],
    "교과목번호": ["교과목번호", "과목코드", "과목 코드", "코드"],
    "분반": ["분반"],
    "학점": ["학점"],
    "교강사명": ["교강사명", "교강사", "교수명", "교수", "강사명", "강사"],
    "교과목명": ["교과목명", "과목명"],
    "프로그램명": ["프로그램명", "사업명"],
    "주수강권장조직": ["주수강권장조직", "권장조직"],
    "권장학년": ["권장학년"],
    "운영 부서": ["운영 부서", "주관 부서", "담당 부서"],
    "담당자": ["담당자"],
    "장소": ["장소", "위치"],
    "문의처": ["문의처", "연락처", "전화", "문의"],
    "신청 마감일": ["신청 마감일", "신청 마감", "마감일", "신청마감"],
    "운영 기간": ["운영 기간", "운영기간", "활동 기간", "기간"],
    "활동 지역": ["활동 지역"],
    "주요 활동": ["주요 활동"],
    "운영 방식": ["운영 방식"],
    "지원 대상": ["지원 대상"],
    "비고": ["비고"],
    "지원 금액": ["지원 금액", "지원금", "지원금액", "금액", "개인 부담금"],
    "총 인원": ["총 인원", "총인원"],
    "학생 선발 인원": ["학생 선발 인원", "선발 인원", "학생 선발", "학생 인원"],
    "인솔자 인원": ["인솔자 인원", "인솔자"],
    "이수영역": ["이수영역", "이수 영역"],
    "시간표": ["시간표"],
    "일정": ["일정"],
    "참여 인원": ["참여 인원"],
}

SUMMARY_KEYWORDS = {
    "요약",
    "정리",
    "설명",
    "개요",
    "핵심",
}

COMPARE_KEYWORDS = {
    "각각",
    "비교",
}

INFO_KEYWORDS = {
    "정보",
    "상세",
    "자세히",
    "어떤",
}

COMMON_QUERY_TOKENS = {
    "모니터링단",
    "대상",
    "대상교과목",
    "교과목",
    "과목",
    "문서",
    "관련",
    "안내",
    "내용",
    "질문",
    "알려줘",
    "알려줄래",
    "언제",
    "언제야",
    "무엇",
    "뭐",
    "각각",
    "비교",
    "요약",
    "정리",
    "설명",
    "요약해줘",
    "3문장",
    "문장",
    "에서",
    "의",
    "와",
    "과",
    "및",
}

KOREAN_PARTICLES = (
    "으로는",
    "으로",
    "에서는",
    "에서",
    "에게",
    "까지",
    "부터",
    "처럼",
    "만의",
    "만",
    "는",
    "은",
    "이",
    "가",
    "을",
    "를",
    "와",
    "과",
    "의",
    "에",
    "도",
)

ALL_FIELD_TERMS = {
    alias
    for canonical, aliases in FIELD_ALIASES.items()
    for alias in [canonical, *aliases]
}

GENERIC_QUERY_TERMS = COMMON_QUERY_TOKENS | ALL_FIELD_TERMS


@dataclass
class QueryProfile:
    query: str
    quoted_terms: List[str] = field(default_factory=list)
    requested_fields: List[str] = field(default_factory=list)
    entity_terms: List[str] = field(default_factory=list)
    compare_entities: List[str] = field(default_factory=list)
    document_hints: List[str] = field(default_factory=list)
    summary_requested: bool = False
    compare_requested: bool = False
    info_requested: bool = False
    exact_lookup: bool = False


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text).lower())


def normalize_keyword(token: str) -> str:
    token = normalize_text(token).strip('"').strip("'")
    for particle in KOREAN_PARTICLES:
        if token.endswith(particle) and len(token) > len(particle) + 1:
            token = token[: -len(particle)]
            break
    return token.strip()


def extract_quoted_terms(query: str) -> List[str]:
    return [normalize_keyword(term) for term in re.findall(r'"([^"]+)"', query) if normalize_keyword(term)]


def canonicalize_label(label: str) -> Optional[str]:
    label = normalize_text(label)
    for canonical, aliases in FIELD_ALIASES.items():
        if label == canonical or label in aliases:
            return canonical
    return None


def tokenize_keywords(query: str) -> List[str]:
    raw_tokens = re.findall(r"[가-힣A-Za-z0-9]+", query)
    seen = set()
    tokens: List[str] = []
    for raw in raw_tokens:
        token = normalize_keyword(raw)
        if len(token) < 2 or token in seen:
            continue
        tokens.append(token)
        seen.add(token)
    return tokens


def detect_requested_fields(query: str) -> List[str]:
    found: List[str] = []
    for canonical, aliases in FIELD_ALIASES.items():
        if any(alias_in_query(query, alias) for alias in aliases):
            found.append(canonical)
    return found


def alias_in_query(query: str, alias: str) -> bool:
    start = 0
    while True:
        idx = query.find(alias, start)
        if idx == -1:
            return False

        prev = query[idx - 1] if idx > 0 else ""
        if prev and re.match(r"[가-힣A-Za-z0-9]", prev):
            start = idx + 1
            continue

        suffix = query[idx + len(alias) :]
        stripped = suffix
        for particle in KOREAN_PARTICLES:
            if stripped.startswith(particle):
                stripped = stripped[len(particle) :]
                break

        if not stripped or re.match(r"[\s,.!?]", stripped[0]):
            return True

        start = idx + 1


def extract_document_hints(query: str) -> List[str]:
    hints: List[str] = []
    for match in re.finditer(r"([가-힣A-Za-z0-9\s]+?)\s*문서를?", query):
        hint = normalize_keyword(match.group(1))
        if hint and len(hint) >= 2:
            hints.append(hint)
    return hints


def extract_compare_entities(query: str, requested_fields: List[str]) -> List[str]:
    quoted_terms = extract_quoted_terms(query)
    if len(quoted_terms) >= 2:
        return quoted_terms[:2]

    cutoff_positions = []
    for canonical in requested_fields:
        for alias in [canonical, *FIELD_ALIASES.get(canonical, [])]:
            pos = query.find(alias)
            if pos > 0:
                cutoff_positions.append(pos)
    cutoff = min(cutoff_positions) if cutoff_positions else len(query)
    prefix = query[:cutoff]
    prefix = re.sub(r"(각각|비교).*?$", "", prefix).strip()
    prefix = re.sub(r"\s*관련 문서.*$", "", prefix).strip()

    parts = re.split(r"(?:와|과)\s+|\s+및\s+", prefix)
    entities: List[str] = []
    for part in parts:
        entity = normalize_keyword(part)
        if not entity or entity in GENERIC_QUERY_TERMS:
            continue
        if entity not in entities:
            entities.append(entity)
    return entities


def extract_meaningful_keywords(query: str) -> List[str]:
    profile = build_query_profile(query)
    if profile.entity_terms:
        return profile.entity_terms
    if profile.document_hints:
        return profile.document_hints
    return []


def build_query_profile(query: str) -> QueryProfile:
    normalized_query = normalize_text(query)
    requested_fields = detect_requested_fields(normalized_query)
    quoted_terms = extract_quoted_terms(normalized_query)
    compare_requested = any(keyword in normalized_query for keyword in COMPARE_KEYWORDS)
    summary_requested = any(keyword in normalized_query for keyword in SUMMARY_KEYWORDS)
    info_requested = any(keyword in normalized_query for keyword in INFO_KEYWORDS)
    document_hints = extract_document_hints(normalized_query)
    compare_entities = extract_compare_entities(normalized_query, requested_fields)
    if len(compare_entities) >= 2:
        compare_requested = True
    elif not compare_requested:
        compare_entities = []

    tokens = tokenize_keywords(normalized_query)
    entity_terms: List[str] = []
    if quoted_terms and not compare_requested:
        entity_terms = quoted_terms
    elif compare_entities:
        entity_terms = compare_entities
    else:
        for token in tokens:
            if token in GENERIC_QUERY_TERMS:
                continue
            entity_terms.append(token)

    if summary_requested and document_hints:
        entity_terms = []

    exact_lookup = bool(requested_fields) and not summary_requested

    return QueryProfile(
        query=normalized_query,
        quoted_terms=quoted_terms,
        requested_fields=requested_fields,
        entity_terms=entity_terms,
        compare_entities=compare_entities,
        document_hints=document_hints,
        summary_requested=summary_requested,
        compare_requested=compare_requested,
        info_requested=info_requested,
        exact_lookup=exact_lookup,
    )


def preferred_output_label(query: str, canonical_field: str) -> str:
    lowered = query.lower()
    for alias in FIELD_ALIASES.get(canonical_field, []):
        if alias.lower() in lowered:
            return alias
    return canonical_field
