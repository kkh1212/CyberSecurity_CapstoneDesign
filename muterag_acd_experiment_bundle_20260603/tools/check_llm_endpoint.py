#!/usr/bin/env python3
"""Small OpenAI-compatible endpoint check for vLLM/OpenAI-style servers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests


def chat_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def models_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        return f"{base_url}/models"
    return f"{base_url}/v1/models"


def content_to_text(content: Any) -> str:
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
        return "\n".join(parts)
    return "" if content is None else str(content)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("LLM_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", ""))
    parser.add_argument("--api-key", default=os.getenv("LLM_API_KEY", ""))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("LLM_TIMEOUT_SEC", "30")))
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()

    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    try:
        response = requests.get(models_url(args.base_url), headers=headers, timeout=args.timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[FAIL] model probe failed: {exc}", file=sys.stderr)
        return 1

    if args.probe_only:
        print("[OK] endpoint is reachable")
        return 0

    if not args.model:
        try:
            body = response.json()
            args.model = body["data"][0]["id"]
        except Exception:
            print("[FAIL] --model is required when it cannot be inferred from /models", file=sys.stderr)
            return 2

    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "temperature": 0,
        "max_tokens": 8,
        "stream": False,
    }
    try:
        response = requests.post(chat_url(args.base_url), headers=headers, json=payload, timeout=args.timeout)
        response.raise_for_status()
        body = response.json()
    except requests.RequestException as exc:
        text = ""
        if getattr(exc, "response", None) is not None:
            text = exc.response.text[:500]
        print(f"[FAIL] chat probe failed: {exc} {text}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"[FAIL] chat probe returned non-JSON response: {exc}", file=sys.stderr)
        return 1

    choices = body.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    text = content_to_text(message.get("content")).strip()
    print(f"[OK] {args.model}: {text[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
