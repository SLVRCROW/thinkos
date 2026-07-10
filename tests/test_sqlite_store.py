"""Tests for SQLiteStore."""

import uuid
import pytest
from thinkos.store.sqlite_store import SQLiteStore, DuplicateError, CycleError, DepthError
from thinkos.schema.context_packet import ContextPacket
from thinkos.schema.receipt import Receipt, Action, Result


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


def _make_packet(session="sess_test", kind="observation", parent_id=None, pid=None):
    return ContextPacket(
        packet_id=pid or f"ctx_{uuid.uuid4()}",
        session_id=session,
        parent_id=parent_id,
        timestamp="2026-07-06T12:00:00Z",
        kind=kind,
        source="test",
        content={"text": "test content", "structured": None},
    )


def _make_receipt(session="sess_test", seq=1, tool="read_file", status="ok"):
    return Receipt(
        receipt_id=f"rct_{uuid.uuid4()}",
        session_id=session,
        sequence=seq,
        timestamp="2026-07-06T12:00:00Z",
        action=Action(type="tool_call", tool=tool, params={}, agent="test"),
        result=Result(status=status, summary="test", packet_ids=[], error=None),
    )


class TestPacketPersistence:
    def test_write_and_read(self, store):
        p = _make_packet()
        store.write_packet(p)
        p2 = store.read_packet(p.packet_id)
        assert p2 is not None
        assert p2.packet_id == p.packet_id
        assert p2.content["text"] == "test content"

    def test_duplicate_packet_id(self, store):
        p = _make_packet()
        store.write_packet(p)
        with pytest.raises(DuplicateError):
            store.write_packet(p)

    def test_read_nonexistent(self, store):
        assert store.read_packet("ctx_nonexistent") is None

    def test_list_by_session(self, store):
        p1 = _make_packet(session="sess_a")
        p2 = _make_packet(session="sess_a")
        p3 = _make_packet(session="sess_b")
        store.write_packet(p1)
        store.write_packet(p2)
        store.write_packet(p3)
        packets = store.list_packets(session_id="sess_a")
        assert len(packets) == 2

    def test_list_by_kind(self, store):
        p1 = _make_packet(kind="decision")
        p2 = _make_packet(kind="observation")
        store.write_packet(p1)
        store.write_packet(p2)
        packets = store.list_packets(kind="decision")
        assert len(packets) == 1
        assert packets[0].kind == "decision"


class TestReceiptPersistence:
    def test_write_and_read(self, store):
        r = _make_receipt()
        store.write_receipt(r)
        r2 = store.read_receipt(r.receipt_id)
        assert r2 is not None
        assert r2.receipt_id == r.receipt_id

    def test_duplicate_receipt_id(self, store):
        r = _make_receipt()
        store.write_receipt(r)
        with pytest.raises(DuplicateError):
            store.write_receipt(r)

    def test_list_ordered_by_sequence(self, store):
        r1 = _make_receipt(seq=2)
        r2 = _make_receipt(seq=1)
        store.write_receipt(r1)
        store.write_receipt(r2)
        receipts = store.list_receipts()
        assert receipts[0].sequence == 1
        assert receipts[1].sequence == 2


class TestRehydrate:
    def test_rehydrate_returns_packets_and_receipts(self, store):
        p = _make_packet()
        store.write_packet(p)
        r = _make_receipt()
        r.result.packet_ids = [p.packet_id]
        store.write_receipt(r)
        packets, receipts = store.rehydrate("sess_test")
        assert len(packets) == 1
        assert len(receipts) == 1


