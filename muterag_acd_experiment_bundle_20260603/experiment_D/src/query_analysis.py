import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


FIELD_ALIASES: Dict[str, List[str]] = {
    "과번": ["과번"],
    "캠퍼스": ["캠퍼스"],
    "과목번호": ["과목번호", "과목코드", "과목 코드", "코드"],
    "분반": ["분반"],
    "학점": ["학점"],
    "교강사명": ["교강사명", "교강사", "교수명", "교수", "강사명", "강사"],
    "과목명": ["과목명", "교과목명"],
    "프로그램명": ["프로그램명", "사업명"],
    "권장조직": ["권장조직"],
    "권장학년": ["권장학년"],
    "운영 부서": ["운영 부서", "주관 부서", "해당 부서"],
    "대상": ["대상"],
    "장소": ["장소", "위치"],
    "문의처": ["문의처", "연락처", "문의"],
    "신청 마감일": ["신청 마감일", "신청 마감", "마감일", "접수 마감일", "접수 마감"],
    "운영 기간": ["운영 기간", "운영기간", "활동 기간", "기간"],
    "활동 지역": ["활동 지역"],
    "주요 활동": ["주요 활동"],
    "운영 방식": ["운영 방식"],
    "지원 내용": ["지원 내용", "지원사항"],
    "비고": ["비고"],
    "지원 금액": ["지원 금액", "지원금", "금액", "개인 부담금"],
    "총 인원": ["총 인원", "총원"],
    "학생 선발 인원": ["학생 선발 인원", "선발 인원", "학생 인원"],
    "인솔자 인원": ["인솔자 인원", "인솔자"],
    "이수영역": ["이수영역", "이수 영역"],
    "시간": ["시간"],
    "일정": ["일정"],
    "참여 인원": ["참여 인원"],
    "대상 조건": ["대상 조건", "적용 대상", "신청 조건", "자격 요건"],
    "최대 신청 가능 일수": ["최대 신청 가능 일수", "최대 신청 일수", "신청 가능 일수", "한도 일수"],
    "계정 관리 원칙": ["계정 관리 원칙", "계정 관리", "관리 원칙"],
    "필수 대상": ["필수 대상", "적용 대상", "대상 시스템"],
    "필수 제출 서류": ["필수 제출 서류", "제출 서류", "필수 서류", "구비 서류"],
    "제출 기한": ["제출 기한", "기한", "제출 기간"],
    "선발 방식": ["선발 방식", "선발 방법", "전형 방법", "평가 방식"],
    "주의사항": ["주의사항", "유의사항", "주의 사항"],
    "신청 자격": ["신청 자격", "지원 자격"],
    "제출 방법": ["제출 방법", "신청 방법"],
    "급여 구성 항목": ["급여 구성 항목", "급여 항목", "구성 항목"],
    "지급 시기": ["지급 시기", "지급일", "지급일자"],
    "공제 가능 항목": ["공제 가능 항목", "공제 항목"],
}

SUMMARY_KEYWORDS = {"요약", "개요", "핵심만", "3문장", "세 문장"}
COMPARE_KEYWORDS = {"비교", "차이", "다른지", "구분", "분류", "나눠", "나눠서"}
PROCEDURE_KEYWORDS = {"절차", "단계", "순서", "과정", "흐름", "처리"}
SYNTHESIS_KEYWORDS = {"함께 보고", "바탕으로", "종합", "연결", "연계", "한 번에", "묶어서"}
INFO_KEYWORDS = {"정보", "상세", "자세히", "어떤"}

COMMON_QUERY_TOKENS = {
    "문서",
    "규정",
    "지침",
    "안내",
    "내용",
    "질문",
    "알려줘",
    "알려줄래",
    "무엇",
    "뭐야",
    "요약",
    "정리",
    "설명",
    "비교",
    "구분",
    "분류",
    "단계",
    "순서",
    "기준",
    "바탕으로",
    "함께",
    "보고",
}

KOREAN_PARTICLES = (
    "으로는",
    "으로",
    "에서의",
    "에서",
    "에게",
    "까지",
    "부터",
    "처럼",
    "만의",
    "만",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "와",
    "과",
    "의",
)

DOCUMENT_SUFFIXES = (
    "규정",
    "지침",
    "학칙 시행세칙",
    "시행세칙",
    "학칙",
    "안내문",
    "안내",
    "절차",
    "기준",
    "정책",
    "방침",
    "모집안내",
    "모집 안내",
    "공고문",
    "운영규정",
)
DOCUMENT_TITLE_PATTERN = re.compile(
    r"([가-힣A-Za-z0-9\-\(\)\s]{2,}?(?:" + "|".join(re.escape(suffix) for suffix in DOCUMENT_SUFFIXES) + r"))"
)

ALL_FIELD_TERMS = {label for canonical, aliases in FIELD_ALIASES.items() for label in [canonical, *aliases]}
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
    procedure_requested: bool = False
    synthesis_requested: bool = False
    multi_document_requested: bool = False
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


