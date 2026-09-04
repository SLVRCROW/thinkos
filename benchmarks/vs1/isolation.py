"""VS-1 isolation: per-arm workdirs, semantic canaries, leakage detection.

Every trajectory gets an isolated workdir keyed by arm + condition + replicate.
A per-arm semantic canary is embedded in the metadata envelope; canary
detection is deterministic. verify_no_leakage rejects any cross-arm or
cross-condition artifact reference.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .schemas import ARMS, CONDITIONS

_TRAJECTORY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def hashlib_hex(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


# Semantic canary values (one per arm). These are embedded in the envelope;
# a successor that repeats a foreign canary proves cross-arm leakage.
CANARIES: dict[str, str] = {
    arm: f"canary-vs1-{arm}-{hashlib_hex(arm)}" for arm in ARMS
}


def validate_trajectory_id(trajectory_id: str) -> str:
    if not trajectory_id or not isinstance(trajectory_id, str):
        raise ValueError(f"Invalid trajectory ID: {trajectory_id!r}")
    if not _TRAJECTORY_ID_PATTERN.match(trajectory_id):
        raise ValueError(f"Trajectory ID contains invalid characters: {trajectory_id!r}")
    if ".." in trajectory_id:
        raise ValueError(f"Trajectory ID must not contain '..': {trajectory_id!r}")
    return trajectory_id


def create_isolated_workdir(trajectory_id: str, base_dir: str | Path | None = None) -> Path:
    validate_trajectory_id(trajectory_id)
    if base_dir is None:
        base_dir = Path(tempfile.mkdtemp(prefix="benchmark_vs1_"))
    else:
        base_dir = Path(base_dir)
    workdir = base_dir / trajectory_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def verify_isolation(workdir: Path) -> bool:
    """Verify no symlink or path escapes the workdir."""
    if not workdir.exists():
        return False
    resolved_root = workdir.resolve()
    for entry in workdir.rglob("*"):
        if entry.is_symlink():
            target = os.readlink(str(entry))
            if not os.path.isabs(target):
                target = str(entry.parent / target)
            resolved_target = Path(target).resolve()
            if not _is_subpath(resolved_target, resolved_root):
                return False
    return True


def embed_canary(arm: str, envelope: dict) -> dict:
    """Embed the arm's semantic canary into a metadata envelope."""
    envelope["canary"] = CANARIES[arm]
    return envelope


def detect_foreign_canary(text: str, expected_arm: str) -> list[str]:
    """Return foreign canary values found in text (excluding the expected arm's)."""
    found = []
    for arm, canary in CANARIES.items():
        if arm == expected_arm:
            continue
        if canary in text:
            found.append(arm)
    return found


def verify_no_leakage(archives: dict[str, Path]) -> bool:
    """Verify that architecture artifact directories cannot reference each other."""
    arch_paths = {arch: path.resolve() for arch, path in archives.items()}
    for arch, base in arch_paths.items():
        if not base.exists():
            continue
        for entry in base.rglob("*"):
            if not entry.is_file():
                continue
            try:
                content = entry.read_text()
            except Exception:
                continue
            for other_arch, other_base in arch_paths.items():
                if other_arch == arch:
                    continue
                if str(other_base) in content:
                    return False
    return True


def reject_traversal(path: str, base_dir: Path) -> Path:
    """Reject any path that escapes base_dir."""
    candidate = (base_dir / path).resolve()
    if not _is_subpath(candidate, base_dir.resolve()):
        raise ValueError(f"Path traversal detected: {path} escapes {base_dir}")
    return candidate
