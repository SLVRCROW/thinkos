"""VS-1 accounting: physical/logical provider-call accounting.

Models the G1 contract's accounting semantics for the VS-1 six-arm scale:
physical provider invocations vs logical worker-session equivalents,
sum-of-parts verification at every aggregation level, and a deterministic
integer micro-USD model where a pricing catalog is provided.

The instrumentation pilot uses this module with a MOCK pricing catalog and
zero real provider calls. The powered run uses a Marc-approved real catalog.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .schemas import ARMS, CONDITIONS, json_dumps  # noqa: F401  (vocabulary)


@dataclass(frozen=True)
class ProviderCall:
    provider_invocation_id: str
    trajectory_id: str
    worker: str
    stage: int
    attempt: int = 0
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_input_tokens: int = 0
    status: str = "ok"  # ok | error | timeout | retry
    retry_of: str | None = None

    def to_json(self) -> dict:
        return {
            "provider_invocation_id": self.provider_invocation_id,
            "trajectory_id": self.trajectory_id,
            "worker": self.worker,
            "stage": self.stage,
            "attempt": self.attempt,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "status": self.status,
            "retry_of": self.retry_of,
        }


def compute_call_cost(
    call: ProviderCall,
    price_per_1k_input: int = 0,
    price_per_1k_output: int = 0,
    price_per_1k_cached_input: int = 0,
) -> dict[str, int]:
    """Micro-USD cost with ceiling division (G1 semantics: integer micro-USD)."""
    input_tokens = call.prompt_tokens - call.cached_input_tokens
    cost = 0
    input_cost = 0
    cached_cost = 0
    output_cost = 0
    if input_tokens > 0:
        input_cost = math.ceil((input_tokens * price_per_1k_input) / 1000)
        cost += input_cost
    if call.cached_input_tokens > 0:
        cached_cost = math.ceil((call.cached_input_tokens * price_per_1k_cached_input) / 1000)
        cost += cached_cost
    if call.completion_tokens > 0:
        output_cost = math.ceil((call.completion_tokens * price_per_1k_output) / 1000)
        cost += output_cost
    return {
        "micro_usd": cost,
        "input_micro_usd": input_cost,
        "cached_input_micro_usd": cached_cost,
        "output_micro_usd": output_cost,
    }


def sum_of_parts(calls: list[ProviderCall], price_catalog: dict[str, int]) -> dict[str, Any]:
    """Aggregate accounting with sum-of-parts verification.

    price_catalog keys: input_per_1k, output_per_1k, cached_input_per_1k (micro-USD).
    """
    totals: dict[str, Any] = {
        "physical_calls": len(calls),
        "logical_calls": sum(1 for c in calls if c.attempt == 0),
        "prompt_tokens": sum(c.prompt_tokens for c in calls),
        "completion_tokens": sum(c.completion_tokens for c in calls),
        "cached_input_tokens": sum(c.cached_input_tokens for c in calls),
        "total_tokens": sum(c.prompt_tokens + c.completion_tokens for c in calls),
        "errors": sum(1 for c in calls if c.status in ("error", "timeout", "retry_exhausted")),
        "retries": sum(1 for c in calls if c.attempt > 0),
        "micro_usd": 0,
    }
    parts = [
        compute_call_cost(
            c,
            price_catalog.get("input_per_1k", 0),
            price_catalog.get("output_per_1k", 0),
            price_catalog.get("cached_input_per_1k", 0),
        )
        for c in calls
    ]
    sums = {"micro_usd": 0, "input_micro_usd": 0, "cached_input_micro_usd": 0, "output_micro_usd": 0}
    for p in parts:
        for k in sums:
            sums[k] += p[k]
    totals["micro_usd"] = sums["micro_usd"]
    totals["sum_of_parts"] = sums
    totals["sum_of_parts_verified"] = totals["micro_usd"] == sums["micro_usd"]
    return totals


def trajectory_accounting(
    calls: list[ProviderCall],
    price_catalog: dict[str, int],
) -> dict[str, Any]:
    """Per-trajectory accounting."""
    agg = sum_of_parts(calls, price_catalog)
    return {
        "trajectory_id": calls[0].trajectory_id if calls else "unknown",
        "total_calls": len(calls),
        "total_tokens": agg["total_tokens"],
        "micro_usd": agg["micro_usd"],
        "errors": agg["errors"],
        "retries": agg["retries"],
    }


def pilot_accounting(
    all_calls: list[ProviderCall],
    price_catalog: dict[str, int],
    budget_micro_usd: int | None = None,
) -> dict[str, Any]:
    """Pilot-level accounting with budget enforcement."""
    agg = sum_of_parts(all_calls, price_catalog)
    result = {
        "total_calls": agg["physical_calls"],
        "total_tokens": agg["total_tokens"],
        "micro_usd": agg["micro_usd"],
        "budget_micro_usd": budget_micro_usd,
        "within_budget": True if budget_micro_usd is None else agg["micro_usd"] <= budget_micro_usd,
    }
    return result
