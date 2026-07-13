"""Tests for store-level handoff authorization — defense in depth."""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from thinkos.schema.verified_context import VerifiedExecutionContext
from thinkos.schema.handoff_record import HandoffRecord
from thinkos.schema.context_packet import ContextPacket
from thinkos.schema.receipt import Receipt, Action, Result
from thinkos.schema.security_envelope import HandoffSecurityEnvelope
from thinkos.store.sqlite_store import SQLiteStore, HandoffReferenceError


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4()}"


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def ctx():
    return VerifiedExecutionContext(
        principal="agent-a",
        session_id="session-source",
        store_namespace="test-ns",
        provider="process-bound",
        issuer="test-harness",
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )


@pytest.fixture
def unverified_ctx():
    return VerifiedExecutionContext(
        principal="unknown",
        session_id="unknown",
        store_namespace="test-ns",
        provider="none",
        issuer="unknown",
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=None,
    )


@pytest.fixture
def expired_ctx():
    return VerifiedExecutionContext(
        principal="agent-a",
        session_id="session-source",
        store_namespace="test-ns",
        provider="process-bound",
        issuer="test-harness",
        issued_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        expires_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )


@pytest.fixture
def wrong_ns_ctx():
    return VerifiedExecutionContext(
        principal="agent-a",
        session_id="session-source",
        store_namespace="other-ns",
        provider="process-bound",
        issuer="test-harness",
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )


def _setup_handoff_with_envelope(store, ctx):
    """Create a packet, receipt, handoff record, and envelope using ctx."""
    packet = ContextPacket(
        packet_id=_id("ctx_"), session_id=ctx.session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        content={"text": "evidence", "structured": None},
    )
    receipt = Receipt(
        receipt_id=_id("rct_"), session_id=ctx.session_id,
        sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
        action=Action(agent=ctx.principal),
        result=Result(summary="evidence recorded"),
    )
    store.write_packet(packet)
    store.write_receipt(receipt)

    handoff_id = _id("hof_")
    now = datetime.now(timezone.utc).isoformat()
    record = HandoffRecord(
        handoff_id=handoff_id,
        source_session_id=ctx.session_id,
        target_session_id="session-target",
        source_agent=ctx.principal,
        target_agent="agent-b",
        timestamp=now,
        purpose_summary="Test",
        packet_ids=[packet.packet_id],
        receipt_ids=[receipt.receipt_id],
    )
    envelope = HandoffSecurityEnvelope(
        envelope_id=_id("env_"),
        handoff_id=handoff_id,
        source_principal=ctx.principal,
        source_session_id=ctx.session_id,
        target_session_intent="session-target",
        store_namespace=ctx.store_namespace,
        provider=ctx.provider,
        issuer=ctx.issuer,
        policy_version="1",
        created_at=now,
    )
    store.write_handoff_with_envelope(record, envelope, ctx)
    return handoff_id