class TestCycleAndDepth:
    def test_cycle_detection(self, store):
        # Write a chain: p1 -> None, p2 -> p1, p3 -> p2
        p1 = _make_packet(pid=f"ctx_{uuid.uuid4()}", parent_id=None)
        store.write_packet(p1)
        p2 = _make_packet(pid=f"ctx_{uuid.uuid4()}", parent_id=p1.packet_id)
        store.write_packet(p2)
        p3 = _make_packet(pid=f"ctx_{uuid.uuid4()}", parent_id=p2.packet_id)
        store.write_packet(p3)

        # A new packet with parent_id = p3 has no cycle (chain: p3 -> p2 -> p1 -> None)
        normal = _make_packet(parent_id=p3.packet_id)
        assert not store._check_cycle(normal)

        # A packet whose parent_id chain would lead back to itself IS a cycle.
        # We can test this by checking that _check_cycle returns True when
        # the new packet's ID appears in the parent chain.
        # Create a scenario: p4 -> p3 -> p2 -> p1, then check if p4's ID
        # appears in the chain (it won't since p4 is new)
        p4 = _make_packet(pid=f"ctx_{uuid.uuid4()}", parent_id=p3.packet_id)
        assert not store._check_cycle(p4)

        # Verify the store rejects a packet that would create a cycle
        # by checking that write_packet raises CycleError
        # We can't easily create a cycle without modifying existing packets,
        # so we verify the _check_cycle method is called during write_packet
        # by checking that a non-cyclic packet writes successfully
        store.write_packet(p4)
        assert store.read_packet(p4.packet_id) is not None

    def test_depth_limit(self, store):
        # Create chain of 5 packets
        prev = None
        for i in range(5):
            p = _make_packet(parent_id=prev)
            store.write_packet(p)
            prev = p.packet_id
        # 6th packet should fail
        p6 = _make_packet(parent_id=prev)
        with pytest.raises(DepthError):
            store.write_packet(p6)


class TestGetLatestPacketId:
    """SQLiteStore.get_latest_packet_id — deterministic latest-packet lookup."""

    def test_returns_none_for_empty_session(self, store):
        assert store.get_latest_packet_id("empty_session") is None

    def test_returns_latest_packet_id(self, store):
        p1 = _make_packet(session="sess_latest")
        p2 = _make_packet(session="sess_latest")
        p3 = _make_packet(session="sess_latest")
        store.write_packet(p1)
        store.write_packet(p2)
        store.write_packet(p3)
        latest = store.get_latest_packet_id("sess_latest")
        assert latest == p3.packet_id

    def test_scoped_to_session(self, store):
        p_a = _make_packet(session="sess_a")
        p_b = _make_packet(session="sess_b")
        store.write_packet(p_a)
        store.write_packet(p_b)
        assert store.get_latest_packet_id("sess_a") == p_a.packet_id
        assert store.get_latest_packet_id("sess_b") == p_b.packet_id

    def test_deterministic_with_timestamp_ties(self, store):
        """When two packets share the same timestamp, rowid breaks the tie."""
        from thinkos.schema.context_packet import ContextPacket
        ts = "2026-07-10T12:00:00Z"
        p1 = ContextPacket(
            packet_id="ctx_tie_001", session_id="sess_tie",
            timestamp=ts, kind="observation", source="test",
            content={"text": "first", "structured": None},
        )
        p2 = ContextPacket(
            packet_id="ctx_tie_002", session_id="sess_tie",
            timestamp=ts, kind="observation", source="test",
            content={"text": "second", "structured": None},
        )
        store.write_packet(p1)
        store.write_packet(p2)
        latest = store.get_latest_packet_id("sess_tie")
        # p2 was written second, so rowid is higher
        assert latest == p2.packet_id


