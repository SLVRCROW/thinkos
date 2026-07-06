"""Tests for StdinConnector."""

import io
import json
import sys
import pytest
from thinkos.connector.stdin import StdinConnector


@pytest.fixture
def connector():
    return StdinConnector()


def test_read_valid_message(connector):
    msg = {"type": "agent_message", "message_id": "msg_001", "session_id": "sess_test",
           "timestamp": "2026-07-06T12:00:00Z", "sender": "test",
           "content": {"text": "hello", "tool_calls": [], "context_refs": []}}
    sys.stdin = io.StringIO(json.dumps(msg) + "\n")
    result = connector.read_message()
    assert result is not None
    assert result["type"] == "agent_message"
    assert result["message_id"] == "msg_001"


def test_read_malformed_json(connector, capsys):
    sys.stdin = io.StringIO("not json\n")
    result = connector.read_message()
    assert result is None
    captured = capsys.readouterr()
    assert "Malformed JSON" in captured.err


def test_read_eof(connector):
    sys.stdin = io.StringIO("")
    result = connector.read_message()
    assert result is None


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
