"""
RAG vs 일반 대화 라우터
출처: 조원 코드 src/router.py 적용
"""

GENERAL_CHAT_KEYWORDS = [
    "안녕",
    "자기소개",
    "너는 누구",
    "오늘 기분",
    "반가워",
    "hello",
    "hi",
]

ROUTER_MIN_DENSE_SCORE = 0.45


def is_general_chat(query: str) -> bool:
    q = query.strip().lower()
    return any(kw.lower() in q for kw in GENERAL_CHAT_KEYWORDS)


def should_use_rag(query: str, dense_results: list, sparse_results: list = None) -> bool:
    if is_general_chat(query):
        return False
    if dense_results:
        return dense_results[0]["score"] >= ROUTER_MIN_DENSE_SCORE
    if sparse_results:
        return bool(sparse_results) and sparse_results[0]["score"] > 0
    return False
