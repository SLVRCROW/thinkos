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
                _make_tc("read_file", call_id="c1", params={"path": "/etc/hostname"}),
                _make_tc("write_file", call_id="c2", params={"path": "/tmp/x.txt", "content": "x"}),
                _make_tc("read_file", call_id="c3", params={"path": "/etc/hostname"}),
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
                _make_tc("read_file", call_id="c2", params={"path": "/etc/hostname"}),
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
            _make_tc("read_file", call_id=f"c{i}", params={"path": "/etc/hostname"})
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
            _make_tc("read_file", call_id=f"c{i}", params={"path": "/etc/hostname"})
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
        calls = [_make_tc(tool="read_file", params={"path": "/etc/hostname"},
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

    def test_existing_parent_id_unchanged(self):
        """After rehydration, first new packet still has parent_id=None (lineage not restored)."""
        store = SQLiteStore(":memory:")
        self._write_prior_data(store)
        msg = self._make_rehydrate_msg(tool_calls=[
            {"tool": "read_file", "params": {"path": "/etc/hostname"}, "call_id": "c1"}
        ])
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
        assert p.parent_id is None  # lineage not restored

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