def detect_requested_fields(query: str) -> List[str]:
    found: List[str] = []
    for canonical, aliases in FIELD_ALIASES.items():
        if any(alias_in_query(query, alias) for alias in [canonical, *aliases]):
            found.append(canonical)
    return found


def extract_document_hints(query: str) -> List[str]:
    hints: List[str] = []
    seen = set()

    def split_hint_candidates(text: str) -> List[str]:
        text = normalize_text(text)
        if not text:
            return []

        parts = re.split(r"\s*(?:와|과|및|,|/|&|\bvs\b)\s*", text, flags=re.IGNORECASE)
        split_parts: List[str] = []
        for part in parts:
            candidate = normalize_keyword(part)
            if len(candidate) < 2:
                continue

            nested_matches = [normalize_text(match.group(1)) for match in DOCUMENT_TITLE_PATTERN.finditer(candidate)]
            if nested_matches:
                split_parts.extend(nested_matches)
            else:
                split_parts.append(candidate)
        return split_parts or [text]

    def append_hint(raw_hint: str):
        for piece in split_hint_candidates(raw_hint):
            hint = normalize_keyword(piece)
            if len(hint) < 2:
                continue
            if hint in GENERIC_QUERY_TERMS:
                continue
            if hint in seen:
                continue
            seen.add(hint)
            hints.append(hint)

    for match in re.finditer(r"([가-힣A-Za-z0-9\s]+?)\s*문서", query):
        append_hint(match.group(1))

    for match in DOCUMENT_TITLE_PATTERN.finditer(query):
        candidate = normalize_text(match.group(1))
        candidate = re.sub(r"(기준으로|기준|바탕으로|함께 보고|함께)$", "", candidate).strip()
        append_hint(candidate)

    return hints


def extract_compare_entities(query: str, requested_fields: List[str], document_hints: List[str]) -> List[str]:
    quoted_terms = extract_quoted_terms(query)
    if len(quoted_terms) >= 2:
        return quoted_terms[:2]

    if len(document_hints) >= 2:
        return document_hints[:2]

    cutoff_positions = []
    for canonical in requested_fields:
        for alias in [canonical, *FIELD_ALIASES.get(canonical, [])]:
            pos = query.find(alias)
            if pos > 0:
                cutoff_positions.append(pos)
    cutoff = min(cutoff_positions) if cutoff_positions else len(query)

    prefix = query[:cutoff]
    prefix = re.sub(r"(각각|비교|차이|다른지).*?$", "", prefix).strip()
    prefix = re.sub(r"\s*관련 문서.*$", "", prefix).strip()

    parts = re.split(r"\s*(?:와|과|및|/|&|\bvs\b)\s*", prefix, flags=re.IGNORECASE)
    entities: List[str] = []
    for part in parts:
        entity = normalize_keyword(part)
        if not entity or entity in GENERIC_QUERY_TERMS:
            continue
        if len(entity) > 40 or len(entity.split()) > 5:
            continue
        if entity not in entities:
            entities.append(entity)

    return entities if len(entities) >= 2 else []


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
    summary_requested = any(keyword in normalized_query for keyword in SUMMARY_KEYWORDS)
    procedure_requested = any(keyword in normalized_query for keyword in PROCEDURE_KEYWORDS)
    synthesis_requested = any(keyword in normalized_query for keyword in SYNTHESIS_KEYWORDS)
    compare_requested = any(keyword in normalized_query for keyword in COMPARE_KEYWORDS)
    info_requested = any(keyword in normalized_query for keyword in INFO_KEYWORDS)
    document_hints = extract_document_hints(normalized_query)
    multi_document_requested = len(document_hints) >= 2
    compare_entities = extract_compare_entities(normalized_query, requested_fields, document_hints)

    if len(compare_entities) >= 2:
        compare_requested = True

    tokens = tokenize_keywords(normalized_query)
    entity_terms: List[str] = []
    if quoted_terms and not compare_requested:
        entity_terms = list(quoted_terms)
    else:
        if compare_entities:
            entity_terms.extend(compare_entities)
        for token in tokens:
            if token in GENERIC_QUERY_TERMS:
                continue
            if token in document_hints:
                continue
            if token in entity_terms:
                continue
            entity_terms.append(token)

    exact_lookup = (
        bool(requested_fields)
        and not summary_requested
        and not compare_requested
        and not procedure_requested
        and not synthesis_requested
        and not multi_document_requested
    )

    return QueryProfile(
        query=normalized_query,
        quoted_terms=quoted_terms,
        requested_fields=requested_fields,
        entity_terms=entity_terms,
        compare_entities=compare_entities if compare_requested else [],
        document_hints=document_hints,
        summary_requested=summary_requested,
        compare_requested=compare_requested,
        info_requested=info_requested,
        procedure_requested=procedure_requested,
        synthesis_requested=synthesis_requested,
        multi_document_requested=multi_document_requested,
        exact_lookup=exact_lookup,
    )


def preferred_output_label(query: str, canonical_field: str) -> str:
    lowered = query.lower()
    for alias in [canonical_field, *FIELD_ALIASES.get(canonical_field, [])]:
        if alias.lower() in lowered:
            return alias
    return canonical_field
