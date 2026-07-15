"""G1-A boundary, round-trip, invalid-input, canonical-hash, and
network-denial tests.

Standard library only. Zero network calls. Zero provider spend.
"""

from __future__ import annotations
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the repo root is on sys.path for G0 imports
_repo_root = Path(__file__).resolve().parents[4]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from benchmarks.context_efficiency_v0.g1 import schemas
from benchmarks.context_efficiency_v0.g1 import serialization
from benchmarks.context_efficiency_v0.g1 import hashing
from benchmarks.context_efficiency_v0.g1 import g0_manifest


# ── Helpers ────────────────────────────────────────────────────────────


def _make_valid_receipt_dict(**overrides) -> dict:
    """Produce a valid ProviderCallReceipt dict for testing.

    The receipt_id is computed from the canonical self-excluding hash
    of the rest of the dict, so it always matches.
    """
    # Build without receipt_id first
    d = {
        "pilot_id": "g1-e2-20260715",
        "run_id": "run-001",
        "provider_invocation_id": "inv-001",
        "attempt_index": 0,
        "trajectory_id": "A-clean-verified_state",
        "logical_session_id": "A-clean-verified_state-B",
        "model_session_id": "A-clean-verified_state-B-m0",
        "worker_label": "B",
        "stage": 2,
        "requested_provider": "openai",
        "requested_model": "gpt-4o",
        "returned_model": "gpt-4o-2026-05-15",
        "model_identity_valid": True,
        "provider_request_id": "req_abc123",
        "request_dispatched": True,
        "temperature_milli": 0,
        "max_tokens": 4096,
        "system_prompt_sha256": "b" * 64,
        "prompt_sha256": "c" * 64,
        "tool_definitions_sha256": None,
        "prompt_tokens_total": 500,
        "cached_input_tokens": 100,
        "uncached_input_tokens": 400,
        "completion_tokens": 150,
        "provider_usage_status": "reported",
        "response_sha256": "d" * 64,
        "provider_finish_reason": "stop",
        "response_present": True,
        "sanitized_error_sha256": None,
        "normalized_execution_status": "completed",
        "start_timestamp": "2026-07-15T12:00:00Z",
        "end_timestamp": "2026-07-15T12:00:02Z",
        "duration_ms": 2000,
        "calculated_micro_usd_cost": 5000,
        "provider_reported_cost_micro_usd": 5000,
        "pricing_source": "pricing_catalog.json#openai/gpt-4o",
        "call_accounting_valid": True,
        "raw_prompt_sha256": "e" * 64,
        "raw_response_sha256": "f" * 64,
        "shared_source_id": None,
        "parent_receipt_ids": [],
        "tool_call_receipt_ids": [],
        "contamination_flags": [],
        "warnings": [],
    }
    d.update(overrides)
    # Compute receipt_id from canonical self-excluding hash
    if "receipt_id" not in overrides:
        d["receipt_id"] = hashing.compute_receipt_hash(d)
    return d


def _make_valid_score_dict(**overrides) -> dict:
    """Produce a valid G1TrajectoryScore dict for testing."""
    d = {
        "trajectory_id": "A-clean-verified_state",
        "architecture": "verified_state",
        "task": "A",
        "condition": "clean",
        "diagnostic_metrics": [
            {"name": "stage_completion", "value": 1.0},
            {"name": "checkpoint_validity", "value": 1.0},
        ],
        "worker_count": 4,
        "stages_attempted": 4,
        "stages_completed": 4,
        "warnings": [],
    }
    d.update(overrides)
    return d


