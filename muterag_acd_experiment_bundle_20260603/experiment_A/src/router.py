from src.config import ROUTER_MIN_DENSE_SCORE


GENERAL_CHAT_KEYWORDS = [
    "안녕",
    "자기소개",
    "너는 누구",
    "오늘 기분",
    "반가워",
    "hello",
    "hi",
]


def is_general_chat(query: str):
    q = query.strip().lower()
    return any(keyword.lower() in q for keyword in GENERAL_CHAT_KEYWORDS)


def should_use_rag(query: str, dense_results, sparse_results=None):
    if is_general_chat(query):
        return False

    if dense_results:
        top_score = dense_results[0]["score"]
        return top_score >= ROUTER_MIN_DENSE_SCORE

    if sparse_results:
        return sparse_results[0]["score"] > 0

    return False
