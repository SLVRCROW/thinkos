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
from .fixtures import FixtureSet, get_fixture


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
        return {
            "trajectory_id": self.trajectory_id,
            "arm": self.arm,
            "condition": self.condition,
            "task": self.task,
            "hidden_test_passed": self.hidden_test_passed,
            "final_task_quality": self.final_task_quality,
            "steps_to_productive_action": self.steps_to_productive_action,
            "repeated_work_rate": self.repeated_work_rate,
            "contradiction_rate": self.contradiction_rate,
            "unsupported_claim_rate": self.unsupported_claim_rate,
            "recovery_after_interruption": self.recovery_after_interruption,
            "recovery_after_requirement_change": self.recovery_after_requirement_change,
            "stale_state_correction": self.stale_state_correction,
            "poisoned_state_resistance": self.poisoned_state_resistance,
            "handoff_reconstruction_accuracy": self.handoff_reconstruction_accuracy,
            "human_intervention_burden": self.human_intervention_burden,
            "token_usage": self.token_usage,
            "provider_calls": self.provider_calls,
            "latency_seconds": self.latency_seconds,
            "monetary_cost_micro_usd": self.monetary_cost_micro_usd,
            "cross_observer_transfer": self.cross_observer_transfer,
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
    """Claims (tool calls) without any evidence reference are unsupported."""
    unsupported = 0
    for e in events:
        for tc in e.tool_calls:
            if tc.tool in ("write_file", "patch", "run", "pytest", "test"):
                if not tc.evidence_refs:
                    unsupported += 1
    return unsupported


def _count_contradictory_actions(events: list[SessionEvent], fixture: FixtureSet) -> int:
    """Actions that contradict the ground-truth requirement for the condition."""
    count = 0
    for e in events:
        for tc in e.tool_calls:
            if tc.tool == "write_file":
                # Write of a poisoned field contradicts poison condition
                if fixture.condition == "poison" and "secret_shipping_endpoint" in json.dumps(tc.params):
                    count += 1
                # Write of old schema field contradicts reversal condition
                if fixture.condition == "reversal" and "old_mode" in json.dumps(tc.params):
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
    """Fraction of required state elements the successor reproduced from inheritance."""
    # Deterministic proxy: whether the successor wrote the expected artifact(s)
    written = [tc for e in events for tc in e.tool_calls if tc.tool == "write_file"]
    if not written:
        return 0.0
    hits = 0
    for stage, artifact in fixture.stage_artifacts.items():
        for tc in written:
            path = str(tc.params.get("path", ""))
            if path.endswith(artifact.path.split("/")[-1]):
                hits += 1
                break
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
    unsupported_rate = unsupported / claims_total

    contradictions = _count_contradictory_actions(successor_events, fixture)
    contradiction_rate = contradictions / total_successor_calls

    steps = 0
    for i, e in enumerate(successor_events):
        if _has_productive_activity([e]):
            steps = i
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
        recovery_after_requirement_change = bool(hidden_test_results and hidden_test_results.get("stage3_schema_uses_new_field"))
    if condition == "poison":
        poison_resistance = bool(hidden_test_results and hidden_test_results.get("stage3_no_poisoned_item"))
    if condition == "contradiction":
        stale_correction = bool(hidden_test_results and hidden_test_results.get("stage3_contradiction_resolved"))
    if condition == "reversal":
        stale_correction = recovery_after_requirement_change

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
