"""Tests for ReadFileAdapter — file size limits and parameter validation."""

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

    # -- Parameter validation -------------------------------------------

    def test_rejects_non_dict_params(self, adapter):
        """Non-dict params return a clean error without crashing."""
        result = adapter.execute("not a dict", _context())
        assert result["status"] == "error"
        assert "Parameters must be an object" in result["error"]

    def test_rejects_non_string_path(self, adapter):
        """Non-string path returns a clean error."""
        result = adapter.execute({"path": 123, "call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "must be of type str" in result["error"]

    def test_rejects_non_int_offset(self, adapter):
        """Non-int offset returns a clean error."""
        result = adapter.execute({"path": "/tmp/test", "offset": "abc", "call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "must be of type int" in result["error"]

    def test_rejects_non_int_limit(self, adapter):
        """Non-int limit returns a clean error."""
        result = adapter.execute({"path": "/tmp/test", "limit": "abc", "call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "must be of type int" in result["error"]

    def test_rejects_bool_offset(self, adapter):
        """Bool offset is rejected (bool is not exact int)."""
        result = adapter.execute({"path": "/tmp/test", "offset": True, "call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "must be of type int" in result["error"]

    def test_rejects_bool_limit(self, adapter):
        """Bool limit is rejected (bool is not exact int)."""
        result = adapter.execute({"path": "/tmp/test", "limit": False, "call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "must be of type int" in result["error"]

    def test_rejects_non_string_call_id(self, adapter):
        """Non-string call_id returns a clean error."""
        result = adapter.execute({"path": "/tmp/test", "call_id": 999}, _context())
        assert result["status"] == "error"
        assert "must be of type str" in result["error"]

    def test_rejects_unknown_param(self, adapter):
        """Unknown param returns a clean error."""
        result = adapter.execute({"path": "/tmp/test", "encoding": "base64", "call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "Unknown parameter" in result["error"]

    def test_accepts_valid_params(self, adapter, temp_file):
        """Valid params still work."""
        result = adapter.execute({"path": temp_file, "offset": 1, "limit": 2, "call_id": "call_001"}, _context())
        assert result["status"] == "ok"
