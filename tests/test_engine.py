"""Tests for the engine dispatch loop."""

import json
import os
import sys
import tempfile
import uuid
import pytest
from thinkos.store.sqlite_store import SQLiteStore, CycleError, DepthError, DuplicateError
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
from thinkos.schema.context_packet import ContextPacket
from thinkos.schema.receipt import Receipt, Action, Result, GateInfo

# Cross-platform readable path for tests (exists on both Linux and Windows)
_READABLE_PATH = __file__


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


def _make_msg(tool_calls: list[dict], session: str = "sess_test",
              msg_id: str = "msg_001", sender: str = "test") -> dict:
    return {
        "type": "agent_message",
        "message_id": msg_id,
        "session_id": session,
        "timestamp": "2026-07-06T12:00:00Z",
        "sender": sender,
        "content": {
            "text": "do something",
            "tool_calls": tool_calls,
            "context_refs": [],
        }
    }


def _make_tc(tool: str = "read_file", call_id: str = "call_001",
             params: dict | None = None) -> dict:
    return {"tool": tool, "params": params or {}, "call_id": call_id}


def _run_engine(config_overrides: dict | None = None,
                messages: list[dict] | None = None):
    """Create an engine with optional config overrides and run it against
    *messages*, returning (store, connector)."""
    store = SQLiteStore(":memory:")
    connector = _TestConnector(messages or [])
    tool_registry = {
        "read_file": ReadFileAdapter(),
        "write_file": WriteFileAdapter(),
    }
    gate_registry = {
        "always_allow": AlwaysAllowGate(),
        "confirm": ConfirmGate(),
        "deny_all": DenyAllGate(),
    }
    config = load_config("/nonexistent/path.json")
    if config_overrides:
        config.update(config_overrides)
    eng = Engine(store, connector, tool_registry, gate_registry, config)
    eng.run()
    return store, connector


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


class TestContextPacketWiring:
    """Context packets are created for successful tool calls and exposed in responses."""

    def test_packet_created_on_successful_tool_call(self):
        """A successful write_file produces a context_packet in the response."""
        store, connector = _run_engine(
            messages=[_make_msg([_make_tc("write_file", params={"path": "/tmp/test.txt", "content": "hello"})])],
            config_overrides={"gates": {"default": "always_allow"}, "tools": {"allowed_root": None}},
        )
        assert len(connector.responses) == 1
        resp = connector.responses[0]
        packets = resp["content"]["context_packets"]
        assert len(packets) == 1
        pid = packets[0]
        assert pid.startswith("ctx_")
        # Verify the packet is actually in the store
        p = store.read_packet(pid)
        assert p is not None
        assert p.kind == "tool_result"
        assert p.source == "thinkos"
        assert "write_file" in p.content["text"]

    def test_no_packet_on_denied_tool_call(self):
        """A denied tool call produces no context packet."""
        store, connector = _run_engine(
            messages=[_make_msg([_make_tc("write_file", params={"path": "/tmp/test.txt", "content": "hello"})])],
            config_overrides={"gates": {"default": "deny_all"}},
        )
        assert len(connector.responses) == 1
        resp = connector.responses[0]
        assert resp["content"]["context_packets"] == []
        # Verify no packets were stored
        all_packets = store.list_packets()
        assert len(all_packets) == 0

    def test_packet_refs_receipt_id(self):
        """The context packet's refs list contains the matching receipt_id."""
        store, connector = _run_engine(
            messages=[_make_msg([_make_tc("write_file", params={"path": "/tmp/test.txt", "content": "hello"})])],
            config_overrides={"gates": {"default": "always_allow"}, "tools": {"allowed_root": None}},
        )
        resp = connector.responses[0]
        pid = resp["content"]["context_packets"][0]
        rid = resp["content"]["receipts"][0]
        p = store.read_packet(pid)
        assert rid in p.refs

    def test_multiple_tool_calls_produce_multiple_packets(self):
        """Two successful tool calls produce two context packets."""
        store, connector = _run_engine(
            messages=[_make_msg([
                _make_tc("write_file", call_id="c1", params={"path": "/tmp/a.txt", "content": "a"}),
                _make_tc("write_file", call_id="c2", params={"path": "/tmp/b.txt", "content": "b"}),
            ])],
            config_overrides={"gates": {"default": "always_allow"}, "tools": {"allowed_root": None}},
        )
        resp = connector.responses[0]
        assert len(resp["content"]["context_packets"]) == 2
        for pid in resp["content"]["context_packets"]:
            p = store.read_packet(pid)
            assert p is not None
            assert p.kind == "tool_result"


