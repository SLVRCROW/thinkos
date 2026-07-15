"""Shared Worker-A baseline for the benchmark harness."""
from __future__ import annotations
import json
import time
from typing import Any, NamedTuple
from pathlib import Path

from .fixtures import FixtureSet, Task, Condition, get_fixture, all_fixtures
from .schemas import (
    TrajectoryID, SessionID, SessionEvent, ToolCallReceipt,
    CheckpointReceipt, EvidenceReference, compute_sha256, make_receipt_id,
    serialize_jsonl,
)
from .checkpoint import evaluate_checkpoint


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def generate_worker_a_checkpoint(
    task: Task | str,
    condition: Condition | str,
    replicate: int,
    base_dir: str | Path,
    trajectory_id: str = "unknown",
) -> tuple[CheckpointReceipt, Path]:
    t = task.value if isinstance(task, Task) else task
    c = condition.value if isinstance(condition, Condition) else condition
    fixture = get_fixture(t, c)
    workdir = Path(base_dir) / f"worker_a_{t}_{c}_r{replicate}"
    workdir.mkdir(parents=True, exist_ok=True)
    fixture.write_inputs(workdir)
    fixture.write_artifact(1, fixture.stage_artifacts[1].content, workdir)
    receipt = evaluate_checkpoint(
        stage=1, artifact_dir=workdir, fixture=fixture,
        worker_label="A", session_token_count=5000,
        trajectory_id=trajectory_id,
    )
    if receipt is None:
        raise RuntimeError(f"Worker-A checkpoint failed for {t}/{c}/r{replicate}")
    return receipt, workdir


def clone_checkpoint_to_architecture(
    source_receipt: CheckpointReceipt,
    source_dir: Path,
    architecture: str,
    target_base: str | Path,
    task: str = "",
    condition: str = "",
    replicate: int = 0,
) -> Path:
    """Clone a Worker-A checkpoint into a distinct architecture arm directory.

    Before cloning, verifies:
    - source artifact is contained within source_dir
    - source content SHA equals source_receipt.artifact_sha256
    - destination content SHA equals the same receipt SHA
    Fails closed on mismatch.
    """
    # Verify source artifact is contained within source_dir
    src_artifact = Path(source_receipt.artifact_path).resolve()
    src_resolved = source_dir.resolve()
    if not _is_subpath(src_artifact, src_resolved):
        raise RuntimeError(
            f"Source artifact {src_artifact} is not within source_dir {src_resolved}"
        )

    # Verify source content SHA matches receipt
    if not src_artifact.exists():
        raise RuntimeError(f"Source artifact {src_artifact} does not exist")
    src_content = src_artifact.read_text()
    src_sha = compute_sha256(src_content)
    if src_sha != source_receipt.artifact_sha256:
        raise RuntimeError(
            f"Source SHA mismatch: computed {src_sha}, receipt {source_receipt.artifact_sha256}"
        )

    # Create destination
    target_dir = Path(target_base) / f"{task}_{condition}_{architecture}_r{replicate}"
    target_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = target_dir / "stage_1"
    stage_dir.mkdir(parents=True, exist_ok=True)
    dest = stage_dir / src_artifact.name
    dest.write_text(src_content)

    # Verify destination SHA matches receipt
    dest_sha = compute_sha256(dest.read_text())
    if dest_sha != source_receipt.artifact_sha256:
        raise RuntimeError(
            f"Destination SHA mismatch: computed {dest_sha}, receipt {source_receipt.artifact_sha256}"
        )

    return target_dir


