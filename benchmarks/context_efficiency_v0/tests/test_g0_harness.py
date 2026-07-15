"""G0 harness acceptance tests — standard library only.

V6: Semantic checkpoint validation, allocation validation, trajectory accounting,
generic vs pilot accounting separation, hard network block.
"""
from __future__ import annotations
import dataclasses
import hashlib
import inspect
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path

from benchmarks.context_efficiency_v0 import adapters
from benchmarks.context_efficiency_v0.accounting import (
    pilot_accounting, verify_accounting, allocate_worker_a_cost,
    ARCHITECTURE_ORDER, compute_trajectory_cost, compute_session_cost,
)
from benchmarks.context_efficiency_v0.adapters import (
    ADAPTERS, StatelessAdapter, SummaryAdapter, VerifiedStateAdapter,
)
from benchmarks.context_efficiency_v0.baseline import (
    build_shared_worker_a_baseline, generate_all_baselines,
    clone_all_architectures, generate_worker_a_checkpoint,
    clone_checkpoint_to_architecture,
)
from benchmarks.context_efficiency_v0.checkpoint import (
    evaluate_checkpoint, evaluate_all_stages,
)
from benchmarks.context_efficiency_v0.fixtures import (
    all_fixtures, get_fixture,
)
from benchmarks.context_efficiency_v0.isolation import (
    create_isolated_workdir, verify_isolation, verify_no_leakage,
    reject_traversal, validate_trajectory_id,
)
from benchmarks.context_efficiency_v0.schemas import (
    CheckpointReceipt, EvidenceReference, SessionEvent,
    ToolCallReceipt, make_receipt_id, compute_sha256,
)
from benchmarks.context_efficiency_v0.scorer import (
    canonicalize_summary, score_observable, score_trajectory,
)


