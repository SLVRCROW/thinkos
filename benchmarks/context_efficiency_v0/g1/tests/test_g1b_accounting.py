"""G1-B accounting and reconstructable evidence tests (zero network/spend)."""

from __future__ import annotations

import copy
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = Path(__file__).resolve().parents[4]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from benchmarks.context_efficiency_v0.g1 import accounting, evidence, g0_manifest, hashing, schemas, serialization


PRICES = {"uncached_input": 2_500_000, "cached_input": 1_250_000, "output": 10_000_000}


def make_call(invocation_id: str, prompt: int | None = 5, cached: int | None = 1,
              completion: int | None = 2, included: bool = True) -> accounting.CallAccounting:
    calculated = accounting.calculate_call_cost(prompt, cached, completion, PRICES, included)
    return accounting.CallAccounting(
        provider_invocation_id=invocation_id,
        prompt_tokens_total=calculated.prompt_tokens_total,
        cached_input_tokens=calculated.cached_input_tokens,
        uncached_input_tokens=calculated.uncached_input_tokens,
        completion_tokens=calculated.completion_tokens,
        calculated_micro_usd_cost=calculated.calculated_micro_usd_cost,
        call_accounting_valid=calculated.call_accounting_valid,
        errors=calculated.errors,
    )


def make_provider_receipt(
    pilot_id: str,
    run_id: str,
    trajectory_id: str,
    worker: str,
    stage: int,
    invocation_id: str,
    prompt: str,
    response: str,
    cost: int | None = None,
    shared_source_id: str | None = None,
    parent_receipt_ids: list[str] | None = None,
) -> dict:
    response_hash = hashing.compute_sha256(response)
    # Compute cost from hardcoded tokens if not provided
    if cost is None:
        result = accounting.calculate_call_cost(3, 1, 1, PRICES, True)
        cost = result.calculated_micro_usd_cost
    receipt = {
        "receipt_id": "",
        "pilot_id": pilot_id,
        "run_id": run_id,
        "provider_invocation_id": invocation_id,
        "attempt_index": 0,
        "trajectory_id": trajectory_id,
        "logical_session_id": f"{trajectory_id}-{worker}",
        "model_session_id": f"{trajectory_id}-{worker}-m0",
        "worker_label": worker,
        "stage": stage,
        "requested_provider": "synthetic",
        "requested_model": "synthetic-model-v1",
        "returned_model": "synthetic-model-v1",
        "model_identity_valid": True,
        "provider_request_id": f"request-{invocation_id}",
        "request_dispatched": True,
        "temperature_milli": 0,
        "max_tokens": 4096,
        "system_prompt_sha256": hashing.compute_sha256("synthetic system"),
        "prompt_sha256": hashing.compute_sha256(prompt),
        "tool_definitions_sha256": None,
        "prompt_tokens_total": 3,
        "cached_input_tokens": 1,
        "uncached_input_tokens": 2,
        "completion_tokens": 1,
        "provider_usage_status": "reported",
        "response_sha256": response_hash,
        "provider_finish_reason": "stop",
        "response_present": True,
        "sanitized_error_sha256": None,
        "normalized_execution_status": "completed",
        "start_timestamp": "2026-07-15T12:00:00Z",
        "end_timestamp": "2026-07-15T12:00:01Z",
        "duration_ms": 1000,
        "calculated_micro_usd_cost": cost,
        "provider_reported_cost_micro_usd": None,
        "pricing_source": "synthetic-pricing-v1",
        "call_accounting_valid": True,
        "raw_prompt_sha256": hashing.compute_sha256(prompt),
        "raw_response_sha256": response_hash,
        "shared_source_id": shared_source_id,
        "parent_receipt_ids": parent_receipt_ids or [],
        "tool_call_receipt_ids": [],
        "contamination_flags": [],
        "warnings": [],
    }
    receipt["receipt_id"] = hashing.compute_receipt_hash(receipt)
    schemas.ProviderCallReceipt.from_json(receipt)
    return receipt


def make_checkpoint(worker: str, stage: int, artifact_path: str, response: str) -> dict:
    checkpoint = {
        "receipt_id": "",
        "stage_number": stage,
        "worker_label": worker,
        "artifact_path": artifact_path,
        "artifact_sha256": hashing.compute_sha256(response),
        "test_results": {"synthetic_fixture": True},
        "timestamp": 1.0,
        "session_token_count": 4,
    }
    checkpoint["receipt_id"] = hashing.compute_receipt_hash(checkpoint)
    return checkpoint


