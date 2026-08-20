"""VS-1 harness tests: adapters, isolation, fixtures, scoring, accounting,
evidence, and the frozen G0 integrity guarantee.

Every test is deterministic and makes zero network/model calls.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.vs1 import ARMS, CONDITIONS, BOUNDARIES, get_adapter
from benchmarks.vs1.schemas import SessionEvent, ToolCallReceipt, compute_sha256, json_dumps
from benchmarks.vs1.adapters import adapter_states
from benchmarks.vs1.fixtures import get_fixture, all_fixtures
from benchmarks.vs1.isolation import (
    CANARIES,
    create_isolated_workdir,
    detect_foreign_canary,
    embed_canary,
    verify_isolation,
    verify_no_leakage,
)
from benchmarks.vs1.scorer import score_trajectory
from benchmarks.vs1.accounting import ProviderCall, sum_of_parts, pilot_accounting
from benchmarks.vs1.evidence import build_evidence_packet, reconstruct_experiment
from benchmarks.vs1.baseline import synthetic_successor


def make_transcript(task: str = "A", condition: str = "clean", n_events: int = 2) -> list[SessionEvent]:
    """Deterministic predecessor transcript for adapter tests."""
    fixture = get_fixture(task, condition)
    events = []
    for i in range(1, n_events + 1):
        tc = ToolCallReceipt(
            receipt_id=compute_sha256(f"r{i}")[:32],
            tool="write_file",
            params={"path": f"stage{i}/config.json", "content": fixture.stage_artifacts[1].content},
            status="ok",
            output="ok",
            evidence_refs=(),
            timestamp=float(i),
        )
        events.append(
            SessionEvent(
                type="agent_message",
                session_id=f"t-{i}",
                trajectory_id=f"t{task}-{condition}",
                arm="verified_state",
                condition=condition,
                worker_label=chr(64 + i),
                stage=i,
                timestamp=float(i),
                tool_calls=(tc,),
            )
        )
    return events


class TestSchemas(unittest.TestCase):
    def test_six_arms_exact(self):
        self.assertEqual(
            ARMS,
            ("stateless", "transcript", "summary", "retrieval", "verified_state", "verified_state_procedure"),
        )

    def test_six_conditions_exact(self):
        self.assertEqual(
            CONDITIONS,
            ("clean", "interruption", "reversal", "contradiction", "poison", "motif"),
        )

    def test_canonical_json_no_nan(self):
        with self.assertRaises(ValueError):
            json_dumps({"x": float("nan")})


class TestAdapters(unittest.TestCase):
    def test_all_six_registered(self):
        from benchmarks.vs1.adapters import ADAPTERS
        self.assertEqual(len(ADAPTERS), 6)

    def test_deterministic(self):
        t = _six_transcript()
        s1 = adapter_states(t)
        s2 = adapter_states(t)
        for arm in ARMS:
            self.assertEqual(json_dumps(s1[arm].to_json()), json_dumps(s2[arm].to_json()))

    def test_stateless_empty(self):
        s = get_adapter("stateless").transform(_six_transcript())
        self.assertEqual(s.content, {})

    def test_transcript_preserves_events(self):
        s = get_adapter("transcript").transform(_six_transcript())
        self.assertGreater(len(s.content["events"]), 0)

    def test_verified_requires_evidence(self):
        t = _six_transcript()
        s = get_adapter("verified_state").transform(t)
        # No evidence refs in the base transcript → no claims (unsupported omitted)
        self.assertEqual(s.content["verified_count"], 0)

    def test_verified_state_procedure_includes_procedures_key(self):
        s = get_adapter("verified_state_procedure").transform(_six_transcript())
        self.assertIn("procedures", s.content)

    def test_boundaries_all_present(self):
        self.assertEqual(len(BOUNDARIES), 6)
        for arm in ARMS:
            b = BOUNDARIES[arm]
            self.assertEqual(b.arm, arm)
            self.assertIsInstance(b.what_enters, str)
            self.assertIsInstance(b.cannot_cross_arms, tuple)


def _six_transcript():
    return _make_transcript_events()


def _make_transcript_events() -> list[SessionEvent]:
    return _six_transcript_events()


def _six_transcript_events():
    return _make_transcript_events_core()


def _make_transcript_events_core():
    fixture = get_fixture("A", "clean")
    tc = ToolCallReceipt(
        receipt_id=compute_sha256("x")[:32],
        tool="write_file",
        params={"path": "stage1/config.json", "content": fixture.stage_artifacts[1].content},
        status="ok",
        output="ok",
        evidence_refs=(),
        timestamp=1.0,
    )
    return [
        SessionEvent(
            type="agent_message",
            session_id="t-A",
            trajectory_id="t",
            arm="verified_state",
            condition="clean",
            worker_label="A",
            stage=1,
            timestamp=1.0,
            tool_calls=(tc,),
        )
    ]


class TestFixtures(unittest.TestCase):
    def test_all_fixtures_18(self):
        self.assertEqual(len(all_fixtures()), 18)

    def test_good_artifact_passes_hidden(self):
        for condition in CONDITIONS:
            fixture = get_fixture("A", condition)
            with tempfile.TemporaryDirectory() as d:
                wd = Path(d)
                fixture.write_inputs(wd)
                for stage, artifact in fixture.stage_artifacts.items():
                    fixture.write_artifact(stage, artifact.content, wd)
                results = fixture.run_hidden_test(wd)
                self.assertTrue(all(results.values()), f"{condition}: {results}")

    def test_bad_artifact_fails_hidden(self):
        for condition in CONDITIONS:
            fixture = get_fixture("A", condition)
            with tempfile.TemporaryDirectory() as d:
                wd = Path(d)
                fixture.write_inputs(wd)
                for stage in fixture.bad_artifacts:
                    fixture.write_bad_artifact(stage, wd)
                results = fixture.run_hidden_test(wd)
                self.assertFalse(all(results.values()), f"{condition}: {results}")

    def test_poison_condition_detects_poisoned_field(self):
        fixture = get_fixture("A", "poison")
        self.assertEqual(fixture.perturbation["label"], "poison")
        self.assertEqual(
            fixture.perturbation["inserted"]["value"],
            "smtp://relay.internal-ops-07.corp",
        )
        self.assertEqual(fixture.perturbation["true_value"], "smtp://relay-01.prod.internal")


class TestIsolation(unittest.TestCase):
    def test_canaries_unique_per_arm(self):
        self.assertEqual(len(set(CANARIES.values())), 6)

    def test_foreign_canary_detected(self):
        found = detect_foreign_canary(CANARIES["summary"], "stateless")
        self.assertIn("summary", found)

    def test_own_canary_not_foreign(self):
        found = detect_foreign_canary(CANARIES["verified_state"], "verified_state")
        self.assertEqual(found, [])

    def test_workdir_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            wd = create_isolated_workdir("traj-A-clean", d)
            self.assertTrue(verify_isolation(wd))

    def test_no_leakage(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "a"
            b = Path(d) / "b"
            a.mkdir()
            b.mkdir()
            (a / "f.json").write_text(json_dumps({"arm": "a"}))
            (b / "f.json").write_text(json_dumps({"arm": "b"}))
            self.assertTrue(verify_no_leakage({"a": a, "b": b}))
            (a / "leak.txt").write_text(str(b))
            self.assertFalse(verify_no_leakage({"a": a, "b": b}))


class TestScoring(unittest.TestCase):
    def test_score_produces_all_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            fixture = get_fixture("A", "clean")
            wd = Path(d)
            fixture.write_inputs(wd)
            events = synthetic_successor("t", "verified_state", "clean", "A", {}, wd, capability=1.0, seed=1)
            hidden = fixture.run_hidden_test(wd)
            score = score_trajectory(
                trajectory_id="t",
                arm="verified_state",
                condition="clean",
                task="A",
                predecessor_events=_make_transcript_events_core(),
                successor_events=events,
                hidden_test_results=hidden,
            )
            self.assertEqual(len(score.to_json()), 25)
            self.assertIsInstance(score.final_task_quality, float)

    def test_stale_state_errors_wired(self):
        """Regression: _count_stale_state_errors must be wired into scoring."""
        with tempfile.TemporaryDirectory() as d:
            fixture = get_fixture("A", "poison")
            wd = Path(d)
            fixture.write_inputs(wd)
            events = synthetic_successor("p", "stateless", "poison", "A", {}, wd, capability=1.0, seed=2)
            hidden = fixture.run_hidden_test(wd)
            score = score_trajectory(
                trajectory_id="p",
                arm="stateless",
                condition="poison",
                task="A",
                predecessor_events=_make_transcript_events_core(),
                successor_events=events,
                hidden_test_results=hidden,
            )
            self.assertIn("stale_state_errors", score.to_json())
            self.assertIsInstance(score.stale_state_errors, int)


class TestAccounting(unittest.TestCase):
    def test_sum_of_parts(self):
        calls = [
            ProviderCall("p1", "t", "B", 2, prompt_tokens=1000, completion_tokens=100),
            ProviderCall("p2", "t", "B", 2, attempt=1, prompt_tokens=1000, completion_tokens=100, status="retry", retry_of="p1"),
        ]
        r = sum_of_parts(calls, {"input_per_1k": 100, "output_per_1k": 300, "cached_input_per_1k": 50})
        self.assertEqual(r["physical_calls"], 2)
        self.assertEqual(r["logical_calls"], 1)
        self.assertTrue(r["sum_of_parts_verified"])
        # p1: 1000*0.1 + 100*0.3 = 100+30 = 130 micro; p2 same → 260
        self.assertEqual(r["micro_usd"], 260)

    def test_budget_enforced(self):
        calls = [ProviderCall("p1", "t", "A", 2, prompt_tokens=1000, completion_tokens=100)]
        r = pilot_accounting(calls, {"input_per_1k": 100, "output_per_1k": 300, "cached_input_per_1k": 50}, budget_micro_usd=100)
        self.assertFalse(r["within_budget"])


class TestEvidence(unittest.TestCase):
    def test_packet_reconstruct(self):
        with tempfile.TemporaryDirectory() as d:
            traj = {
                "t1": {
                    "trajectory": {"id": "t1", "arm": "stateless", "condition": "clean"},
                    "adapter_state": {"arm": "stateless", "content": {}, "token_cost": 0},
                    "receipt": {"receipt_id": "rct_t1", "kind": "trajectory"},
                }
            }
            packet = build_evidence_packet(
                run_id="r1",
                pilot_dir=d,
                trajectories=traj,
                pilot_config={"arms": list(ARMS), "conditions": list(CONDITIONS)},
                scores={"t1": {"final_task_quality": 0.5}},
            )
            recon = reconstruct_experiment(packet)
            self.assertEqual(recon["n_trajectories"], 1)
            self.assertTrue((packet / "MANIFEST.sha256").exists())

    def test_traversal_rejected(self):
        """Codex C10: run_id and trajectory IDs must be safe."""
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                build_evidence_packet(
                    run_id="../../../escaped",
                    pilot_dir=d,
                    trajectories={},
                    pilot_config={},
                    scores={},
                )
            with self.assertRaises(ValueError):
                build_evidence_packet(
                    run_id="r1",
                    pilot_dir=d,
                    trajectories={"../evil": {"trajectory": {}}},
                    pilot_config={},
                    scores={},
                )

    def test_reconstruction_fails_closed(self):
        """Codex C11: tampered trajectory must fail reconstruction."""
        with tempfile.TemporaryDirectory() as d:
            traj = {
                "t1": {
                    "trajectory": {"id": "t1", "arm": "stateless", "condition": "clean"},
                    "adapter_state": {"arm": "stateless", "content": {}, "token_cost": 0},
                    "receipt": {"receipt_id": "rct_t1", "kind": "trajectory"},
                }
            }
            packet = build_evidence_packet(
                run_id="r2",
                pilot_dir=d,
                trajectories=traj,
                pilot_config={"arms": list(ARMS)},
                scores={"t1": {"final_task_quality": 0.5}},
            )
            # Tamper with a trajectory file
            tampered = packet / "trajectories" / "trajectory_t1" / "trajectory.json"
            tampered.write_text('{"id": "evil"}')
            with self.assertRaises(ValueError):
                reconstruct_experiment(packet)


class TestG0IntegrityGuarantee(unittest.TestCase):
    """VS-1 must never modify G0 frozen files."""

    def test_g0_frozen_files_untouched(self):
        from benchmarks.context_efficiency_v0.g1 import g0_manifest
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        mismatches = g0_manifest.verify_frozen_manifest(g0_manifest.FROZEN_MANIFEST, root)
        self.assertEqual(mismatches, [], "G0 frozen files must remain byte-identical")
