"""Basic Windows smoke coverage for package import and path sandboxing."""

import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows smoke test",
)


def test_windows_import_and_sandbox_paths(tmp_path):
    import thinkos
    from thinkos.tools.sandbox import SandboxError, resolve_path

    assert thinkos is not None

    allowed = tmp_path / "allowed"
    target = allowed / "nested" / "file.txt"
    target.parent.mkdir(parents=True)
    target.write_text("windows smoke")

    resolved = resolve_path(r"nested\file.txt", str(allowed))
    assert Path(resolved) == target.resolve()

    other_drive = "D:" if allowed.drive.upper() != "D:" else "C:"
    with pytest.raises(SandboxError, match="Access denied"):
        resolve_path(other_drive + r"\outside\file.txt", str(allowed))