def generate_all_baselines(
    base_dir: str | Path,
    tasks: list[str] | None = None,
    conditions: list[str] | None = None,
    replicates: list[int] | None = None,
) -> dict[tuple[str, str, int], tuple[CheckpointReceipt, Path]]:
    if tasks is None:
        tasks = ["A", "B", "C"]
    if conditions is None:
        conditions = ["clean", "drift"]
    if replicates is None:
        replicates = [0, 1, 2]
    baselines: dict[tuple[str, str, int], tuple[CheckpointReceipt, Path]] = {}
    for t in tasks:
        for c in conditions:
            for r in replicates:
                tid = f"{t}_{c}_r{r}"
                receipt, workdir = generate_worker_a_checkpoint(t, c, r, base_dir, trajectory_id=tid)
                baselines[(t, c, r)] = (receipt, workdir)
    return baselines


def clone_all_architectures(
    baselines: dict[tuple[str, str, int], tuple[CheckpointReceipt, Path]],
    architectures: list[str],
    target_base: str | Path,
) -> dict[tuple[str, str, str, int], Path]:
    result: dict[tuple[str, str, str, int], Path] = {}
    for (t, c, r), (receipt, workdir) in baselines.items():
        for arch in architectures:
            target = clone_checkpoint_to_architecture(
                receipt, workdir, arch, target_base,
                task=t, condition=c, replicate=r,
            )
            result[(t, c, arch, r)] = target
    return result


def accounting_summary(
    baselines: dict[tuple[str, str, int], tuple[CheckpointReceipt, Path]],
    clones: dict[tuple[str, str, str, int], Path],
    architectures: list[str],
) -> dict:
    """Derive accounting from actual baselines, clones, and architectures.

    Validates that clones == baselines × architectures.
    """
    unique_worker_a = len(baselines)
    total_clones = len(clones)
    expected_clones = unique_worker_a * len(architectures)
    if total_clones != expected_clones:
        raise RuntimeError(
            f"Clone count mismatch: {total_clones} != {unique_worker_a} × {len(architectures)} = {expected_clones}"
        )

    logical_trajectories = unique_worker_a * len(architectures)

    # Derive pilot from actual data
    pilot_baselines = 0
    for (t, c, r) in baselines:
        if t == "A" and c == "clean" and r in (0, 1):
            pilot_baselines += 1

    pilot_architectures = [a for a in architectures if a in ("stateless", "summary", "verified_state")]
    pilot_traj = pilot_baselines * len(pilot_architectures)
    pilot_sessions = pilot_traj * 4
    pilot_successors = pilot_traj * 3
    pilot_unique = pilot_baselines + pilot_successors

    return {
        "unique_worker_a_checkpoints": unique_worker_a,
        "total_clones": total_clones,
        "logical_trajectories_full_study": logical_trajectories,
        "pilot": {
            "trajectories": pilot_traj,
            "total_sessions": pilot_sessions,
            "successor_sessions": pilot_successors,
            "unique_worker_a_sessions": pilot_baselines,
            "unique_model_session_equivalents": pilot_unique,
            "accounting_formula": f"{pilot_sessions} logical = {pilot_unique} unique ({pilot_baselines} Worker-A + {pilot_successors} successors)",
        },
    }


class BaselineRecord(NamedTuple):
    worker: str
    architecture: str
    model_session_id: str


class BaselineResult(NamedTuple):
    sources: list[str]
    logical_records: list[BaselineRecord]


def build_shared_worker_a_baseline(base_dir: str | Path) -> BaselineResult:
    """Fixed pilot-contract function: 2 Worker-A, 3 architectures, 24 logical, 20 unique."""
    base = Path(base_dir)
    architectures = ["stateless", "summary", "verified_state"]
    sources = []
    logical_records = []
    for replicate in [0, 1]:
        source_id = f"A_task_A_clean_r{replicate}"
        sources.append(source_id)
        for arch in architectures:
            logical_records.append(BaselineRecord("A", arch, source_id))
            for worker in ["B", "C", "D"]:
                model_id = f"{worker}_{arch}_task_A_clean_r{replicate}"
                logical_records.append(BaselineRecord(worker, arch, model_id))
    return BaselineResult(sources=sources, logical_records=logical_records)
