"""Tests for ReadFileAdapter — file size limits."""

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


@pytest.fixture
def big_file():
    """Create a file larger than 100 bytes for limit testing."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("x" * 200 + "\n")
        fname = f.name
    yield fname
    os.unlink(fname)


def _context(limits: dict | None = None):
    return {"allowed_root": None, "limits": limits or {}}


class TestReadFile:
    def test_read_existing_file(self, adapter, temp_file):
        result = adapter.execute({"path": temp_file, "call_id": "call_001"}, _context())
        assert result["status"] == "ok"
        assert "line1" in result["output"]
        assert "line5" in result["output"]

    def test_read_with_offset(self, adapter, temp_file):
        result = adapter.execute({"path": temp_file, "offset": 3, "call_id": "call_001"}, _context())
        assert result["status"] == "ok"
        assert "line3" in result["output"]

    def test_read_with_limit(self, adapter, temp_file):
        result = adapter.execute({"path": temp_file, "limit": 2, "call_id": "call_001"}, _context())
        assert result["status"] == "ok"
        lines = result["output"].strip().split("\n")
        assert len(lines) == 2

    def test_file_not_found(self, adapter):
        result = adapter.execute({"path": "/nonexistent/path.txt", "call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "File not found" in result["error"]

    def test_path_traversal_rejected(self, adapter):
        result = adapter.execute({"path": "../etc/passwd", "call_id": "call_001"}, _context())
        pass  # sandbox test is in test_path_sandbox.py

    def test_missing_path(self, adapter):
        result = adapter.execute({"call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "Missing required parameter" in result["error"]

    # -- File size limits -----------------------------------------------

    def test_rejects_oversized_file(self, adapter, big_file):
        """File exceeding max_read_output_bytes is rejected."""
        ctx = _context({"max_read_output_bytes": 100})
        result = adapter.execute({"path": big_file, "call_id": "call_001"}, ctx)
        assert result["status"] == "error"
        assert "exceeds maximum readable size" in result["error"]

    def test_accepts_file_at_limit(self, adapter, temp_file):
        """File exactly at max_read_output_bytes is read."""
        # temp_file is ~30 bytes, set limit to 30
        ctx = _context({"max_read_output_bytes": 30})
        result = adapter.execute({"path": temp_file, "call_id": "call_001"}, ctx)
        assert result["status"] == "ok"

    def test_accepts_file_under_limit(self, adapter, temp_file):
        """File under max_read_output_bytes is read normally."""
        ctx = _context({"max_read_output_bytes": 1000})
        result = adapter.execute({"path": temp_file, "call_id": "call_001"}, ctx)
        assert result["status"] == "ok"

    def test_limit_disabled_when_null(self, adapter, big_file):
        """Setting max_read_output_bytes to 0 disables the limit."""
        ctx = _context({"max_read_output_bytes": 0})
        result = adapter.execute({"path": big_file, "call_id": "call_001"}, ctx)
        assert result["status"] == "ok"
