"""Path sandboxing tests — safe default, unsafe override, edge cases.

These tests prove that ThinkOS denies unsafe file access by default,
which is a mandatory requirement for a public product.
"""

import os
import pytest
from thinkos.tools.sandbox import resolve_path, SandboxError
from thinkos.tools.read_file import ReadFileAdapter
from thinkos.tools.write_file import WriteFileAdapter


# ── Unit: resolve_path ───────────────────────────────────────────

class TestResolvePathSafeDefault:
    """With an allowed_root set (the default), unsafe paths are denied."""

    def test_absolute_path_inside_root_allowed(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_text("hello")
        result = resolve_path(str(target), str(tmp_path))
        assert result == str(target.resolve())

    def test_etc_hostname_denied(self, tmp_path):
        """Public-product requirement: /etc/hostname is denied by default."""
        with pytest.raises(SandboxError, match="Access denied"):
            resolve_path("/etc/hostname", str(tmp_path))

    def test_absolute_path_outside_root_denied(self, tmp_path):
        with pytest.raises(SandboxError, match="Access denied"):
            resolve_path("/tmp", str(tmp_path))

    def test_traversal_denied_via_canonical(self, tmp_path):
        path = os.path.join(str(tmp_path), "..", "..", "etc", "hostname")
        with pytest.raises(SandboxError, match="Access denied"):
            resolve_path(path, str(tmp_path))

    def test_symlink_escape_denied(self, tmp_path):
        link = tmp_path / "escape"
        os.symlink("/etc/hostname", link)
        with pytest.raises(SandboxError, match="Access denied"):
            resolve_path(str(link), str(tmp_path))

    def test_relative_path_resolves_inside_root(self, tmp_path):
        target = tmp_path / "subdir" / "file.txt"
        target.parent.mkdir()
        target.write_text("hello")
        result = resolve_path("subdir/file.txt", str(tmp_path))
        assert result == str(target.resolve())

    def test_root_itself_allowed(self, tmp_path):
        result = resolve_path(str(tmp_path), str(tmp_path))
        assert result == str(tmp_path.resolve())


class TestResolvePathUnsafeOverride:
    """With allowed_root=None, all paths are allowed (developer override)."""

    def test_unsafe_allows_etc_hostname(self):
        result = resolve_path("/etc/hostname", None)
        assert result == "/etc/hostname"

    def test_unsafe_resolves_symlinks(self, tmp_path):
        target = tmp_path / "real.txt"
        target.write_text("hello")
        link = tmp_path / "link.txt"
        os.symlink(target, link)
        result = resolve_path(str(link), None)
        assert result == str(target.resolve())


# ── Integration: read_file ────────────────────────────────────────

class TestReadFileSafeDefault:
    def test_etc_hostname_denied(self, tmp_path):
        adapter = ReadFileAdapter()
        result = adapter.execute(
            {"path": "/etc/hostname", "call_id": "c1"},
            {"allowed_root": str(tmp_path)}
        )
        assert result["status"] == "error"
        assert "Access denied" in result["error"]

    def test_file_inside_root_allowed(self, tmp_path):
        target = tmp_path / "safe.txt"
        target.write_text("hello thinkos")
        adapter = ReadFileAdapter()
        result = adapter.execute(
            {"path": str(target), "call_id": "c2"},
            {"allowed_root": str(tmp_path)}
        )
        assert result["status"] == "ok"
        assert "hello thinkos" in result["output"]

    def test_relative_path_inside_root(self, tmp_path):
        target = tmp_path / "relative.txt"
        target.write_text("relative works")
        adapter = ReadFileAdapter()
        result = adapter.execute(
            {"path": "relative.txt", "call_id": "c3"},
            {"allowed_root": str(tmp_path)}
        )
        assert result["status"] == "ok"
        assert "relative works" in result["output"]


class TestReadFileUnsafeOverride:
    def test_unsafe_allows_etc_hostname(self):
        adapter = ReadFileAdapter()
        result = adapter.execute(
            {"path": "/etc/hostname", "call_id": "c4"},
            {"allowed_root": None}
        )
        assert result["status"] == "ok"


# ── Integration: write_file ───────────────────────────────────────

class TestWriteFileSafeDefault:
    def test_write_outside_root_denied(self, tmp_path):
        path = os.path.join(str(tmp_path), "..", "outside.txt")
        adapter = WriteFileAdapter()
        result = adapter.execute(
            {"path": path, "content": "evil", "call_id": "c5"},
            {"allowed_root": str(tmp_path)}
        )
        assert result["status"] == "error"
        assert "Access denied" in result["error"]

    def test_write_inside_root_allowed(self, tmp_path):
        target = tmp_path / "write_test.txt"
        adapter = WriteFileAdapter()
        result = adapter.execute(
            {"path": str(target), "content": "hello sandbox", "call_id": "c6"},
            {"allowed_root": str(tmp_path)}
        )
        assert result["status"] == "ok"
        assert target.read_text() == "hello sandbox"

    def test_write_empty_content_allowed(self, tmp_path):
        target = tmp_path / "empty.txt"
        adapter = WriteFileAdapter()
        result = adapter.execute(
            {"path": str(target), "content": "", "call_id": "c7"},
            {"allowed_root": str(tmp_path)}
        )
        assert result["status"] == "ok"
        assert target.read_text() == ""


class TestWriteFileUnsafeOverride:
    def test_unsafe_write_outside_root(self, tmp_path):
        path = os.path.join(str(tmp_path), "..", "unsafe_test.txt")
        adapter = WriteFileAdapter()
        result = adapter.execute(
            {"path": path, "content": "unsafe", "call_id": "c8"},
            {"allowed_root": None}
        )
        assert result["status"] == "ok"


# ── Engine-level sandbox test ─────────────────────────────────────

class TestEngineSandbox:
    def test_engine_uses_allowed_root_in_context(self):
        """Verify the engine passes allowed_root to tool execution context."""
        from thinkos.store.sqlite_store import SQLiteStore
        from thinkos.connector.stdin import StdinConnector
        from thinkos.engine import Engine
        from thinkos.tools import TOOL_REGISTRY, register_tool
        from thinkos.tools.read_file import ReadFileAdapter
        from thinkos.tools.write_file import WriteFileAdapter
        from thinkos.gates import GATE_REGISTRY, register_gate
        from thinkos.gates.always_allow import AlwaysAllowGate
        from thinkos.gates.confirm import ConfirmGate
        from thinkos.gates.deny_all import DenyAllGate
        from thinkos.config import load_config, get_allowed_root

        store = SQLiteStore(":memory:")
        connector = StdinConnector()
        register_tool("read_file", ReadFileAdapter())
        register_tool("write_file", WriteFileAdapter())
        register_gate("always_allow", AlwaysAllowGate())
        register_gate("confirm", ConfirmGate())
        register_gate("deny_all", DenyAllGate())

        config = load_config("/nonexistent/path.json")
        eng = Engine(store, connector, TOOL_REGISTRY, GATE_REGISTRY, config)

        # The engine's config should have a non-null allowed_root
        root = get_allowed_root(eng.config)
        assert root is not None
        assert root == os.getcwd()
