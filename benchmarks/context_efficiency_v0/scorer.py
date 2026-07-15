"""Deterministic scorer for the benchmark harness.

Scores observable behavior without inspecting chain-of-thought.
Worker B is scored against stage 2 (predecessor stage 1).
Worker C against stage 3 (predecessor stage 2).
Worker D against stage 4 (predecessor stage 3).

No credit is awarded without productive successor activity and a valid
stage checkpoint. A completely inactive trajectory scores zero.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any

from .schemas import SessionEvent, ToolCallReceipt, CheckpointReceipt
from .fixtures import FixtureSet


@dataclass(frozen=True)
class TrajectoryScore:
    trajectory_id: str
    architecture: str
    task: str
    condition: str
    continuation_correctness: tuple[float, ...] = ()
    task_correctness: float = 0.0
    stage_invariants_preserved: float = 0.0
    completed_work_repeated: float = 0.0
    required_work_skipped: float = 0.0
    acceptance_test_progression: float = 0.0
    evidence_backed_transitions: float = 0.0
    stale_state_errors: int = 0
    total_tokens: int = 0
    recovery_tokens: int = 0
    human_interventions: int = 0
    authority_violations: int = 0
    normalized_total: float = 0.0

    def to_json(self) -> dict:
        return {
            "trajectory_id": self.trajectory_id,
            "architecture": self.architecture,
            "task": self.task,
            "condition": self.condition,
            "continuation_correctness": list(self.continuation_correctness),
            "task_correctness": self.task_correctness,
            "stage_invariants_preserved": self.stage_invariants_preserved,
            "completed_work_repeated": self.completed_work_repeated,
            "required_work_skipped": self.required_work_skipped,
            "acceptance_test_progression": self.acceptance_test_progression,
            "evidence_backed_transitions": self.evidence_backed_transitions,
            "stale_state_errors": self.stale_state_errors,
            "total_tokens": self.total_tokens,
            "recovery_tokens": self.recovery_tokens,
            "human_interventions": self.human_interventions,
            "authority_violations": self.authority_violations,
            "normalized_total": self.normalized_total,
        }


def _find_checkpoint(events: list[SessionEvent], stage: int) -> CheckpointReceipt | None:
    for event in events:
        if event.checkpoint and event.checkpoint.stage_number == stage:
            return event.checkpoint
    return None


def _has_productive_activity(events: list[SessionEvent]) -> bool:
    """A successor has productive activity if it has any tool calls beyond exploration."""
    for event in events:
        for tc in event.tool_calls:
            if tc.tool not in ("ls", "pwd", "cat", "echo", "which"):
                return True
    return False


def _count_repeated_calls(
    predecessor_events: list[SessionEvent],
    successor_events: list[SessionEvent],
) -> int:
    predecessor_calls: set[tuple[str, str]] = set()
    for event in predecessor_events:
        for tc in event.tool_calls:
            if tc.status == "ok":
                params_str = json.dumps(tc.params, sort_keys=True)
                predecessor_calls.add((tc.tool, params_str))
    repeated = 0
    for event in successor_events:
        for tc in event.tool_calls:
            if tc.status == "ok":
                params_str = json.dumps(tc.params, sort_keys=True)
                if (tc.tool, params_str) in predecessor_calls:
                    repeated += 1
    return repeated


def _count_stale_state_errors(
    events: list[SessionEvent],
    fixture: FixtureSet,
    condition: str,
) -> int:
    """Count actions on state that contradicts the current environment.

    In drift condition, if a successor reads a file using a path or format
    from the clean condition, that is a stale-state error.
    """
    errors = 0
    for event in events:
        for tc in event.tool_calls:
            if tc.tool == "read_file" and condition == "drift":
                path = tc.params.get("path", "")
                clean_fixture = None
                try:
                    from .fixtures import get_fixture
                    clean_fixture = get_fixture(
                        fixture.task.value if hasattr(fixture.task, 'value') else fixture.task,
                        "clean"
                    )
                except Exception:
                    pass
                if clean_fixture and path in clean_fixture.input_files:
                    if path not in fixture.input_files:
                        errors += 1
                    elif fixture.input_files[path] != clean_fixture.input_files[path]:
                        if tc.output and tc.output == clean_fixture.input_files.get(path, ""):
                            errors += 1
    return errors


def score_trajectory(
    trajectory_id: str,
    architecture: str,
    task: str,
    condition: str,
    sessions: dict[str, list[SessionEvent]],
    fixture: FixtureSet,
) -> TrajectoryScore:
    workers = ["A", "B", "C", "D"]
    continuation_scores: list[float] = []

    total_stage_invariants = 0.0
    total_repeated = 0.0
    total_skipped = 0.0
    total_acceptance = 0.0
    total_evidence = 0.0
    total_stale_errors = 0
    total_tokens = 0
    total_recovery_tokens = 0
    total_interventions = 0
    total_authority = 0

    for i, worker in enumerate(workers):
        events = sessions.get(worker, [])
        if not events:
            continue

        for event in events:
            tc = event.metadata.get("token_count", 0)
            if isinstance(tc, (int, float)):
                total_tokens += int(tc)

        if i == 0:
            continue  # Worker A — no continuation score

        successor_stage = i + 1
        predecessor_stage = i
        predecessor = workers[i - 1]
        predecessor_events = sessions.get(predecessor, [])

        checkpoint = _find_checkpoint(events, successor_stage)
        predecessor_checkpoint = _find_checkpoint(predecessor_events, predecessor_stage)

        # No credit without productive successor activity AND a valid stage checkpoint
        has_activity = _has_productive_activity(events)
        has_valid_checkpoint = checkpoint is not None and predecessor_checkpoint is not None

        if not has_activity or not has_valid_checkpoint:
            # Inactive or checkpointless successor scores zero for this turnover
            continuation_scores.append(0.0)
            total_stale_errors += _count_stale_state_errors(events, fixture, condition)
            continue

        # Stage invariants preserved (0-3)
        invariants = 0.0
        if checkpoint.artifact_sha256:
            invariants += 1.5
        if checkpoint.test_results and all(checkpoint.test_results.values()):
            invariants += 1.5
        total_stage_invariants += invariants

        # Completed work repeated (0-3, inverted)
        repeated = _count_repeated_calls(predecessor_events, events)
        if repeated == 0:
            repeated_score = 3.0
        elif repeated <= 2:
            repeated_score = 2.0
        elif repeated <= 5:
            repeated_score = 1.0
        else:
            repeated_score = 0.0
        total_repeated += repeated_score

        # Required work skipped (0-2, inverted)
        skipped = 0.0
        if checkpoint.test_results:
            passed = sum(1 for v in checkpoint.test_results.values() if v)
            total_t = len(checkpoint.test_results)
            if total_t > 0:
                skipped = 2.0 * (passed / total_t)
        total_skipped += skipped

        # Acceptance test progression (0-2)
        acceptance = 0.0
        if checkpoint.test_results:
            if all(checkpoint.test_results.values()):
                acceptance = 2.0
            elif any(checkpoint.test_results.values()):
                acceptance = 1.0
        total_acceptance += acceptance

        # Evidence-backed transitions (0-2 bonus)
        evidence = 0.0
        for event in events:
            for tc in event.tool_calls:
                if tc.evidence_refs:
                    evidence += 0.5
        evidence = min(evidence, 2.0)
        total_evidence += evidence

        raw = invariants + repeated_score + skipped + acceptance + evidence
        continuation_scores.append(min(raw / 12.0 * 10.0, 10.0))

        # Stale state errors
        total_stale_errors += _count_stale_state_errors(events, fixture, condition)

        # Recovery tokens
        for event in events:
            for tc in event.tool_calls:
                if tc.tool not in ("ls", "pwd", "cat", "echo", "which"):
                    total_recovery_tokens += len(json.dumps(tc.params, sort_keys=True)) // 4
                    break
            else:
                continue
            break

    # Task correctness (0-10) — gated on productive final-worker activity + valid checkpoint
    final_events = sessions.get("D", [])
    final_checkpoint = _find_checkpoint(final_events, 4)
    task_correctness = 0.0
    if _has_productive_activity(final_events) and final_checkpoint and final_checkpoint.test_results:
        if all(final_checkpoint.test_results.values()):
            task_correctness = 10.0
        else:
            passed = sum(1 for v in final_checkpoint.test_results.values() if v)
            total = len(final_checkpoint.test_results)
            task_correctness = 10.0 * (passed / total)

    raw_total = total_stage_invariants + total_repeated + total_skipped + total_acceptance + total_evidence
    max_raw = (3 + 3 + 2 + 2 + 2) * 3
    normalized = min(raw_total / max_raw * 10.0, 10.0) if max_raw > 0 else 0.0

    return TrajectoryScore(
        trajectory_id=trajectory_id,
        architecture=architecture,
        task=task,
        condition=condition,
        continuation_correctness=tuple(continuation_scores),
        task_correctness=task_correctness,
        stage_invariants_preserved=total_stage_invariants,
        completed_work_repeated=total_repeated,
        required_work_skipped=total_skipped,
        acceptance_test_progression=total_acceptance,
        evidence_backed_transitions=total_evidence,
        stale_state_errors=total_stale_errors,
        total_tokens=total_tokens,
        recovery_tokens=total_recovery_tokens,
        human_interventions=total_interventions,
        authority_violations=total_authority,
        normalized_total=normalized,
    )


def canonicalize_summary(summary: dict) -> dict:
    """Normalize only the documented variable fields in a G0 summary.

    output_dir is variable. All other fields (gates, scores, counts, hashes)
    are preserved as-is.
    """
    result = dict(summary)
    result.pop("output_dir", None)
    return result


def score_observable(events: list[SessionEvent]) -> dict:
    checkpoint_count = 0
    behavioral_checks_passed = 0
    for event in events:
        if event.checkpoint:
            checkpoint_count += 1
            if event.checkpoint.test_results:
                behavioral_checks_passed += sum(1 for v in event.checkpoint.test_results.values() if v)
    return {
        "checkpoint_count": checkpoint_count,
        "behavioral_checks_passed": behavioral_checks_passed,
    }
