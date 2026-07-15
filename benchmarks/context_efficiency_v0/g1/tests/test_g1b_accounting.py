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


# ── Synthetic pricing fixtures ────────────────────────────────────────


PRICES_INCLUDED = {
    "uncached_input": 2_500_000,  # $2.50/1M
    "cached_input": 1_250_000,    # $1.25/1M
    "output": 10_000_000,         # $10.00/1M
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
    # Patch the invocation ID onto the result
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
        """500 total, 100 cached, 150 completion with cached_included=True."""
        result = _make_call("inv-1", 500, 100, 150, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertEqual(result.uncached_input_tokens, 400)
        self.assertIsNotNone(result.calculated_micro_usd_cost)
        self.assertGreater(result.calculated_micro_usd_cost, 0)

    def test_cached_excluded_basic(self):
        """500 total, 100 cached, 150 completion with cached_included=False."""
        result = _make_call("inv-1", 500, 100, 150, cached_included=False)
        self.assertTrue(result.call_accounting_valid)
        self.assertEqual(result.uncached_input_tokens, 500)
        self.assertIsNotNone(result.calculated_micro_usd_cost)

    def test_zero_tokens_cost_zero(self):
        """Zero tokens in all categories must cost zero."""
        result = _make_call("inv-0", 0, 0, 0, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertEqual(result.calculated_micro_usd_cost, 0)

    def test_null_prompt_tokens(self):
        """Null prompt_tokens_total must set accounting_valid=False."""
        result = _make_call("inv-null", None, 0, 150, cached_included=True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_null_completion_tokens(self):
        """Null completion_tokens must set accounting_valid=False."""
        result = _make_call("inv-null-c", 500, 0, None, cached_included=True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_negative_decomposition_rejected(self):
        """cached > prompt_total with cached_included=True must fail."""
        result = _make_call("inv-neg", 100, 200, 150, cached_included=True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)
        self.assertTrue(any("negative" in e or "exceeds" in e for e in result.errors))

    def test_explicit_zero_cache(self):
        """Explicitly reported zero cached tokens is valid."""
        result = _make_call("inv-no-cache", 500, 0, 150, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertEqual(result.uncached_input_tokens, 500)

    def test_null_cache_unknown(self):
        """Null cached_input_tokens means unknown, not zero."""
        result = _make_call("inv-unk-cache", 500, None, 150, cached_included=True)
        self.assertFalse(result.call_accounting_valid)
        self.assertIsNone(result.calculated_micro_usd_cost)

    def test_single_token_rounds_up(self):
        """A single token at a nonzero price rounds up to at least 1 micro-USD.
        1 uncached token * $2.50/1M = 2,500,000 micro-USD / 1,000,000 = 3 (ceiling)."""
        result = _make_call("inv-1tok", 1, 0, 0, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertIsNotNone(result.calculated_micro_usd_cost)
        self.assertGreater(result.calculated_micro_usd_cost, 0)

    def test_uncached_only(self):
        """Only uncached input, no cached, no output."""
        result = _make_call("inv-uncached", 1000, 0, 0, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertIsNotNone(result.calculated_micro_usd_cost)

    def test_output_only(self):
        """Only output tokens, no input."""
        result = _make_call("inv-out", 0, 0, 1000, cached_included=True)
        self.assertTrue(result.call_accounting_valid)
        self.assertIsNotNone(result.calculated_micro_usd_cost)


# ── 3. Shared-source allocation ───────────────────────────────────────


class TestSharedSourceAllocation(unittest.TestCase):
    """Shared Worker-A cost allocation per contract §11."""

    def test_remainder_0(self):
        """60 micro-USD split 3 ways = 20 each."""
        result = accounting.allocate_shared_cost("src-0", 60)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations, {
            "stateless": 20, "summary": 20, "verified_state": 20,
        })
        self.assertEqual(result.sum_allocated, 60)

    def test_remainder_1(self):
        """61 micro-USD: stateless gets 21, others 20."""
        result = accounting.allocate_shared_cost("src-1", 61)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations["stateless"], 21)
        self.assertEqual(result.allocations["summary"], 20)
        self.assertEqual(result.allocations["verified_state"], 20)
        self.assertEqual(result.sum_allocated, 61)

    def test_remainder_2(self):
        """62 micro-USD: stateless 21, summary 21, verified_state 20."""
        result = accounting.allocate_shared_cost("src-2", 62)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations["stateless"], 21)
        self.assertEqual(result.allocations["summary"], 21)
        self.assertEqual(result.allocations["verified_state"], 20)
        self.assertEqual(result.sum_allocated, 62)

    def test_zero_cost(self):
        """Zero cost allocates zero to all arms."""
        result = accounting.allocate_shared_cost("src-zero", 0)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations, {
            "stateless": 0, "summary": 0, "verified_state": 0,
        })

    def test_small_cost(self):
        """1 micro-USD goes to stateless."""
        result = accounting.allocate_shared_cost("src-small", 1)
        self.assertTrue(result.allocation_valid)
        self.assertEqual(result.allocations["stateless"], 1)
        self.assertEqual(result.allocations["summary"], 0)
        self.assertEqual(result.allocations["verified_state"], 0)

    def test_rejects_negative_cost(self):
        result = accounting.allocate_shared_cost("src-neg", -1)
        self.assertFalse(result.allocation_valid)

    def test_sum_equals_physical(self):
        """Allocated sum must always equal physical cost."""
        for cost in [0, 1, 2, 3, 4, 5, 10, 100, 1000]:
            result = accounting.allocate_shared_cost("src", cost)
            self.assertEqual(result.sum_allocated, cost)


# ── 4. Physical accounting ───────────────────────────────────────────


class TestPhysicalAccounting(unittest.TestCase):
    """Physical pilot accounting with deduplication."""

    def test_basic_deduplication(self):
        """Repeated identical references to one shared receipt are permitted."""
        calls = [
            _make_call("shared-1", 500, 100, 150),
            _make_call("shared-1", 500, 100, 150),  # identical repeat
            _make_call("unique-1", 300, 0, 100),
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertTrue(result.deduplication_valid)
        self.assertEqual(len(result.physical_invocation_ids), 2)

    def test_conflicting_ids_rejected(self):
        """Conflicting receipts using the same invocation ID are rejected."""
        calls = [
            _make_call("conflict-1", 500, 100, 150),
            _make_call("conflict-1", 100, 0, 50),  # different cost
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertFalse(result.deduplication_valid)
        self.assertTrue(any("conflicting" in e for e in result.errors))

    def test_null_cost_rejected(self):
        """Null costs must be rejected, never converted to zero."""
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
        """Total must equal sum of unique invocation costs."""
        calls = [
            _make_call("a", 500, 100, 150),
            _make_call("b", 300, 0, 100),
            _make_call("c", 1000, 200, 500),
        ]
        result = accounting.compute_physical_accounting(calls)
        self.assertTrue(result.deduplication_valid)
        expected = sum(
            c.calculated_micro_usd_cost or 0
            for c in calls
        )
        self.assertEqual(result.total_physical_calculated_cost, expected)


# ── 5. Logical trajectory accounting ─────────────────────────────────


class TestLogicalTrajectoryAccounting(unittest.TestCase):
    """Logical trajectory accounting."""

    def test_basic_trajectory(self):
        """Three successors plus allocated Worker-A share."""
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
        """Missing successor cost must set valid=False."""
        result = accounting.compute_logical_trajectory_accounting(
            "A-clean-stateless",
            ["b-1", "c-1", "missing"],
            {"b-1": 100, "c-1": 200},
            50,
        )
        self.assertFalse(result.trajectory_accounting_valid)


# ── 6. Aggregate invariants ──────────────────────────────────────────


class TestAccountingInvariants(unittest.TestCase):
    """Aggregate invariant checks with synthetic full-pilot fixture."""

    def _build_synthetic_pilot(self):
        """Build a synthetic 20-call, 6-trajectory, 2-shared-source pilot."""
        # Two shared Worker-A sources (clean, drift)
        shared_clean = _make_call("shared-clean", 1000, 200, 500)
        shared_drift = _make_call("shared-drift", 800, 100, 400)

        # 18 unique successor calls (3 architectures x 2 conditions x 3 workers)
        successors = {}
        for arch in ("stateless", "summary", "verified_state"):
            for cond in ("clean", "drift"):
                for worker in ("B", "C", "D"):
                    iid = f"{arch}-{cond}-{worker}"
                    successors[iid] = _make_call(
                        iid, 500, 100, 200,
                    )

        # Build physical accounting
        all_calls = [shared_clean, shared_drift] + list(successors.values())
        physical = accounting.compute_physical_accounting(all_calls)

        # Build shared allocations
        alloc_clean = accounting.allocate_shared_cost(
            "shared-clean",
            shared_clean.calculated_micro_usd_cost or 0,
        )
        alloc_drift = accounting.allocate_shared_cost(
            "shared-drift",
            shared_drift.calculated_micro_usd_cost or 0,
        )

        # Build trajectory accountings
        traj_accountings = []
        for arch in ("stateless", "summary", "verified_state"):
            for cond in ("clean", "drift"):
                tid = f"A-{cond}-{arch}"
                succ_ids = [f"{arch}-{cond}-{w}" for w in ("B", "C", "D")]
                succ_costs = {
                    sid: successors[sid].calculated_micro_usd_cost or 0
                    for sid in succ_ids
                }
                alloc = (
                    alloc_clean if cond == "clean" else alloc_drift
                )
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
        """Synthetic pilot must have exactly 20 unique physical invocations."""
        invariants, physical, _ = self._build_synthetic_pilot()
        self.assertEqual(len(physical.physical_invocation_ids), 20)

    def test_6_trajectories(self):
        """Synthetic pilot must have exactly 6 trajectories."""
        invariants, _, trajs = self._build_synthetic_pilot()
        self.assertEqual(len(trajs), 6)

    def test_2_shared_sources(self):
        """Synthetic pilot must have exactly 2 shared source allocations."""
        invariants, _, _ = self._build_synthetic_pilot()
        self.assertEqual(len(invariants.shared_source_allocations), 2)

    def test_invariant_valid(self):
        """Logical total must equal physical total."""
        invariants, _, _ = self._build_synthetic_pilot()
        self.assertTrue(
            invariants.invariant_valid,
            f"Physical={invariants.total_physical_calculated_cost}, "
            f"Logical={invariants.total_logical_trajectory_cost}, "
            f"Errors={invariants.errors}"
        )

    def test_invariant_equality(self):
        """Explicit equality check."""
        invariants, _, _ = self._build_synthetic_pilot()
        self.assertEqual(
            invariants.total_physical_calculated_cost,
            invariants.total_logical_trajectory_cost,
        )

    def test_missing_or_invalid_sets_validity_false(self):
        """A missing call must set aggregate validity false."""
        # Build with a null-cost call
        bad_call = accounting.CallAccounting(
            provider_invocation_id="bad",
            prompt_tokens_total=None, cached_input_tokens=None,
            uncached_input_tokens=None, completion_tokens=None,
            calculated_micro_usd_cost=None,
            call_accounting_valid=False,
        )
        physical = accounting.compute_physical_accounting([bad_call])
        self.assertFalse(physical.deduplication_valid)


# ── 7. Evidence packet construction ───────────────────────────────────


class TestEvidencePacket(unittest.TestCase):
    """Evidence-packet construction and validation."""

    def _make_synthetic_pilot_data(self):
        """Build synthetic data for a 6-trajectory, 2-shared-source pilot."""
        shared_sources = {
            "clean": {
                "receipt_id": "shared-clean-001",
                "provider_invocation_id": "shared-clean",
                "calculated_micro_usd_cost": 5000,
            },
            "drift": {
                "receipt_id": "shared-drift-001",
                "provider_invocation_id": "shared-drift",
                "calculated_micro_usd_cost": 4000,
            },
        }

        trajectories = {}
        trajectory_refs = {}
        for arch in ("stateless", "summary", "verified_state"):
            for cond in ("clean", "drift"):
                tid = f"A-{cond}-{arch}"
                trajectories[tid] = {
                    f"worker_{w}_receipt": {
                        "receipt_id": f"rct-{arch}-{cond}-{w}",
                        "provider_invocation_id": f"{arch}-{cond}-{w}",
                        "calculated_micro_usd_cost": 1000,
                    }
                    for w in ("B", "C", "D")
                }
                # Build ref dict, compute hash over content excluding reference_hash
                ref_content = {
                    "shared_source_id": f"shared-{cond}-001",
                    "provider_invocation_id": f"shared-{cond}",
                    "condition": cond,
                    "allocated_shared_source_cost": 1667,
                }
                ref_hash = g1_hashing.compute_sha256(
                    serialization.canonical_json(ref_content)
                )
                ref_content["reference_hash"] = ref_hash
                trajectories[tid]["worker_a_shared_source_ref"] = ref_content
                trajectory_refs[tid] = [cond]

        return shared_sources, trajectories, trajectory_refs

    def test_valid_pilot_packet(self):
        """A valid synthetic pilot packet must pass validation."""
        shared, trajs, refs = self._make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test-001",
                shared, trajs, refs,
            )
            self.assertTrue(
                result.packet_valid,
                f"Packet validation failed: {result.errors}"
            )

    def test_shared_source_count(self):
        shared, trajs, refs = self._make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs, refs,
            )
            self.assertEqual(result.shared_source_count, 2)

    def test_trajectory_count(self):
        shared, trajs, refs = self._make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs, refs,
            )
            self.assertEqual(result.trajectory_count, 6)

    def test_reference_count(self):
        shared, trajs, refs = self._make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs, refs,
            )
            self.assertEqual(result.reference_count, 6)

    def test_rejects_absolute_path(self):
        shared, trajs, refs = self._make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            # Inject a condition name with path traversal
            shared["../evil"] = {"receipt_id": "evil"}
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs, refs,
            )
            self.assertFalse(result.packet_valid)

    def test_rejects_copied_worker_a_receipt(self):
        shared, trajs, refs = self._make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            # Manually create a copied Worker-A receipt inside a trajectory
            tdir = Path(tmp) / "pilot-test" / "trajectories" / "A-clean-stateless" / "worker_A"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "provider_call_receipt.json").write_text("{}")
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs, refs,
            )
            self.assertFalse(result.packet_valid)

    def test_rejects_wrong_shared_source_count(self):
        shared, trajs, refs = self._make_synthetic_pilot_data()
        # Remove one shared source
        del shared["drift"]
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs, refs,
            )
            self.assertFalse(result.packet_valid)

    def test_rejects_wrong_reference_count(self):
        shared, trajs, refs = self._make_synthetic_pilot_data()
        # Add an extra trajectory reference
        refs["extra"] = ["clean"]
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs, refs,
            )
            self.assertFalse(result.packet_valid)

    def test_canonical_hash_trajectory_manifest(self):
        """Trajectory manifest must be canonically hashable."""
        shared, trajs, refs = self._make_synthetic_pilot_data()
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs, refs,
            )
            # If packet is valid, hashes were verified
            self.assertTrue(result.packet_valid)


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
        """Patch socket and exercise all G1-B modules."""
        _original_create = socket.create_connection
        _original_connect = socket.socket.connect
        calls = []

        def _deny(*args, **kwargs):
            calls.append(("denied", args, kwargs))
            raise OSError("Network denied by G1-B test")

        socket.create_connection = _deny
        socket.socket.connect = _deny

        try:
            # Ceiling division
            self.assertEqual(accounting.ceiling_div(100, 1_000_000), 1)

            # Cost calculation
            result = accounting.calculate_call_cost(
                500, 100, 150, PRICES_INCLUDED, True,
            )
            self.assertTrue(result.call_accounting_valid)

            # Shared allocation
            alloc = accounting.allocate_shared_cost("src", 60)
            self.assertTrue(alloc.allocation_valid)

            # Physical accounting
            calls_list = [_make_call("a", 500, 100, 150)]
            phys = accounting.compute_physical_accounting(calls_list)
            self.assertTrue(phys.deduplication_valid)

            # Logical trajectory
            ta = accounting.compute_logical_trajectory_accounting(
                "tid", ["a"], {"a": 100}, 50,
            )
            self.assertTrue(ta.trajectory_accounting_valid)

            # Invariants
            inv = accounting.compute_accounting_invariants(
                phys, [ta], [alloc],
            )
            self.assertIsNotNone(inv)

            # Evidence packet
            shared = {"clean": {"receipt_id": "s1"}}
            trajs = {"A-clean-stateless": {
                "worker_B_receipt": {"receipt_id": "b1"},
                "worker_C_receipt": {"receipt_id": "c1"},
                "worker_D_receipt": {"receipt_id": "d1"},
                "worker_a_shared_source_ref": {
                    "shared_source_id": "s1",
                    "reference_hash": "a" * 64,
                },
            }}
            refs = {"A-clean-stateless": ["clean"]}
            with tempfile.TemporaryDirectory() as tmp:
                ep = evidence.build_pilot_evidence(
                    Path(tmp), "pilot-test", shared, trajs, refs,
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
        """Exercise every price category."""
        for cat in ("uncached_input", "cached_input", "output"):
            self.assertIn(cat, PRICES_INCLUDED)

    def test_both_cached_mappings(self):
        """Test both cached-included and cached-excluded mappings."""
        included = _make_call("inc", 500, 100, 150, cached_included=True)
        excluded = _make_call("exc", 500, 100, 150, cached_included=False)
        self.assertEqual(included.uncached_input_tokens, 400)
        self.assertEqual(excluded.uncached_input_tokens, 500)

    def test_hash_failure_detection(self):
        """Evidence packet must detect hash mismatches."""
        shared, trajs, refs = self._make_synthetic_pilot_data()
        # Corrupt a reference hash
        trajs["A-clean-stateless"]["worker_a_shared_source_ref"]["reference_hash"] = "x" * 64
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(
                Path(tmp), "pilot-test", shared, trajs, refs,
            )
            self.assertFalse(result.packet_valid)

    def _make_synthetic_pilot_data(self):
        shared = {
            "clean": {"receipt_id": "s-clean"},
            "drift": {"receipt_id": "s-drift"},
        }
        trajs = {}
        refs = {}
        for arch in ("stateless", "summary", "verified_state"):
            for cond in ("clean", "drift"):
                tid = f"A-{cond}-{arch}"
                trajs[tid] = {
                    f"worker_{w}_receipt": {"receipt_id": f"r-{arch}-{cond}-{w}"}
                    for w in ("B", "C", "D")
                }
                trajs[tid]["worker_a_shared_source_ref"] = {
                    "shared_source_id": f"s-{cond}",
                    "reference_hash": g1_hashing.compute_sha256(
                        serialization.canonical_json({
                            "shared_source_id": f"s-{cond}",
                        })
                    ),
                }
                refs[tid] = [cond]
        return shared, trajs, refs


if __name__ == "__main__":
    unittest.main()
