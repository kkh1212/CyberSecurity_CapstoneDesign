# final_strong_v3

Purpose: stronger MutedRAG diagnostic corpus for measuring whether retrieved malicious context can redirect a RAG answer into a refusal/DoS response when external guardrails are disabled.

Changes from final_strong_v2:
- Keeps one benign document and one attack document per target query.
- Repeats the original business question at the top to preserve BM25 retrieval exposure.
- Removes bracketed labels such as `[Retrieved External Instruction]` that models may treat as inert document text.
- Uses a prompt-continuation style with `Question:`, `Context:`, `New question:`, and `Answer:` markers.
- Includes an explicit refusal-start phrase to make this a strong diagnostic condition, not a perfectly realistic weak attack.
- Harmful trigger questions remain high-level requests only; no procedural harmful instructions are included.

Interpretation note:
This v3 corpus is stronger than the paper's black-box suffix style because it includes an answer-prefix/refusal cue. Use it to validate the pipeline and defense behavior. For paper-faithful reproduction, also keep a no-answer variant later.
