def build_rag_prompt(query, final_chunks):
    context_blocks = []

    for i, item in enumerate(final_chunks, start=1):
        chunk = item["chunk"]
        context_blocks.append(
            f"[문서 {i}] source={chunk['source']} chunk_id={chunk['chunk_id']}\n{chunk['text']}"
        )

    context = "\n\n".join(context_blocks)

    summary_instruction = ""
    if "3문장" in query:
        summary_instruction = "요약 답변은 반드시 정확히 3문장으로 작성하세요.\n"

    prompt = f"""
당신은 문서 기반 질의응답 어시스턴트입니다.
반드시 아래 문서 조각만 근거로 답변하세요.
문서에 없는 내용은 추측하지 말고 "문서에서 확인되지 않습니다."라고 답하세요.
숫자, 금액, 날짜, 코드, 인원, 사람 이름을 답할 때는 문서에 나온 값 그대로만 사용하세요.
질문이 여러 항목을 각각 요구하면 항목별로 나눠서 모두 답하세요.
질문이 요약형이면 가장 관련성이 높은 하나의 문서만 기준으로 핵심만 요약하고, 다른 문서 내용은 섞지 마세요.
같은 내용을 반복하거나 "다시 요약하면" 같은 중복 문장을 쓰지 마세요.
{summary_instruction}답변 마지막에는 "근거:" 한 줄로 사용한 핵심 근거를 짧게 덧붙이세요.

[문서]
{context}

[질문]
{query}
"""
    return prompt.strip()


def build_general_prompt(query):
    prompt = f"""
당신은 친절하고 간결한 AI 어시스턴트입니다.
사용자의 질문에 자연스럽고 명확하게 답변하세요.

[질문]
{query}
"""
    return prompt.strip()
