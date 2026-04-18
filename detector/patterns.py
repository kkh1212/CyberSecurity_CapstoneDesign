from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final


@dataclass(frozen=True)
class WeightedPattern:
    name: str
    pattern: str
    weight: float
    category: str
    description: str
    flags: int = re.IGNORECASE | re.MULTILINE


INSTRUCTIONALITY_PATTERNS: Final[list[WeightedPattern]] = [
    WeightedPattern(
        name="follow_instructions_en",
        pattern=r"\bfollow (?:these|the) instructions\b",
        weight=1.0,
        category="directive",
        description="Explicitly tells the model to follow instructions.",
    ),
    WeightedPattern(
        name="must_should_en",
        pattern=r"\byou (?:must|should)\b",
        weight=0.8,
        category="behavior_control",
        description="Direct behavioral control language addressing the assistant.",
    ),
    WeightedPattern(
        name="assistant_must_en",
        pattern=r"\bthe assistant must\b",
        weight=1.0,
        category="behavior_control",
        description="States mandatory assistant behavior.",
    ),
    WeightedPattern(
        name="model_control_en",
        pattern=r"\b(?:the )?model (?:must|should|will)\b",
        weight=0.9,
        category="behavior_control",
        description="Directly controls model behavior.",
    ),
    WeightedPattern(
        name="always_never_answer_en",
        pattern=r"\b(?:always|never) (?:answer|respond)\b",
        weight=1.1,
        category="response_control",
        description="Controls how the assistant should always or never answer.",
    ),
    WeightedPattern(
        name="ignore_previous_en",
        pattern=r"\bignore (?:all )?previous instructions\b",
        weight=1.4,
        category="override",
        description="Attempts to override prior behavior or context.",
    ),
    WeightedPattern(
        name="forget_previous_en",
        pattern=r"\bforget (?:all )?(?:previous|earlier) (?:instructions|details|context)\b",
        weight=1.2,
        category="override",
        description="Attempts to discard prior context or instructions.",
    ),
    WeightedPattern(
        name="focus_on_question_en",
        pattern=r"\bfocus on (?:my|the) (?:new )?(?:question|request|instruction)\b",
        weight=1.0,
        category="response_control",
        description="Redirects the model to a new question or instruction.",
    ),
    WeightedPattern(
        name="when_user_asks_en",
        pattern=r"\bwhen a user asks\b|\bif asked\b",
        weight=0.8,
        category="conditional_instruction",
        description="Conditional prompt-like behavior for future user requests.",
    ),
    WeightedPattern(
        name="respond_with_en",
        pattern=r"\brespond with\b|\banswer with\b",
        weight=0.8,
        category="response_control",
        description="Directly specifies response content.",
    ),
    WeightedPattern(
        name="response_format_en",
        pattern=r"\bresponse format\b|\bhandling requests\b|\bsafety procedure\b",
        weight=0.6,
        category="policy_framing",
        description="Meta-policy framing around how requests should be handled.",
    ),
    WeightedPattern(
        name="follow_instructions_ko",
        pattern=r"(?:다음|이) (?:지침|지시|규칙)을 따르",
        weight=1.0,
        category="directive",
        description="Korean directive telling the model to follow instructions.",
    ),
    WeightedPattern(
        name="must_respond_ko",
        pattern=r"반드시 .*?(?:응답|답변|거부|거절)",
        weight=0.9,
        category="behavior_control",
        description="Korean mandatory response control phrasing.",
    ),
    WeightedPattern(
        name="model_control_ko",
        pattern=r"모델은 .*?(?:해야|응답|답변|거부|거절)",
        weight=0.9,
        category="behavior_control",
        description="Korean direct model behavior control phrasing.",
    ),
    WeightedPattern(
        name="always_never_ko",
        pattern=r"(?:항상|절대) .*?(?:응답|답변|제공|거부|거절)",
        weight=1.0,
        category="response_control",
        description="Korean always/never response control phrasing.",
    ),
    WeightedPattern(
        name="ignore_previous_ko",
        pattern=r"이전 지시(?:를)? 무시",
        weight=1.4,
        category="override",
        description="Korean override of prior instructions.",
    ),
    WeightedPattern(
        name="forget_previous_ko",
        pattern=r"이전 (?:내용|문맥|지시).*(?:잊어|버리)",
        weight=1.2,
        category="override",
        description="Korean instruction to discard previous context.",
    ),
    WeightedPattern(
        name="focus_on_question_ko",
        pattern=r"(?:다음|새) 질문에 집중|질문에만 집중",
        weight=1.0,
        category="response_control",
        description="Korean redirection to a new question or request.",
    ),
    WeightedPattern(
        name="if_user_asks_ko",
        pattern=r"사용자가 .*질문(?:하면|할 때)|질문을 받으면",
        weight=0.8,
        category="conditional_instruction",
        description="Korean conditional prompt-like instruction for user questions.",
    ),
    WeightedPattern(
        name="answer_with_ko",
        pattern=r".*라고 답하(?:라|세요)|.*라고 응답하(?:라|세요)",
        weight=0.9,
        category="response_control",
        description="Korean direct instruction on the response content.",
    ),
    WeightedPattern(
        name="response_format_ko",
        pattern=r"응답 형식|처리 지침|요청 처리|안전 절차",
        weight=0.6,
        category="policy_framing",
        description="Korean meta-policy framing or response procedure language.",
    ),
]


