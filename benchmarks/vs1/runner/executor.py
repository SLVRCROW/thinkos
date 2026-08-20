"""VS-1 powered executor — one frozen run.

Responsibilities:
1. For each schedule cell: build predecessor state via frozen adapters,
   build the frozen prompt, make exactly ONE provider call.
2. Write the model's artifact to the isolated workdir.
3. Run hidden tests in a SEPARATE subprocess (truth never enters model
   context; leakage attempts during preflight prove this).
4. Persist raw evidence: prompt, response, artifact, receipt, score.
5. Enforce hard call ceiling (108); halt on any deviation.
6. Write immutable manifest + seal raw evidence BEFORE analysis.

Design constraint: this module imports the frozen measurement package
(benchmarks/vs1/*) for adapters/scoring, but never modifies it. It does
NOT touch product runtime, G0, or G1.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.vs1.schemas import (
    ARMS,
    CONDITIONS,
    SessionEvent,
    ToolCallReceipt,
    EvidenceReference,
    compute_sha256,
    json_dumps,
)
from benchmarks.vs1.adapters import get_adapter
from benchmarks.vs1.fixtures import get_fixture, inject_predecessor_state
from benchmarks.vs1.scorer import score_trajectory
from benchmarks.vs1.isolation import create_isolated_workdir

from .provider import OllamaCloudAdapter, ProviderCallResult
from .prompts import build_prompt, fixture_artifact_path


@dataclass(frozen=True)
class CellOutcome:
    trajectory_id: str
    arm: str
    condition: str
    replicate: int
    call_id: str
    provider: ProviderCallResult
    artifact_written: bool
    artifact_path: str
    hidden_tests: dict[str, bool] | None
    score: dict[str, Any]
    prompt_sha256: str
    prompt_text: str
    method_failure: bool
    method_failure_reason: str = ""

    def to_json(self) -> dict:
        return {
            "trajectory_id": self.trajectory_id,
            "arm": self.arm,
            "condition": self.condition,
            "replicate": self.replicate,
            "call_id": self.call_id,
            "provider": self.provider.to_json(),
            "artifact_written": self.artifact_written,
            "artifact_path": self.artifact_path,
            "hidden_tests": self.hidden_tests,
            "score": self.score,
            "prompt_sha256": self.prompt_sha256,
            "method_failure": self.method_failure,
            "method_failure_reason": self.method_failure_reason,
        }


def parse_artifact(content: str) -> tuple[bool, str]:
    """Extract a JSON artifact from a model's response.

    Strips markdown fences if present, finds the first balanced JSON object.
    Returns (parsed, canonical_json) or (False, raw_text).
    """
    import re
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        return False, text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    parsed = json.loads(candidate)
                    return True, json.dumps(parsed, sort_keys=True)
                except json.JSONDecodeError:
                    return False, text
    return False, text


def build_predecessor_events(tid: str, condition: str) -> list[SessionEvent]:
    """Deterministic predecessor events with condition injection (no model)."""
    fixture = get_fixture("A", condition)
    content = fixture.stage_artifacts[1].content
    path = fixture.stage_artifacts[1].path
    tc = ToolCallReceipt(
        receipt_id=compute_sha256(tid)[:32],
        tool="write_file",
        params={"path": path, "content": content},
        status="ok",
        output="ok",
        evidence_refs=(),
        timestamp=1.0,
    )
    base = [
        SessionEvent(
            type="agent_message",
            session_id=f"{tid}-A",
            trajectory_id=tid,
            arm="verified_state",
            condition=condition,
            worker_label="A",
            stage=1,
            timestamp=1.0,
            tool_calls=(tc,),
        )
    ]
    return inject_predecessor_state(condition, base)


def build_successor_events(tid: str, arm: str, condition: str, artifact: str, stage: int = 3) -> list[SessionEvent]:
    """Reconstruct the successor event stream from the recorded artifact write
    (no model call — deterministic)."""
    rel = fixture_artifact_path("A", condition, stage)
    tc = ToolCallReceipt(
        receipt_id=compute_sha256(f"{tid}-succ")[:32],
        tool="write_file",
        params={"path": rel, "content": artifact},
        status="ok",
        output="ok",
        evidence_refs=(
            (EvidenceReference(receipt_id="rct_succ", claim_type="artifact", claim_value=artifact[:64]),)
            if arm in ("verified_state", "verified_state_procedure")
            else ()
        ),
        timestamp=float(stage),
    )
    return [
        SessionEvent(
            type="agent_message",
            session_id=f"{tid}-S",
            trajectory_id=tid,
            arm=arm,
            condition=condition,
            worker_label="S",
            stage=3,
            timestamp=3.0,
            tool_calls=(tc,),
        )
    ]


class PoweredExecutor:
    """One frozen powered run against a schedule."""

    def __init__(
        self,
        provider: OllamaCloudAdapter,
        schedule: dict[str, Any],
        workdir: Path,
        model: str,
        prompt_version: str = "v0.1.0",
    ):
        self.provider = provider
        self.schedule = schedule
        self.workdir = workdir
        self.model = model
        self.prompt_version = prompt_version
        self.results: list[CellOutcome] = []

    def run(self) -> dict[str, Any]:
        cells = self.schedule["cells"]
        planned = self.schedule["expected_calls"]
        if len(cells) != planned:
            raise RuntimeError(f"Schedule mismatch: {len(cells)} cells vs {planned} planned")

        call_count = 0
        for cell in cells:
            call_count += 1
            if call_count > self.schedule["hard_max_calls"]:
                raise RuntimeError("CALL CEILING EXCEEDED — halting per containment")
            outcome = self._run_cell(cell)
            self.results.append(outcome)
            print(
                f"[{call_count}/{planned}] {cell['trajectory_id']} "
                f"{cell['arm']:24s} {cell['condition']:14s} "
                f"status={outcome.provider.status} mf={outcome.method_failure}",
                flush=True,
            )

        return {
            "call_count": call_count,
            "planned": planned,
            "hard_max": self.schedule["hard_max_calls"],
            "model": self.model,
            "outcomes": [o.to_json() for o in self.results],
        }

    def _run_cell(self, cell: dict[str, Any]) -> CellOutcome:
        arm = cell["arm"]
        condition = cell["condition"]
        replicate = cell["replicate"]
        tid = cell["trajectory_id"]
        stage = cell["stage"]
        fixture = get_fixture("A", condition)

        pred = build_predecessor_events(tid, condition)
        # F5 REPAIR: for interruption stage-3, the stage-2 successor's output
        # (written by the earlier call in this same trajectory) becomes
        # predecessor state for the stage-3 successor, rendered through each
        # architecture's succession mechanism.
        if condition == "interruption" and stage == 3:
            pred = self._prepend_stage2_output(pred, tid, condition)
        state = get_adapter(arm).transform(pred)
        prompt = build_prompt(arm, condition, stage, state, prompt_version=self.prompt_version)

        call_id = cell["expected_call_id"]
        provider_res = self.provider.complete(prompt.text, call_id)

        workdir = create_isolated_workdir(tid, self.workdir)
        # F1 REPAIR: materialize every fixture-declared predecessor artifact
        # into the trajectory workdir BEFORE successor execution/evaluation.
        self._materialize_predecessor(workdir, pred, condition, current_stage=stage)
        # F5 REPAIR: for interruption stage-3, the stage-2 artifact produced by
        # the earlier call in this trajectory must be present as predecessor.
        if condition == "interruption" and stage == 3:
            self._materialize_stage2_output(workdir, tid, arm, condition)
        ok, artifact_text = parse_artifact(provider_res.content)
        artifact_path = ""
        if ok:
            rel = fixture_artifact_path("A", condition, stage)
            p = workdir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(artifact_text)
            artifact_path = str(p)

        hidden: dict[str, bool] | None = None
        method_failure = False
        reason = ""
        if provider_res.status != "ok":
            method_failure = True
            reason = f"provider error: {provider_res.error}"
        elif not ok:
            method_failure = True
            reason = "artifact parse failed"
        else:
            hidden = self._evaluate_hidden(workdir, condition)

        succ_events = build_successor_events(tid, arm, condition, artifact_text if ok else "")
        score = score_trajectory(
            trajectory_id=tid,
            arm=arm,
            condition=condition,
            task="A",
            predecessor_events=pred,
            successor_events=succ_events,
            hidden_test_results=hidden,
            method_failure=method_failure,
            method_failure_reason=reason,
            token_usage=provider_res.total_tokens,
            provider_calls=1 if provider_res.status == "ok" else 0,
            latency_seconds=provider_res.latency_seconds,
        )

        return CellOutcome(
            trajectory_id=tid,
            arm=arm,
            condition=condition,
            replicate=replicate,
            call_id=provider_res.provider_invocation_id,
            provider=provider_res,
            artifact_written=ok,
            artifact_path=artifact_path,
            hidden_tests=hidden,
            score=score.to_json(),
            prompt_sha256=prompt.sha256,
            prompt_text=prompt.text,
            method_failure=method_failure,
            method_failure_reason=reason,
        )

    def _prepend_stage2_output(self, pred: list[SessionEvent], tid: str, condition: str) -> list[SessionEvent]:
        """F5: read the stage-2 artifact from the shared workdir (written by
        the stage-2 call in this trajectory) and append it as a predecessor
        event so the stage-3 successor receives it through its architecture.
        """
        fixture = get_fixture("A", condition)
        stage2 = fixture.stage_artifacts.get(2)
        if stage2 is None:
            return pred
        workdir = self.workdir / tid
        p = workdir / stage2.path
        if not p.exists():
            return pred
        content = p.read_text()
        tc = ToolCallReceipt(
            receipt_id=compute_sha256(f"{tid}-s2out")[:32],
            tool="write_file",
            params={"path": stage2.path, "content": content},
            status="ok",
            output="ok",
            evidence_refs=(),
            timestamp=2.0,
        )
        ev = SessionEvent(
            type="agent_message",
            session_id=f"{tid}-S2",
            trajectory_id=tid,
            arm="verified_state",
            condition=condition,
            worker_label="S2",
            stage=2,
            timestamp=2.0,
            tool_calls=(tc,),
        )
        return list(pred) + [ev]

    def _materialize_predecessor(self, workdir: Path, pred: list[SessionEvent], condition: str, current_stage: int = 3) -> None:
        """Write every fixture-declared predecessor artifact into the workdir.

        F1 repair (act §5): the fixture is the authority for what predecessor
        artifacts a trajectory requires. Materialize every stage artifact
        declared by the frozen fixture for stages STRICTLY BEFORE the current
        stage (the successor's own target stage must NOT be pre-written —
        that would contaminate the hidden-test score). Plus any explicit
        write_file events in the predecessor stream. Contents come from the
        frozen fixture — never invented.
        """
        fixture = get_fixture("A", condition)
        for stage, artifact in fixture.stage_artifacts.items():
            if stage >= current_stage:
                continue
            p = workdir / artifact.path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(artifact.content)
        # Also honor any explicit write_file events (e.g., injected state)
        for event in pred:
            for tc in event.tool_calls:
                if tc.tool == "write_file":
                    rel = str(tc.params.get("path", ""))
                    content = str(tc.params.get("content", ""))
                    if not rel:
                        continue
                    p = workdir / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content)

    def _materialize_stage2_output(self, workdir: Path, tid: str, arm: str, condition: str) -> None:
        """F5 repair: for interruption stage-3, the stage-2 artifact must be
        present as valid predecessor state. The stage-2 call in this same
        trajectory already wrote it to the shared workdir; if absent (e.g.,
        stage-2 call failed), materialize the frozen stage-2 fixture artifact
        so the stage-3 successor can resume from a valid predecessor.
        """
        fixture = get_fixture("A", condition)
        stage2 = fixture.stage_artifacts.get(2)
        if stage2 is None:
            return
        p = workdir / stage2.path
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(stage2.content)

    def _evaluate_hidden(self, workdir: Path, condition: str) -> dict[str, bool]:
        """Run hidden tests in a SEPARATE subprocess (isolation)."""
        cmd = [
            sys.executable,
            "-c",
            (
                "import json,sys;"
                "from benchmarks.vs1.fixtures import get_fixture;"
                "f=get_fixture('A', sys.argv[1]);"
                "print(json.dumps(f.run_hidden_test(sys.argv[2])))"
            ),
            condition,
            str(workdir),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return {}
        try:
            return json.loads(proc.stdout.strip())
        except Exception:
            return {}
