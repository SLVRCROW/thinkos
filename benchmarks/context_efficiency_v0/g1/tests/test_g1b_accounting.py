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

_repo_root = Path(__file__).resolve().parents[4]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from benchmarks.context_efficiency_v0.g1 import accounting
from benchmarks.context_efficiency_v0.g1 import evidence
from benchmarks.context_efficiency_v0.g1 import hashing as g1_hashing
from benchmarks.context_efficiency_v0.g1 import serialization
from benchmarks.context_efficiency_v0.g1 import schemas as g1_schemas


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


def _make_call(inv_id, prompt_total, cached, completion, cached_included=True):
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


def _make_synthetic_receipt(inv_id, cost=1000, worker_label="A", stage=1):
    receipt = {
        "pilot_id": "pilot-test", "run_id": "run-001",
        "provider_invocation_id": inv_id, "attempt_index": 0,
        "trajectory_id": "traj-001", "logical_session_id": "sess-001",
        "model_session_id": "ms-001", "worker_label": worker_label,
        "stage": stage, "requested_provider": "test-provider",
        "requested_model": "test-model", "returned_model": "test-model",
        "model_identity_valid": True, "provider_request_id": "req-001",
        "request_dispatched": True, "temperature_milli": 0, "max_tokens": 1000,
        "system_prompt_sha256": "a" * 64, "prompt_sha256": "b" * 64,
        "tool_definitions_sha256": None, "prompt_tokens_total": 500,
        "cached_input_tokens": 100, "uncached_input_tokens": 400,
        "completion_tokens": 150, "provider_usage_status": "reported",
        "response_sha256": "c" * 64, "provider_finish_reason": "stop",
        "response_present": True, "sanitized_error_sha256": None,
        "normalized_execution_status": "completed",
        "start_timestamp": "2026-01-01T00:00:00Z",
        "end_timestamp": "2026-01-01T00:00:10Z", "duration_ms": 10000,
        "calculated_micro_usd_cost": cost,
        "provider_reported_cost_micro_usd": cost, "pricing_source": "test",
        "call_accounting_valid": True, "raw_prompt_sha256": "d" * 64,
        "raw_response_sha256": "e" * 64, "shared_source_id": None,
        "parent_receipt_ids": [], "tool_call_receipt_ids": [],
        "contamination_flags": [], "warnings": [],
    }
    receipt["receipt_id"] = g1_hashing.compute_receipt_hash(receipt)
    return receipt


def _make_synthetic_pilot_data():
    """Build synthetic data for a 6-trajectory, 2-shared-source pilot.

    Returns (shared_sources, trajectories).
    """
    shared_sources = {
        "clean": _make_synthetic_receipt("shared-clean", 5000),
        "drift": _make_synthetic_receipt("shared-drift", 4000),
    }
    clean_rid = shared_sources["clean"]["receipt_id"]
    drift_rid = shared_sources["drift"]["receipt_id"]

    trajectories = {}
    for arch in ("stateless", "summary", "verified_state"):
        for cond in ("clean", "drift"):
            tid = f"A-{cond}-{arch}"
            trajectories[tid] = {
                f"worker_{w}_receipt": _make_synthetic_receipt(
                    f"{arch}-{cond}-{w}", 1000, worker_label=w,
                ) for w in ("B", "C", "D")
            }
            rid = clean_rid if cond == "clean" else drift_rid
            ref_content = {
                "shared_source_id": f"shared-{cond}-001",
                "provider_invocation_id": f"shared-{cond}",
                "provider_receipt_id": rid,
                "checkpoint_receipt_id": f"chk-{cond}-001",
                "condition": cond,
                "allocated_shared_source_cost": 1667,
            }
            ref_content["reference_hash"] = g1_hashing.compute_sha256(
                serialization.canonical_json(ref_content)
            )
            trajectories[tid]["worker_a_shared_source_ref"] = ref_content

    return shared_sources, trajectories


# ── 1. Ceiling division ──────────────────────────────────────────────


