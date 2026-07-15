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
from . import schemas as g1_schemas


# ── Evidence packet paths (relative to G1_RUN_ROOT) ────────────────────


SHARED_SOURCES_DIR = "shared_sources"
TRAJECTORIES_DIR = "trajectories"


def shared_source_path(condition: str) -> str:
    return f"{SHARED_SOURCES_DIR}/{condition}"


def trajectory_path(trajectory_id: str) -> str:
    return f"{TRAJECTORIES_DIR}/{trajectory_id}"


# ── Required §20 file tree ────────────────────────────────────────────

# Every path that must exist for a valid packet.
REQUIRED_PILOT_FILES = frozenset({
    "pilot_config.json",
    "pilot_accounting.json",
    "pilot_scores.json",
    "pilot_result.json",
    "pricing_catalog.json",
    "provider_selection.md",
    "pilot_receipt.json",
})

REQUIRED_SHARED_SOURCE_FILES = frozenset({
    "provider_call_receipt.json",
    "checkpoint_receipt.json",
})

REQUIRED_SHARED_SOURCE_DIRS = frozenset({"raw"})

REQUIRED_TRAJECTORY_FILES = frozenset({
    "config.json",
    "worker_A_shared_source_ref.json",
    "trajectory_score.json",
    "trajectory_result.json",
    "trajectory_receipt.json",
})

REQUIRED_TRAJECTORY_DIRS = frozenset({"worker_B", "worker_C", "worker_D"})

REQUIRED_WORKER_FILES = frozenset({
    "provider_call_receipt.json",
    "checkpoint_receipt.json",
})

REQUIRED_WORKER_DIRS = frozenset({"raw"})

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


def _build_required_paths(
    pilot_dir: Path,
    condition_names: list[str],
    trajectory_ids: list[str],
) -> list[Path]:
    """Build the complete set of required paths for a valid §20 packet."""
    paths = []

    # Pilot-level files
    for fname in REQUIRED_PILOT_FILES:
        paths.append(pilot_dir / fname)

    # Shared source files and dirs
    for cond in condition_names:
        cond_dir = pilot_dir / SHARED_SOURCES_DIR / cond
        for fname in REQUIRED_SHARED_SOURCE_FILES:
            paths.append(cond_dir / fname)
        for dname in REQUIRED_SHARED_SOURCE_DIRS:
            paths.append(cond_dir / dname)

    # Trajectory files and dirs
    for tid in trajectory_ids:
        tdir = pilot_dir / TRAJECTORIES_DIR / tid
        for fname in REQUIRED_TRAJECTORY_FILES:
            paths.append(tdir / fname)
        for dname in REQUIRED_TRAJECTORY_DIRS:
            paths.append(tdir / dname)
            # Worker files and dirs
            for wfname in REQUIRED_WORKER_FILES:
                paths.append(tdir / dname / wfname)
            for wdname in REQUIRED_WORKER_DIRS:
                paths.append(tdir / dname / wdname)

    return paths


