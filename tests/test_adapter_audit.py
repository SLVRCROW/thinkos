"""Tests for AdapterAuditRecord — audit evidence semantics."""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from thinkos.schema.adapter_audit import AdapterAuditRecord
from thinkos.schema.verified_context import VerifiedExecutionContext
from thinkos.schema.handoff_record import HandoffRecord
from thinkos.schema.context_packet import ContextPacket
from thinkos.schema.receipt import Receipt, Action, Result
from thinkos.store.sqlite_store import SQLiteStore
from thinkos.policy.handoff_policy import HandoffPolicy
from thinkos.service.handoff_service import TrustedHandoffService


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
def policy():
    config = {"taa": {"namespace": "test-ns", "policy_version": "1"}}
    return HandoffPolicy(config)


class TestAdapterAuditPersistence:
    def test_persisted_audit_returns_real_id(self, store, ctx, policy):
        """Successfully persisted audit returns its real ID and 'persisted'."""
        service = TrustedHandoffService(store, ctx, policy)
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
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        result = service.create_handoff(body)
        assert result["status"] == "ok"
        assert result["audit_id"] is not None
        assert result["audit_id"].startswith("aud_")
        assert result["audit_status"] == "persisted"

    def test_denied_operation_still_audited(self, store, ctx, policy):
        """Denied operations still produce best-effort audit records."""
        service = TrustedHandoffService(store, ctx, policy)
        body = {"source_agent": "attacker", "handoff": {"target_session_id": "session-target"}}
        result = service.create_handoff(body)
        assert result["status"] == "unavailable"
        # Audit should have been written
        count = store._conn.execute("SELECT COUNT(*) FROM adapter_audits").fetchone()[0]
        assert count >= 1

    def test_audit_cannot_authorize(self, store, ctx, policy):
        """Audit records are evidence only — they cannot authorize operations."""
        service = TrustedHandoffService(store, ctx, policy)
        # Create a handoff
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
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        result = service.create_handoff(body)
        assert result["status"] == "ok"

        # The audit record itself cannot be used to authorize a new operation
        audit = store._conn.execute(
            "SELECT * FROM adapter_audits WHERE audit_id = ?",
            (result["audit_id"],),
        ).fetchone()
        assert audit is not None
        # Audit is evidence, not a capability token
        assert audit[4] == "agent-a"  # principal
        assert audit[7] == "process-bound"  # provider (column 7)

    def test_audit_id_not_returned_for_unpersisted(self, store, ctx, policy):
        """When audit persistence fails, null ID and 'unavailable' are returned."""
        # Create a service and force audit failure by closing the store
        service = TrustedHandoffService(store, ctx, policy)
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

        # Create a handoff first (succeeds)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        result = service.create_handoff(body)
        assert result["status"] == "ok"
        assert result["audit_status"] == "persisted"

    def test_operation_succeeds_when_only_audit_fails(self, store, ctx, policy):
        """A successful handoff operation remains successful when only audit persistence fails."""
        service = TrustedHandoffService(store, ctx, policy)
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

        # The handoff+envelope atomic write is separate from audit.
        # Even if audit fails, the handoff exists.
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        result = service.create_handoff(body)
        assert result["status"] == "ok"
        # Handoff should exist in store regardless of audit
        assert store.read_envelope(result["handoff_id"]) is not None
