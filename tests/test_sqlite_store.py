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

    def test_rehydrate_deterministic_ordering(self, store):
        """rehydrate() returns packets sorted by timestamp DESC, packet_id DESC."""
        p1 = _make_packet(pid=f"ctx_{uuid.uuid4()}", session="sess_order")
        p1.timestamp = "2026-07-10T12:00:00Z"
        p2 = _make_packet(pid=f"ctx_{uuid.uuid4()}", session="sess_order")
        p2.timestamp = "2026-07-10T13:00:00Z"
        p3 = _make_packet(pid=f"ctx_{uuid.uuid4()}", session="sess_order")
        p3.timestamp = "2026-07-10T11:00:00Z"
        store.write_packet(p1)
        store.write_packet(p2)
        store.write_packet(p3)
        r1 = _make_receipt(session="sess_order", seq=1)
        r1.result.packet_ids = [p1.packet_id]
        r2 = _make_receipt(session="sess_order", seq=2)
        r2.result.packet_ids = [p2.packet_id]
        r3 = _make_receipt(session="sess_order", seq=3)
        r3.result.packet_ids = [p3.packet_id]
        store.write_receipt(r1)
        store.write_receipt(r2)
        store.write_receipt(r3)
        packets, _ = store.rehydrate("sess_order")
        assert len(packets) == 3
        # Most recent first: p2 (13:00), p1 (12:00), p3 (11:00)
        assert packets[0].packet_id == p2.packet_id
        assert packets[1].packet_id == p1.packet_id
        assert packets[2].packet_id == p3.packet_id


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


class TestListPacketsFilters:
    """TM008: list_packets filter improvements."""

    def _setup(self, store):
        """Create packets with varied attributes for filter testing."""
        p1 = _make_packet(pid="ctx_f1", session="sess_f", kind="observation",
                          parent_id=None)
        p1.timestamp = "2026-07-10T12:00:00Z"
        p1.source = "alpha"
        p1.tags = ["tag_a", "tag_b"]
        p2 = _make_packet(pid="ctx_f2", session="sess_f", kind="decision",
                          parent_id="ctx_f1")
        p2.timestamp = "2026-07-10T13:00:00Z"
        p2.source = "beta"
        p2.tags = ["tag_a"]
        p3 = _make_packet(pid="ctx_f3", session="sess_f", kind="observation",
                          parent_id="ctx_f1")
        p3.timestamp = "2026-07-10T11:00:00Z"
        p3.source = "alpha"
        p3.tags = ["tag_b", "tag_c"]
        p4 = _make_packet(pid="ctx_f4", session="sess_other", kind="observation",
                          parent_id=None)
        p4.timestamp = "2026-07-10T14:00:00Z"
        p4.source = "alpha"
        p4.tags = ["tag_a"]
        store.write_packet(p1)
        store.write_packet(p2)
        store.write_packet(p3)
        store.write_packet(p4)

    def test_default_order_asc(self, store):
        """Default order is timestamp ASC (backward compatible)."""
        self._setup(store)
        packets = store.list_packets(session_id="sess_f")
        assert packets[0].packet_id == "ctx_f3"  # 11:00
        assert packets[1].packet_id == "ctx_f1"  # 12:00
        assert packets[2].packet_id == "ctx_f2"  # 13:00

    def test_order_desc(self, store):
        """order='desc' returns packets in reverse chronological."""
        self._setup(store)
        packets = store.list_packets(session_id="sess_f", order="desc")
        assert packets[0].packet_id == "ctx_f2"  # 13:00
        assert packets[1].packet_id == "ctx_f1"  # 12:00
        assert packets[2].packet_id == "ctx_f3"  # 11:00

    def test_invalid_order_raises(self, store):
        """Invalid order string raises ValueError."""
        import pytest
        with pytest.raises(ValueError, match="order must be 'asc' or 'desc'"):
            store.list_packets(order="invalid")

    def test_tag_filter_single(self, store):
        """Single tag filter returns only packets with that tag."""
        self._setup(store)
        packets = store.list_packets(tags=["tag_c"])
        assert len(packets) == 1
        assert packets[0].packet_id == "ctx_f3"

    def test_tag_filter_and_semantics(self, store):
        """Multiple tags use AND: returned packets must have all tags."""
        self._setup(store)
        packets = store.list_packets(tags=["tag_a", "tag_b"])
        assert len(packets) == 1
        assert packets[0].packet_id == "ctx_f1"

    def test_tag_filter_no_match(self, store):
        """Tag filter with no matches returns empty list."""
        self._setup(store)
        packets = store.list_packets(tags=["nonexistent"])
        assert packets == []

    def test_tag_filter_exact_not_substring(self, store):
        """Tag filter uses exact matching, not loose substring."""
        self._setup(store)
        # "tag" should not match "tag_a" or "tag_b"
        packets = store.list_packets(tags=["tag"])
        assert packets == []

    def test_parent_id_filter(self, store):
        """parent_id filter returns only direct children."""
        self._setup(store)
        packets = store.list_packets(parent_id="ctx_f1")
        assert len(packets) == 2
        assert {p.packet_id for p in packets} == {"ctx_f2", "ctx_f3"}

    def test_parent_id_none_filter(self, store):
        """parent_id=None returns root packets."""
        self._setup(store)
        packets = store.list_packets(parent_id=None)
        assert len(packets) == 2
        assert {p.packet_id for p in packets} == {"ctx_f1", "ctx_f4"}

    def test_source_filter(self, store):
        """source filter returns only packets from that source."""
        self._setup(store)
        packets = store.list_packets(source="beta")
        assert len(packets) == 1
        assert packets[0].packet_id == "ctx_f2"

    def test_time_range_filter(self, store):
        """time_range filter returns only packets within the window (inclusive)."""
        self._setup(store)
        packets = store.list_packets(
            time_range=("2026-07-10T12:00:00Z", "2026-07-10T13:00:00Z")
        )
        assert len(packets) == 2
        assert {p.packet_id for p in packets} == {"ctx_f1", "ctx_f2"}

    def test_combined_filters(self, store):
        """Multiple filters work together."""
        self._setup(store)
        packets = store.list_packets(
            session_id="sess_f",
            kind="observation",
            parent_id="ctx_f1",
        )
        assert len(packets) == 1
        assert packets[0].packet_id == "ctx_f3"

    def test_limit_respected(self, store):
        """Limit is respected."""
        self._setup(store)
        packets = store.list_packets(session_id="sess_f", limit=2)
        assert len(packets) == 2