class HarnessTests(unittest.TestCase):
    """Core G0 harness tests."""

    def _transcript(self, root: str = "/tmp/run-one", timestamp: float = 123.5,
                    trajectory_id: str = "trajectory-1") -> list[SessionEvent]:
        rid = make_receipt_id("tool", trajectory_id, "A", 1, 0)
        cid = make_receipt_id("cp", trajectory_id, "A", 1, 0)
        tool_receipt = ToolCallReceipt(
            receipt_id=rid,
            tool="write_file",
            params={"path": f"{root}/stage_1/output.json", "content": '{"ok": true}'},
            status="ok",
            output="written",
            evidence_refs=(EvidenceReference(rid, "file_written", "stage_1/output.json"),),
            timestamp=timestamp,
        )
        checkpoint = CheckpointReceipt(
            receipt_id=cid,
            stage_number=1,
            worker_label="A",
            artifact_path=f"{root}/stage_1/output.json",
            artifact_sha256="a" * 64,
            test_results={"is_valid_json": True},
            timestamp=timestamp,
        )
        return [SessionEvent(
            type="checkpoint",
            session_id="session-1",
            trajectory_id=trajectory_id,
            architecture="summary",
            worker_label="A",
            stage=1,
            timestamp=timestamp,
            tool_calls=(tool_receipt,),
            checkpoint=checkpoint,
        )]

    # ── 1. Exactly three adapters ─────────────────────────────────────

    def test_exactly_three_pilot_adapters(self) -> None:
        adapter_classes = {
            name for name, value in vars(adapters).items()
            if inspect.isclass(value) and name.endswith("Adapter")
        }
        self.assertEqual(
            adapter_classes,
            {"StatelessAdapter", "SummaryAdapter", "VerifiedStateAdapter"},
        )
        self.assertEqual(set(ADAPTERS), {"stateless", "summary", "verified_state"})
        self.assertFalse(hasattr(adapters, "VerifiedStateProceduresAdapter"))

    def test_stateless_adapter_returns_empty_state(self) -> None:
        state = StatelessAdapter().transform(self._transcript())
        self.assertEqual(state.content, {})
        self.assertEqual(state.token_cost, 0)

    def test_summary_is_deterministic_and_checkpoint_derived(self) -> None:
        first = SummaryAdapter().transform(self._transcript(timestamp=1.0, trajectory_id="test_det"))
        second = SummaryAdapter().transform(self._transcript(timestamp=999.0, trajectory_id="test_det"))
        self.assertEqual(first.content["completed_stages"], second.content["completed_stages"])
        self.assertEqual(first.content["completed_stages"], [1])
        self.assertEqual(len(first.content["checkpoint_receipt_ids"]), 1)
        self.assertNotIn("analysis", first.content)

    def test_verified_state_claims_are_receipt_backed(self) -> None:
        content = VerifiedStateAdapter().transform(self._transcript()).content
        self.assertTrue(content["claims"])
        for claim in content["claims"]:
            self.assertTrue(claim["receipt_ids"])
        encoded = json.dumps(content, sort_keys=True)
        self.assertNotIn("summary", encoded)
        self.assertNotIn("analysis", encoded)

    def test_verified_state_omits_unsupported_events(self) -> None:
        rid = make_receipt_id("tool", "trajectory-1", "A", 1, 0)
        unsupported = dataclasses.replace(
            self._transcript()[0],
            tool_calls=(ToolCallReceipt(
                receipt_id=rid,
                tool="write_file",
                params={"path": "/tmp/unsupported", "content": "x"},
                status="ok",
                output="written",
            ),),
            checkpoint=None,
        )
        content = VerifiedStateAdapter().transform([unsupported]).content
        self.assertEqual(content["claims"], [])

    # ── 2. Fixtures ──────────────────────────────────────────────────

    def test_all_task_condition_fixtures_exist(self) -> None:
        keys = {(task, condition) for task, condition, _ in all_fixtures()}
        self.assertEqual(keys, {
            ("A", "clean"), ("A", "drift"),
            ("B", "clean"), ("B", "drift"),
            ("C", "clean"), ("C", "drift"),
        })

    # ── 3. Semantic checkpoint validation ──────────────────────────────

    def test_checkpoint_is_behavioral_and_sha_is_evidence(self) -> None:
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            artifact = fixture.stage_artifacts[1]
            data = json.loads(artifact.content)
            behaviorally_equivalent = json.dumps(data, separators=(",", ":"), sort_keys=True)
            self.assertNotEqual(
                artifact.sha256,
                hashlib.sha256(behaviorally_equivalent.encode()).hexdigest(),
            )
            fixture.write_artifact(1, behaviorally_equivalent, tmp)
            receipt = evaluate_checkpoint(1, tmp, fixture, worker_label="A",
                                          trajectory_id="test_behavioral")
            self.assertIsNotNone(receipt)
            self.assertTrue(all(receipt.test_results.values()))
            self.assertEqual(len(receipt.artifact_sha256), 64)

    def test_bad_artifacts_fail_behavioral_checkpoint(self) -> None:
        for task, condition, fixture in all_fixtures():
            with self.subTest(task=task, condition=condition):
                with tempfile.TemporaryDirectory() as tmp:
                    for stage in range(1, 5):
                        fixture.write_bad_artifact(stage, tmp)
                        self.assertIsNone(
                            evaluate_checkpoint(stage, tmp, fixture, trajectory_id="test_bad")
                        )

    def test_non_dict_list_member_rejected(self) -> None:
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_inputs(root)
            bad_content = json.dumps(["not_a_dict", "also_not_a_dict"])
            artifact_path = root / fixture.stage_artifacts[1].path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(bad_content)
            receipt = evaluate_checkpoint(1, root, fixture, trajectory_id="test_non_dict")
            self.assertIsNone(receipt)

    def test_expected_validation_checks_required(self) -> None:
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_inputs(root)
            wrong_structure = json.dumps({"not": "records"})
            artifact_path = root / fixture.stage_artifacts[1].path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(wrong_structure)
            receipt = evaluate_checkpoint(1, root, fixture, trajectory_id="test_checks")
            self.assertIsNone(receipt)

    def test_empty_checks_dict_rejected(self) -> None:
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_inputs(root)
            empty_checks = json.dumps({"validation": "PASS", "checks": {}})
            artifact_path = root / fixture.stage_artifacts[4].path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(empty_checks)
            receipt = evaluate_checkpoint(4, root, fixture, trajectory_id="test_empty_checks")
            self.assertIsNone(receipt)

    def test_acceptance_tests_match_rejects_0_of_5(self) -> None:
        """The 0/5 artifact (validation FAIL, acceptance_tests_passed=0, total_tests=5)
        must be rejected by acceptance_tests_match and cannot earn task correctness."""
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_inputs(root)
            # Write an artifact where acceptance_tests_passed=0, total_tests=5
            zero_of_five = json.dumps({
                "validation": "PASS",
                "checks": {
                    "structure_valid": True,
                    "all_stages_present": True,
                    "acceptance_tests_passed": 0,
                    "total_tests": 5,
                },
            })
            artifact_path = root / fixture.stage_artifacts[4].path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(zero_of_five)
            receipt = evaluate_checkpoint(4, root, fixture, trajectory_id="test_0_of_5")
            self.assertIsNone(receipt)

    def test_acceptance_tests_match_rejects_validation_fail(self) -> None:
        """validation != 'PASS' must be rejected by acceptance_tests_match directly."""
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_inputs(root)
            bad = json.dumps({
                "validation": "FAIL",
                "checks": {
                    "structure_valid": True,
                    "all_stages_present": True,
                    "acceptance_tests_passed": 5,
                    "total_tests": 5,
                },
            })
            artifact_path = root / fixture.stage_artifacts[4].path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(bad)
            receipt = evaluate_checkpoint(4, root, fixture, trajectory_id="test_val_fail")
            self.assertIsNone(receipt)

    def test_acceptance_tests_match_directly_rejects_validation_fail(self) -> None:
        """Direct behavioral test: acceptance_tests_match helper itself must reject validation='FAIL'
        without relying on validation_is_pass composition."""
        from benchmarks.context_efficiency_v0.checkpoint import _run_test
        data = {
            "validation": "FAIL",
            "checks": {
                "structure_valid": True,
                "all_stages_present": True,
                "acceptance_tests_passed": 5,
                "total_tests": 5,
            },
        }
        test = {"name": "acceptance_tests_match", "params": {}}
        self.assertFalse(_run_test(test, data))

    def test_acceptance_tests_match_rejects_structure_valid_false(self) -> None:
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_inputs(root)
            bad = json.dumps({
                "validation": "PASS",
                "checks": {
                    "structure_valid": False,
                    "all_stages_present": True,
                    "acceptance_tests_passed": 5,
                    "total_tests": 5,
                },
            })
            artifact_path = root / fixture.stage_artifacts[4].path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(bad)
            receipt = evaluate_checkpoint(4, root, fixture, trajectory_id="test_sv_false")
            self.assertIsNone(receipt)

    def test_acceptance_tests_match_rejects_all_stages_present_false(self) -> None:
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_inputs(root)
            bad = json.dumps({
                "validation": "PASS",
                "checks": {
                    "structure_valid": True,
                    "all_stages_present": False,
                    "acceptance_tests_passed": 5,
                    "total_tests": 5,
                },
            })
            artifact_path = root / fixture.stage_artifacts[4].path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(bad)
            receipt = evaluate_checkpoint(4, root, fixture, trajectory_id="test_asp_false")
            self.assertIsNone(receipt)

    def test_acceptance_tests_match_rejects_boolean_integers(self) -> None:
        """Boolean values for acceptance_tests_passed/total_tests must be rejected."""
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_inputs(root)
            bad = json.dumps({
                "validation": "PASS",
                "checks": {
                    "structure_valid": True,
                    "all_stages_present": True,
                    "acceptance_tests_passed": True,
                    "total_tests": 5,
                },
            })
            artifact_path = root / fixture.stage_artifacts[4].path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(bad)
            receipt = evaluate_checkpoint(4, root, fixture, trajectory_id="test_bool_int")
            self.assertIsNone(receipt)

    def test_acceptance_tests_match_rejects_zero_total_tests(self) -> None:
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write_inputs(root)
            bad = json.dumps({
                "validation": "PASS",
                "checks": {
                    "structure_valid": True,
                    "all_stages_present": True,
                    "acceptance_tests_passed": 0,
                    "total_tests": 0,
                },
            })
            artifact_path = root / fixture.stage_artifacts[4].path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(bad)
            receipt = evaluate_checkpoint(4, root, fixture, trajectory_id="test_zero_tt")
            self.assertIsNone(receipt)

    def test_0_of_5_artifact_cannot_earn_task_correctness(self) -> None:
        """A trajectory where stage 4 fails acceptance_tests_match must get 0 task correctness."""
        fixture = get_fixture("A", "clean")
        sessions = {}
        for i, worker in enumerate(["A", "B", "C", "D"]):
            stage = i + 1
            rid = make_receipt_id("tool", "test_0of5", worker, stage, 0)
            cid = make_receipt_id("cp", "test_0of5", worker, stage, 0)
            tc = ToolCallReceipt(
                receipt_id=rid, tool="write_file",
                params={"path": f"stage_{stage}/output.json", "content": "data"},
                status="ok", output="ok",
            )
            # Stage 4 gets a failing artifact
            if stage == 4:
                cp = None
            else:
                cp = CheckpointReceipt(
                    receipt_id=cid, stage_number=stage, worker_label=worker,
                    artifact_path=f"/tmp/stage_{stage}/output.json",
                    artifact_sha256=compute_sha256("data"),
                    test_results={f"test_{stage}_1": True},
                    timestamp=float(stage),
                )
            sessions[worker] = [
                SessionEvent(
                    type="session_end", session_id=f"s_{worker}", trajectory_id="test_0of5",
                    architecture="stateless", worker_label=worker, stage=stage,
                    timestamp=float(stage), tool_calls=(tc,), checkpoint=cp,
                ),
            ]
        score = score_trajectory("test_0of5", "stateless", "A", "clean", sessions, fixture)
        self.assertEqual(score.task_correctness, 0.0)

    # ── 4. Canonical determinism ─────────────────────────────────────

    def test_canonical_comparison_normalizes_output_dir(self) -> None:
        left = {"output_dir": "/tmp/run_a", "gates_passed": 14, "g0_dry_run": "PASS"}
        right = {"output_dir": "/tmp/run_b", "gates_passed": 14, "g0_dry_run": "PASS"}
        self.assertEqual(canonicalize_summary(left), canonicalize_summary(right))

    def test_canonical_preserves_booleans_and_scores(self) -> None:
        left = {"g0_dry_run": "PASS", "gates_passed": 14}
        right = {"g0_dry_run": "FAIL", "gates_passed": 14}
        self.assertNotEqual(canonicalize_summary(left), canonicalize_summary(right))
        left2 = {"g0_dry_run": "PASS", "gates_passed": 14}
        right2 = {"g0_dry_run": "PASS", "gates_passed": 10}
        self.assertNotEqual(canonicalize_summary(left2), canonicalize_summary(right2))

    def test_canonical_preserves_hashes(self) -> None:
        h = "75053261128d33ebde982ecdfd41b127652c824c3336e58111e001a6cac481ec"
        d = {"hash": h}
        self.assertEqual(canonicalize_summary(d), {"hash": h})

    def test_canonical_preserves_numeric_timestamps(self) -> None:
        d = {"timestamp": 1234567890.5, "value": 42}
        self.assertEqual(canonicalize_summary(d), {"timestamp": 1234567890.5, "value": 42})

    # ── 5. Scoring ───────────────────────────────────────────────────

    def test_scorer_uses_observable_receipts_only(self) -> None:
        score = score_observable(self._transcript())
        self.assertEqual(score["checkpoint_count"], 1)
        self.assertEqual(score["behavioral_checks_passed"], 1)
        self.assertNotIn("reasoning", score)
        self.assertNotIn("chain_of_thought", score)

    def test_successful_trajectory_gets_invariant_credit(self) -> None:
        fixture = get_fixture("A", "clean")
        sessions = {}
        for i, worker in enumerate(["A", "B", "C", "D"]):
            stage = i + 1
            rid = make_receipt_id("tool", "test_success", worker, stage, 0)
            cid = make_receipt_id("cp", "test_success", worker, stage, 0)
            tc = ToolCallReceipt(
                receipt_id=rid, tool="write_file",
                params={"path": f"stage_{stage}/output.json", "content": "data"},
                status="ok", output="ok",
                evidence_refs=(EvidenceReference(rid, "file_written", f"stage_{stage}/output.json"),),
            )
            cp = CheckpointReceipt(
                receipt_id=cid, stage_number=stage, worker_label=worker,
                artifact_path=f"/tmp/stage_{stage}/output.json",
                artifact_sha256=compute_sha256("data"),
                test_results={f"test_{stage}_1": True},
                timestamp=float(stage),
            )
            sessions[worker] = [
                SessionEvent(
                    type="session_end", session_id=f"s_{worker}", trajectory_id="test_success",
                    architecture="verified_state", worker_label=worker, stage=stage,
                    timestamp=float(stage), tool_calls=(tc,), checkpoint=cp,
                ),
            ]
        score = score_trajectory("test_success", "verified_state", "A", "clean", sessions, fixture)
        self.assertGreater(score.stage_invariants_preserved, 0)
        self.assertGreater(score.task_correctness, 0)

    def test_inactive_trajectory_scores_zero(self) -> None:
        fixture = get_fixture("A", "clean")
        sessions = {}
        for worker in ["A", "B", "C", "D"]:
            sessions[worker] = [
                SessionEvent(
                    type="session_end", session_id=f"s_{worker}", trajectory_id="test_inactive",
                    architecture="stateless", worker_label=worker, stage=1,
                    timestamp=1.0,
                ),
            ]
        score = score_trajectory("test_inactive", "stateless", "A", "clean", sessions, fixture)
        self.assertEqual(score.task_correctness, 0.0)
        self.assertEqual(score.normalized_total, 0.0)
        for cs in score.continuation_correctness:
            self.assertEqual(cs, 0.0)

    def test_stale_drift_actions_increase_stale_state_errors(self) -> None:
        fixture = get_fixture("A", "drift")
        clean_fixture = get_fixture("A", "clean")
        sessions = {}
        for i, worker in enumerate(["A", "B"]):
            stage = i + 1
            rid = make_receipt_id("tool", "test_stale", worker, stage, 0)
            cid = make_receipt_id("cp", "test_stale", worker, stage, 0)
            output = clean_fixture.input_files["app.log"]
            tc = ToolCallReceipt(
                receipt_id=rid, tool="read_file",
                params={"path": "app.log"},
                status="ok",
                output=output,
            )
            cp = CheckpointReceipt(
                receipt_id=cid, stage_number=stage, worker_label=worker,
                artifact_path=f"/tmp/stage_{stage}/output.json",
                artifact_sha256=compute_sha256("data"),
                test_results={f"test_{stage}_1": True},
                timestamp=float(stage),
            )
            sessions[worker] = [
                SessionEvent(
                    type="session_end", session_id=f"s_{worker}", trajectory_id="test_stale",
                    architecture="verified_state", worker_label=worker, stage=stage,
                    timestamp=float(stage), tool_calls=(tc,), checkpoint=cp,
                ),
            ]
        score = score_trajectory("test_stale", "verified_state", "A", "drift", sessions, fixture)
        self.assertGreater(score.stale_state_errors, 0)

    def test_clean_condition_has_zero_stale_errors(self) -> None:
        fixture = get_fixture("A", "clean")
        sessions = {}
        for i, worker in enumerate(["A", "B"]):
            stage = i + 1
            rid = make_receipt_id("tool", "test_clean_stale", worker, stage, 0)
            cid = make_receipt_id("cp", "test_clean_stale", worker, stage, 0)
            tc = ToolCallReceipt(
                receipt_id=rid, tool="read_file",
                params={"path": "app.log"},
                status="ok",
                output="2026-01-15 08:30:00 INFO  User login successful user_id=42\n",
            )
            cp = CheckpointReceipt(
                receipt_id=cid, stage_number=stage, worker_label=worker,
                artifact_path=f"/tmp/stage_{stage}/output.json",
                artifact_sha256=compute_sha256("data"),
                test_results={f"test_{stage}_1": True},
                timestamp=float(stage),
            )
            sessions[worker] = [
                SessionEvent(
                    type="session_end", session_id=f"s_{worker}", trajectory_id="test_clean_stale",
                    architecture="verified_state", worker_label=worker, stage=stage,
                    timestamp=float(stage), tool_calls=(tc,), checkpoint=cp,
                ),
            ]
        score = score_trajectory("test_clean_stale", "verified_state", "A", "clean", sessions, fixture)
        self.assertEqual(score.stale_state_errors, 0)

    # ── 6. Isolation ─────────────────────────────────────────────────

    def test_isolation_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "artifact.txt").write_text("ok", encoding="utf-8")
            self.assertTrue(verify_isolation(root))
            self.assertTrue(reject_traversal("stage/output.json", root))
            self.assertFalse(reject_traversal("../escape", root))
            self.assertFalse(reject_traversal("../../etc/passwd", root))

    def test_sibling_prefix_escape_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "foo"
            root.mkdir()
            sibling = Path(tmp) / "foobar"
            sibling.mkdir()
            self.assertFalse(reject_traversal("../../etc", root))
            self.assertTrue(reject_traversal("valid/file.txt", root))

    def test_absolute_path_escape_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(reject_traversal("/etc/passwd", root))

    def test_symlink_escape_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("secret")
            workdir = root / "workdir"
            workdir.mkdir()
            link = workdir / "escape"
            os.symlink("../outside/secret.txt", str(link))
            self.assertFalse(verify_isolation(workdir))

    def test_malicious_fixture_path_rejected(self) -> None:
        fixture = get_fixture("A", "clean")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside_path = root / ".." / "etc" / "passwd"
            outside_path.parent.mkdir(parents=True, exist_ok=True)
            outside_path.write_text("root:x:0:0:root:/root:/bin/bash")
            receipt = evaluate_checkpoint(1, root, fixture, trajectory_id="test_malicious")
            self.assertIsNone(receipt)

    def test_trajectory_id_validation(self) -> None:
        with self.assertRaises(ValueError):
            validate_trajectory_id("../escape")
        with self.assertRaises(ValueError):
            validate_trajectory_id("with spaces")
        with self.assertRaises(ValueError):
            validate_trajectory_id("")
        with self.assertRaises(ValueError):
            validate_trajectory_id("a/b")
        self.assertEqual(validate_trajectory_id("A-clean-stateless-r0"), "A-clean-stateless-r0")
        self.assertEqual(validate_trajectory_id("test_123"), "test_123")

    def test_checkpoint_only_no_tool_trajectory_scores_zero_task(self) -> None:
        fixture = get_fixture("A", "clean")
        sessions = {}
        for i, worker in enumerate(["A", "B", "C", "D"]):
            stage = i + 1
            rid = make_receipt_id("tool", "test_cp_only", worker, stage, 0)
            cid = make_receipt_id("cp", "test_cp_only", worker, stage, 0)
            tc = ToolCallReceipt(
                receipt_id=rid, tool="write_file",
                params={"path": f"stage_{stage}/output.json", "content": "data"},
                status="ok", output="ok",
            )
            cp = CheckpointReceipt(
                receipt_id=cid, stage_number=stage, worker_label=worker,
                artifact_path=f"/tmp/stage_{stage}/output.json",
                artifact_sha256=compute_sha256("data"),
                test_results={f"test_{stage}_1": True},
                timestamp=float(stage),
            )
            sessions[worker] = [
                SessionEvent(
                    type="session_end", session_id=f"s_{worker}", trajectory_id="test_cp_only",
                    architecture="stateless", worker_label=worker, stage=stage,
                    timestamp=float(stage), tool_calls=(tc,), checkpoint=cp,
                ),
            ]
        sessions["D"] = [
            SessionEvent(
                type="session_end", session_id="s_D", trajectory_id="test_cp_only",
                architecture="stateless", worker_label="D", stage=4,
                timestamp=4.0, checkpoint=sessions["D"][0].checkpoint,
            ),
        ]
        score = score_trajectory("test_cp_only", "stateless", "A", "clean", sessions, fixture)
        self.assertEqual(score.task_correctness, 0.0)

    def test_fixture_write_traversal_rejected(self) -> None:
        fixture = get_fixture("A", "clean")
        malicious_artifact = dataclasses.replace(
            fixture.stage_artifacts[1],
            path="../outside.txt",
        )
        malicious_fixture = dataclasses.replace(
            fixture,
            stage_artifacts={1: malicious_artifact},
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                malicious_fixture.write_artifact(1, "content", root)
            self.assertFalse((root.parent / "outside.txt").exists())

    # ── 7. Shared Worker-A baseline ──────────────────────────────────

    def test_shared_worker_a_accounting(self) -> None:
        accounting = pilot_accounting()
        self.assertEqual(accounting, {
            "worker_a_source_sessions": 2,
            "successor_sessions": 18,
            "unique_model_session_equivalents": 20,
            "logical_session_records": 24,
            "formula": "24 logical = 20 unique (2 Worker-A + 18 successors)",
        })
        with tempfile.TemporaryDirectory() as tmp:
            baseline = build_shared_worker_a_baseline(tmp)
            self.assertEqual(len(baseline.sources), 2)
            self.assertEqual(len(baseline.logical_records), 24)
            self.assertEqual(len({r.model_session_id for r in baseline.logical_records}), 20)

    def test_clone_directories_are_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            baselines = generate_all_baselines(base, tasks=["A"], conditions=["clean"], replicates=[0, 1])
            architectures = ["stateless", "summary", "verified_state"]
            clones = clone_all_architectures(baselines, architectures, base / "clones")
            clone_paths = list(clones.values())
            self.assertEqual(len(clone_paths), len(set(str(p) for p in clone_paths)))
            for path in clone_paths:
                self.assertTrue(path.exists(), f"Clone path does not exist: {path}")

    def test_clone_content_matches_source_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            baselines = generate_all_baselines(base, tasks=["A"], conditions=["clean"], replicates=[0])
            architectures = ["stateless", "summary", "verified_state"]
            clones = clone_all_architectures(baselines, architectures, base / "clones")
            for (t, c, arch, r), clone_dir in clones.items():
                receipt, _ = baselines[(t, c, r)]
                artifact_files = list(clone_dir.rglob("*"))
                self.assertTrue(len(artifact_files) > 0, f"No artifacts in clone {clone_dir}")
                for af in artifact_files:
                    if af.is_file():
                        content = af.read_text()
                        sha = compute_sha256(content)
                        self.assertEqual(sha, receipt.artifact_sha256,
                                         f"Clone SHA mismatch for {af}")

    def test_clone_verifies_source_containment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside_file = base / "outside.txt"
            outside_file.write_text("outside content")
            bad_receipt = CheckpointReceipt(
                receipt_id="bad", stage_number=1, worker_label="A",
                artifact_path=str(outside_file),
                artifact_sha256=compute_sha256("outside content"),
                test_results={"test": True},
            )
            source_dir = base / "source"
            source_dir.mkdir()
            with self.assertRaises(RuntimeError):
                clone_checkpoint_to_architecture(
                    bad_receipt, source_dir, "stateless", base / "clones",
                    task="A", condition="clean", replicate=0,
                )

    def test_clone_verifies_source_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source_dir = base / "source"
            source_dir.mkdir()
            artifact = source_dir / "stage_1" / "output.json"
            artifact.parent.mkdir()
            artifact.write_text("content")
            wrong_sha_receipt = CheckpointReceipt(
                receipt_id="bad_sha", stage_number=1, worker_label="A",
                artifact_path=str(artifact),
                artifact_sha256="a" * 64,
                test_results={"test": True},
            )
            with self.assertRaises(RuntimeError):
                clone_checkpoint_to_architecture(
                    wrong_sha_receipt, source_dir, "stateless", base / "clones",
                    task="A", condition="clean", replicate=0,
                )

    # ── 8. Receipt identity ───────────────────────────────────────────

    def test_receipt_ids_are_unique(self) -> None:
        ids = set()
        for i in range(100):
            for worker in ["A", "B", "C", "D"]:
                for stage in range(1, 5):
                    rid = make_receipt_id("cp", f"test_traj_{i}", worker, stage, 0)
                    self.assertNotIn(rid, ids, f"Duplicate receipt ID: {rid}")
                    ids.add(rid)
        self.assertEqual(len(ids), 100 * 4 * 4)

    def test_receipt_ids_embed_trajectory_identity(self) -> None:
        rid1 = make_receipt_id("cp", "traj_A", "A", 1, 0)
        rid2 = make_receipt_id("cp", "traj_B", "A", 1, 0)
        self.assertNotEqual(rid1, rid2)

    # ── 9. Worker-A allocation ───────────────────────────────────────

    def test_allocate_worker_a_cost_deterministic(self) -> None:
        archs = ["stateless", "summary", "verified_state"]
        r1 = allocate_worker_a_cost(57, archs)
        r2 = allocate_worker_a_cost(57, archs)
        self.assertEqual(r1, r2)

    def test_allocate_worker_a_cost_no_negative(self) -> None:
        archs = ["stateless", "summary", "verified_state"]
        for cost in range(0, 1001):
            alloc = allocate_worker_a_cost(cost, archs)
            for v in alloc.values():
                self.assertGreaterEqual(v, 0, f"Negative allocation at cost {cost}")

    def test_allocate_worker_a_cost_sum_exact(self) -> None:
        archs = ["stateless", "summary", "verified_state"]
        for cost in range(0, 1001):
            alloc = allocate_worker_a_cost(cost, archs)
            self.assertEqual(sum(alloc.values()), cost,
                             f"Sum mismatch at cost {cost}: {alloc}")

    def test_allocate_57_across_6(self) -> None:
        archs = ARCHITECTURE_ORDER[:6]
        alloc = allocate_worker_a_cost(57, archs)
        values = [alloc[a] for a in archs]
        self.assertEqual(values, [10, 10, 10, 9, 9, 9])
        self.assertEqual(sum(values), 57)

    def test_allocate_5_across_6(self) -> None:
        archs = ARCHITECTURE_ORDER[:6]
        alloc = allocate_worker_a_cost(5, archs)
        values = [alloc[a] for a in archs]
        self.assertEqual(values, [1, 1, 1, 1, 1, 0])
        self.assertEqual(sum(values), 5)

    def test_allocate_60_across_6(self) -> None:
        archs = ARCHITECTURE_ORDER[:6]
        alloc = allocate_worker_a_cost(60, archs)
        values = [alloc[a] for a in archs]
        self.assertEqual(values, [10, 10, 10, 10, 10, 10])
        self.assertEqual(sum(values), 60)

    def test_allocate_architecture_order_frozen(self) -> None:
        self.assertEqual(ARCHITECTURE_ORDER, [
            "stateless",
            "summary",
            "verified_state",
            "raw_memory",
            "retrieval",
            "verified_state_procedures",
        ])

    def test_allocate_rejects_negative_cost(self) -> None:
        with self.assertRaises(ValueError):
            allocate_worker_a_cost(-1, ["stateless"])

    def test_allocate_rejects_non_integer_cost(self) -> None:
        with self.assertRaises(ValueError):
            allocate_worker_a_cost(3.5, ["stateless"])

    def test_allocate_rejects_empty_architectures(self) -> None:
        with self.assertRaises(ValueError):
            allocate_worker_a_cost(10, [])

    def test_allocate_rejects_duplicate_architectures(self) -> None:
        with self.assertRaises(ValueError):
            allocate_worker_a_cost(10, ["stateless", "stateless"])

    def test_allocate_rejects_unknown_architecture(self) -> None:
        with self.assertRaises(ValueError):
            allocate_worker_a_cost(10, ["unknown_arch"])

    def test_trajectory_cost_excludes_full_worker_a(self) -> None:
        rid = "rct_a1"
        tc = ToolCallReceipt(receipt_id=rid, tool="write_file",
                             params={"path": "/tmp/test.txt", "content": "x"},
                             status="ok", output="ok")
        cp = CheckpointReceipt(receipt_id="cp_a1", stage_number=1, worker_label="A",
                               artifact_path="/tmp/test.txt", artifact_sha256="a"*64,
                               test_results={"t1": True})
        worker_a_events = [SessionEvent(type="session_end", session_id="sA", trajectory_id="t",
                                         architecture="stateless", worker_label="A", stage=1,
                                         timestamp=1.0, tool_calls=(tc,), checkpoint=cp)]
        worker_b_events = [SessionEvent(type="session_end", session_id="sB", trajectory_id="t",
                                         architecture="stateless", worker_label="B", stage=2,
                                         timestamp=2.0, tool_calls=(tc,))]
        sessions = {"A": worker_a_events, "B": worker_b_events}
        all_archs = ["stateless", "summary", "verified_state"]
        cost = compute_trajectory_cost("test_traj", sessions, "stateless",
                                       all_architectures=all_archs)
        self.assertNotEqual(cost.total_tokens(), cost.sessions["A"].total())

    def test_trajectory_cost_rejects_absent_architecture(self) -> None:
        rid = "rct_a1"
        tc = ToolCallReceipt(receipt_id=rid, tool="write_file",
                             params={"path": "/tmp/test.txt", "content": "x"},
                             status="ok", output="ok")
        cp = CheckpointReceipt(receipt_id="cp_a1", stage_number=1, worker_label="A",
                               artifact_path="/tmp/test.txt", artifact_sha256="a"*64,
                               test_results={"t1": True})
        events = [SessionEvent(type="session_end", session_id="sA", trajectory_id="t",
                                architecture="stateless", worker_label="A", stage=1,
                                timestamp=1.0, tool_calls=(tc,), checkpoint=cp)]
        sessions = {"A": events}
        with self.assertRaises(ValueError):
            compute_trajectory_cost("test_traj", sessions, "unknown_arch",
                                    all_architectures=["stateless", "summary"])

    def test_unit_separation(self) -> None:
        """Worker-A source token cost must equal neutral SessionCost.total().
        Storage bytes must be tracked separately and never added to token totals."""
        rid = "rct_a1"
        tc = ToolCallReceipt(receipt_id=rid, tool="write_file",
                             params={"path": "/tmp/test.txt", "content": "x" * 100},
                             status="ok", output="ok" * 50)
        cp = CheckpointReceipt(receipt_id="cp_a1", stage_number=1, worker_label="A",
                               artifact_path="/tmp/test.txt", artifact_sha256="a"*64,
                               test_results={"t1": True})
        events = [SessionEvent(type="session_end", session_id="sA", trajectory_id="t",
                                architecture="stateless", worker_label="A", stage=1,
                                timestamp=1.0, tool_calls=(tc,), checkpoint=cp)]

        from benchmarks.context_efficiency_v0.accounting import compute_worker_a_source_cost, WorkerASourceCost
        source = compute_worker_a_source_cost(events)
        neutral_cost = compute_session_cost(events, "stateless", is_worker_a=True)

        # Source token total must equal neutral SessionCost.total()
        self.assertEqual(source.token_total, neutral_cost.total(),
                         f"Source tokens {source.token_total} != neutral cost {neutral_cost.total()}")

        # Storage bytes must be tracked separately
        self.assertEqual(source.storage_bytes, neutral_cost.storage_bytes,
                         f"Source storage {source.storage_bytes} != neutral storage {neutral_cost.storage_bytes}")

        # Storage bytes must NEVER be added to token totals
        self.assertNotEqual(neutral_cost.total(), neutral_cost.storage_bytes,
                            "storage_bytes must not equal total_tokens")
        self.assertGreater(neutral_cost.storage_bytes, 0,
                           "Checkpoint should produce storage bytes")

    def test_integrated_six_arm_shared_worker_a_cost(self) -> None:
        """Use one nonzero Worker-A transcript containing a checkpoint.
        All six arms reference the same neutral source token cost.
        Source token cost equals SessionCost.total().
        Storage bytes remain separate.
        Six token allocations sum exactly to one source token cost.
        raw_memory overhead affects successors only.
        No caller can inject or omit allocation values."""
        rid = "rct_a1"
        tc = ToolCallReceipt(receipt_id=rid, tool="write_file",
                             params={"path": "/tmp/test.txt", "content": "x" * 100},
                             status="ok", output="ok" * 50)
        cp = CheckpointReceipt(receipt_id="cp_a1", stage_number=1, worker_label="A",
                               artifact_path="/tmp/test.txt", artifact_sha256="a"*64,
                               test_results={"t1": True})
        worker_a_events = [SessionEvent(type="session_end", session_id="sA", trajectory_id="t",
                                         architecture="stateless", worker_label="A", stage=1,
                                         timestamp=1.0, tool_calls=(tc,), checkpoint=cp)]
        worker_b_events = [SessionEvent(type="session_end", session_id="sB", trajectory_id="t",
                                         architecture="stateless", worker_label="B", stage=2,
                                         timestamp=2.0, tool_calls=(tc,))]
        sessions = {"A": worker_a_events, "B": worker_b_events}
        all_archs = ARCHITECTURE_ORDER[:6]

        from benchmarks.context_efficiency_v0.accounting import compute_worker_a_source_cost, WorkerASourceCost
        source = compute_worker_a_source_cost(worker_a_events)

        # Compute all six trajectory costs
        costs = {}
        for arch in all_archs:
            cost = compute_trajectory_cost(
                "test_integrated", sessions, arch,
                all_architectures=all_archs,
            )
            costs[arch] = cost

        # Every arm references the same Worker-A source token cost
        source_tokens = {a: c.worker_a_source_tokens for a, c in costs.items()}
        self.assertEqual(len(set(source_tokens.values())), 1,
                         f"Worker-A source tokens differ by architecture: {source_tokens}")

        # Source token cost equals SessionCost.total()
        neutral_cost = compute_session_cost(worker_a_events, "stateless", is_worker_a=True)
        for arch, cost in costs.items():
            self.assertEqual(cost.worker_a_source_tokens, neutral_cost.total(),
                             f"{arch} source tokens != neutral cost")

        # Storage bytes remain separate
        for arch, cost in costs.items():
            self.assertEqual(cost.worker_a_storage_bytes, source.storage_bytes,
                             f"{arch} storage bytes mismatch")
            # Storage bytes must not be in total_tokens
            self.assertNotEqual(cost.total_tokens(), cost.worker_a_storage_bytes)

        # Allocations sum exactly to source token cost
        alloc_sum = sum(c.shared_baseline_allocation for c in costs.values())
        self.assertEqual(alloc_sum, source.token_total,
                         f"Allocations sum {alloc_sum} != source tokens {source.token_total}")

        # raw_memory delivery overhead applies only to successor delivery
        for arch in all_archs:
            succ_cost = costs[arch].sessions["B"].total()
            if arch == "raw_memory":
                self.assertGreater(succ_cost, costs["stateless"].sessions["B"].total(),
                                   f"raw_memory successor should have higher cost than stateless")
            else:
                self.assertEqual(succ_cost, costs["stateless"].sessions["B"].total(),
                                 f"{arch} successor cost differs from stateless")

        # No caller can inject or omit allocation values (precomputed params removed)
        self.assertFalse(hasattr(compute_trajectory_cost, "precomputed_worker_a_cost"),
                         "precomputed parameters should not exist")

        # Test with multiple costs
        for test_cost in [0, 1, 5, 57, 60, 100, 1000]:
            alloc = allocate_worker_a_cost(test_cost, all_archs)
            self.assertEqual(sum(alloc.values()), test_cost,
                             f"Six-arm sum mismatch at cost {test_cost}")

    # ── 10. Generic vs pilot accounting ──────────────────────────────

    def test_accounting_empty_inputs(self) -> None:
        result = verify_accounting(0, 0, [])
        self.assertIn("error", result)

    def test_accounting_1x1(self) -> None:
        """1 baseline, 1 architecture, 1 clone."""
        result = verify_accounting(1, 1, ["stateless"])
        self.assertEqual(result["full_study"]["trajectories"], 1)

    def test_accounting_reduced_inputs(self) -> None:
        result = verify_accounting(1, 1, ["stateless"])
        self.assertEqual(result["full_study"]["trajectories"], 1)

    def test_accounting_full_study(self) -> None:
        result = verify_accounting(18, 108, ["stateless", "summary", "verified_state",
                                            "raw_memory", "retrieval", "verified_state_procedures"])
        self.assertEqual(result["full_study"]["unique_worker_a"], 18)
        self.assertEqual(result["full_study"]["clones"], 108)
        self.assertEqual(result["full_study"]["trajectories"], 108)
        self.assertEqual(result["full_study"]["sessions"], 432)
        self.assertEqual(result["full_study"]["unique_model_session_equivalents"], 342)

    def test_accounting_clone_mismatch_detected(self) -> None:
        result = verify_accounting(6, 30, ["stateless", "summary", "verified_state"])
        self.assertIn("error", result)

    def test_accounting_rejects_duplicate_architectures(self) -> None:
        result = verify_accounting(6, 12, ["stateless", "stateless"])
        self.assertIn("error", result)

    def test_accounting_rejects_unknown_architecture(self) -> None:
        result = verify_accounting(6, 6, ["stateless", "unknown_arch"])
        self.assertIn("error", result)

    def test_accounting_no_pilot_block(self) -> None:
        """verify_accounting must contain no pilot block."""
        result = verify_accounting(6, 6, ["stateless"])
        self.assertIn("full_study", result)
        self.assertNotIn("pilot", result)

    def test_pilot_accounting_separate(self) -> None:
        """pilot_accounting() is the only fixed 24/20 function."""
        p = pilot_accounting()
        self.assertEqual(p["logical_session_records"], 24)
        self.assertEqual(p["unique_model_session_equivalents"], 20)

    # ── 11. No network proof ─────────────────────────────────────────

    def test_dry_run_no_network(self) -> None:
        suspicious = ["requests", "urllib3", "httpx", "openai", "anthropic", "ollama"]
        for mod_name in suspicious:
            self.assertNotIn(mod_name, sys.modules,
                             f"{mod_name} should not be imported")

    def test_no_network_socket_blocked(self) -> None:
        """Hard-block runtime test using side_effect exceptions on socket.create_connection
        and socket.socket.connect. Run the complete dry-run and assert zero calls."""
        import unittest.mock as mock
        import tempfile

        original_create = socket.create_connection
        original_connect = socket.socket.connect

        create_mock = mock.MagicMock(wraps=original_create)
        connect_mock = mock.MagicMock(wraps=original_connect)

        with mock.patch('socket.create_connection', create_mock), \
             mock.patch('socket.socket.connect', connect_mock):
            from benchmarks.context_efficiency_v0.__main__ import run_g0_dry_run
            with tempfile.TemporaryDirectory() as tmp:
                result = run_g0_dry_run(tmp)
                self.assertEqual(result.get("g0_dry_run"), "PASS",
                                 f"Dry-run failed: {result.get('gates')}")
                self.assertEqual(create_mock.call_count, 0,
                                 f"socket.create_connection was called {create_mock.call_count} times")
                self.assertEqual(connect_mock.call_count, 0,
                                 f"socket.socket.connect was called {connect_mock.call_count} times")


import sys
if __name__ == "__main__":
    unittest.main()
