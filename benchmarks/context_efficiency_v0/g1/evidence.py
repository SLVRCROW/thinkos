"""G1-B evidence-packet construction.

Builds and validates the §20 pilot evidence structure from recorded
synthetic inputs. No runtime evidence may enter the Git worktree.
"""

from __future__ import annotations
import dataclasses
import json
import hashlib
from pathlib import Path
from typing import Any

from . import hashing as g1_hashing
from . import serialization


# ── Evidence packet paths (relative to G1_RUN_ROOT) ────────────────────


SHARED_SOURCES_DIR = "shared_sources"
TRAJECTORIES_DIR = "trajectories"


def shared_source_path(condition: str) -> str:
    return f"{SHARED_SOURCES_DIR}/{condition}"


def trajectory_path(trajectory_id: str) -> str:
    return f"{TRAJECTORIES_DIR}/{trajectory_id}"


# ── Evidence packet builder ──────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class EvidencePacketResult:
    """Result of building and validating an evidence packet."""

    packet_valid: bool
    shared_source_count: int
    trajectory_count: int
    reference_count: int
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def build_pilot_evidence(
    run_root: Path,
    pilot_id: str,
    shared_sources: dict[str, dict],  # condition -> receipt dict
    trajectories: dict[str, dict],  # trajectory_id -> trajectory data
    trajectory_refs: dict[str, list[str]],  # trajectory_id -> [condition]
    expected_refs_per_condition: int = 3,
) -> EvidencePacketResult:
    """Build and validate a pilot evidence packet in a temporary directory.

    Stores each physical shared Worker-A receipt once under shared_sources/.
    Stores trajectory references instead of copied Worker-A receipts.
    Validates reference counts, path safety, and canonical hashes.
    """
    errors = []
    warnings = []

    # Create directory structure
    pilot_dir = run_root / pilot_id
    shared_dir = pilot_dir / SHARED_SOURCES_DIR
    traj_dir = pilot_dir / TRAJECTORIES_DIR

    shared_dir.mkdir(parents=True, exist_ok=True)
    traj_dir.mkdir(parents=True, exist_ok=True)

    # Write shared sources
    for condition, receipt in shared_sources.items():
        cond_dir = shared_dir / condition
        cond_dir.mkdir(parents=True, exist_ok=True)
        _write_json(cond_dir / "provider_call_receipt.json", receipt)

    # Write trajectories
    for tid, tdata in trajectories.items():
        tdir = traj_dir / tid
        tdir.mkdir(parents=True, exist_ok=True)

        # Write worker_B, C, D receipts
        for worker in ("B", "C", "D"):
            wdir = tdir / f"worker_{worker}"
            wdir.mkdir(parents=True, exist_ok=True)
            receipt = tdata.get(f"worker_{worker}_receipt")
            if receipt:
                _write_json(wdir / "provider_call_receipt.json", receipt)

        # Write shared source reference (not a copy of the receipt)
        ref = tdata.get("worker_a_shared_source_ref")
        if ref:
            _write_json(tdir / "worker_A_shared_source_ref.json", ref)

    # Validate
    # 1. Shared source count
    actual_shared = len(shared_sources)
    if actual_shared != 2:
        errors.append(
            f"expected 2 shared sources, got {actual_shared}"
        )

    # 2. Trajectory count
    actual_traj = len(trajectories)
    if actual_traj != 6:
        errors.append(
            f"expected 6 trajectories, got {actual_traj}"
        )

    # 3. Reference count per condition
    ref_counts: dict[str, int] = {}
    for tid, conditions in trajectory_refs.items():
        for cond in conditions:
            ref_counts[cond] = ref_counts.get(cond, 0) + 1

    for cond, count in ref_counts.items():
        if count != expected_refs_per_condition:
            errors.append(
                f"condition '{cond}': expected {expected_refs_per_condition} "
                f"references, got {count}"
            )

    # 4. Path safety — reject absolute or traversal paths
    for path_str in _collect_paths(pilot_dir):
        p = Path(path_str)
        if p.is_absolute():
            errors.append(f"absolute path not allowed: {path_str}")
        if ".." in path_str.split("/"):
            errors.append(f"traversal path not allowed: {path_str}")

    # 5. No copied Worker-A receipts inside trajectory directories
    for tid in trajectories:
        copied = traj_dir / tid / "worker_A" / "provider_call_receipt.json"
        if copied.exists():
            errors.append(
                f"copied Worker-A receipt found in trajectory {tid}"
            )

    # 6. Canonically hash trajectory and pilot manifests
    for tid in trajectories:
        ref_file = traj_dir / tid / "worker_A_shared_source_ref.json"
        if ref_file.exists():
            ref_data = json.loads(ref_file.read_text())
            ref_hash = ref_data.get("reference_hash", "")
            # Self-exclude reference_hash from its own digest
            content = {k: v for k, v in ref_data.items() if k != "reference_hash"}
            computed = g1_hashing.compute_sha256(
                serialization.canonical_json(content)
            )
            if ref_hash and ref_hash != computed:
                errors.append(
                    f"reference hash mismatch for trajectory {tid}"
                )

    packet_valid = len(errors) == 0
    return EvidencePacketResult(
        packet_valid=packet_valid,
        shared_source_count=actual_shared,
        trajectory_count=actual_traj,
        reference_count=sum(ref_counts.values()),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _write_json(path: Path, data: dict) -> None:
    """Write a JSON file with canonical serialization."""
    path.write_text(serialization.canonical_json(data) + "\n")


def _collect_paths(root: Path) -> list[str]:
    """Collect all relative paths under root."""
    paths = []
    for f in root.rglob("*"):
        if f.is_file():
            paths.append(str(f.relative_to(root)))
    return paths
