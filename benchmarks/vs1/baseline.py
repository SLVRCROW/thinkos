"""VS-1 synthetic successor simulation.

Deterministic, no-model successor used by the instrumentation pilot to prove
the measurement chassis end-to-end: adapters → isolation → canaries →
scoring → accounting → evidence → reconstruction.

A synthetic successor consumes the inherited state and produces a stage
artifact per the frozen condition's ground truth, subject to a deterministic
"capability" parameter. This is NOT the powered run — it validates the
instrumentation, not the thesis.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fixtures import get_fixture
from .schemas import (
    CheckpointReceipt,
    EvidenceReference,
    SessionEvent,
    ToolCallReceipt,
    compute_sha256,
    make_receipt_id,
)


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

    capability ∈ [0,1] is the probability the synthetic successor produces
    the condition-correct artifact. Deterministic via seeded hash.
    """
    import random
    rng = random.Random(seed)

    fixture = get_fixture(task, condition)
    fixture.write_inputs(workdir)

    events: list[SessionEvent] = []
    # Stage 2..4 succession (Worker B/C/D semantics)
    for stage in (2, 3, 4):
        correct = rng.random() < capability
        if correct:
            artifact = fixture.stage_artifacts.get(stage)
            if artifact is None:
                artifact = fixture.stage_artifacts.get(1)
            content = artifact.content
            path = artifact.path
        else:
            content = fixture.bad_artifacts.get(stage, "{}")
            path = fixture.stage_artifacts.get(stage, fixture.stage_artifacts[1]).path

        # Materialize the artifact on disk (the successor's write must be real
        # for the hidden test to see it).
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
