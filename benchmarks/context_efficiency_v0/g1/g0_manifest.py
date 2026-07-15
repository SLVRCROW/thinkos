"""Frozen G0 file manifest bound to G0_BASE_COMMIT.

Contract §24: 13 frozen G0 files, hashes computed from G0_BASE_COMMIT
9222c0a66a9e786ca9a9f54194d074b42158b783.

The exact manifest is recorded as frozen constants. Tests hash base-commit
objects as raw bytes and separately hash current worktree files as raw bytes
to detect any change.
"""

from __future__ import annotations
import hashlib
import subprocess
from pathlib import Path


G0_BASE_COMMIT = "9222c0a66a9e786ca9a9f54194d074b42158b783"

G0_FROZEN_FILES = [
    "benchmarks/context_efficiency_v0/__init__.py",
    "benchmarks/context_efficiency_v0/__main__.py",
    "benchmarks/context_efficiency_v0/accounting.py",
    "benchmarks/context_efficiency_v0/adapters.py",
    "benchmarks/context_efficiency_v0/baseline.py",
    "benchmarks/context_efficiency_v0/checkpoint.py",
    "benchmarks/context_efficiency_v0/fixtures.py",
    "benchmarks/context_efficiency_v0/isolation.py",
    "benchmarks/context_efficiency_v0/schemas.py",
    "benchmarks/context_efficiency_v0/scorer.py",
    "benchmarks/context_efficiency_v0/tests/__init__.py",
    "benchmarks/context_efficiency_v0/tests/test_g0_harness.py",
    "benchmarks/context_efficiency_v0/README.md",
]

# Frozen manifest: SHA-256 of each G0 file as raw bytes from G0_BASE_COMMIT.
# These are the authoritative reference hashes for G1-A.
FROZEN_MANIFEST: dict[str, str] = {
    "benchmarks/context_efficiency_v0/__init__.py": "b80dfe0e03de8d9c894288524ebc3deb50d6ada4509d9f4d17503616ad53f077",
    "benchmarks/context_efficiency_v0/__main__.py": "b772f04abff8c32e3764d18ec33fdd7ec3bc50679988a347895cc2df67d45611",
    "benchmarks/context_efficiency_v0/accounting.py": "47c8f5c73cef18d8548d5001b55d220dbbe25e7dff8dc8de68621c5f9ef50254",
    "benchmarks/context_efficiency_v0/adapters.py": "b9e2c39b189653c0f12ec0c697fab46547f3add5862192ffe63816a9381e4e13",
    "benchmarks/context_efficiency_v0/baseline.py": "b894fd0d5929fcea4b53884b33dd53dce2e70460b2520b23132be07323185c7b",
    "benchmarks/context_efficiency_v0/checkpoint.py": "abeb973fcc3a165b77ce642ca36da625ec2bb500d1f51497c64c838245a0b43d",
    "benchmarks/context_efficiency_v0/fixtures.py": "b2f14acff1d67d0615e2b39188fd45dd745f3c8c767de1002d74f68b60c44398",
    "benchmarks/context_efficiency_v0/isolation.py": "96964636d2dfbc70e2d2c9b3a41560e10657c1c0f414b967cfd781e9f2f9d6e7",
    "benchmarks/context_efficiency_v0/schemas.py": "ac1bb91de4d86162241132eb935584288cd0ca3412b6010015d8f7fed365a2b0",
    "benchmarks/context_efficiency_v0/scorer.py": "723c96f44ad2bd9f7b98135f0e63629a3e2fad761d268a071eca62523aff00c1",
    "benchmarks/context_efficiency_v0/tests/__init__.py": "7b02f8acd6b24e72ed69f51622d949af62a30f3a57a5fb7d548984003e5911c7",
    "benchmarks/context_efficiency_v0/tests/test_g0_harness.py": "402e1271b6f68de2244040e826866602a868a2e0ac4aa1107209cc27d3fcb306",
    "benchmarks/context_efficiency_v0/README.md": "ca4872fa532ba9796b5be0d1722ee061c39ff10efe4488003820a62d54e7e55f",
}


def _git_show_raw_bytes(rel_path: str, commit: str,
                         repo_root: str | Path | None = None) -> bytes:
    """Get raw file bytes from a git commit."""
    cmd = ["git", "show", f"{commit}:{rel_path}"]
    result = subprocess.run(
        cmd, capture_output=True, cwd=repo_root or "."
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Cannot read {rel_path} from {commit}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def _file_raw_bytes(rel_path: str, repo_root: str | Path) -> bytes:
    """Get raw file bytes from the current worktree."""
    full_path = Path(repo_root) / rel_path
    return full_path.read_bytes()


def compute_frozen_manifest(repo_root: str | Path | None = None) -> dict[str, str]:
    """Compute SHA-256 hashes of all 13 frozen G0 files from G0_BASE_COMMIT.

    Hashes are computed over raw bytes from the git object, not decoded text.
    Returns dict mapping relative path to lowercase hex SHA-256.
    """
    manifest = {}
    for rel_path in G0_FROZEN_FILES:
        raw = _git_show_raw_bytes(rel_path, G0_BASE_COMMIT, repo_root)
        manifest[rel_path] = hashlib.sha256(raw).hexdigest()
    return manifest


def compute_worktree_manifest(repo_root: str | Path) -> dict[str, str]:
    """Compute SHA-256 hashes of frozen G0 files from the current worktree.

    Hashes are computed over raw file bytes. Used to detect local changes.
    """
    manifest = {}
    for rel_path in G0_FROZEN_FILES:
        raw = _file_raw_bytes(rel_path, repo_root)
        manifest[rel_path] = hashlib.sha256(raw).hexdigest()
    return manifest


def verify_frozen_manifest(
    expected: dict[str, str] | None = None,
    repo_root: str | Path | None = None,
) -> list[str]:
    """Verify current frozen-file hashes from G0_BASE_COMMIT match expected.

    If expected is None, uses FROZEN_MANIFEST constants.
    Returns list of mismatch descriptions. Empty list means all match.
    """
    if expected is None:
        expected = FROZEN_MANIFEST
    current = compute_frozen_manifest(repo_root)
    mismatches = []
    for path in G0_FROZEN_FILES:
        if path not in current:
            mismatches.append(f"{path}: missing from current")
        elif path not in expected:
            mismatches.append(f"{path}: missing from expected")
        elif current[path] != expected[path]:
            mismatches.append(
                f"{path}: expected {expected[path][:16]}..., "
                f"got {current[path][:16]}..."
            )
    return mismatches
