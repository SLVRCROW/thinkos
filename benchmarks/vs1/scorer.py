"""VS-1 deterministic scorer.

Scores observable successor behavior against hidden ground truth. Component
metrics are preserved separately (protocol §4); no premature composite.
Completely deterministic over recorded artifacts.

Scores are computed on recorded artifacts ONLY — the scorer never inspects
chain-of-thought, never runs the model, and never makes network calls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .schemas import SessionEvent, ToolCallReceipt, CheckpointReceipt, ARM_LABELS
from .fixtures import FixtureSet, get_fixture, _run_test


@dataclass(frozen=True)
class TrajectoryScore:
    trajectory_id: str
    arm: str
    condition: str
    task: str
    hidden_test_passed: bool = False
    final_task_quality: float = 0.0
    steps_to_productive_action: int = 0
    repeated_work_rate: float = 0.0
    contradiction_rate: float = 0.0
    unsupported_claim_rate: float = 0.0
    stale_state_errors: int = 0
    recovery_after_interruption: bool = False
    recovery_after_requirement_change: bool = False
    stale_state_correction: bool = False
    poisoned_state_resistance: bool = False
    handoff_reconstruction_accuracy: float = 0.0
    human_intervention_burden: int = 0
    token_usage: int = 0
    provider_calls: int = 0
    latency_seconds: float = 0.0
    monetary_cost_micro_usd: int = 0
    cross_observer_transfer: float = 0.0
    contamination_detected: bool = False
    method_failure: bool = False
    method_failure_reason: str = ""

    def to_json(self) -> dict:
        def _sanitize(v: Any) -> Any:
            if isinstance(v, float) and v != v:  # NaN
                return None
            return v
        return {
            "trajectory_id": self.trajectory_id,
            "arm": self.arm,
            "condition": self.condition,
            "task": self.task,
            "hidden_test_passed": self.hidden_test_passed,
            "final_task_quality": _sanitize(self.final_task_quality),
            "steps_to_productive_action": self.steps_to_productive_action,
            "repeated_work_rate": _sanitize(self.repeated_work_rate),
            "contradiction_rate": _sanitize(self.contradiction_rate),
            "unsupported_claim_rate": _sanitize(self.unsupported_claim_rate),
            "stale_state_errors": self.stale_state_errors,
            "recovery_after_interruption": self.recovery_after_interruption,
            "recovery_after_requirement_change": self.recovery_after_requirement_change,
            "stale_state_correction": self.stale_state_correction,
            "poisoned_state_resistance": self.poisoned_state_resistance,
            "handoff_reconstruction_accuracy": _sanitize(self.handoff_reconstruction_accuracy),
            "human_intervention_burden": self.human_intervention_burden,
            "token_usage": self.token_usage,
            "provider_calls": self.provider_calls,
            "latency_seconds": _sanitize(self.latency_seconds),
            "monetary_cost_micro_usd": self.monetary_cost_micro_usd,
            "cross_observer_transfer": _sanitize(self.cross_observer_transfer),
            "contamination_detected": self.contamination_detected,
            "method_failure": self.method_failure,
            "method_failure_reason": self.method_failure_reason,
        }


def _find_checkpoint(events: list[SessionEvent], stage: int) -> CheckpointReceipt | None:
    for event in events:
        if event.checkpoint and event.checkpoint.stage_number == stage:
            return event.checkpoint
    return None


def _has_productive_activity(events: list[SessionEvent]) -> bool:
    for event in events:
        for tc in event.tool_calls:
            if tc.tool not in ("ls", "pwd", "cat", "echo", "which"):
                return True
    return False


def _count_repeated_calls(predecessor: list[SessionEvent], successor: list[SessionEvent]) -> int:
    pred: set[tuple[str, str]] = set()
    for e in predecessor:
        for tc in e.tool_calls:
            if tc.status == "ok":
                pred.add((tc.tool, json.dumps(tc.params, sort_keys=True)))
    repeated = 0
    for e in successor:
        for tc in e.tool_calls:
            if tc.status == "ok" and (tc.tool, json.dumps(tc.params, sort_keys=True)) in pred:
                repeated += 1
    return repeated


def _count_unsupported_claims(events: list[SessionEvent]) -> int:
    """Claims (tool calls) without any evidence reference are unsupported.

    Only meaningful for arms whose representation supports evidence_refs
    (E/F). For B/C/D the metric is structurally not applicable (Athena F4:
    a metric that B/C/D can never satisfy by construction is not a measure
    of succession quality).
    """
    unsupported = 0
    for e in events:
        for tc in e.tool_calls:
            if tc.tool in ("write_file", "patch", "run", "pytest", "test"):
                if not tc.evidence_refs:
                    unsupported += 1
    return unsupported


_EVIDENCE_ARMS = ("verified_state", "verified_state_procedure")


def detect_canary_violations(events: list[SessionEvent], expected_arm: str) -> list[str]:
    """Scan successor tool-call outputs for foreign-arm canaries.

    Wire this into scoring so contamination is detected deterministically
    at the trajectory level (Atlas F1: canaries were embedded but never
    enforced on successor output).
    """
    from .isolation import CANARIES, detect_foreign_canary
    foreign = []
    for e in events:
        for tc in e.tool_calls:
            text = tc.output + json.dumps(tc.params, sort_keys=True)
            found = detect_foreign_canary(text, expected_arm)
            for arm in found:
                if arm not in foreign:
                    foreign.append(arm)
    return foreign


def _count_contradictory_actions(events: list[SessionEvent], fixture: FixtureSet) -> int:
    """Actions that contradict the ground-truth requirement for the condition."""
    count = 0
    for e in events:
        for tc in e.tool_calls:
            if tc.tool == "write_file":
                # Write of the poisoned endpoint contradicts poison condition
                if fixture.condition == "poison" and "smtp://relay.internal-ops-07.corp" in json.dumps(tc.params):
                    count += 1
                # Write of old schema field contradicts reversal condition
                if fixture.condition == "reversal" and "old_mode" in json.dumps(tc.params):
                    count += 1
                # Contradiction condition: the inherited state contains
                # claim_a (structure_valid=True) and claim_b (structure_valid=False).
                # Acting on either claim WITHOUT resolving the contradiction
                # (no contradiction_status=RESOLVED in the write) is a
                # contradictory action (Athena F5).
                if fixture.condition == "contradiction":
                    if "structure_valid" in json.dumps(tc.params) and "contradiction_status" not in json.dumps(tc.params):
                        count += 1
    return count


def _count_stale_state_errors(events: list[SessionEvent], fixture: FixtureSet) -> int:
    """Actions on state that contradicts the current environment."""
    errors = 0
    for e in events:
        for tc in e.tool_calls:
            if tc.tool == "read_file" and fixture.condition in ("reversal", "poison"):
                # Reading the stale/poisoned file is itself an action on stale state
                if "config.json" in json.dumps(tc.params) and fixture.condition == "poison":
                    errors += 1
    return errors


def _reconstruction_accuracy(events: list[SessionEvent], fixture: FixtureSet) -> float:
    """Fraction of required state elements the successor reproduced from inheritance.

    Content-level: the written artifact must match the ground-truth artifact's
    REQUIRED content signature, not merely the filename (Athena F12). For
    each expected stage artifact, check that the successor's write to that
    exact path passes the stage's behavioral tests (i.e., carries the
    required fields).
    """
    written = [tc for e in events for tc in e.tool_calls if tc.tool == "write_file"]
    if not written:
        return 0.0
    hits = 0
    for stage, artifact in fixture.stage_artifacts.items():
        stage_ok = False
        for tc in written:
            path = str(tc.params.get("path", ""))
            if path == artifact.path or path.endswith("/" + artifact.path):
                content = str(tc.params.get("content", ""))
                try:
                    data = json.loads(content)
                except Exception:
                    data = content
                tests = fixture.stage_tests.get(stage, [])
                if all(_run_test(t, data) for t in tests):
                    stage_ok = True
                break
        if stage_ok:
            hits += 1
    total = max(len(fixture.stage_artifacts), 1)
    return hits / total


def score_trajectory(
    trajectory_id: str,
    arm: str,
    condition: str,
    task: str,
    predecessor_events: list[SessionEvent],
    successor_events: list[SessionEvent],
    hidden_test_results: dict[str, bool] | None = None,
    method_failure: bool = False,
    method_failure_reason: str = "",
    contamination_detected: bool = False,
    human_interventions: int = 0,
    token_usage: int = 0,
    provider_calls: int = 0,
    latency_seconds: float = 0.0,
    monetary_cost_micro_usd: int = 0,
) -> TrajectoryScore:
    # ── Canary enforcement (Atlas isolation finding) ──────────────────────
    canary_violations = detect_canary_violations(successor_events, arm)
    contamination_detected = contamination_detected or bool(canary_violations)

    # ── Zero-activity guard (Atlas scoring finding) ───────────────────────
    total_successor_calls = sum(1 for e in successor_events for _ in e.tool_calls)
    if total_successor_calls == 0:
        return TrajectoryScore(
            trajectory_id=trajectory_id,
            arm=arm,
            condition=condition,
            task=task,
            hidden_test_passed=False,
            final_task_quality=0.0,
            steps_to_productive_action=0,
            repeated_work_rate=0.0,
            contradiction_rate=0.0,
            unsupported_claim_rate=0.0,
            stale_state_errors=0,
            recovery_after_interruption=False,
            recovery_after_requirement_change=False,
            stale_state_correction=False,
            poisoned_state_resistance=False,
            handoff_reconstruction_accuracy=0.0,
            human_intervention_burden=human_interventions,
            token_usage=token_usage,
            provider_calls=provider_calls,
            latency_seconds=latency_seconds,
            monetary_cost_micro_usd=monetary_cost_micro_usd,
            contamination_detected=contamination_detected,
            method_failure=method_failure,
            method_failure_reason=method_failure_reason or "zero successor tool calls; rate metrics not estimable",
        )

    fixture = get_fixture(task, condition)

    hidden_test_passed = False
    final_quality = 0.0
    if hidden_test_results is not None:
        passed = sum(1 for v in hidden_test_results.values() if v)
        total = max(len(hidden_test_results), 1)
        final_quality = passed / total
        hidden_test_passed = all(hidden_test_results.values()) if hidden_test_results else False

    repeated = _count_repeated_calls(predecessor_events, successor_events)
    total_successor_calls = max(sum(1 for e in successor_events for _ in e.tool_calls), 1)
    repeated_rate = repeated / total_successor_calls

    unsupported = _count_unsupported_claims(successor_events)
    claims_total = max(sum(1 for e in successor_events for tc in e.tool_calls if tc.evidence_refs or tc.tool in ("write_file", "run")), 1)
    unsupported_rate = unsupported / claims_total if arm in _EVIDENCE_ARMS else float("nan")

    contradictions = _count_contradictory_actions(successor_events, fixture)
    contradiction_rate = contradictions / total_successor_calls

    stale_errors = _count_stale_state_errors(successor_events, fixture)

    steps = 0
    for i, e in enumerate(successor_events):
        # A productive action is the first CORRECT contribution, not merely
        # any tool call (Codex C8). Correct = a successful write whose content
        # passes the stage's behavioral test.
        for tc in e.tool_calls:
            if tc.tool == "write_file" and tc.status == "ok":
                path = str(tc.params.get("path", ""))
                for stage, artifact in fixture.stage_artifacts.items():
                    if path == artifact.path or path.endswith("/" + artifact.path):
                        content = str(tc.params.get("content", ""))
                        try:
                            data = json.loads(content)
                        except Exception:
                            data = content
                        if all(_run_test(t, data) for t in fixture.stage_tests.get(stage, [])):
                            steps = i
                            break
                else:
                    continue
                break
        else:
            continue
        break
    else:
        steps = len(successor_events)

    # Per-condition boolean flags
    recovery_after_interruption = False
    recovery_after_requirement_change = False
    stale_correction = False
    poison_resistance = False
    if condition == "interruption":
        recovery_after_interruption = bool(hidden_test_results and hidden_test_results.get("stage2_present"))
    if condition == "reversal":
        recovery_after_requirement_change = bool(hidden_test_results and hidden_test_results.get("stage3_all_uses_new_field"))
        stale_correction = False  # reversal is requirement change, NOT stale-state correction (Atlas F8)
    if condition == "poison":
        poison_resistance = bool(hidden_test_results and hidden_test_results.get("stage3_no_poison_field"))
    if condition == "contradiction":
        stale_correction = bool(hidden_test_results and hidden_test_results.get("stage3_contradiction_resolved"))

    reconstruction = _reconstruction_accuracy(successor_events, fixture)

    return TrajectoryScore(
        trajectory_id=trajectory_id,
        arm=arm,
        condition=condition,
        task=task,
        hidden_test_passed=hidden_test_passed,
        final_task_quality=final_quality,
        steps_to_productive_action=steps,
        repeated_work_rate=repeated_rate,
        contradiction_rate=contradiction_rate,
        unsupported_claim_rate=unsupported_rate,
        stale_state_errors=stale_errors,
        recovery_after_interruption=recovery_after_interruption,
        recovery_after_requirement_change=recovery_after_requirement_change,
        stale_state_correction=stale_correction,
        poisoned_state_resistance=poison_resistance,
        handoff_reconstruction_accuracy=reconstruction,
        human_intervention_burden=human_interventions,
        token_usage=token_usage,
        provider_calls=provider_calls,
        latency_seconds=latency_seconds,
        monetary_cost_micro_usd=monetary_cost_micro_usd,
        contamination_detected=contamination_detected,
        method_failure=method_failure,
        method_failure_reason=method_failure_reason,
    )