def _make_valid_evidence_dict(**overrides) -> dict:
    """Produce a valid G1PilotEvidence dict for testing."""
    d = {
        "pilot_id": "g1-e2-20260715",
        "run_id": "run-001",
        "contract_sha256": "4400ae315386812049431f359447dfbce74fb208caf9f0e0625b77826172d6f6",
        "g0_base_commit": "9222c0a66a9e786ca9a9f54194d074b42158b783",
        "g0_manifest_sha256": "a" * 64,
        "pilot_config_sha256": "b" * 64,
        "trajectory_ids": ["A-clean-stateless", "A-clean-summary"],
        "provider_receipt_ids": ["rct_001", "rct_002"],
        "shared_source_receipt_ids": ["src_clean_001"],
        "trajectory_receipt_ids": ["tr_001", "tr_002"],
        "checksum": "",
    }
    d.update(overrides)
    # Compute checksum from canonical self-excluding hash
    # unless the caller explicitly set checksum to a non-empty value
    # or explicitly set it to empty string (for testing empty rejection)
    if "checksum" not in overrides:
        d["checksum"] = hashing.compute_manifest_hash(d)
    elif overrides.get("checksum") and overrides["checksum"] != "":
        pass  # Use caller-supplied value
    return d


# ── 1. Boundary tests ──────────────────────────────────────────────────


class TestProviderCallReceiptBoundary(unittest.TestCase):
    """ProviderCallReceipt schema boundary tests."""

    def test_valid_minimal(self):
        d = _make_valid_receipt_dict()
        errors = schemas.validate_provider_call_receipt(d)
        self.assertEqual(errors, [])

    def test_valid_shared_source(self):
        d = _make_valid_receipt_dict(
            worker_label="A", stage=1, shared_source_id="shared-clean-001",
        )
        errors = schemas.validate_provider_call_receipt(d)
        self.assertEqual(errors, [])

    def test_valid_error_receipt(self):
        d = _make_valid_receipt_dict(
            response_sha256=None, provider_finish_reason="error",
            response_present=False,
            sanitized_error_sha256="a" * 64,
            normalized_execution_status="provider_error",
            prompt_tokens_total=None, cached_input_tokens=None,
            uncached_input_tokens=None, completion_tokens=None,
            provider_usage_status="error",
            calculated_micro_usd_cost=None, call_accounting_valid=False,
            raw_response_sha256=None,
        )
        errors = schemas.validate_provider_call_receipt(d)
        self.assertEqual(errors, [])

    def test_rejects_empty_receipt_id(self):
        d = _make_valid_receipt_dict(receipt_id="")
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("receipt_id" in e for e in errors))

    def test_rejects_bool_as_int(self):
        d = _make_valid_receipt_dict(attempt_index=True)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("attempt_index" in e for e in errors))

    def test_rejects_negative_stage(self):
        d = _make_valid_receipt_dict(stage=0)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("stage" in e for e in errors))

    def test_rejects_stage_above_4(self):
        d = _make_valid_receipt_dict(stage=5)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("stage" in e for e in errors))

    def test_rejects_invalid_worker_label(self):
        d = _make_valid_receipt_dict(worker_label="E")
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("worker_label" in e for e in errors))

    def test_rejects_invalid_usage_status(self):
        d = _make_valid_receipt_dict(provider_usage_status="unknown")
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("provider_usage_status" in e for e in errors))

    def test_rejects_invalid_execution_status(self):
        d = _make_valid_receipt_dict(normalized_execution_status="unknown")
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("normalized_execution_status" in e for e in errors))

    def test_rejects_non_null_tool_definitions(self):
        d = _make_valid_receipt_dict(tool_definitions_sha256="abc")
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("tool_definitions_sha256" in e for e in errors))

    def test_rejects_negative_temperature(self):
        d = _make_valid_receipt_dict(temperature_milli=-1)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("temperature_milli" in e for e in errors))

    def test_rejects_excessive_temperature(self):
        d = _make_valid_receipt_dict(temperature_milli=200_000)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("temperature_milli" in e for e in errors))

    def test_rejects_zero_duration(self):
        d = _make_valid_receipt_dict(duration_ms=0)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("duration_ms" in e for e in errors))

    def test_rejects_negative_duration(self):
        d = _make_valid_receipt_dict(duration_ms=-100)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("duration_ms" in e for e in errors))

    def test_rejects_zero_max_tokens(self):
        d = _make_valid_receipt_dict(max_tokens=0)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("max_tokens" in e for e in errors))

    def test_rejects_negative_attempt(self):
        d = _make_valid_receipt_dict(attempt_index=-1)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("attempt_index" in e for e in errors))

    def test_rejects_non_string_in_tuple_field(self):
        d = _make_valid_receipt_dict(parent_receipt_ids=[123])
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("parent_receipt_ids" in e for e in errors))

    def test_rejects_malformed_hash(self):
        d = _make_valid_receipt_dict(system_prompt_sha256="not-a-sha256")
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("system_prompt_sha256" in e for e in errors))

    def test_rejects_uppercase_hash(self):
        d = _make_valid_receipt_dict(system_prompt_sha256="A" * 64)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("system_prompt_sha256" in e for e in errors))

    def test_rejects_non_rfc3339_timestamp(self):
        d = _make_valid_receipt_dict(start_timestamp="2026-07-15 12:00:00")
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("start_timestamp" in e for e in errors))

    def test_rejects_negative_token_count(self):
        d = _make_valid_receipt_dict(prompt_tokens_total=-1)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("prompt_tokens_total" in e for e in errors))

    def test_rejects_negative_cost(self):
        d = _make_valid_receipt_dict(calculated_micro_usd_cost=-1)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("calculated_micro_usd_cost" in e for e in errors))

    def test_receipt_id_must_match_hash(self):
        """receipt_id must match its canonical self-excluding hash."""
        d = _make_valid_receipt_dict()
        d["receipt_id"] = "x" * 64  # Wrong hash
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("receipt_id" in e and "canonical hash" in e for e in errors))

    def test_from_json_rejects_invalid_data(self):
        """from_json must reject invalid data with ValueError."""
        d = _make_valid_receipt_dict(stage=99)
        with self.assertRaises(ValueError):
            schemas.ProviderCallReceipt.from_json(d)

    def test_call_accounting_valid_requires_tokens(self):
        """call_accounting_valid=True requires all token/cost fields non-null."""
        d = _make_valid_receipt_dict(
            call_accounting_valid=True,
            prompt_tokens_total=None,
        )
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("prompt_tokens_total" in e for e in errors))

    def test_call_accounting_valid_requires_cost(self):
        """call_accounting_valid=True requires calculated_micro_usd_cost non-null."""
        d = _make_valid_receipt_dict(
            call_accounting_valid=True,
            calculated_micro_usd_cost=None,
        )
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("calculated_micro_usd_cost" in e for e in errors))

    def test_model_identity_valid_requires_returned_model(self):
        """model_identity_valid=True requires returned_model non-null."""
        d = _make_valid_receipt_dict(
            model_identity_valid=True,
            returned_model=None,
        )
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("returned_model" in e for e in errors))

    def test_model_identity_valid_with_returned_model_passes(self):
        """model_identity_valid=True with non-null returned_model passes."""
        d = _make_valid_receipt_dict(
            model_identity_valid=True,
            returned_model="gpt-4o-2026-05-15",
        )
        errors = schemas.validate_provider_call_receipt(d)
        self.assertEqual(errors, [])


