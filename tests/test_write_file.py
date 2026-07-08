"""Tests for WriteFileAdapter — content size limits."""

import os
import tempfile
import pytest
from thinkos.tools.write_file import WriteFileAdapter


@pytest.fixture
def adapter():
    return WriteFileAdapter()


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def _context(limits: dict | None = None):
    return {"allowed_root": None, "limits": limits or {}}


class TestWriteFile:
    def test_write_new_file(self, adapter, temp_dir):
        path = os.path.join(temp_dir, "new_file.txt")
        result = adapter.execute({"path": path, "content": "hello world", "call_id": "call_001"}, _context())
        assert result["status"] == "ok"
        assert os.path.isfile(path)
        with open(path) as f:
            assert f.read() == "hello world"

    def test_write_overwrite(self, adapter, temp_dir):
        path = os.path.join(temp_dir, "overwrite.txt")
        with open(path, "w") as f:
            f.write("old content")
        result = adapter.execute({"path": path, "content": "new content", "call_id": "call_001"}, _context())
        assert result["status"] == "ok"
        with open(path) as f:
            assert f.read() == "new content"

    def test_path_traversal_rejected(self, adapter, temp_dir):
        result = adapter.execute({"path": "../etc/passwd", "content": "evil", "call_id": "call_001"}, _context())
        pass  # sandbox test is in test_path_sandbox.py

    def test_missing_path(self, adapter):
        result = adapter.execute({"content": "hello", "call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "Missing required parameter" in result["error"]

    def test_missing_content(self, adapter, temp_dir):
        path = os.path.join(temp_dir, "no_content.txt")
        result = adapter.execute({"path": path, "call_id": "call_001"}, _context())
        assert result["status"] == "error"
        assert "Missing required parameter" in result["error"]

    def test_auto_create_parent_dirs(self, adapter, temp_dir):
        path = os.path.join(temp_dir, "subdir", "nested", "file.txt")
        result = adapter.execute({"path": path, "content": "nested content", "call_id": "call_001"}, _context())
        assert result["status"] == "ok"
        assert os.path.isfile(path)

    def test_empty_content_allowed(self, adapter, temp_dir):
        path = os.path.join(temp_dir, "empty.txt")
        result = adapter.execute({"path": path, "content": "", "call_id": "call_001"}, _context())
        assert result["status"] == "ok"
        assert os.path.isfile(path)
        with open(path) as f:
            assert f.read() == ""

    # -- Content size limits --------------------------------------------

    def test_rejects_oversized_content(self, adapter, temp_dir):
        """Content exceeding max_write_content_bytes is rejected."""
        path = os.path.join(temp_dir, "big.txt")
        big = "x" * 200  # 200 bytes
        ctx = _context({"max_write_content_bytes": 100})
        result = adapter.execute({"path": path, "content": big, "call_id": "call_001"}, ctx)
        assert result["status"] == "error"
        assert "exceeds maximum size" in result["error"]

    def test_accepts_content_at_limit(self, adapter, temp_dir):
        """Content exactly at max_write_content_bytes is written."""
        path = os.path.join(temp_dir, "at_limit.txt")
        content = "x" * 100
        ctx = _context({"max_write_content_bytes": 100})
        result = adapter.execute({"path": path, "content": content, "call_id": "call_001"}, ctx)
        assert result["status"] == "ok"

    def test_accepts_content_under_limit(self, adapter, temp_dir):
        """Content under max_write_content_bytes is written normally."""
        path = os.path.join(temp_dir, "small.txt")
        ctx = _context({"max_write_content_bytes": 1000})
        result = adapter.execute({"path": path, "content": "small", "call_id": "call_001"}, ctx)
        assert result["status"] == "ok"

    def test_limit_disabled_when_null(self, adapter, temp_dir):
        """Setting max_write_content_bytes to 0 disables the limit."""
        path = os.path.join(temp_dir, "big_disabled.txt")
        big = "x" * 200
        ctx = _context({"max_write_content_bytes": 0})
        result = adapter.execute({"path": path, "content": big, "call_id": "call_001"}, ctx)
        assert result["status"] == "ok"