class TestCeilingDivision(unittest.TestCase):
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
    def test_cached_included_basic(self):
        r = _make_call("inv-1", 500, 100, 150, True)
        self.assertTrue(r.call_accounting_valid)
        self.assertEqual(r.uncached_input_tokens, 400)
        self.assertIsNotNone(r.calculated_micro_usd_cost)
    def test_cached_excluded_basic(self):
        r = _make_call("inv-1", 500, 100, 150, False)
        self.assertTrue(r.call_accounting_valid)
        self.assertEqual(r.uncached_input_tokens, 500)
    def test_zero_tokens_cost_zero(self):
        r = _make_call("inv-0", 0, 0, 0, True)
        self.assertTrue(r.call_accounting_valid)
        self.assertEqual(r.calculated_micro_usd_cost, 0)
    def test_null_prompt_tokens(self):
        r = _make_call("inv-null", None, 0, 150, True)
        self.assertFalse(r.call_accounting_valid)
        self.assertIsNone(r.calculated_micro_usd_cost)
    def test_null_completion_tokens(self):
        r = _make_call("inv-null-c", 500, 0, None, True)
        self.assertFalse(r.call_accounting_valid)
        self.assertIsNone(r.calculated_micro_usd_cost)
    def test_negative_decomposition_rejected(self):
        r = _make_call("inv-neg", 100, 200, 150, True)
        self.assertFalse(r.call_accounting_valid)
        self.assertIsNone(r.calculated_micro_usd_cost)
    def test_explicit_zero_cache(self):
        r = _make_call("inv-no-cache", 500, 0, 150, True)
        self.assertTrue(r.call_accounting_valid)
        self.assertEqual(r.uncached_input_tokens, 500)
    def test_null_cache_unknown(self):
        r = _make_call("inv-unk-cache", 500, None, 150, True)
        self.assertFalse(r.call_accounting_valid)
        self.assertIsNone(r.calculated_micro_usd_cost)
    def test_single_token_rounds_up(self):
        r = _make_call("inv-1tok", 1, 0, 0, True)
        self.assertTrue(r.call_accounting_valid)
        self.assertIsNotNone(r.calculated_micro_usd_cost)
        self.assertGreater(r.calculated_micro_usd_cost, 0)
    def test_uncached_only(self):
        r = _make_call("inv-uncached", 1000, 0, 0, True)
        self.assertTrue(r.call_accounting_valid)
    def test_output_only(self):
        r = _make_call("inv-out", 0, 0, 1000, True)
        self.assertTrue(r.call_accounting_valid)
    def test_negative_prompt_tokens_rejected(self):
        r = _make_call("inv-neg-p", -1, 0, 150)
        self.assertFalse(r.call_accounting_valid)
    def test_negative_cached_tokens_rejected(self):
        r = _make_call("inv-neg-c", 500, -1, 150)
        self.assertFalse(r.call_accounting_valid)
    def test_negative_completion_tokens_rejected(self):
        r = _make_call("inv-neg-co", 500, 0, -1)
        self.assertFalse(r.call_accounting_valid)
    def test_negative_prices_rejected(self):
        r = accounting.calculate_call_cost(500, 100, 150, {"uncached_input": -1, "cached_input": 1_250_000, "output": 10_000_000}, True)
        self.assertFalse(r.call_accounting_valid)
    def test_missing_price_categories_rejected(self):
        r = accounting.calculate_call_cost(500, 100, 150, {"uncached_input": 2_500_000, "cached_input": 1_250_000}, True)
        self.assertFalse(r.call_accounting_valid)
    def test_non_integer_prices_rejected(self):
        r = accounting.calculate_call_cost(500, 100, 150, {"uncached_input": "2.5", "cached_input": 1_250_000, "output": 10_000_000}, True)
        self.assertFalse(r.call_accounting_valid)
    def test_non_integer_token_counts_rejected(self):
        r = accounting.calculate_call_cost(500.0, 100, 150, PRICES_INCLUDED, True)
        self.assertFalse(r.call_accounting_valid)
    def test_non_boolean_cached_included_rejected(self):
        r = accounting.calculate_call_cost(500, 100, 150, PRICES_INCLUDED, "yes")
        self.assertFalse(r.call_accounting_valid)
    def test_null_cost_not_converted_to_zero(self):
        r = _make_call("inv-null-cost", None, None, None)
        self.assertFalse(r.call_accounting_valid)
        self.assertIsNone(r.calculated_micro_usd_cost)
    def test_malformed_float_cost_returns_invalid_not_typeerror(self):
        r = accounting.calculate_call_cost(500.0, 100, 150, PRICES_INCLUDED, True)
        self.assertFalse(r.call_accounting_valid)
        self.assertIsNone(r.calculated_micro_usd_cost)


# ── 3. Shared-source allocation ───────────────────────────────────────