class TestTrajectoryScoreBoundary(unittest.TestCase):
    """G1TrajectoryScore schema boundary tests."""

    def test_valid_minimal(self):
        d = _make_valid_score_dict()
        errors = schemas.validate_trajectory_score(d)
        self.assertEqual(errors, [])

    def test_valid_empty_metrics(self):
        d = _make_valid_score_dict(diagnostic_metrics=[])
        errors = schemas.validate_trajectory_score(d)
        self.assertEqual(errors, [])

    def test_rejects_invalid_architecture(self):
        d = _make_valid_score_dict(architecture="unknown")
        errors = schemas.validate_trajectory_score(d)
        self.assertTrue(any("architecture" in e for e in errors))

    def test_rejects_invalid_task(self):
        d = _make_valid_score_dict(task="D")
        errors = schemas.validate_trajectory_score(d)
        self.assertTrue(any("task" in e for e in errors))

    def test_rejects_invalid_condition(self):
        d = _make_valid_score_dict(condition="unknown")
        errors = schemas.validate_trajectory_score(d)
        self.assertTrue(any("condition" in e for e in errors))

    def test_rejects_bool_as_int_for_worker_count(self):
        d = _make_valid_score_dict(worker_count=True)
        errors = schemas.validate_trajectory_score(d)
        self.assertTrue(any("worker_count" in e for e in errors))

    def test_rejects_non_dict_in_metrics(self):
        d = _make_valid_score_dict(diagnostic_metrics=["not_a_dict"])
        errors = schemas.validate_trajectory_score(d)
        self.assertTrue(any("diagnostic_metrics" in e for e in errors))

    def test_from_json_rejects_invalid_data(self):
        d = _make_valid_score_dict(architecture="unknown")
        with self.assertRaises(ValueError):
            schemas.G1TrajectoryScore.from_json(d)


