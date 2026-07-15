"""G1-B accounting: pricing, cost calculation, allocation, and invariants.

Standard library only. No provider, scoring, or runtime imports.
"""

from __future__ import annotations
import dataclasses
from typing import Any


# ── Synthetic pricing entry ───────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class PricingEntry:
    """Integer micro-USD per million tokens for one price category."""

    provider: str
    model: str
    category: str  # "uncached_input", "cached_input", "output"
    price_per_million: int  # micro-USD per 1M tokens
    effective_date: str  # RFC 3339 UTC
    source: str  # provenance reference


# ── Per-call accounting result ────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class CallAccounting:
    """Accounting result for one provider invocation."""

    provider_invocation_id: str
    prompt_tokens_total: int | None
    cached_input_tokens: int | None
    uncached_input_tokens: int | None
    completion_tokens: int | None
    calculated_micro_usd_cost: int | None
    call_accounting_valid: bool
    errors: tuple[str, ...] = ()


# ── Shared-source allocation ──────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class SharedSourceAllocation:
    """Allocation of one shared Worker-A cost across architecture arms."""

    shared_source_id: str
    physical_calculated_cost: int
    allocations: dict[str, int]  # architecture -> allocated micro-USD
    sum_allocated: int
    allocation_valid: bool
    errors: tuple[str, ...] = ()


# ── Logical trajectory accounting ─────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class LogicalTrajectoryAccounting:
    """Accounting for one logical trajectory."""

    trajectory_id: str
    successor_call_ids: tuple[str, ...]
    successor_calculated_cost: int
    allocated_worker_a_cost: int
    logical_trajectory_cost: int
    trajectory_accounting_valid: bool
    errors: tuple[str, ...] = ()


# ── Physical pilot accounting ─────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class PhysicalPilotAccounting:
    """Physical-level accounting for the entire pilot."""

    physical_invocation_ids: tuple[str, ...]
    physical_calculated_costs: dict[str, int]  # invocation_id -> cost
    total_physical_calculated_cost: int
    deduplication_valid: bool
    errors: tuple[str, ...] = ()


# ── Aggregate validity and invariant results ───────────────────────────


@dataclasses.dataclass(frozen=True)
class AccountingInvariants:
    """Aggregate invariant check results for the pilot."""

    total_physical_calculated_cost: int
    total_logical_trajectory_cost: int
    invariant_valid: bool
    shared_source_allocations: tuple[SharedSourceAllocation, ...]
    trajectory_accountings: tuple[LogicalTrajectoryAccounting, ...]
    physical_accounting: PhysicalPilotAccounting
    errors: tuple[str, ...] = ()


# ── Cost calculation ───────────────────────────────────────────────────


ALLOCATION_ORDER = ("stateless", "summary", "verified_state")


def ceiling_div(numerator: int, denominator: int) -> int:
    """Integer ceiling division. Returns (numerator + denominator - 1) // denominator."""
    if denominator <= 0:
        raise ValueError(f"denominator must be positive, got {denominator}")
    if numerator < 0:
        raise ValueError(f"numerator must be non-negative, got {numerator}")
    return (numerator + denominator - 1) // denominator


def calculate_call_cost(
    prompt_tokens_total: int | None,
    cached_input_tokens: int | None,
    completion_tokens: int | None,
    prices: dict[str, int],
    cached_included: bool,  # True if prompt_tokens_total includes cached
) -> CallAccounting:
    """Calculate cost for one provider call.

    prices must contain keys: "uncached_input", "cached_input", "output"
    each as integer micro-USD per million tokens.
    """
    errors = []

    # Validate inputs
    if prompt_tokens_total is None:
        errors.append("prompt_tokens_total is null")
    if completion_tokens is None:
        errors.append("completion_tokens is null")

    # Normalize cached tokens
    if cached_input_tokens is None:
        pass  # unknown — keep null
    elif cached_input_tokens == 0:
        cached_input_tokens = 0  # explicitly reported no cache

    # Decompose
    uncached_input_tokens: int | None = None
    if prompt_tokens_total is not None and cached_input_tokens is not None:
        if cached_included:
            uncached_input_tokens = prompt_tokens_total - cached_input_tokens
        else:
            uncached_input_tokens = prompt_tokens_total

    # Validate decomposition
    if uncached_input_tokens is not None and uncached_input_tokens < 0:
        errors.append(
            f"negative uncached_input_tokens ({uncached_input_tokens}): "
            f"prompt_tokens_total={prompt_tokens_total}, "
            f"cached_input_tokens={cached_input_tokens}, "
            f"cached_included={cached_included}"
        )
        uncached_input_tokens = None

    if prompt_tokens_total is not None and cached_input_tokens is not None:
        if cached_included and cached_input_tokens > prompt_tokens_total:
            errors.append(
                f"cached_input_tokens ({cached_input_tokens}) exceeds "
                f"prompt_tokens_total ({prompt_tokens_total})"
            )

    # Calculate cost
    calculated_cost: int | None = None
    call_valid = True

    if (
        prompt_tokens_total is not None
        and completion_tokens is not None
        and uncached_input_tokens is not None
        and cached_input_tokens is not None
        and not errors
    ):
        cost = 0
        # Uncached input
        if uncached_input_tokens > 0:
            cost += ceiling_div(
                uncached_input_tokens * prices["uncached_input"], 1_000_000
            )
        # Cached input
        if cached_input_tokens > 0:
            cost += ceiling_div(
                cached_input_tokens * prices["cached_input"], 1_000_000
            )
        # Output
        if completion_tokens > 0:
            cost += ceiling_div(
                completion_tokens * prices["output"], 1_000_000
            )
        calculated_cost = cost
    else:
        call_valid = False

    return CallAccounting(
        provider_invocation_id="",
        prompt_tokens_total=prompt_tokens_total,
        cached_input_tokens=cached_input_tokens,
        uncached_input_tokens=uncached_input_tokens,
        completion_tokens=completion_tokens,
        calculated_micro_usd_cost=calculated_cost,
        call_accounting_valid=call_valid,
        errors=tuple(errors),
    )