class TestSharedSourceAllocation(unittest.TestCase):
    def test_remainder_0(self):
        r = accounting.allocate_shared_cost("src-0", 60)
        self.assertTrue(r.allocation_valid)
        self.assertEqual(r.allocations, {"stateless": 20, "summary": 20, "verified_state": 20})
    def test_remainder_1(self):
        r = accounting.allocate_shared_cost("src-1", 61)
        self.assertTrue(r.allocation_valid)
        self.assertEqual(r.allocations["stateless"], 21)
    def test_remainder_2(self):
        r = accounting.allocate_shared_cost("src-2", 62)
        self.assertTrue(r.allocation_valid)
        self.assertEqual(r.allocations["stateless"], 21)
        self.assertEqual(r.allocations["summary"], 21)
        self.assertEqual(r.allocations["verified_state"], 20)
    def test_zero_cost(self):
        r = accounting.allocate_shared_cost("src-zero", 0)
        self.assertTrue(r.allocation_valid)
        self.assertEqual(r.allocations, {"stateless": 0, "summary": 0, "verified_state": 0})
    def test_small_cost(self):
        r = accounting.allocate_shared_cost("src-small", 1)
        self.assertTrue(r.allocation_valid)
        self.assertEqual(r.allocations["stateless"], 1)
    def test_rejects_negative_cost(self):
        r = accounting.allocate_shared_cost("src-neg", -1)
        self.assertFalse(r.allocation_valid)
    def test_sum_equals_physical(self):
        for cost in [0, 1, 2, 3, 4, 5, 10, 100, 1000]:
            r = accounting.allocate_shared_cost("src", cost)
            self.assertEqual(r.sum_allocated, cost)
    def test_rejects_alternate_architecture_order(self):
        r = accounting.allocate_shared_cost("src-alt", 60, architecture_order=("summary", "stateless", "verified_state"))
        self.assertFalse(r.allocation_valid)
    def test_rejects_partial_architecture_order(self):
        r = accounting.allocate_shared_cost("src-partial", 60, architecture_order=("stateless", "summary"))
        self.assertFalse(r.allocation_valid)
    def test_malformed_float_cost_returns_invalid_not_typeerror(self):
        r = accounting.allocate_shared_cost("src-float", 60.0)
        self.assertFalse(r.allocation_valid)


# ── 4. Physical accounting ───────────────────────────────────────────


class TestPhysicalAccounting(unittest.TestCase):
    def test_basic_deduplication(self):
        calls = [_make_call("shared-1", 500, 100, 150), _make_call("shared-1", 500, 100, 150), _make_call("unique-1", 300, 0, 100)]
        r = accounting.compute_physical_accounting(calls)
        self.assertTrue(r.deduplication_valid)
        self.assertEqual(len(r.physical_invocation_ids), 2)
    def test_conflicting_ids_rejected(self):
        calls = [_make_call("conflict-1", 500, 100, 150), _make_call("conflict-1", 100, 0, 50)]
        r = accounting.compute_physical_accounting(calls)
        self.assertFalse(r.deduplication_valid)
    def test_null_cost_rejected(self):
        calls = [accounting.CallAccounting(provider_invocation_id="null-cost", prompt_tokens_total=None, cached_input_tokens=None, uncached_input_tokens=None, completion_tokens=None, calculated_micro_usd_cost=None, call_accounting_valid=False)]
        r = accounting.compute_physical_accounting(calls)
        self.assertFalse(r.deduplication_valid)
    def test_empty_invocation_id_rejected(self):
        calls = [accounting.CallAccounting(provider_invocation_id="", prompt_tokens_total=0, cached_input_tokens=0, uncached_input_tokens=0, completion_tokens=0, calculated_micro_usd_cost=0, call_accounting_valid=True)]
        r = accounting.compute_physical_accounting(calls)
        self.assertFalse(r.deduplication_valid)
    def test_total_is_sum(self):
        calls = [_make_call("a", 500, 100, 150), _make_call("b", 300, 0, 100), _make_call("c", 1000, 200, 500)]
        r = accounting.compute_physical_accounting(calls)
        self.assertTrue(r.deduplication_valid)
        self.assertEqual(r.total_physical_calculated_cost, sum(c.calculated_micro_usd_cost or 0 for c in calls))
    def test_same_invocation_id_different_tokens_rejected(self):
        c1 = accounting.CallAccounting(provider_invocation_id="dup", prompt_tokens_total=500, cached_input_tokens=100, uncached_input_tokens=400, completion_tokens=150, calculated_micro_usd_cost=5, call_accounting_valid=True)
        c2 = accounting.CallAccounting(provider_invocation_id="dup", prompt_tokens_total=100, cached_input_tokens=0, uncached_input_tokens=100, completion_tokens=50, calculated_micro_usd_cost=5, call_accounting_valid=True)
        r = accounting.compute_physical_accounting([c1, c2])
        self.assertFalse(r.deduplication_valid)
    def test_negative_calculated_cost_rejected(self):
        calls = [accounting.CallAccounting(provider_invocation_id="neg-cost", prompt_tokens_total=500, cached_input_tokens=100, uncached_input_tokens=400, completion_tokens=150, calculated_micro_usd_cost=-5, call_accounting_valid=True)]
        r = accounting.compute_physical_accounting(calls)
        self.assertFalse(r.deduplication_valid)
    def test_invalid_call_with_non_null_cost_rejected(self):
        calls = [accounting.CallAccounting(provider_invocation_id="invalid-cost", prompt_tokens_total=None, cached_input_tokens=None, uncached_input_tokens=None, completion_tokens=None, calculated_micro_usd_cost=100, call_accounting_valid=False)]
        r = accounting.compute_physical_accounting(calls)
        self.assertFalse(r.deduplication_valid)
    def test_duplicate_fingerprint_includes_validity_state(self):
        c1 = accounting.CallAccounting(provider_invocation_id="dup", prompt_tokens_total=500, cached_input_tokens=100, uncached_input_tokens=400, completion_tokens=150, calculated_micro_usd_cost=5, call_accounting_valid=True)
        c2 = accounting.CallAccounting(provider_invocation_id="dup", prompt_tokens_total=500, cached_input_tokens=100, uncached_input_tokens=400, completion_tokens=150, calculated_micro_usd_cost=5, call_accounting_valid=False)
        r = accounting.compute_physical_accounting([c1, c2])
        self.assertFalse(r.deduplication_valid)
    def test_boolean_cost_rejected(self):
        calls = [accounting.CallAccounting(provider_invocation_id="bool-cost", prompt_tokens_total=500, cached_input_tokens=100, uncached_input_tokens=400, completion_tokens=150, calculated_micro_usd_cost=True, call_accounting_valid=True)]
        r = accounting.compute_physical_accounting(calls)
        self.assertFalse(r.deduplication_valid)
    def test_float_cost_rejected(self):
        calls = [accounting.CallAccounting(provider_invocation_id="float-cost", prompt_tokens_total=500, cached_input_tokens=100, uncached_input_tokens=400, completion_tokens=150, calculated_micro_usd_cost=5.5, call_accounting_valid=True)]
        r = accounting.compute_physical_accounting(calls)
        self.assertFalse(r.deduplication_valid)
    def test_string_cost_rejected(self):
        calls = [accounting.CallAccounting(provider_invocation_id="str-cost", prompt_tokens_total=500, cached_input_tokens=100, uncached_input_tokens=400, completion_tokens=150, calculated_micro_usd_cost="5", call_accounting_valid=True)]
        r = accounting.compute_physical_accounting(calls)
        self.assertFalse(r.deduplication_valid)
    def test_negative_token_fields_rejected_even_if_valid_claimed(self):
        calls = [accounting.CallAccounting(provider_invocation_id="neg-tok", prompt_tokens_total=-1, cached_input_tokens=0, uncached_input_tokens=0, completion_tokens=0, calculated_micro_usd_cost=0, call_accounting_valid=True)]
        r = accounting.compute_physical_accounting(calls)
        self.assertFalse(r.deduplication_valid)