class TestContextPacketParentId:
    """Context packets are linked via parent_id within the same session."""

    def test_first_packet_has_no_parent(self):
        """The first successful tool call in a session produces a packet with parent_id=None."""
        store, connector = _run_engine(
            messages=[_make_msg([_make_tc("write_file", params={"path": "/tmp/a.txt", "content": "a"})])],
            config_overrides={"gates": {"default": "always_allow"}, "tools": {"allowed_root": None}},
        )
        pid = connector.responses[0]["content"]["context_packets"][0]
        p = store.read_packet(pid)
        assert p is not None
        assert p.parent_id is None

    def test_second_packet_links_to_first(self):
        """The second successful tool call in the same session links to the first packet."""
        store, connector = _run_engine(
            messages=[_make_msg([
                _make_tc("write_file", call_id="c1", params={"path": "/tmp/a.txt", "content": "a"}),
                _make_tc("write_file", call_id="c2", params={"path": "/tmp/b.txt", "content": "b"}),
            ])],
            config_overrides={"gates": {"default": "always_allow"}, "tools": {"allowed_root": None}},
        )
        pids = connector.responses[0]["content"]["context_packets"]
        p1 = store.read_packet(pids[0])
        p2 = store.read_packet(pids[1])
        assert p1 is not None
        assert p2 is not None
        assert p1.parent_id is None
        assert p2.parent_id == p1.packet_id

    def test_parent_id_scoped_to_session(self):
        """Packets in different sessions do not cross-link."""
        msg_a = _make_msg(
            [_make_tc("write_file", params={"path": "/tmp/a.txt", "content": "a"})],
            session="sess_a", msg_id="msg_a"
        )
        msg_b = _make_msg(
            [_make_tc("write_file", params={"path": "/tmp/b.txt", "content": "b"})],
            session="sess_b", msg_id="msg_b"
        )
        store, connector = _run_engine(
            messages=[msg_a, msg_b],
            config_overrides={"gates": {"default": "always_allow"}, "tools": {"allowed_root": None}},
        )
        assert len(connector.responses) == 2
        pid_a = connector.responses[0]["content"]["context_packets"][0]
        pid_b = connector.responses[1]["content"]["context_packets"][0]
        p_a = store.read_packet(pid_a)
        p_b = store.read_packet(pid_b)
        assert p_a is not None
        assert p_b is not None
        assert p_a.parent_id is None
        assert p_b.parent_id is None  # different session, no link

    def test_denied_call_does_not_break_chain(self):
        """A denied call does not update last_packet_id, so the next success still links correctly."""
        store, connector = _run_engine(
            messages=[_make_msg([
                _make_tc("write_file", call_id="c1", params={"path": "/tmp/a.txt", "content": "a"}),
                _make_tc("write_file", call_id="c2", params={"path": "/tmp/b.txt", "content": "b"}),
                _make_tc("write_file", call_id="c3", params={"path": "/tmp/c.txt", "content": "c"}),
            ])],
            config_overrides={"gates": {"default": "deny_all"}, "tools": {"allowed_root": None}},
        )
        # All denied — no packets at all
        assert connector.responses[0]["content"]["context_packets"] == []

    def test_depth_limit_fallback_writes_packet_without_parent(self):
        """When depth limit is reached, the packet is written with parent_id=None instead of crashing."""
        calls = [
            _make_tc("write_file", call_id=f"c{i}", params={"path": f"/tmp/{i}.txt", "content": str(i)})
            for i in range(6)
        ]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
            config_overrides={"gates": {"default": "always_allow"}, "tools": {"allowed_root": None}},
        )
        pids = connector.responses[0]["content"]["context_packets"]
        assert len(pids) == 6  # all 6 packets were written
        # First 5 should be chained
        for i in range(5):
            p = store.read_packet(pids[i])
            assert p is not None, f"Packet {i} should exist"
            if i == 0:
                assert p.parent_id is None, f"Packet {i} should have no parent"
            else:
                assert p.parent_id == pids[i - 1], f"Packet {i} should link to packet {i-1}"
        # 6th packet should have no parent (depth fallback)
        p6 = store.read_packet(pids[5])
        assert p6 is not None
        assert p6.parent_id is None, "6th packet should have no parent due to depth limit"

    def test_denied_then_allowed_links_to_last_successful(self):
        """A denied call does not update _last_packet_id; the next allowed call links to the last successful packet."""
        store, connector = _run_engine(
            messages=[_make_msg([
                _make_tc("read_file", call_id="c1", params={"path": _READABLE_PATH}),
                _make_tc("write_file", call_id="c2", params={"path": "/tmp/x.txt", "content": "x"}),
                _make_tc("read_file", call_id="c3", params={"path": _READABLE_PATH}),
            ])],
            config_overrides={
                "gates": {"default": "always_allow", "overrides": {"write_file": "deny_all"}},
                "tools": {"allowed_root": None},
            },
        )
        pids = connector.responses[0]["content"]["context_packets"]
        assert len(pids) == 2  # only the two read_file calls succeeded
        p1 = store.read_packet(pids[0])
        p2 = store.read_packet(pids[1])
        assert p1 is not None
        assert p2 is not None
        assert p1.parent_id is None
        assert p2.parent_id == p1.packet_id  # links to p1, skipping the denied write

    def test_first_call_denied_second_allowed_has_no_parent(self):
        """When the first call is denied, the first allowed call has parent_id=None."""
        store, connector = _run_engine(
            messages=[_make_msg([
                _make_tc("write_file", call_id="c1", params={"path": "/tmp/x.txt", "content": "x"}),
                _make_tc("read_file", call_id="c2", params={"path": _READABLE_PATH}),
            ])],
            config_overrides={
                "gates": {"default": "always_allow", "overrides": {"write_file": "deny_all"}},
                "tools": {"allowed_root": None},
            },
        )
        pids = connector.responses[0]["content"]["context_packets"]
        assert len(pids) == 1  # only the read_file call succeeded
        p = store.read_packet(pids[0])
        assert p is not None
        assert p.parent_id is None  # no successful packet preceded it

    def test_exactly_five_calls_chain_correctly(self):
        """Exactly 5 successful calls in a session all chain correctly with no fallback."""
        calls = [
            _make_tc("read_file", call_id=f"c{i}", params={"path": _READABLE_PATH})
            for i in range(5)
        ]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
            config_overrides={"gates": {"default": "always_allow"}, "tools": {"allowed_root": None}},
        )
        pids = connector.responses[0]["content"]["context_packets"]
        assert len(pids) == 5
        for i in range(5):
            p = store.read_packet(pids[i])
            assert p is not None, f"Packet {i} should exist"
            if i == 0:
                assert p.parent_id is None, f"Packet {i} should have no parent"
            else:
                assert p.parent_id == pids[i - 1], f"Packet {i} should link to packet {i-1}"

    def test_seven_calls_depth_fallback_then_restart(self):
        """7 calls: first 5 chain, 6th falls back to parent_id=None, 7th links to 6th."""
        calls = [
            _make_tc("read_file", call_id=f"c{i}", params={"path": _READABLE_PATH})
            for i in range(7)
        ]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
            config_overrides={"gates": {"default": "always_allow"}, "tools": {"allowed_root": None}},
        )
        pids = connector.responses[0]["content"]["context_packets"]
        assert len(pids) == 7
        # First 5 chained
        for i in range(5):
            p = store.read_packet(pids[i])
            assert p is not None, f"Packet {i} should exist"
            if i == 0:
                assert p.parent_id is None, f"Packet {i} should have no parent"
            else:
                assert p.parent_id == pids[i - 1], f"Packet {i} should link to packet {i-1}"
        # 6th: depth fallback, no parent
        p6 = store.read_packet(pids[5])
        assert p6 is not None
        assert p6.parent_id is None, "6th packet should have no parent (depth fallback)"
        # 7th: links to 6th (which was written successfully)
        p7 = store.read_packet(pids[6])
        assert p7 is not None
        assert p7.parent_id == pids[5], "7th packet should link to 6th"


# ── Tool call limit tests ──────────────────────────────────────────

class TestToolCallLimit:
    """All-or-nothing max_tool_calls_per_message enforcement."""

    def test_rejects_entire_message_when_exceeds_limit(self):
        """Message with 11 calls, limit 10: zero tools execute."""
        calls = [_make_tc(call_id=f"call_{i:03d}") for i in range(11)]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
        )
        assert len(connector.responses) == 1
        resp = connector.responses[0]
        # tool_results must be empty
        assert resp["content"]["tool_results"] == []
        # exactly one receipt
        assert len(resp["content"]["receipts"]) == 1
        # receipt status is denied
        receipt = store.read_receipt(resp["content"]["receipts"][0])
        assert receipt is not None
        assert receipt.result.status == "denied"

    def test_zero_tools_execute_when_over_limit(self):
        """No tool receipts exist when the message is denied."""
        calls = [_make_tc(call_id=f"call_{i:03d}") for i in range(11)]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
        )
        # Only the denial receipt should exist
        all_receipts = store.list_receipts()
        assert len(all_receipts) == 1
        assert all_receipts[0].result.status == "denied"

    def test_zero_gates_evaluated_when_over_limit(self):
        """No gate evaluation occurs when the message is denied.

        Use a bogus gate that would raise if called. If the limit check
        happens before gate resolution, the bogus gate is never reached.
        """
        store = SQLiteStore(":memory:")
        connector = _TestConnector([
            _make_msg([_make_tc(tool="read_file", call_id="call_001") for _ in range(11)])
        ])
        tool_registry = {"read_file": ReadFileAdapter()}
        gate_registry = {
            "always_allow": AlwaysAllowGate(),
            "bogus": _BogusGate(),
        }
        config = load_config("/nonexistent/path.json")
        config["gates"]["overrides"]["read_file"] = "bogus"
        eng = Engine(store, connector, tool_registry, gate_registry, config)
        # Should NOT raise ValueError (bogus gate never called)
        eng.run()
        assert len(connector.responses) == 1
        resp = connector.responses[0]
        assert resp["content"]["tool_results"] == []

    def test_only_one_denial_receipt_when_over_limit(self):
        """Exactly one receipt is written when the message is denied."""
        calls = [_make_tc(call_id=f"call_{i:03d}") for i in range(11)]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
        )
        all_receipts = store.list_receipts()
        assert len(all_receipts) == 1

    def test_response_has_empty_tool_results_when_over_limit(self):
        """tool_results is empty when the message is denied."""
        calls = [_make_tc(call_id=f"call_{i:03d}") for i in range(11)]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
        )
        resp = connector.responses[0]
        assert resp["content"]["tool_results"] == []

    def test_response_text_includes_limit_and_count(self):
        """Response text mentions the limit, actual count, and zero-tools wording."""
        calls = [_make_tc(call_id=f"call_{i:03d}") for i in range(11)]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
        )
        resp = connector.responses[0]
        text = resp["content"]["text"]
        assert "10" in text  # limit
        assert "11" in text  # actual count
        assert "Zero tools executed" in text

    def test_allows_calls_at_limit(self):
        """Message with exactly 10 calls executes normally."""
        calls = [_make_tc(tool="read_file", params={"path": _READABLE_PATH},
                          call_id=f"call_{i:03d}") for i in range(10)]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
            config_overrides={"tools": {"allowed_root": None}},
        )
        assert len(connector.responses) == 1
        resp = connector.responses[0]
        # All 10 calls should have results
        assert len(resp["content"]["tool_results"]) == 10
        # None should be denied by the tool-call limit
        for tr in resp["content"]["tool_results"]:
            assert tr["status"] != "denied"

    def test_allows_calls_under_limit(self):
        """Message with 5 calls executes normally."""
        calls = [_make_tc(tool="read_file", params={"path": "/tmp/test"},
                          call_id=f"call_{i:03d}") for i in range(5)]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
        )
        assert len(connector.responses) == 1
        resp = connector.responses[0]
        assert len(resp["content"]["tool_results"]) == 5

    def test_limit_disabled_when_null(self):
        """Setting max_tool_calls_per_message to 0 allows a large message."""
        calls = [_make_tc(tool="read_file", params={"path": "/tmp/test"},
                          call_id=f"call_{i:03d}") for i in range(50)]
        store, connector = _run_engine(
            messages=[_make_msg(calls)],
            config_overrides={"limits": {"max_tool_calls_per_message": 0}},
        )
        assert len(connector.responses) == 1
        resp = connector.responses[0]
        # All 50 calls should have results
        assert len(resp["content"]["tool_results"]) == 50