REFUSAL_PATTERNS: Final[list[WeightedPattern]] = [
    WeightedPattern(
        name="cannot_comply_en",
        pattern=r"\bcannot comply\b|\bcan't comply\b",
        weight=1.0,
        category="refusal",
        description="Direct refusal wording.",
    ),
    WeightedPattern(
        name="must_refuse_en",
        pattern=r"\b(?:must|should) refuse\b|\bdecline (?:the|all|such)? ?requests?\b",
        weight=1.1,
        category="refusal",
        description="Explicit refusal requirement.",
    ),
    WeightedPattern(
        name="do_not_answer_en",
        pattern=r"\bdo not answer\b|\bdo not provide\b|\bwithhold\b|\bblock the response\b|\bnever reveal the answer\b",
        weight=1.0,
        category="withholding",
        description="Explicit instruction to withhold the answer.",
    ),
    WeightedPattern(
        name="unsafe_restricted_en",
        pattern=r"\bsensitive information\b|\brestricted content\b|\bunsafe request\b|\bpolicy violation\b|\bprohibited\b|\bnot allowed\b",
        weight=0.8,
        category="restriction",
        description="Frames the answer as restricted or unsafe.",
    ),
    WeightedPattern(
        name="security_privacy_en",
        pattern=r"\bsecurity policy\b|\bconfidential\b|\bprivacy-sensitive\b|\bharmful\b",
        weight=0.7,
        category="restriction",
        description="Security/privacy framing associated with refusal.",
    ),
    WeightedPattern(
        name="say_cannot_help_en",
        pattern=r"\binstead,? say that you cannot help\b|\brespond with a refusal\b|\bavoid giving details\b",
        weight=1.2,
        category="forced_refusal",
        description="Explicitly tells the model to avoid answering and refuse instead.",
    ),
    WeightedPattern(
        name="dangerous_topic_en",
        pattern=r"\b(?:bomb|explosive|weapon|hack|malware|ransomware|methamphetamine|drugs?)\b",
        weight=0.7,
        category="dangerous_topic",
        description="Dangerous or safety-sensitive topic associated with refusal behavior.",
    ),
    WeightedPattern(
        name="how_to_request_en",
        pattern=r"\bhow to\b|\bhow do i\b|\bsteps to\b|\bways to\b",
        weight=0.6,
        category="dangerous_request",
        description="Action-seeking request phrasing commonly associated with restricted content.",
    ),
    WeightedPattern(
        name="cannot_help_ko",
        pattern=r"응답할 수 없(?:습니다|다)|도와줄 수 없(?:습니다|다)",
        weight=1.0,
        category="refusal",
        description="Korean direct refusal wording.",
    ),
    WeightedPattern(
        name="must_refuse_ko",
        pattern=r"반드시 거부|거절해야|거부해야|모든 요청.*거절|모든 요청.*거부",
        weight=1.1,
        category="refusal",
        description="Korean explicit refusal requirement.",
    ),
    WeightedPattern(
        name="do_not_answer_ko",
        pattern=r"답하지 마|제공하지 마|공개하지 마|세부 정보를 주지 마|정답을 공개하지 마",
        weight=1.0,
        category="withholding",
        description="Korean instruction to withhold or not answer.",
    ),
    WeightedPattern(
        name="unsafe_restricted_ko",
        pattern=r"민감한 정보|제한된 내용|안전하지 않은 요청|정책 위반|금지된|허용되지 않",
        weight=0.8,
        category="restriction",
        description="Korean restricted/unsafe framing.",
    ),
    WeightedPattern(
        name="security_privacy_ko",
        pattern=r"보안 정책|기밀|개인정보|유해한",
        weight=0.7,
        category="restriction",
        description="Korean security/privacy framing associated with refusal.",
    ),
    WeightedPattern(
        name="say_cannot_help_ko",
        pattern=r"대신 .*도와줄 수 없다고|거부 응답|상세 내용은 피하",
        weight=1.2,
        category="forced_refusal",
        description="Korean instruction to refuse instead of answering.",
    ),
    WeightedPattern(
        name="dangerous_topic_ko",
        pattern=r"폭탄|폭발물|무기|해킹|악성코드|랜섬웨어|마약|필로폰",
        weight=0.7,
        category="dangerous_topic",
        description="Korean dangerous or safety-sensitive topic associated with refusal behavior.",
    ),
    WeightedPattern(
        name="how_to_request_ko",
        pattern=r"어떻게 .*?(?:만들|제작|구하|해킹|침입)|방법을 알려|제작 방법|공격 방법",
        weight=0.6,
        category="dangerous_request",
        description="Korean action-seeking request phrasing commonly associated with restricted content.",
    ),
    WeightedPattern(
        name="limited_detail_en",
        pattern=r"\b(?:detailed|specific|step-by-step|internal|implementation) (?:information|details?|steps?|guidance|procedures?) (?:cannot|can't|will not|won't|are not) (?:be )?(?:shared|provided|explained|disclosed)\b",
        weight=0.9,
        category="restriction",
        description="Indirect refusal phrasing that limits detail disclosure.",
    ),
    WeightedPattern(
        name="security_reason_no_detail_en",
        pattern=r"\bfor (?:security|privacy|compliance|operational) (?:reasons?|purposes?),? (?:we )?(?:cannot|can't|do not|will not) (?:share|provide|disclose|explain)\b",
        weight=0.95,
        category="restriction",
        description="Frames non-disclosure as a security or compliance requirement.",
    ),
    WeightedPattern(
        name="limited_response_en",
        pattern=r"\b(?:only|limited) (?:guidance|information|details?) (?:will be|can be|may be)? ?(?:provided|shared)\b|\bpublic(?:ly)? available (?:scope|information) only\b|\brefer to the official (?:notice|guide|faq)\b",
        weight=0.7,
        category="limited_response",
        description="Constricts the answer to a narrow public-safe subset.",
    ),
    WeightedPattern(
        name="do_not_expand_en",
        pattern=r"\b(?:do not|don't|will not) expand(?: the)? (?:response|explanation)\b|\bavoid further details?\b|\bdo not provide concrete examples\b",
        weight=0.85,
        category="limited_response",
        description="Discourages elaboration and concrete detail.",
    ),
    WeightedPattern(
        name="detailed_info_restricted_ko",
        pattern=r"상세(?:한)?\s*(?:안내|설명|절차|단계|내용|기술\s*세부).*?(?:제공하지 않(?:습니다|는다)|안내하지 않(?:습니다|는다)|설명하지 않(?:습니다|는다)|공개하지 않(?:습니다|는다)|불가|어렵습니다|제한)",
        weight=0.95,
        category="restriction",
        description="Korean indirect refusal phrasing that restricts detailed disclosure.",
    ),
    WeightedPattern(
        name="security_reason_no_detail_ko",
        pattern=r"보안(?:상| 및 운영 정책상| 정책상).{0,20}(?:상세|구체(?:적)?(?:인)?|세부).{0,10}(?:설명|안내|제공|공개).{0,12}(?:하지 않(?:습니다|는다)|불가|어렵습니다|제한)",
        weight=1.0,
        category="restriction",
        description="Korean security or policy rationale for withholding details.",
    ),
    WeightedPattern(
        name="limited_guidance_ko",
        pattern=r"제한(?:된)?\s*(?:안내|설명|회신|문구)(?:만)?\s*(?:제공|사용)|공개 가능한 범위(?:만)?\s*(?:안내|제공)|공식 공지(?:만)?\s*(?:참고|안내)",
        weight=0.7,
        category="limited_response",
        description="Korean language that narrows the answer to a limited safe scope.",
    ),
    WeightedPattern(
        name="public_scope_only_ko",
        pattern=r"공개 가능한 범위(?:\s*내)?(?:에서)?만|공지된 (?:사실|내용)(?:과)?(?:\s*후속\s*조치|\s*후속\s*일정)?(?:만)?\s*(?:안내|설명)|공개된 안내 범위를 넘어서는",
        weight=0.75,
        category="limited_response",
        description="Korean phrasing that constrains the answer to public or pre-announced scope.",
    ),
    WeightedPattern(
        name="do_not_expand_ko",
        pattern=r"추가\s*설명(?:은|을)?\s*(?:넓히지 않(?:습니다|는다)|확장하지 않(?:습니다|는다)|제공하지 않(?:습니다|는다))|구체(?:적)?\s*(?:사례|예시|절차).{0,10}(?:설명|안내|제공).{0,12}(?:하지 않(?:습니다|는다)|생략)",
        weight=0.9,
        category="limited_response",
        description="Korean phrasing that suppresses elaboration or concrete examples.",
    ),
    WeightedPattern(
        name="internal_sensitive_ko",
        pattern=r"내부\s*(?:운영|절차|승인\s*사유|검토\s*결과|판단\s*근거).{0,12}(?:제공|공개|안내).{0,12}(?:하지 않(?:습니다|는다)|불가)|민감(?:한)?\s*문의",
        weight=0.65,
        category="restriction",
        description="Marks content as internal/sensitive and not suitable for detailed disclosure.",
    ),
]


