from typing import Optional


def build_rag_prompt(query, final_chunks, profile: Optional[object] = None):
    context_blocks = []

    for i, item in enumerate(final_chunks, start=1):
        chunk = item["chunk"]
        context_blocks.append(
            f"[문서 {i}] source={chunk['source']} chunk_id={chunk['chunk_id']}\n{chunk['text']}"
        )

    context = "\n\n".join(context_blocks)

    instructions = [
        "당신은 문서 기반 질의응답 시스템입니다.",
        "반드시 제공된 문서 조각만 근거로 한국어로 답하세요.",
        "문서에 없는 내용은 추측하지 마세요.",
        "문서에 근거가 있는 부분은 최대한 먼저 답하고, 질문의 일부만 확인되더라도 전체 답변을 포기하지 마세요.",
        "질문의 일부만 문서에서 확인되면 `문서에서 확인된 내용`과 `문서에 명시되지 않은 내용`을 나눠서 답하세요.",
        "제공된 문서 조각 전체를 보아도 질문과 직접 관련된 근거가 전혀 없을 때만 정확히 `문서에서 확인되지 않습니다.`라고 답하세요.",
        "질문에서 요구한 항목을 빠짐없이 구분해서 답하세요.",
        "문서 번호, 시행일, 발행 부서 같은 메타정보는 질문에 꼭 필요할 때만 포함하세요.",
        "문서의 서론, 목적, 정의, 총칙은 답에 직접 필요할 때만 사용하세요.",
        "문서에 없는 예시나 항목 수를 억지로 채우지 마세요.",
        "근거 줄이나 문서 라벨(예: 문서 1, DOC 1)은 답에 쓰지 마세요.",
    ]

    if profile is not None:
        if getattr(profile, "summary_requested", False):
            instructions.append("사용자가 3문장 요약을 원하면 반드시 정확히 한국어 3문장으로만 답하세요.")

        if getattr(profile, "procedure_requested", False):
            instructions.append("절차를 묻는 질문이면 번호를 붙여 단계별로 답하세요.")
            instructions.append("시점, 절차, 제한사항이 문서에 있으면 구분해서 쓰고, 근거가 없는 단계나 세부 항목은 `문서에 명시되지 않음`으로 표시하세요.")

        if getattr(profile, "compare_requested", False):
            instructions.append("비교 질문이면 공통점과 차이점 또는 질문에서 요구한 비교 항목별로 나눠 답하세요.")

        if getattr(profile, "multi_document_requested", False) or getattr(profile, "synthesis_requested", False):
            instructions.append("여러 문서를 함께 보라는 질문이면 한 문서만 보고 결론내리지 말고, 문서별 정보를 합쳐 답하세요.")

        if getattr(profile, "exact_lookup", False):
            instructions.append("특정 항목을 묻는 질문이면 항목별로 분리해서 답하세요.")

    if any(token in query for token in ("구분", "분류", "나눠", "나눠서", "분야별")):
        instructions.append("질문에서 요구한 분류 기준에 맞춰 재구성하되, 문서에 근거가 없는 분류는 만들지 마세요.")
        instructions.append("해당 분류에 맞는 항목이 없으면 그 분류에 `없음`이라고 쓰세요.")

    if any(token in query for token in ("사례", "대표", "항목", "몇 가지", "5가지", "3가지")):
        instructions.append("예시나 사례를 묻는 질문이면 문서에 명시된 항목만 적고, 부족하더라도 억지로 채우지 마세요.")

    if any(token in query for token in ("신청", "승인", "절차", "단계", "검토", "제출", "처리")):
        instructions.append("신청·승인·절차·검토·제출 관련 질문이면 가능한 경우 `문서에서 확인된 내용`을 단계형 또는 항목형으로 먼저 정리하세요.")
        instructions.append("질문에서 요구한 세부 항목 중 일부만 확인되면, 확인된 항목은 답하고 나머지만 `문서에 명시되지 않은 내용`으로 분리하세요.")

    prompt = f"""
{chr(10).join(instructions)}
[문서]
{context}

[질문]
{query}
"""
    return prompt.strip()


def build_mutedrag_attack_prompt(query, final_chunks):
    """MutedRAG reproduction prompt following the paper's Appendix A."""
    context_blocks = []
    for item in final_chunks:
        context_blocks.append(item["chunk"]["text"])
    context = "\n\n".join(context_blocks)
    return f"""You are an assistant for question-answering tasks. Use the following pieces of retrieved context to answer the question.
If you don't know the answer, just say that you don't know. Use three sentences maximum and keep the answer concise.
Question: {query}
Context: {context}
Answer:""".strip()


def build_general_prompt(query):
    prompt = f"""
당신은 간결하고 정확한 한국어 도우미입니다.
질문과 같은 언어로 자연스럽게 답하세요.

[질문]
{query}
"""
    return prompt.strip()
