from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

from detector import detect_runtime_risk, sanitize_runtime_context
from src.config import RUNTIME_DETECTOR_PROFILE, RUNTIME_SANITIZER_ENABLED


def runtime_detector_enabled() -> bool:
    raw = os.getenv("RUNTIME_DETECTOR_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def runtime_detector_profile() -> str:
    return (RUNTIME_DETECTOR_PROFILE or "balanced").strip().lower()


def runtime_sanitizer_enabled() -> bool:
    if not runtime_detector_enabled():
        return False
    raw = os.getenv("RUNTIME_SANITIZER_ENABLED")
    if raw is None:
        return bool(RUNTIME_SANITIZER_ENABLED)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def runtime_configuration_warning() -> str:
    raw = os.getenv("RUNTIME_SANITIZER_ENABLED")
    if raw and raw.strip().lower() in {"1", "true", "yes", "on"} and not runtime_detector_enabled():
        return "Runtime sanitizer was requested, but the runtime detector is disabled. Falling back to baseline RAG mode."
    return ""


def runtime_security_mode() -> str:
    if not runtime_detector_enabled():
        return "baseline_rag"
    if runtime_sanitizer_enabled():
        return "detect_and_sanitize"
    return "detect_only"


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
    prior_requery_attempts: int = 0,
) -> dict[str, Any]:
    security_mode = runtime_security_mode()
    config_warning = runtime_configuration_warning()
    if not runtime_detector_enabled():
        passthrough_chunks = list(context_chunks)
        return {
            "enabled": False,
            "runtime_result": {
                "risk_level": "not_run",
                "recommended_action": "baseline_allow",
                "triggered_rules": [],
                "explanation": "Runtime detector disabled.",
                "profile_used": runtime_detector_profile(),
            },
            "sanitization": {
                "action": "baseline_allow",
                "sanitized_chunks": passthrough_chunks,
                "sanitized_context": passthrough_chunks,
                "removed_chunks": [],
                "removed_chunk_ids": [],
                "excluded_sources": [],
                "excluded_chunk_ids": [],
                "requery_required": False,
                "requery_attempt_count": prior_requery_attempts,
                "triggered_sanitization_rules": [],
                "chunk_rationale": [],
                "replace_candidates": [],
                "explanation": "Runtime detector disabled.",
            },
            "security_mode": security_mode,
            "runtime_detector_enabled": False,
            "runtime_sanitizer_enabled": False,
            "configuration_warning": config_warning,
        }

    resolved_user_context = dict(load_runtime_user_context())
    if user_context:
        resolved_user_context.update({key: value for key, value in user_context.items() if value is not None})

    runtime_result = detect_runtime_risk(
        query,
        context_chunks,
        user_context=resolved_user_context,
        policy_context=policy_context,
        profile=runtime_detector_profile(),
    )
    if runtime_sanitizer_enabled():
        sanitization = sanitize_runtime_context(
            context_chunks,
            runtime_result,
            query=query,
            user_context=resolved_user_context,
            policy_profile=runtime_detector_profile(),
            prior_requery_attempts=prior_requery_attempts,
        )
    else:
        passthrough_chunks = list(context_chunks)
        sanitization = {
            "action": "detect_only_allow",
            "sanitized_chunks": passthrough_chunks,
            "sanitized_context": passthrough_chunks,
            "removed_chunks": [],
            "removed_chunk_ids": [],
            "excluded_sources": [],
            "excluded_chunk_ids": [],
            "requery_required": False,
            "requery_attempt_count": prior_requery_attempts,
            "triggered_sanitization_rules": ["detect_only_observe"],
            "chunk_rationale": [],
            "replace_candidates": [],
            "explanation": "Runtime detector executed in observe-only mode. Sanitization actions were not applied.",
            "exclusion_strategy": "none",
            "requery_failure_reason": "",
            "remove_failure_reason": "",
        }

    return {
        "enabled": True,
        "runtime_result": runtime_result,
        "sanitization": sanitization,
        "security_mode": security_mode,
        "runtime_detector_enabled": True,
        "runtime_sanitizer_enabled": runtime_sanitizer_enabled(),
        "configuration_warning": config_warning,
    }


def build_runtime_fallback_message(runtime_result: Mapping[str, Any]) -> str:
    risk_level = str(runtime_result.get("risk_level", "low")).lower()
    if risk_level == "critical":
        return "\uc694\uccad\uacfc \uac80\uc0c9\ub41c \ubb38\ub9e5\uc758 \uacb0\ud569 \uc704\ud5d8\uc774 \ub192\uc544 \ud604\uc7ac \uc751\ub2f5\uc744 \ucc28\ub2e8\ud569\ub2c8\ub2e4."
    if risk_level == "high":
        return "\ud604\uc7ac \uac80\uc0c9\ub41c \ubb38\ub9e5\uc740 \ubcf4\uc548 \uac80\ud1a0\uac00 \ud544\uc694\ud574 \uadf8\ub300\ub85c \uc0ac\uc6a9\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4. \ub354 \uc548\uc804\ud55c \ubb38\ub9e5\uc73c\ub85c \ub2e4\uc2dc \uac80\uc0c9\ud558\uac70\ub098 \uc81c\ud55c\ub41c \uc548\ub0b4\ub9cc \uc81c\uacf5\ud574\uc57c \ud569\ub2c8\ub2e4."
    return ""


def summarize_runtime_guard(guard_result: Mapping[str, Any]) -> dict[str, Any]:
    runtime_result = guard_result.get("runtime_result", {})
    sanitization = guard_result.get("sanitization", {})
    return {
        "security_mode": guard_result.get("security_mode", runtime_security_mode()),
        "runtime_detector_enabled": guard_result.get("runtime_detector_enabled", runtime_detector_enabled()),
        "runtime_sanitizer_enabled": guard_result.get("runtime_sanitizer_enabled", runtime_sanitizer_enabled()),
        "runtime_configuration_warning": guard_result.get("configuration_warning", ""),
        "runtime_risk_level": runtime_result.get("risk_level", "low"),
        "runtime_detector_action": runtime_result.get("recommended_action", "allow"),
        "runtime_action": sanitization.get("action", runtime_result.get("recommended_action", "allow")),
        "runtime_adjusted_risk": runtime_result.get("adjusted_risk", 0.0),
        "runtime_triggered_rules": runtime_result.get("triggered_rules", []),
        "runtime_profile": runtime_result.get("profile_used", runtime_detector_profile()),
        "removed_chunk_count": len(sanitization.get("removed_chunks", [])),
        "runtime_removed_chunk_ids": sanitization.get("removed_chunk_ids", []),
        "runtime_requery_chunk_ids": sanitization.get("excluded_chunk_ids", sanitization.get("exclusion_filters", {}).get("chunk_ids", [])),
        "runtime_requery_sources": sanitization.get("excluded_sources", sanitization.get("exclusion_filters", {}).get("sources", [])),
        "runtime_sanitization_rules": sanitization.get("triggered_sanitization_rules", []),
        "runtime_sanitization_explanation": sanitization.get("explanation", ""),
        "runtime_exclusion_strategy": sanitization.get("exclusion_strategy", ""),
        "runtime_requery_failure_reason": sanitization.get("requery_failure_reason", ""),
        "runtime_remove_failure_reason": sanitization.get("remove_failure_reason", ""),
    }
