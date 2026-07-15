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

REQUIRED_PRICE_CATEGORIES = frozenset({"uncached_input", "cached_input", "output"})


def ceiling_div(numerator: int, denominator: int) -> int:
    """Integer ceiling division. Returns (numerator + denominator - 1) // denominator."""
    if denominator <= 0:
        raise ValueError(f"denominator must be positive, got {denominator}")
    if numerator < 0:
        raise ValueError(f"numerator must be non-negative, got {numerator}")
    return (numerator + denominator - 1) // denominator


def _validate_prices(prices: dict[str, Any]) -> list[str]:
    """Validate the prices dict. Returns list of error strings."""
    errors = []
    if not isinstance(prices, dict):
        errors.append("prices must be a dict")
        return errors
    for cat in REQUIRED_PRICE_CATEGORIES:
        if cat not in prices:
            errors.append(f"missing price category: {cat}")
        else:
            val = prices[cat]
            if not isinstance(val, int) or isinstance(val, bool):
                errors.append(f"price '{cat}' must be an integer, got {type(val).__name__}")
            elif val < 0:
                errors.append(f"price '{cat}' must be non-negative, got {val}")
    return errors


def _safe_int(val: Any, name: str, errors: list[str]) -> int | None:
    """Safely coerce a value to int or None, appending errors on malformed input."""
    if val is None:
        return None
    if isinstance(val, bool):
        errors.append(f"{name} must be an integer or null, got bool")
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        errors.append(f"{name} must be an integer or null, got float")
        return None
    errors.append(f"{name} must be an integer or null, got {type(val).__name__}")
    return None


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

    # Validate prices
    price_errors = _validate_prices(prices)
    errors.extend(price_errors)

    # Validate cached_included is boolean
    if not isinstance(cached_included, bool):
        errors.append(f"cached_included must be a boolean, got {type(cached_included).__name__}")

    # Safely coerce token values — malformed types produce invalid accounting, not TypeError
    prompt_tokens_total = _safe_int(prompt_tokens_total, "prompt_tokens_total", errors)
    cached_input_tokens = _safe_int(cached_input_tokens, "cached_input_tokens", errors)
    completion_tokens = _safe_int(completion_tokens, "completion_tokens", errors)

    # Validate non-negative
    for name, val in [
        ("prompt_tokens_total", prompt_tokens_total),
        ("cached_input_tokens", cached_input_tokens),
        ("completion_tokens", completion_tokens),
    ]:
        if val is not None and val < 0:
            errors.append(f"{name} must be non-negative, got {val}")

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
    architecture_order: tuple[str, ...] | None = None,
) -> SharedSourceAllocation:
    """Allocate a shared Worker-A cost across architecture arms.

    Uses quotient/remainder allocation per contract §11.
    The only permitted allocation order is ALLOCATION_ORDER.
    """
    errors = []

    # Freeze allocation order — caller-selected alternate orders are rejected
    if architecture_order is not None and architecture_order != ALLOCATION_ORDER:
        errors.append(
            f"invalid allocation order: got {architecture_order}, "
            f"expected {ALLOCATION_ORDER}"
        )
    order = ALLOCATION_ORDER

    # Safely coerce cost — malformed types produce invalid accounting, not TypeError
    if isinstance(physical_calculated_cost, bool):
        errors.append("physical_calculated_cost must be an integer")
        physical_calculated_cost = 0
    elif not isinstance(physical_calculated_cost, int):
        errors.append(f"physical_calculated_cost must be an integer, got {type(physical_calculated_cost).__name__}")
        physical_calculated_cost = 0
    elif physical_calculated_cost < 0:
        errors.append("physical_calculated_cost must be non-negative")

    n = len(order)
    if n == 0:
        errors.append("architecture_order must not be empty")

    allocations: dict[str, int] = {}
    if not errors:
        base = physical_calculated_cost // n
        remainder = physical_calculated_cost % n
        for i, arch in enumerate(order):
            alloc = base + (1 if i < remainder else 0)
            allocations[arch] = alloc

    sum_allocated = sum(allocations.values())
    allocation_valid = (
        not errors
        and sum_allocated == physical_calculated_cost
        and len(allocations) == len(order)
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

    Two records using the same provider_invocation_id are identical only
    if all accounting fields agree — not merely their calculated cost.
    The fingerprint includes call_accounting_valid.
    """
    errors = []
    seen: dict[str, tuple] = {}
    # (cost, prompt, cached, uncached, completion, valid)
    for ca in call_accountings:
        iid = ca.provider_invocation_id
        if not iid:
            errors.append("empty provider_invocation_id")
            continue
        cost = ca.calculated_micro_usd_cost
        if cost is None:
            errors.append(f"null cost for {iid}")
            continue
        if cost < 0:
            errors.append(f"negative cost for {iid}: {cost}")
            continue
        # Invalid call with a non-null cost
        if not ca.call_accounting_valid and cost is not None:
            errors.append(f"invalid call {iid} has non-null cost {cost}")
            continue
        # Build full accounting fingerprint including validity state
        fingerprint = (
            cost,
            ca.prompt_tokens_total,
            ca.cached_input_tokens,
            ca.uncached_input_tokens,
            ca.completion_tokens,
            ca.call_accounting_valid,
        )
        if iid in seen:
            if seen[iid] != fingerprint:
                errors.append(
                    f"conflicting duplicate for {iid}: "
                    f"first={seen[iid]}, second={fingerprint}"
                )
            # else: repeated identical reference — permitted
        else:
            seen[iid] = fingerprint

    total = sum(fp[0] for fp in seen.values())
    return PhysicalPilotAccounting(
        physical_invocation_ids=tuple(seen.keys()),
        physical_calculated_costs={iid: fp[0] for iid, fp in seen.items()},
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
    """Compute accounting for one logical trajectory.

    Requires exactly three successor invocation IDs, three distinct non-empty
    IDs, a cost for every successor, all successor costs nonnegative, and a
    nonnegative allocated Worker-A cost.
    """
    errors = []

    # Validate exactly three successors
    if len(successor_call_ids) != 3:
        errors.append(
            f"expected exactly 3 successor call IDs, got {len(successor_call_ids)}"
        )

    # Validate distinct, non-empty IDs
    for sid in successor_call_ids:
        if not sid:
            errors.append("empty successor call ID")
    if len(set(successor_call_ids)) != len(successor_call_ids):
        errors.append("duplicate successor call IDs")

    # Safely coerce allocated_worker_a_cost — malformed types produce invalid accounting
    if isinstance(allocated_worker_a_cost, bool):
        errors.append("allocated_worker_a_cost must be an integer")
        allocated_worker_a_cost = 0
    elif not isinstance(allocated_worker_a_cost, int):
        errors.append(f"allocated_worker_a_cost must be an integer, got {type(allocated_worker_a_cost).__name__}")
        allocated_worker_a_cost = 0
    elif allocated_worker_a_cost < 0:
        errors.append(f"allocated_worker_a_cost must be non-negative, got {allocated_worker_a_cost}")

    successor_total = 0
    for sid in successor_call_ids:
        cost = successor_costs.get(sid)
        if cost is None:
            errors.append(f"missing successor cost for {sid}")
        else:
            if isinstance(cost, bool):
                errors.append(f"successor cost for {sid} must be an integer, got bool")
            elif not isinstance(cost, int):
                errors.append(f"successor cost for {sid} must be an integer, got {type(cost).__name__}")
            elif cost < 0:
                errors.append(f"successor cost for {sid} must be non-negative, got {cost}")
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
    """Compute aggregate invariant checks for the pilot.

    invariant_valid is true only when ALL of these are true:
    - Physical deduplication is valid.
    - Every physical call has call_accounting_valid = true.
    - Every trajectory accounting is valid.
    - Every shared-source allocation is valid.
    - Physical and logical totals are equal.
    """
    errors = []

    # 1. Physical deduplication must be valid
    if not physical_accounting.deduplication_valid:
        errors.append(
            f"physical deduplication invalid: {physical_accounting.errors}"
        )

    total_logical = sum(
        ta.logical_trajectory_cost for ta in trajectory_accountings
    )
    total_physical = physical_accounting.total_physical_calculated_cost

    # 2. Every trajectory accounting must be valid
    for ta in trajectory_accountings:
        if not ta.trajectory_accounting_valid:
            errors.append(
                f"invalid trajectory accounting for {ta.trajectory_id}: {ta.errors}"
            )

    # 3. Every shared-source allocation must be valid
    for sa in shared_allocations:
        if not sa.allocation_valid:
            errors.append(
                f"invalid allocation for {sa.shared_source_id}: {sa.errors}"
            )

    # 4. Physical and logical totals must be equal
    totals_match = total_logical == total_physical
    if not totals_match:
        errors.append(
            f"logical total ({total_logical}) != "
            f"physical total ({total_physical})"
        )

    invariant_valid = (
        physical_accounting.deduplication_valid
        and all(ta.trajectory_accounting_valid for ta in trajectory_accountings)
        and all(sa.allocation_valid for sa in shared_allocations)
        and totals_match
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