class TestPilotEvidenceBoundary(unittest.TestCase):
    """G1PilotEvidence schema boundary tests."""

    def test_valid_minimal(self):
        d = _make_valid_evidence_dict()
        errors = schemas.validate_pilot_evidence(d)
        self.assertEqual(errors, [])

    def test_rejects_malformed_sha256(self):
        d = _make_valid_evidence_dict(contract_sha256="not-a-sha256")
        errors = schemas.validate_pilot_evidence(d)
        self.assertTrue(any("contract_sha256" in e for e in errors))

    def test_rejects_uppercase_hex(self):
        d = _make_valid_evidence_dict(contract_sha256="A" * 64)
        errors = schemas.validate_pilot_evidence(d)
        self.assertTrue(any("contract_sha256" in e for e in errors))

    def test_rejects_short_sha256(self):
        d = _make_valid_evidence_dict(contract_sha256="a" * 63)
        errors = schemas.validate_pilot_evidence(d)
        self.assertTrue(any("contract_sha256" in e for e in errors))

    def test_rejects_malformed_commit_sha(self):
        d = _make_valid_evidence_dict(g0_base_commit="not-a-commit")
        errors = schemas.validate_pilot_evidence(d)
        self.assertTrue(any("g0_base_commit" in e for e in errors))

    def test_checksum_validates(self):
        """A valid checksum must pass."""
        d = _make_valid_evidence_dict()
        errors = schemas.validate_pilot_evidence(d)
        self.assertEqual(errors, [])

    def test_checksum_mismatch_detected(self):
        """A wrong checksum must be detected."""
        d = _make_valid_evidence_dict(checksum="x" * 64)
        errors = schemas.validate_pilot_evidence(d)
        self.assertTrue(any("checksum" in e for e in errors))

    def test_checksum_rejects_empty(self):
        """An empty checksum must be rejected."""
        d = _make_valid_evidence_dict(checksum="")
        errors = schemas.validate_pilot_evidence(d)
        self.assertTrue(any("checksum" in e for e in errors))

    def test_checksum_rejects_missing(self):
        """A missing checksum must be rejected."""
        d = _make_valid_evidence_dict()
        del d["checksum"]
        errors = schemas.validate_pilot_evidence(d)
        self.assertTrue(any("checksum" in e for e in errors))

    def test_from_json_rejects_invalid_data(self):
        d = _make_valid_evidence_dict(contract_sha256="x" * 64)
        with self.assertRaises(ValueError):
            schemas.G1PilotEvidence.from_json(d)


# ── 2. Round-trip tests ────────────────────────────────────────────────


