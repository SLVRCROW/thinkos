"""Isolation verification for the benchmark harness.

Uses Path.relative_to() for containment checks. Resolves symlink targets
against the symlink's parent directory. Validates trajectory IDs before
directory creation.
"""
from __future__ import annotations
import os
import re
import tempfile
from pathlib import Path
from typing import Any

# Allow only alphanumeric, hyphens, and underscores in trajectory IDs
_TRAJECTORY_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


def validate_trajectory_id(trajectory_id: str) -> str:
    """Validate a trajectory ID. Raises ValueError if invalid."""
    if not trajectory_id or not isinstance(trajectory_id, str):
        raise ValueError(f"Invalid trajectory ID: {trajectory_id!r}")
    if not _TRAJECTORY_ID_PATTERN.match(trajectory_id):
        raise ValueError(f"Trajectory ID contains invalid characters: {trajectory_id!r}")
    if ".." in trajectory_id:
        raise ValueError(f"Trajectory ID must not contain '..': {trajectory_id!r}")
    return trajectory_id


def create_isolated_workdir(trajectory_id: str, base_dir: str | Path | None = None) -> Path:
    """Create a fresh isolated working directory for a trajectory."""
    validate_trajectory_id(trajectory_id)
    if base_dir is None:
        base_dir = Path(tempfile.mkdtemp(prefix="benchmark_g0_"))
    else:
        base_dir = Path(base_dir)
    workdir = base_dir / trajectory_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _is_subpath(child: Path, parent: Path) -> bool:
    """Check whether child is within parent using relative_to."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def verify_isolation(workdir: Path) -> bool:
    """Verify that a workdir contains no cross-trajectory state.

    Uses Path.relative_to() for containment. Resolves symlink targets
    against the symlink's parent directory.
    """
    if not workdir.exists():
        return False
    resolved_root = workdir.resolve()
    for entry in workdir.rglob("*"):
        if entry.is_symlink():
            target = os.readlink(str(entry))
            # Resolve relative symlinks against the symlink's parent
            if not os.path.isabs(target):
                target = str(entry.parent / target)
            resolved_target = Path(target).resolve()
            if not _is_subpath(resolved_target, resolved_root):
                return False
    return True


def verify_no_leakage(archives: dict[str, Path]) -> bool:
    """Verify that architecture artifacts cannot reference each other."""
    arch_paths = {arch: path.resolve() for arch, path in archives.items()}
    for arch, base in arch_paths.items():
        if not base.exists():
            continue
        for entry in base.rglob("*"):
            if not entry.is_file():
                continue
            try:
                content = entry.read_text()
            except (OSError, PermissionError):
                continue
            for other_arch, other_path in arch_paths.items():
                if other_arch == arch:
                    continue
                if str(other_path) in content:
                    return False
    return True


def reject_traversal(path: str, allowed_root: Path) -> bool:
    """Check whether a path attempts traversal outside the allowed root.

    Uses Path.relative_to() for containment. Returns True if safe.
    """
    resolved_root = allowed_root.resolve()
    candidate = (resolved_root / path).resolve()
    return _is_subpath(candidate, resolved_root)


def reject_sibling_prefix(path: str, allowed_root: Path) -> bool:
    """Reject paths that would escape via sibling-prefix tricks.

    E.g., allowed_root=/tmp/foo, path=foobar/../etc should be rejected
    even though foobar shares a prefix with foo.
    """
    resolved_root = allowed_root.resolve()
    candidate = (resolved_root / path).resolve()
    # The candidate must be within resolved_root AND the relative path
    # must not start with '..'
    try:
        rel = candidate.relative_to(resolved_root)
        return not str(rel).startswith("..")
    except ValueError:
        return False
