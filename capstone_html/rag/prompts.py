"""
LLM 프롬프트 빌더 (업그레이드: QueryProfile 기반 맞춤형 지시 생성)
"""
from __future__ import annotations
from typing import List, Optional


def build_rag_system_prompt(final_chunks: list, query: str = "", profile=None) -> str:
    """
    RAG 컨텍스트를 담은 system 메시지 생성.
    profile이 있으면 질문 유형에 맞는 추가 지시를 포함.
    """
    context_blocks = []
    for i, item in enumerate(final_chunks, start=1):
        chunk = item["chunk"]
        meta = f"[문서 {i}] 출처: {chunk.get('source', '')}"
        if chunk.get("clause_title"):
            meta += f" / 조항: {chunk['clause_title']}"
        context_blocks.append(f"{meta}\n{chunk['text']}")
    context = "\n\n".join(context_blocks)

    instructions: List[str] = [
        "당신은 회사 내부 문서를 기반으로 직원들의 질문에 답변하는 AI 어시스턴트입니다.",
        "아래 참고 문서를 최대한 활용하여 친절하고 상세하게 한국어로 답변하세요.",
        "참고 문서에 관련 내용이 있으면 반드시 그 내용을 중심으로 답변하세요.",
        "문서에 없는 세부 내용은 일반 지식으로 보완할 수 있으나, 그 사실을 명시하세요.",
        "답변은 충분히 상세하게 작성하고, 관련 규정·절차·예외사항도 포함하세요.",
        "목록이나 항목이 있으면 번호나 불릿으로 구분해서 작성하세요.",
        "근거 줄이나 문서 라벨(예: 문서 1)은 답에 직접 쓰지 마세요.",
    ]

    if profile is not None:
        if getattr(profile, "summary_requested", False):
            instructions.append("사용자가 요약을 원하면 핵심 내용을 3문장 이내로 정리하세요.")
        if getattr(profile, "procedure_requested", False):
            instructions.append("절차를 묻는 질문이면 번호를 붙여 단계별로 답하세요.")
            instructions.append("시점, 절차, 제한사항이 문서에 있으면 구분해서 쓰고, 없으면 없다고 쓰세요.")
        if getattr(profile, "compare_requested", False):
            instructions.append("비교 질문이면 공통점과 차이점을 항목별로 나눠 답하세요.")
        if getattr(profile, "multi_document_requested", False) or getattr(profile, "synthesis_requested", False):
            instructions.append("여러 문서를 함께 보라는 질문이면 문서별 정보를 종합해서 답하세요.")
        if getattr(profile, "exact_lookup", False):
            instructions.append("특정 항목을 묻는 질문이면 항목별로 분리해서 정확히 답하세요.")

    if query and any(token in query for token in ("구분", "분류", "나눠", "나눠서", "분야별")):
        instructions.append("질문에서 요구한 분류 기준에 맞춰 재구성하되, 해당 분류에 맞는 항목이 없으면 '없음'이라고 쓰세요.")

    if query and any(token in query for token in ("사례", "대표", "항목", "몇 가지", "5가지", "3가지")):
        instructions.append("예시나 사례를 묻는 질문이면 문서에 명시된 항목만 적고, 부족하더라도 억지로 채우지 마세요.")

    instruction_text = "\n".join(f"- {inst}" for inst in instructions)

    return f"""{instruction_text}

[참고 문서]
{context}"""


def build_rag_prompt(query: str, final_chunks: list, profile=None) -> str:
    """하위 호환용 — system+user 통합 단일 프롬프트"""
    system = build_rag_system_prompt(final_chunks, query=query, profile=profile)
    return f"{system}\n\n[질문]\n{query}"


def build_general_system_prompt() -> str:
    return (
        "당신은 회사 AI 어시스턴트입니다. "
        "직원들의 질문에 친절하고 자연스러운 한국어로 답변하세요."
    )
