"""VS-1 runner offline validation — mock provider, no network.

Proves the executor pipeline end-to-end WITHOUT provider calls:
- schedule validation + call ceiling
- prompt freeze (identical core, arm-specific state only)
- artifact parse (markdown fences, commentary, malformed)
- hidden-test subprocess isolation
- scoring wiring
- evidence seal + readback

Run: python -m pytest benchmarks/vs1/runner/tests/ -q
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.vs1.runner.schedule import build_schedule, validate_schedule
from benchmarks.vs1.runner.provider import OllamaCloudAdapter, ProviderCallResult
from benchmarks.vs1.runner.prompts import build_prompt
from benchmarks.vs1.runner.executor import PoweredExecutor, parse_artifact
from benchmarks.vs1.runner.sealer import EvidenceSealer
from benchmarks.vs1.fixtures import get_fixture
from benchmarks.vs1.schemas import compute_sha256


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

    # Duck-typed interface for the executor; no class inheritance needed.
    model = "mock"


class TestSchedule(unittest.TestCase):
    def test_frozen_topology(self):
        s = build_schedule(replicates=3)
        self.assertEqual(s["expected_calls"], 108)
        self.assertEqual(s["hard_max_calls"], 108)
        self.assertTrue(validate_schedule(s))

    def test_schedule_invariants(self):
        s = build_schedule(replicates=1)
        self.assertEqual(s["expected_calls"], 36)
        self.assertTrue(validate_schedule(s))
        s["hard_max_calls"] = 999
        self.assertFalse(validate_schedule(s))


class TestPrompts(unittest.TestCase):
    def test_core_task_identical_across_arms(self):
        state = {"x": 1}
        p_stateless = build_prompt("stateless", "clean", 3, None)
        p_transcript = build_prompt("transcript", "clean", 3, state)
        # Core instruction must be the same; only state block differs.
        core_a = p_stateless.text.split("INHERITED STATE:")[0]
        core_b = p_transcript.text.split("INHERITED STATE:")[0]
        self.assertEqual(core_a, core_b)

    def test_no_coaching_language(self):
        """Protocol §13: no extra coaching for E/F."""
        state = {"claims": [{"receipt_ids": ["r1"]}]}
        for arm in ("verified_state", "verified_state_procedure"):
            p = build_prompt(arm, "clean", 3, state)
            self.assertNotIn("verify carefully", p.text.lower())
            self.assertNotIn("check contradictions", p.text.lower())
            self.assertNotIn("use provenance", p.text.lower())

    def test_prompt_sha_recorded(self):
        p = build_prompt("stateless", "clean", 3, None)
        self.assertEqual(p.sha256, compute_sha256(p.text))


class TestParseArtifact(unittest.TestCase):
    def test_plain_json(self):
        ok, text = parse_artifact('{"validation": "PASS"}')
        self.assertTrue(ok)
        self.assertIn("PASS", text)

    def test_markdown_fence(self):
        ok, text = parse_artifact('```json\n{"validation":"PASS"}\n```')
        self.assertTrue(ok)

    def test_commentary_wrapped(self):
        ok, text = parse_artifact('Here is the file:\n{"validation":"PASS"}\nDone.')
        self.assertTrue(ok)

    def test_not_json(self):
        ok, _ = parse_artifact("I cannot complete this task.")
        self.assertFalse(ok)


class TestExecutorPipeline(unittest.TestCase):
    def test_full_pipeline_mock(self):
        """Executor with mock provider: 1 replicate, 1 condition, all arms."""
        schedule = build_schedule(replicates=1, conditions=("clean",))
        provider = MockProvider()
        with tempfile.TemporaryDirectory() as d:
            executor = PoweredExecutor(
                provider=provider,
                schedule=schedule,
                workdir=Path(d) / "work",
                model="mock",
            )
            run = executor.run()
            self.assertEqual(run["call_count"], 6)  # 6 arms × 1 condition × 1 rep
            self.assertEqual(len(run["outcomes"]), 6)
            # All arms got an artifact + hidden tests ran
            for o in run["outcomes"]:
                self.assertTrue(o["artifact_written"])
                self.assertIsNotNone(o["hidden_tests"])

    def test_call_ceiling_enforced(self):
        """Executor must halt if schedule would exceed hard max."""
        schedule = build_schedule(replicates=1, conditions=("clean",))
        schedule["hard_max_calls"] = 3  # below 6 cells
        provider = MockProvider()
        with tempfile.TemporaryDirectory() as d:
            executor = PoweredExecutor(
                provider=provider,
                schedule=schedule,
                workdir=Path(d) / "work",
                model="mock",
            )
            with self.assertRaises(RuntimeError):
                executor.run()


class TestEvidenceSealer(unittest.TestCase):
    def test_seal_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            sealer = EvidenceSealer(Path(d))
            manifest = sealer.seal(
                run_metadata={"model": "mock"},
                outcomes=[{"trajectory_id": "t1", "arm": "stateless"}],
                prompts={"t1": "prompt text"},
                schedule={"cells": []},
            )
            self.assertTrue(manifest.exists())
            self.assertTrue(EvidenceSealer.verify(Path(d)))

    def test_verify_detects_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            sealer = EvidenceSealer(Path(d))
            sealer.seal(
                run_metadata={},
                outcomes=[{"trajectory_id": "t1"}],
                prompts={},
                schedule={},
            )
            # Tamper with a raw file
            raw = Path(d) / "raw"
            for f in raw.glob("*.outcome.json"):
                f.write_text('{"tampered": true}')
                break
            self.assertFalse(EvidenceSealer.verify(Path(d)))


if __name__ == "__main__":
    unittest.main()