def make_score(trajectory_id: str) -> dict:
    _, condition, architecture = trajectory_id.split("-", 2)
    return schemas.G1TrajectoryScore(
        trajectory_id=trajectory_id,
        architecture=architecture,
        task="A",
        condition=condition,
        diagnostic_metrics=({"name": "synthetic_fixture", "value": 1},),
        worker_count=4,
        stages_attempted=4,
        stages_completed=4,
        warnings=(),
    ).to_json()


def make_packet() -> evidence.EvidencePacketInput:
    pilot_id = "g1-b-synthetic-pilot"
    run_id = "g1-b-synthetic-run"
    shared_sources: dict[str, dict] = {}
    shared_allocations = []
    physical_ids: list[str] = []
    physical_costs: dict[str, int] = {}

    for condition in ("clean", "drift"):
        source_id = f"source-A-{condition}"
        invocation_id = f"inv-A-{condition}"
        prompt = f"synthetic shared prompt {condition}\n"
        response = f"synthetic shared response {condition}\n"
        provider = make_provider_receipt(
            pilot_id, run_id, f"A-{condition}-shared", "A", 1,
            invocation_id, prompt, response, shared_source_id=source_id,
        )
        cost = provider["calculated_micro_usd_cost"]
        checkpoint = make_checkpoint(
            "A", 1, f"shared_sources/{condition}/raw/response.txt", response
        )
        shared_sources[condition] = {
            "shared_source_id": source_id,
            "provider_receipt": provider,
            "checkpoint_receipt": checkpoint,
            "raw_prompt": prompt,
            "raw_response": response,
        }
        allocation = accounting.allocate_shared_cost(source_id, cost)
        shared_allocations.append({
            "shared_source_id": source_id,
            "physical_calculated_cost": cost,
            "allocations": allocation.allocations,
            "sum_allocated": allocation.sum_allocated,
        })
        physical_ids.append(invocation_id)
        physical_costs[invocation_id] = cost

    trajectories: dict[str, dict] = {}
    logical_trajectories = []
    all_scores = []
    allocation_map = dict(zip(evidence.CONDITIONS, shared_allocations))
    for trajectory_id in evidence.TRAJECTORY_ORDER:
        _, condition, architecture = trajectory_id.split("-", 2)
        source = shared_sources[condition]
        allocated = allocation_map[condition]["allocations"][architecture]
        workers: dict[str, dict] = {}
        successor_ids: list[str] = []
        successor_cost = 0
        for worker in evidence.WORKERS:
            stage = evidence.WORKER_STAGES[worker]
            invocation_id = f"inv-{condition}-{architecture}-{worker}"
            prompt = f"synthetic {trajectory_id} worker {worker} prompt\n"
            response = f"synthetic {trajectory_id} worker {worker} response\n"
            provider = make_provider_receipt(
                pilot_id, run_id, trajectory_id, worker, stage, invocation_id,
                prompt, response,
                parent_receipt_ids=[source["provider_receipt"]["receipt_id"]],
            )
            cost = provider["calculated_micro_usd_cost"]
            checkpoint = make_checkpoint(
                worker,
                stage,
                f"trajectories/{trajectory_id}/worker_{worker}/raw/response.txt",
                response,
            )
            workers[worker] = {
                "provider_receipt": provider,
                "checkpoint_receipt": checkpoint,
                "raw_prompt": prompt,
                "raw_response": response,
            }
            physical_ids.append(invocation_id)
            physical_costs[invocation_id] = cost
            successor_ids.append(invocation_id)
            successor_cost += cost

        score = make_score(trajectory_id)
        all_scores.append(score)
        trajectories[trajectory_id] = {
            "config": {
                "pilot_id": pilot_id,
                "run_id": run_id,
                "trajectory_id": trajectory_id,
                "task": "A",
                "condition": condition,
                "architecture": architecture,
                "synthetic": True,
            },
            "score": score,
            "result": {
                "trajectory_id": trajectory_id,
                "execution_status": "COMPLETE",
                "integrity_valid": True,
                "contamination_detected": False,
                "accounting_valid": True,
                "task_score_valid": True,
                "warnings": [],
            },
            "worker_A_shared_source_ref": {
                "shared_source_id": source["shared_source_id"],
                "provider_invocation_id": source["provider_receipt"]["provider_invocation_id"],
                "provider_receipt_id": source["provider_receipt"]["receipt_id"],
                "checkpoint_receipt_id": source["checkpoint_receipt"]["receipt_id"],
                "condition": condition,
                "allocated_shared_source_cost": allocated,
            },
            "workers": workers,
        }
        logical_trajectories.append({
            "trajectory_id": trajectory_id,
            "successor_call_ids": successor_ids,
            "successor_calculated_cost": successor_cost,
            "allocated_shared_source_cost": allocated,
            "logical_trajectory_cost": successor_cost + allocated,
        })

    total = sum(physical_costs.values())
    assert len(physical_ids) == 20
    assert sum(item["logical_trajectory_cost"] for item in logical_trajectories) == total
    return evidence.EvidencePacketInput(
        pilot_id=pilot_id,
        run_id=run_id,
        pilot_config={
            "pilot_id": pilot_id,
            "run_id": run_id,
            "contract_sha256": evidence.CONTRACT_SHA256,
            "g0_base_commit": g0_manifest.G0_BASE_COMMIT,
            "synthetic": True,
            "task": "A",
            "conditions": list(evidence.CONDITIONS),
            "architectures": list(evidence.ARCHITECTURES),
        },
        pilot_accounting={
            "accounting_valid": True,
            "physical_invocation_ids": physical_ids,
            "physical_calculated_costs": physical_costs,
            "total_calculated_pilot_cost": total,
            "logical_trajectories": logical_trajectories,
            "shared_allocations": shared_allocations,
        },
        pilot_scores={"pilot_id": pilot_id, "trajectory_scores": all_scores},
        pilot_result={
            "pilot_id": pilot_id,
            "execution_status": "COMPLETE",
            "integrity_valid": True,
            "contamination_detected": False,
            "accounting_valid": True,
            "task_score_valid": True,
            "warnings": [],
        },
        pricing_catalog={
            "synthetic": True,
            "entries": [
                {
                    "provider": "synthetic", "model": "synthetic-model-v1",
                    "category": category, "price_per_million": price,
                    "effective_date": "2026-07-15T00:00:00Z", "source": "synthetic-test-fixture",
                }
                for category, price in PRICES.items()
            ],
        },
        provider_selection_md="# Synthetic provider selection\n\nNo real provider or network call is used.\n",
        shared_sources=shared_sources,
        trajectories=trajectories,
    )