class TestRoundTrip(unittest.TestCase):
    """Dataclass -> dict -> JSON -> dict -> dataclass round-trips."""

    def test_provider_call_receipt_round_trip(self):
        d = _make_valid_receipt_dict()
        obj = schemas.ProviderCallReceipt.from_json(d)
        self.assertEqual(obj.receipt_id, d["receipt_id"])
        self.assertEqual(obj.worker_label, d["worker_label"])
        d2 = obj.to_json()
        self.assertEqual(d2["receipt_id"], d["receipt_id"])
        self.assertIsInstance(d2["parent_receipt_ids"], list)

    def test_trajectory_score_round_trip(self):
        d = _make_valid_score_dict()
        obj = schemas.G1TrajectoryScore.from_json(d)
        self.assertEqual(obj.trajectory_id, d["trajectory_id"])
        d2 = obj.to_json()
        self.assertEqual(d2["trajectory_id"], d["trajectory_id"])

    def test_pilot_evidence_round_trip(self):
        d = _make_valid_evidence_dict()
        obj = schemas.G1PilotEvidence.from_json(d)
        self.assertEqual(obj.pilot_id, d["pilot_id"])
        d2 = obj.to_json()
        self.assertEqual(d2["pilot_id"], d["pilot_id"])

    def test_from_json_rejects_extra_fields(self):
        d = _make_valid_receipt_dict()
        d["extra_field"] = "should_not_be_here"
        with self.assertRaises(ValueError):
            schemas.ProviderCallReceipt.from_json(d)

    def test_from_json_rejects_missing_fields(self):
        d = _make_valid_receipt_dict()
        del d["receipt_id"]
        with self.assertRaises(ValueError):
            schemas.ProviderCallReceipt.from_json(d)


# ── 3. Canonical serialization tests ───────────────────────────────────


class TestCanonicalSerialization(unittest.TestCase):
    """Strict JSON serialization per contract section 21."""

    def test_sorted_keys(self):
        a = {"z": 1, "a": 2}
        b = {"a": 2, "z": 1}
        self.assertEqual(
            serialization.canonical_json(a),
            serialization.canonical_json(b),
        )

    def test_fixed_separators(self):
        result = serialization.canonical_json({"a": 1, "b": 2})
        self.assertNotIn(" ", result)
        self.assertIn('{"a":1,', result)

    def test_ensure_ascii(self):
        result = serialization.canonical_json({"a": "\u00e9"})
        self.assertIn("\\u00e9", result)

    def test_rejects_nan(self):
        with self.assertRaises(ValueError):
            serialization.canonical_json({"a": float("nan")})

    def test_rejects_infinity(self):
        with self.assertRaises(ValueError):
            serialization.canonical_json({"a": float("inf")})

    def test_rejects_negative_infinity(self):
        with self.assertRaises(ValueError):
            serialization.canonical_json({"a": float("-inf")})

    def test_rejects_set(self):
        with self.assertRaises(ValueError):
            serialization.canonical_json({"a": {1, 2, 3}})

    def test_rejects_non_string_key(self):
        with self.assertRaises(ValueError):
            serialization.canonical_json({1: "value"})

    def test_rejects_unsupported_type(self):
        class Custom:
            pass
        with self.assertRaises(ValueError):
            serialization.canonical_json(Custom())

    def test_accepts_list(self):
        result = serialization.canonical_json({"a": [1, 2, 3]})
        self.assertIsInstance(result, str)

    def test_accepts_bool(self):
        result = serialization.canonical_json(True)
        self.assertIsInstance(result, str)

    def test_accepts_none(self):
        result = serialization.canonical_json(None)
        self.assertEqual(result, "null")

    def test_empty_dict(self):
        result = serialization.canonical_json({})
        self.assertEqual(result, "{}")

    def test_empty_list(self):
        result = serialization.canonical_json([])
        self.assertEqual(result, "[]")

    def test_nested_structures(self):
        data = {"outer": {"inner": [1, {"key": "val"}]}}
        result = serialization.canonical_json(data)
        parsed = json.loads(result)
        self.assertEqual(parsed, data)

    def test_stable_array_ordering(self):
        result = serialization.canonical_json({"a": [3, 1, 2]})
        self.assertIn("[3,1,2]", result)

    def test_parse_canonical_rejects_nan(self):
        with self.assertRaises(ValueError):
            serialization.parse_canonical('{"a": NaN}')

    def test_parse_canonical_rejects_inf(self):
        with self.assertRaises(ValueError):
            serialization.parse_canonical('{"a": Infinity}')

    def test_parse_canonical_utf8(self):
        data = '{"a": "\\u00e9"}'
        result = serialization.parse_canonical(data)
        self.assertEqual(result["a"], "\u00e9")


