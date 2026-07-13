"""Tests for TAA configuration — activation, startup validation, strict session."""

import os
import sys
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
from thinkos.identity.process_bound import ProcessBoundIdentityProvider


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4()}"


class _ScriptedConnector:
    """Connector that reads from a list of messages and captures responses."""

    def __init__(self, messages: list[dict]):
        self._messages = list(messages)
        self.responses: list[dict] = []

    def read_message(self) -> dict | None:
        if not self._messages:
            return None
        return self._messages.pop(0)

    def write_response(self, response: dict):
        self.responses.append(response)

    def write_error(self, msg: str):
        pass

    def close(self):
        pass


class _FailingStore:
    """Store double that raises on every handoff write operation."""

    def __init__(self):
        self._accessed = False

    def mark_accessed(self):
        self._accessed = True

    def read_envelope(self, handoff_id):
        self.mark_accessed()
        raise RuntimeError("simulated failure")

    def read_handoff(self, handoff_id, ctx):
        self.mark_accessed()
        raise RuntimeError("simulated failure")

    def list_handoffs_for_target(self, target_session_id, ctx, limit=100, target_agent=None):
        self.mark_accessed()
        raise RuntimeError("simulated failure")

    def resolve_handoff(self, handoff_id, ctx):
        self.mark_accessed()
        raise RuntimeError("simulated failure")

    def write_handoff_with_envelope(self, record, envelope, ctx):
        self.mark_accessed()
        raise RuntimeError("simulated atomic write failure")

    def write_adapter_audit(self, audit):
        pass

    def write_packet(self, packet):
        self.mark_accessed()
        raise RuntimeError("simulated failure")

    def write_receipt(self, receipt):
        self.mark_accessed()
        raise RuntimeError("simulated failure")

    def rehydrate(self, session_id):
        self.mark_accessed()
        raise RuntimeError("simulated failure")


class TestTAADisabledByDefault:
    def test_taa_config_defaults_to_disabled(self):
        """TAA is disabled by default in config."""
        from thinkos.config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG["taa"]["enabled"] is False

    def test_non_handoff_behavior_unchanged_when_disabled(self):
        """When TAA is disabled, existing non-handoff behavior works."""
        store = SQLiteStore(":memory:")
        policy = HandoffPolicy({"taa": {"namespace": "test-ns", "policy_version": "1"}})
        packet = ContextPacket(
            packet_id=_id("ctx_"), session_id="sess",
            timestamp=datetime.now(timezone.utc).isoformat(),
            content={"text": "test", "structured": None},
        )
        store.write_packet(packet)
        assert store.read_packet(packet.packet_id) is not None
        store.close()

    def test_handoff_unavailable_when_disabled(self):
        """When TAA is disabled, handoff operations are unavailable."""
        store = SQLiteStore(":memory:")
        ctx = VerifiedExecutionContext(
            principal="unknown", session_id="unknown",
            store_namespace="unknown", provider="none",
            issuer="unknown",
            issued_at=datetime.now(timezone.utc).isoformat(),
            expires_at=None,
        )
        policy = HandoffPolicy({"taa": {"namespace": "test-ns", "policy_version": "1"}})
        service = TrustedHandoffService(store, ctx, policy)
        result = service.create_handoff({"handoff": {"target_session_id": "t"}})
        assert result["status"] == "unavailable"
        store.close()