class TestCostCalculation(unittest.TestCase):
    def test_ceiling_division(self):
        self.assertEqual(accounting.ceiling_div(1, 1_000_000), 1)
        self.assertEqual(accounting.ceiling_div(0, 1_000_000), 0)
        self.assertEqual(accounting.ceiling_div(2_000_000, 1_000_000), 2)

    def test_ceiling_division_rejects_invalid(self):
        for numerator, denominator in ((-1, 1), (1, 0)):
            with self.subTest(numerator=numerator, denominator=denominator), self.assertRaises(ValueError):
                accounting.ceiling_div(numerator, denominator)

    def test_cached_included_and_excluded(self):
        included = accounting.calculate_call_cost(10, 4, 2, PRICES, True)
        excluded = accounting.calculate_call_cost(10, 4, 2, PRICES, False)
        self.assertEqual(included.uncached_input_tokens, 6)
        self.assertEqual(excluded.uncached_input_tokens, 10)
        self.assertTrue(included.call_accounting_valid)
        self.assertTrue(excluded.call_accounting_valid)

    def test_missing_negative_and_ambiguous_usage_fail_closed(self):
        cases = ((None, 0, 1), (1, None, 1), (1, 0, None), (-1, 0, 1), (1, 2, 1))
        for prompt, cached, completion in cases:
            with self.subTest(case=(prompt, cached, completion)):
                result = accounting.calculate_call_cost(prompt, cached, completion, PRICES, True)
                self.assertFalse(result.call_accounting_valid)
                self.assertIsNone(result.calculated_micro_usd_cost)

    def test_malformed_types_and_prices_fail_closed(self):
        cases = [
            (1.0, 0, 1, PRICES, True),
            (1, False, 1, PRICES, True),
            (1, 0, "1", PRICES, True),
            (1, 0, 1, {"uncached_input": 1}, True),
            (1, 0, 1, {**PRICES, "output": -1}, True),
            (1, 0, 1, PRICES, "yes"),
        ]
        for args in cases:
            with self.subTest(args=args):
                result = accounting.calculate_call_cost(*args)
                self.assertFalse(result.call_accounting_valid)
                self.assertIsNone(result.calculated_micro_usd_cost)