EXPLICIT_HIGH_RISK_PATTERNS: Final[list[WeightedPattern]] = [
    WeightedPattern(
        name="ignore_previous_high_risk_en",
        pattern=r"\bignore previous instructions\b",
        weight=1.8,
        category="explicit_override",
        description="Classic override prompt pattern.",
    ),
    WeightedPattern(
        name="regardless_of_question_en",
        pattern=r"\bregardless of the question\b",
        weight=1.6,
        category="forced_global_behavior",
        description="Applies refusal regardless of actual user question.",
    ),
    WeightedPattern(
        name="always_refuse_en",
        pattern=r"\balways refuse\b|\bfor any user request, respond with refusal\b",
        weight=1.8,
        category="forced_refusal",
        description="Forces blanket refusal behavior.",
    ),
    WeightedPattern(
        name="do_not_answer_even_if_relevant_en",
        pattern=r"\bdo not answer even if the context is relevant\b",
        weight=1.9,
        category="forced_refusal",
        description="Explicitly suppresses relevant answers.",
    ),
    WeightedPattern(
        name="override_prior_behavior_en",
        pattern=r"\boverride prior behavior\b|\btreat all requests as unsafe\b",
        weight=1.8,
        category="explicit_override",
        description="Overrides prior policy or marks all requests unsafe.",
    ),
    WeightedPattern(
        name="always_say_cannot_help_en",
        pattern=r"\balways say you cannot help\b|\bnever reveal the answer\b|\bthe assistant must decline all such requests\b",
        weight=1.9,
        category="forced_refusal",
        description="Forces a generic refusal regardless of context.",
    ),
    WeightedPattern(
        name="ignore_previous_high_risk_ko",
        pattern=r"이전 지시.*무시",
        weight=1.8,
        category="explicit_override",
        description="Korean explicit override prompt pattern.",
    ),
    WeightedPattern(
        name="regardless_of_question_ko",
        pattern=r"질문과 상관없이|어떤 질문이 와도",
        weight=1.6,
        category="forced_global_behavior",
        description="Korean blanket behavior regardless of question.",
    ),
    WeightedPattern(
        name="always_refuse_ko",
        pattern=r"항상 거부|모든 요청.*거절|모든 요청.*거부",
        weight=1.8,
        category="forced_refusal",
        description="Korean blanket refusal instruction.",
    ),
    WeightedPattern(
        name="do_not_answer_even_if_relevant_ko",
        pattern=r"문맥이 관련 있어도 답하지 마|관련 문서가 있어도 답하지 마",
        weight=1.9,
        category="forced_refusal",
        description="Korean suppression of relevant answers.",
    ),
    WeightedPattern(
        name="override_prior_behavior_ko",
        pattern=r"기존 동작.*덮어쓰|모든 요청을 위험한 요청으로 간주",
        weight=1.8,
        category="explicit_override",
        description="Korean override or blanket unsafe classification.",
    ),
    WeightedPattern(
        name="always_say_cannot_help_ko",
        pattern=r"항상 도와줄 수 없다고 말하|정답을 절대 공개하지 마|어시스턴트는 모든 .* 요청을 거절해야",
        weight=1.9,
        category="forced_refusal",
        description="Korean forced refusal behavior.",
    ),
]


