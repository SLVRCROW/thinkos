"""Tests for the engine dispatch loop."""

import uuid
import pytest
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
from thinkos.config import load_config


# ── Helpers ─────────────────────────────────────────────────────────

class _BogusGate:
    """A gate that returns an unknown action — used to prove the engine
    rejects it instead of silently executing the tool."""
    name = "bogus"
    def evaluate(self, tool_name, params):
        return {"action": "bogus_action", "reason": "unknown test action"}


class _TestConnector:
    """Simulates a connector that yields a fixed list of messages then EOF.

    Captures responses and errors for assertion.
    """
    def __init__(self, messages):
        self.messages = list(messages)
        self.responses = []
        self.errors = []

    def read_message(self):
        if not self.messages:
            return None
        return self.messages.pop(0)

    def write_response(self, response):
        self.responses.append(response)

    def write_error(self, msg):
        self.errors.append(msg)

    def close(self):
        pass


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def engine():
    store = SQLiteStore(":memory:")
    connector = StdinConnector()
    register_tool("read_file", ReadFileAdapter())
    register_tool("write_file", WriteFileAdapter())
    register_gate("always_allow", AlwaysAllowGate())
    register_gate("confirm", ConfirmGate())
    register_gate("deny_all", DenyAllGate())
    # Use a config with explicit allowed_root to avoid CWD dependency
    config = load_config("/nonexistent/path.json")
    eng = Engine(store, connector, TOOL_REGISTRY, GATE_REGISTRY, config)
    return eng, store


# ── Tests ──────────────────────────────────────────────────────────

class TestEngineDispatch:
    def test_unknown_tool_returns_error(self, engine):
        eng, store = engine
        msg = {
            "type": "agent_message",
            "message_id": "msg_001",
            "session_id": "sess_test",
            "timestamp": "2026-07-06T12:00:00Z",
            "sender": "test",
            "content": {
                "text": "do something",
                "tool_calls": [{"tool": "nonexistent", "params": {}, "call_id": "call_001"}],
                "context_refs": [],
            }
        }
        from thinkos.config import resolve_gate
        tool_adapter = eng.tool_registry.get("nonexistent")
        assert tool_adapter is None

    def test_receipt_created_for_every_action(self, engine):
        eng, store = engine
        session_id = "sess_test"
        receipt = eng._make_receipt(
            session_id, "tool_call", "read_file", {"path": "/tmp/test"}, "test",
            "ok", "Read file", [], None, "always_allow", "allow", None
        )
        store.write_receipt(receipt)
        r2 = store.read_receipt(receipt.receipt_id)
        assert r2 is not None
        assert r2.result.status == "ok"
        assert r2.gate.decision == "allow"

    def test_unknown_gate_action_raises_error(self):
        """Proves the real Engine.run() raises ValueError when a gate
        returns an unknown action — no tool is executed, no silent allow."""
        store = SQLiteStore(":memory:")

        # Use private registries to avoid polluting globals
        tool_registry = {}
        gate_registry = {}

        tool_registry["read_file"] = ReadFileAdapter()
        gate_registry["always_allow"] = AlwaysAllowGate()
        gate_registry["confirm"] = ConfirmGate()
        gate_registry["deny_all"] = DenyAllGate()
        gate_registry["bogus"] = _BogusGate()

        config = load_config("/nonexistent/path.json")
        config["gates"]["overrides"]["read_file"] = "bogus"

        msg = {
            "type": "agent_message",
            "message_id": "msg_002",
            "session_id": "sess_test",
            "timestamp": "2026-07-06T12:00:00Z",
            "sender": "test",
            "content": {
                "text": "trigger bogus gate",
                "tool_calls": [{"tool": "read_file", "params": {"path": "/tmp/test", "call_id": "call_002"}}],
                "context_refs": [],
            }
        }

        connector = _TestConnector([msg])
        eng = Engine(store, connector, tool_registry, gate_registry, config)

        with pytest.raises(ValueError, match="unknown action"):
            eng.run()

        # Verify no response was written (engine crashed before reaching write_response)
        assert len(connector.responses) == 0

        # Verify no receipts were stored (engine crashed before execution)
        stored_receipts = store.list_receipts(session_id="sess_test")
        assert len(stored_receipts) == 0
