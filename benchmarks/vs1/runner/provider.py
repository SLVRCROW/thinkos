"""VS-1 provider adapter — Ollama Cloud (OpenAI-compatible).

Single provider route, single frozen model, temperature 0, zero retries,
zero fallback. Captures: returned model identity, usage tokens, latency,
and any error. Stdlib only (urllib) — no third-party client.

This module is the ONLY place that makes network calls in the powered path.
The frozen measurement package (benchmarks/vs1/) never imports it.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderCallResult:
    provider_invocation_id: str
    requested_model: str
    returned_model: str
    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    status: str  # ok | error | timeout
    error: str = ""
    latency_seconds: float = 0.0

    def to_json(self) -> dict:
        return {
            "provider_invocation_id": self.provider_invocation_id,
            "requested_model": self.requested_model,
            "returned_model": self.returned_model,
            "content": self.content,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "status": self.status,
            "error": self.error,
            "latency_seconds": self.latency_seconds,
        }


class OllamaCloudAdapter:
    """Frozen adapter for one model on Ollama Cloud.

    FROZEN: model, temperature 0, max_tokens 4096, retries 0, no fallback.
    Raises RuntimeError on any non-200 (no silent retry).
    """

    def __init__(
        self,
        model: str = "deepseek-v4-pro:0813",
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_seconds: int = 120,
    ):
        self.model = model
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or "https://ollama.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("OLLAMA_API_KEY", "")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        if not self.api_key:
            raise RuntimeError("OLLAMA_API_KEY not set; refusing to call provider")

    def complete(self, prompt: str, invocation_id: str) -> ProviderCallResult:
        """One completion. Zero retries. Returns a full receipt."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            latency = time.monotonic() - start
        except urllib.error.HTTPError as e:
            latency = time.monotonic() - start
            return ProviderCallResult(
                provider_invocation_id=invocation_id,
                requested_model=self.model,
                returned_model="",
                content="",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="error",
                error=f"HTTP {e.code}: {e.reason}",
                latency_seconds=latency,
            )
        except Exception as e:  # network/timeout/parse
            latency = time.monotonic() - start
            return ProviderCallResult(
                provider_invocation_id=invocation_id,
                requested_model=self.model,
                returned_model="",
                content="",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="error",
                error=str(e),
                latency_seconds=latency,
            )

        returned = body.get("model", "")
        if returned != self.model:
            return ProviderCallResult(
                provider_invocation_id=invocation_id,
                requested_model=self.model,
                returned_model=returned,
                content="",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="error",
                error=f"MODEL IDENTITY MISMATCH: requested {self.model}, returned {returned}",
                latency_seconds=latency,
            )

        choices = body.get("choices", [])
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        usage = body.get("usage", {}) or {}
        return ProviderCallResult(
            provider_invocation_id=invocation_id,
            requested_model=self.model,
            returned_model=returned,
            content=content,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            status="ok" if content else "error",
            error="" if content else "empty completion",
            latency_seconds=latency,
        )