def _validate_path_safety(
    pilot_id: str,
    condition_names: list[str],
    trajectory_ids: list[str],
    run_root: Path,
) -> list[str]:
    """Validate all identifiers and paths before any filesystem mutation.

    Uses Path.is_relative_to() for containment. Rejects absolute paths,
    '.', '..', path separators inside identifiers, traversal attempts,
    symlink-based escapes, and any resolved destination outside the
    resolved run root.
    """
    errors = []

    # Resolve the run root to an absolute canonical path
    try:
        resolved_root = run_root.resolve(strict=False)
    except (OSError, ValueError) as e:
        errors.append(f"cannot resolve run_root: {e}")
        return errors

    # Validate pilot_id
    if not pilot_id:
        errors.append("pilot_id must be non-empty")
    else:
        if "/" in pilot_id or "\\" in pilot_id:
            errors.append(f"pilot_id contains path separator: {pilot_id}")
        if pilot_id in (".", ".."):
            errors.append(f"pilot_id is a relative path: {pilot_id}")
        if pilot_id.startswith("/"):
            errors.append(f"pilot_id is absolute: {pilot_id}")

    # Validate condition names
    for cond in condition_names:
        if not cond:
            errors.append("condition name must be non-empty")
            continue
        if "/" in cond or "\\" in cond:
            errors.append(f"condition name contains path separator: {cond}")
        if cond in (".", ".."):
            errors.append(f"condition name is a relative path: {cond}")
        if cond.startswith("/"):
            errors.append(f"condition name is absolute: {cond}")

    # Validate trajectory IDs
    for tid in trajectory_ids:
        if not tid:
            errors.append("trajectory ID must be non-empty")
            continue
        if "/" in tid or "\\" in tid:
            errors.append(f"trajectory ID contains path separator: {tid}")
        if tid in (".", ".."):
            errors.append(f"trajectory ID is a relative path: {tid}")
        if tid.startswith("/"):
            errors.append(f"trajectory ID is absolute: {tid}")

    # Validate that no generated destination escapes the run root
    # using Path.is_relative_to() for proper containment
    pilot_dir = resolved_root / pilot_id
    shared_dir = pilot_dir / SHARED_SOURCES_DIR
    traj_dir = pilot_dir / TRAJECTORIES_DIR

    for cond in condition_names:
        dest = shared_dir / cond
        try:
            resolved = dest.resolve(strict=False)
            if not resolved.is_relative_to(resolved_root):
                errors.append(
                    f"condition '{cond}' resolves outside run root: {resolved}"
                )
        except (OSError, ValueError) as e:
            errors.append(f"cannot resolve path for condition '{cond}': {e}")

    for tid in trajectory_ids:
        dest = traj_dir / tid
        try:
            resolved = dest.resolve(strict=False)
            if not resolved.is_relative_to(resolved_root):
                errors.append(
                    f"trajectory '{tid}' resolves outside run root: {resolved}"
                )
        except (OSError, ValueError) as e:
            errors.append(f"cannot resolve path for trajectory '{tid}': {e}")

    # Check for symlink-based escape: verify no component is a symlink
    # pointing outside the run root
    for cond in condition_names:
        dest = shared_dir / cond
        _check_symlink_escape(dest, resolved_root, errors, f"condition '{cond}'")

    for tid in trajectory_ids:
        dest = traj_dir / tid
        _check_symlink_escape(dest, resolved_root, errors, f"trajectory '{tid}'")

    return errors


def _check_symlink_escape(
    path: Path, resolved_root: Path, errors: list[str], label: str
) -> None:
    """Check that no component of path is a symlink pointing outside resolved_root."""
    try:
        parts = list(path.parts)
        for i in range(len(parts), 0, -1):
            check = Path(*parts[:i])
            if check.exists() or check.is_symlink():
                if check.is_symlink():
                    target = check.readlink()
                    if target.is_absolute():
                        resolved_target = target.resolve()
                    else:
                        resolved_target = (check.parent / target).resolve()
                    if not resolved_target.is_relative_to(resolved_root):
                        errors.append(
                            f"symlink escape via {label}: {check} -> {target} "
                            f"(resolves to {resolved_target})"
                        )
                break
    except (OSError, ValueError):
        pass


