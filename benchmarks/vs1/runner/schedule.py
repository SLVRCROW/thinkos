"""VS-1 execution schedule generator (mechanical, frozen).

Generates EXECUTION_SCHEDULE.json from the frozen topology:
  6 arms × 6 conditions × 3 replicates = 108 successor calls
  source baseline is deterministic fixture state (no model call)
  HARD_MAX_CALLS = 108, RETRIES = 0, REPLACEMENTS = 0

The schedule is hashed and frozen before launch. The executor must not
deviate from it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..schemas import ARMS, CONDITIONS
from ..schemas import compute_sha256, json_dumps


@dataclass(frozen=True)
class ScheduleCell:
    trajectory_id: str
    arm: str
    condition: str
    replicate: int
    stage: int
    prompt_id: str
    expected_call_id: str

    def to_json(self) -> dict:
        return {
            "trajectory_id": self.trajectory_id,
            "arm": self.arm,
            "condition": self.condition,
            "replicate": self.replicate,
            "stage": self.stage,
            "worker": "successor",
            "prompt_id": self.prompt_id,
            "expected_call_id": self.expected_call_id,
            "role": "successor-call",
        }


def build_schedule(
    replicates: int = 3,
    arms: tuple[str, ...] = ARMS,
    conditions: tuple[str, ...] = CONDITIONS,
    schedule_version: str = "v0.1.0",
) -> dict[str, Any]:
    """Generate the schedule mechanically from experiment semantics.

    F5 REPAIR (Marc act AUTHORIZE_VS1_R3...): interruption trajectories
    contain TWO successor calls (stage-2 then stage-3) belonging to ONE
    statistical trajectory. All other conditions: one call per trajectory.

    Expected topology: 6 arms × 6 conditions × 3 replicates = 108 trajectories;
    90 non-interruption × 1 call + 18 interruption × 2 calls = 126 provider calls.
    """
    cells = []
    for r in range(1, replicates + 1):
        for condition in conditions:
            for arm in arms:
                tid = f"r{r}-{condition}-{arm}"
                if condition == "interruption":
                    # Two calls, one trajectory (F5)
                    cells.append(ScheduleCell(
                        trajectory_id=tid,
                        arm=arm,
                        condition=condition,
                        replicate=r,
                        stage=2,
                        prompt_id=f"vs1-{schedule_version}-{arm}-{condition}-s2",
                        expected_call_id=f"call-{schedule_version}-{tid}-s2",
                    ))
                    cells.append(ScheduleCell(
                        trajectory_id=tid,
                        arm=arm,
                        condition=condition,
                        replicate=r,
                        stage=3,
                        prompt_id=f"vs1-{schedule_version}-{arm}-{condition}-s3",
                        expected_call_id=f"call-{schedule_version}-{tid}-s3",
                    ))
                else:
                    cells.append(ScheduleCell(
                        trajectory_id=tid,
                        arm=arm,
                        condition=condition,
                        replicate=r,
                        stage=3,
                        prompt_id=f"vs1-{schedule_version}-{arm}-{condition}-s3",
                        expected_call_id=f"call-{schedule_version}-{tid}",
                    ))
    schedule = {
        "schedule_version": schedule_version,
        "arms": list(arms),
        "conditions": list(conditions),
        "replicates": replicates,
        "trajectories": len({c.trajectory_id for c in cells}),
        "expected_calls": len(cells),
        "hard_max_calls": len(cells),
        "retries": 0,
        "replacements": 0,
        "cells": [c.to_json() for c in cells],
    }
    return schedule


def write_schedule(
    schedule: dict[str, Any],
    path: str | Path,
) -> Path:
    """Write + hash the frozen schedule. Returns the written path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json_dumps(schedule)
    p.write_text(text)
    manifest = {"schedule_file": p.name, "sha256": compute_sha256(text), "expected_calls": schedule["expected_calls"]}
    (p.parent / f"{p.name}.sha256.json").write_text(json_dumps(manifest))
    return p


def validate_schedule(schedule: dict[str, Any]) -> bool:
    """Validate schedule invariants.

    R3 topology (Marc act AUTHORIZE_VS1_R3...): 108 trajectories, 126 calls
    (90 non-interruption × 1 + 18 interruption × 2). Assert mechanically.
    """
    cells = schedule.get("cells", [])
    ids = [c["expected_call_id"] for c in cells]
    if len(ids) != len(set(ids)):
        return False
    trajectories = {c["trajectory_id"] for c in cells}
    if len(trajectories) != schedule.get("trajectories"):
        return False
    if len(cells) != schedule.get("expected_calls"):
        return False
    if schedule.get("hard_max_calls") != schedule.get("expected_calls"):
        return False
    if schedule.get("retries") != 0 or schedule.get("replacements") != 0:
        return False
    # R3 topology assertions: 108 trajectories, 126 calls
    if schedule.get("trajectories") != 108:
        return False
    if schedule.get("expected_calls") != 126:
        return False
    # Every interruption trajectory must have exactly 2 calls (s2 + s3)
    from collections import Counter
    call_counts = Counter(c["trajectory_id"] for c in cells)
    for tid, n in call_counts.items():
        if tid.split("-", 2)[1] == "interruption" and n != 2:
            return False
        if tid.split("-", 2)[1] != "interruption" and n != 1:
            return False
    return True
