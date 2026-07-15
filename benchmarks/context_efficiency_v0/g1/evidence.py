"""G1-B evidence-packet construction and disk-only reconstruction.

Builder: validates complete synthetic input before writing, then constructs.
Disk validator: accepts only pilot_dir, validates using files on disk only.
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


SHARED_SOURCES_DIR = "shared_sources"
TRAJECTORIES_DIR = "trajectories"

REQUIRED_PILOT_FILES = frozenset({
    "pilot_config.json", "pilot_accounting.json", "pilot_scores.json",
    "pilot_result.json", "pricing_catalog.json", "provider_selection.md",
    "pilot_receipt.json",
})
REQUIRED_SHARED_SOURCE_FILES = frozenset({"provider_call_receipt.json", "checkpoint_receipt.json"})
REQUIRED_SHARED_SOURCE_DIRS = frozenset({"raw"})
REQUIRED_TRAJECTORY_FILES = frozenset({
    "config.json", "worker_A_shared_source_ref.json",
    "trajectory_score.json", "trajectory_result.json", "trajectory_receipt.json",
})
REQUIRED_TRAJECTORY_DIRS = frozenset({"worker_B", "worker_C", "worker_D"})
REQUIRED_WORKER_FILES = frozenset({"provider_call_receipt.json", "checkpoint_receipt.json"})
REQUIRED_WORKER_DIRS = frozenset({"raw"})

# Files that must contain non-empty, structurally valid content
NONEMPTY_FILES = frozenset({
    "pilot_config.json", "pilot_accounting.json", "pilot_scores.json",
    "pilot_result.json", "pricing_catalog.json", "pilot_receipt.json",
    "config.json", "trajectory_score.json", "trajectory_result.json",
    "trajectory_receipt.json",
})


@dataclasses.dataclass(frozen=True)
class EvidencePacketResult:
    packet_valid: bool
    shared_source_count: int
    trajectory_count: int
    reference_count: int
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# ── Builder ──────────────────────────────────────────────────────────


def build_pilot_evidence(
    run_root: Path,
    pilot_id: str,
    shared_sources: dict[str, dict],
    trajectories: dict[str, dict],
    trajectory_refs: dict[str, list[str]] | None = None,
    expected_refs_per_condition: int = 3,
) -> EvidencePacketResult:
    """Build and validate a pilot evidence packet.

    Validates complete synthetic input before writing, then constructs.
    """
    errors = []
    warnings = []

    condition_names = list(shared_sources.keys())
    trajectory_ids = list(trajectories.keys())

    # Step 1: Validate paths
    path_errors = _validate_path_safety(pilot_id, condition_names, trajectory_ids, run_root)
    if path_errors:
        errors.extend(path_errors)
        return _fail(len(shared_sources), len(trajectories), errors, warnings)

    pilot_dir = run_root / pilot_id

    # Step 2: Preflight collision check
    collision_errors = _preflight_collision_check(pilot_dir, condition_names, trajectory_ids)
    if collision_errors:
        errors.extend(collision_errors)
        return _fail(len(shared_sources), len(trajectories), errors, warnings)

    # Step 3: Validate synthetic input content before writing
    content_errors = _validate_synthetic_content(shared_sources, trajectories, condition_names, trajectory_ids)
    if content_errors:
        errors.extend(content_errors)
        return _fail(len(shared_sources), len(trajectories), errors, warnings)

    # Step 4: Create directory structure and write files
    shared_dir = pilot_dir / SHARED_SOURCES_DIR
    traj_dir = pilot_dir / TRAJECTORIES_DIR
    shared_dir.mkdir(parents=True, exist_ok=False)
    traj_dir.mkdir(parents=True, exist_ok=False)

    # Pilot-level files
    _write_json(pilot_dir / "pilot_config.json", {"pilot_id": pilot_id, "synthetic": True, "g1_phase": "G1-B"})
    _write_json(pilot_dir / "pilot_accounting.json", {"pilot_id": pilot_id, "total_physical_cost": 0, "total_logical_cost": 0, "synthetic": True})
    _write_json(pilot_dir / "pilot_scores.json", {"pilot_id": pilot_id, "trajectory_count": len(trajectories), "synthetic": True})
    _write_json(pilot_dir / "pilot_result.json", {"pilot_id": pilot_id, "status": "synthetic", "synthetic": True})
    _write_json(pilot_dir / "pricing_catalog.json", {"provider": "test-provider", "model": "test-model", "categories": {"uncached_input": 2500000, "cached_input": 1250000, "output": 10000000}, "synthetic": True})
    (pilot_dir / "provider_selection.md").write_text(
        "# Provider Selection\n\n"
        "This packet uses a synthetic G1-B fixture. "
        "Real provider selection is deferred to G1-D.\n",
        encoding="utf-8",
    )
    # Pilot receipt with proper checksum
    pilot_receipt = {"pilot_id": pilot_id, "synthetic": True}
    pilot_receipt["checksum"] = g1_hashing.compute_sha256(
        serialization.canonical_json({k: v for k, v in pilot_receipt.items() if k != "checksum"})
    )
    _write_json(pilot_dir / "pilot_receipt.json", pilot_receipt)

    # Shared sources
    for condition, receipt in shared_sources.items():
        cond_dir = shared_dir / condition
        cond_dir.mkdir(parents=True, exist_ok=False)
        _write_json(cond_dir / "provider_call_receipt.json", receipt)
        _write_json(cond_dir / "checkpoint_receipt.json", {"receipt_id": f"chk-{condition}-001"})
        (cond_dir / "raw").mkdir(parents=True, exist_ok=False)

    # Trajectories
    for tid, tdata in trajectories.items():
        tdir = traj_dir / tid
        tdir.mkdir(parents=True, exist_ok=False)
        _write_json(tdir / "config.json", {"trajectory_id": tid, "synthetic": True, "g1_phase": "G1-B"})
        _write_json(tdir / "trajectory_score.json", {"trajectory_id": tid, "synthetic": True})
        _write_json(tdir / "trajectory_result.json", {"trajectory_id": tid, "status": "synthetic", "synthetic": True})
        # Trajectory receipt with proper checksum
        traj_receipt = {"trajectory_id": tid, "synthetic": True}
        traj_receipt["checksum"] = g1_hashing.compute_sha256(
            serialization.canonical_json({k: v for k, v in traj_receipt.items() if k != "checksum"})
        )
        _write_json(tdir / "trajectory_receipt.json", traj_receipt)

        for worker in ("B", "C", "D"):
            wdir = tdir / f"worker_{worker}"
            wdir.mkdir(parents=True, exist_ok=False)
            receipt = tdata.get(f"worker_{worker}_receipt")
            if receipt:
                _write_json(wdir / "provider_call_receipt.json", receipt)
            _write_json(wdir / "checkpoint_receipt.json", {"receipt_id": f"chk-{tid}-{worker}"})
            (wdir / "raw").mkdir(parents=True, exist_ok=False)

        ref = tdata.get("worker_a_shared_source_ref")
        if ref:
            _write_json(tdir / "worker_A_shared_source_ref.json", ref)

    # Step 5: Validate counts
    if len(shared_sources) != 2:
        errors.append(f"expected 2 shared sources, got {len(shared_sources)}")
    if len(trajectories) != 6:
        errors.append(f"expected 6 trajectories, got {len(trajectories)}")

    # Step 6: Run disk-only validation
    disk_errors = validate_pilot_evidence_from_disk(pilot_dir)
    errors.extend(disk_errors)

    ref_count = sum(
        1 for tid in trajectories
        if (traj_dir / tid / "worker_A_shared_source_ref.json").exists()
    )

    return EvidencePacketResult(
        packet_valid=len(errors) == 0,
        shared_source_count=len(shared_sources),
        trajectory_count=len(trajectories),
        reference_count=ref_count,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


# ── Disk-only validator ──────────────────────────────────────────────


def validate_pilot_evidence_from_disk(pilot_dir: Path) -> list[str]:
    """Validate a pilot evidence packet using files on disk only.

    Does not trust the original shared_sources or trajectories dicts.
    Returns list of error strings. Empty list means valid.
    """
    errors = []

    if not pilot_dir.exists():
        errors.append("pilot directory does not exist")
        return errors

    # Discover conditions and trajectory IDs from disk
    shared_dir = pilot_dir / SHARED_SOURCES_DIR
    traj_dir = pilot_dir / TRAJECTORIES_DIR

    condition_names = sorted(
        d.name for d in shared_dir.iterdir() if d.is_dir()
    ) if shared_dir.exists() else []
    trajectory_ids = sorted(
        d.name for d in traj_dir.iterdir() if d.is_dir()
    ) if traj_dir.exists() else []

    # 1. Validate required file tree
    tree_errors = _validate_required_file_tree(pilot_dir, condition_names, trajectory_ids)
    errors.extend(tree_errors)

    # 2. Validate non-empty content
    content_errors = _validate_nonempty_content(pilot_dir, condition_names, trajectory_ids)
    errors.extend(content_errors)

    # 3. Validate provider_selection.md
    sel_path = pilot_dir / "provider_selection.md"
    if sel_path.exists():
        text = sel_path.read_text(encoding="utf-8")
        if not text.strip():
            errors.append("provider_selection.md is empty")
    else:
        errors.append("missing provider_selection.md")

    # 4. Validate provider receipts through G1-A schema
    for cond in condition_names:
        rpath = shared_dir / cond / "provider_call_receipt.json"
        if rpath.exists():
            try:
                data = json.loads(rpath.read_text(encoding="utf-8"))
                schema_errors = g1_schemas.validate_provider_call_receipt(data)
                if schema_errors:
                    errors.append(f"shared source '{cond}' receipt: {'; '.join(schema_errors)}")
            except (json.JSONDecodeError, OSError) as e:
                errors.append(f"shared source '{cond}' receipt: {e}")

    for tid in trajectory_ids:
        for worker in ("B", "C", "D"):
            rpath = traj_dir / tid / f"worker_{worker}" / "provider_call_receipt.json"
            if rpath.exists():
                try:
                    data = json.loads(rpath.read_text(encoding="utf-8"))
                    schema_errors = g1_schemas.validate_provider_call_receipt(data)
                    if schema_errors:
                        errors.append(f"trajectory '{tid}' worker_{worker}: {'; '.join(schema_errors)}")
                except (json.JSONDecodeError, OSError) as e:
                    errors.append(f"trajectory '{tid}' worker_{worker}: {e}")

    # 5. Validate checkpoint receipts (non-empty, has receipt_id)
    for cond in condition_names:
        cpath = shared_dir / cond / "checkpoint_receipt.json"
        if cpath.exists():
            try:
                data = json.loads(cpath.read_text(encoding="utf-8"))
                if not data:
                    errors.append(f"shared source '{cond}' checkpoint_receipt is empty")
                elif not data.get("receipt_id"):
                    errors.append(f"shared source '{cond}' checkpoint_receipt has no receipt_id")
            except (json.JSONDecodeError, OSError) as e:
                errors.append(f"shared source '{cond}' checkpoint_receipt: {e}")

    for tid in trajectory_ids:
        for worker in ("B", "C", "D"):
            cpath = traj_dir / tid / f"worker_{worker}" / "checkpoint_receipt.json"
            if cpath.exists():
                try:
                    data = json.loads(cpath.read_text(encoding="utf-8"))
                    if not data:
                        errors.append(f"trajectory '{tid}' worker_{worker} checkpoint_receipt is empty")
                    elif not data.get("receipt_id"):
                        errors.append(f"trajectory '{tid}' worker_{worker} checkpoint_receipt has no receipt_id")
                except (json.JSONDecodeError, OSError) as e:
                    errors.append(f"trajectory '{tid}' worker_{worker} checkpoint_receipt: {e}")

    # 6. Validate trajectory manifests (mandatory checksums)
    for tid in trajectory_ids:
        _validate_trajectory_manifest(traj_dir / tid, tid, errors)

    # 7. Validate pilot manifest
    _validate_pilot_manifest(pilot_dir, errors)

    # 8. Validate references from disk
    ref_errors = _validate_references_from_disk(pilot_dir, condition_names, trajectory_ids)
    errors.extend(ref_errors)

    # 9. Reject unexpected disk contents
    unexpected_errors = _reject_unexpected_contents(pilot_dir, condition_names, trajectory_ids)
    errors.extend(unexpected_errors)

    return errors


# ── Internal helpers ────────────────────────────────────────────────


def _fail(sc, tc, errors, warnings):
    return EvidencePacketResult(
        packet_valid=False, shared_source_count=sc, trajectory_count=tc,
        reference_count=0, errors=tuple(errors), warnings=tuple(warnings),
    )


def _validate_path_safety(pilot_id, condition_names, trajectory_ids, run_root):
    errors = []
    try:
        resolved_root = run_root.resolve(strict=False)
    except (OSError, ValueError) as e:
        errors.append(f"cannot resolve run_root: {e}")
        return errors

    if not pilot_id:
        errors.append("pilot_id must be non-empty")
    else:
        if "/" in pilot_id or "\\" in pilot_id:
            errors.append(f"pilot_id contains path separator: {pilot_id}")
        if pilot_id in (".", ".."):
            errors.append(f"pilot_id is a relative path: {pilot_id}")
        if pilot_id.startswith("/"):
            errors.append(f"pilot_id is absolute: {pilot_id}")

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

    pilot_dir = resolved_root / pilot_id
    shared_dir = pilot_dir / SHARED_SOURCES_DIR
    traj_dir = pilot_dir / TRAJECTORIES_DIR

    for cond in condition_names:
        dest = shared_dir / cond
        try:
            resolved = dest.resolve(strict=False)
            if not resolved.is_relative_to(resolved_root):
                errors.append(f"condition '{cond}' resolves outside run root: {resolved}")
        except (OSError, ValueError) as e:
            errors.append(f"cannot resolve path for condition '{cond}': {e}")

    for tid in trajectory_ids:
        dest = traj_dir / tid
        try:
            resolved = dest.resolve(strict=False)
            if not resolved.is_relative_to(resolved_root):
                errors.append(f"trajectory '{tid}' resolves outside run root: {resolved}")
        except (OSError, ValueError) as e:
            errors.append(f"cannot resolve path for trajectory '{tid}': {e}")

    for cond in condition_names:
        _check_symlink_escape(shared_dir / cond, resolved_root, errors, f"condition '{cond}'")
    for tid in trajectory_ids:
        _check_symlink_escape(traj_dir / tid, resolved_root, errors, f"trajectory '{tid}'")

    return errors


def _check_symlink_escape(path, resolved_root, errors, label):
    try:
        parts = list(path.parts)
        for i in range(len(parts), 0, -1):
            check = Path(*parts[:i])
            if check.exists() or check.is_symlink():
                if check.is_symlink():
                    target = check.readlink()
                    resolved_target = target.resolve() if target.is_absolute() else (check.parent / target).resolve()
                    if not resolved_target.is_relative_to(resolved_root):
                        errors.append(f"symlink escape via {label}: {check} -> {target} (resolves to {resolved_target})")
                break
    except (OSError, ValueError):
        pass


def _preflight_collision_check(pilot_dir, condition_names, trajectory_ids):
    errors = []
    for fname in REQUIRED_PILOT_FILES:
        if (pilot_dir / fname).exists():
            errors.append(f"pilot-level file already exists: {fname}")
    for cond in condition_names:
        cond_dir = pilot_dir / SHARED_SOURCES_DIR / cond
        if cond_dir.exists():
            errors.append(f"shared source directory already exists: {cond}")
        for fname in REQUIRED_SHARED_SOURCE_FILES:
            if (cond_dir / fname).exists():
                errors.append(f"shared source file already exists: {cond}/{fname}")
        for dname in REQUIRED_SHARED_SOURCE_DIRS:
            if (cond_dir / dname).exists():
                errors.append(f"shared source dir already exists: {cond}/{dname}")
    for tid in trajectory_ids:
        tdir = pilot_dir / TRAJECTORIES_DIR / tid
        if tdir.exists():
            errors.append(f"trajectory directory already exists: {tid}")
        for fname in REQUIRED_TRAJECTORY_FILES:
            if (tdir / fname).exists():
                errors.append(f"trajectory file already exists: {tid}/{fname}")
        for dname in REQUIRED_TRAJECTORY_DIRS:
            dpath = tdir / dname
            if dpath.exists():
                errors.append(f"trajectory dir already exists: {tid}/{dname}")
            for wfname in REQUIRED_WORKER_FILES:
                if (dpath / wfname).exists():
                    errors.append(f"worker file already exists: {tid}/{dname}/{wfname}")
            for wdname in REQUIRED_WORKER_DIRS:
                if (dpath / wdname).exists():
                    errors.append(f"worker dir already exists: {tid}/{dname}/{wdname}")
    return errors


def _validate_synthetic_content(shared_sources, trajectories, condition_names, trajectory_ids):
    """Validate synthetic input content before writing. No empty placeholders."""
    errors = []
    for cond in condition_names:
        receipt = shared_sources.get(cond, {})
        if not receipt:
            errors.append(f"shared source '{cond}' receipt is empty")
        else:
            schema_errors = g1_schemas.validate_provider_call_receipt(receipt)
            if schema_errors:
                errors.append(f"shared source '{cond}' receipt: {'; '.join(schema_errors)}")
    for tid in trajectory_ids:
        tdata = trajectories.get(tid, {})
        for worker in ("B", "C", "D"):
            receipt = tdata.get(f"worker_{worker}_receipt")
            if receipt:
                schema_errors = g1_schemas.validate_provider_call_receipt(receipt)
                if schema_errors:
                    errors.append(f"trajectory '{tid}' worker_{worker}: {'; '.join(schema_errors)}")
        ref = tdata.get("worker_a_shared_source_ref")
        if not ref:
            errors.append(f"trajectory '{tid}' has no worker_a_shared_source_ref")
    return errors


def _validate_required_file_tree(pilot_dir, condition_names, trajectory_ids):
    errors = []
    for fname in REQUIRED_PILOT_FILES:
        if not (pilot_dir / fname).exists():
            errors.append(f"missing required pilot file: {fname}")
    for cond in condition_names:
        cond_dir = pilot_dir / SHARED_SOURCES_DIR / cond
        if not cond_dir.exists():
            errors.append(f"missing shared source directory: {cond}")
            continue
        for fname in REQUIRED_SHARED_SOURCE_FILES:
            if not (cond_dir / fname).exists():
                errors.append(f"missing shared source file: {cond}/{fname}")
        for dname in REQUIRED_SHARED_SOURCE_DIRS:
            if not (cond_dir / dname).exists():
                errors.append(f"missing shared source dir: {cond}/{dname}")
    for tid in trajectory_ids:
        tdir = pilot_dir / TRAJECTORIES_DIR / tid
        if not tdir.exists():
            errors.append(f"missing trajectory directory: {tid}")
            continue
        for fname in REQUIRED_TRAJECTORY_FILES:
            if not (tdir / fname).exists():
                errors.append(f"missing trajectory file: {tid}/{fname}")
        for dname in REQUIRED_TRAJECTORY_DIRS:
            dpath = tdir / dname
            if not dpath.exists():
                errors.append(f"missing trajectory dir: {tid}/{dname}")
                continue
            for wfname in REQUIRED_WORKER_FILES:
                if not (dpath / wfname).exists():
                    errors.append(f"missing worker file: {tid}/{dname}/{wfname}")
            for wdname in REQUIRED_WORKER_DIRS:
                if not (dpath / wdname).exists():
                    errors.append(f"missing worker dir: {tid}/{dname}/{wdname}")
    return errors


def _validate_nonempty_content(pilot_dir, condition_names, trajectory_ids):
    """Reject empty {}, empty string, or structurally invalid content in required files."""
    errors = []
    # Pilot-level nonempty files
    for fname in NONEMPTY_FILES:
        fpath = pilot_dir / fname
        if fpath.exists():
            _check_nonempty_json(fpath, fname, errors)
    # Trajectory config, score, result, receipt
    for tid in trajectory_ids:
        tdir = pilot_dir / TRAJECTORIES_DIR / tid
        for fname in ("config.json", "trajectory_score.json", "trajectory_result.json", "trajectory_receipt.json"):
            fpath = tdir / fname
            if fpath.exists():
                _check_nonempty_json(fpath, f"{tid}/{fname}", errors)
    # Checkpoint receipts
    for cond in condition_names:
        cpath = pilot_dir / SHARED_SOURCES_DIR / cond / "checkpoint_receipt.json"
        if cpath.exists():
            _check_nonempty_json(cpath, f"{cond}/checkpoint_receipt.json", errors)
    for tid in trajectory_ids:
        for worker in ("B", "C", "D"):
            cpath = pilot_dir / TRAJECTORIES_DIR / tid / f"worker_{worker}" / "checkpoint_receipt.json"
            if cpath.exists():
                _check_nonempty_json(cpath, f"{tid}/{worker}/checkpoint_receipt.json", errors)
    return errors


def _check_nonempty_json(fpath, label, errors):
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"cannot read {label}: {e}")
        return
    if not data:
        errors.append(f"{label} is empty")
    elif not isinstance(data, dict):
        errors.append(f"{label} is not a dict")


def _validate_trajectory_manifest(tdir, tid, errors):
    """Validate trajectory receipt manifest with mandatory checksum."""
    rpath = tdir / "trajectory_receipt.json"
    if not rpath.exists():
        errors.append(f"missing trajectory receipt for '{tid}'")
        return
    try:
        data = json.loads(rpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"cannot read trajectory receipt for '{tid}': {e}")
        return
    if not isinstance(data, dict):
        errors.append(f"malformed trajectory receipt for '{tid}'")
        return
    if not data:
        errors.append(f"trajectory '{tid}' receipt is empty")
        return
    if "trajectory_id" not in data:
        errors.append(f"trajectory '{tid}' receipt has no trajectory_id")
    if "checksum" not in data:
        errors.append(f"trajectory '{tid}' receipt has no checksum")
        return
    checksum = data["checksum"]
    content = {k: v for k, v in data.items() if k != "checksum"}
    computed = g1_hashing.compute_sha256(serialization.canonical_json(content))
    if checksum != computed:
        errors.append(
            f"trajectory '{tid}' receipt checksum mismatch: "
            f"got {checksum[:16]}..., expected {computed[:16]}..."
        )


def _validate_pilot_manifest(pilot_dir, errors):
    """Validate pilot receipt manifest with mandatory checksum."""
    rpath = pilot_dir / "pilot_receipt.json"
    if not rpath.exists():
        errors.append("missing pilot receipt")
        return
    try:
        data = json.loads(rpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"cannot read pilot receipt: {e}")
        return
    if not isinstance(data, dict):
        errors.append("malformed pilot receipt")
        return
    if not data:
        errors.append("pilot receipt is empty")
        return
    if "checksum" not in data:
        errors.append("pilot receipt has no checksum")
        return
    checksum = data["checksum"]
    content = {k: v for k, v in data.items() if k != "checksum"}
    computed = g1_hashing.compute_sha256(serialization.canonical_json(content))
    if checksum != computed:
        errors.append(
            f"pilot receipt checksum mismatch: "
            f"got {checksum[:16]}..., expected {computed[:16]}..."
        )


def _validate_references_from_disk(pilot_dir, condition_names, trajectory_ids):
    """Validate references from disk. Exact ID matching, no substring rules."""
    errors = []
    traj_dir = pilot_dir / TRAJECTORIES_DIR
    shared_dir = pilot_dir / SHARED_SOURCES_DIR

    MANDATORY_REF_FIELDS = frozenset({
        "shared_source_id", "provider_invocation_id", "provider_receipt_id",
        "checkpoint_receipt_id", "condition", "allocated_shared_source_cost",
    })

    refs_by_condition = {}
    total_refs = 0

    for tid in trajectory_ids:
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

        # Validate reference hash
        ref_hash = ref_data.get("reference_hash", "")
        if not ref_hash:
            errors.append(f"trajectory '{tid}' reference has no reference_hash")
        else:
            content = {k: v for k, v in ref_data.items() if k != "reference_hash"}
            computed = g1_hashing.compute_sha256(serialization.canonical_json(content))
            if ref_hash != computed:
                errors.append(f"reference hash mismatch for trajectory '{tid}'")

        # Check mandatory fields
        for field in MANDATORY_REF_FIELDS:
            val = ref_data.get(field)
            if val is None or (isinstance(val, str) and not val):
                errors.append(f"trajectory '{tid}' reference missing or empty field: {field}")

        condition = ref_data.get("condition")
        if condition not in ("clean", "drift"):
            errors.append(f"trajectory '{tid}' reference has unknown condition: {condition}")
            continue

        # Exact shared_source_id match
        ref_shared_id = ref_data.get("shared_source_id")
        if ref_shared_id:
            expected_id = f"shared-{condition}-001"
            if ref_shared_id != expected_id:
                errors.append(
                    f"trajectory '{tid}' shared_source_id ({ref_shared_id}) "
                    f"does not match expected ({expected_id})"
                )

        # Verify shared source directory exists
        cond_dir = shared_dir / condition
        if not cond_dir.exists():
            errors.append(f"trajectory '{tid}' references condition '{condition}' but no shared source dir exists")
            continue

        # Verify provider_invocation_id matches
        ref_inv_id = ref_data.get("provider_invocation_id")
        rpath = cond_dir / "provider_call_receipt.json"
        if rpath.exists() and ref_inv_id:
            try:
                receipt_data = json.loads(rpath.read_text(encoding="utf-8"))
                actual_inv_id = receipt_data.get("provider_invocation_id")
                if actual_inv_id and ref_inv_id != actual_inv_id:
                    errors.append(f"trajectory '{tid}' provider_invocation_id ({ref_inv_id}) != shared source ({actual_inv_id})")
            except (json.JSONDecodeError, OSError):
                pass

        # Verify provider_receipt_id matches
        ref_receipt_id = ref_data.get("provider_receipt_id")
        if rpath.exists() and ref_receipt_id:
            try:
                receipt_data = json.loads(rpath.read_text(encoding="utf-8"))
                actual_receipt_id = receipt_data.get("receipt_id")
                if actual_receipt_id and ref_receipt_id != actual_receipt_id:
                    errors.append(f"trajectory '{tid}' provider_receipt_id ({ref_receipt_id}) != shared source ({actual_receipt_id})")
            except (json.JSONDecodeError, OSError):
                pass

        # Verify checkpoint_receipt_id matches
        ref_checkpoint_id = ref_data.get("checkpoint_receipt_id")
        cpath = cond_dir / "checkpoint_receipt.json"
        if cpath.exists() and ref_checkpoint_id:
            try:
                checkpoint_data = json.loads(cpath.read_text(encoding="utf-8"))
                actual_checkpoint_id = checkpoint_data.get("receipt_id")
                if actual_checkpoint_id and ref_checkpoint_id != actual_checkpoint_id:
                    errors.append(f"trajectory '{tid}' checkpoint_receipt_id ({ref_checkpoint_id}) != shared source ({actual_checkpoint_id})")
            except (json.JSONDecodeError, OSError):
                pass

        refs_by_condition[condition] = refs_by_condition.get(condition, 0) + 1
        total_refs += 1

    if total_refs != 6:
        errors.append(f"expected exactly 6 trajectory references, got {total_refs}")
    if refs_by_condition.get("clean", 0) != 3:
        errors.append(f"expected exactly 3 references to clean, got {refs_by_condition.get('clean', 0)}")
    if refs_by_condition.get("drift", 0) != 3:
        errors.append(f"expected exactly 3 references to drift, got {refs_by_condition.get('drift', 0)}")

    # Check for copied Worker-A receipts
    for tid in trajectory_ids:
        copied = traj_dir / tid / "worker_A" / "provider_call_receipt.json"
        if copied.exists():
            errors.append(f"copied Worker-A receipt found in trajectory {tid}")

    return errors


def _reject_unexpected_contents(pilot_dir, condition_names, trajectory_ids):
    """Reject unexpected files, directories, duplicate receipt IDs, etc."""
    errors = []
    traj_dir = pilot_dir / TRAJECTORIES_DIR
    shared_dir = pilot_dir / SHARED_SOURCES_DIR

    # Unexpected files at pilot level
    expected_pilot = REQUIRED_PILOT_FILES
    if pilot_dir.exists():
        for f in pilot_dir.iterdir():
            if f.is_file() and f.name not in expected_pilot:
                errors.append(f"unexpected pilot-level file: {f.name}")

    # Unexpected files in shared source dirs
    for cond in condition_names:
        cond_dir = shared_dir / cond
        if cond_dir.exists():
            expected_shared = set(REQUIRED_SHARED_SOURCE_FILES) | set(REQUIRED_SHARED_SOURCE_DIRS)
            for f in cond_dir.iterdir():
                if f.name not in expected_shared:
                    errors.append(f"unexpected content in shared source '{cond}': {f.name}")

    # Unexpected files in trajectory dirs
    for tid in trajectory_ids:
        tdir = traj_dir / tid
        if tdir.exists():
            expected_traj = set(REQUIRED_TRAJECTORY_FILES) | set(REQUIRED_TRAJECTORY_DIRS)
            for f in tdir.iterdir():
                if f.name not in expected_traj:
                    errors.append(f"unexpected content in trajectory '{tid}': {f.name}")
            for dname in REQUIRED_TRAJECTORY_DIRS:
                wdir = tdir / dname
                if wdir.exists():
                    expected_worker = set(REQUIRED_WORKER_FILES) | set(REQUIRED_WORKER_DIRS)
                    for f in wdir.iterdir():
                        if f.name not in expected_worker:
                            errors.append(f"unexpected content in trajectory '{tid}/{dname}': {f.name}")

    # Duplicate receipt IDs across all provider_call_receipt.json files
    receipt_ids_seen = set()
    for cond in condition_names:
        rpath = shared_dir / cond / "provider_call_receipt.json"
        if rpath.exists():
            try:
                data = json.loads(rpath.read_text(encoding="utf-8"))
                rid = data.get("receipt_id")
                if rid:
                    if rid in receipt_ids_seen:
                        errors.append(f"duplicate receipt ID: {rid[:16]}...")
                    receipt_ids_seen.add(rid)
            except (json.JSONDecodeError, OSError):
                pass
    for tid in trajectory_ids:
        for worker in ("B", "C", "D"):
            rpath = traj_dir / tid / f"worker_{worker}" / "provider_call_receipt.json"
            if rpath.exists():
                try:
                    data = json.loads(rpath.read_text(encoding="utf-8"))
                    rid = data.get("receipt_id")
                    if rid:
                        if rid in receipt_ids_seen:
                            errors.append(f"duplicate receipt ID: {rid[:16]}...")
                        receipt_ids_seen.add(rid)
                except (json.JSONDecodeError, OSError):
                    pass

    return errors


def _write_json(path: Path, data: dict) -> None:
    path.write_text(serialization.canonical_json(data) + "\n", encoding="utf-8")
