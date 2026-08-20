"""VS-1 R4 regression tests (act §10 A-O).

Proves the repaired instrument:
- persists raw completions BEFORE parsing (never erases model output)
- survives mid-run halts with reconstructible evidence
- classifies subject vs method vs provider failure per frozen rules
- enforces the method gate with the frozen attempted-call denominator
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.vs1.runner.classification import (
    INSTRUMENT_FAILURE,
    MIXED_AMBIGUOUS,
    OK,
    PROVIDER_RUNTIME_FAILURE,
    SUBJECT_TASK_FAILURE,
    classify_outcome,
    contract_check,
    contract_check_csv,
    contract_check_json,
)
from benchmarks.vs1.runner.method_gate import (
    CATASTROPHIC_BURST,
    METHOD_FAILURE_THRESHOLD,
    MIN_SAMPLE,
    MethodGateState,
)
from benchmarks.vs1.runner.sealer import EvidenceSealer
from benchmarks.vs1.runner.executor import PoweredExecutor, parse_artifact
from benchmarks.vs1.runner.provider import ProviderCallResult
from benchmarks.vs1.runner.schedule import build_schedule
from benchmarks.vs1.fixtures import get_fixture


def make_provider(content: str, status: str = "ok", error: str = "") -> ProviderCallResult:
    return ProviderCallResult(
        provider_invocation_id="call-test",
        requested_model="mock",
        returned_model="mock",
        content=content,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        status=status,
        error=error,
        latency_seconds=0.1,
    )


class TestRawPersistence(unittest.TestCase):
    """A + B + C + E: raw completion persisted before parsing, survives failures."""

    def _run_with_sealer(self, content: str, status: str = "ok", error: str = "", condition: str = "interruption", stage: int = 2):
        # NOTE: mkdtemp (not TemporaryDirectory context) so the dir survives
        # the helper's return — the test reads the persisted files after.
        d = tempfile.mkdtemp()
        root = Path(d)
        sealer = EvidenceSealer(root)
        sched = build_schedule(replicates=1, conditions=(condition,), arms=("stateless",))
        cell = next(c for c in sched["cells"] if c["stage"] == stage)
        ex = PoweredExecutor(
            provider=type("P", (), {"complete": lambda self, p, i: make_provider(content, status, error)})(),
            schedule=sched,
            workdir=root / "work",
            model="mock",
            sealer=sealer,
        )
        outcome = ex._run_cell(cell)
        return root, sealer, outcome, cell["expected_call_id"]

    def test_A_valid_csv_persisted_parsed_scored(self):
        root, sealer, outcome, cid = self._run_with_sealer("id,score,status\na1,90,ok\n")
        raw = (root / "raw" / f"{cid}.raw.txt").read_text()
        self.assertEqual(raw, "id,score,status\na1,90,ok\n")
        self.assertTrue(outcome.artifact_written)
        self.assertFalse(outcome.method_failure)
        self.assertEqual(outcome.classification["category"], OK)

    def test_B_invalid_csv_raw_preserved_subject_failure(self):
        root, sealer, outcome, cid = self._run_with_sealer("this is not a csv at all")
        raw = (root / "raw" / f"{cid}.raw.txt").read_text()
        self.assertEqual(raw, "this is not a csv at all")
        self.assertFalse(outcome.artifact_written)
        self.assertFalse(outcome.method_failure)  # subject failure, NOT method
        self.assertEqual(outcome.classification["category"], SUBJECT_TASK_FAILURE)

    def test_C_fenced_valid_csv_preserved_and_parsed(self):
        root, sealer, outcome, cid = self._run_with_sealer("```csv\nid,score,status\na1,90,ok\n```")
        raw = (root / "raw" / f"{cid}.raw.txt").read_text()
        self.assertIn("```csv", raw)  # raw is preserved verbatim
        self.assertTrue(outcome.artifact_written)  # parser strips fences
        self.assertEqual(outcome.classification["category"], OK)

    def test_E_empty_provider_response_receipt_preserved(self):
        root, sealer, outcome, cid = self._run_with_sealer("", status="error", error="empty completion")
        raw = (root / "raw" / f"{cid}.raw.txt").read_text()
        self.assertEqual(raw, "")
        prov = json.loads((root / "raw" / f"{cid}.provider.json").read_text())
        self.assertEqual(prov["status"], "error")
        self.assertTrue(outcome.method_failure)
        self.assertEqual(outcome.classification["category"], PROVIDER_RUNTIME_FAILURE)


class TestIncrementalDurability(unittest.TestCase):
    """F + G + H: mid-run halt leaves every completed call reconstructible."""

    def test_G_intentional_halt_reconstructs_all_calls(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sealer = EvidenceSealer(root)
            sched = build_schedule(replicates=1, conditions=("clean", "reversal", "contradiction"), arms=("stateless",))
            # Provider that raises after 2 calls (simulating a crash)
            class CrashProvider:
                def __init__(self):
                    self.n = 0
                def complete(self, prompt, inv):
                    self.n += 1
                    if self.n > 2:
                        raise RuntimeError("simulated crash")
                    return make_provider("id,score,status\na1,90,ok\n")
            ex = PoweredExecutor(
                provider=CrashProvider(),
                schedule=sched,
                workdir=root / "work",
                model="mock",
                sealer=sealer,
            )
            with self.assertRaises(RuntimeError):
                ex.run()
            # Every completed call must be reconstructible from the ledger + raw files
            ledger = (root / "raw" / "CALL_LEDGER.jsonl").read_text().strip().splitlines()
            self.assertEqual(len(ledger), 2)
            raw_files = sorted((root / "raw").glob("*.raw.txt"))
            self.assertEqual(len(raw_files), 2)
            for entry in ledger:
                d = json.loads(entry)
                raw = (root / "raw" / f"{d['call_id']}.raw.txt").read_text()
                self.assertEqual(raw, "id,score,status\na1,90,ok\n")

    def test_H_manifest_reconstruction_after_halt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sealer = EvidenceSealer(root)
            sched = build_schedule(replicates=1, conditions=("clean",), arms=("stateless",))
            ex = PoweredExecutor(
                provider=type("P", (), {"complete": lambda self, p, i: make_provider("id,score,status\na1,90,ok\n")})(),
                schedule=sched,
                workdir=root / "work",
                model="mock",
                sealer=sealer,
            )
            ex.run()
            manifest = sealer.seal(
                run_metadata={"model": "mock", "actual_calls": 1},
                outcomes=[o.to_json() for o in ex.results],
                prompts={},
                schedule=sched,
            )
            self.assertTrue(manifest.exists())
            self.assertTrue(EvidenceSealer.verify(root))


class TestClassification(unittest.TestCase):
    """D + classification rules: parser rejection of valid CSV = INSTRUMENT_FAILURE."""

    def test_D_parser_rejects_contract_valid_csv(self):
        # Contract-valid CSV that the parser would reject (e.g., no comma in header)
        raw = "id score status\na1 90 ok\n"
        self.assertFalse(contract_check_csv(raw, "stage2/records.csv"))
        # But if contract says valid and parser rejects -> INSTRUMENT_FAILURE
        # (simulate by forcing contract_ok=True with parse_ok=False)
        cls = classify_outcome(
            provider_status="ok",
            provider_error="",
            raw_content=raw,
            parse_ok=False,
            contract_ok=True,
            target_path="stage2/records.csv",
        )
        self.assertEqual(cls.category, INSTRUMENT_FAILURE)

    def test_contract_check_csv(self):
        self.assertTrue(contract_check_csv("id,score,status\na1,90,ok\n", "stage2/records.csv"))
        self.assertFalse(contract_check_csv("no comma here", "stage2/records.csv"))
        self.assertFalse(contract_check_csv("", "stage2/records.csv"))
        self.assertIsNone(contract_check_csv("anything", "stage3/config.json"))

    def test_contract_check_json(self):
        self.assertTrue(contract_check_json('{"validation": "PASS"}', "stage3/config.json"))
        self.assertFalse(contract_check_json("not json", "stage3/config.json"))
        self.assertIsNone(contract_check_json("anything", "stage2/records.csv"))

    def test_classify_provider_failure(self):
        cls = classify_outcome(
            provider_status="error",
            provider_error="timeout",
            raw_content="",
            parse_ok=False,
            contract_ok=None,
            target_path="stage3/config.json",
        )
        self.assertEqual(cls.category, PROVIDER_RUNTIME_FAILURE)

    def test_classify_empty_completion(self):
        cls = classify_outcome(
            provider_status="ok",
            provider_error="",
            raw_content="",
            parse_ok=False,
            contract_ok=None,
            target_path="stage3/config.json",
        )
        self.assertEqual(cls.category, PROVIDER_RUNTIME_FAILURE)

    def test_classify_subject_failure(self):
        cls = classify_outcome(
            provider_status="ok",
            provider_error="",
            raw_content="garbage",
            parse_ok=False,
            contract_ok=False,
            target_path="stage3/config.json",
        )
        self.assertEqual(cls.category, SUBJECT_TASK_FAILURE)

    def test_classify_ok(self):
        cls = classify_outcome(
            provider_status="ok",
            provider_error="",
            raw_content='{"validation": "PASS"}',
            parse_ok=True,
            contract_ok=True,
            target_path="stage3/config.json",
        )
        self.assertEqual(cls.category, OK)

    def test_classify_parsed_but_contract_violated(self):
        """Atlas F1/F3: parse_ok=True but contract_ok=False -> SUBJECT_TASK_FAILURE."""
        cls = classify_outcome(
            provider_status="ok",
            provider_error="",
            raw_content="id,score,status\n",  # header-only CSV
            parse_ok=True,
            contract_ok=False,
            target_path="stage2/records.csv",
        )
        self.assertEqual(cls.category, SUBJECT_TASK_FAILURE)

    def test_json_brace_in_string(self):
        """Atlas F2: braces inside string values must not break the scanner."""
        raw = '{"key": "value {nested}", "validation": "PASS"}'
        self.assertTrue(contract_check_json(raw, "stage3/config.json"))
        ok, _ = parse_artifact(raw, "stage3/config.json")
        self.assertTrue(ok)

    def test_json_brace_in_string_escaped(self):
        raw = '{"key": "value \\" {nested}", "validation": "PASS"}'
        self.assertTrue(contract_check_json(raw, "stage3/config.json"))


class TestMethodGate(unittest.TestCase):
    """I + J + K + L: denominator reference vectors, early failures, bursts."""

    def test_I_denominator_is_attempted_calls(self):
        g = MethodGateState()
        # 2 method failures out of 20 attempted = 10% > 5% -> halt
        for i in range(20):
            cat = PROVIDER_RUNTIME_FAILURE if i < 2 else OK
            g.record({"category": cat, "reason": ""})
        self.assertTrue(g.halted)
        self.assertEqual(g.attempted_calls, 20)
        self.assertEqual(g.method_failures, 2)
        self.assertAlmostEqual(g.rate, 0.10)

    def test_I_below_threshold_no_halt(self):
        g = MethodGateState()
        # 1 method failure at attempt 20 of 20 = 5% exactly -> NOT > 5% -> no halt
        for i in range(20):
            cat = PROVIDER_RUNTIME_FAILURE if i == 19 else OK
            g.record({"category": cat, "reason": ""})
        self.assertFalse(g.halted)
        self.assertAlmostEqual(g.rate, 0.05)

    def test_J_early_failures_below_min_sample_no_halt(self):
        g = MethodGateState()
        # 1 failure in first 5 attempts: 20% but below MIN_SAMPLE=10 -> no halt
        g.record({"category": PROVIDER_RUNTIME_FAILURE, "reason": ""})
        for _ in range(4):
            g.record({"category": OK, "reason": ""})
        self.assertFalse(g.halted)

    def test_J_boundary_one_failure_at_min_sample_halt(self):
        """Solomon F7: exactly MIN_SAMPLE=10 with 1 failure = 10% > 5% -> halt."""
        g = MethodGateState()
        g.record({"category": PROVIDER_RUNTIME_FAILURE, "reason": ""})
        for _ in range(9):
            g.record({"category": OK, "reason": ""})
        self.assertTrue(g.halted)
        self.assertEqual(g.attempted_calls, 10)
        self.assertIn("METHOD_FAILURE_TOLERANCE_EXCEEDED", g.halt_reason)

    def test_K_six_of_six_infrastructure_failures_halt(self):
        g = MethodGateState()
        # 6 consecutive method failures -> CATASTROPHIC_BURST at 3 -> halt
        for _ in range(6):
            g.record({"category": PROVIDER_RUNTIME_FAILURE, "reason": ""})
        self.assertTrue(g.halted)
        self.assertIn("CATASTROPHIC_BURST", g.halt_reason)

    def test_L_subject_failures_do_not_count_as_method(self):
        g = MethodGateState()
        # 20 subject task failures = 0 method failures -> no halt
        for _ in range(20):
            g.record({"category": SUBJECT_TASK_FAILURE, "reason": ""})
        self.assertFalse(g.halted)
        self.assertEqual(g.method_failures, 0)
        self.assertEqual(g.subject_task_failures, 20)

    def test_L_mixed_ambiguous_counts_as_method(self):
        g = MethodGateState()
        for _ in range(20):
            g.record({"category": MIXED_AMBIGUOUS, "reason": ""})
        self.assertTrue(g.halted)
        self.assertEqual(g.method_failures, 20)


class TestImmediateValidityHalt(unittest.TestCase):
    """M + N + O: catastrophic validity failures halt regardless of percentage."""

    def test_M_model_mismatch(self):
        from benchmarks.vs1.runner.method_gate import immediate_validity_halt
        r = immediate_validity_halt("model identity mismatch: expected deepseek-v4-pro:0813, got other")
        self.assertTrue(r["halted"])
        self.assertIn("model identity mismatch", r["halt_reason"])

    def test_N_evidence_persistence_failure(self):
        from benchmarks.vs1.runner.method_gate import immediate_validity_halt
        r = immediate_validity_halt("evidence persistence failure: raw write failed")
        self.assertTrue(r["halted"])

    def test_O_hidden_test_leakage(self):
        from benchmarks.vs1.runner.method_gate import immediate_validity_halt
        r = immediate_validity_halt("hidden-test leakage detected")
        self.assertTrue(r["halted"])


if __name__ == "__main__":
    unittest.main()