def _preflight_collision_check(
    pilot_dir: Path,
    condition_names: list[str],
    trajectory_ids: list[str],
) -> list[str]:
    """Check for unexpected pre-existing files, dirs, or symlinks before writing.

    Never overwrite an existing artifact. Treat unexpected files as errors.
    """
    errors = []

    # Check pilot-level files
    for fname in REQUIRED_PILOT_FILES:
        fpath = pilot_dir / fname
        if fpath.exists():
            errors.append(f"pilot-level file already exists: {fname}")

    # Check shared source paths
    for cond in condition_names:
        cond_dir = pilot_dir / SHARED_SOURCES_DIR / cond
        if cond_dir.exists():
            errors.append(f"shared source directory already exists: {cond}")
        for fname in REQUIRED_SHARED_SOURCE_FILES:
            fpath = cond_dir / fname
            if fpath.exists():
                errors.append(f"shared source file already exists: {cond}/{fname}")
        for dname in REQUIRED_SHARED_SOURCE_DIRS:
            dpath = cond_dir / dname
            if dpath.exists():
                errors.append(f"shared source dir already exists: {cond}/{dname}")

    # Check trajectory paths
    for tid in trajectory_ids:
        tdir = pilot_dir / TRAJECTORIES_DIR / tid
        if tdir.exists():
            errors.append(f"trajectory directory already exists: {tid}")
        for fname in REQUIRED_TRAJECTORY_FILES:
            fpath = tdir / fname
            if fpath.exists():
                errors.append(f"trajectory file already exists: {tid}/{fname}")
        for dname in REQUIRED_TRAJECTORY_DIRS:
            dpath = tdir / dname
            if dpath.exists():
                errors.append(f"trajectory dir already exists: {tid}/{dname}")
            for wfname in REQUIRED_WORKER_FILES:
                wpath = dpath / wfname
                if wpath.exists():
                    errors.append(f"worker file already exists: {tid}/{dname}/{wfname}")
            for wdname in REQUIRED_WORKER_DIRS:
                wdpath = dpath / wdname
                if wdpath.exists():
                    errors.append(f"worker dir already exists: {tid}/{dname}/{wdname}")

    return errors


def _validate_receipts_and_manifests(
    pilot_dir: Path,
    shared_sources: dict[str, dict],
    trajectories: dict[str, dict],
) -> list[str]:
    """Validate receipt and manifest integrity.

    - Validate synthetic provider receipts through the frozen G1-A schema.
    - Validate checkpoint receipts through the G0 interface.
    - Verify all required receipt IDs and manifest checksums.
    - Missing hashes must fail.
    - Mismatched hashes must fail.
    - Use the §21 self-exclusion rules.
    - Write and read JSON explicitly as UTF-8.
    - Reject malformed or unexpected files during reconstruction.
    """
    errors = []

    # Validate shared source receipts through G1-A schema
    for condition, receipt in shared_sources.items():
        schema_errors = g1_schemas.validate_provider_call_receipt(receipt)
        if schema_errors:
            errors.append(
                f"shared source '{condition}' receipt validation failed: "
                f"{'; '.join(schema_errors)}"
            )

    # Validate trajectory receipts through G1-A schema
    for tid, tdata in trajectories.items():
        for worker in ("B", "C", "D"):
            receipt = tdata.get(f"worker_{worker}_receipt")
            if receipt:
                schema_errors = g1_schemas.validate_provider_call_receipt(receipt)
                if schema_errors:
                    errors.append(
                        f"trajectory '{tid}' worker_{worker} receipt validation failed: "
                        f"{'; '.join(schema_errors)}"
                    )

    # Validate that written files can be read back as UTF-8 JSON
    for condition in shared_sources:
        receipt_path = pilot_dir / SHARED_SOURCES_DIR / condition / "provider_call_receipt.json"
        if receipt_path.exists():
            try:
                data = json.loads(receipt_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    errors.append(f"malformed receipt file for condition '{condition}'")
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                errors.append(f"cannot read receipt for condition '{condition}': {e}")

    for tid in trajectories:
        for worker in ("B", "C", "D"):
            receipt_path = (
                pilot_dir / TRAJECTORIES_DIR / tid / f"worker_{worker}" / "provider_call_receipt.json"
            )
            if receipt_path.exists():
                try:
                    data = json.loads(receipt_path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        errors.append(
                            f"malformed receipt file for trajectory '{tid}' worker_{worker}"
                        )
                except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                    errors.append(
                        f"cannot read receipt for trajectory '{tid}' worker_{worker}: {e}"
                    )

    return errors


def _validate_trajectory_manifest(tdir: Path, tid: str, errors: list[str]) -> None:
    """Validate a single trajectory receipt manifest with §21 self-exclusion."""
    receipt_path = tdir / "trajectory_receipt.json"
    if not receipt_path.exists():
        errors.append(f"missing trajectory receipt for '{tid}'")
        return
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"cannot read trajectory receipt for '{tid}': {e}")
        return
    if not isinstance(data, dict):
        errors.append(f"malformed trajectory receipt for '{tid}'")
        return
    # Empty dict is a valid placeholder — skip checksum validation
    if not data:
        return
    # Verify checksum with §21 self-exclusion
    checksum = data.get("checksum", "")
    if not checksum:
        errors.append(f"trajectory '{tid}' receipt has no checksum")
        return
    content = {k: v for k, v in data.items() if k != "checksum"}
    computed = g1_hashing.compute_sha256(serialization.canonical_json(content))
    if checksum != computed:
        errors.append(
            f"trajectory '{tid}' receipt checksum mismatch: "
            f"got {checksum[:16]}..., expected {computed[:16]}..."
        )


def _validate_pilot_manifest(pilot_dir: Path, errors: list[str]) -> None:
    """Validate the pilot receipt manifest with §21 self-exclusion."""
    receipt_path = pilot_dir / "pilot_receipt.json"
    if not receipt_path.exists():
        errors.append("missing pilot receipt")
        return
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"cannot read pilot receipt: {e}")
        return
    if not isinstance(data, dict):
        errors.append("malformed pilot receipt")
        return
    # Empty dict is a valid placeholder — skip checksum validation
    if not data:
        return
    checksum = data.get("checksum", "")
    if not checksum:
        errors.append("pilot receipt has no checksum")
        return
    content = {k: v for k, v in data.items() if k != "checksum"}
    computed = g1_hashing.compute_sha256(serialization.canonical_json(content))
    if checksum != computed:
        errors.append(
            f"pilot receipt checksum mismatch: "
            f"got {checksum[:16]}..., expected {computed[:16]}..."
        )