class TestAllocationAndAggregates(unittest.TestCase):
    def test_frozen_allocation_all_remainders(self):
        for cost, expected in (
            (6, {"stateless": 2, "summary": 2, "verified_state": 2}),
            (7, {"stateless": 3, "summary": 2, "verified_state": 2}),
            (8, {"stateless": 3, "summary": 3, "verified_state": 2}),
        ):
            with self.subTest(cost=cost):
                result = accounting.allocate_shared_cost("source", cost)
                self.assertTrue(result.allocation_valid)
                self.assertEqual(result.allocations, expected)

    def test_alternate_order_rejected(self):
        result = accounting.allocate_shared_cost("source", 7, tuple(reversed(accounting.ALLOCATION_ORDER)))
        self.assertFalse(result.allocation_valid)

    def test_physical_deduplicates_identical_and_rejects_conflicts(self):
        call = make_call("same")
        valid = accounting.compute_physical_accounting([call, call, make_call("other")])
        self.assertTrue(valid.deduplication_valid)
        self.assertEqual(len(valid.physical_invocation_ids), 2)
        conflicting = copy.deepcopy(call)
        object.__setattr__(conflicting, "completion_tokens", call.completion_tokens + 1)
        invalid = accounting.compute_physical_accounting([call, conflicting])
        self.assertFalse(invalid.deduplication_valid)

    def test_physical_rejects_bad_costs_and_tokens(self):
        base = make_call("bad")
        for field, value in (
            ("calculated_micro_usd_cost", True),
            ("calculated_micro_usd_cost", 1.0),
            ("prompt_tokens_total", -1),
            ("completion_tokens", "1"),
            ("provider_invocation_id", ""),
        ):
            malformed = copy.deepcopy(base)
            object.__setattr__(malformed, field, value)
            with self.subTest(field=field, value=value):
                self.assertFalse(accounting.compute_physical_accounting([malformed]).deduplication_valid)

    def test_logical_requires_three_distinct_successors(self):
        costs = {"b": 1, "c": 2, "d": 3}
        valid = accounting.compute_logical_trajectory_accounting("t", ["b", "c", "d"], costs, 2)
        self.assertTrue(valid.trajectory_accounting_valid)
        self.assertEqual(valid.logical_trajectory_cost, 8)
        for ids in (["b", "c"], ["b", "b", "d"], ["b", "c", "missing"]):
            with self.subTest(ids=ids):
                self.assertFalse(accounting.compute_logical_trajectory_accounting("t", list(ids), costs, 2).trajectory_accounting_valid)

    def test_aggregate_fails_closed(self):
        physical = accounting.compute_physical_accounting([make_call("b"), make_call("c"), make_call("d"), make_call("a")])
        logical = accounting.compute_logical_trajectory_accounting(
            "t", ["b", "c", "d"], physical.physical_calculated_costs, physical.physical_calculated_costs["a"]
        )
        allocation = accounting.allocate_shared_cost("source", physical.physical_calculated_costs["a"])
        valid = accounting.compute_accounting_invariants(physical, [logical], [allocation])
        self.assertTrue(valid.invariant_valid)
        broken = copy.deepcopy(logical)
        object.__setattr__(broken, "logical_trajectory_cost", logical.logical_trajectory_cost + 1)
        object.__setattr__(broken, "trajectory_accounting_valid", False)
        self.assertFalse(accounting.compute_accounting_invariants(physical, [broken], [allocation]).invariant_valid)


class EvidenceTestCase(unittest.TestCase):
    def build(self, packet: evidence.EvidencePacketInput | None = None):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        packet = packet or make_packet()
        result = evidence.build_pilot_evidence(Path(self.temp.name), packet)
        return packet, result, Path(self.temp.name) / packet.pilot_id

    @staticmethod
    def rewrite_json(path: Path, mutator) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        mutator(data)
        path.write_text(serialization.canonical_json(data) + "\n", encoding="utf-8")


