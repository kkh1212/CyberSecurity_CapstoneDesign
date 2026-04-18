from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

from detector import detect_runtime_risk, sanitize_runtime_context


def runtime_detector_enabled() -> bool:
    raw = os.getenv("RUNTIME_DETECTOR_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_runtime_user_context() -> dict[str, str]:
    return {
        "role": os.getenv("RUNTIME_USER_ROLE", "").strip(),
        "dept": os.getenv("RUNTIME_USER_DEPT", "").strip(),
        "rank": os.getenv("RUNTIME_USER_RANK", "").strip(),
    }


def apply_runtime_guard(
    query: str,
    context_chunks: Sequence[Mapping[str, Any]],
    *,
    user_context: Mapping[str, Any] | None = None,
    policy_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not runtime_detector_enabled():
        return {
            "enabled": False,
            "runtime_result": {
                "risk_level": "low",
                "recommended_action": "allow",
                "triggered_rules": [],
                "explanation": "Runtime detector disabled.",
            },
            "sanitization": {
                "action": "allow",
                "sanitized_chunks": list(context_chunks),
                "removed_chunks": [],
                "explanation": "Runtime detector disabled.",
            },
        }

    resolved_user_context = dict(load_runtime_user_context())
    if user_context:
        resolved_user_context.update({key: value for key, value in user_context.items() if value is not None})

    runtime_result = detect_runtime_risk(
        query,
        context_chunks,
        user_context=resolved_user_context,
        policy_context=policy_context,
    )
    sanitization = sanitize_runtime_context(context_chunks, runtime_result)

    return {
        "enabled": True,
        "runtime_result": runtime_result,
        "sanitization": sanitization,
    }


def build_runtime_fallback_message(runtime_result: Mapping[str, Any]) -> str:
    risk_level = str(runtime_result.get("risk_level", "low")).lower()
    if risk_level == "critical":
        return "요청과 검색된 문맥의 결합 위험이 높아 응답을 중단합니다."
    if risk_level == "high":
        return "현재 검색된 문맥은 보안상 검토가 필요하여 사용하지 않습니다. 다른 안전한 문맥으로 다시 조회가 필요합니다."
    return ""


def summarize_runtime_guard(guard_result: Mapping[str, Any]) -> dict[str, Any]:
    runtime_result = guard_result.get("runtime_result", {})
    sanitization = guard_result.get("sanitization", {})
    return {
        "runtime_risk_level": runtime_result.get("risk_level", "low"),
        "runtime_action": runtime_result.get("recommended_action", "allow"),
        "runtime_adjusted_risk": runtime_result.get("adjusted_risk", 0.0),
        "runtime_triggered_rules": runtime_result.get("triggered_rules", []),
        "removed_chunk_count": len(sanitization.get("removed_chunks", [])),
    }
