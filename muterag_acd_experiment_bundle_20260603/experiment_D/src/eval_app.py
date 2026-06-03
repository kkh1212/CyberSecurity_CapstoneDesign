from src.evaluation_cases import EVAL_CASES
from src.ollama_client import ask_ollama
from src.prompts import build_rag_prompt
from src.query_app import collect_structured_candidates, select_context_chunks, validate_index_ready
from src.query_analysis import build_query_profile
from src.reranker import rerank_results
from src.retrievers import hybrid_search
from src.structured_qa import build_structured_answer


def evaluate_case(case):
    query = case["query"]
    profile = build_query_profile(query)
    search_output = hybrid_search(query)
    merged_results = search_output["merged_results"]
    final_chunks = rerank_results(query, merged_results)

    top_source = final_chunks[0]["chunk"]["source"] if final_chunks else ""
    structured_candidates = collect_structured_candidates(profile, final_chunks, merged_results)
    structured_result = build_structured_answer(query, structured_candidates)

    if structured_result:
        answer = structured_result["answer"]
        sources = [structured_result["chunk"]["source"]]
    else:
        context_chunks = select_context_chunks(profile, final_chunks, merged_results)
        prompt = build_rag_prompt(query, context_chunks)
        response = ask_ollama(prompt)
        answer = response.get("response", "")
        sources = [item["chunk"]["source"] for item in context_chunks]

    expected_source = case.get("expected_source", "")
    expected_substrings = case.get("expected_substrings", [])

    source_ok = expected_source in sources or expected_source == top_source
    substrings_ok = all(substring in answer for substring in expected_substrings)
    passed = source_ok and substrings_ok

    print(f"[{'PASS' if passed else 'FAIL'}] {case['name']}")
    print(f"query: {query}")
    print(f"top_source: {top_source}")
    print(f"sources: {sources}")
    print(f"answer: {answer}")
    print()
    return passed


def main():
    validate_index_ready()
    passed = 0
    for case in EVAL_CASES:
        if evaluate_case(case):
            passed += 1
    print(f"passed {passed}/{len(EVAL_CASES)} cases")


if __name__ == "__main__":
    main()