# ── 5. Logical trajectory accounting ─────────────────────────────────


class TestLogicalTrajectoryAccounting(unittest.TestCase):
    def test_basic_trajectory(self):
        r = accounting.compute_logical_trajectory_accounting("A-clean-stateless", ["b-1", "c-1", "d-1"], {"b-1": 100, "c-1": 200, "d-1": 150}, 50)
        self.assertTrue(r.trajectory_accounting_valid)
        self.assertEqual(r.successor_calculated_cost, 450)
        self.assertEqual(r.logical_trajectory_cost, 500)
    def test_missing_successor(self):
        r = accounting.compute_logical_trajectory_accounting("A-clean-stateless", ["b-1", "c-1", "missing"], {"b-1": 100, "c-1": 200}, 50)
        self.assertFalse(r.trajectory_accounting_valid)
    def test_requires_exactly_three_successors(self):
        r = accounting.compute_logical_trajectory_accounting("tid", ["a", "b"], {"a": 100, "b": 200}, 50)
        self.assertFalse(r.trajectory_accounting_valid)
    def test_requires_three_distinct_ids(self):
        r = accounting.compute_logical_trajectory_accounting("tid", ["a", "a", "a"], {"a": 100}, 50)
        self.assertFalse(r.trajectory_accounting_valid)
    def test_rejects_empty_successor_id(self):
        r = accounting.compute_logical_trajectory_accounting("tid", ["a", "", "b"], {"a": 100, "b": 200}, 50)
        self.assertFalse(r.trajectory_accounting_valid)
    def test_rejects_negative_successor_cost(self):
        r = accounting.compute_logical_trajectory_accounting("tid", ["a", "b", "c"], {"a": 100, "b": -1, "c": 200}, 50)
        self.assertFalse(r.trajectory_accounting_valid)
    def test_rejects_negative_worker_a_cost(self):
        r = accounting.compute_logical_trajectory_accounting("tid", ["a", "b", "c"], {"a": 100, "b": 200, "c": 150}, -1)
        self.assertFalse(r.trajectory_accounting_valid)
    def test_malformed_float_worker_a_cost_returns_invalid_not_typeerror(self):
        r = accounting.compute_logical_trajectory_accounting("tid", ["a", "b", "c"], {"a": 100, "b": 200, "c": 150}, 50.0)
        self.assertFalse(r.trajectory_accounting_valid)


# ── 6. Aggregate invariants ──────────────────────────────────────────


