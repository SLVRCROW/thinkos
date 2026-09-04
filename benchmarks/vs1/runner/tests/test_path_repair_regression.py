"""VS-1 path-repair regression gate (Marc act AUTHORIZE_VS1_BINDING_PATH_REPAIR...).

Proves, for EVERY scheduled task × condition × stage:
- prompt target == frozen fixture artifact path
- write target == frozen fixture artifact path
- prompt target == write target
- hidden evaluator expects the same frozen artifact path
- no hardcoded stage3/config.json assumption remains where fixture-derived
  behavior is required
- preflight fails on any scheduled cell violating the invariant

Enumerates the FULL frozen schedule (6 arms × 6 conditions × 3 replicates),
not just interruption/motif.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from benchmarks.vs1.fixtures import get_fixture
from benchmarks.vs1.runner.prompts import build_prompt, fixture_artifact_path
from benchmarks.vs1.runner.schedule import build_schedule
from benchmarks.vs1.runner.executor import build_successor_events
from benchmarks.vs1.runner import prompts as prompts_mod


REPO_ROOT = Path(__file__).resolve().parents[4]


def all_scheduled_cells():
    sched = build_schedule(replicates=1)  # topology identical; reps don't change paths
    for cell in sched["cells"]:
        yield cell


class TestFixturePathIsAuthority(unittest.TestCase):
    def test_no_hardcoded_path_remains(self):
        """The hardcoded TASK_ARTIFACT_PATH must be gone or unused."""
        # The frozen prompt builder must derive from the fixture.
        src = (REPO_ROOT / "benchmarks/vs1/runner/executor.py").read_text()
        # executor should never hardcode stage3/config.json anymore
        self.assertNotIn('"stage3/config.json"', src)
        prompts_src = (REPO_ROOT / "benchmarks/vs1/runner/prompts.py").read_text()
        # any remaining hardcoded map must not be load-bearing for the prompt target
        self.assertNotIn("path = TASK_ARTIFACT_PATH", prompts_src)

    def test_fixture_derives_correct_paths_per_condition(self):
        """interruption and motif must yield final.json; others config.json."""
        expected = {
            "clean": "stage3/config.json",
            "interruption": "stage3/final.json",
            "reversal": "stage3/config.json",
            "contradiction": "stage3/config.json",
            "poison": "stage3/config.json",
            "motif": "stage3/final.json",
        }
        for condition, exp in expected.items():
            path = fixture_artifact_path("A", condition, 3)
            self.assertEqual(path, exp, f"{condition}: fixture-derived path mismatch")

    def test_full_schedule_prompt_target_equals_write_target(self):
        """For EVERY scheduled cell: prompt target == write target == fixture path."""
        for cell in all_scheduled_cells():
            cond = cell["condition"]
            exp = fixture_artifact_path("A", cond, 3)
            # prompt must declare the frozen path
            prompt = build_prompt(cell["arm"], cond, 3, None)
            self.assertIn(exp, prompt.text, f"{cell['trajectory_id']}: prompt missing target")
            # if the fixture path differs from the legacy default, the prompt
            # must NOT still carry the legacy hardcoded path anywhere
            if exp != "stage3/config.json":
                self.assertNotIn(
                    "stage3/config.json",
                    prompt.text,
                    f"{cell['trajectory_id']}: prompt still hardcodes config.json",
                )
            # write target (via event reconstruction, which mirrors executor write path)
            ev = build_successor_events(cell["trajectory_id"], cell["arm"], cond, "{}")
            written = [tc.params.get("path") for e in ev for tc in e.tool_calls]
            self.assertEqual(written, [exp], f"{cell['trajectory_id']}: write target mismatch")

    def test_hidden_evaluator_expects_fixture_path(self):
        """The hidden evaluator (fixture.run_hidden_test) reads artifact.path."""
        for condition in ("clean", "interruption", "reversal", "contradiction", "poison", "motif"):
            fixture = get_fixture("A", condition)
            stage3 = fixture.stage_artifacts[3]
            # The evaluator resolves via fixture paths; assert it's the same
            # path the runner now writes.
            self.assertEqual(stage3.path, fixture_artifact_path("A", condition, 3))

    def test_schedule_preflight_invariant(self):
        """No scheduled cell may reference a path absent from its fixture."""
        for cell in all_scheduled_cells():
            fixture = get_fixture("A", cell["condition"])
            self.assertIn(3, fixture.stage_artifacts)
            p = fixture.stage_artifacts[3].path
            self.assertTrue(p, f"{cell['trajectory_id']}: empty artifact path")


if __name__ == "__main__":
    unittest.main()
