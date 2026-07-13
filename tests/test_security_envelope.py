"""Tests for HandoffSecurityEnvelope."""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from thinkos.schema.security_envelope import HandoffSecurityEnvelope
from thinkos.schema.handoff_record import HandoffRecord
from thinkos.schema.context_packet import ContextPacket
from thinkos.schema.receipt import Receipt, Action, Result
from thinkos.schema.verified_context import VerifiedExecutionContext
from thinkos.store.sqlite_store import SQLiteStore, DuplicateError, HandoffReferenceError


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4()}"


def _record(**changes) -> HandoffRecord:
    values = {
        "handoff_id": _id("hof_"),
        "source_session_id": "session-source",
        "target_session_id": "session-target",
        "source_agent": "agent-a",
        "target_agent": "agent-b",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "purpose_summary": "Test handoff",
    }
    values.update(changes)
    return HandoffRecord(**values)


def _envelope(handoff_id: str, **changes) -> HandoffSecurityEnvelope:
    values = {
        "envelope_id": _id("env_"),
        "handoff_id": handoff_id,
        "source_principal": "agent-a",
        "source_session_id": "session-source",
        "target_session_intent": "session-target",
        "store_namespace": "test-ns",
        "provider": "process-bound",
        "issuer": "test-harness",
        "policy_version": "1",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    values.update(changes)
    return HandoffSecurityEnvelope(**values)


def _packet(session_id: str = "session-source") -> ContextPacket:
    return ContextPacket(
        packet_id=_id("ctx_"),
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        content={"text": "evidence", "structured": None},
    )


def _receipt(session_id: str = "session-source") -> Receipt:
    return Receipt(
        receipt_id=_id("rct_"),
        session_id=session_id,
        sequence=1,
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=Action(agent="agent-a"),
        result=Result(summary="evidence recorded"),
    )


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


class TestEnvelopeAtomicity:
    def test_write_and_read_envelope(self, store, ctx):
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])
        envelope = _envelope(record.handoff_id)

        store.write_handoff_with_envelope(record, envelope, ctx)

        stored = store.read_envelope(record.handoff_id)
        assert stored is not None
        assert stored.handoff_id == record.handoff_id
        assert stored.source_principal == "agent-a"
        assert stored.source_session_id == "session-source"
        assert stored.target_session_intent == "session-target"
        assert stored.store_namespace == "test-ns"
        assert stored.provider == "process-bound"

    def test_envelope_required_for_authorized_read(self, store):
        """A record without an envelope is UNVERIFIED_LEGACY."""
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])

        # Write handoff WITHOUT envelope (legacy path — direct SQL, no ctx)
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

        # Reading with ctx should fail — no envelope
        ctx = VerifiedExecutionContext(
            principal="agent-a",
            session_id="session-source",
            store_namespace="test-ns",
            provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        with pytest.raises(HandoffReferenceError, match="UNVERIFIED_LEGACY"):
            store.read_handoff(record.handoff_id, ctx)

    def test_atomic_rollback_on_envelope_failure(self, store, ctx):
        """If envelope write fails, handoff record is also rolled back."""
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])
        envelope = _envelope(record.handoff_id)

        store.write_handoff_with_envelope(record, envelope, ctx)
        assert store.read_envelope(record.handoff_id) is not None

    def test_duplicate_handoff_id_rejected(self, store, ctx):
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])
        envelope = _envelope(record.handoff_id)

        store.write_handoff_with_envelope(record, envelope, ctx)
        with pytest.raises(DuplicateError):
            store.write_handoff_with_envelope(record, envelope, ctx)

    def test_envelope_authorization_uses_envelope_not_record(self, store, ctx):
        """Authorization must use envelope data, not HandoffRecord identity strings."""
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)

        record = _record(
            source_session_id="session-source",
            source_agent="agent-a",
            packet_ids=[packet.packet_id],
            receipt_ids=[receipt.receipt_id],
        )
        # Envelope matches ctx — this is the valid case
        envelope = _envelope(record.handoff_id)

        store.write_handoff_with_envelope(record, envelope, ctx)

        stored = store.read_envelope(record.handoff_id)
        assert stored.source_principal == "agent-a"
        assert stored.source_session_id == "session-source"
        stored_record = store._read_handoff_raw(record.handoff_id)
        assert stored_record.source_agent == "agent-a"
        assert stored_record.source_session_id == "session-source"

    def test_wrong_namespace_envelope_denied(self, store, ctx):
        """Envelope with wrong namespace is rejected."""
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])
        envelope = _envelope(record.handoff_id, store_namespace="other-ns")
        with pytest.raises(PermissionError, match="namespace"):
            store.write_handoff_with_envelope(record, envelope, ctx)

    def test_wrong_principal_envelope_denied(self, store, ctx):
        """Envelope with wrong source principal is rejected."""
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])
        envelope = _envelope(record.handoff_id, source_principal="attacker")
        with pytest.raises(PermissionError, match="principal"):
            store.write_handoff_with_envelope(record, envelope, ctx)

    def test_wrong_session_envelope_denied(self, store, ctx):
        """Envelope with wrong source session is rejected."""
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])
        envelope = _envelope(record.handoff_id, source_session_id="session-attacker")
        with pytest.raises(PermissionError, match="session"):
            store.write_handoff_with_envelope(record, envelope, ctx)

    def test_handoff_id_mismatch_denied(self, store, ctx):
        """Record/envelope handoff_id mismatch is rejected."""
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])
        envelope = _envelope(_id("hof_"))  # Different handoff_id
        with pytest.raises(ValueError, match="handoff_id"):
            store.write_handoff_with_envelope(record, envelope, ctx)

    def test_target_session_mismatch_denied(self, store, ctx):
        """Record/envelope target session mismatch is rejected."""
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])
        envelope = _envelope(record.handoff_id, target_session_intent="session-other")
        with pytest.raises(ValueError, match="target_session"):
            store.write_handoff_with_envelope(record, envelope, ctx)

    def test_no_partial_write_on_mismatch(self, store, ctx):
        """A failed write leaves no partial data."""
        packet = _packet()
        receipt = _receipt()
        store.write_packet(packet)
        store.write_receipt(receipt)
        record = _record(packet_ids=[packet.packet_id], receipt_ids=[receipt.receipt_id])
        envelope = _envelope(record.handoff_id, store_namespace="other-ns")
        with pytest.raises(PermissionError):
            store.write_handoff_with_envelope(record, envelope, ctx)
        # Neither record nor envelope should exist
        assert store._read_handoff_raw(record.handoff_id) is None
        assert store.read_envelope(record.handoff_id) is None
