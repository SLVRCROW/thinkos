"""Tests for WriteFileAdapter."""

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


class TestWriteFile:
    def test_write_new_file(self, adapter, temp_dir):
        path = os.path.join(temp_dir, "new_file.txt")
        result = adapter.execute({"path": path, "content": "hello world", "call_id": "call_001"}, {})
        assert result["status"] == "ok"
        assert os.path.isfile(path)
        with open(path) as f:
            assert f.read() == "hello world"

    def test_write_overwrite(self, adapter, temp_dir):
        path = os.path.join(temp_dir, "overwrite.txt")
        with open(path, "w") as f:
            f.write("old content")
        result = adapter.execute({"path": path, "content": "new content", "call_id": "call_001"}, {})
        assert result["status"] == "ok"
        with open(path) as f:
            assert f.read() == "new content"

    def test_path_traversal_rejected(self, adapter, temp_dir):
        result = adapter.execute({"path": "../etc/passwd", "content": "evil", "call_id": "call_001"}, {})
        assert result["status"] == "error"
        assert "Path traversal" in result["error"]

    def test_missing_path(self, adapter):
        result = adapter.execute({"content": "hello", "call_id": "call_001"}, {})
        assert result["status"] == "error"
        assert "Missing required parameter" in result["error"]

    def test_missing_content(self, adapter, temp_dir):
        path = os.path.join(temp_dir, "no_content.txt")
        result = adapter.execute({"path": path, "call_id": "call_001"}, {})
        assert result["status"] == "error"
        assert "Missing required parameter" in result["error"]

    def test_auto_create_parent_dirs(self, adapter, temp_dir):
        path = os.path.join(temp_dir, "subdir", "nested", "file.txt")
        result = adapter.execute({"path": path, "content": "nested content", "call_id": "call_001"}, {})
        assert result["status"] == "ok"
        assert os.path.isfile(path)
