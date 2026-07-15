"""Resource accounting for the benchmark harness.

Deterministic remainder allocation for shared Worker-A token cost:

base_share = worker_a_tokens // arm_count
remainder = worker_a_tokens % arm_count

Allocate:
- base_share + 1 to the first `remainder` arms in a stable predefined architecture order;
- base_share to every remaining arm.

Architecture ordering is frozen: stateless, summary, verified_state, raw_memory, retrieval, verified_state_procedures.

CRITICAL: Worker-A source token cost is computed ONCE using neutral source-session rules
(no adapter cost, no transcript-delivery overhead). Storage bytes are tracked separately
and NEVER added to token totals. That one immutable token cost is allocated across the
frozen architecture order. Each trajectory receives only its allocation plus its
successor-session costs. raw_memory transcript-delivery overhead applies ONLY to
successor delivery, never to Worker A.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any

from .schemas import SessionEvent

# Frozen stable architecture order for remainder allocation
ARCHITECTURE_ORDER = [
    "stateless",
    "summary",
    "verified_state",
    "raw_memory",
    "retrieval",
    "verified_state_procedures",
]


def _validate_architectures(architectures: list[str]) -> None:
    """Validate architecture list: no empty, no duplicates, no unknown names."""
    if not architectures:
        raise ValueError("Architecture list must not be empty")
    if len(architectures) != len(set(architectures)):
        raise ValueError(f"Duplicate architectures in list: {architectures}")
    for a in architectures:
        if a not in ARCHITECTURE_ORDER:
            raise ValueError(f"Unknown architecture: {a!r}. Must be one of {ARCHITECTURE_ORDER}")


def allocate_worker_a_cost(worker_a_tokens: int, architectures: list[str]) -> dict[str, int]:
    """Allocate Worker-A token cost across architectures using deterministic remainder.

    Returns dict mapping architecture name to its allocated share.
    Sum of all allocations == worker_a_tokens exactly.
    Raises ValueError for invalid inputs.
    """
    if not isinstance(worker_a_tokens, int) or isinstance(worker_a_tokens, bool):
        raise ValueError(f"Worker-A tokens must be a non-negative integer, got {worker_a_tokens!r}")
    if worker_a_tokens < 0:
        raise ValueError(f"Worker-A tokens must be non-negative, got {worker_a_tokens}")
    _validate_architectures(architectures)

    n = len(architectures)
    if n == 0:
        return {}
    base_share = worker_a_tokens // n
    remainder = worker_a_tokens % n

    ordered = sorted(architectures, key=lambda a: ARCHITECTURE_ORDER.index(a))

    allocation: dict[str, int] = {}
    for i, arch in enumerate(ordered):
        if i < remainder:
            allocation[arch] = base_share + 1
        else:
            allocation[arch] = base_share
    return allocation


@dataclass(frozen=True)
class WorkerASourceCost:
    """Structured value for Worker-A source cost.

    token_total: neutral token cost (no adapter, no transcript delivery)
    storage_bytes: checkpoint storage bytes (tracked separately, never added to tokens)
    """
    token_total: int = 0
    storage_bytes: int = 0


def compute_worker_a_source_cost(events: list[SessionEvent]) -> WorkerASourceCost:
    """Compute Worker-A source cost using neutral rules.

    Returns a WorkerASourceCost with token_total and storage_bytes separated.
    No adapter cost, no transcript-delivery overhead, no architecture-specific charges.
    storage_bytes is NEVER added to token_total.
    """
    token_total = 0
    storage_bytes = 0
    for event in events:
        for tc in event.tool_calls:
            params_str = json.dumps(tc.params, sort_keys=True)
            token_total += len(params_str) // 4
            token_total += len(tc.output) // 4
        if event.checkpoint:
            cp_str = json.dumps(event.checkpoint.to_json(), sort_keys=True)
            storage_bytes += len(cp_str.encode("utf-8"))
    return WorkerASourceCost(token_total=token_total, storage_bytes=storage_bytes)


@dataclass(frozen=True)
class SessionCost:
    input_tokens: int = 0
    output_tokens: int = 0
    adapter_tokens: int = 0
    transcript_delivery_tokens: int = 0
    retrieval_tokens: int = 0
    wall_time_seconds: float = 0.0
    storage_bytes: int = 0

    def total(self) -> int:
        """Token total: input + output + adapter + transcript_delivery + retrieval.
        storage_bytes is NEVER included in total()."""
        return self.input_tokens + self.output_tokens + self.adapter_tokens + self.transcript_delivery_tokens + self.retrieval_tokens

    def to_json(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "adapter_tokens": self.adapter_tokens,
            "transcript_delivery_tokens": self.transcript_delivery_tokens,
            "retrieval_tokens": self.retrieval_tokens,
            "wall_time_seconds": self.wall_time_seconds,
            "storage_bytes": self.storage_bytes,
            "total_tokens": self.total(),
        }


@dataclass(frozen=True)
class TrajectoryCost:
    trajectory_id: str
    sessions: dict[str, SessionCost]
    shared_baseline_allocation: int = 0
    architecture: str = ""
    worker_a_source_tokens: int = 0
    worker_a_storage_bytes: int = 0

    def total_tokens(self) -> int:
        """Total = successor sessions + this arm's allocation.
        Worker-A cost is NOT included directly."""
        return sum(s.total() for w, s in self.sessions.items() if w != "A") + self.shared_baseline_allocation

    def to_json(self) -> dict:
        return {
            "trajectory_id": self.trajectory_id,
            "architecture": self.architecture,
            "sessions": {k: v.to_json() for k, v in self.sessions.items()},
            "shared_baseline_allocation": self.shared_baseline_allocation,
            "worker_a_source_tokens": self.worker_a_source_tokens,
            "worker_a_storage_bytes": self.worker_a_storage_bytes,
            "total_tokens": self.total_tokens(),
        }


def compute_session_cost(
    events: list[SessionEvent],
    architecture: str,
    adapter_token_cost: int = 0,
    is_worker_a: bool = False,
) -> SessionCost:
    """Compute cost for one session.

    For Worker A: no adapter cost, no transcript-delivery overhead.
    raw_memory transcript-delivery applies ONLY to successor sessions.
    storage_bytes is tracked but NEVER added to total().
    """
    input_tokens = 0
    output_tokens = 0
    storage_bytes = 0
    for event in events:
        for tc in event.tool_calls:
            params_str = json.dumps(tc.params, sort_keys=True)
            input_tokens += len(params_str) // 4
            output_tokens += len(tc.output) // 4
        if event.checkpoint:
            cp_str = json.dumps(event.checkpoint.to_json(), sort_keys=True)
            storage_bytes += len(cp_str.encode("utf-8"))
    transcript_delivery_tokens = 0
    if architecture == "raw_memory" and not is_worker_a:
        transcript_str = json.dumps([e.to_json() for e in events], sort_keys=True)
        transcript_delivery_tokens = len(transcript_str) // 4
    return SessionCost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        adapter_tokens=adapter_token_cost if not is_worker_a else 0,
        transcript_delivery_tokens=transcript_delivery_tokens,
        wall_time_seconds=len(events) * 1.0,
        storage_bytes=storage_bytes,
    )


def compute_trajectory_cost(
    trajectory_id: str,
    sessions: dict[str, list[SessionEvent]],
    architecture: str,
    adapter_token_cost: int = 0,
    all_architectures: list[str] | None = None,
) -> TrajectoryCost:
    """Compute cost for one trajectory arm.

    Worker-A token cost is computed ONCE using neutral source-session rules and
    allocated across all architecture arms using deterministic remainder.
    This arm receives its share. Storage bytes are tracked separately.

    If all_architectures is omitted, defaults to the three pilot arms.
    Non-pilot architectures must supply an explicit valid arm set.
    Raises ValueError if architecture is absent from all_architectures.
    """
    if all_architectures is None:
        all_architectures = ARCHITECTURE_ORDER[:3]

    if architecture not in all_architectures:
        raise ValueError(
            f"Architecture {architecture!r} is not in all_architectures {all_architectures}"
        )

    session_costs: dict[str, SessionCost] = {}

    for worker, events in sessions.items():
        is_wa = worker == "A"
        cost = compute_session_cost(
            events, architecture,
            adapter_token_cost if not is_wa else 0,
            is_worker_a=is_wa,
        )
        session_costs[worker] = cost

    # Compute Worker-A neutrally from supplied Worker-A events
    worker_a_events = sessions.get("A", [])
    source = compute_worker_a_source_cost(worker_a_events)
    source_tokens = source.token_total

    # Deterministically calculate allocation from validated cost and architecture set
    allocation = allocate_worker_a_cost(source_tokens, all_architectures)
    this_share = allocation.get(architecture, 0)

    return TrajectoryCost(
        trajectory_id=trajectory_id,
        sessions=session_costs,
        shared_baseline_allocation=this_share,
        architecture=architecture,
        worker_a_source_tokens=source_tokens,
        worker_a_storage_bytes=source.storage_bytes,
    )


def verify_accounting(
    baselines_count: int,
    clones_count: int,
    architectures: list[str],
) -> dict:
    """Generic accounting — derives entirely from supplied parameters.

    No fixed pilot block. Returns only generic study accounting.
    Validates counts as positive, non-boolean integers.
    Rejects duplicate or unknown architectures.
    """
    for name, val in [("baselines_count", baselines_count), ("clones_count", clones_count)]:
        if not isinstance(val, int) or isinstance(val, bool):
            return {"error": "invalid_inputs", "message": f"{name} must be a positive integer, got {val!r}"}
        if val <= 0:
            return {"error": "invalid_inputs", "message": f"{name} must be positive, got {val}"}

    if not architectures:
        return {"error": "invalid_inputs", "message": "architectures list must not be empty"}
    if len(architectures) != len(set(architectures)):
        return {"error": "invalid_inputs", "message": f"Duplicate architectures in list: {architectures}"}
    for a in architectures:
        if a not in ARCHITECTURE_ORDER:
            return {"error": "invalid_inputs", "message": f"Unknown architecture: {a!r}"}

    expected_clones = baselines_count * len(architectures)
    if clones_count != expected_clones:
        return {
            "error": "clone_mismatch",
            "message": f"Expected {expected_clones} clones ({baselines_count} × {len(architectures)}), got {clones_count}",
        }

    full_trajectories = baselines_count * len(architectures)
    full_sessions = full_trajectories * 4
    full_unique = baselines_count + (full_trajectories * 3)

    return {
        "full_study": {
            "unique_worker_a": baselines_count,
            "clones": clones_count,
            "trajectories": full_trajectories,
            "sessions": full_sessions,
            "unique_model_session_equivalents": full_unique,
        },
    }


def pilot_accounting() -> dict:
    """Fixed pilot-contract function: 2 Worker-A, 3 architectures, 24 logical, 20 unique."""
    return {
        "worker_a_source_sessions": 2,
        "successor_sessions": 18,
        "unique_model_session_equivalents": 20,
        "logical_session_records": 24,
        "formula": "24 logical = 20 unique (2 Worker-A + 18 successors)",
    }
