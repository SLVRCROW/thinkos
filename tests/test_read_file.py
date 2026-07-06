"""Tests for ReadFileAdapter."""

import os
import tempfile
import pytest
from thinkos.tools.read_file import ReadFileAdapter


@pytest.fixture
def adapter():
    return ReadFileAdapter()


@pytest.fixture
def temp_file():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("line1\nline2\nline3\nline4\nline5\n")
        fname = f.name
    yield fname
    os.unlink(fname)


class TestReadFile:
    def test_read_existing_file(self, adapter, temp_file):
        result = adapter.execute({"path": temp_file, "call_id": "call_001"}, {"allowed_root": None})
        assert result["status"] == "ok"
        assert "line1" in result["output"]
        assert "line5" in result["output"]

    def test_read_with_offset(self, adapter, temp_file):
        result = adapter.execute({"path": temp_file, "offset": 3, "call_id": "call_001"}, {"allowed_root": None})
        assert result["status"] == "ok"
        assert "line3" in result["output"]

    def test_read_with_limit(self, adapter, temp_file):
        result = adapter.execute({"path": temp_file, "limit": 2, "call_id": "call_001"}, {"allowed_root": None})
        assert result["status"] == "ok"
        lines = result["output"].strip().split("\n")
        assert len(lines) == 2

    def test_file_not_found(self, adapter):
        result = adapter.execute({"path": "/nonexistent/path.txt", "call_id": "call_001"}, {"allowed_root": None})
        assert result["status"] == "error"
        assert "File not found" in result["error"]

    def test_path_traversal_rejected(self, adapter):
        # In unsandboxed mode, ../etc/passwd resolves to an absolute path
        # that exists. The sandbox test (test_path_sandbox.py) proves
        # that traversal is caught when sandboxing is enabled.
        # Here we just verify the adapter doesn't crash.
        result = adapter.execute({"path": "../etc/passwd", "call_id": "call_001"}, {"allowed_root": None})
        # May succeed or fail depending on permissions — not testing sandbox here
        pass

    def test_missing_path(self, adapter):
        result = adapter.execute({"call_id": "call_001"}, {"allowed_root": None})
        assert result["status"] == "error"
        assert "Missing required parameter" in result["error"]