class TestStoreReadHandoffAuth:
    def test_missing_context_raises_type_error(self, store):
        """read_handoff without ctx raises TypeError."""
        with pytest.raises(TypeError):
            store.read_handoff("hof_test")  # no ctx

    def test_ctx_none_raises_type_error(self, store):
        """read_handoff with ctx=None raises TypeError."""
        with pytest.raises(TypeError):
            store.read_handoff("hof_test", None)

    def test_unverified_context_denied(self, store, unverified_ctx):
        with pytest.raises(PermissionError, match="unverified"):
            store.read_handoff("hof_test", unverified_ctx)

    def test_expired_context_denied(self, store, expired_ctx):
        with pytest.raises(PermissionError, match="expired"):
            store.read_handoff("hof_test", expired_ctx)

    def test_namespace_mismatch_denied(self, store, ctx, wrong_ns_ctx):
        handoff_id = _setup_handoff_with_envelope(store, ctx)
        with pytest.raises(PermissionError, match="namespace"):
            store.read_handoff(handoff_id, wrong_ns_ctx)

    def test_legacy_record_denied(self, store, ctx):
        """A handoff without an envelope is UNVERIFIED_LEGACY."""
        packet = ContextPacket(
            packet_id=_id("ctx_"), session_id="session-source",
            timestamp=datetime.now(timezone.utc).isoformat(),
            content={"text": "evidence", "structured": None},
        )
        receipt = Receipt(
            receipt_id=_id("rct_"), session_id="session-source",
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(agent="agent-a"),
            result=Result(summary="evidence recorded"),
        )
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = HandoffRecord(
            handoff_id=_id("hof_"),
            source_session_id="session-source",
            target_session_id="session-target",
            source_agent="agent-a",
            target_agent="agent-b",
            timestamp=datetime.now(timezone.utc).isoformat(),
            purpose_summary="Legacy",
            packet_ids=[packet.packet_id],
            receipt_ids=[receipt.receipt_id],
        )
        # Create legacy record via direct SQL (no envelope — simulates pre-TAA record)
        import json as _json
        store._conn.execute(
            """INSERT INTO handoffs
               (handoff_id, schema_version, source_session_id, target_session_id,
                source_agent, target_agent, timestamp, expires_at, purpose_summary,
                packet_ids, receipt_ids, omitted_packet_count, omissions_summary,
                evidence_policy, authority_transfer, requires_fresh_approval, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.handoff_id, record.schema_version,
                record.source_session_id, record.target_session_id,
                record.source_agent, record.target_agent,
                record.timestamp, record.expires_at,
                record.purpose_summary,
                _json.dumps(record.packet_ids),
                _json.dumps(record.receipt_ids),
                record.omitted_packet_count, record.omissions_summary,
                record.evidence_policy, record.authority_transfer,
                int(record.requires_fresh_approval),
                _json.dumps(record.tags),
            ),
        )
        store._conn.commit()
        with pytest.raises(HandoffReferenceError, match="UNVERIFIED_LEGACY"):
            store.read_handoff(record.handoff_id, ctx)

    def test_valid_context_succeeds(self, store, ctx):
        handoff_id = _setup_handoff_with_envelope(store, ctx)
        result = store.read_handoff(handoff_id, ctx)
        assert result is not None
        assert result.handoff_id == handoff_id


class TestStoreSessionMismatch:
    """Store-level context enforcement — defense in depth.

    The store enforces: context validity, expiry, namespace match,
    envelope presence, and record/envelope binding.
    """

    @pytest.fixture
    def source_ctx(self):
        return VerifiedExecutionContext(
            principal="agent-a", session_id="session-source",
            store_namespace="test-ns", provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

    @pytest.fixture
    def target_ctx(self):
        return VerifiedExecutionContext(
            principal="agent-b", session_id="session-target",
            store_namespace="test-ns", provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

    @pytest.fixture
    def unrelated_ctx(self):
        return VerifiedExecutionContext(
            principal="agent-c", session_id="session-unrelated",
            store_namespace="test-ns", provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

    @pytest.fixture
    def wrong_ns_ctx(self):
        return VerifiedExecutionContext(
            principal="agent-c", session_id="session-unrelated",
            store_namespace="other-ns", provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

    def test_wrong_namespace_cannot_read(self, store, source_ctx, wrong_ns_ctx):
        """A verified context with wrong namespace cannot read."""
        handoff_id = _setup_handoff_with_envelope(store, source_ctx)
        with pytest.raises(PermissionError, match="namespace"):
            store.read_handoff(handoff_id, wrong_ns_ctx)

    def test_wrong_namespace_cannot_list(self, store, source_ctx, wrong_ns_ctx):
        """A verified context with wrong namespace is denied list (session check first)."""
        _setup_handoff_with_envelope(store, source_ctx)
        # wrong_ns_ctx has session="session-unrelated" which doesn't match
        # target_session_id="session-target" — session check fires first
        with pytest.raises(PermissionError, match="session mismatch"):
            store.list_handoffs_for_target("session-target", wrong_ns_ctx)

    def test_wrong_namespace_cannot_resolve(self, store, source_ctx, wrong_ns_ctx):
        """A verified context with wrong namespace cannot resolve."""
        handoff_id = _setup_handoff_with_envelope(store, source_ctx)
        with pytest.raises(PermissionError, match="namespace"):
            store.resolve_handoff(handoff_id, wrong_ns_ctx)

    def test_wrong_namespace_create_denied(self, store, source_ctx, wrong_ns_ctx):
        """write_handoff_with_envelope with wrong namespace is denied atomically."""
        packet = ContextPacket(
            packet_id=_id("ctx_"), session_id="session-source",
            timestamp=datetime.now(timezone.utc).isoformat(),
            content={"text": "evidence", "structured": None},
        )
        receipt = Receipt(
            receipt_id=_id("rct_"), session_id="session-source",
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(agent="agent-a"),
            result=Result(summary="evidence recorded"),
        )
        store.write_packet(packet)
        store.write_receipt(receipt)
        handoff_id = _id("hof_")
        record = HandoffRecord(
            handoff_id=handoff_id,
            source_session_id="session-source",
            target_session_id="session-target",
            source_agent="agent-a",
            target_agent="agent-b",
            timestamp=datetime.now(timezone.utc).isoformat(),
            purpose_summary="Test",
            packet_ids=[packet.packet_id],
            receipt_ids=[receipt.receipt_id],
        )
        # Envelope has different namespace than source_ctx
        envelope = HandoffSecurityEnvelope(
            envelope_id=_id("env_"),
            handoff_id=handoff_id,
            source_principal=source_ctx.principal,
            source_session_id=source_ctx.session_id,
            target_session_intent="session-target",
            store_namespace="other-ns",  # Wrong namespace
            provider="process-bound",
            issuer="test-harness",
            policy_version="1",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises(PermissionError, match="namespace"):
            store.write_handoff_with_envelope(record, envelope, source_ctx)
        # No partial data
        assert store._read_handoff_raw(handoff_id) is None
        assert store.read_envelope(handoff_id) is None

    # --- Store-level session-binding tests ---

    def test_read_source_session_succeeds(self, store, source_ctx):
        """Source session may read its own handoff."""
        handoff_id = _setup_handoff_with_envelope(store, source_ctx)
        result = store.read_handoff(handoff_id, source_ctx)
        assert result is not None
        assert result.handoff_id == handoff_id

    def test_read_target_session_succeeds(self, store, source_ctx, target_ctx):
        """Target session may read a handoff addressed to it."""
        handoff_id = _setup_handoff_with_envelope(store, source_ctx)
        result = store.read_handoff(handoff_id, target_ctx)
        assert result is not None
        assert result.handoff_id == handoff_id

    def test_read_unrelated_session_denied(self, store, source_ctx, unrelated_ctx):
        """Unrelated verified session is denied read."""
        handoff_id = _setup_handoff_with_envelope(store, source_ctx)
        with pytest.raises(PermissionError, match="session not authorized"):
            store.read_handoff(handoff_id, unrelated_ctx)

    def test_list_own_session_succeeds(self, store, source_ctx):
        """A context may list for its own session."""
        _setup_handoff_with_envelope(store, source_ctx)
        # source_ctx session is "session-source" — list for that session
        result = store.list_handoffs_for_target("session-source", source_ctx)
        assert len(result) == 0  # No handoffs target "session-source"

    def test_list_target_session_succeeds(self, store, source_ctx, target_ctx):
        """Target session may list for itself."""
        _setup_handoff_with_envelope(store, source_ctx)
        result = store.list_handoffs_for_target("session-target", target_ctx)
        assert len(result) == 1

    def test_list_other_session_denied(self, store, source_ctx, unrelated_ctx):
        """Requesting another session is denied."""
        _setup_handoff_with_envelope(store, source_ctx)
        with pytest.raises(PermissionError, match="session mismatch"):
            store.list_handoffs_for_target("session-target", unrelated_ctx)

    def test_resolve_target_session_succeeds(self, store, source_ctx, target_ctx):
        """Target session may resolve."""
        handoff_id = _setup_handoff_with_envelope(store, source_ctx)
        result = store.resolve_handoff(handoff_id, target_ctx)
        assert result is not None
        assert result["record"].handoff_id == handoff_id

    def test_resolve_source_session_denied(self, store, source_ctx):
        """Source session may not resolve."""
        handoff_id = _setup_handoff_with_envelope(store, source_ctx)
        with pytest.raises(PermissionError, match="only target session"):
            store.resolve_handoff(handoff_id, source_ctx)

    def test_resolve_unrelated_session_denied(self, store, source_ctx, unrelated_ctx):
        """Unrelated session may not resolve."""
        handoff_id = _setup_handoff_with_envelope(store, source_ctx)
        with pytest.raises(PermissionError, match="only target session"):
            store.resolve_handoff(handoff_id, unrelated_ctx)

    def test_forged_source_session_denied(self, store, ctx):
        """Envelope with forged source session is denied atomically."""
        packet = ContextPacket(
            packet_id=_id("ctx_"), session_id="session-source",
            timestamp=datetime.now(timezone.utc).isoformat(),
            content={"text": "evidence", "structured": None},
        )
        receipt = Receipt(
            receipt_id=_id("rct_"), session_id="session-source",
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(agent="agent-a"),
            result=Result(summary="evidence recorded"),
        )
        store.write_packet(packet)
        store.write_receipt(receipt)
        handoff_id = _id("hof_")
        record = HandoffRecord(
            handoff_id=handoff_id,
            source_session_id="session-source",
            target_session_id="session-target",
            source_agent="agent-a",
            target_agent="agent-b",
            timestamp=datetime.now(timezone.utc).isoformat(),
            purpose_summary="Test",
            packet_ids=[packet.packet_id],
            receipt_ids=[receipt.receipt_id],
        )
        # Envelope claims a different source session than ctx
        envelope = HandoffSecurityEnvelope(
            envelope_id=_id("env_"),
            handoff_id=handoff_id,
            source_principal=ctx.principal,
            source_session_id="session-forged",  # Forged!
            target_session_intent="session-target",
            store_namespace=ctx.store_namespace,
            provider="process-bound",
            issuer="test-harness",
            policy_version="1",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises(PermissionError, match="session"):
            store.write_handoff_with_envelope(record, envelope, ctx)
        # No partial data
        assert store._read_handoff_raw(handoff_id) is None
        assert store.read_envelope(handoff_id) is None

    def test_forged_source_principal_denied(self, store, ctx):
        """Envelope with forged source principal is denied atomically."""
        packet = ContextPacket(
            packet_id=_id("ctx_"), session_id="session-source",
            timestamp=datetime.now(timezone.utc).isoformat(),
            content={"text": "evidence", "structured": None},
        )
        receipt = Receipt(
            receipt_id=_id("rct_"), session_id="session-source",
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(agent="agent-a"),
            result=Result(summary="evidence recorded"),
        )
        store.write_packet(packet)
        store.write_receipt(receipt)
        handoff_id = _id("hof_")
        record = HandoffRecord(
            handoff_id=handoff_id,
            source_session_id="session-source",
            target_session_id="session-target",
            source_agent="agent-a",
            target_agent="agent-b",
            timestamp=datetime.now(timezone.utc).isoformat(),
            purpose_summary="Test",
            packet_ids=[packet.packet_id],
            receipt_ids=[receipt.receipt_id],
        )
        # Envelope claims a different principal than ctx
        envelope = HandoffSecurityEnvelope(
            envelope_id=_id("env_"),
            handoff_id=handoff_id,
            source_principal="attacker",  # Forged!
            source_session_id=ctx.session_id,
            target_session_intent="session-target",
            store_namespace=ctx.store_namespace,
            provider="process-bound",
            issuer="test-harness",
            policy_version="1",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises(PermissionError, match="principal"):
            store.write_handoff_with_envelope(record, envelope, ctx)
        # No partial data
        assert store._read_handoff_raw(handoff_id) is None
        assert store.read_envelope(handoff_id) is None

    def test_forged_target_session_denied(self, store, ctx):
        """Record/envelope target session mismatch is denied atomically."""
        packet = ContextPacket(
            packet_id=_id("ctx_"), session_id="session-source",
            timestamp=datetime.now(timezone.utc).isoformat(),
            content={"text": "evidence", "structured": None},
        )
        receipt = Receipt(
            receipt_id=_id("rct_"), session_id="session-source",
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(agent="agent-a"),
            result=Result(summary="evidence recorded"),
        )
        store.write_packet(packet)
        store.write_receipt(receipt)
        handoff_id = _id("hof_")
        record = HandoffRecord(
            handoff_id=handoff_id,
            source_session_id="session-source",
            target_session_id="session-target",
            source_agent="agent-a",
            target_agent="agent-b",
            timestamp=datetime.now(timezone.utc).isoformat(),
            purpose_summary="Test",
            packet_ids=[packet.packet_id],
            receipt_ids=[receipt.receipt_id],
        )
        # Envelope has different target than record
        envelope = HandoffSecurityEnvelope(
            envelope_id=_id("env_"),
            handoff_id=handoff_id,
            source_principal=ctx.principal,
            source_session_id=ctx.session_id,
            target_session_intent="session-other",  # Mismatch!
            store_namespace=ctx.store_namespace,
            provider="process-bound",
            issuer="test-harness",
            policy_version="1",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises(ValueError, match="target_session"):
            store.write_handoff_with_envelope(record, envelope, ctx)
        # No partial data
        assert store._read_handoff_raw(handoff_id) is None
        assert store.read_envelope(handoff_id) is None


class TestNonHandoffCompatibility:
    def test_write_packet_no_ctx(self, store):
        p = ContextPacket(
            packet_id=_id("ctx_"), session_id="sess",
            timestamp=datetime.now(timezone.utc).isoformat(),
            content={"text": "test", "structured": None},
        )
        store.write_packet(p)  # No ctx required
        assert store.read_packet(p.packet_id) is not None

    def test_write_receipt_no_ctx(self, store):
        r = Receipt(
            receipt_id=_id("rct_"), session_id="sess",
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(agent="test"),
            result=Result(summary="test"),
        )
        store.write_receipt(r)  # No ctx required
        assert store.read_receipt(r.receipt_id) is not None

    def test_rehydrate_no_ctx(self, store):
        """Rehydration does not require context."""
        p = ContextPacket(
            packet_id=_id("ctx_"), session_id="sess",
            timestamp=datetime.now(timezone.utc).isoformat(),
            content={"text": "test", "structured": None},
        )
        store.write_packet(p)
        packets, receipts = store.rehydrate("sess")
        assert len(packets) == 0  # No receipts reference this packet