class TestAccountingInvariants(unittest.TestCase):
    def _build_synthetic_pilot(self):
        sc = _make_call("shared-clean", 1000, 200, 500)
        sd = _make_call("shared-drift", 800, 100, 400)
        successors = {}
        for arch in ("stateless", "summary", "verified_state"):
            for cond in ("clean", "drift"):
                for w in ("B", "C", "D"):
                    successors[f"{arch}-{cond}-{w}"] = _make_call(f"{arch}-{cond}-{w}", 500, 100, 200)
        all_calls = [sc, sd] + list(successors.values())
        physical = accounting.compute_physical_accounting(all_calls)
        ac = accounting.allocate_shared_cost("shared-clean", sc.calculated_micro_usd_cost or 0)
        ad = accounting.allocate_shared_cost("shared-drift", sd.calculated_micro_usd_cost or 0)
        trajs = []
        for arch in ("stateless", "summary", "verified_state"):
            for cond in ("clean", "drift"):
                tid = f"A-{cond}-{arch}"
                succ_ids = [f"{arch}-{cond}-{w}" for w in ("B", "C", "D")]
                succ_costs = {sid: successors[sid].calculated_micro_usd_cost or 0 for sid in succ_ids}
                alloc = ac if cond == "clean" else ad
                trajs.append(accounting.compute_logical_trajectory_accounting(tid, succ_ids, succ_costs, alloc.allocations.get(arch, 0)))
        inv = accounting.compute_accounting_invariants(physical, trajs, [ac, ad])
        return inv, physical, trajs

    def test_20_physical_invocations(self):
        inv, physical, _ = self._build_synthetic_pilot()
        self.assertEqual(len(physical.physical_invocation_ids), 20)
    def test_6_trajectories(self):
        inv, _, trajs = self._build_synthetic_pilot()
        self.assertEqual(len(trajs), 6)
    def test_2_shared_sources(self):
        inv, _, _ = self._build_synthetic_pilot()
        self.assertEqual(len(inv.shared_source_allocations), 2)
    def test_invariant_valid(self):
        inv, _, _ = self._build_synthetic_pilot()
        self.assertTrue(inv.invariant_valid)
    def test_invariant_equality(self):
        inv, _, _ = self._build_synthetic_pilot()
        self.assertEqual(inv.total_physical_calculated_cost, inv.total_logical_trajectory_cost)
    def test_invalid_physical_fails_aggregate(self):
        inv, physical, trajs = self._build_synthetic_pilot()
        bp = accounting.PhysicalPilotAccounting(physical.physical_invocation_ids, physical.physical_calculated_costs, physical.total_physical_calculated_cost, False, ("simulated",))
        r = accounting.compute_accounting_invariants(bp, list(trajs), list(inv.shared_source_allocations))
        self.assertFalse(r.invariant_valid)
    def test_invalid_trajectory_fails_aggregate(self):
        inv, physical, trajs = self._build_synthetic_pilot()
        trajs[0] = accounting.LogicalTrajectoryAccounting("bad", ("a",), 0, 0, 0, False, ("simulated",))
        r = accounting.compute_accounting_invariants(physical, list(trajs), list(inv.shared_source_allocations))
        self.assertFalse(r.invariant_valid)
    def test_invalid_shared_allocation_fails_aggregate(self):
        inv, physical, trajs = self._build_synthetic_pilot()
        ba = accounting.SharedSourceAllocation("bad", 100, {}, 0, False, ("simulated",))
        r = accounting.compute_accounting_invariants(physical, list(trajs), [ba])
        self.assertFalse(r.invariant_valid)
    def test_duplicate_invocation_id_with_equal_cost_different_tokens_fails_aggregate(self):
        c1 = accounting.CallAccounting("dup", 500, 100, 400, 150, 5, True)
        c2 = accounting.CallAccounting("dup", 100, 0, 100, 50, 5, True)
        physical = accounting.compute_physical_accounting([c1, c2])
        self.assertFalse(physical.deduplication_valid)
        traj = accounting.compute_logical_trajectory_accounting("tid", ["a", "b", "c"], {"a": 5, "b": 0, "c": 0}, 0)
        alloc = accounting.allocate_shared_cost("src", 0)
        r = accounting.compute_accounting_invariants(physical, [traj], [alloc])
        self.assertFalse(r.invariant_valid)


# ── 7. Evidence packet construction ───────────────────────────────────


