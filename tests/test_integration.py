"""Integration tests — end-to-end pipeline."""

import uuid
import os
import tempfile
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
from thinkos.schema.context_packet import ContextPacket, serialize as serialize_packet
from thinkos.schema.receipt import Receipt, Action, Result


@pytest.fixture
def engine():
    store = SQLiteStore(":memory:")
    connector = StdinConnector()
    register_tool("read_file", ReadFileAdapter())
    register_tool("write_file", WriteFileAdapter())
    register_gate("always_allow", AlwaysAllowGate())
    register_gate("confirm", ConfirmGate())
    register_gate("deny_all", DenyAllGate())
    config = load_config("/nonexistent/path.json")
    eng = Engine(store, connector, TOOL_REGISTRY, GATE_REGISTRY, config)
    return eng, store


class TestIntegration:
    def test_packet_write_and_read(self, engine):
        eng, store = engine
        p = ContextPacket(
            packet_id=f"ctx_{uuid.uuid4()}",
            session_id="sess_int",
            timestamp="2026-07-06T12:00:00Z",
            kind="decision",
            source="test",
            content={"text": "SQLite for MVP", "structured": None},
            tags=["decision"],
        )
        store.write_packet(p)
        p2 = store.read_packet(p.packet_id)
        assert p2 is not None
        assert p2.content["text"] == "SQLite for MVP"

    def test_receipt_chain(self, engine):
        eng, store = engine
        r1 = Receipt(
            receipt_id=f"rct_{uuid.uuid4()}",
            session_id="sess_int",
            sequence=1,
            timestamp="2026-07-06T12:00:00Z",
            action=Action(type="tool_call", tool="read_file", params={}, agent="test"),
            result=Result(status="ok", summary="Step 1", packet_ids=[], error=None),
        )
        r2 = Receipt(
            receipt_id=f"rct_{uuid.uuid4()}",
            session_id="sess_int",
            sequence=2,
            timestamp="2026-07-06T12:00:01Z",
            action=Action(type="tool_call", tool="write_file", params={}, agent="test"),
            result=Result(status="ok", summary="Step 2", packet_ids=[], error=None),
        )
        store.write_receipt(r1)
        store.write_receipt(r2)
        packets, receipts = store.rehydrate("sess_int")
        assert len(receipts) == 2
        assert receipts[0].sequence == 1
        assert receipts[1].sequence == 2

    def test_3_round_handoff(self, engine):
        """S1 test: write → read → append → read, all fields survive."""
        eng, store = engine
        p1 = ContextPacket(
            packet_id=f"ctx_{uuid.uuid4()}",
            session_id="sess_handoff",
            timestamp="2026-07-06T12:00:00Z",
            kind="decision",
            source="agent_a",
            content={"text": "Use SQLite", "structured": {"db": "sqlite"}},
            tags=["db"],
        )
        store.write_packet(p1)

        p2 = store.read_packet(p1.packet_id)
        assert p2 is not None
        assert p2.content["text"] == "Use SQLite"
        assert p2.content["structured"]["db"] == "sqlite"
        assert p2.source == "agent_a"

        p3 = ContextPacket(
            packet_id=f"ctx_{uuid.uuid4()}",
            session_id="sess_handoff",
            parent_id=p1.packet_id,
            timestamp="2026-07-06T12:01:00Z",
            kind="decision",
            source="agent_b",
            content={"text": "Confirmed: SQLite", "structured": {"db": "sqlite", "confirmed_by": "agent_b"}},
            tags=["db", "confirmed"],
        )
        store.write_packet(p3)

        p4 = store.read_packet(p3.packet_id)
        assert p4 is not None
        assert p4.content["text"] == "Confirmed: SQLite"
        assert p4.parent_id == p1.packet_id

    def test_gate_enforcement(self, engine):
        """S3 test: confirm gate blocks writes, allows reads."""
        eng, store = engine
        from thinkos.gates.confirm import ConfirmGate
        import io
        import sys
        gate = ConfirmGate()

        read_result = gate.evaluate("read_file", {"path": "/tmp/test"})
        assert read_result["action"] == "allow"

        old_stdin = sys.stdin
        sys.stdin = io.StringIO("\n")
        try:
            write_result = gate.evaluate("write_file", {"path": "/tmp/test"})
            assert write_result["action"] == "deny"
        finally:
            sys.stdin = old_stdin

    def test_negative_malformed_packet(self, engine):
        """Invalid packet should be rejected by schema validation."""
        from thinkos.schema.context_packet import validate
        p = ContextPacket(
            packet_id="bad_id",
            kind="invalid_kind",
            content={"text": ""},
        )
        errs = validate(p)
        assert len(errs) > 0

    def test_negative_path_traversal(self, engine):
        """Path traversal should be rejected by tools."""
        from thinkos.tools.read_file import ReadFileAdapter
        adapter = ReadFileAdapter()
        result = adapter.execute({"path": "../../etc/passwd", "call_id": "call_001"}, {"allowed_root": None})
        assert result["status"] == "error"
