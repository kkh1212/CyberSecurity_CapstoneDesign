"""
LLM 프롬프트 빌더
출처: 조원 코드 src/prompts.py 기반, 회사 문서 컨텍스트로 수정
"""


def build_rag_system_prompt(final_chunks: list) -> str:
    """
    RAG 컨텍스트를 담은 system 메시지 생성.
    쿼리는 별도의 user 메시지로 전달됨.
    """
    context_blocks = []
    for i, item in enumerate(final_chunks, start=1):
        chunk = item["chunk"]
        context_blocks.append(
            f"[문서 {i}] 출처: {chunk['source']}\n{chunk['text']}"
        )
    context = "\n\n".join(context_blocks)

    return f"""당신은 회사 내부 문서를 기반으로 직원들의 질문에 답변하는 AI 어시스턴트입니다.

아래 참고 문서를 최대한 활용하여 친절하고 상세하게 한국어로 답변하세요.
- 참고 문서에 관련 내용이 있으면 반드시 그 내용을 중심으로 답변하세요.
- 문서에 없는 세부 내용은 일반 지식으로 보완할 수 있으나, 그 사실을 명시하세요.
- 답변은 충분히 상세하게 작성하고, 관련 규정·절차·예외사항도 포함하세요.
- 목록이나 항목이 있으면 번호나 불릿으로 구분해서 작성하세요.

[참고 문서]
{context}"""


def build_rag_prompt(query: str, final_chunks: list) -> str:
    """하위 호환용 — system+user 통합 단일 프롬프트 (사용 안 함)"""
    system = build_rag_system_prompt(final_chunks)
    return f"{system}\n\n[질문]\n{query}"


def build_general_system_prompt() -> str:
    return (
        "당신은 회사 AI 어시스턴트입니다. "
        "직원들의 질문에 친절하고 자연스러운 한국어로 답변하세요."
    )