class TestEvidencePacket(unittest.TestCase):
    def test_valid_pilot_packet(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(Path(tmp), "pilot-test-001", shared, trajs)
            self.assertTrue(result.packet_valid, f"Errors: {result.errors}")

    def test_shared_source_count(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertEqual(r.shared_source_count, 2)

    def test_trajectory_count(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertEqual(r.trajectory_count, 6)

    def test_reference_count(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertEqual(r.reference_count, 6)

    def test_rejects_absolute_path(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            shared["../evil"] = _make_synthetic_receipt("evil")
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_rejects_copied_worker_a_receipt(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp) / "pilot-test" / "trajectories" / "A-clean-stateless" / "worker_A"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "provider_call_receipt.json").write_text("{}")
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_rejects_wrong_shared_source_count(self):
        shared, trajs = _make_synthetic_pilot_data()
        del shared["drift"]
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_empty_reference_map_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        for tid in trajs:
            del trajs[tid]["worker_a_shared_source_ref"]
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_missing_clean_references_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        for tid in list(trajs.keys()):
            if "clean" in tid:
                del trajs[tid]
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_missing_drift_references_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        for tid in list(trajs.keys()):
            if "drift" in tid:
                del trajs[tid]
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_reference_ids_that_do_not_resolve_fail(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["condition"] = "nonexistent"
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json({k: v for k, v in ref.items() if k != "reference_hash"}))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_missing_required_s20_files_fail(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            del shared["clean"]
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_missing_hashes_fail(self):
        shared, trajs = _make_synthetic_pilot_data()
        del trajs["A-clean-stateless"]["worker_a_shared_source_ref"]["reference_hash"]
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_mismatched_hashes_fail(self):
        shared, trajs = _make_synthetic_pilot_data()
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"]["reference_hash"] = "x" * 64
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_absolute_path_creates_no_files(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "/etc/evil", shared, trajs)
            self.assertFalse(r.packet_valid)
            for f in Path(tmp).rglob("*"):
                self.assertTrue(str(f).startswith(str(Path(tmp))))

    def test_traversal_attempt_creates_no_files(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "../../evil", shared, trajs)
            self.assertFalse(r.packet_valid)
            for f in Path(tmp).rglob("*"):
                self.assertTrue(str(f).startswith(str(Path(tmp))))

    def test_symlink_escape_attempt_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            outside = Path(tempfile.mkdtemp())
            escape_link = run_root / "pilot-test" / "shared_sources" / "clean"
            escape_link.parent.mkdir(parents=True, exist_ok=True)
            try:
                escape_link.symlink_to(outside, target_is_directory=True)
                r = evidence.build_pilot_evidence(run_root, "pilot-test", shared, trajs)
                self.assertFalse(r.packet_valid)
            finally:
                import shutil
                shutil.rmtree(outside, ignore_errors=True)
                if escape_link.exists():
                    escape_link.unlink()

    def test_complete_synthetic_packet_reconstructs_successfully(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(result.packet_valid, f"Errors: {result.errors}")
            self.assertEqual(result.shared_source_count, 2)
            self.assertEqual(result.trajectory_count, 6)
            self.assertEqual(result.reference_count, 6)

            pilot_dir = Path(tmp) / "pilot-test"
            for fname in evidence.REQUIRED_PILOT_FILES:
                self.assertTrue((pilot_dir / fname).exists(), f"Missing: {fname}")
            for cond in ("clean", "drift"):
                cd = pilot_dir / "shared_sources" / cond
                self.assertTrue(cd.exists())
                for fname in evidence.REQUIRED_SHARED_SOURCE_FILES:
                    self.assertTrue((cd / fname).exists(), f"Missing: {cond}/{fname}")
                for dname in evidence.REQUIRED_SHARED_SOURCE_DIRS:
                    self.assertTrue((cd / dname).exists(), f"Missing: {cond}/{dname}")
            for arch in ("stateless", "summary", "verified_state"):
                for cond in ("clean", "drift"):
                    tid = f"A-{cond}-{arch}"
                    tdir = pilot_dir / "trajectories" / tid
                    self.assertTrue(tdir.exists())
                    for fname in evidence.REQUIRED_TRAJECTORY_FILES:
                        self.assertTrue((tdir / fname).exists(), f"Missing: {tid}/{fname}")
                    for dname in evidence.REQUIRED_TRAJECTORY_DIRS:
                        wdir = tdir / dname
                        self.assertTrue(wdir.exists())
                        for wfname in evidence.REQUIRED_WORKER_FILES:
                            self.assertTrue((wdir / wfname).exists(), f"Missing: {tid}/{dname}/{wfname}")
                        for wdname in evidence.REQUIRED_WORKER_DIRS:
                            self.assertTrue((wdir / wdname).exists(), f"Missing: {tid}/{dname}/{wdname}")

            # Verify content is non-empty by parsing
            for fname in evidence.NONEMPTY_FILES:
                fpath = pilot_dir / fname
                if fpath.exists():
                    data = json.loads(fpath.read_text(encoding="utf-8"))
                    self.assertTrue(data, f"{fname} should not be empty")

    def test_disk_validator_independent_reconstruction(self):
        """Build once, then validate from disk only."""
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(result.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            disk_errors = evidence.validate_pilot_evidence_from_disk(pilot_dir)
            self.assertEqual(disk_errors, [], f"Disk validation failed: {disk_errors}")

    def test_disk_validator_rejects_empty_placeholders(self):
        """Empty {} files must fail disk validation."""
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(result.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            # Replace pilot_config.json with empty
            (pilot_dir / "pilot_config.json").write_text("{}\n")
            disk_errors = evidence.validate_pilot_evidence_from_disk(pilot_dir)
            self.assertTrue(any("empty" in e for e in disk_errors))

    def test_disk_validator_rejects_empty_checkpoint(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(result.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "shared_sources" / "clean" / "checkpoint_receipt.json").write_text("{}\n")
            disk_errors = evidence.validate_pilot_evidence_from_disk(pilot_dir)
            self.assertTrue(any("empty" in e for e in disk_errors))

    def test_disk_validator_rejects_empty_provider_selection(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(result.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "provider_selection.md").write_text("")
            disk_errors = evidence.validate_pilot_evidence_from_disk(pilot_dir)
            self.assertTrue(any("empty" in e for e in disk_errors))

    def test_reference_mandatory_fields_absent(self):
        shared, trajs = _make_synthetic_pilot_data()
        for field in ["shared_source_id", "provider_invocation_id", "provider_receipt_id", "checkpoint_receipt_id", "condition", "allocated_shared_source_cost"]:
            with tempfile.TemporaryDirectory() as tmp:
                tc = {k: dict(v) for k, v in trajs.items()}
                ref = dict(tc["A-clean-stateless"]["worker_a_shared_source_ref"])
                del ref[field]
                ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json({k: v for k, v in ref.items() if k != "reference_hash"}))
                tc["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
                r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, tc)
                self.assertFalse(r.packet_valid, f"Should fail with missing field: {field}")

    def test_mismatched_provider_invocation_id_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["provider_invocation_id"] = "wrong"
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json({k: v for k, v in ref.items() if k != "reference_hash"}))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs).packet_valid)

    def test_mismatched_provider_receipt_id_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["provider_receipt_id"] = "wrong"
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json({k: v for k, v in ref.items() if k != "reference_hash"}))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs).packet_valid)

    def test_mismatched_checkpoint_receipt_id_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["checkpoint_receipt_id"] = "wrong"
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json({k: v for k, v in ref.items() if k != "reference_hash"}))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs).packet_valid)

    def test_mismatched_condition_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["condition"] = "drift"
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json({k: v for k, v in ref.items() if k != "reference_hash"}))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs).packet_valid)

    def test_mismatched_shared_source_id_fails(self):
        shared, trajs = _make_synthetic_pilot_data()
        ref = dict(trajs["A-clean-stateless"]["worker_a_shared_source_ref"])
        ref["shared_source_id"] = "wrong-shared-source"
        ref["reference_hash"] = g1_hashing.compute_sha256(serialization.canonical_json({k: v for k, v in ref.items() if k != "reference_hash"}))
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"] = ref
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs).packet_valid)

    def test_preflight_rejects_existing_files(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pilot-test").mkdir()
            (Path(tmp) / "pilot-test" / "pilot_config.json").write_text("{}")
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_preflight_rejects_existing_dirs(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pilot-test" / "shared_sources" / "clean").mkdir(parents=True)
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)

    def test_preflight_leaves_destination_unchanged_after_failure(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            pilot_dir = Path(tmp) / "pilot-test"
            pilot_dir.mkdir()
            (pilot_dir / "pilot_config.json").write_text("original")
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)
            self.assertEqual((pilot_dir / "pilot_config.json").read_text(), "original")

    def test_sibling_path_sharing_prefix_rejected(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            sibling = run_root.parent / f"{run_root.name}-sibling"
            sibling.mkdir(exist_ok=True)
            try:
                r = evidence.build_pilot_evidence(run_root, "../sibling", shared, trajs)
                self.assertFalse(r.packet_valid)
            finally:
                sibling.rmdir()

    def test_disk_validator_rejects_unexpected_files(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "unexpected.txt").write_text("evil")
            disk_errors = evidence.validate_pilot_evidence_from_disk(pilot_dir)
            self.assertTrue(any("unexpected" in e for e in disk_errors))

    def test_disk_validator_rejects_missing_file(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "pilot_config.json").unlink()
            disk_errors = evidence.validate_pilot_evidence_from_disk(pilot_dir)
            self.assertTrue(any("missing" in e for e in disk_errors))

    def test_disk_validator_rejects_copied_worker_a_receipt(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            tdir = pilot_dir / "trajectories" / "A-clean-stateless" / "worker_A"
            tdir.mkdir(exist_ok=True)
            (tdir / "provider_call_receipt.json").write_text("{}")
            disk_errors = evidence.validate_pilot_evidence_from_disk(pilot_dir)
            self.assertTrue(any("copied" in e for e in disk_errors))

    def test_disk_validator_rejects_duplicate_receipt_ids(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            # Copy clean's receipt into drift to create duplicate
            clean_receipt = json.loads((pilot_dir / "shared_sources" / "clean" / "provider_call_receipt.json").read_text())
            (pilot_dir / "shared_sources" / "drift" / "provider_call_receipt.json").write_text(
                serialization.canonical_json(clean_receipt) + "\n"
            )
            disk_errors = evidence.validate_pilot_evidence_from_disk(pilot_dir)
            self.assertTrue(any("duplicate" in e for e in disk_errors))

    def test_mutate_each_pilot_file_individually(self):
        """Delete or corrupt each pilot-level required file and verify disk validator fails."""
        shared, trajs = _make_synthetic_pilot_data()
        for fname in evidence.REQUIRED_PILOT_FILES:
            with tempfile.TemporaryDirectory() as tmp:
                r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
                self.assertTrue(r.packet_valid)
                pilot_dir = Path(tmp) / "pilot-test"
                fpath = pilot_dir / fname
                if fpath.exists():
                    fpath.unlink()
                disk_errors = evidence.validate_pilot_evidence_from_disk(pilot_dir)
                self.assertTrue(disk_errors, f"Should fail with missing {fname}")

    def test_mutate_shared_provider_receipt(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "shared_sources" / "clean" / "provider_call_receipt.json").unlink()
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))

    def test_mutate_shared_checkpoint_receipt(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "shared_sources" / "clean" / "checkpoint_receipt.json").unlink()
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))

    def test_mutate_shared_raw_dir(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            import shutil
            shutil.rmtree(pilot_dir / "shared_sources" / "clean" / "raw")
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))

    def test_mutate_trajectory_config(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "trajectories" / "A-clean-stateless" / "config.json").unlink()
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))

    def test_mutate_worker_provider_receipt(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "trajectories" / "A-clean-stateless" / "worker_B" / "provider_call_receipt.json").unlink()
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))

    def test_mutate_worker_checkpoint_receipt(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "trajectories" / "A-clean-stateless" / "worker_B" / "checkpoint_receipt.json").unlink()
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))

    def test_mutate_worker_raw_dir(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            import shutil
            shutil.rmtree(pilot_dir / "trajectories" / "A-clean-stateless" / "worker_B" / "raw")
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))

    def test_mutate_trajectory_checksum(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            tpath = pilot_dir / "trajectories" / "A-clean-stateless" / "trajectory_receipt.json"
            data = json.loads(tpath.read_text(encoding="utf-8"))
            data["checksum"] = "x" * 64
            tpath.write_text(serialization.canonical_json(data) + "\n", encoding="utf-8")
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))

    def test_mutate_pilot_checksum(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            ppath = pilot_dir / "pilot_receipt.json"
            data = json.loads(ppath.read_text(encoding="utf-8"))
            data["checksum"] = "x" * 64
            ppath.write_text(serialization.canonical_json(data) + "\n", encoding="utf-8")
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))

    def test_mutate_provider_selection_content(self):
        shared, trajs = _make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertTrue(r.packet_valid)
            pilot_dir = Path(tmp) / "pilot-test"
            (pilot_dir / "provider_selection.md").write_text("")
            self.assertTrue(evidence.validate_pilot_evidence_from_disk(pilot_dir))