# ── 4. Canonical hash tests ────────────────────────────────────────────


class TestCanonicalHashing(unittest.TestCase):
    """SHA-256 receipt hashing with self-exclusion per contract section 21."""

    def test_receipt_hash_excludes_receipt_id(self):
        d1 = _make_valid_receipt_dict(receipt_id="a" * 64)
        d2 = _make_valid_receipt_dict(receipt_id="b" * 64)
        h1 = hashing.compute_receipt_hash(d1)
        h2 = hashing.compute_receipt_hash(d2)
        self.assertEqual(h1, h2)

    def test_receipt_hash_changes_with_content(self):
        d1 = _make_valid_receipt_dict(pilot_id="pilot-a")
        d2 = _make_valid_receipt_dict(pilot_id="pilot-b")
        h1 = hashing.compute_receipt_hash(d1)
        h2 = hashing.compute_receipt_hash(d2)
        self.assertNotEqual(h1, h2)

    def test_manifest_hash_excludes_checksum(self):
        d1 = _make_valid_evidence_dict(checksum="a" * 64)
        d2 = _make_valid_evidence_dict(checksum="b" * 64)
        h1 = hashing.compute_manifest_hash(d1)
        h2 = hashing.compute_manifest_hash(d2)
        self.assertEqual(h1, h2)

    def test_manifest_hash_changes_with_content(self):
        d1 = _make_valid_evidence_dict(pilot_id="pilot-a")
        d2 = _make_valid_evidence_dict(pilot_id="pilot-b")
        h1 = hashing.compute_manifest_hash(d1)
        h2 = hashing.compute_manifest_hash(d2)
        self.assertNotEqual(h1, h2)

    def test_sha256_output_format(self):
        h = hashing.compute_sha256("test")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_sha256_deterministic(self):
        self.assertEqual(
            hashing.compute_sha256("test"),
            hashing.compute_sha256("test"),
        )


# ── 5. G0 frozen manifest tests ──────────────────────────────────────