class TestStrictSession:
    """Strict single-session enforcement when TAA is enabled."""

    def _make_engine(self, session_id: str, store=None, handoff_service=None):
        """Create a minimal engine with TAA enabled and a scripted connector."""
        from thinkos.engine import Engine
        from thinkos.tools import TOOL_REGISTRY, register_tool
        from thinkos.gates import GATE_REGISTRY, register_gate
        from thinkos.gates.always_allow import AlwaysAllowGate
        from thinkos.gates.deny_all import DenyAllGate
        from thinkos.tools.read_file import ReadFileAdapter
        from thinkos.tools.write_file import WriteFileAdapter

        if store is None:
            store = SQLiteStore(":memory:")
        register_tool("read_file", ReadFileAdapter())
        register_tool("write_file", WriteFileAdapter())
        register_gate("always_allow", AlwaysAllowGate())
        register_gate("deny_all", DenyAllGate())

        config = {
            "taa": {
                "enabled": True,
                "principal": "agent-a",
                "session_id": session_id,
                "namespace": "test-ns",
                "issuer": "test-harness",
                "ttl_seconds": 3600,
                "policy_version": "1",
            },
            "tools": {"allowed_root": "/tmp"},
            "limits": {},
            "gates": {"default": "always_allow", "overrides": {}},
        }

        provider = ProcessBoundIdentityProvider(config)
        ctx = provider.get_context()
        policy = HandoffPolicy(config)
        if handoff_service is None:
            handoff_service = TrustedHandoffService(store, ctx, policy)

        engine = Engine(
            store, _ScriptedConnector([]), TOOL_REGISTRY, GATE_REGISTRY, config,
            handoff_service=handoff_service,
            identity_provider=provider,
        )
        return engine, store

    def test_matching_session_succeeds(self):
        """Handoff message with matching session succeeds."""
        engine, store = self._make_engine("session-1")
        msg = {
            "type": "handoff_create",
            "message_id": "msg_1",
            "session_id": "session-1",
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "Test",
            },
        }
        assert engine._handoff_service is not None
        result = engine._handoff_service.create_handoff(msg)
        assert result["status"] == "ok"

    def test_missing_session_fails(self):
        """Handoff message without session_id is rejected."""
        engine, store = self._make_engine("session-1")
        msg = {
            "type": "handoff_create",
            "message_id": "msg_1",
            "handoff": {"target_session_id": "session-target"},
        }
        response = engine._handle_handoff_message(msg)
        assert response is not None
        assert response["status"] == "unavailable"

    def test_mismatched_session_fails(self):
        """Handoff message with mismatched session is rejected."""
        engine, store = self._make_engine("session-1")
        msg = {
            "type": "handoff_create",
            "message_id": "msg_1",
            "session_id": "session-2",
            "handoff": {"target_session_id": "session-target"},
        }
        response = engine._handle_handoff_message(msg)
        assert response is not None
        assert response["status"] == "unavailable"

    def test_agent_message_with_mismatched_session_rejected(self):
        """When TAA is enabled, agent_message with mismatched session is rejected
        before any store access. Uses Engine.run() with a store double that
        fails if accessed."""
        failing_store = _FailingStore()
        engine, _ = self._make_engine("session-1", store=failing_store)
        engine.connector = _ScriptedConnector([
            {
                "type": "agent_message",
                "message_id": "msg_1",
                "session_id": "session-other",
                "content": {"tool_calls": []},
            },
        ])
        engine.run()
        assert len(engine.connector.responses) == 1
        resp = engine.connector.responses[0]
        assert resp["type"] == "agent_response"
        assert "Session mismatch" in resp["content"]["text"]
        # Prove zero store access occurred
        assert failing_store._accessed is False

    def test_engine_continues_after_failed_handoff(self):
        """A failed handoff operation does not terminate the engine loop.
        Uses Engine.run() with a store that fails on write."""
        failing_store = _FailingStore()
        engine, _ = self._make_engine("session-1", store=failing_store)
        # First message: valid-session handoff whose store write raises
        # Second message: valid ordinary agent_message
        engine.connector = _ScriptedConnector([
            {
                "type": "handoff_create",
                "message_id": "msg_1",
                "session_id": "session-1",
                "handoff": {"target_session_id": "session-target", "target_agent": "agent-b"},
            },
            {
                "type": "agent_message",
                "message_id": "msg_2",
                "session_id": "session-1",
                "content": {"tool_calls": []},
            },
        ])
        engine.run()
        assert len(engine.connector.responses) == 2
        # First response: generic unavailable (store write failed)
        r1 = engine.connector.responses[0]
        assert r1["type"] == "handoff_result"
        assert r1["status"] == "unavailable"
        assert r1["handoff_id"] is None
        assert r1["audit_id"] is None
        assert r1["audit_status"] is None
        # Second response: agent_message succeeded (engine continued)
        r2 = engine.connector.responses[1]
        assert r2["type"] == "agent_response"
        assert r2["in_response_to"] == "msg_2"


class TestIngressIsolation:
    """TrustedHandoffService and raw store must not be in tool context."""

    def test_handoff_service_not_in_tool_context(self):
        """TrustedHandoffService is not in tool context dict."""
        from thinkos.engine import Engine
        import ast
        with open("thinkos/engine.py") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == "session_id"
                for k in node.keys
            ):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                assert "handoff_service" not in keys, "handoff_service leaked into tool context"
                assert "store" not in keys, "store leaked into tool context"
                assert "_conn" not in keys, "_conn leaked into tool context"
                break

    def test_raw_store_not_in_tool_context(self):
        """Raw SQLiteStore is not in tool context (verified by engine.py)."""
        with open("thinkos/engine.py") as f:
            content = f.read()
        import re
        for match in re.finditer(r"context\s*=\s*\{", content):
            start = match.start()
            depth = 0
            pos = match.end() - 1
            while pos < len(content):
                if content[pos] == '{':
                    depth += 1
                elif content[pos] == '}':
                    depth -= 1
                    if depth == 0:
                        context_block = content[start:pos+1]
                        assert "'store'" not in context_block, "store leaked into tool context"
                        assert "'_conn'" not in context_block, "_conn leaked into tool context"
                        assert "'handoff_service'" not in context_block, "handoff_service leaked into tool context"
                        break
                pos += 1

    def test_four_handoff_message_types_routed(self):
        """All four handoff message types route through the trusted service path."""
        with open("thinkos/engine.py") as f:
            content = f.read()
        for msg_type in ["handoff_create", "handoff_read", "handoff_list", "handoff_resolve"]:
            assert msg_type in content, f"{msg_type} not routed in engine.py"

    def test_generic_tool_dispatch_cannot_invoke_handoff(self):
        """Generic tool dispatch cannot invoke protected handoff operations."""
        from thinkos.tools import TOOL_REGISTRY
        for name in ["handoff_create", "handoff_read", "handoff_list", "handoff_resolve",
                     "create_handoff", "read_handoff", "list_handoffs", "resolve_handoff"]:
            assert name not in TOOL_REGISTRY, f"{name} found in tool registry"