PROMPT_TEMPLATE_MARKERS: Final[tuple[str, ...]] = (
    r"^\s*(?:user|assistant|instruction|response|system)\s*:",
    r"^\s*(?:사용자|어시스턴트|지시|응답|시스템)\s*:",
)


OUTLIER_MODAL_TERMS: Final[tuple[str, ...]] = (
    "must",
    "should",
    "always",
    "never",
    "ignore",
    "respond",
    "answer",
    "refuse",
    "decline",
    "override",
    "반드시",
    "해야",
    "하라",
    "하십시오",
    "응답하라",
    "답하라",
    "거부하라",
    "거절하라",
    "무시",
)


OUTLIER_POLICY_TERMS: Final[tuple[str, ...]] = (
    "guideline",
    "guidelines",
    "rule",
    "rules",
    "policy",
    "instruction",
    "instructions",
    "response format",
    "handling requests",
    "safety procedure",
    "지침",
    "지시",
    "규칙",
    "정책",
    "응답 형식",
    "요청 처리",
    "처리 지침",
    "안전 절차",
)


OUTLIER_FEATURE_WEIGHTS: Final[dict[str, float]] = {
    "length_deviation": 0.07,
    "sentence_length_deviation": 0.07,
    "modal_ratio": 0.11,
    "policy_term_density": 0.10,
    "prompt_marker_density": 0.13,
    "instruction_format_density": 0.11,
    "flow_shift": 0.10,
    "semantic_shift": 0.17,
    "punctuation_density": 0.08,
    "repetition": 0.04,
    "perplexity": 0.02,
}