class TestG0Manifest(unittest.TestCase):
    """Frozen G0 file manifest bound to G0_BASE_COMMIT."""

    def test_all_13_files_present(self):
        self.assertEqual(len(g0_manifest.G0_FROZEN_FILES), 13)

    def test_frozen_manifest_has_13_entries(self):
        self.assertEqual(len(g0_manifest.FROZEN_MANIFEST), 13)

    def test_compute_manifest_succeeds(self):
        manifest = g0_manifest.compute_frozen_manifest(_repo_root)
        self.assertEqual(len(manifest), 13)
        for path, sha in manifest.items():
            self.assertEqual(len(sha), 64)
            self.assertTrue(all(c in "0123456789abcdef" for c in sha))

    def test_verify_manifest_against_frozen_constants(self):
        mismatches = g0_manifest.verify_frozen_manifest(
            g0_manifest.FROZEN_MANIFEST, _repo_root
        )
        self.assertEqual(
            mismatches, [],
            f"G0 frozen files have changed from G0_BASE_COMMIT: {mismatches}"
        )

    def test_verify_detects_mismatch(self):
        manifest = g0_manifest.compute_frozen_manifest(_repo_root)
        bad_key = g0_manifest.G0_FROZEN_FILES[0]
        manifest[bad_key] = "x" * 64
        mismatches = g0_manifest.verify_frozen_manifest(manifest, _repo_root)
        self.assertTrue(len(mismatches) > 0)

    def test_worktree_manifest_matches_frozen(self):
        """Current worktree files must match frozen manifest hashes."""
        worktree = g0_manifest.compute_worktree_manifest(_repo_root)
        mismatches = []
        for path, expected_sha in g0_manifest.FROZEN_MANIFEST.items():
            current_sha = worktree.get(path)
            if current_sha != expected_sha:
                mismatches.append(
                    f"{path}: expected {expected_sha[:16]}..., "
                    f"got {current_sha[:16] if current_sha else 'MISSING'}..."
                )
        self.assertEqual(
            mismatches, [],
            f"Worktree G0 files differ from frozen manifest: {mismatches}"
        )

    def test_worktree_detects_changed_file(self):
        """Copy frozen files to temp dir, mutate one, verify detection."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmp:
            # Copy all 13 frozen files preserving relative paths
            for rel_path in g0_manifest.G0_FROZEN_FILES:
                src = _repo_root / rel_path
                dst = Path(tmp) / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

            # Verify temporary worktree matches frozen manifest
            worktree = g0_manifest.compute_worktree_manifest(Path(tmp))
            mismatches = []
            for path, expected_sha in g0_manifest.FROZEN_MANIFEST.items():
                current_sha = worktree.get(path)
                if current_sha != expected_sha:
                    mismatches.append(path)
            self.assertEqual(
                mismatches, [],
                "Temp worktree should match frozen manifest before mutation"
            )

            # Mutate one copied file
            mutated_path = Path(tmp) / g0_manifest.G0_FROZEN_FILES[0]
            mutated_path.write_text(mutated_path.read_text() + "\n# MUTATED\n")

            # Verify worktree-manifest check reports the exact mismatch
            worktree_after = g0_manifest.compute_worktree_manifest(Path(tmp))
            mismatches_after = []
            for path, expected_sha in g0_manifest.FROZEN_MANIFEST.items():
                current_sha = worktree_after.get(path)
                if current_sha != expected_sha:
                    mismatches_after.append(path)
            self.assertEqual(
                len(mismatches_after), 1,
                f"Expected exactly 1 mismatch after mutating one file, got: {mismatches_after}"
            )
            self.assertEqual(
                mismatches_after[0], g0_manifest.G0_FROZEN_FILES[0],
                f"Expected mismatch on {g0_manifest.G0_FROZEN_FILES[0]}, got {mismatches_after[0]}"
            )

    def test_g0_base_commit_is_40_hex(self):
        commit = g0_manifest.G0_BASE_COMMIT
        self.assertEqual(len(commit), 40)
        self.assertTrue(all(c in "0123456789abcdef" for c in commit))


# ── 6. Network-denial tests ────────────────────────────────────────────


class TestNetworkDenial(unittest.TestCase):
    """Prove G1-A makes zero network or provider calls."""

    def test_no_network_imports_in_g1_modules(self):
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

    def test_no_socket_import_in_g1_modules(self):
        g1_dir = _repo_root / "benchmarks" / "context_efficiency_v0" / "g1"
        for py_file in g1_dir.glob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            content = py_file.read_text()
            if "import socket" in content or "from socket" in content:
                self.fail(f"{py_file.name} imports socket")

    def test_active_network_denial_socket_connect(self):
        """Patch socket.create_connection and verify G1-A modules work."""
        _original_create = socket.create_connection
        _original_connect = socket.socket.connect

        calls = []

        def _deny_create(*args, **kwargs):
            calls.append(("create_connection", args, kwargs))
            raise OSError("Network denied by G1-A test")

        def _deny_connect(self, *args, **kwargs):
            calls.append(("connect", args, kwargs))
            raise OSError("Network denied by G1-A test")

        socket.create_connection = _deny_create
        socket.socket.connect = _deny_connect

        try:
            # Exercise all G1-A modules
            d = _make_valid_receipt_dict()
            errors = schemas.validate_provider_call_receipt(d)
            self.assertEqual(errors, [])

            obj = schemas.ProviderCallReceipt.from_json(d)
            self.assertIsNotNone(obj)

            j = serialization.canonical_json({"test": "value"})
            self.assertIsInstance(j, str)

            h = hashing.compute_sha256("test")
            self.assertEqual(len(h), 64)

            manifest = g0_manifest.compute_frozen_manifest(_repo_root)
            self.assertEqual(len(manifest), 13)

            # Verify no network calls were attempted
            self.assertEqual(
                calls, [],
                f"Network calls were attempted: {calls}"
            )
        finally:
            socket.create_connection = _original_create
            socket.socket.connect = _original_connect

    def test_active_network_denial_socket_socket(self):
        """Patch socket.socket.connect and verify G1-A modules work."""
        _original_connect = socket.socket.connect
        calls = []

        def _deny_connect(self, *args, **kwargs):
            calls.append(("socket.connect", args, kwargs))
            raise OSError("Network denied by G1-A test")

        socket.socket.connect = _deny_connect

        try:
            d = _make_valid_receipt_dict()
            obj = schemas.ProviderCallReceipt.from_json(d)
            self.assertIsNotNone(obj)

            j = serialization.canonical_json({"a": 1})
            self.assertIsInstance(j, str)

            h = hashing.compute_sha256("test")
            self.assertEqual(len(h), 64)

            self.assertEqual(
                calls, [],
                f"Network calls were attempted: {calls}"
            )
        finally:
            socket.socket.connect = _original_connect

    def test_serialization_no_network(self):
        result = serialization.canonical_json({"test": "value"})
        self.assertIsInstance(result, str)

    def test_hashing_no_network(self):
        h = hashing.compute_sha256("test")
        self.assertEqual(len(h), 64)

    def test_validation_no_network(self):
        d = _make_valid_receipt_dict()
        errors = schemas.validate_provider_call_receipt(d)
        self.assertEqual(errors, [])


# ── 7. Schema edge cases ───────────────────────────────────────────────


class TestSchemaEdgeCases(unittest.TestCase):
    """Edge cases for schema validation."""

    def test_empty_string_rejected(self):
        d = _make_valid_receipt_dict(pilot_id="")
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("pilot_id" in e for e in errors))

    def test_none_instead_of_string_rejected(self):
        d = _make_valid_receipt_dict(pilot_id=None)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("pilot_id" in e for e in errors))

    def test_float_instead_of_int_rejected(self):
        d = _make_valid_receipt_dict(stage=2.0)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("stage" in e for e in errors))

    def test_string_instead_of_int_rejected(self):
        d = _make_valid_receipt_dict(stage="2")
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("stage" in e for e in errors))

    def test_int_instead_of_bool_rejected(self):
        d = _make_valid_receipt_dict(request_dispatched=1)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertTrue(any("request_dispatched" in e for e in errors))

    def test_all_worker_labels_valid(self):
        for label in ("A", "B", "C", "D"):
            d = _make_valid_receipt_dict(worker_label=label)
            errors = schemas.validate_provider_call_receipt(d)
            self.assertEqual(errors, [])

    def test_all_execution_statuses_valid(self):
        for status in ("completed", "timeout", "provider_error",
                       "policy_denied", "no_response"):
            d = _make_valid_receipt_dict(normalized_execution_status=status)
            errors = schemas.validate_provider_call_receipt(d)
            self.assertEqual(errors, [])

    def test_all_usage_statuses_valid(self):
        for status in ("reported", "partial", "missing", "error"):
            d = _make_valid_receipt_dict(provider_usage_status=status)
            errors = schemas.validate_provider_call_receipt(d)
            self.assertEqual(errors, [])

    def test_all_finish_reasons_valid(self):
        for reason in ("stop", "length", "tool_calls", "error"):
            d = _make_valid_receipt_dict(provider_finish_reason=reason)
            errors = schemas.validate_provider_call_receipt(d)
            self.assertEqual(errors, [])

    def test_null_finish_reason_valid(self):
        d = _make_valid_receipt_dict(provider_finish_reason=None)
        errors = schemas.validate_provider_call_receipt(d)
        self.assertEqual(errors, [])

    def test_rfc3339_timestamps_valid(self):
        for ts in ("2026-07-15T12:00:00Z",
                   "2026-07-15T12:00:00.000Z",
                   "2026-07-15T12:00:00+00:00",
                   "2026-07-15T12:00:00-05:00"):
            d = _make_valid_receipt_dict(start_timestamp=ts)
            errors = schemas.validate_provider_call_receipt(d)
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
