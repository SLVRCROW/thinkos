"""VS-1 synthetic successor simulation.

Deterministic, no-model successor used by the instrumentation pilot to prove
the measurement chassis end-to-end: adapters -> isolation -> canaries ->
scoring -> accounting -> evidence -> reconstruction.

The synthetic successor CONSUMES the inherited adapter state: its behavior
depends on declared, inspectable features of the state (Codex C6). Paired
arms within a task/condition/replicate share the same exogenous seed so that
outcome differences are attributable to the adapter state, not arbitrary
arm-keyed randomness. This is NOT the powered run — it validates the
instrumentation, not the thesis.

WARNING: this synthetic successor may access ground-truth artifacts to
emulate capability. It MUST NOT be used in the powered run.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from .fixtures import get_fixture, inject_predecessor_state
from .schemas import (
    CheckpointReceipt,
    EvidenceReference,
    SessionEvent,
    ToolCallReceipt,
    compute_sha256,
    make_receipt_id,
)


def _consume_adapter_state(arm: str, state: Any) -> dict[str, Any]:
    """Extract the declared, inspectable features that shape successor behavior.

    Every arm's state must expose enough to drive behavior deterministically:
    - stateless: no content -> successor starts blind
    - transcript: n_events + presence of injected claims
    - summary: completed stages
    - retrieval: top-k result count
    - verified_state: verified_count, constraints, exact_next_action
    - verified_state_procedure: claims + procedures + failure conditions
    """
    if state is None:
        return {}
    if isinstance(state, dict):
        return state
    if hasattr(state, "content") and isinstance(state.content, dict):
        return state.content
    if hasattr(state, "to_json"):
        return state.to_json()
    return {}


def synthetic_successor(
    trajectory_id: str,
    arm: str,
    condition: str,
    task: str,
    adapter_state: Any,
    workdir: Path,
    capability: float = 0.7,
    seed: int = 0,
) -> list[SessionEvent]:
    """Produce successor events for the instrumentation pilot.

    Behavior depends on adapter_state features (Codex C6). The same exogenous
    seed yields the same random draws for a given task/condition/replicate;
    arm differences arise from state consumption, not from arm-keyed seeds.
    """
    rng = random.Random(seed)
    inherited = _consume_adapter_state(arm, adapter_state)

    # Declared feature extraction from the state.
    has_claims = bool(inherited.get("claims") or inherited.get("verified_count"))
    has_constraints = bool(inherited.get("constraints"))
    has_next_action = bool(inherited.get("exact_next_action"))
    has_procedures = bool(inherited.get("procedures"))
    n_inherited_events = int(inherited.get("n_events", 0))
    n_stages = int(inherited.get("n_stages", 0))
    if arm == "transcript":
        n_inherited_events = int(inherited.get("events", []) and len(inherited.get("events", [])))
    if arm == "retrieval":
        n_inherited_events = int(len(inherited.get("results", [])))

    # State benefit model (deterministic; used only to make the pilot able to
    # detect an arm effect — NOT a treatment-effect estimate).
    state_benefit = 0.0
    if arm in ("verified_state", "verified_state_procedure"):
        state_benefit = 0.08
        if has_constraints:
            state_benefit += 0.04
        if has_next_action:
            state_benefit += 0.04
        if arm == "verified_state_procedure" and has_procedures:
            state_benefit += 0.06
    elif arm == "summary":
        state_benefit = 0.02 if n_stages > 0 else 0.0
    elif arm == "retrieval":
        state_benefit = 0.03 if n_inherited_events > 0 else 0.0
    elif arm == "transcript":
        state_benefit = 0.01 if n_inherited_events > 0 else 0.0

    effective_cap = min(0.98, max(0.05, capability + state_benefit))

    fixture = get_fixture(task, condition)
    fixture.write_inputs(workdir)

    # Materialize the predecessor stage-1 artifact first (Codex C7): the
    # successor resumes FROM it, not through a phantom stage-4 fallback.
    if 1 in fixture.stage_artifacts:
        pred = fixture.stage_artifacts[1]
        p = workdir / pred.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(pred.content)

    events: list[SessionEvent] = []
    # Successor stages are the fixture's real remaining stages (2..max).
    max_stage = max(fixture.stage_artifacts.keys())
    for stage in range(2, max_stage + 1):
        artifact = fixture.stage_artifacts.get(stage)
        if artifact is None:
            # No declared stage: reject rather than silently fall back (C7).
            raise ValueError(f"Undefined stage {stage} for condition {condition}")
        correct = rng.random() < effective_cap
        if correct:
            content = artifact.content
            path = artifact.path
        else:
            content = fixture.bad_artifacts.get(stage, "{}")
            path = artifact.path

        target = workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

        receipt_id = make_receipt_id("vs1", trajectory_id, chr(64 + stage), stage)
        tc = ToolCallReceipt(
            receipt_id=receipt_id,
            tool="write_file",
            params={"path": path, "content": content},
            status="ok",
            output=f"wrote {path}",
            evidence_refs=(
                (EvidenceReference(receipt_id=receipt_id, claim_type="artifact", claim_value=content[:64]),)
                if arm in ("verified_state", "verified_state_procedure")
                else ()
            ),
            timestamp=float(stage),
        )
        events.append(
            SessionEvent(
                type="agent_message",
                session_id=f"{trajectory_id}-{chr(64+stage)}",
                trajectory_id=trajectory_id,
                arm=arm,
                condition=condition,
                worker_label=chr(64 + stage),
                stage=stage,
                timestamp=float(stage),
                tool_calls=(tc,),
            )
        )
    return events
