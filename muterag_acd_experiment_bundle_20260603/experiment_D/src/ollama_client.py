import os
from typing import Any

import requests

from src.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SEC,
    LLM_TOP_P,
    OLLAMA_GENERATE_URL,
    OLLAMA_MODEL,
)


OPENAI_COMPATIBLE_PROVIDERS = {"openai", "openai_compatible", "vllm"}


def _call_ollama(prompt: str) -> dict[str, Any]:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
            "top_p": 0.9,
            "repeat_penalty": 1.05,
        },
    }

    try:
        response = requests.post(OLLAMA_GENERATE_URL, json=payload, timeout=LLM_TIMEOUT_SEC)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        response_text = ""
        response_error = ""
        if exc.response is not None:
            try:
                response_text = exc.response.text.strip()
            except Exception:
                response_text = ""
            try:
                response_error = exc.response.json().get("error", "")
            except Exception:
                response_error = ""

        details = f"HTTP {status_code}"
        if response_text:
            details += f" | response={response_text[:300]}"

        if exc.response is not None and exc.response.status_code == 404:
            if response_error and "model" in response_error.lower() and "not found" in response_error.lower():
                raise RuntimeError(
                    f"Failed to call Ollama at {OLLAMA_GENERATE_URL}. {details}. "
                    f"The Ollama server is reachable, but the configured model `{OLLAMA_MODEL}` is not installed. "
                    f"Pull it first with `ollama pull {OLLAMA_MODEL}` or switch `OLLAMA_MODEL` to an installed model."
                ) from exc
            raise RuntimeError(
                f"Failed to call Ollama at {OLLAMA_GENERATE_URL}. {details}. "
                "A server is reachable on that host/port, but it does not expose the Ollama native endpoint "
                "`/api/generate`. Verify that Ollama itself is running there by checking `/api/tags`."
            ) from exc

        raise RuntimeError(
            f"Failed to call Ollama at {OLLAMA_GENERATE_URL}. {details}. "
            "Check that Ollama is running on the host and the model is pulled."
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Failed to call Ollama at {OLLAMA_GENERATE_URL}. "
            "Check that Ollama is running on the host and the model is pulled."
        ) from exc

    return response.json()


def _openai_chat_url() -> str:
    base_url = LLM_BASE_URL
    if not base_url and LLM_PROVIDER == "vllm":
        base_url = "http://localhost:8000/v1"
    if not base_url:
        raise RuntimeError(
            "LLM_PROVIDER is set to an OpenAI-compatible backend, but LLM_BASE_URL is empty. "
            "For vLLM use something like `LLM_BASE_URL=http://localhost:8000/v1`."
        )
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _extract_openai_compatible_text(body: dict[str, Any]) -> str:
    choices = body.get("choices") or []
    if choices:
        first = choices[0]
        message = first.get("message") or {}
        text = _content_to_text(message.get("content"))
        if text:
            return text
        return _content_to_text(first.get("text"))
    return _content_to_text(body.get("output_text"))


def _call_openai_compatible(prompt: str) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    messages: list[dict[str, str]] = []
    system_prompt = os.getenv("LLM_SYSTEM_PROMPT", "").strip()
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
        "stream": False,
        "temperature": LLM_TEMPERATURE,
        "top_p": LLM_TOP_P,
    }
    if LLM_MAX_TOKENS:
        payload["max_tokens"] = int(LLM_MAX_TOKENS)

    url = _openai_chat_url()
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT_SEC)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        response_text = ""
        if exc.response is not None:
            try:
                response_text = exc.response.text.strip()
            except Exception:
                response_text = ""
        details = f"HTTP {status_code}"
        if response_text:
            details += f" | response={response_text[:500]}"
        raise RuntimeError(
            f"Failed to call OpenAI-compatible LLM at {url}. {details}. "
            f"Check LLM_BASE_URL, LLM_MODEL, and whether the vLLM server is ready."
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Failed to call OpenAI-compatible LLM at {url}. "
            "Check that the vLLM server is reachable."
        ) from exc

    body = response.json()
    return {
        "response": _extract_openai_compatible_text(body),
        "model": LLM_MODEL,
        "provider": LLM_PROVIDER,
    }


def ask_ollama(prompt: str) -> dict[str, Any]:
    if LLM_PROVIDER in {"", "ollama"}:
        return _call_ollama(prompt)
    if LLM_PROVIDER in OPENAI_COMPATIBLE_PROVIDERS:
        return _call_openai_compatible(prompt)
    raise RuntimeError(
        f"Unsupported LLM_PROVIDER `{LLM_PROVIDER}`. "
        "Use `ollama`, `vllm`, or `openai_compatible`."
    )