class TestEvidenceBuild(EvidenceTestCase):
    def test_complete_packet_builds_and_reconstructs(self):
        packet, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        self.assertEqual(evidence.validate_pilot_evidence_from_disk(root), [])
        self.assertEqual(result.shared_source_count, 2)
        self.assertEqual(result.trajectory_count, 6)
        self.assertEqual(result.reference_count, 6)
        pilot_receipt = json.loads((root / "pilot_receipt.json").read_text())
        parsed = schemas.G1PilotEvidence.from_json(pilot_receipt)
        self.assertEqual(parsed.pilot_id, packet.pilot_id)
        self.assertEqual(len(parsed.provider_receipt_ids), 20)

    def test_every_required_artifact_is_semantic_and_nonempty(self):
        _, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        for path in root.rglob("*"):
            if path.is_file():
                self.assertGreater(path.stat().st_size, 0, str(path))
                if path.suffix == ".json":
                    parsed = json.loads(path.read_text(encoding="utf-8"))
                    self.assertIsInstance(parsed, dict, str(path))
                    self.assertTrue(parsed, str(path))

    def test_all_provider_scores_and_checkpoints_parse(self):
        _, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        for path in root.rglob("provider_call_receipt.json"):
            schemas.ProviderCallReceipt.from_json(json.loads(path.read_text()))
        self.assertEqual(len(list(root.rglob("provider_call_receipt.json"))), 20)
        for path in root.rglob("trajectory_score.json"):
            schemas.G1TrajectoryScore.from_json(json.loads(path.read_text()))
        self.assertEqual(len(list(root.rglob("trajectory_score.json"))), 6)
        self.assertEqual(len(list(root.rglob("checkpoint_receipt.json"))), 20)

    def test_trajectory_manifests_cover_actual_files(self):
        _, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        for trajectory_id in evidence.TRAJECTORY_ORDER:
            directory = root / "trajectories" / trajectory_id
            manifest = json.loads((directory / "trajectory_receipt.json").read_text())
            self.assertEqual(manifest["checksum"], hashing.compute_manifest_hash(manifest))
            self.assertEqual(manifest["file_sha256"], evidence._trajectory_file_hashes(directory))

    def test_pilot_config_covers_shared_and_pilot_artifacts(self):
        _, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        config = json.loads((root / "pilot_config.json").read_text())
        self.assertEqual(config["artifact_sha256"], evidence._pilot_artifact_hashes(root))

    def test_existing_destination_is_never_overwritten(self):
        packet = make_packet()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / packet.pilot_id
            target.mkdir()
            marker = target / "owner.txt"
            marker.write_text("preserve", encoding="utf-8")
            result = evidence.build_pilot_evidence(Path(tmp), packet)
            self.assertFalse(result.packet_valid)
            self.assertEqual(marker.read_text(), "preserve")

    def test_unsafe_pilot_ids_fail_before_write(self):
        for pilot_id in ("../escape", "/absolute", ".", "a/b", "a\\b"):
            packet = copy.deepcopy(make_packet())
            object.__setattr__(packet, "pilot_id", pilot_id)
            with tempfile.TemporaryDirectory() as tmp, self.subTest(pilot_id=pilot_id):
                result = evidence.build_pilot_evidence(Path(tmp), packet)
                self.assertFalse(result.packet_valid)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_symlink_run_root_rejected(self):
        packet = make_packet()
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(real, target_is_directory=True)
            result = evidence.build_pilot_evidence(link, packet)
            self.assertFalse(result.packet_valid)
            self.assertEqual(list(real.iterdir()), [])

    def test_active_network_denial(self):
        packet = make_packet()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
            result = evidence.build_pilot_evidence(Path(tmp), packet)
            self.assertTrue(result.packet_valid, result.errors)


