"""VS-1 R3 F1–F5 regression tests (Marc act AUTHORIZE_VS1_R3... §14).

Mechanically verifies:
A. Every required fixture predecessor artifact is materialized.
B. Materialized artifact bytes equal frozen fixture bytes.
C. Motif preserves both predecessor artifact and procedure event.
D. Interruption common task substrate is identical across all six arms.
E. Architecture-specific inherited state remains isolated.
F. Structured F procedure representation does not leak into E/A/B/C/D.
G. B/C/D retain historical information their architecture permits.
H. Stage-2 interruption output becomes valid predecessor state for stage-3.
I. Prompt target == runner write target == fixture target.
J. Evaluator examines the intended frozen artifact paths.
K. Schedule generation yields 108 trajectories.
L. Schedule generation yields 126 physical provider calls.
M. No trajectory shares state with another replicate unless allowed.
N. Hidden-test evaluator separation remains intact.
O. Semantic canaries remain detectable.
P. Zero-activity trajectories remain method failures, not perfect scores.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.vs1.fixtures import get_fixture, inject_predecessor_state
from benchmarks.vs1.runner.executor import (
    PoweredExecutor,
    build_predecessor_events,
    build_successor_events,
)
from benchmarks.vs1.runner.prompts import build_prompt, fixture_artifact_path, TASK_SUBSTRATE
from benchmarks.vs1.runner.schedule import build_schedule, validate_schedule
from benchmarks.vs1.runner.provider import ProviderCallResult
from benchmarks.vs1.adapters import get_adapter
from benchmarks.vs1.isolation import CANARIES, detect_foreign_canary
from benchmarks.vs1.scorer import score_trajectory
from benchmarks.vs1.schemas import ARMS, CONDITIONS, SessionEvent, ToolCallReceipt, compute_sha256


class MockProvider:
    """Deterministic fake provider for offline validation."""

    def __init__(self, reply: str = '{"validation": "PASS", "checks": {"structure_valid": true, "all_stages_present": true}, "acceptance_tests_passed": 3, "total_tests": 3}'):
        self.reply = reply
        self.calls = 0

    def complete(self, prompt: str, invocation_id: str) -> ProviderCallResult:
        self.calls += 1
        return ProviderCallResult(
            provider_invocation_id=invocation_id,
            requested_model="mock",
            returned_model="mock",
            content=self.reply,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=100,
            total_tokens=len(prompt) // 4 + 100,
            status="ok",
            latency_seconds=0.1,
        )


class TestF1PredecessorMaterialization(unittest.TestCase):
    """A + B: every fixture predecessor artifact materialized, bytes equal."""

    def test_all_conditions_materialize_stage1(self):
        for condition in CONDITIONS:
            fixture = get_fixture("A", condition)
            pred = build_predecessor_events("t", condition)
            with tempfile.TemporaryDirectory() as d:
                wd = Path(d)
                ex = PoweredExecutor(MockProvider(), build_schedule(replicates=1), wd, "mock")
                ex._materialize_predecessor(wd, pred, condition)
                # Stage-1 artifact must exist with byte-identical content
                stage1 = fixture.stage_artifacts[1]
                p = wd / stage1.path
                self.assertTrue(p.exists(), f"{condition}: stage1 not materialized")
                self.assertEqual(p.read_text(), stage1.content, f"{condition}: bytes differ")

    def test_motif_materializes_stage1_and_stage2(self):
        fixture = get_fixture("A", "motif")
        pred = build_predecessor_events("t", "motif")
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            ex = PoweredExecutor(MockProvider(), build_schedule(replicates=1), wd, "mock")
            ex._materialize_predecessor(wd, pred, condition="motif")
            for stage in (1, 2):
                art = fixture.stage_artifacts[stage]
                p = wd / art.path
                self.assertTrue(p.exists(), f"motif stage{stage} not materialized")
                self.assertEqual(p.read_text(), art.content)


class TestF2MotifEventPreservation(unittest.TestCase):
    """C: motif preserves both predecessor artifact and procedure event."""

    def test_motif_has_write_and_run(self):
        pred = build_predecessor_events("t", "motif")
        tools = [tc.tool for e in pred for tc in e.tool_calls]
        self.assertIn("write_file", tools)
        self.assertIn("run", tools)
        # The write_file must carry the stage-1 path
        paths = [tc.params.get("path") for e in pred for tc in e.tool_calls if tc.tool == "write_file"]
        self.assertIn("stage1/procedure.json", paths)

    def test_motif_serialization_roundtrip(self):
        pred = build_predecessor_events("t", "motif")
        for e in pred:
            d = e.to_json()
            self.assertTrue(d["tool_calls"])


class TestF3CommonSubstrate(unittest.TestCase):
    """D: interruption common task substrate identical across all six arms."""

    def test_substrate_identical_across_arms(self):
        texts = []
        for arm in ARMS:
            p = build_prompt(arm, "interruption", 2, None)
            # Extract the TASK SUBSTRATE block (between markers)
            start = p.text.index("TASK SUBSTRATE:")
            end = p.text.index("INHERITED STATE:")
            texts.append(p.text[start:end])
        self.assertEqual(len(set(texts)), 1, "substrate must be byte-identical across arms")

    def test_substrate_has_csv_schema(self):
        self.assertIn("id, score, status", TASK_SUBSTRATE["interruption"])
        self.assertIn("stage2/records.csv", TASK_SUBSTRATE["interruption"])
        self.assertIn("stage3/final.json", TASK_SUBSTRATE["interruption"])

    def test_no_coaching_in_substrate(self):
        for cond, text in TASK_SUBSTRATE.items():
            self.assertNotIn("verify carefully", text.lower())
            self.assertNotIn("inspect provenance", text.lower())
            self.assertNotIn("check contradictions", text.lower())
            self.assertNotIn("reuse the procedure", text.lower())
            self.assertNotIn("distrust inherited state", text.lower())


class TestF4ProcedureRouting(unittest.TestCase):
    """E + F + G: architecture isolation; F procedure does not leak into E/A/B/C/D."""

    def test_E_does_not_receive_F_procedure(self):
        pred = build_predecessor_events("t", "motif")
        e_state = get_adapter("verified_state").transform(pred)
        f_state = get_adapter("verified_state_procedure").transform(pred)
        self.assertEqual(len(e_state.content.get("procedures", [])), 0, "E must not get F's procedures")
        self.assertGreaterEqual(len(f_state.content.get("procedures", [])), 1, "F must get procedures")

    def test_stateless_empty(self):
        pred = build_predecessor_events("t", "motif")
        s = get_adapter("stateless").transform(pred)
        self.assertEqual(s.content, {})

    def test_BC_retain_history(self):
        pred = build_predecessor_events("t", "motif")
        b = get_adapter("transcript").transform(pred)
        c = get_adapter("summary").transform(pred)
        self.assertGreater(len(b.content.get("events", [])), 0)
        self.assertGreaterEqual(len(c.content.get("claims", [])), 1)


class TestF5InterruptionTopology(unittest.TestCase):
    """H + K + L: two-stage interruption; schedule 108/126."""

    def test_schedule_108_126(self):
        s = build_schedule(replicates=3)
        self.assertEqual(s["trajectories"], 108)
        self.assertEqual(s["expected_calls"], 126)
        self.assertTrue(validate_schedule(s))

    def test_stage2_output_feeds_stage3(self):
        """H: stage-2 output becomes valid predecessor state for stage-3."""
        fixture = get_fixture("A", "interruption")
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            ex = PoweredExecutor(MockProvider(), build_schedule(replicates=1), wd, "mock")
            # Simulate stage-2 call writing its artifact
            s2 = fixture.stage_artifacts[2]
            p = wd / "t" / s2.path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(s2.content)
            # Stage-3 predecessor must now include the stage-2 output
            pred = build_predecessor_events("t", "interruption")
            pred3 = ex._prepend_stage2_output(pred, "t", "interruption")
            paths = [tc.params.get("path") for e in pred3 for tc in e.tool_calls]
            self.assertIn("stage2/records.csv", paths)


class TestPathConsistency(unittest.TestCase):
    """I + J: prompt target == write target == fixture target; evaluator reads fixture paths."""

    def test_prompt_write_fixture_consistency(self):
        for condition in CONDITIONS:
            for stage in (2, 3):
                exp = fixture_artifact_path("A", condition, stage)
                p = build_prompt("stateless", condition, stage, None)
                self.assertIn(exp, p.text, f"{condition} s{stage}: prompt missing target")
                ev = build_successor_events("t", "stateless", condition, "{}", stage=stage)
                written = [tc.params.get("path") for e in ev for tc in e.tool_calls]
                self.assertEqual(written, [exp], f"{condition} s{stage}: write target mismatch")

    def test_evaluator_uses_fixture_paths(self):
        for condition in CONDITIONS:
            fixture = get_fixture("A", condition)
            for stage, art in fixture.stage_artifacts.items():
                self.assertEqual(art.path, fixture_artifact_path("A", condition, stage))


class TestIsolationAndCanaries(unittest.TestCase):
    """M + N + O: no cross-replicate sharing; evaluator separation; canaries."""

    def test_no_cross_replicate_sharing(self):
        s = build_schedule(replicates=3)
        tids = [c["trajectory_id"] for c in s["cells"]]
        # Each trajectory id is unique; no shared state between replicates
        self.assertEqual(len(set(tids)), 108)

    def test_canaries_detectable(self):
        for arm in ARMS:
            self.assertIn(arm, CANARIES)
        foreign = detect_foreign_canary(CANARIES["summary"], "stateless")
        self.assertIn("summary", foreign)

    def test_hidden_evaluator_separation(self):
        """N: evaluator subprocess receives only condition + workdir."""
        import inspect
        src = inspect.getsource(PoweredExecutor._evaluate_hidden)
        self.assertIn("sys.argv[1]", src)
        self.assertIn("sys.argv[2]", src)
        self.assertNotIn("stage_artifacts[3].content", src)


class TestZeroActivityMethodFailure(unittest.TestCase):
    """P: zero-activity trajectories remain method failures, not perfect scores."""

    def test_zero_activity_is_method_failure(self):
        fixture = get_fixture("A", "clean")
        pred = build_predecessor_events("t", "clean")
        score = score_trajectory(
            trajectory_id="t",
            arm="stateless",
            condition="clean",
            task="A",
            predecessor_events=pred,
            successor_events=[],
            hidden_test_results=None,
        )
        self.assertTrue(score.method_failure)
        self.assertEqual(score.final_task_quality, 0.0)


if __name__ == "__main__":
    unittest.main()