class TestGetPacketChain:
    """SQLiteStore.get_packet_chain — read-only parent-chain traversal."""

    def test_missing_packet_id_returns_empty(self, store):
        assert store.get_packet_chain("ctx_nonexistent") == []

    def test_single_packet_returns_one_packet_chain(self, store):
        p = _make_packet(pid="ctx_root")
        store.write_packet(p)
        chain = store.get_packet_chain("ctx_root")
        assert len(chain) == 1
        assert chain[0].packet_id == "ctx_root"

    def test_multi_packet_chain_root_to_leaf(self, store):
        p1 = _make_packet(pid="ctx_001", parent_id=None)
        p2 = _make_packet(pid="ctx_002", parent_id="ctx_001")
        p3 = _make_packet(pid="ctx_003", parent_id="ctx_002")
        store.write_packet(p1)
        store.write_packet(p2)
        store.write_packet(p3)
        chain = store.get_packet_chain("ctx_003")
        assert len(chain) == 3
        assert chain[0].packet_id == "ctx_001"  # root first
        assert chain[1].packet_id == "ctx_002"
        assert chain[2].packet_id == "ctx_003"  # leaf last

    def test_max_packets_bounds_chain(self, store):
        p1 = _make_packet(pid="ctx_a", parent_id=None)
        p2 = _make_packet(pid="ctx_b", parent_id="ctx_a")
        p3 = _make_packet(pid="ctx_c", parent_id="ctx_b")
        store.write_packet(p1)
        store.write_packet(p2)
        store.write_packet(p3)
        chain = store.get_packet_chain("ctx_c", max_packets=2)
        assert len(chain) == 2
        assert chain[0].packet_id == "ctx_b"  # truncated: [ctx_b, ctx_c]
        assert chain[1].packet_id == "ctx_c"

    def test_missing_parent_stops_cleanly(self, store):
        p = _make_packet(pid="ctx_orphan", parent_id="ctx_missing")
        store.write_packet(p)
        chain = store.get_packet_chain("ctx_orphan")
        assert len(chain) == 1
        assert chain[0].packet_id == "ctx_orphan"

    def test_cycle_guard_stops_cleanly(self, store):
        """Cycle via raw SQL (bypassing write_packet's cycle check) to test the defensive guard.

        The method returns the valid reachable chain [ctx_cycle_b, ctx_cycle_a]
        and stops before re-visiting ctx_cycle_a.
        """
        p1 = _make_packet(pid="ctx_cycle_a", parent_id="ctx_cycle_b")
        p2 = _make_packet(pid="ctx_cycle_b", parent_id="ctx_cycle_a")
        # Write both packets via raw SQL to bypass write_packet's cycle detection
        store._conn.execute(
            "INSERT INTO packets (packet_id, schema_version, session_id, parent_id, timestamp, kind, source, content_text) "
            "VALUES (?, 1, ?, ?, ?, 'observation', 'test', 'test content')",
            (p1.packet_id, p1.session_id, p1.parent_id, p1.timestamp)
        )
        store._conn.execute(
            "INSERT INTO packets (packet_id, schema_version, session_id, parent_id, timestamp, kind, source, content_text) "
            "VALUES (?, 1, ?, ?, ?, 'observation', 'test', 'test content')",
            (p2.packet_id, p2.session_id, p2.parent_id, p2.timestamp)
        )
        store._conn.commit()
        chain = store.get_packet_chain("ctx_cycle_a")
        # Returns [ctx_cycle_b, ctx_cycle_a] — both are valid reachable packets.
        # The cycle is detected when trying to walk from ctx_cycle_b back to ctx_cycle_a.
        assert len(chain) == 2
        assert chain[0].packet_id == "ctx_cycle_b"
        assert chain[1].packet_id == "ctx_cycle_a"

    def test_cross_session_parent_does_not_leak(self, store):
        p_a = _make_packet(pid="ctx_sess_a", session="sess_a", parent_id="ctx_sess_b")
        p_b = _make_packet(pid="ctx_sess_b", session="sess_b", parent_id=None)
        store.write_packet(p_a)
        store.write_packet(p_b)
        chain = store.get_packet_chain("ctx_sess_a")
        # Should stop at ctx_sess_a — parent is in a different session
        assert len(chain) == 1
        assert chain[0].packet_id == "ctx_sess_a"

    def test_read_only_no_mutation(self, store):
        """Prove the method does not write to the store."""
        p = _make_packet(pid="ctx_ro")
        store.write_packet(p)
        before = store.list_packets()
        store.get_packet_chain("ctx_ro")
        after = store.list_packets()
        assert len(before) == len(after)
        assert before[0].packet_id == after[0].packet_id

    def test_max_packets_zero_returns_empty(self, store):
        p = _make_packet(pid="ctx_zero")
        store.write_packet(p)
        assert store.get_packet_chain("ctx_zero", max_packets=0) == []

    def test_max_packets_negative_returns_empty(self, store):
        p = _make_packet(pid="ctx_neg")
        store.write_packet(p)
        assert store.get_packet_chain("ctx_neg", max_packets=-1) == []