class TestEvidenceDiskTampering(EvidenceTestCase):
    def test_missing_each_artifact_class_fails_without_exception(self):
        cases = [
            "pilot_accounting.json",
            "pilot_scores.json",
            "pilot_result.json",
            "pricing_catalog.json",
            "provider_selection.md",
            "pilot_receipt.json",
            "shared_sources/clean/provider_call_receipt.json",
            "shared_sources/drift/checkpoint_receipt.json",
            "shared_sources/clean/raw/prompt.txt",
            "trajectories/A-clean-stateless/config.json",
            "trajectories/A-clean-stateless/worker_A_shared_source_ref.json",
            "trajectories/A-clean-stateless/trajectory_score.json",
            "trajectories/A-clean-stateless/trajectory_result.json",
            "trajectories/A-clean-stateless/trajectory_receipt.json",
            "trajectories/A-drift-summary/worker_B/provider_call_receipt.json",
            "trajectories/A-drift-summary/worker_C/checkpoint_receipt.json",
            "trajectories/A-drift-summary/worker_D/raw/response.txt",
        ]
        for relative in cases:
            with self.subTest(relative=relative):
                _, result, root = self.build()
                self.assertTrue(result.packet_valid, result.errors)
                (root / relative).unlink()
                self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

    def test_raw_response_tamper_fails(self):
        _, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        path = root / "trajectories/A-clean-summary/worker_B/raw/response.txt"
        path.write_text("tampered\n", encoding="utf-8")
        self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

    def test_reference_invocation_and_allocation_tamper_fail(self):
        for key, value in (("provider_invocation_id", "wrong"), ("allocated_shared_source_cost", 999)):
            with self.subTest(key=key):
                _, result, root = self.build()
                self.assertTrue(result.packet_valid, result.errors)
                path = root / "trajectories/A-clean-stateless/worker_A_shared_source_ref.json"
                self.rewrite_json(path, lambda data: data.__setitem__(key, value))
                self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

    def test_checkpoint_path_and_hash_tamper_fail(self):
        for key, value in (("artifact_path", "raw/response.txt"), ("artifact_sha256", "0" * 64)):
            with self.subTest(key=key):
                _, result, root = self.build()
                self.assertTrue(result.packet_valid, result.errors)
                path = root / "trajectories/A-drift-verified_state/worker_D/checkpoint_receipt.json"
                def mutate(data):
                    data[key] = value
                    data["receipt_id"] = hashing.compute_receipt_hash(data)
                self.rewrite_json(path, mutate)
                self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

    def test_unknown_schema_fields_fail(self):
        _, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        path = root / "shared_sources/clean/provider_call_receipt.json"
        def mutate(data):
            data["unexpected"] = True
            data["receipt_id"] = hashing.compute_receipt_hash(data)
        self.rewrite_json(path, mutate)
        self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

    def test_noncanonical_and_duplicate_key_json_fail(self):
        _, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        path = root / "pilot_result.json"
        data = json.loads(path.read_text())
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

        _, result, root = self.build()
        path = root / "pilot_result.json"
        path.write_text('{"pilot_id":"x","pilot_id":"y"}\n', encoding="utf-8")
        self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

    def test_manifest_checksum_and_file_map_tamper_fail(self):
        for field in ("checksum", "file_sha256"):
            with self.subTest(field=field):
                _, result, root = self.build()
                self.assertTrue(result.packet_valid, result.errors)
                path = root / "trajectories/A-drift-stateless/trajectory_receipt.json"
                def mutate(data):
                    if field == "checksum":
                        data[field] = "0" * 64
                    else:
                        data[field].pop(next(iter(data[field])))
                        data["checksum"] = hashing.compute_manifest_hash(data)
                self.rewrite_json(path, mutate)
                self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

    def test_pilot_receipt_checksum_and_lineage_tamper_fail(self):
        for key in ("checksum", "provider_receipt_ids", "pilot_config_sha256"):
            with self.subTest(key=key):
                _, result, root = self.build()
                self.assertTrue(result.packet_valid, result.errors)
                path = root / "pilot_receipt.json"
                def mutate(data):
                    if key == "checksum":
                        data[key] = "0" * 64
                    elif key == "provider_receipt_ids":
                        data[key][0] = "0" * 64
                        data["checksum"] = hashing.compute_manifest_hash(data)
                    else:
                        data[key] = "0" * 64
                        data["checksum"] = hashing.compute_manifest_hash(data)
                self.rewrite_json(path, mutate)
                self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

    def test_accounting_and_score_semantic_tamper_fail(self):
        _, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        path = root / "pilot_accounting.json"
        self.rewrite_json(path, lambda data: data.__setitem__("total_calculated_pilot_cost", 0))
        self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

        _, result, root = self.build()
        path = root / "trajectories/A-clean-summary/trajectory_score.json"
        self.rewrite_json(path, lambda data: data.__setitem__("architecture", "stateless"))
        self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

    def test_unexpected_file_and_symlink_fail(self):
        _, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        (root / "unexpected.txt").write_text("x", encoding="utf-8")
        self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))

        _, result, root = self.build()
        raw = root / "shared_sources/clean/raw"
        prompt = raw / "prompt.txt"
        prompt.unlink()
        prompt.symlink_to(root / "provider_selection.md")
        self.assertTrue(evidence.validate_pilot_evidence_from_disk(root))