# ── Shared Worker-A allocation ────────────────────────────────────────


def allocate_shared_cost(
    shared_source_id: str,
    physical_calculated_cost: int,
    architecture_order: tuple[str, ...] = ALLOCATION_ORDER,
) -> SharedSourceAllocation:
    """Allocate a shared Worker-A cost across architecture arms.

    Uses quotient/remainder allocation per contract §11.
    """
    errors = []
    if physical_calculated_cost < 0:
        errors.append("physical_calculated_cost must be non-negative")

    n = len(architecture_order)
    if n == 0:
        errors.append("architecture_order must not be empty")

    allocations: dict[str, int] = {}
    if not errors:
        base = physical_calculated_cost // n
        remainder = physical_calculated_cost % n
        for i, arch in enumerate(architecture_order):
            alloc = base + (1 if i < remainder else 0)
            allocations[arch] = alloc

    sum_allocated = sum(allocations.values())
    allocation_valid = (
        not errors
        and sum_allocated == physical_calculated_cost
        and len(allocations) == len(architecture_order)
    )

    if not allocation_valid and not errors:
        errors.append(
            f"sum_allocated ({sum_allocated}) != "
            f"physical_calculated_cost ({physical_calculated_cost})"
        )

    return SharedSourceAllocation(
        shared_source_id=shared_source_id,
        physical_calculated_cost=physical_calculated_cost,
        allocations=allocations,
        sum_allocated=sum_allocated,
        allocation_valid=allocation_valid,
        errors=tuple(errors),
    )


# ── Physical accounting ───────────────────────────────────────────────


def compute_physical_accounting(
    call_accountings: list[CallAccounting],
) -> PhysicalPilotAccounting:
    """Compute physical pilot accounting with deduplication.

    Deduplicates by provider_invocation_id. Repeated identical references
    to one shared Worker-A receipt are permitted. Conflicting receipts
    using the same invocation ID are rejected.
    """
    errors = []
    seen: dict[str, int] = {}  # invocation_id -> cost
    for ca in call_accountings:
        iid = ca.provider_invocation_id
        if not iid:
            errors.append("empty provider_invocation_id")
            continue
        cost = ca.calculated_micro_usd_cost
        if cost is None:
            errors.append(f"null cost for {iid}")
            continue
        if iid in seen:
            if seen[iid] != cost:
                errors.append(
                    f"conflicting cost for {iid}: "
                    f"first={seen[iid]}, second={cost}"
                )
            # else: repeated identical reference — permitted
        else:
            seen[iid] = cost

    total = sum(seen.values())
    return PhysicalPilotAccounting(
        physical_invocation_ids=tuple(seen.keys()),
        physical_calculated_costs=seen,
        total_physical_calculated_cost=total,
        deduplication_valid=not errors,
        errors=tuple(errors),
    )


# ── Logical trajectory accounting ──────────────────────────────────────


def compute_logical_trajectory_accounting(
    trajectory_id: str,
    successor_call_ids: list[str],
    successor_costs: dict[str, int],
    allocated_worker_a_cost: int,
) -> LogicalTrajectoryAccounting:
    """Compute accounting for one logical trajectory."""
    errors = []
    successor_total = 0
    for sid in successor_call_ids:
        cost = successor_costs.get(sid)
        if cost is None:
            errors.append(f"missing successor cost for {sid}")
        else:
            successor_total += cost

    logical_cost = successor_total + allocated_worker_a_cost
    valid = not errors
    return LogicalTrajectoryAccounting(
        trajectory_id=trajectory_id,
        successor_call_ids=tuple(successor_call_ids),
        successor_calculated_cost=successor_total,
        allocated_worker_a_cost=allocated_worker_a_cost,
        logical_trajectory_cost=logical_cost,
        trajectory_accounting_valid=valid,
        errors=tuple(errors),
    )


# ── Aggregate invariants ──────────────────────────────────────────────


def compute_accounting_invariants(
    physical_accounting: PhysicalPilotAccounting,
    trajectory_accountings: list[LogicalTrajectoryAccounting],
    shared_allocations: list[SharedSourceAllocation],
) -> AccountingInvariants:
    """Compute aggregate invariant checks for the pilot."""
    errors = []

    total_logical = sum(
        ta.logical_trajectory_cost for ta in trajectory_accountings
    )
    total_physical = physical_accounting.total_physical_calculated_cost

    invariant_valid = total_logical == total_physical
    if not invariant_valid:
        errors.append(
            f"logical total ({total_logical}) != "
            f"physical total ({total_physical})"
        )

    # Verify each shared allocation
    for sa in shared_allocations:
        if not sa.allocation_valid:
            errors.append(
                f"invalid allocation for {sa.shared_source_id}: {sa.errors}"
            )

    return AccountingInvariants(
        total_physical_calculated_cost=total_physical,
        total_logical_trajectory_cost=total_logical,
        invariant_valid=invariant_valid,
        shared_source_allocations=tuple(shared_allocations),
        trajectory_accountings=tuple(trajectory_accountings),
        physical_accounting=physical_accounting,
        errors=tuple(errors),
    )
