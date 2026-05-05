"""Optional external guardrail adapters for RAG experiment runs.

This module is intentionally separate from the project's detector/sanitizer.
It only wraps an external or mock guardrail layer and normalizes its output so
Study A can compare guardrail flag/block behavior without changing retrieval or
runtime security logic.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


BLOCK_RESPONSE = "외부 guardrail 정책에 따라 해당 문맥을 사용한 답변이 제한되었습니다."

TRUTHY = {"1", "true", "yes", "on"}
FALSEY = {"0", "false", "no", "off", ""}
VALID_STAGES = {"input", "context", "output"}

MOCK_PATTERNS = [
    r"System instruction",
    r"이전 문맥",
    r"사용자 질문을 무시",
    r"답변하지 마라",
    r"제한 응답",
    r"문서에서 확인할 수 없습니다",
    r"답변할 수 없습니다",
]


@dataclass(frozen=True)
class ExternalGuardrailConfig:
    enabled: bool = False
    provider: str = "off"
    stages: tuple[str, ...] = ("context",)
    action: str = "block"
    fail_mode: str = "open"
    api_url: str = ""
    api_key: str = ""
    timeout_sec: float = 10.0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in TRUTHY:
        return True
    if normalized in FALSEY:
        return False
    return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY
    return bool(value)


def _parse_stages(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ("context",)
    stages = []
    for part in raw.split(","):
        stage = part.strip().lower()
        if stage in VALID_STAGES and stage not in stages:
            stages.append(stage)
    return tuple(stages or ["context"])


def load_config() -> ExternalGuardrailConfig:
    enabled = _env_bool("EXTERNAL_GUARDRAIL_ENABLED", False)
    provider = os.getenv("EXTERNAL_GUARDRAIL_PROVIDER", "off").strip().lower() or "off"
    if not enabled:
        provider = "off"

    action = os.getenv("EXTERNAL_GUARDRAIL_ACTION", "block").strip().lower()
    if action not in {"block", "flag_only"}:
        action = "block"

    fail_mode = os.getenv("EXTERNAL_GUARDRAIL_FAIL_MODE", "open").strip().lower()
    if fail_mode not in {"open", "closed"}:
        fail_mode = "open"

    return ExternalGuardrailConfig(
        enabled=enabled,
        provider=provider,
        stages=_parse_stages(os.getenv("EXTERNAL_GUARDRAIL_STAGES", "context")),
        action=action,
        fail_mode=fail_mode,
        api_url=os.getenv("EXTERNAL_GUARDRAIL_API_URL", "").strip(),
        api_key=os.getenv("EXTERNAL_GUARDRAIL_API_KEY", "").strip(),
        timeout_sec=_env_float("EXTERNAL_GUARDRAIL_TIMEOUT_SEC", 10.0),
    )


def stage_enabled(config: ExternalGuardrailConfig, stage: str) -> bool:
    return config.enabled and config.provider != "off" and stage in config.stages


def default_result(config: ExternalGuardrailConfig, stage: str) -> dict[str, Any]:
    return {
        "enabled": bool(config.enabled),
        "provider": config.provider,
        "stage": stage,
        "flagged": False,
        "blocked": False,
        "risk_score": None,
        "categories": [],
        "reason": "",
        "error": None,
        "raw_response": {},
    }


def _error_result(config: ExternalGuardrailConfig, stage: str, message: str) -> dict[str, Any]:
    result = default_result(config, stage)
    result["error"] = message
    result["reason"] = f"guardrail_error:{message}"
    if config.fail_mode == "closed":
        result["flagged"] = True
        result["blocked"] = config.action == "block"
    return result


def _finalize(config: ExternalGuardrailConfig, result: dict[str, Any]) -> dict[str, Any]:
    result["flagged"] = bool(result.get("flagged"))
    provider_blocked = bool(result.get("blocked"))
    result["blocked"] = bool(config.action == "block" and (provider_blocked or result["flagged"]))
    if result["categories"] is None:
        result["categories"] = []
    if not isinstance(result["categories"], list):
        result["categories"] = [str(result["categories"])]
    return result


def _mock_guardrail(config: ExternalGuardrailConfig, stage: str, text: str) -> dict[str, Any]:
    result = default_result(config, stage)
    hits = [pattern for pattern in MOCK_PATTERNS if re.search(pattern, text or "", re.IGNORECASE)]
    if hits:
        result["flagged"] = True
        result["categories"] = ["prompt_injection", "refusal_instruction"]
        result["reason"] = "mock_pattern_match:" + ",".join(hits[:5])
        result["risk_score"] = min(1.0, 0.25 + 0.15 * len(hits))
    return _finalize(config, result)


def _parse_generic_response(config: ExternalGuardrailConfig, stage: str, payload: Any) -> dict[str, Any]:
    result = default_result(config, stage)
    result["raw_response"] = payload if isinstance(payload, dict) else {"value": payload}
    if not isinstance(payload, dict):
        return _finalize(config, result)

    result["flagged"] = _as_bool(payload.get("flagged", payload.get("flag", False)))
    action = str(payload.get("action", "")).lower()
    result["blocked"] = _as_bool(payload.get("blocked", action in {"block", "blocked", "deny"}))
    result["risk_score"] = payload.get("risk_score", payload.get("score"))
    result["categories"] = payload.get("categories", payload.get("category", [])) or []
    result["reason"] = str(payload.get("reason", payload.get("message", "")) or "")
    return _finalize(config, result)


def _generic_http_guardrail(
    config: ExternalGuardrailConfig,
    stage: str,
    text: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    if not config.api_url:
        return _error_result(config, stage, "EXTERNAL_GUARDRAIL_API_URL is required")

    body = json.dumps(
        {
            "text": text,
            "stage": stage,
            "metadata": metadata or {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    request = urllib.request.Request(config.api_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_sec) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _error_result(config, stage, str(exc))

    try:
        payload: Any = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"raw_text": raw}
    return _parse_generic_response(config, stage, payload)


def _unimplemented_provider(config: ExternalGuardrailConfig, stage: str) -> dict[str, Any]:
    return _error_result(
        config,
        stage,
        f"provider '{config.provider}' is a skeleton; use generic_http or implement a provider parser",
    )


def _lakera_guardrail(config: ExternalGuardrailConfig, stage: str, text: str) -> dict[str, Any]:
    result = default_result(config, stage)
    if not config.api_key:
        return _error_result(config, stage, "EXTERNAL_GUARDRAIL_API_KEY not set for lakera")

    url = "https://api.lakera.ai/v2/guard"
    payload = json.dumps(
        {"messages": [{"role": "user", "content": text or ""}]},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
            "User-Agent": "curl/7.81.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _error_result(config, stage, str(exc))

    result["raw_response"] = data
    result["flagged"] = bool(data.get("flagged", False))
    result["categories"] = data.get("categories") or []
    result["reason"] = ",".join(result["categories"]) if result["categories"] else ""
    result["risk_score"] = 1.0 if result["flagged"] else 0.0
    return _finalize(config, result)


def check(
    stage: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    config: ExternalGuardrailConfig | None = None,
) -> dict[str, Any]:
    config = config or load_config()
    stage = stage.strip().lower()
    if stage not in VALID_STAGES:
        return _error_result(config, stage, f"invalid guardrail stage: {stage}")
    if not stage_enabled(config, stage):
        return default_result(config, stage)

    if config.provider == "mock":
        return _mock_guardrail(config, stage, text)
    if config.provider == "generic_http":
        return _generic_http_guardrail(config, stage, text, metadata)
    if config.provider == "lakera":
        return _lakera_guardrail(config, stage, text)
    if config.provider == "azure":
        return _unimplemented_provider(config, stage)
    if config.provider == "off":
        return default_result(config, stage)
    return _error_result(config, stage, f"unknown provider: {config.provider}")


def empty_summary(config: ExternalGuardrailConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    return {
        "external_guardrail_enabled": bool(config.enabled),
        "external_guardrail_provider": config.provider,
        "external_guardrail_stages": ",".join(config.stages),
        "external_guardrail_action": config.action,
        "input_guardrail_flagged": False,
        "input_guardrail_blocked": False,
        "context_guardrail_flagged": False,
        "context_guardrail_blocked": False,
        "output_guardrail_flagged": False,
        "output_guardrail_blocked": False,
        "external_guardrail_reason": "",
        "external_guardrail_categories": [],
        "external_guardrail_error": "",
        "llm_called": False,
        "final_answer_source": "",
        "guardrail_block_response": "",
    }


def merge_stage_result(summary: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    stage = str(result.get("stage", "")).strip().lower()
    if stage in VALID_STAGES:
        summary[f"{stage}_guardrail_flagged"] = bool(result.get("flagged"))
        summary[f"{stage}_guardrail_blocked"] = bool(result.get("blocked"))

    reason = str(result.get("reason") or "")
    if reason:
        existing = str(summary.get("external_guardrail_reason") or "")
        summary["external_guardrail_reason"] = " | ".join([v for v in [existing, reason] if v])

    categories = result.get("categories") or []
    if not isinstance(categories, list):
        categories = [str(categories)]
    merged_categories = list(summary.get("external_guardrail_categories") or [])
    for category in categories:
        if category not in merged_categories:
            merged_categories.append(category)
    summary["external_guardrail_categories"] = merged_categories

    error = result.get("error")
    if error:
        existing_error = str(summary.get("external_guardrail_error") or "")
        summary["external_guardrail_error"] = " | ".join([v for v in [existing_error, str(error)] if v])

    return summary