class TestInputValidation(EvidenceTestCase):
    def test_input_reference_invocation_mismatch_rejected_before_write(self):
        packet = copy.deepcopy(make_packet())
        packet.trajectories["A-clean-stateless"]["worker_A_shared_source_ref"]["provider_invocation_id"] = "wrong"
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(Path(tmp), packet)
            self.assertFalse(result.packet_valid)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_input_checkpoint_path_mismatch_rejected_before_write(self):
        packet = copy.deepcopy(make_packet())
        checkpoint = packet.trajectories["A-clean-stateless"]["workers"]["B"]["checkpoint_receipt"]
        checkpoint["artifact_path"] = "raw/response.txt"
        checkpoint["receipt_id"] = hashing.compute_receipt_hash(checkpoint)
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(Path(tmp), packet)
            self.assertFalse(result.packet_valid)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_input_schema_extra_field_rejected_before_write(self):
        packet = copy.deepcopy(make_packet())
        receipt = packet.shared_sources["clean"]["provider_receipt"]
        receipt["unexpected"] = True
        receipt["receipt_id"] = hashing.compute_receipt_hash(receipt)
        with tempfile.TemporaryDirectory() as tmp:
            result = evidence.build_pilot_evidence(Path(tmp), packet)
            self.assertFalse(result.packet_valid)
            self.assertEqual(list(Path(tmp).iterdir()), [])


# ── 4. Semantic reconstruction falsification tests ────────────────────


class TestSemanticReconstructionFalsification(EvidenceTestCase):
    """Prove that coordinated, rehashed tampering for each of the four
    G1-B defect classes fails disk reconstruction."""

    def test_falsify_cost_derivation(self):
        """Defect 1: recorded cost disagrees with token/pricing derivation."""
        packet, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        path = root / "trajectories/A-clean-stateless/worker_B/provider_call_receipt.json"
        self.rewrite_json(path, lambda data: data.__setitem__("calculated_micro_usd_cost", 999))
        disk_errors = evidence.validate_pilot_evidence_from_disk(root)
        self.assertTrue(
            any("cost derivation" in e for e in disk_errors),
            f"Expected cost-derivation error, got: {disk_errors}",
        )

    def test_falsify_allocation_rule(self):
        """Defect 2: shared allocation violates quotient/remainder rule."""
        packet, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        path = root / "pilot_accounting.json"
        def mutate(data):
            data["shared_allocations"][0]["allocations"]["verified_state"] += 1
            data["shared_allocations"][0]["sum_allocated"] += 1
        self.rewrite_json(path, mutate)
        disk_errors = evidence.validate_pilot_evidence_from_disk(root)
        self.assertTrue(
            any("allocation rule" in e for e in disk_errors),
            f"Expected allocation-rule error, got: {disk_errors}",
        )

    def test_falsify_trajectory_lineage(self):
        """Defect 3: successor_call_ids cross-wired between trajectories."""
        packet, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        path = root / "pilot_accounting.json"
        def mutate(data):
            # Swap the first successor ID of A-clean-stateless with A-drift-stateless
            trajs = data["logical_trajectories"]
            first_clean = trajs[0]
            first_drift = trajs[3]
            first_clean["successor_call_ids"][0] = first_drift["successor_call_ids"][0]
        self.rewrite_json(path, mutate)
        disk_errors = evidence.validate_pilot_evidence_from_disk(root)
        self.assertTrue(
            any("trajectory lineage" in e for e in disk_errors),
            f"Expected trajectory-lineage error, got: {disk_errors}",
        )

    def test_falsify_foreign_receipt_identity(self):
        """Defect 4: provider receipt belongs to a foreign pilot/run."""
        packet, result, root = self.build()
        self.assertTrue(result.packet_valid, result.errors)
        path = root / "shared_sources/clean/provider_call_receipt.json"
        def mutate(data):
            data["pilot_id"] = "foreign-pilot"
            data["receipt_id"] = hashing.compute_receipt_hash(data)
        self.rewrite_json(path, mutate)
        disk_errors = evidence.validate_pilot_evidence_from_disk(root)
        self.assertTrue(
            any("foreign receipt" in e for e in disk_errors),
            f"Expected foreign-receipt error, got: {disk_errors}",
        )


if __name__ == "__main__":
    unittest.main()