# ── 8. Network-denial tests ───────────────────────────────────────────


class TestNetworkDenial(unittest.TestCase):
    def test_no_network_imports(self):
        suspicious = ["requests", "urllib3", "httpx", "openai", "anthropic", "ollama", "http.client"]
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
            r = accounting.calculate_call_cost(500, 100, 150, PRICES_INCLUDED, True)
            self.assertTrue(r.call_accounting_valid)
            alloc = accounting.allocate_shared_cost("src", 60)
            self.assertTrue(alloc.allocation_valid)
            phys = accounting.compute_physical_accounting([_make_call("a", 500, 100, 150)])
            self.assertTrue(phys.deduplication_valid)
            ta = accounting.compute_logical_trajectory_accounting("tid", ["a", "b", "c"], {"a": 100, "b": 200, "c": 150}, 50)
            self.assertTrue(ta.trajectory_accounting_valid)
            inv = accounting.compute_accounting_invariants(phys, [ta], [alloc])
            self.assertIsNotNone(inv)
            shared, trajs = _make_synthetic_pilot_data()
            with tempfile.TemporaryDirectory() as tmp:
                ep = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
                self.assertIsNotNone(ep)
            self.assertEqual(calls, [], f"Network calls attempted: {calls}")
        finally:
            socket.create_connection = _original_create
            socket.socket.connect = _original_connect


# ── 9. Edge cases ────────────────────────────────────────────────────


class TestAccountingEdgeCases(unittest.TestCase):
    def test_all_price_categories_exercised(self):
        for cat in ("uncached_input", "cached_input", "output"):
            self.assertIn(cat, PRICES_INCLUDED)

    def test_both_cached_mappings(self):
        inc = _make_call("inc", 500, 100, 150, True)
        exc = _make_call("exc", 500, 100, 150, False)
        self.assertEqual(inc.uncached_input_tokens, 400)
        self.assertEqual(exc.uncached_input_tokens, 500)

    def test_hash_failure_detection(self):
        shared, trajs = _make_synthetic_pilot_data()
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"]["reference_hash"] = "x" * 64
        with tempfile.TemporaryDirectory() as tmp:
            r = evidence.build_pilot_evidence(Path(tmp), "pilot-test", shared, trajs)
            self.assertFalse(r.packet_valid)


if __name__ == "__main__":
    unittest.main()
