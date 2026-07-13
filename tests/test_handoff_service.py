"""Tests for TrustedHandoffService — full create/read/list/resolve flows."""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
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
def target_ctx():
    return VerifiedExecutionContext(
        principal="agent-b",
        session_id="session-target",
        store_namespace="test-ns",
        provider="process-bound",
        issuer="test-harness",
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )


@pytest.fixture
def unrelated_ctx():
    return VerifiedExecutionContext(
        principal="agent-c",
        session_id="session-unrelated",
        store_namespace="test-ns",
        provider="process-bound",
        issuer="test-harness",
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def policy():
    config = {"taa": {"namespace": "test-ns", "policy_version": "1"}}
    return HandoffPolicy(config)


@pytest.fixture
def service(store, ctx, policy):
    return TrustedHandoffService(store, ctx, policy)


@pytest.fixture
def target_service(store, target_ctx, policy):
    return TrustedHandoffService(store, target_ctx, policy)


def _setup_evidence(store):
    """Create a packet and receipt for handoff references."""
    packet = ContextPacket(
        packet_id=_id("ctx_"),
        session_id="session-source",
        timestamp=datetime.now(timezone.utc).isoformat(),
        content={"text": "evidence", "structured": None},
    )
    receipt = Receipt(
        receipt_id=_id("rct_"),
        session_id="session-source",
        sequence=1,
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=Action(agent="agent-a"),
        result=Result(summary="evidence recorded"),
    )
    store.write_packet(packet)
    store.write_receipt(receipt)
    return packet, receipt


class TestHandoffServiceCreate:
    def test_create_success(self, service, store):
        packet, receipt = _setup_evidence(store)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test handoff",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        result = service.create_handoff(body)
        assert result["status"] == "ok"
        assert result["handoff_id"].startswith("hof_")
        assert result["audit_status"] == "persisted"

    def test_create_rejects_prohibited_fields(self, service):
        body = {
            "source_agent": "attacker",
            "handoff": {"target_session_id": "session-target"},
        }
        result = service.create_handoff(body)
        assert result["status"] == "unavailable"

    def test_create_rejects_missing_target(self, service):
        body = {"handoff": {}}
        result = service.create_handoff(body)
        assert result["status"] == "unavailable"

    def test_create_rejects_same_session(self, service):
        body = {
            "handoff": {
                "target_session_id": "session-source",  # same as ctx
            }
        }
        result = service.create_handoff(body)
        assert result["status"] == "unavailable"

    def test_create_envelope_stored(self, service, store):
        packet, receipt = _setup_evidence(store)
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
        envelope = store.read_envelope(result["handoff_id"])
        assert envelope is not None
        assert envelope.source_principal == "agent-a"
        assert envelope.source_session_id == "session-source"


class TestHandoffServiceRead:
    def test_source_can_read(self, service, store):
        packet, receipt = _setup_evidence(store)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        create_result = service.create_handoff(body)
        read_result = service.read_handoff({"handoff_id": create_result["handoff_id"]})
        assert read_result["status"] == "ok"
        assert read_result["handoff"]["handoff_id"] == create_result["handoff_id"]

    def test_target_can_read(self, target_service, store, service):
        packet, receipt = _setup_evidence(store)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        create_result = service.create_handoff(body)
        read_result = target_service.read_handoff({"handoff_id": create_result["handoff_id"]})
        assert read_result["status"] == "ok"

    def test_unrelated_cannot_read(self, unrelated_service, store, service):
        packet, receipt = _setup_evidence(store)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        create_result = service.create_handoff(body)
        read_result = unrelated_service.read_handoff({"handoff_id": create_result["handoff_id"]})
        assert read_result["status"] == "unavailable"

    def test_nonexistent_handoff(self, service):
        result = service.read_handoff({"handoff_id": "hof_nonexistent"})
        assert result["status"] == "unavailable"

    def test_read_rejects_prohibited_fields(self, service):
        result = service.read_handoff({"handoff_id": "hof_test", "principal": "attacker"})
        assert result["status"] == "unavailable"


class TestThreeNamespaceAgreement:
    """All three namespaces must agree: ctx, envelope, and policy namespace."""

    @pytest.fixture
    def mismatched_policy(self):
        return HandoffPolicy({"taa": {"namespace": "expected-ns", "policy_version": "1"}})

    @pytest.fixture
    def mismatched_ctx(self):
        return VerifiedExecutionContext(
            principal="agent-a", session_id="session-source",
            store_namespace="other-ns", provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

    def test_read_denied_when_policy_namespace_differs(self, store, mismatched_policy, mismatched_ctx):
        """When policy namespace differs from ctx/envelope, read returns unavailable."""
        # Create a handoff with a service that has matching ctx/envelope
        matching_policy = HandoffPolicy({"taa": {"namespace": "other-ns", "policy_version": "1"}})
        matching_service = TrustedHandoffService(store, mismatched_ctx, matching_policy)
        packet, receipt = _setup_evidence(store)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        create_result = matching_service.create_handoff(body)
        assert create_result["status"] == "ok"

        # Now try to read with a service whose policy namespace is "expected-ns"
        # while ctx and envelope are "other-ns"
        read_service = TrustedHandoffService(store, mismatched_ctx, mismatched_policy)
        result = read_service.read_handoff({"handoff_id": create_result["handoff_id"]})
        assert result["status"] == "unavailable"

    def test_resolve_denied_when_policy_namespace_differs(self, store, mismatched_policy, mismatched_ctx):
        """When policy namespace differs from ctx/envelope, resolve returns unavailable."""
        matching_policy = HandoffPolicy({"taa": {"namespace": "other-ns", "policy_version": "1"}})
        matching_service = TrustedHandoffService(store, mismatched_ctx, matching_policy)
        packet, receipt = _setup_evidence(store)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        create_result = matching_service.create_handoff(body)
        assert create_result["status"] == "ok"

        # Target session context with same namespace as envelope
        target_ctx = VerifiedExecutionContext(
            principal="agent-b", session_id="session-target",
            store_namespace="other-ns", provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        resolve_service = TrustedHandoffService(store, target_ctx, mismatched_policy)
        result = resolve_service.resolve_handoff({"handoff_id": create_result["handoff_id"]})
        assert result["status"] == "unavailable"


class TestHandoffServiceList:
    def test_list_own_session(self, target_service, store, service):
        packet, receipt = _setup_evidence(store)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        service.create_handoff(body)
        result = target_service.list_handoffs({"target_session_id": "session-target"})
        assert result["status"] == "ok"
        assert len(result["handoffs"]) == 1

    def test_list_other_session_denied(self, service):
        result = service.list_handoffs({"target_session_id": "session-other"})
        assert result["status"] == "unavailable"

    def test_list_empty(self, target_service):
        result = target_service.list_handoffs({"target_session_id": "session-target"})
        assert result["status"] == "ok"
        assert len(result["handoffs"]) == 0


class TestHandoffServiceResolve:
    def test_target_can_resolve(self, target_service, store, service):
        packet, receipt = _setup_evidence(store)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        create_result = service.create_handoff(body)
        result = target_service.resolve_handoff({"handoff_id": create_result["handoff_id"]})
        assert result["status"] == "ok"
        assert result["source_principal"] == "agent-a"
        assert len(result["packets"]) == 1
        assert len(result["receipts"]) == 1

    def test_source_cannot_resolve(self, service, store):
        packet, receipt = _setup_evidence(store)
        body = {
            "handoff": {
                "target_session_id": "session-target",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        }
        create_result = service.create_handoff(body)
        result = service.resolve_handoff({"handoff_id": create_result["handoff_id"]})
        assert result["status"] == "unavailable"


@pytest.fixture
def unrelated_service(store, unrelated_ctx, policy):
    return TrustedHandoffService(store, unrelated_ctx, policy)


# --- Failing store double for failure-containment tests ---

class _FailingStore:
    """Store double that raises on every handoff operation."""

    def read_envelope(self, handoff_id):
        raise RuntimeError("simulated envelope read failure")

    def read_handoff(self, handoff_id, ctx):
        raise RuntimeError("simulated handoff read failure")

    def list_handoffs_for_target(self, target_session_id, ctx, limit=100, target_agent=None):
        raise RuntimeError("simulated list failure")

    def resolve_handoff(self, handoff_id, ctx):
        raise RuntimeError("simulated resolve failure")

    def write_handoff_with_envelope(self, record, envelope, ctx):
        raise RuntimeError("simulated atomic write failure")

    def write_adapter_audit(self, audit):
        pass  # audit may succeed or fail independently


@pytest.fixture
def failing_store():
    return _FailingStore()


@pytest.fixture
def failing_service(failing_store, ctx, policy):
    return TrustedHandoffService(failing_store, ctx, policy)


_GENERIC_UNAVAILABLE = {
    "status": "unavailable",
    "handoff_id": None,
    "audit_id": None,
    "audit_status": None,
}


class TestStoreFailureContainment:
    """Every store exception must return generic unavailable, never propagate."""

    def test_create_atomic_write_failure(self, failing_service):
        result = failing_service.create_handoff({
            "handoff": {"target_session_id": "session-target", "target_agent": "agent-b"}
        })
        assert result == _GENERIC_UNAVAILABLE

    def test_read_envelope_failure(self, failing_service):
        result = failing_service.read_handoff({"handoff_id": "hof_test"})
        assert result == _GENERIC_UNAVAILABLE

    def test_read_record_failure(self, ctx, policy):
        """Simulate a store that returns envelope but fails on record read."""
        class _ReadFailingStore:
            def read_envelope(self, handoff_id):
                from thinkos.schema.security_envelope import HandoffSecurityEnvelope
                return HandoffSecurityEnvelope(
                    envelope_id="env_test", handoff_id=handoff_id,
                    source_principal="agent-a", source_session_id="session-source",
                    target_session_intent="session-target", store_namespace="test-ns",
                    provider="process-bound", issuer="test-harness",
                    policy_version="1", created_at="2026-01-01T00:00:00Z",
                )
            def read_handoff(self, handoff_id, ctx):
                raise RuntimeError("simulated record read failure")
            def write_adapter_audit(self, audit):
                pass

        svc = TrustedHandoffService(_ReadFailingStore(), ctx, policy)
        result = svc.read_handoff({"handoff_id": "hof_test"})
        assert result == _GENERIC_UNAVAILABLE

    def test_list_failure(self, failing_service):
        result = failing_service.list_handoffs({"target_session_id": "session-target"})
        assert result == _GENERIC_UNAVAILABLE

    def test_resolve_envelope_failure(self, failing_service):
        result = failing_service.resolve_handoff({"handoff_id": "hof_test"})
        assert result == _GENERIC_UNAVAILABLE

    def test_resolve_record_failure(self, ctx, policy):
        """Simulate a store that returns envelope but fails on resolve."""
        class _ResolveFailingStore:
            def read_envelope(self, handoff_id):
                from thinkos.schema.security_envelope import HandoffSecurityEnvelope
                return HandoffSecurityEnvelope(
                    envelope_id="env_test", handoff_id=handoff_id,
                    source_principal="agent-a", source_session_id="session-source",
                    target_session_intent="session-target", store_namespace="test-ns",
                    provider="process-bound", issuer="test-harness",
                    policy_version="1", created_at="2026-01-01T00:00:00Z",
                )
            def resolve_handoff(self, handoff_id, ctx):
                raise RuntimeError("simulated resolve failure")
            def write_adapter_audit(self, audit):
                pass

        svc = TrustedHandoffService(_ResolveFailingStore(), ctx, policy)
        result = svc.resolve_handoff({"handoff_id": "hof_test"})
        assert result == _GENERIC_UNAVAILABLE