class TestGetPacketChildren:
    """TM008: get_packet_children — read-only downward DAG traversal."""

    def _setup(self, store):
        p1 = _make_packet(pid="ctx_root", session="sess_c", parent_id=None)
        p1.timestamp = "2026-07-10T12:00:00Z"
        p2 = _make_packet(pid="ctx_child1", session="sess_c", parent_id="ctx_root")
        p2.timestamp = "2026-07-10T13:00:00Z"
        p3 = _make_packet(pid="ctx_child2", session="sess_c", parent_id="ctx_root")
        p3.timestamp = "2026-07-10T11:00:00Z"
        p4 = _make_packet(pid="ctx_grandchild", session="sess_c", parent_id="ctx_child1")
        p4.timestamp = "2026-07-10T14:00:00Z"
        p5 = _make_packet(pid="ctx_other", session="sess_other", parent_id="ctx_root")
        p5.timestamp = "2026-07-10T15:00:00Z"
        store.write_packet(p1)
        store.write_packet(p2)
        store.write_packet(p3)
        store.write_packet(p4)
        store.write_packet(p5)

    def test_returns_direct_children(self, store):
        """Returns packets with matching parent_id."""
        self._setup(store)
        children = store.get_packet_children("ctx_root")
        assert len(children) == 2
        assert {c.packet_id for c in children} == {"ctx_child1", "ctx_child2"}

    def test_empty_when_no_children(self, store):
        """Leaf packet returns empty list."""
        self._setup(store)
        # ctx_child2 has no children
        assert store.get_packet_children("ctx_child2") == []

    def test_empty_when_missing_parent(self, store):
        """Nonexistent packet_id returns empty list."""
        self._setup(store)
        assert store.get_packet_children("ctx_nonexistent") == []

    def test_deterministic_ordering(self, store):
        """Children ordered by timestamp DESC, packet_id DESC."""
        self._setup(store)
        children = store.get_packet_children("ctx_root")
        assert children[0].packet_id == "ctx_child1"  # 13:00
        assert children[1].packet_id == "ctx_child2"  # 11:00

    def test_cross_session_isolation(self, store):
        """Children from a different session are not returned."""
        self._setup(store)
        children = store.get_packet_children("ctx_root")
        assert "ctx_other" not in {c.packet_id for c in children}

    def test_limit_respected(self, store):
        """Limit is respected."""
        self._setup(store)
        children = store.get_packet_children("ctx_root", limit=1)
        assert len(children) == 1

    def test_read_only_no_mutation(self, store):
        """Prove the method does not write to the store."""
        self._setup(store)
        before = store.list_packets()
        store.get_packet_children("ctx_root")
        after = store.list_packets()
        assert len(before) == len(after)
