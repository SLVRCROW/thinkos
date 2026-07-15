"""G1-B accounting, evidence, and invariant tests.

Standard library only. Zero network calls. Zero provider spend.
"""

from __future__ import annotations
import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the repo root is on sys.path
_repo_root = Path(__file__).resolve().parents[4]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from benchmarks.context_efficiency_v0.g1 import accounting
from benchmarks.context_efficiency_v0.g1 import evidence
from benchmarks.context_efficiency_v0.g1 import hashing as g1_hashing
from benchmarks.context_efficiency_v0.g1 import serialization
from benchmarks.context_efficiency_v0.g1 import schemas as g1_schemas


# ── Synthetic pricing fixtures ────────────────────────────────────────


PRICES_INCLUDED = {
    "uncached_input": 2_500_000,
    "cached_input": 1_250_000,
    "output": 10_000_000,
}

PRICES_EXCLUDED = {
    "uncached_input": 2_500_000,
    "cached_input": 1_250_000,
    "output": 10_000_000,
}


def _make_call(
    inv_id: str,
    prompt_total: int | None,
    cached: int | None,
    completion: int | None,
    cached_included: bool = True,
) -> accounting.CallAccounting:
    result = accounting.calculate_call_cost(
        prompt_total, cached, completion,
        PRICES_INCLUDED if cached_included else PRICES_EXCLUDED,
        cached_included,
    )
    return accounting.CallAccounting(
        provider_invocation_id=inv_id,
        prompt_tokens_total=result.prompt_tokens_total,
        cached_input_tokens=result.cached_input_tokens,
        uncached_input_tokens=result.uncached_input_tokens,
        completion_tokens=result.completion_tokens,
        calculated_micro_usd_cost=result.calculated_micro_usd_cost,
        call_accounting_valid=result.call_accounting_valid,
        errors=result.errors,
    )


def _make_synthetic_receipt(
    inv_id: str,
    cost: int | None = 1000,
    worker_label: str = "A",
    stage: int = 1,
) -> dict:
    """Build a minimal valid ProviderCallReceipt dict for testing.

    The receipt_id is computed from the canonical hash of the receipt
    content (excluding receipt_id itself) per §21 self-exclusion rules.
    """
    receipt = {
        "pilot_id": "pilot-test",
        "run_id": "run-001",
        "provider_invocation_id": inv_id,
        "attempt_index": 0,
        "trajectory_id": "traj-001",
        "logical_session_id": "sess-001",
        "model_session_id": "ms-001",
        "worker_label": worker_label,
        "stage": stage,
        "requested_provider": "test-provider",
        "requested_model": "test-model",
        "returned_model": "test-model",
        "model_identity_valid": True,
        "provider_request_id": "req-001",
        "request_dispatched": True,
        "temperature_milli": 0,
        "max_tokens": 1000,
        "system_prompt_sha256": "a" * 64,
        "prompt_sha256": "b" * 64,
        "tool_definitions_sha256": None,
        "prompt_tokens_total": 500,
        "cached_input_tokens": 100,
        "uncached_input_tokens": 400,
        "completion_tokens": 150,
        "provider_usage_status": "reported",
        "response_sha256": "c" * 64,
        "provider_finish_reason": "stop",
        "response_present": True,
        "sanitized_error_sha256": None,
        "normalized_execution_status": "completed",
        "start_timestamp": "2026-01-01T00:00:00Z",
        "end_timestamp": "2026-01-01T00:00:10Z",
        "duration_ms": 10000,
        "calculated_micro_usd_cost": cost,
        "provider_reported_cost_micro_usd": cost,
        "pricing_source": "test",
        "call_accounting_valid": True,
        "raw_prompt_sha256": "d" * 64,
        "raw_response_sha256": "e" * 64,
        "shared_source_id": None,
        "parent_receipt_ids": [],
        "tool_call_receipt_ids": [],
        "contamination_flags": [],
        "warnings": [],
    }
    computed_id = g1_hashing.compute_receipt_hash(receipt)
    receipt["receipt_id"] = computed_id
    return receipt


def _make_synthetic_pilot_data():
    """Build synthetic data for a 6-trajectory, 2-shared-source pilot.

    Returns (shared_sources, trajectories) where shared_sources maps
    condition name to a valid ProviderCallReceipt dict, and trajectories
    maps trajectory_id to a dict with worker receipts and a reference.
    """
    shared_sources = {
        "clean": _make_synthetic_receipt("shared-clean", 5000),
        "drift": _make_synthetic_receipt("shared-drift", 4000),
    }

    clean_receipt_id = shared_sources["clean"]["receipt_id"]
    drift_receipt_id = shared_sources["drift"]["receipt_id"]

    trajectories = {}
    for arch in ("stateless", "summary", "verified_state"):
        for cond in ("clean", "drift"):
            tid = f"A-{cond}-{arch}"
            trajectories[tid] = {
                f"worker_{w}_receipt": _make_synthetic_receipt(
                    f"{arch}-{cond}-{w}",
                    1000,
                    worker_label=w,
                )
                for w in ("B", "C", "D")
            }
            receipt_id = clean_receipt_id if cond == "clean" else drift_receipt_id
            ref_content = {
                "shared_source_id": f"shared-{cond}-001",
                "provider_invocation_id": f"shared-{cond}",
                "provider_receipt_id": receipt_id,
                "checkpoint_receipt_id": f"chk-{cond}-001",
                "condition": cond,
                "allocated_shared_source_cost": 1667,
            }
            ref_hash = g1_hashing.compute_sha256(
                serialization.canonical_json(ref_content)
            )
            ref_content["reference_hash"] = ref_hash
            trajectories[tid]["worker_a_shared_source_ref"] = ref_content

    return shared_sources, trajectories


