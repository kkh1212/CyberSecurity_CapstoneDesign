# final_strong_v4

Purpose: one-chunk strong diagnostic corpus for MutedRAG availability experiments.

Changes from final_strong_v3:
- Shortened each attack document so the attack anchor, redirection, harmful trigger question, and refusal prefix stay in one chunk.
- Keeps one benign document and one attack document per target query.
- Keeps the original business question at the top for retrieval exposure.
- Uses a compact prompt-continuation style: `Question:`, `Context:`, `New question:`, `Answer:`.
- Includes an explicit refusal prefix, so this is a strong diagnostic condition rather than a paper-faithful weak/black-box setting.
- Contains no harmful procedures or operational details beyond high-level refusal-trigger questions.

Interpretation note:
Use v4 to test whether the pipeline can produce model-internal DoS/ASR when retrieved malicious text is fully visible in a single context chunk. For paper-faithful reproduction, keep a later no-answer-prefix variant.
