"""Tests for connector-level handoff message routing and generic error behavior."""

import json
import io
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from thinkos.schema.verified_context import VerifiedExecutionContext
from thinkos.schema.handoff_record import HandoffRecord
from thinkos.schema.context_packet import ContextPacket
from thinkos.schema.receipt import Receipt, Action, Result
from thinkos.schema.security_envelope import HandoffSecurityEnvelope
from thinkos.store.sqlite_store import SQLiteStore
from thinkos.policy.handoff_policy import HandoffPolicy
from thinkos.service.handoff_service import TrustedHandoffService
from thinkos.engine import Engine


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4()}"


class _CaptureConnector:
    """Minimal connector that yields a fixed list of messages then EOF.
    Captures responses for assertion."""
    def __init__(self, messages):
        self.messages = list(messages)
        self.responses = []

    def read_message(self):
        if not self.messages:
            return None
        return self.messages.pop(0)

    def write_response(self, response):
        self.responses.append(response)

    def write_error(self, msg):
        pass

    def close(self):
        pass


class TestGenericUnavailableResponse:
    """All denial cases must return the same external shape."""

    _EXPECTED_KEYS = {"status", "handoff_id", "audit_id", "audit_status"}
    _EXPECTED_STATUS = "unavailable"

    def _assert_generic_unavailable(self, result: dict):
        assert result["status"] == self._EXPECTED_STATUS
        assert result["handoff_id"] is None
        assert result["audit_id"] is None or result["audit_status"] == "unavailable"
        # No extra fields that could distinguish cases
        assert set(result.keys()) == self._EXPECTED_KEYS

    @pytest.fixture
    def store(self):
        s = SQLiteStore(":memory:")
        yield s
        s.close()

    @pytest.fixture
    def ctx(self):
        return VerifiedExecutionContext(
            principal="agent-a", session_id="session-source",
            store_namespace="test-ns", provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

    @pytest.fixture
    def policy(self):
        return HandoffPolicy({"taa": {"namespace": "test-ns", "policy_version": "1"}})

    def test_nonexistent_handoff(self, store, ctx, policy):
        service = TrustedHandoffService(store, ctx, policy)
        result = service.read_handoff({"handoff_id": "hof_nonexistent"})
        self._assert_generic_unavailable(result)

    def test_unauthorized_session(self, store, ctx, policy):
        """Unrelated session trying to read."""
        service = TrustedHandoffService(store, ctx, policy)
        # Create a handoff first
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
        create_result = service.create_handoff(body)
        assert create_result["status"] == "ok"

        # Now try to read with an unrelated session
        unrelated_ctx = VerifiedExecutionContext(
            principal="agent-c", session_id="session-unrelated",
            store_namespace="test-ns", provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        unrelated_service = TrustedHandoffService(store, unrelated_ctx, policy)
        result = unrelated_service.read_handoff({"handoff_id": create_result["handoff_id"]})
        self._assert_generic_unavailable(result)

    def test_legacy_record(self, store, ctx, policy):
        """Legacy records (no envelope) return generic unavailable."""
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
        # Create legacy record via direct SQL (no envelope)
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

        service = TrustedHandoffService(store, ctx, policy)
        result = service.read_handoff({"handoff_id": record.handoff_id})
        self._assert_generic_unavailable(result)

    def test_malformed_handoff_id(self, store, ctx, policy):
        service = TrustedHandoffService(store, ctx, policy)
        result = service.read_handoff({"handoff_id": ""})
        self._assert_generic_unavailable(result)

    def test_prohibited_field_injection(self, store, ctx, policy):
        """All prohibited fields produce the same generic response."""
        service = TrustedHandoffService(store, ctx, policy)
        for field in ["source_agent", "source_session_id", "principal",
                       "issuer", "namespace", "store_namespace", "provider",
                       "policy_version", "authorization", "capability",
                       "verified_context", "security_envelope"]:
            body = {
                field: "injected",
                "handoff": {"target_session_id": "session-target", "target_agent": "agent-b"},
            }
            result = service.create_handoff(body)
            self._assert_generic_unavailable(result)

    def test_indistinguishable_error_responses(self, store, ctx, policy):
        """Multiple different failure modes produce byte-identical unavailable responses."""
        service = TrustedHandoffService(store, ctx, policy)

        # Collect responses from different failure modes
        responses = []

        # 1. Nonexistent
        responses.append(service.read_handoff({"handoff_id": "hof_nonexistent"}))

        # 2. Prohibited field
        responses.append(service.create_handoff({"source_agent": "x", "handoff": {}}))

        # 3. Missing target
        responses.append(service.create_handoff({"handoff": {}}))

        # All should have identical keys and status
        for r in responses:
            assert set(r.keys()) == {"status", "handoff_id", "audit_id", "audit_status"}
            assert r["status"] == "unavailable"
            assert r["handoff_id"] is None


class TestEngineHandoffContainment:
    """Engine-level handoff failure containment.

    A real store exception during a handoff operation must:
    1. Return the exact six-key engine-level generic unavailable response.
    2. Not expose internal failure details.
    3. Leave the engine loop available for a subsequent request.

    This test injects a one-shot store exception by monkey-patching
    the store's read_envelope method. The failing request goes through
    an actual Engine instance with a _TestConnector. Only engine.run()
    is called — no direct service calls after the patch begins.
    """

    @pytest.fixture
    def store(self):
        s = SQLiteStore(":memory:")
        yield s
        s.close()

    @pytest.fixture
    def ctx(self):
        return VerifiedExecutionContext(
            principal="agent-a", session_id="session-source",
            store_namespace="test-ns", provider="process-bound",
            issuer="test-harness",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

    @pytest.fixture
    def policy(self):
        return HandoffPolicy({"taa": {"namespace": "test-ns", "policy_version": "1"}})

    def test_injected_store_exception_contained_engine_loop_continues(self, store, ctx, policy):
        """A real store exception during handoff returns generic unavailable
        and the engine loop continues to process the next message.

        The injected exception simulates a transient store failure (e.g.,
        SQLite disk-full or I/O error). The service layer catches it and
        returns _GENERIC_UNAVAILABLE. The engine wraps it in the handoff_result
        envelope and continues the loop to consume the next message.
        """
        from unittest.mock import patch

        # Create a handoff first so there's something to read
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

        create_result = TrustedHandoffService(store, ctx, policy).create_handoff({
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
                "packet_ids": [packet.packet_id],
                "receipt_ids": [receipt.receipt_id],
            }
        })
        assert create_result["status"] == "ok"
        handoff_id = create_result["handoff_id"]

        # Build two handoff_read messages with distinct message IDs
        msg_id_1 = "msg_fail_001"
        msg_id_2 = "msg_success_002"

        messages = [
            {
                "type": "handoff_read",
                "message_id": msg_id_1,
                "session_id": "session-source",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "handoff_id": handoff_id,
            },
            {
                "type": "handoff_read",
                "message_id": msg_id_2,
                "session_id": "session-source",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "handoff_id": handoff_id,
            },
        ]

        # Create a capture connector
        connector = _CaptureConnector(messages)

        # Create identity provider stub that returns the target context
        class _IdentityStub:
            def get_context(self):
                return ctx

        # Create the handoff service
        service = TrustedHandoffService(store, ctx, policy)

        # Create the engine with TAA enabled
        config = {
            "taa": {"enabled": True, "namespace": "test-ns", "policy_version": "1"},
            "gates": {"default": "always_allow", "overrides": {}},
        }
        engine = Engine(store, connector, {}, {}, config,
                        handoff_service=service,
                        identity_provider=_IdentityStub())

        # Inject a one-shot store exception into read_envelope
        original_read_envelope = store.read_envelope
        call_count = [0]
        failure_count = [0]

        def _failing_read_envelope(env_handoff_id):
            call_count[0] += 1
            if call_count[0] == 1:
                failure_count[0] += 1
                raise RuntimeError("SIMULATED_STORE_FAILURE")
            return original_read_envelope(env_handoff_id)

        with patch.object(store, "read_envelope", _failing_read_envelope):
            engine.run()

        # Assertions
        assert len(connector.responses) == 2, (
            f"Expected exactly 2 engine responses, got {len(connector.responses)}"
        )

        # Response 1: the exact six-key engine-level generic unavailable
        resp1 = connector.responses[0]
        assert set(resp1.keys()) == {
            "type", "in_response_to", "status",
            "handoff_id", "audit_id", "audit_status",
        }, f"Response 1 keys: {set(resp1.keys())}"
        assert resp1["type"] == "handoff_result"
        assert resp1["in_response_to"] == msg_id_1
        assert resp1["status"] == "unavailable"
        assert resp1["handoff_id"] is None
        assert resp1["audit_id"] is None
        assert resp1["audit_status"] is None
        # No exception detail leaks into the public response
        assert "SIMULATED_STORE_FAILURE" not in json.dumps(resp1)

        # Response 2: succeeds
        resp2 = connector.responses[1]
        assert resp2["type"] == "handoff_result"
        assert resp2["in_response_to"] == msg_id_2
        assert resp2["status"] == "ok"
        assert resp2.get("handoff", {}).get("handoff_id") == handoff_id

        # The simulated failure fired exactly once.
        # Subsequent calls (audit persistence, second request) succeed.
        assert failure_count[0] == 1, (
            f"Expected exactly 1 simulated failure, got {failure_count[0]}"
        )