# ── 1. Ceiling division ──────────────────────────────────────────────


class TestCeilingDivision(unittest.TestCase):
    """Ceiling division edge cases."""

    def test_exact_division(self):
        self.assertEqual(accounting.ceiling_div(1_000_000, 1_000_000), 1)

    def test_one_token_rounds_up(self):
        self.assertEqual(accounting.ceiling_div(1, 1_000_000), 1)

    def test_zero_tokens_cost_zero(self):
        self.assertEqual(accounting.ceiling_div(0, 1_000_000), 0)

    def test_large_count(self):
        self.assertEqual(accounting.ceiling_div(5_000_000, 1_000_000), 5)

    def test_999999_rounds_up(self):
        self.assertEqual(accounting.ceiling_div(999_999, 1_000_000), 1)

    def test_rejects_negative_numerator(self):
        with self.assertRaises(ValueError):
            accounting.ceiling_div(-1, 1_000_000)

    def test_rejects_zero_denominator(self):
        with self.assertRaises(ValueError):
            accounting.ceiling_div(100, 0)


# ── 2. Cost calculation ──────────────────────────────────────────────


class TestCostCalculation(unittest.TestCase):
    """Per-call cost calculation with cached-token decomposition."""

    def test_cached_included_basic(self):
        result = _make_call("inv-1", 500, 100, 150, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertEqual(result.uncached_input_tokens, 400)
        self.assertIsNotNone(result.calculated_micro_usd_cost)
        self.assertGreater(result.calculated_micro_usd_cost, 0)

    def test_cached_excluded_basic(self):
        result = _make_call("inv-1", 500, 100, 150, cached_included=False)
        self.assertTrue(result.call_accounting_valid)
        self.assertEqual(result.uncached_input_tokens, 500)
        self.assertIsNotNone(result.calculated_micro_usd_cost)

    def test_zero_tokens_cost_zero(self):
        result = _make_call("inv-0", 0, 0, 0, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertEqual(result.calculated_micro_usd_cost, 0)

    def test_null_prompt_tokens(self):
        result = _make_call("inv-null", None, 0, 150, cached_included=True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_null_completion_tokens(self):
        result = _make_call("inv-null-c", 500, 0, None, cached_included=True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_negative_decomposition_rejected(self):
        result = _make_call("inv-neg", 100, 200, 150, cached_included=True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)
        self.assertTrue(any("negative" in e or "exceeds" in e for e in result.errors))

    def test_explicit_zero_cache(self):
        result = _make_call("inv-no-cache", 500, 0, 150, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertEqual(result.uncached_input_tokens, 500)

    def test_null_cache_unknown(self):
        result = _make_call("inv-unk-cache", 500, None, 150, cached_included=True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_single_token_rounds_up(self):
        result = _make_call("inv-1tok", 1, 0, 0, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertIsNotNone(result.calculated_micro_usd_cost)
        self.assertGreater(result.calculated_micro_usd_cost, 0)

    def test_uncached_only(self):
        result = _make_call("inv-uncached", 1000, 0, 0, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertIsNotNone(result.calculated_micro_usd_cost)

    def test_output_only(self):
        result = _make_call("inv-out", 0, 0, 1000, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertIsNotNone(result.calculated_micro_usd_cost)

    def test_negative_prompt_tokens_rejected(self):
        result = _make_call("inv-neg-p", -1, 0, 150)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_negative_cached_tokens_rejected(self):
        result = _make_call("inv-neg-c", 500, -1, 150)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_negative_completion_tokens_rejected(self):
        result = _make_call("inv-neg-co", 500, 0, -1)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_negative_prices_rejected(self):
        bad_prices = {"uncached_input": -1, "cached_input": 1_250_000, "output": 10_000_000}
        result = accounting.calculate_call_cost(500, 100, 150, bad_prices, True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_missing_price_categories_rejected(self):
        bad_prices = {"uncached_input": 2_500_000, "cached_input": 1_250_000}
        result = accounting.calculate_call_cost(500, 100, 150, bad_prices, True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_non_integer_prices_rejected(self):
        bad_prices = {"uncached_input": "2.5", "cached_input": 1_250_000, "output": 10_000_000}
        result = accounting.calculate_call_cost(500, 100, 150, bad_prices, True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_non_integer_token_counts_rejected(self):
        result = accounting.calculate_call_cost(500.0, 100, 150, PRICES_INCLUDED, True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_non_boolean_cached_included_rejected(self):
        result = accounting.calculate_call_cost(500, 100, 150, PRICES_INCLUDED, "yes")
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_null_cost_not_converted_to_zero(self):
        result = _make_call("inv-null-cost", None, None, None)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)
        self.assertNotEqual(result.calculated_micro_usd_cost, 0)

    def test_malformed_float_cost_returns_invalid_not_typeerror(self):
        result = accounting.calculate_call_cost(500.0, 100, 150, PRICES_INCLUDED, True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)


# ── 3. Shared-source allocation ───────────────────────────────────────


class TestSharedSourceAllocation(unittest.TestCase):
    """Shared Worker-A cost allocation per contract §11."""

    def test_remainder_0(self):
        result = accounting.allocate_shared_cost("src-0", 60)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations, {
            "stateless": 20, "summary": 20, "verified_state": 20,
        })
        self.assertEqual(result.sum_allocated, 60)

    def test_remainder_1(self):
        result = accounting.allocate_shared_cost("src-1", 61)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations["stateless"], 21)
        self.assertEqual(result.allocations["summary"], 20)
        self.assertEqual(result.allocations["verified_state"], 20)
        self.assertEqual(result.sum_allocated, 61)

    def test_remainder_2(self):
        result = accounting.allocate_shared_cost("src-2", 62)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations["stateless"], 21)
        self.assertEqual(result.allocations["summary"], 21)
        self.assertEqual(result.allocations["verified_state"], 20)
        self.assertEqual(result.sum_allocated, 62)

    def test_zero_cost(self):
        result = accounting.allocate_shared_cost("src-zero", 0)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations, {
            "stateless": 0, "summary": 0, "verified_state": 0,
        })

    def test_small_cost(self):
        result = accounting.allocate_shared_cost("src-small", 1)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations["stateless"], 1)
        self.assertEqual(result.allocations["summary"], 0)
        self.assertEqual(result.allocations["verified_state"], 0)

    def test_rejects_negative_cost(self):
        result = accounting.allocate_shared_cost("src-neg", -1)
        self.assertFalse(result.allocation_valid)

    def test_sum_equals_physical(self):
        for cost in [0, 1, 2, 3, 4, 5, 10, 100, 1000]:
            result = accounting.allocate_shared_cost("src", cost)
            self.assertEqual(result.sum_allocated, cost)

    def test_rejects_alternate_architecture_order(self):
        result = accounting.allocate_shared_cost(
            "src-alt", 60,
            architecture_order=("summary", "stateless", "verified_state"),
        )
        self.assertFalse(result.allocation_valid)
        self.assertTrue(any("invalid allocation order" in e for e in result.errors))

    def test_rejects_partial_architecture_order(self):
        result = accounting.allocate_shared_cost(
            "src-partial", 60,
            architecture_order=("stateless", "summary"),
        )
        self.assertFalse(result.allocation_valid)
        self.assertTrue(any("invalid allocation order" in e for e in result.errors))

    def test_malformed_float_cost_returns_invalid_not_typeerror(self):
        result = accounting.allocate_shared_cost("src-float", 60.0)
        self.assertFalse(result.allocation_valid)


# ── 4. Physical accounting ───────────────────────────────────────────


class TestPhysicalAccounting(unittest.TestCase):
    """Physical pilot accounting with deduplication."""

    def test_basic_deduplication(self):
        calls = [
            _make_call("shared-1", 500, 100, 150),
            _make_call("shared-1", 500, 100, 150),
            _make_call("unique-1", 300, 0, 100),
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertTrue(result.deduplication_valid)
        self.assertEqual(len(result.physical_invocation_ids), 2)

    def test_conflicting_ids_rejected(self):
        calls = [
            _make_call("conflict-1", 500, 100, 150),
            _make_call("conflict-1", 100, 0, 50),
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertFalse(result.deduplication_valid)
        self.assertTrue(any("conflicting" in e for e in result.errors))

    def test_null_cost_rejected(self):
        calls = [
            accounting.CallAccounting(
                provider_invocation_id="null-cost",
                prompt_tokens_total=None, cached_input_tokens=None,
                uncached_input_tokens=None, completion_tokens=None,
                calculated_micro_usd_cost=None,
                call_accounting_valid=False,
            ),
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertFalse(result.deduplication_valid)
        self.assertTrue(any("null cost" in e for e in result.errors))

    def test_empty_invocation_id_rejected(self):
        calls = [
            accounting.CallAccounting(
                provider_invocation_id="",
                prompt_tokens_total=0, cached_input_tokens=0,
                uncached_input_tokens=0, completion_tokens=0,
                calculated_micro_usd_cost=0,
                call_accounting_valid=True,
            ),
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertFalse(result.deduplication_valid)

    def test_total_is_sum(self):
        calls = [
            _make_call("a", 500, 100, 150),
            _make_call("b", 300, 0, 100),
            _make_call("c", 1000, 200, 500),
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertTrue(result.deduplication_valid)
        expected = sum(c.calculated_micro_usd_cost or 0 for c in calls)
        self.assertEqual(result.total_physical_calculated_cost, expected)

    def test_same_invocation_id_different_tokens_rejected(self):
        call1 = accounting.CallAccounting(
            provider_invocation_id="dup",
            prompt_tokens_total=500, cached_input_tokens=100,
            uncached_input_tokens=400, completion_tokens=150,
            calculated_micro_usd_cost=5,
            call_accounting_valid=True,
        )
        call2 = accounting.CallAccounting(
            provider_invocation_id="dup",
            prompt_tokens_total=100, cached_input_tokens=0,
            uncached_input_tokens=100, completion_tokens=50,
            calculated_micro_usd_cost=5,
            call_accounting_valid=True,
        )
        result = accounting.compute_physical_accounting([call1, call2])
        self.assertFalse(result.deduplication_valid)
        self.assertTrue(any("conflicting" in e for e in result.errors))

    def test_negative_calculated_cost_rejected(self):
        calls = [
            accounting.CallAccounting(
                provider_invocation_id="neg-cost",
                prompt_tokens_total=500, cached_input_tokens=100,
                uncached_input_tokens=400, completion_tokens=150,
                calculated_micro_usd_cost=-5,
                call_accounting_valid=True,
            ),
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertFalse(result.deduplication_valid)
        self.assertTrue(any("negative" in e for e in result.errors))

    def test_invalid_call_with_non_null_cost_rejected(self):
        calls = [
            accounting.CallAccounting(
                provider_invocation_id="invalid-cost",
                prompt_tokens_total=None, cached_input_tokens=None,
                uncached_input_tokens=None, completion_tokens=None,
                calculated_micro_usd_cost=100,
                call_accounting_valid=False,
            ),
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertFalse(result.deduplication_valid)
        self.assertTrue(any("invalid call" in e for e in result.errors))

    def test_duplicate_fingerprint_includes_validity_state(self):
        """The duplicate fingerprint must include validity state.

        Two calls with same invocation ID, same tokens, same cost, but
        different validity state must be detected as conflicting.
        """
        call1 = accounting.CallAccounting(
            provider_invocation_id="dup",
            prompt_tokens_total=500, cached_input_tokens=100,
            uncached_input_tokens=400, completion_tokens=150,
            calculated_micro_usd_cost=5,
            call_accounting_valid=True,
        )
        call2 = accounting.CallAccounting(
            provider_invocation_id="dup",
            prompt_tokens_total=500, cached_input_tokens=100,
            uncached_input_tokens=400, completion_tokens=150,
            calculated_micro_usd_cost=5,
            call_accounting_valid=False,
        )
        result = accounting.compute_physical_accounting([call1, call2])
        self.assertFalse(result.deduplication_valid)
        self.assertTrue(
            any("invalid call" in e for e in result.errors),
            f"Expected 'invalid call' error, got: {result.errors}"
        )


# ── 5. Logical trajectory accounting ─────────────────────────────────


class TestLogicalTrajectoryAccounting(unittest.TestCase):
    """Logical trajectory accounting."""

    def test_basic_trajectory(self):
        result = accounting.compute_logical_trajectory_accounting(
            "A-clean-stateless",
            ["b-1", "c-1", "d-1"],
            {"b-1": 100, "c-1": 200, "d-1": 150},
            50,
        )
        self.assertTrue(result.trajectory_accounting_valid)
        self.assertEqual(result.successor_calculated_cost, 450)
        self.assertEqual(result.logical_trajectory_cost, 500)

    def test_missing_successor(self):
        result = accounting.compute_logical_trajectory_accounting(
            "A-clean-stateless",
            ["b-1", "c-1", "missing"],
            {"b-1": 100, "c-1": 200},
            50,
        )
        self.assertFalse(result.trajectory_accounting_valid)

    def test_requires_exactly_three_successors(self):
        result = accounting.compute_logical_trajectory_accounting(
            "tid", ["a", "b"], {"a": 100, "b": 200}, 50,
        )
        self.assertFalse(result.trajectory_accounting_valid)
        self.assertTrue(any("exactly 3" in e for e in result.errors))

    def test_requires_three_distinct_ids(self):
        result = accounting.compute_logical_trajectory_accounting(
            "tid", ["a", "a", "a"], {"a": 100}, 50,
        )
        self.assertFalse(result.trajectory_accounting_valid)
        self.assertTrue(any("duplicate" in e for e in result.errors))

    def test_rejects_empty_successor_id(self):
        result = accounting.compute_logical_trajectory_accounting(
            "tid", ["a", "", "b"], {"a": 100, "b": 200}, 50,
        )
        self.assertFalse(result.trajectory_accounting_valid)
        self.assertTrue(any("empty" in e for e in result.errors))

    def test_rejects_negative_successor_cost(self):
        result = accounting.compute_logical_trajectory_accounting(
            "tid", ["a", "b", "c"], {"a": 100, "b": -1, "c": 200}, 50,
        )
        self.assertFalse(result.trajectory_accounting_valid)
        self.assertTrue(any("non-negative" in e for e in result.errors))

    def test_rejects_negative_worker_a_cost(self):
        result = accounting.compute_logical_trajectory_accounting(
            "tid", ["a", "b", "c"], {"a": 100, "b": 200, "c": 150}, -1,
        )
        self.assertFalse(result.trajectory_accounting_valid)
        self.assertTrue(any("non-negative" in e for e in result.errors))

    def test_malformed_float_worker_a_cost_returns_invalid_not_typeerror(self):
        result = accounting.compute_logical_trajectory_accounting(
            "tid", ["a", "b", "c"], {"a": 100, "b": 200, "c": 150}, 50.0,
        )
        self.assertFalse(result.trajectory_accounting_valid)


# ── 6. Aggregate invariants ──────────────────────────────────────────


class TestAccountingInvariants(unittest.TestCase):
    """Aggregate invariant checks with synthetic full-pilot fixture."""

    def _build_synthetic_pilot(self):
        shared_clean = _make_call("shared-clean", 1000, 200, 500)
        shared_drift = _make_call("shared-drift", 800, 100, 400)

        successors = {}
        for arch in ("stateless", "summary", "verified_state"):
            for cond in ("clean", "drift"):
                for worker in ("B", "C", "D"):
                    iid = f"{arch}-{cond}-{worker}"
                    successors[iid] = _make_call(iid, 500, 100, 200)

        all_calls = [shared_clean, shared_drift] + list(successors.values())
        physical = accounting.compute_physical_accounting(all_calls)

        alloc_clean = accounting.allocate_shared_cost(
            "shared-clean",
            shared_clean.calculated_micro_usd_cost or 0,
        )
        alloc_drift = accounting.allocate_shared_cost(
            "shared-drift",
            shared_drift.calculated_micro_usd_cost or 0,
        )

        traj_accountings = []
        for arch in ("stateless", "summary", "verified_state"):
            for cond in ("clean", "drift"):
                tid = f"A-{cond}-{arch}"
                succ_ids = [f"{arch}-{cond}-{w}" for w in ("B", "C", "D")]
                succ_costs = {
                    sid: successors[sid].calculated_micro_usd_cost or 0
                    for sid in succ_ids
                }
                alloc = alloc_clean if cond == "clean" else alloc_drift
                ta = accounting.compute_logical_trajectory_accounting(
                    tid, succ_ids, succ_costs,
                    alloc.allocations.get(arch, 0),
                )
                traj_accountings.append(ta)

        invariants = accounting.compute_accounting_invariants(
            physical, traj_accountings,
            [alloc_clean, alloc_drift],
        )
        return invariants, physical, traj_accountings

    def test_20_physical_invocations(self):
        invariants, physical, _ = self._build_synthetic_pilot()
        self.assertEqual(len(physical.physical_invocation_ids), 20)

    def test_6_trajectories(self):
        invariants, _, trajs = self._build_synthetic_pilot()
        self.assertEqual(len(trajs), 6)

    def test_2_shared_sources(self):
        invariants, _, _ = self._build_synthetic_pilot()
        self.assertEqual(len(invariants.shared_source_allocations), 2)

    def test_invariant_valid(self):
        invariants, _, _ = self._build_synthetic_pilot()
        self.assertTrue(
            invariants.invariant_valid,
            f"Physical={invariants.total_physical_calculated_cost}, "
            f"Logical={invariants.total_logical_trajectory_cost}, "
            f"Errors={invariants.errors}"
        )

    def test_invariant_equality(self):
        invariants, _, _ = self._build_synthetic_pilot()
        self.assertEqual(
            invariants.total_physical_calculated_cost,
            invariants.total_logical_trajectory_cost,
        )

    def test_missing_or_invalid_sets_validity_false(self):
        bad_call = accounting.CallAccounting(
            provider_invocation_id="bad",
            prompt_tokens_total=None, cached_input_tokens=None,
            uncached_input_tokens=None, completion_tokens=None,
            calculated_micro_usd_cost=None,
            call_accounting_valid=False,
        )
        physical = accounting.compute_physical_accounting([bad_call])
        self.assertFalse(physical.deduplication_valid)

    def test_invalid_physical_fails_aggregate(self):
        invariants, physical, trajs = self._build_synthetic_pilot()
        bad_physical = accounting.PhysicalPilotAccounting(
            physical_invocation_ids=physical.physical_invocation_ids,
            physical_calculated_costs=physical.physical_calculated_costs,
            total_physical_calculated_cost=physical.total_physical_calculated_cost,
            deduplication_valid=False,
            errors=("simulated dedup failure",),
        )
        result = accounting.compute_accounting_invariants(
            bad_physical, list(trajs),
            list(invariants.shared_source_allocations),
        )
        self.assertFalse(result.invariant_valid)
        self.assertTrue(any("deduplication" in e for e in result.errors))

    def test_invalid_trajectory_fails_aggregate(self):
        invariants, physical, trajs = self._build_synthetic_pilot()
        bad_trajs = list(trajs)
        bad_trajs[0] = accounting.LogicalTrajectoryAccounting(
            trajectory_id="bad-traj",
            successor_call_ids=("a",),
            successor_calculated_cost=0,
            allocated_worker_a_cost=0,
            logical_trajectory_cost=0,
            trajectory_accounting_valid=False,
            errors=("simulated failure",),
        )
        result = accounting.compute_accounting_invariants(
            physical, bad_trajs,
            list(invariants.shared_source_allocations),
        )
        self.assertFalse(result.invariant_valid)
        self.assertTrue(any("invalid trajectory" in e for e in result.errors))

    def test_invalid_shared_allocation_fails_aggregate(self):
        invariants, physical, trajs = self._build_synthetic_pilot()
        bad_alloc = accounting.SharedSourceAllocation(
            shared_source_id="bad",
            physical_calculated_cost=100,
            allocations={},
            sum_allocated=0,
            allocation_valid=False,
            errors=("simulated failure",),
        )
        result = accounting.compute_accounting_invariants(
            physical, list(trajs), [bad_alloc],
        )
        self.assertFalse(result.invariant_valid)
        self.assertTrue(any("invalid allocation" in e for e in result.errors))

    def test_duplicate_invocation_id_with_equal_cost_different_tokens_fails_aggregate(self):
        call1 = accounting.CallAccounting(
            provider_invocation_id="dup",
            prompt_tokens_total=500, cached_input_tokens=100,
            uncached_input_tokens=400, completion_tokens=150,
            calculated_micro_usd_cost=5,
            call_accounting_valid=True,
        )
        call2 = accounting.CallAccounting(
            provider_invocation_id="dup",
            prompt_tokens_total=100, cached_input_tokens=0,
            uncached_input_tokens=100, completion_tokens=50,
            calculated_micro_usd_cost=5,
            call_accounting_valid=True,
        )
        physical = accounting.compute_physical_accounting([call1, call2])
        self.assertFalse(physical.deduplication_valid)
        traj = accounting.compute_logical_trajectory_accounting(
            "tid", ["a", "b", "c"], {"a": 5, "b": 0, "c": 0}, 0,
        )
        alloc = accounting.allocate_shared_cost("src", 0)
        result = accounting.compute_accounting_invariants(
            physical, [traj], [alloc],
        )
        self.assertFalse(result.invariant_valid)


# ── 7. Evidence packet construction ───────────────────────────────────


class TestEvidencePacket(unittest.TestCase):
    """Evidence-packet construction and validation."""

    def test_valid_pilot_packet(self):
        """A valid synthetic pilot packet must pass validation."""
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test-001",
                shared, trajs,
            )
            self.assertTrue(
                result.packet_valid,
                f"Packet validation failed: {result.errors}"
            )

    def test_shared_source_count(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertEqual(result.shared_source_count, 2)

    def test_trajectory_count(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertEqual(result.trajectory_count, 6)

    def test_reference_count(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertEqual(result.reference_count, 6)

    def test_rejects_absolute_path(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            shared["../evil"] = _make_synthetic_receipt("evil")
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_rejects_copied_worker_a_receipt(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp) / "pilot-test" / "trajectories" / "A-clean-stateless" / "worker_A"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "provider_call_receipt.json").write_text("{}")
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_rejects_wrong_shared_source_count(self):
        shared, trajs = _make_synthetic_pilot_data()
        del shared["drift"]
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_canonical_hash_trajectory_manifest(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertTrue(result.packet_valid)

    def test_empty_reference_map_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        for tid in trajs:
            del trajs[tid]["worker_a_shared_source_ref"]
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_missing_clean_references_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        for tid in list(trajs.keys()):
            if "clean" in tid:
                del trajs[tid]
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_missing_drift_references_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        for tid in list(trajs.keys()):
            if "drift" in tid:
                del trajs[tid]
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_reference_ids_that_do_not_resolve_fail(self):
        shared, trajs = _make_synthetic_pilot_data()
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"]["condition"] = "nonexistent"
        ref_content = {k: v for k, v in trajs["A-clean-stateless"]["worker_a_shared_source_ref"].items() if k != "reference_hash"}
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"]["reference_hash"] = g1_hashing.compute_sha256(
            serialization.canonical_json(ref_content)
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_missing_required_s20_files_fail(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            shared_minus_clean = dict(shared)
            del shared_minus_clean["clean"]
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared_minus_clean, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_missing_hashes_fail(self):
        shared, trajs = _make_synthetic_pilot_data()
        del trajs["A-clean-stateless"]["worker_a_shared_source_ref"]["reference_hash"]
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_mismatched_hashes_fail(self):
        shared, trajs = _make_synthetic_pilot_data()
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"]["reference_hash"] = "x" * 64
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_absolute_path_creates_no_files(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            result = evidence.build_pilot_evidence(
                run_root, "/etc/evil", shared, trajs,
            )
            self.assertFalse(result.packet_valid)
            for f in run_root.rglob("*"):
                self.assertTrue(str(f).startswith(str(run_root)))

    def test_traversal_attempt_creates_no_files(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            result = evidence.build_pilot_evidence(
                run_root, "../../evil", shared, trajs,
            )
            self.assertFalse(result.packet_valid)
            for f in run_root.rglob("*"):
                self.assertTrue(str(f).startswith(str(run_root)))

    def test_symlink_escape_attempt_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            outside = Path(tempfile.mkdtemp())
            escape_link = run_root / "pilot-test" / "shared_sources" / "clean"
            escape_link.parent.mkdir(parents=True, exist_ok=True)
            try:
                escape_link.symlink_to(outside, target_is_directory=True)
                result = evidence.build_pilot_evidence(
                    run_root, "pilot-test", shared, trajs,
                )
                self.assertFalse(result.packet_valid)
            finally:
                import shutil
                shutil.rmtree(outside, ignore_errors=True)
                if escape_link.exists():
                    escape_link.unlink()

    def test_complete_synthetic_packet_reconstructs_successfully(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertTrue(
                result.packet_valid,
                f"Packet validation failed: {result.errors}"
            )
            self.assertEqual(result.shared_source_count, 2)
            self.assertEqual(result.trajectory_count, 6)
            self.assertEqual(result.reference_count, 6)

            pilot_dir = Path(tmp) / "pilot-test"

            for fname in evidence.REQUIRED_PILOT_FILES:
                self.assertTrue(
                    (pilot_dir / fname).exists(),
                    f"Missing pilot file: {fname}"
                )

            for cond in ("clean", "drift"):
                cond_dir = pilot_dir / "shared_sources" / cond
                self.assertTrue(cond_dir.exists(), f"Missing shared source dir: {cond}")
                for fname in evidence.REQUIRED_SHARED_SOURCE_FILES:
                    self.assertTrue(
                        (cond_dir / fname).exists(),
                        f"Missing shared source file: {cond}/{fname}"
                    )
                for dname in evidence.REQUIRED_SHARED_SOURCE_DIRS:
                    self.assertTrue(
                        (cond_dir / dname).exists(),
                        f"Missing shared source dir: {cond}/{dname}"
                    )

            for arch in ("stateless", "summary", "verified_state"):
                for cond in ("clean", "drift"):
                    tid = f"A-{cond}-{arch}"
                    tdir = pilot_dir / "trajectories" / tid
                    self.assertTrue(tdir.exists(), f"Missing trajectory dir: {tid}")
                    for fname in evidence.REQUIRED_TRAJECTORY_FILES:
                        self.assertTrue(
                            (tdir / fname).exists(),
                            f"Missing trajectory file: {tid}/{fname}"
                        )
                    for dname in evidence.REQUIRED_TRAJECTORY_DIRS:
                        wdir = tdir / dname
                        self.assertTrue(wdir.exists(), f"Missing trajectory dir: {tid}/{dname}")
                        for wfname in evidence.REQUIRED_WORKER_FILES:
                            self.assertTrue(
                                (wdir / wfname).exists(),
                                f"Missing worker file: {tid}/{dname}/{wfname}"
                            )
                        for wdname in evidence.REQUIRED_WORKER_DIRS:
                            self.assertTrue(
                                (wdir / wdname).exists(),
                                f"Missing worker dir: {tid}/{dname}/{wdname}"
                            )

    def test_missing_each_required_file_type_individually(self):
        shared, trajs = _make_synthetic_pilot_data()

        with tempfile.TemporaryDirectory() as tmp:
            shared_minus = dict(shared)
            del shared_minus["clean"]
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared_minus, trajs,
            )
            self.assertFalse(result.packet_valid)

        with tempfile.TemporaryDirectory() as tmp:
            trajs_minus = dict(trajs)
            del trajs_minus["A-clean-stateless"]
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs_minus,
            )
            self.assertFalse(result.packet_valid)

    def test_reference_mandatory_fields_absent(self):
        shared, trajs = _make_synthetic_pilot_data()
        mandatory_fields = [
            "shared_source_id", "provider_invocation_id", "provider_receipt_id",
            "checkpoint_receipt_id", "condition", "allocated_shared_source_cost",
        ]
        for field in mandatory_fields:
            with tempfile.TemporaryDirectory() as tmp:
                trajs_copy = {k: dict(v) for k, v in trajs.items()}
                ref = dict(trajs_copy["A-clean-stateless"]["worker_a_shared_source_ref"])
                del ref[field]
                ref_content = {k: v for k, v in ref.items() if k != "reference_hash"}
                ref["reference_hash"] = g1_hashing.compute_sha256(
                    serialization.canonical_json(ref_content)
                )
                trajs_copy["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
                result = evidence.build_pilot_evidence(
                    Path(tmp), "pilot-test", shared, trajs_copy,
                )
                self.assertFalse(
                    result.packet_valid,
                    f"Should fail with missing field: {field}"
                )

    def test_mismatched_provider_invocation_id_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["provider_invocation_id"] = "wrong-invocation-id"
        ref_content = {k: v for k, v in ref.items() if k != "reference_hash"}
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json(ref_content))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_mismatched_provider_receipt_id_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["provider_receipt_id"] = "wrong-receipt-id"
        ref_content = {k: v for k, v in ref.items() if k != "reference_hash"}
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json(ref_content))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_mismatched_checkpoint_receipt_id_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["checkpoint_receipt_id"] = "wrong-checkpoint-id"
        ref_content = {k: v for k, v in ref.items() if k != "reference_hash"}
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json(ref_content))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_mismatched_condition_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["condition"] = "drift"
        ref_content = {k: v for k, v in ref.items() if k != "reference_hash"}
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json(ref_content))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_mismatched_shared_source_id_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["shared_source_id"] = "wrong-shared-source"
        ref_content = {k: v for k, v in ref.items() if k != "reference_hash"}
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json(ref_content))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)

    def test_preflight_rejects_existing_files(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            pilot_dir = Path(tmp) / "pilot-test"
            pilot_dir.mkdir(parents=True, exist_ok=True)
            (pilot_dir / "pilot_config.json").write_text("{}")
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)
            self.assertTrue(any("already exists" in e for e in result.errors))

    def test_preflight_rejects_existing_dirs(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            cond_dir = Path(tmp) / "pilot-test" / "shared_sources" / "clean"
            cond_dir.mkdir(parents=True, exist_ok=True)
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)
            self.assertTrue(any("already exists" in e for e in result.errors))

    def test_preflight_leaves_destination_unchanged_after_failure(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            pilot_dir = Path(tmp) / "pilot-test"
            pilot_dir.mkdir(parents=True, exist_ok=True)
            (pilot_dir / "pilot_config.json").write_text("original")
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)
            self.assertTrue((pilot_dir / "pilot_config.json").exists())
            self.assertEqual(
                (pilot_dir / "pilot_config.json").read_text(),
                "original"
            )

    def test_sibling_path_sharing_prefix_rejected(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            sibling = run_root.parent / f"{run_root.name}-sibling"
            sibling.mkdir(exist_ok=True)
            try:
                result = evidence.build_pilot_evidence(
                    run_root, "../sibling", shared, trajs,
                )
                self.assertFalse(result.packet_valid)
            finally:
                sibling.rmdir()


# ── 8. Network-denial tests ───────────────────────────────────────────


class TestNetworkDenial(unittest.TestCase):
    """Prove G1-B makes zero network or provider calls."""

    def test_no_network_imports(self):
        suspicious = ["requests", "urllib3", "httpx", "openai", "anthropic",
                      "ollama", "http.client"]
        g1_dir = _repo_root / "benchmarks" / "context_efficiency_v0" / "g1"
        for py_file in g1_dir.glob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            content = py_file.read_text()
            for mod in suspicious:
                if f"import {mod}" in content or f"from {mod}" in content:
                    self.fail(f"{py_file.name} imports {mod}")

    def test_active_network_denial(self):
        _original_create = socket.create_connection
        _original_connect = socket.socket.connect
        calls = []

        def _deny(*args, **kwargs):
            calls.append(("denied", args, kwargs))
            raise OSError("Network denied by G1-B test")

        socket.create_connection = _deny
        socket.socket.connect = _deny

        try:
            self.assertEqual(accounting.ceiling_div(100, 1_000_000), 1)

            result = accounting.calculate_call_cost(
                500, 100, 150, PRICES_INCLUDED, True,
            )
            self.assertTrue(result.call_accounting_valid)

            alloc = accounting.allocate_shared_cost("src", 60)
            self.assertTrue(alloc.allocation_valid)

            calls_list = [_make_call("a", 500, 100, 150)]
            phys = accounting.compute_physical_accounting(calls_list)
            self.assertTrue(phys.deduplication_valid)

            ta = accounting.compute_logical_trajectory_accounting(
                "tid", ["a", "b", "c"], {"a": 100, "b": 200, "c": 150}, 50,
            )
            self.assertTrue(ta.trajectory_accounting_valid)

            inv = accounting.compute_accounting_invariants(
                phys, [ta], [alloc],
            )
            self.assertIsNotNone(inv)

            shared, trajs = _make_synthetic_pilot_data()
            with tempfile.TemporaryDirectory() as tmp:
                ep = evidence.build_pilot_evidence(
                    Path(tmp), "pilot-test", shared, trajs,
                )
                self.assertIsNotNone(ep)

            self.assertEqual(
                calls, [],
                f"Network calls were attempted: {calls}"
            )
        finally:
            socket.create_connection = _original_create
            socket.socket.connect = _original_connect


# ── 9. Edge cases ────────────────────────────────────────────────────


class TestAccountingEdgeCases(unittest.TestCase):
    """Additional edge cases for accounting."""

    def test_all_price_categories_exercised(self):
        for cat in ("uncached_input", "cached_input", "output"):
            self.assertIn(cat, PRICES_INCLUDED)

    def test_both_cached_mappings(self):
        included = _make_call("inc", 500, 100, 150, cached_included=True)
        excluded = _make_call("exc", 500, 100, 150, cached_included=False)
        self.assertEqual(included.uncached_input_tokens, 400)
        self.assertEqual(excluded.uncached_input_tokens, 500)

    def test_hash_failure_detection(self):
        shared, trajs = _make_synthetic_pilot_data()
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"]["reference_hash"] = "x" * 64
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs,
            )
            self.assertFalse(result.packet_valid)


if __name__ == "__main__":
    unittest.main()
