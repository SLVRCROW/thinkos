"""Tests for StdinConnector — bounded line reading and oversized line handling."""

import io
import json
import sys
import pytest
from thinkos.connector.stdin import StdinConnector


@pytest.fixture
def connector():
    return StdinConnector(max_line_bytes=1024)  # small limit for testing


def _feed(text: str):
    """Replace sys.stdin with a BytesIO-based object that has a .buffer attr."""
    raw = io.BytesIO(text.encode("utf-8"))

    class _FakeStdin:
        buffer = raw

    sys.stdin = _FakeStdin()  # type: ignore


# ---------------------------------------------------------------------------
# Basic reading
# ---------------------------------------------------------------------------

def test_read_valid_message(connector):
    msg = {"type": "agent_message", "message_id": "msg_001", "session_id": "sess_test",
           "timestamp": "2026-07-06T12:00:00Z", "sender": "test",
           "content": {"text": "hello", "tool_calls": [], "context_refs": []}}
    _feed(json.dumps(msg) + "\n")
    result = connector.read_message()
    assert result is not None
    assert result["type"] == "agent_message"
    assert result["message_id"] == "msg_001"


def test_read_malformed_json(connector, capsys):
    _feed("not json\n")
    result = connector.read_message()
    assert result is None
    captured = capsys.readouterr()
    assert "Malformed JSON" in captured.err


def test_read_eof(connector):
    _feed("")
    result = connector.read_message()
    assert result is None


# ---------------------------------------------------------------------------
# Bounded line reading
# ---------------------------------------------------------------------------

def test_rejects_oversized_line(connector, capsys):
    """Line exceeding max_line_bytes is rejected."""
    long_key = "x" * 1100
    line = json.dumps({"key": long_key}) + "\n"
    _feed(line)
    result = connector.read_message()
    assert result is None
    captured = capsys.readouterr()
    assert "exceeds maximum size" in captured.err


def test_oversized_line_drain_is_stream_safe(connector, capsys):
    """After rejecting an oversized line, the next valid line is readable."""
    long_key = "x" * 1100
    oversized = json.dumps({"key": long_key}) + "\n"
    valid = json.dumps({"type": "agent_message", "message_id": "msg_002",
                        "session_id": "sess_test", "timestamp": "2026-07-06T12:00:00Z",
                        "sender": "test",
                        "content": {"text": "second", "tool_calls": [], "context_refs": []}}) + "\n"
    _feed(oversized + valid)

    # First read — oversized, should be rejected
    r1 = connector.read_message()
    assert r1 is None
    capsys.readouterr()  # clear captured error

    # Second read — should be the valid message
    r2 = connector.read_message()
    assert r2 is not None
    assert r2["message_id"] == "msg_002"


def test_accepts_line_at_limit(connector):
    """Line exactly at max_line_bytes content + newline is accepted."""
    # Build content that produces a line of exactly 1024 bytes + \n
    payload_size = 1024 - len('{"key":""}\n')
    long_val = "a" * payload_size
    line = json.dumps({"key": long_val}) + "\n"
    assert len(line.encode("utf-8")) == 1025  # 1024 content + 1 newline
    _feed(line)
    result = connector.read_message()
    assert result is not None
    assert result["key"] == long_val


def test_accepts_line_under_limit(connector):
    """Normal small line is accepted."""
    _feed('{"msg":"ok"}\n')
    result = connector.read_message()
    assert result is not None
    assert result["msg"] == "ok"


# ---------------------------------------------------------------------------
# UTF-8 handling
# ---------------------------------------------------------------------------

def test_invalid_utf8_does_not_crash(connector, capsys):
    """Invalid UTF-8 bytes are caught and return None."""
    raw = io.BytesIO(b"\xff\xfe\x00\x01\n")

    class _FakeStdin:
        buffer = raw

    sys.stdin = _FakeStdin()  # type: ignore
    result = connector.read_message()
    assert result is None
    captured = capsys.readouterr()
    assert "Invalid UTF-8" in captured.err


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def test_write_response(connector, capsys):
    response = {"type": "agent_response", "status": "ok"}
    connector.write_response(response)
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["type"] == "agent_response"


def test_write_error(connector, capsys):
    connector.write_error("test error")
    captured = capsys.readouterr()
    assert "test error" in captured.err