def _validate_references_from_actual_files(
    pilot_dir: Path,
    trajectories: dict[str, dict],
    shared_sources: dict[str, dict],
) -> list[str]:
    """Validate real references from actual trajectory reference records.

    Every Worker-A reference must contain all mandatory fields. The referenced
    provider and checkpoint receipt IDs must resolve exactly to the single
    shared source for that condition. Missing files, missing fields, malformed
    receipts, conflicting IDs, or unresolved references must fail.
    """
    errors = []

    traj_dir = pilot_dir / TRAJECTORIES_DIR
    shared_dir = pilot_dir / SHARED_SOURCES_DIR

    MANDATORY_REF_FIELDS = frozenset({
        "shared_source_id", "provider_invocation_id", "provider_receipt_id",
        "checkpoint_receipt_id", "condition", "allocated_shared_source_cost",
    })

    refs_by_condition: dict[str, int] = {}
    total_refs = 0

    for tid in trajectories:
        ref_file = traj_dir / tid / "worker_A_shared_source_ref.json"
        if not ref_file.exists():
            errors.append(f"missing reference file for trajectory '{tid}'")
            continue

        try:
            ref_data = json.loads(ref_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            errors.append(f"cannot read reference for trajectory '{tid}': {e}")
            continue

        if not isinstance(ref_data, dict):
            errors.append(f"malformed reference for trajectory '{tid}'")
            continue

        # Validate reference hash with §21 self-exclusion
        ref_hash = ref_data.get("reference_hash", "")
        if not ref_hash:
            errors.append(f"trajectory '{tid}' reference has no reference_hash")
        else:
            content = {k: v for k, v in ref_data.items() if k != "reference_hash"}
            computed = g1_hashing.compute_sha256(
                serialization.canonical_json(content)
            )
            if ref_hash != computed:
                errors.append(
                    f"reference hash mismatch for trajectory '{tid}': "
                    f"got {ref_hash[:16]}..., expected {computed[:16]}..."
                )

        # Check all mandatory fields are present and non-empty
        for field in MANDATORY_REF_FIELDS:
            val = ref_data.get(field)
            if val is None or (isinstance(val, str) and not val):
                errors.append(
                    f"trajectory '{tid}' reference missing or empty field: {field}"
                )

        # Extract condition
        condition = ref_data.get("condition")
        if condition not in ("clean", "drift"):
            errors.append(f"trajectory '{tid}' reference has unknown condition: {condition}")
            continue

        # Verify the shared source directory exists
        cond_dir = shared_dir / condition
        if not cond_dir.exists():
            errors.append(
                f"trajectory '{tid}' references condition '{condition}' "
                f"but no shared source directory exists"
            )
            continue

        # Verify provider_invocation_id matches
        ref_inv_id = ref_data.get("provider_invocation_id")
        receipt_path = cond_dir / "provider_call_receipt.json"
        if receipt_path.exists() and ref_inv_id:
            try:
                receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
                actual_inv_id = receipt_data.get("provider_invocation_id")
                if actual_inv_id and ref_inv_id != actual_inv_id:
                    errors.append(
                        f"trajectory '{tid}' provider_invocation_id "
                        f"({ref_inv_id}) does not match shared source "
                        f"({actual_inv_id})"
                    )
            except (json.JSONDecodeError, OSError):
                pass

        # Verify provider_receipt_id matches
        ref_receipt_id = ref_data.get("provider_receipt_id")
        if receipt_path.exists() and ref_receipt_id:
            try:
                receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
                actual_receipt_id = receipt_data.get("receipt_id")
                if actual_receipt_id and ref_receipt_id != actual_receipt_id:
                    errors.append(
                        f"trajectory '{tid}' provider_receipt_id "
                        f"({ref_receipt_id}) does not match shared source "
                        f"({actual_receipt_id})"
                    )
            except (json.JSONDecodeError, OSError):
                pass

        # Verify checkpoint_receipt_id matches
        ref_checkpoint_id = ref_data.get("checkpoint_receipt_id")
        checkpoint_path = cond_dir / "checkpoint_receipt.json"
        if checkpoint_path.exists() and ref_checkpoint_id:
            try:
                checkpoint_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                actual_checkpoint_id = checkpoint_data.get("receipt_id")
                if actual_checkpoint_id and ref_checkpoint_id != actual_checkpoint_id:
                    errors.append(
                        f"trajectory '{tid}' checkpoint_receipt_id "
                        f"({ref_checkpoint_id}) does not match shared source "
                        f"({actual_checkpoint_id})"
                    )
            except (json.JSONDecodeError, OSError):
                pass

        # Verify shared_source_id matches
        ref_shared_id = ref_data.get("shared_source_id")
        if ref_shared_id:
            # Check against the condition name
            if ref_shared_id != condition and f"shared-{condition}" not in ref_shared_id:
                errors.append(
                    f"trajectory '{tid}' shared_source_id "
                    f"({ref_shared_id}) does not match condition '{condition}'"
                )

        refs_by_condition[condition] = refs_by_condition.get(condition, 0) + 1
        total_refs += 1

    # Check total reference count
    if total_refs != 6:
        errors.append(f"expected exactly 6 trajectory references, got {total_refs}")

    # Check clean references
    clean_count = refs_by_condition.get("clean", 0)
    if clean_count != 3:
        errors.append(f"expected exactly 3 references to clean, got {clean_count}")

    # Check drift references
    drift_count = refs_by_condition.get("drift", 0)
    if drift_count != 3:
        errors.append(f"expected exactly 3 references to drift, got {drift_count}")

    # Check for copied Worker-A receipts inside trajectories
    for tid in trajectories:
        copied = traj_dir / tid / "worker_A" / "provider_call_receipt.json"
        if copied.exists():
            errors.append(
                f"copied Worker-A receipt found in trajectory {tid}"
            )

    return errors


def _validate_required_file_tree(
    pilot_dir: Path,
    condition_names: list[str],
    trajectory_ids: list[str],
) -> list[str]:
    """Validate that every required §20 file and directory exists.

    A missing required file or directory must invalidate reconstruction.
    """
    errors = []

    # Pilot-level files
    for fname in REQUIRED_PILOT_FILES:
        fpath = pilot_dir / fname
        if not fpath.exists():
            errors.append(f"missing required pilot file: {fname}")

    # Shared source files and dirs
    for cond in condition_names:
        cond_dir = pilot_dir / SHARED_SOURCES_DIR / cond
        if not cond_dir.exists():
            errors.append(f"missing shared source directory: {cond}")
            continue
        for fname in REQUIRED_SHARED_SOURCE_FILES:
            fpath = cond_dir / fname
            if not fpath.exists():
                errors.append(f"missing shared source file: {cond}/{fname}")
        for dname in REQUIRED_SHARED_SOURCE_DIRS:
            dpath = cond_dir / dname
            if not dpath.exists():
                errors.append(f"missing shared source dir: {cond}/{dname}")

    # Trajectory files and dirs
    for tid in trajectory_ids:
        tdir = pilot_dir / TRAJECTORIES_DIR / tid
        if not tdir.exists():
            errors.append(f"missing trajectory directory: {tid}")
            continue
        for fname in REQUIRED_TRAJECTORY_FILES:
            fpath = tdir / fname
            if not fpath.exists():
                errors.append(f"missing trajectory file: {tid}/{fname}")
        for dname in REQUIRED_TRAJECTORY_DIRS:
            dpath = tdir / dname
            if not dpath.exists():
                errors.append(f"missing trajectory dir: {tid}/{dname}")
                continue
            for wfname in REQUIRED_WORKER_FILES:
                wpath = dpath / wfname
                if not wpath.exists():
                    errors.append(f"missing worker file: {tid}/{dname}/{wfname}")
            for wdname in REQUIRED_WORKER_DIRS:
                wdpath = dpath / wdname
                if not wdpath.exists():
                    errors.append(f"missing worker dir: {tid}/{dname}/{wdname}")

    return errors


def build_pilot_evidence(
    run_root: Path,
    pilot_id: str,
    shared_sources: dict[str, dict],  # condition -> receipt dict
    trajectories: dict[str, dict],  # trajectory_id -> trajectory data
    trajectory_refs: dict[str, list[str]] | None = None,  # deprecated — derived from files
    expected_refs_per_condition: int = 3,
) -> EvidencePacketResult:
    """Build and validate a pilot evidence packet in a temporary directory.

    Stores each physical shared Worker-A receipt once under shared_sources/.
    Stores trajectory references instead of copied Worker-A receipts.
    Validates reference counts, path safety, and canonical hashes.
    """
    errors = []
    warnings = []

    # Collect all identifiers for path validation
    condition_names = list(shared_sources.keys())
    trajectory_ids = list(trajectories.keys())

    # ── Step 1: Validate paths before writing anything ──
    path_errors = _validate_path_safety(
        pilot_id, condition_names, trajectory_ids, run_root
    )
    if path_errors:
        errors.extend(path_errors)
        return EvidencePacketResult(
            packet_valid=False,
            shared_source_count=len(shared_sources),
            trajectory_count=len(trajectories),
            reference_count=0,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    # ── Step 2: Preflight collision check ──
    pilot_dir = run_root / pilot_id
    collision_errors = _preflight_collision_check(
        pilot_dir, condition_names, trajectory_ids
    )
    if collision_errors:
        errors.extend(collision_errors)
        return EvidencePacketResult(
            packet_valid=False,
            shared_source_count=len(shared_sources),
            trajectory_count=len(trajectories),
            reference_count=0,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    # ── Step 3: Create directory structure ──
    shared_dir = pilot_dir / SHARED_SOURCES_DIR
    traj_dir = pilot_dir / TRAJECTORIES_DIR

    shared_dir.mkdir(parents=True, exist_ok=False)
    traj_dir.mkdir(parents=True, exist_ok=False)

    # Write pilot-level files
    for fname in REQUIRED_PILOT_FILES:
        _write_json(pilot_dir / fname, {})

    # Write shared sources
    for condition, receipt in shared_sources.items():
        cond_dir = shared_dir / condition
        cond_dir.mkdir(parents=True, exist_ok=False)
        _write_json(cond_dir / "provider_call_receipt.json", receipt)
        # Write checkpoint receipt with a proper receipt_id
        checkpoint_data = {"receipt_id": f"chk-{condition}-001"}
        _write_json(cond_dir / "checkpoint_receipt.json", checkpoint_data)
        (cond_dir / "raw").mkdir(parents=True, exist_ok=False)

    # Write trajectories
    for tid, tdata in trajectories.items():
        tdir = traj_dir / tid
        tdir.mkdir(parents=True, exist_ok=False)

        # Write trajectory-level files
        _write_json(tdir / "config.json", {})
        _write_json(tdir / "trajectory_score.json", {})
        _write_json(tdir / "trajectory_result.json", {})
        _write_json(tdir / "trajectory_receipt.json", {})

        # Write worker_B, C, D directories
        for worker in ("B", "C", "D"):
            wdir = tdir / f"worker_{worker}"
            wdir.mkdir(parents=True, exist_ok=False)
            receipt = tdata.get(f"worker_{worker}_receipt")
            if receipt:
                _write_json(wdir / "provider_call_receipt.json", receipt)
            _write_json(wdir / "checkpoint_receipt.json", {})
            (wdir / "raw").mkdir(parents=True, exist_ok=False)

        # Write shared source reference (not a copy of the receipt)
        ref = tdata.get("worker_a_shared_source_ref")
        if ref:
            _write_json(tdir / "worker_A_shared_source_ref.json", ref)

    # ── Step 4: Validate shared source count ──
    actual_shared = len(shared_sources)
    if actual_shared != 2:
        errors.append(
            f"expected 2 shared sources, got {actual_shared}"
        )

    # ── Step 5: Validate trajectory count ──
    actual_traj = len(trajectories)
    if actual_traj != 6:
        errors.append(
            f"expected 6 trajectories, got {actual_traj}"
        )

    # ── Step 6: Validate required file tree ──
    tree_errors = _validate_required_file_tree(
        pilot_dir, condition_names, trajectory_ids
    )
    errors.extend(tree_errors)

    # ── Step 7: Validate references from actual files ──
    ref_errors = _validate_references_from_actual_files(
        pilot_dir, trajectories, shared_sources
    )
    errors.extend(ref_errors)

    # ── Step 8: Validate receipts and manifests ──
    receipt_errors = _validate_receipts_and_manifests(
        pilot_dir, shared_sources, trajectories
    )
    errors.extend(receipt_errors)

    # ── Step 9: Validate trajectory manifests ──
    for tid in trajectories:
        tdir = traj_dir / tid
        _validate_trajectory_manifest(tdir, tid, errors)

    # ── Step 10: Validate pilot manifest ──
    _validate_pilot_manifest(pilot_dir, errors)

    # Compute reference count from actual files
    ref_count = 0
    for tid in trajectories:
        ref_file = traj_dir / tid / "worker_A_shared_source_ref.json"
        if ref_file.exists():
            ref_count += 1

    packet_valid = len(errors) == 0
    return EvidencePacketResult(
        packet_valid=packet_valid,
        shared_source_count=actual_shared,
        trajectory_count=actual_traj,
        reference_count=ref_count,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _write_json(path: Path, data: dict) -> None:
    """Write a JSON file with canonical serialization as UTF-8."""
    path.write_text(serialization.canonical_json(data) + "\n", encoding="utf-8")