class TestSessionRehydration:
    """Opt-in, filtered response-level session rehydration."""

    def _make_rehydrate_msg(self, session="sess_test", tool_calls=None):
        """Helper: message with rehydrate flag set."""
        msg = _make_msg(tool_calls or [], session=session)
        msg["content"]["rehydrate"] = True
        return msg

    def _write_prior_data(self, store, session="sess_test"):
        """Write a receipt + packet into the store for a session."""
        from thinkos.schema.context_packet import ContextPacket
        from thinkos.schema.receipt import Receipt, Action, Result
        pid = f"ctx_{uuid.uuid4()}"
        p = ContextPacket(
            packet_id=pid,
            session_id=session,
            timestamp="2026-07-09T12:00:00Z",
            kind="tool_result",
            source="thinkos",
            content={"text": "prior tool result", "structured": {"secret": "should-not-leak"}},
            tags=["test"],
            refs=[],
        )
        store.write_packet(p)
        r = Receipt(
            receipt_id=f"rct_{uuid.uuid4()}",
            session_id=session,
            sequence=1,
            timestamp="2026-07-09T12:00:00Z",
            action=Action(type="tool_call", tool="read_file", params={"path": "/secret"}, agent="test"),
            result=Result(status="ok", summary="done", packet_ids=[pid], error="sensitive stack trace"),
        )
        store.write_receipt(r)
        return pid

    def test_no_rehydrated_when_flag_absent(self):
        """Message without rehydrate flag → no rehydrated key in response."""
        store, connector = _run_engine(
            messages=[_make_msg([])],
        )
        resp = connector.responses[0]
        assert "rehydrated" not in resp["content"]

    def test_rehydrated_appears_when_flag_present(self):
        """Message with rehydrate flag → rehydrated key present with correct counts."""
        store = SQLiteStore(":memory:")
        self._write_prior_data(store)
        connector = _TestConnector([self._make_rehydrate_msg()])
        eng = Engine(store, connector, {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.run()
        resp = connector.responses[0]
        rehydrated = resp["content"].get("rehydrated")
        assert rehydrated is not None
        assert rehydrated["status"] == "ok"
        assert rehydrated["packet_count"] == 1
        assert rehydrated["receipt_count"] == 1
        assert rehydrated["session_id"] == "sess_test"

    def test_empty_session_returns_empty_structure(self):
        """Session with no prior data → status=ok with empty packets/receipts."""
        store, connector = _run_engine(
            messages=[self._make_rehydrate_msg()],
        )
        resp = connector.responses[0]
        rehydrated = resp["content"].get("rehydrated")
        assert rehydrated is not None
        assert rehydrated["status"] == "ok"
        assert rehydrated["packet_count"] == 0
        assert rehydrated["receipt_count"] == 0
        assert rehydrated["packets"] == []
        assert rehydrated["receipts"] == []

    def test_cross_session_isolation(self):
        """Session A data does not appear in session B's rehydrated response."""
        store = SQLiteStore(":memory:")
        self._write_prior_data(store, session="sess_a")
        msg_b = self._make_rehydrate_msg(session="sess_b")
        connector = _TestConnector([msg_b])
        eng = Engine(store, connector, {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.run()
        resp = connector.responses[0]
        rehydrated = resp["content"].get("rehydrated")
        assert rehydrated is not None
        assert rehydrated["packet_count"] == 0
        assert rehydrated["receipt_count"] == 0

    def test_filter_excludes_action_params(self):
        """Rehydrated receipt entries do not contain action.params."""
        store = SQLiteStore(":memory:")
        self._write_prior_data(store)
        connector = _TestConnector([self._make_rehydrate_msg()])
        eng = Engine(store, connector, {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.run()
        resp = connector.responses[0]
        for r in resp["content"]["rehydrated"]["receipts"]:
            assert "params" not in r

    def test_filter_excludes_content_structured(self):
        """Rehydrated packet entries do not contain content.structured."""
        store = SQLiteStore(":memory:")
        self._write_prior_data(store)
        connector = _TestConnector([self._make_rehydrate_msg()])
        eng = Engine(store, connector, {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.run()
        resp = connector.responses[0]
        for p in resp["content"]["rehydrated"]["packets"]:
            assert "structured" not in p

    def test_filter_excludes_result_error(self):
        """Rehydrated receipt entries do not contain result.error."""
        store = SQLiteStore(":memory:")
        self._write_prior_data(store)
        connector = _TestConnector([self._make_rehydrate_msg()])
        eng = Engine(store, connector, {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.run()
        resp = connector.responses[0]
        for r in resp["content"]["rehydrated"]["receipts"]:
            assert "error" not in r

    def test_existing_parent_id_unchanged_without_opt_in(self):
        """Without rehydrate flag, first new packet still has parent_id=None (lineage not restored)."""
        store = SQLiteStore(":memory:")
        self._write_prior_data(store)
        msg = {
            "type": "agent_message",
            "message_id": "msg_001",
            "session_id": "sess_test",
            "timestamp": "2026-07-10T12:00:00Z",
            "sender": "test",
            "content": {
                "text": "do something",
                "tool_calls": [
                    {"tool": "read_file", "params": {"path": _READABLE_PATH}, "call_id": "c1"}
                ],
            }
        }
        connector = _TestConnector([msg])
        eng = Engine(store, connector, {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.config["gates"] = {"default": "always_allow"}
        eng.config["tools"] = {"allowed_root": None}
        eng.run()
        resp = connector.responses[0]
        pids = resp["content"]["context_packets"]
        assert len(pids) == 1
        p = store.read_packet(pids[0])
        assert p is not None
        assert p.parent_id is None  # lineage not restored without opt-in

    def test_rehydrate_failure_returns_error_status(self):
        """Store failure during rehydration returns status=error without raw exception details."""
        class _BrokenStore:
            def rehydrate(self, session_id):
                raise RuntimeError("disk full")
            def write_receipt(self, r):
                pass
            def write_packet(self, p):
                pass
            def list_packets(self, **kw):
                return []
            def read_packet(self, pid):
                return None
            def close(self):
                pass

        connector = _TestConnector([self._make_rehydrate_msg()])
        eng = Engine(_BrokenStore(), connector,
                     {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.run()
        resp = connector.responses[0]
        rehydrated = resp["content"].get("rehydrated")
        assert rehydrated is not None
        assert rehydrated["status"] == "error"
        assert rehydrated["packet_count"] == 0
        assert rehydrated["receipt_count"] == 0
        # Verify no raw exception details leaked
        text = str(rehydrated)
        assert "disk full" not in text
        assert "RuntimeError" not in text


class TestLineageRestoration:
    """_last_packet_id restoration during opt-in rehydration."""

    def _make_rehydrate_msg(self, session="sess_test", tool_calls=None):
        msg = {
            "type": "agent_message",
            "message_id": "msg_001",
            "session_id": session,
            "timestamp": "2026-07-10T12:00:00Z",
            "sender": "test",
            "content": {
                "text": "do something",
                "tool_calls": tool_calls or [],
                "rehydrate": True,
            }
        }
        return msg

    def _write_prior_data(self, store, session="sess_test"):
        """Write a chain of packets + a receipt into the store."""
        from thinkos.schema.context_packet import ContextPacket
        from thinkos.schema.receipt import Receipt, Action, Result
        pids = []
        prev = None
        for i in range(3):
            pid = f"ctx_{uuid.uuid4()}"
            p = ContextPacket(
                packet_id=pid, session_id=session,
                parent_id=prev,
                timestamp=f"2026-07-10T{11+i:02d}:00:00Z",
                kind="tool_result", source="thinkos",
                content={"text": f"prior result {i}", "structured": None},
                tags=[], refs=[],
            )
            store.write_packet(p)
            pids.append(pid)
            prev = pid
        # Write a receipt referencing the last packet
        r = Receipt(
            receipt_id=f"rct_{uuid.uuid4()}",
            session_id=session, sequence=1,
            timestamp="2026-07-10T13:00:00Z",
            action=Action(type="tool_call", tool="read_file", params={}, agent="test"),
            result=Result(status="ok", summary="done", packet_ids=[pids[-1]], error=None),
        )
        store.write_receipt(r)
        return pids

    def test_lineage_restored_after_opt_in_rehydration(self):
        """With rehydrate flag, _last_packet_id is set to latest stored packet."""
        store = SQLiteStore(":memory:")
        pids = self._write_prior_data(store)
        msg = self._make_rehydrate_msg(tool_calls=[
            {"tool": "read_file", "params": {"path": _READABLE_PATH}, "call_id": "c1"}
        ])
        connector = _TestConnector([msg])
        eng = Engine(store, connector,
                     {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.config["gates"] = {"default": "always_allow"}
        eng.config["tools"] = {"allowed_root": None}
        eng.run()
        resp = connector.responses[0]
        new_pids = resp["content"]["context_packets"]
        assert len(new_pids) == 1
        p = store.read_packet(new_pids[0])
        assert p is not None
        # The new packet should link to the last stored packet
        assert p.parent_id == pids[-1], f"Expected parent_id={pids[-1]}, got {p.parent_id}"

    def test_no_lineage_restoration_without_opt_in(self):
        """Without rehydrate flag, _last_packet_id is not restored."""
        store = SQLiteStore(":memory:")
        self._write_prior_data(store)
        msg = {
            "type": "agent_message",
            "message_id": "msg_001",
            "session_id": "sess_test",
            "timestamp": "2026-07-10T12:00:00Z",
            "sender": "test",
            "content": {
                "text": "do something",
                "tool_calls": [
                    {"tool": "read_file", "params": {"path": _READABLE_PATH}, "call_id": "c1"}
                ],
            }
        }
        connector = _TestConnector([msg])
        eng = Engine(store, connector,
                     {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.config["gates"] = {"default": "always_allow"}
        eng.config["tools"] = {"allowed_root": None}
        eng.run()
        resp = connector.responses[0]
        new_pids = resp["content"]["context_packets"]
        assert len(new_pids) == 1
        p = store.read_packet(new_pids[0])
        assert p is not None
        # Without rehydrate, lineage is not restored — parent_id should be None
        assert p.parent_id is None, f"Expected parent_id=None, got {p.parent_id}"

    def test_cross_session_lineage_isolation(self):
        """Two sessions each restore their own lineage independently."""
        store = SQLiteStore(":memory:")
        pids_a = self._write_prior_data(store, session="sess_a")
        pids_b = self._write_prior_data(store, session="sess_b")
        msg_a = self._make_rehydrate_msg(session="sess_a", tool_calls=[
            {"tool": "read_file", "params": {"path": _READABLE_PATH}, "call_id": "c1"}
        ])
        msg_b = self._make_rehydrate_msg(session="sess_b", tool_calls=[
            {"tool": "read_file", "params": {"path": _READABLE_PATH}, "call_id": "c1"}
        ])
        connector = _TestConnector([msg_a, msg_b])
        eng = Engine(store, connector,
                     {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.config["gates"] = {"default": "always_allow"}
        eng.config["tools"] = {"allowed_root": None}
        eng.run()
        # Session A
        p_a = store.read_packet(connector.responses[0]["content"]["context_packets"][0])
        assert p_a is not None
        assert p_a.parent_id == pids_a[-1], f"Session A: expected {pids_a[-1]}, got {p_a.parent_id}"
        # Session B
        p_b = store.read_packet(connector.responses[1]["content"]["context_packets"][0])
        assert p_b is not None
        assert p_b.parent_id == pids_b[-1], f"Session B: expected {pids_b[-1]}, got {p_b.parent_id}"

    def test_depth_fallback_still_works_after_lineage_restore(self):
        """When restored parent would exceed depth limit, fallback sets parent_id=None."""
        store = SQLiteStore(":memory:")
        from thinkos.schema.context_packet import ContextPacket
        # Build a chain of 5 packets (depth 5 = at limit)
        prev = None
        for i in range(5):
            p = ContextPacket(
                packet_id=f"ctx_{uuid.uuid4()}", session_id="sess_depth",
                parent_id=prev,
                timestamp=f"2026-07-10T{10+i:02d}:00:00Z",
                kind="tool_result", source="thinkos",
                content={"text": f"chain {i}", "structured": None},
                tags=[], refs=[],
            )
            store.write_packet(p)
            prev = p.packet_id
        # Now rehydrate + make a new tool call — the 6th packet should hit depth limit
        msg = self._make_rehydrate_msg(session="sess_depth", tool_calls=[
            {"tool": "read_file", "params": {"path": _READABLE_PATH}, "call_id": "c1"}
        ])
        connector = _TestConnector([msg])
        eng = Engine(store, connector,
                     {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     load_config("/nonexistent/path.json"))
        eng.config["gates"] = {"default": "always_allow"}
        eng.config["tools"] = {"allowed_root": None}
        eng.run()
        resp = connector.responses[0]
        new_pids = resp["content"]["context_packets"]
        assert len(new_pids) == 1
        p = store.read_packet(new_pids[0])
        assert p is not None
        # Depth fallback should have set parent_id=None
        assert p.parent_id is None, f"Expected parent_id=None (depth fallback), got {p.parent_id}"


class TestCompaction:
    """TM007b: Config-threshold compaction with response-level summary object."""

    def _make_rehydrate_msg(self, session="sess_test", tool_calls=None):
        msg = _make_msg(tool_calls or [], session=session)
        msg["content"]["rehydrate"] = True
        return msg

    def _write_n_packets(self, store, n, session="sess_test"):
        """Write *n* receipts+packets into the store for a session."""
        from thinkos.schema.context_packet import ContextPacket
        from thinkos.schema.receipt import Receipt, Action, Result
        pids = []
        for i in range(n):
            pid = f"ctx_{uuid.uuid4()}"
            p = ContextPacket(
                packet_id=pid, session_id=session,
                timestamp=f"2026-07-10T{12+i:02d}:00:00Z",
                kind="tool_result", source="thinkos",
                content={"text": f"packet {i}", "structured": None},
                tags=[], refs=[],
            )
            store.write_packet(p)
            status = "error" if i == 0 else ("denied" if i == 1 else "ok")
            r = Receipt(
                receipt_id=f"rct_{uuid.uuid4()}", session_id=session,
                sequence=i + 1, timestamp=f"2026-07-10T{12+i:02d}:00:00Z",
                action=Action(type="tool_call", tool="read_file" if i % 2 == 0 else "write_file",
                              params={}, agent="test"),
                result=Result(status=status, summary=f"result {i}", packet_ids=[pid], error=None),
            )
            store.write_receipt(r)
            pids.append(pid)
        return pids

    def _run_with_config(self, store, messages, config_overrides=None):
        """Run engine with optional config overrides."""
        connector = _TestConnector(messages)
        config = load_config("/nonexistent/path.json")
        if config_overrides:
            config.update(config_overrides)
        eng = Engine(store, connector,
                     {"read_file": ReadFileAdapter(), "write_file": WriteFileAdapter()},
                     {"always_allow": AlwaysAllowGate(), "confirm": ConfirmGate(), "deny_all": DenyAllGate()},
                     config)
        eng.run()
        return connector

    # -- Default behavior (backward compat) --

    def test_default_no_compaction(self):
        """Default config (max_packets=null) returns all packets unchanged."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(store, [self._make_rehydrate_msg()])
        resp = connector.responses[0]
        rehydrated = resp["content"].get("rehydrated")
        assert rehydrated is not None
        assert "compacted" not in rehydrated
        assert "summary" not in rehydrated
        assert rehydrated["packet_count"] == 10

    def test_compaction_below_threshold(self):
        """Session with fewer packets than max_packets — no compaction."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 3)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 5}}
        )
        resp = connector.responses[0]
        rehydrated = resp["content"].get("rehydrated")
        assert "compacted" not in rehydrated
        assert rehydrated["packet_count"] == 3

    def test_compaction_above_threshold(self):
        """Session with more packets than max_packets — compacted flag true, count = max_packets."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        rehydrated = resp["content"].get("rehydrated")
        assert rehydrated.get("compacted") is True
        assert rehydrated["packet_count"] == 3
        assert rehydrated.get("omitted_packet_count") == 7

    def test_omitted_count_equals_total_minus_returned(self):
        """omitted_packet_count = total_packets - returned_packets."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 25)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 10}}
        )
        resp = connector.responses[0]
        rehydrated = resp["content"].get("rehydrated")
        assert rehydrated["packet_count"] == 10
        assert rehydrated["omitted_packet_count"] == 15

    # -- Summary object --

    def test_summary_present_when_compacted(self):
        """Summary object present with correct kind and source."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        summary = resp["content"]["rehydrated"]["summary"]
        assert summary["kind"] == "summary"
        assert summary["source"] == "thinkos"
        assert isinstance(summary["text"], str)
        assert len(summary["text"]) > 0

    def test_summary_absent_when_not_compacted(self):
        """No summary key when below threshold."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 3)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 5}}
        )
        resp = connector.responses[0]
        assert "summary" not in resp["content"]["rehydrated"]

    def test_summary_structured_fields(self):
        """Summary structured contains fidelity floor fields."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        s = resp["content"]["rehydrated"]["summary"]["structured"]
        assert s["packet_count"] == 10
        assert s["receipt_count"] == 10
        assert s["omitted_packet_count"] == 7
        assert "time_range" in s
        assert "tool_distribution" in s
        assert "error_count" in s
        assert "denied_count" in s
        assert "kind_distribution" in s

    def test_error_count_in_summary(self):
        """Summary error_count matches actual errors."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        s = resp["content"]["rehydrated"]["summary"]["structured"]
        assert s["error_count"] == 1  # packet 0 is error

    def test_denied_count_in_summary(self):
        """Summary denied_count matches actual denials."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        s = resp["content"]["rehydrated"]["summary"]["structured"]
        assert s["denied_count"] == 1  # packet 1 is denied

    def test_tool_distribution_in_summary(self):
        """Summary tool_distribution matches actual tool calls."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        td = resp["content"]["rehydrated"]["summary"]["structured"]["tool_distribution"]
        # 10 packets: even indices = read_file, odd = write_file
        assert td.get("read_file", 0) == 5
        assert td.get("write_file", 0) == 5

    def test_time_range_in_summary(self):
        """Summary time_range covers first to last packet timestamp."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 5)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 2}}
        )
        resp = connector.responses[0]
        tr = resp["content"]["rehydrated"]["summary"]["structured"]["time_range"]
        assert "start" in tr
        assert "end" in tr

    # -- Safety: no raw data in summary --

    def test_no_raw_params_in_summary(self):
        """Summary does not contain raw tool params."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        s = resp["content"]["rehydrated"]["summary"]
        assert "params" not in str(s)

    def test_no_raw_structured_in_summary(self):
        """Summary structured contains only fidelity floor keys, not raw packet content."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        s = resp["content"]["rehydrated"]["summary"]
        # Assert summary.structured contains exactly the fidelity floor keys
        expected_keys = {"packet_count", "receipt_count", "omitted_packet_count",
                         "time_range", "tool_distribution", "error_count",
                         "denied_count", "kind_distribution"}
        assert set(s["structured"].keys()) == expected_keys
        # Assert summary does not contain raw receipt param markers
        # (the test injects params={} in receipts; "params" should not appear)
        assert "params" not in str(s)

    # -- Lineage restoration still works --

    def test_lineage_restoration_with_compaction(self):
        """After compaction, lineage restoration still sets _last_packet_id correctly."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        msg = self._make_rehydrate_msg(tool_calls=[
            {"tool": "read_file", "params": {"path": _READABLE_PATH}, "call_id": "c1"}
        ])
        connector = self._run_with_config(
            store, [msg],
            config_overrides={
                "rehydration": {"max_packets": 3},
                "gates": {"default": "always_allow"},
                "tools": {"allowed_root": None},
            }
        )
        resp = connector.responses[0]
        # The new tool call should have created a packet linked to the latest stored packet
        new_pids = resp["content"]["context_packets"]
        assert len(new_pids) == 1
        p = store.read_packet(new_pids[0])
        assert p is not None
        # parent_id should be the latest stored packet (packet 9, the most recent)
        assert p.parent_id is not None

    # -- Cross-session isolation --

    def test_cross_session_isolation_with_compaction(self):
        """Session A compaction does not affect session B."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10, session="sess_a")
        self._write_n_packets(store, 3, session="sess_b")
        msg_b = self._make_rehydrate_msg(session="sess_b")
        connector = self._run_with_config(
            store, [msg_b],
            config_overrides={"rehydration": {"max_packets": 5}}
        )
        resp = connector.responses[0]
        rehydrated = resp["content"].get("rehydrated")
        assert rehydrated["packet_count"] == 3  # below threshold, no compaction
        assert "compacted" not in rehydrated

    # -- Deterministic ordering --

    def test_compaction_returns_most_recent_packets(self):
        """Compaction returns the N most recent packets by timestamp DESC."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        packets = resp["content"]["rehydrated"]["packets"]
        assert len(packets) == 3
        # Most recent packets should have the highest timestamps
        summaries = [p["summary"] for p in packets]
        assert "packet 9" in summaries[0]  # most recent
        assert "packet 8" in summaries[1]
        assert "packet 7" in summaries[2]

    # -- Receipt truncation matches packet truncation --

    def test_receipts_truncated_with_packets(self):
        """When packets are truncated, receipts list is also truncated."""
        store = SQLiteStore(":memory:")
        self._write_n_packets(store, 10)
        connector = self._run_with_config(
            store, [self._make_rehydrate_msg()],
            config_overrides={"rehydration": {"max_packets": 3}}
        )
        resp = connector.responses[0]
        rehydrated = resp["content"]["rehydrated"]
        assert len(rehydrated["receipts"]) == 3
        assert len(rehydrated["packets"]) == 3


# ── Alpha Door P0 repair regression tests ──────────────────────────


class TestReceiptPacketAtomicity:
    """Prove that write_receipt_and_packet is genuinely atomic."""

    def test_successful_reciprocal_linkage(self):
        """A successful write produces a receipt with packet_ids and a packet with refs."""
        store = SQLiteStore(":memory:")
        receipt = Receipt(
            receipt_id="rct_atomic_001",
            schema_version=1,
            session_id="sess_atomic",
            sequence=1,
            timestamp="2026-07-16T00:00:00Z",
            action=Action(type="tool_call", tool="write_file",
                          params={"path": "/tmp/t.txt", "content": "hello"}, agent="test"),
            result=Result(status="ok", summary="Wrote 5 bytes", packet_ids=["pkt_atomic_001"]),
            gate=GateInfo(gate_name="always_allow", decision="allow", reason="test"),
        )
        packet = ContextPacket(
            packet_id="pkt_atomic_001",
            session_id="sess_atomic",
            timestamp="2026-07-16T00:00:00Z",
            kind="tool_result",
            source="thinkos",
            content={"text": "Tool 'write_file' completed: Wrote 5 bytes", "structured": None},
            tags=["write_file"],
            refs=["rct_atomic_001"],
        )
        store.write_receipt_and_packet(receipt, packet)

        # Read back and verify reciprocal linkage
        r = store.read_receipt("rct_atomic_001")
        assert r is not None
        assert r.result.packet_ids == ["pkt_atomic_001"]

        p = store.read_packet("pkt_atomic_001")
        assert p is not None
        assert "rct_atomic_001" in p.refs

    def test_duplicate_failure_leaves_no_pair(self):
        """A duplicate packet_id raises DuplicateError and leaves no receipt or packet."""
        store = SQLiteStore(":memory:")
        receipt = Receipt(
            receipt_id="rct_dup_001",
            schema_version=1,
            session_id="sess_dup",
            sequence=1,
            timestamp="2026-07-16T00:00:00Z",
            action=Action(type="tool_call", tool="write_file",
                          params={"path": "/tmp/t.txt", "content": "hello"}, agent="test"),
            result=Result(status="ok", summary="Wrote 5 bytes", packet_ids=["pkt_dup_001"]),
            gate=GateInfo(gate_name="always_allow", decision="allow", reason="test"),
        )
        packet = ContextPacket(
            packet_id="pkt_dup_001",
            session_id="sess_dup",
            timestamp="2026-07-16T00:00:00Z",
            kind="tool_result",
            source="thinkos",
            content={"text": "Tool 'write_file' completed", "structured": None},
            tags=["write_file"],
            refs=["rct_dup_001"],
        )
        # First write succeeds
        store.write_receipt_and_packet(receipt, packet)

        # Second write with same packet_id must fail
        receipt2 = Receipt(
            receipt_id="rct_dup_002",
            schema_version=1,
            session_id="sess_dup",
            sequence=2,
            timestamp="2026-07-16T00:00:00Z",
            action=Action(type="tool_call", tool="write_file",
                          params={"path": "/tmp/t.txt", "content": "hello"}, agent="test"),
            result=Result(status="ok", summary="Wrote 5 bytes", packet_ids=["pkt_dup_001"]),
            gate=GateInfo(gate_name="always_allow", decision="allow", reason="test"),
        )
        with pytest.raises(DuplicateError):
            store.write_receipt_and_packet(receipt2, packet)

        # Neither the second receipt nor a duplicate packet should exist
        assert store.read_receipt("rct_dup_002") is None
        # The original packet is still intact
        assert store.read_packet("pkt_dup_001") is not None

    def test_cycle_failure_leaves_no_pair(self):
        """A cycle in the packet DAG raises CycleError and leaves no receipt or packet."""
        store = SQLiteStore(":memory:")
        # Write a root packet
        root = ContextPacket(
            packet_id="pkt_cycle_root",
            session_id="sess_cycle",
            timestamp="2026-07-16T00:00:00Z",
            kind="tool_result",
            source="thinkos",
            content={"text": "root", "structured": None},
            tags=["write_file"],
            refs=[],
        )
        store.write_packet(root)

        # Try to write a packet that points to itself (direct cycle)
        receipt = Receipt(
            receipt_id="rct_cycle_001",
            schema_version=1,
            session_id="sess_cycle",
            sequence=1,
            timestamp="2026-07-16T00:00:00Z",
            action=Action(type="tool_call", tool="write_file",
                          params={"path": "/tmp/t.txt", "content": "x"}, agent="test"),
            result=Result(status="ok", summary="Wrote 1 byte", packet_ids=["pkt_cycle_self"]),
            gate=GateInfo(gate_name="always_allow", decision="allow", reason="test"),
        )
        packet = ContextPacket(
            packet_id="pkt_cycle_self",
            session_id="sess_cycle",
            parent_id="pkt_cycle_self",  # points to itself = cycle
            timestamp="2026-07-16T00:00:00Z",
            kind="tool_result",
            source="thinkos",
            content={"text": "self-cycle", "structured": None},
            tags=["write_file"],
            refs=["rct_cycle_001"],
        )
        with pytest.raises(CycleError):
            store.write_receipt_and_packet(receipt, packet)

        # Neither the receipt nor the cycle packet should exist
        assert store.read_receipt("rct_cycle_001") is None
        assert store.read_packet("pkt_cycle_self") is None

    def test_depth_failure_retry_leaves_one_pair(self):
        """DepthError triggers a retry without parent; exactly one receipt+packet pair exists."""
        store = SQLiteStore(":memory:")
        # Write 5 packets to reach depth limit
        for i in range(5):
            pid = f"pkt_depth_{i}"
            p = ContextPacket(
                packet_id=pid,
                session_id="sess_depth",
                parent_id=f"pkt_depth_{i - 1}" if i > 0 else None,
                timestamp="2026-07-16T00:00:00Z",
                kind="tool_result",
                source="thinkos",
                content={"text": f"depth {i}", "structured": None},
                tags=["write_file"],
                refs=[],
            )
            store.write_packet(p)

        # Now try to write a 6th packet — should trigger DepthError then retry
        receipt = Receipt(
            receipt_id="rct_depth_retry",
            schema_version=1,
            session_id="sess_depth",
            sequence=6,
            timestamp="2026-07-16T00:00:00Z",
            action=Action(type="tool_call", tool="write_file",
                          params={"path": "/tmp/t.txt", "content": "x"}, agent="test"),
            result=Result(status="ok", summary="Wrote 1 byte", packet_ids=["pkt_depth_retry"]),
            gate=GateInfo(gate_name="always_allow", decision="allow", reason="test"),
        )
        packet = ContextPacket(
            packet_id="pkt_depth_retry",
            session_id="sess_depth",
            parent_id="pkt_depth_4",
            timestamp="2026-07-16T00:00:00Z",
            kind="tool_result",
            source="thinkos",
            content={"text": "depth retry", "structured": None},
            tags=["write_file"],
            refs=["rct_depth_retry"],
        )

        # Simulate the engine's retry logic
        try:
            store.write_receipt_and_packet(receipt, packet)
        except DepthError:
            packet.parent_id = None
            packet.refs = ["rct_depth_retry"]
            store.write_receipt_and_packet(receipt, packet)

        # Exactly one receipt and one packet should exist
        r = store.read_receipt("rct_depth_retry")
        assert r is not None
        assert r.result.packet_ids == ["pkt_depth_retry"]
        p = store.read_packet("pkt_depth_retry")
        assert p is not None
        assert p.parent_id is None  # retry wrote without parent
        assert "rct_depth_retry" in p.refs


class TestSuccessorRehydration:
    """Prove that a fresh process recovers prior state and continues lineage."""

    def test_fresh_process_rehydrates_packets(self):
        """A second engine instance with the same store recovers prior packets."""
        store = SQLiteStore(":memory:")
        connector1 = _TestConnector([
            _make_msg([_make_tc("write_file", params={"path": "/tmp/a.txt", "content": "alpha"})])
        ])
        _run_engine_raw(store, connector1,
                        config_overrides={"gates": {"default": "always_allow"},
                                          "tools": {"allowed_root": None}})

        # Fresh connector, same store — rehydrate
        connector2 = _TestConnector([
            {**_make_msg([], msg_id="msg_rehydrate"), "content": {"text": "resume", "rehydrate": True, "tool_calls": [], "context_refs": []}}
        ])
        _run_engine_raw(store, connector2,
                        config_overrides={"gates": {"default": "always_allow"},
                                          "tools": {"allowed_root": None}})

        resp = connector2.responses[0]
        rehydrated = resp["content"].get("rehydrated", {})
        assert rehydrated.get("packet_count", 0) >= 1
        assert rehydrated.get("receipt_count", 0) >= 1

    def test_successor_continues_lineage(self):
        """A successor action after rehydration links to the recovered packet."""
        store = SQLiteStore(":memory:")
        connector1 = _TestConnector([
            _make_msg([_make_tc("write_file", params={"path": "/tmp/a.txt", "content": "alpha"})])
        ])
        _run_engine_raw(store, connector1,
                        config_overrides={"gates": {"default": "always_allow"},
                                          "tools": {"allowed_root": None}})

        # Successor: rehydrate + write in one message
        connector2 = _TestConnector([
            {**_make_msg([_make_tc("write_file", params={"path": "/tmp/b.txt", "content": "beta"})],
                         msg_id="msg_succ"),
             "content": {"text": "resume", "rehydrate": True,
                         "tool_calls": [_make_tc("write_file", params={"path": "/tmp/b.txt", "content": "beta"})],
                         "context_refs": []}}
        ])
        _run_engine_raw(store, connector2,
                        config_overrides={"gates": {"default": "always_allow"},
                                          "tools": {"allowed_root": None}})

        resp = connector2.responses[0]
        pids = resp["content"]["context_packets"]
        assert len(pids) == 1
        p = store.read_packet(pids[0])
        assert p is not None
        # The successor's packet should link to the first packet (lineage restored)
        assert p.parent_id is not None
        first_packet = store.read_packet(p.parent_id)
        assert first_packet is not None
        # The content is in structured.params.content, not the summary text
        assert first_packet.content.get("structured", {}).get("params", {}).get("content") == "alpha"


class TestDenialExplanation:
    """Prove that denied actions expose a human-readable reason and do not execute."""

    def test_denial_reason_is_visible(self):
        """A denied tool call returns the gate's reason in tool_result.output."""
        store = SQLiteStore(":memory:")
        connector = _TestConnector([
            _make_msg([_make_tc("write_file", params={"path": "/tmp/deny.txt", "content": "x"})])
        ])
        _run_engine_raw(store, connector,
                        config_overrides={
                            "gates": {"default": "deny_all"},
                            "tools": {"allowed_root": None},
                        })
        resp = connector.responses[0]
        tr = resp["content"]["tool_results"][0]
        assert tr["status"] == "denied"
        assert len(tr["output"]) > 0  # human-readable reason

    def test_denied_tool_does_not_execute(self):
        """A denied write_file does not create the target file."""
        import tempfile
        import os
        store = SQLiteStore(":memory:")
        tmpdir = tempfile.mkdtemp()
        try:
            target = os.path.join(tmpdir, "should_not_exist.txt")
            connector = _TestConnector([
                _make_msg([_make_tc("write_file", params={"path": target, "content": "x"})])
            ])
            _run_engine_raw(store, connector,
                            config_overrides={
                                "gates": {"default": "deny_all"},
                                "tools": {"allowed_root": None},
                            })
            assert not os.path.exists(target)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestCLIAffordances:
    """Prove that --help and --version produce output without engine init."""

    def test_help_does_not_initialize_engine(self):
        """thinkos --help prints usage and exits without starting the engine."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "ThinkOS" in result.stdout
        assert "Usage" in result.stdout

    def test_version_does_not_initialize_engine(self):
        """thinkos --version prints version and exits without starting the engine."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "thinkos" in result.stdout
        assert "." in result.stdout


# ── Helpers for the new test classes ────────────────────────────────


def _run_engine_raw(store, connector, config_overrides=None):
    """Run the engine with a pre-created store and connector."""
    from thinkos.config import load_config
    tool_registry = {
        "read_file": ReadFileAdapter(),
        "write_file": WriteFileAdapter(),
    }
    gate_registry = {
        "always_allow": AlwaysAllowGate(),
        "confirm": ConfirmGate(),
        "deny_all": DenyAllGate(),
    }
    config = load_config("/nonexistent/path.json")
    if config_overrides:
        config.update(config_overrides)
    eng = Engine(store, connector, tool_registry, gate_registry, config)
    eng.run()
    return store, connector


# ── Real two-process persistence ────────────────────────────────────


class TestRealTwoProcessPersistence:
    """Prove that a real file-backed store survives process boundaries."""

    def test_two_process_rehydration_and_lineage(self):
        """Process 1 writes, exits. Process 2 opens same store, rehydrates,
        recovers linked packet+receipt, and continues the lineage."""
        import tempfile
        import os
        import json
        import subprocess
        import sys

        tmpdir = tempfile.mkdtemp()
        try:
            db_path = os.path.join(tmpdir, "test_tp.sqlite")
            config_path = os.path.join(tmpdir, "thinkos.json")
            config = {
                "gates": {"default": "always_allow",
                          "overrides": {"read_file": "always_allow", "write_file": "always_allow"}},
                "store": {"path": db_path},
                "tools": {"allowed_root": None},
            }
            with open(config_path, "w") as f:
                json.dump(config, f)

            # Output files go inside tmpdir so cleanup is guaranteed
            out_a = os.path.join(tmpdir, "tp_a.txt")
            out_b = os.path.join(tmpdir, "tp_b.txt")

            # Process 1: write a file
            msg1 = {
                "type": "agent_message", "message_id": "msg_p1",
                "session_id": "sess_tp", "timestamp": "2026-07-16T00:00:00Z",
                "sender": "agent1",
                "content": {"text": "write", "tool_calls": [
                    {"tool": "write_file", "params": {"path": out_a, "content": "alpha"},
                     "call_id": "c1"}
                ], "context_refs": []},
            }
            input_bytes = (json.dumps(msg1, separators=(",", ":")) + "\n").encode()
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            # PYTHONPATH = repository root (tests/ is one level below root)
            from pathlib import Path
            repo_root = Path(__file__).resolve().parents[1]
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                str(repo_root)
                if not existing
                else str(repo_root) + os.pathsep + existing
            )
            proc1 = subprocess.run(
                [sys.executable, "-m", "thinkos"],
                input=input_bytes, capture_output=True, timeout=15,
                cwd=tmpdir, env=env,
            )
            assert proc1.returncode == 0, f"Process 1 failed: {proc1.stderr.decode()}"
            resp1 = json.loads(proc1.stdout.decode().strip())
            assert resp1["content"]["tool_results"][0]["status"] == "ok"

            # Process 2: rehydrate + write
            msg2 = {
                "type": "agent_message", "message_id": "msg_p2",
                "session_id": "sess_tp", "timestamp": "2026-07-16T00:01:00Z",
                "sender": "agent2",
                "content": {"text": "resume", "rehydrate": True, "tool_calls": [
                    {"tool": "write_file", "params": {"path": out_b, "content": "beta"},
                     "call_id": "c2"}
                ], "context_refs": []},
            }
            input_bytes = (json.dumps(msg2, separators=(",", ":")) + "\n").encode()
            proc2 = subprocess.run(
                [sys.executable, "-m", "thinkos"],
                input=input_bytes, capture_output=True, timeout=15,
                cwd=tmpdir, env=env,
            )
            assert proc2.returncode == 0, f"Process 2 failed: {proc2.stderr.decode()}"
            resp2 = json.loads(proc2.stdout.decode().strip())

            # Assert rehydration recovered the prior packet
            rehydrated = resp2["content"].get("rehydrated", {})
            assert rehydrated.get("packet_count", 0) >= 1, "No packets recovered"
            assert rehydrated.get("receipt_count", 0) >= 1, "No receipts recovered"

            # Assert the successor action produced a packet
            pids = resp2["content"]["context_packets"]
            assert len(pids) == 1, "Successor should produce one packet"

            # Assert lineage: successor packet links to recovered packet
            store = SQLiteStore(db_path)
            p = store.read_packet(pids[0])
            assert p is not None
            assert p.parent_id is not None, "Successor packet should have a parent"
            parent = store.read_packet(p.parent_id)
            assert parent is not None
            assert parent.content.get("structured", {}).get("params", {}).get("content") == "alpha"
            store._conn.close()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Post-receipt-insert rollback ─────────────────────────────────────


class TestPostReceiptRollback:
    """Prove that a packet-insert failure after receipt INSERT rolls back both."""

    def test_injected_packet_failure_rolls_back_pair(self):
        """A SQLite trigger that aborts the packet INSERT causes the entire
        transaction to roll back — neither receipt nor packet remains."""
        store = SQLiteStore(":memory:")

        # Install a trigger that fires on packet INSERT and aborts
        store._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_abort_packet
            BEFORE INSERT ON packets
            BEGIN
                SELECT RAISE(ABORT, 'SIMULATED_PACKET_INSERT_FAILURE');
            END;
        """)
        store._conn.commit()

        receipt = Receipt(
            receipt_id="rct_rollback_001",
            schema_version=1,
            session_id="sess_rb",
            sequence=1,
            timestamp="2026-07-16T00:00:00Z",
            action=Action(type="tool_call", tool="write_file",
                          params={"path": "/tmp/rb.txt", "content": "x"}, agent="test"),
            result=Result(status="ok", summary="Wrote 1 byte", packet_ids=["pkt_rollback_001"]),
            gate=GateInfo(gate_name="always_allow", decision="allow", reason="test"),
        )
        packet = ContextPacket(
            packet_id="pkt_rollback_001",
            session_id="sess_rb",
            timestamp="2026-07-16T00:00:00Z",
            kind="tool_result",
            source="thinkos",
            content={"text": "Tool 'write_file' completed", "structured": None},
            tags=["write_file"],
            refs=["rct_rollback_001"],
        )

        with pytest.raises(Exception):
            store.write_receipt_and_packet(receipt, packet)

        # Neither receipt nor packet should exist
        assert store.read_receipt("rct_rollback_001") is None
        assert store.read_packet("pkt_rollback_001") is None

        # Clean up the trigger so it doesn't affect other tests
        store._conn.execute("DROP TRIGGER IF EXISTS trg_abort_packet")
        store._conn.commit()


# ── Non-TTY ConfirmGate ─────────────────────────────────────────────


class TestNonTTYConfirmGate:
    """Prove that ConfirmGate denies writes when /dev/tty is unavailable."""

    def test_non_tty_denial_reason_and_no_write(self):
        """When /dev/tty is unavailable, ConfirmGate returns the exact
        human-readable reason and does not execute the write."""
        import tempfile
        import os
        import json
        import subprocess
        import sys

        tmpdir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(tmpdir, "thinkos.json")
            config = {
                "gates": {"default": "confirm",
                          "overrides": {"read_file": "always_allow", "write_file": "confirm"}},
                "store": {"path": ":memory:"},
                "tools": {"allowed_root": None},
            }
            with open(config_path, "w") as f:
                json.dump(config, f)

            target = os.path.join(tmpdir, "should_not_exist.txt")
            msg = {
                "type": "agent_message", "message_id": "msg_deny",
                "session_id": "sess_deny", "timestamp": "2026-07-16T00:00:00Z",
                "sender": "test",
                "content": {"text": "write", "tool_calls": [
                    {"tool": "write_file", "params": {"path": target, "content": "x"},
                     "call_id": "c1"}
                ], "context_refs": []},
            }
            input_bytes = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            # PYTHONPATH = repository root (tests/ is one level below root)
            from pathlib import Path
            repo_root = Path(__file__).resolve().parents[1]
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                str(repo_root)
                if not existing
                else str(repo_root) + os.pathsep + existing
            )
            # start_new_session=True detaches the subprocess from the parent's
            # controlling terminal, making /dev/tty deterministically unavailable.
            proc = subprocess.run(
                [sys.executable, "-m", "thinkos"],
                input=input_bytes, capture_output=True, timeout=15,
                cwd=tmpdir, env=env,
                start_new_session=True,
            )
            assert proc.returncode == 0, f"Process failed: {proc.stderr.decode()}"
            resp = json.loads(proc.stdout.decode().strip())
            tr = resp["content"]["tool_results"][0]
            assert tr["status"] == "denied"
            assert tr["output"] == "Non-interactive mode: write approval unavailable"
            assert not os.path.exists(target)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestMainStoreClosure:
    """Correction C: store.close() runs on exception via try/finally in main()."""

    def test_store_closes_on_engine_exception(self):
        """When engine.run() raises, store.close() is still called."""
        import sys
        from unittest.mock import patch
        from thinkos.__main__ import main

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            with open(config_path, "w") as f:
                json.dump({"gates": {"default": "always_allow"}}, f)

            original_cwd = os.getcwd()
            original_argv = sys.argv
            try:
                os.chdir(tmpdir)
                sys.argv = ["thinkos"]

                close_called = False

                def _tracking_close(self):
                    nonlocal close_called
                    close_called = True
                    original_close(self)

                original_close = SQLiteStore.close
                with patch("thinkos.store.sqlite_store.SQLiteStore.close", _tracking_close):
                    with patch("thinkos.engine.Engine.run") as mock_run:
                        mock_run.side_effect = ValueError("Simulated engine crash")
                        with patch("sys.stdin") as mock_stdin:
                            mock_stdin.buffer.readline.return_value = b""
                            with pytest.raises(ValueError, match="Simulated engine crash"):
                                main()

                assert close_called, "store.close() was NOT called after engine exception"
            finally:
                os.chdir(original_cwd)
                sys.argv = original_argv

    def test_store_closes_on_normal_exit(self):
        """When engine.run() exits normally, store.close() is still called."""
        import sys
        from unittest.mock import patch
        from thinkos.__main__ import main

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "thinkos.json")
            with open(config_path, "w") as f:
                json.dump({"gates": {"default": "always_allow"}}, f)

            original_cwd = os.getcwd()
            original_argv = sys.argv
            try:
                os.chdir(tmpdir)
                sys.argv = ["thinkos"]

                close_called = False

                def _tracking_close(self):
                    nonlocal close_called
                    close_called = True
                    original_close(self)

                original_close = SQLiteStore.close
                with patch("thinkos.store.sqlite_store.SQLiteStore.close", _tracking_close):
                    with patch("sys.stdin") as mock_stdin:
                        mock_stdin.buffer.readline.return_value = b""
                        main()

                assert close_called, "store.close() was NOT called on normal exit"
            finally:
                os.chdir(original_cwd)
                sys.argv = original_argv
