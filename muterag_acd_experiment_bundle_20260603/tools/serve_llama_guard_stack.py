#!/usr/bin/env python
"""Serve a local Prompt Guard 2 + Llama Guard 3 moderation stack.

The experiment runner starts a fresh query process per question. Keeping these
models in a small HTTP service avoids loading both checkpoints hundreds of
times during Study A.
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, AutoTokenizer


TRUTHY = {"1", "true", "yes", "on"}


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in TRUTHY


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def resolve_device(raw: str) -> str:
    value = raw.strip().lower()
    if value == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if value.startswith("cuda") and not torch.cuda.is_available():
        return "cpu"
    return value


def token_windows(tokenizer, text: str, max_tokens: int, overlap: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    token_ids = tokenizer.encode(text, add_special_tokens=False)
    if len(token_ids) <= max_tokens:
        return [text]
    step = max(1, max_tokens - overlap)
    windows = []
    for start in range(0, len(token_ids), step):
        window = token_ids[start : start + max_tokens]
        if not window:
            break
        windows.append(tokenizer.decode(window, skip_special_tokens=True))
        if start + max_tokens >= len(token_ids):
            break
    return windows


class GuardStack:
    def __init__(self) -> None:
        self.prompt_guard_enabled = env_bool("LLAMA_STACK_ENABLE_PROMPT_GUARD", True)
        self.safety_guard_enabled = env_bool("LLAMA_STACK_ENABLE_SAFETY_GUARD", True)
        self.prompt_guard_model_id = os.getenv(
            "PROMPT_GUARD_MODEL",
            "meta-llama/Llama-Prompt-Guard-2-86M",
        )
        self.safety_guard_model_id = os.getenv(
            "SAFETY_GUARD_MODEL",
            "meta-llama/Llama-Guard-3-1B",
        )
        self.prompt_guard_device = resolve_device(os.getenv("PROMPT_GUARD_DEVICE", "cpu"))
        self.safety_guard_device = resolve_device(os.getenv("SAFETY_GUARD_DEVICE", "auto"))
        self.prompt_guard_threshold = env_float("PROMPT_GUARD_THRESHOLD", 0.5)
        self.prompt_guard_window_tokens = env_int("PROMPT_GUARD_WINDOW_TOKENS", 480)
        self.prompt_guard_window_overlap = env_int("PROMPT_GUARD_WINDOW_OVERLAP", 64)
        self.safety_guard_window_tokens = env_int("SAFETY_GUARD_WINDOW_TOKENS", 1536)
        self.safety_guard_window_overlap = env_int("SAFETY_GUARD_WINDOW_OVERLAP", 128)

        self.prompt_guard_tokenizer = None
        self.prompt_guard_model = None
        self.safety_guard_tokenizer = None
        self.safety_guard_model = None
        self._load_models()

    def _load_models(self) -> None:
        if self.prompt_guard_enabled:
            print(f"[guard-stack] loading prompt_guard={self.prompt_guard_model_id} device={self.prompt_guard_device}", flush=True)
            self.prompt_guard_tokenizer = AutoTokenizer.from_pretrained(self.prompt_guard_model_id)
            self.prompt_guard_model = AutoModelForSequenceClassification.from_pretrained(self.prompt_guard_model_id)
            self.prompt_guard_model.to(self.prompt_guard_device)
            self.prompt_guard_model.eval()

        if self.safety_guard_enabled:
            dtype = torch.float16 if self.safety_guard_device.startswith("cuda") else torch.float32
            print(f"[guard-stack] loading safety_guard={self.safety_guard_model_id} device={self.safety_guard_device}", flush=True)
            self.safety_guard_tokenizer = AutoTokenizer.from_pretrained(self.safety_guard_model_id)
            self.safety_guard_model = AutoModelForCausalLM.from_pretrained(
                self.safety_guard_model_id,
                torch_dtype=dtype,
            )
            self.safety_guard_model.to(self.safety_guard_device)
            self.safety_guard_model.eval()

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "prompt_guard_enabled": self.prompt_guard_enabled,
            "prompt_guard_model": self.prompt_guard_model_id,
            "prompt_guard_device": self.prompt_guard_device,
            "prompt_guard_threshold": self.prompt_guard_threshold,
            "safety_guard_enabled": self.safety_guard_enabled,
            "safety_guard_model": self.safety_guard_model_id,
            "safety_guard_device": self.safety_guard_device,
        }

    def prompt_guard_check(self, text: str) -> dict[str, Any]:
        if not self.prompt_guard_enabled:
            return {"enabled": False, "flagged": False, "score": 0.0, "windows": []}

        assert self.prompt_guard_tokenizer is not None
        assert self.prompt_guard_model is not None
        windows = token_windows(
            self.prompt_guard_tokenizer,
            text,
            self.prompt_guard_window_tokens,
            self.prompt_guard_window_overlap,
        )
        details = []
        max_score = 0.0
        for index, window in enumerate(windows):
            inputs = self.prompt_guard_tokenizer(
                window,
                return_tensors="pt",
                truncation=True,
                max_length=512,
            ).to(self.prompt_guard_device)
            with torch.inference_mode():
                logits = self.prompt_guard_model(**inputs).logits
                probabilities = torch.softmax(logits, dim=-1)[0]
            # Prompt Guard 2 is binary: 0=BENIGN, 1=MALICIOUS. Some published
            # checkpoint revisions omit id2label, so use the documented index.
            score = float(probabilities[1].item())
            max_score = max(max_score, score)
            details.append({"window": index, "malicious_score": round(score, 6)})
            if score >= self.prompt_guard_threshold:
                break
        return {
            "enabled": True,
            "flagged": max_score >= self.prompt_guard_threshold,
            "score": round(max_score, 6),
            "windows": details,
        }

    def safety_guard_check(self, text: str) -> dict[str, Any]:
        if not self.safety_guard_enabled:
            return {"enabled": False, "flagged": False, "output": "", "categories": []}

        assert self.safety_guard_tokenizer is not None
        assert self.safety_guard_model is not None
        windows = token_windows(
            self.safety_guard_tokenizer,
            text,
            self.safety_guard_window_tokens,
            self.safety_guard_window_overlap,
        )
        details = []
        categories: list[str] = []
        flagged = False
        for index, window in enumerate(windows):
            conversation: list[dict[str, Any]] = [
                {"role": "user", "content": [{"type": "text", "text": window}]}
            ]
            try:
                input_ids = self.safety_guard_tokenizer.apply_chat_template(
                    conversation,
                    return_tensors="pt",
                ).to(self.safety_guard_device)
            except (TypeError, ValueError):
                input_ids = self.safety_guard_tokenizer.apply_chat_template(
                    [{"role": "user", "content": window}],
                    return_tensors="pt",
                ).to(self.safety_guard_device)
            with torch.inference_mode():
                output_ids = self.safety_guard_model.generate(
                    input_ids,
                    max_new_tokens=20,
                    pad_token_id=0,
                    do_sample=False,
                )
            generated = self.safety_guard_tokenizer.decode(
                output_ids[0][input_ids.shape[-1] :],
                skip_special_tokens=True,
            ).strip()
            window_categories = sorted(set(re.findall(r"\bS\d+\b", generated.upper())))
            details.append({"window": index, "output": generated, "categories": window_categories})
            if generated.lower().startswith("unsafe"):
                flagged = True
                categories = window_categories
                break
        return {
            "enabled": True,
            "flagged": flagged,
            "output": details[-1]["output"] if details else "",
            "categories": categories,
            "windows": details,
        }

    def check(self, payload: dict[str, Any]) -> dict[str, Any]:
        stage = str(payload.get("stage") or "context").strip().lower()
        text = str(payload.get("text") or "")
        metadata = payload.get("metadata") or {}
        items = metadata.get("guardrail_context_items") if isinstance(metadata, dict) else None
        if stage == "context" and isinstance(items, list) and items:
            candidates = [
                {
                    "chunk_id": str(item.get("chunk_id") or ""),
                    "source": str(item.get("source") or ""),
                    "text": str(item.get("text") or ""),
                }
                for item in items
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
        else:
            candidates = [{"chunk_id": "", "source": "", "text": text}]

        prompt_guard_hits = []
        safety_guard_hits = []
        max_prompt_score = 0.0
        for candidate in candidates:
            prompt = self.prompt_guard_check(candidate["text"])
            max_prompt_score = max(max_prompt_score, float(prompt.get("score") or 0.0))
            if prompt.get("flagged"):
                prompt_guard_hits.append({**candidate, "text": "", "result": prompt})

            safety = self.safety_guard_check(candidate["text"])
            if safety.get("flagged"):
                safety_guard_hits.append({**candidate, "text": "", "result": safety})

        blocked_by = []
        categories = []
        if prompt_guard_hits:
            blocked_by.append("prompt_guard")
            categories.append("prompt_injection")
        if safety_guard_hits:
            blocked_by.append("safety_guard")
            safety_categories = sorted(
                {
                    category
                    for hit in safety_guard_hits
                    for category in hit["result"].get("categories", [])
                }
            )
            categories.extend([f"safety_guard:{category}" for category in safety_categories] or ["safety_guard:unsafe"])

        flagged = bool(blocked_by)
        return {
            "flagged": flagged,
            "blocked": flagged,
            "risk_score": round(max_prompt_score, 6) if self.prompt_guard_enabled else None,
            "blocked_by": blocked_by,
            "categories": categories,
            "reason": "blocked_by:" + ",".join(blocked_by) if blocked_by else "",
            "details": {
                "stage": stage,
                "candidate_count": len(candidates),
                "prompt_guard_hits": prompt_guard_hits,
                "safety_guard_hits": safety_guard_hits,
            },
        }


class Handler(BaseHTTPRequestHandler):
    stack: GuardStack

    def log_message(self, format: str, *args: Any) -> None:
        print("[guard-stack] " + format % args, flush=True)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_json(404, {"error": "not found"})
            return
        self.send_json(200, self.stack.health())

    def do_POST(self) -> None:
        if self.path != "/check":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.send_json(200, self.stack.check(payload))
        except Exception as exc:
            self.send_json(500, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    host = os.getenv("LLAMA_STACK_HOST", "127.0.0.1")
    port = env_int("LLAMA_STACK_PORT", 8191)
    Handler.stack = GuardStack()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[guard-stack] ready=http://{host}:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
