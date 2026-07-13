"""Tests for HandoffPolicy."""

from datetime import datetime, timezone, timedelta

import pytest
from thinkos.schema.verified_context import VerifiedExecutionContext
from thinkos.schema.security_envelope import HandoffSecurityEnvelope
from thinkos.policy.handoff_policy import HandoffPolicy


def _ctx(**kw):
    defaults = {
        "principal": "agent-a",
        "session_id": "session-source",
        "store_namespace": "test-ns",
        "provider": "process-bound",
        "issuer": "test-harness",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
    defaults.update(kw)
    return VerifiedExecutionContext(**defaults)


def _expired_ctx(**kw):
    kw.setdefault("expires_at", (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
    return _ctx(**kw)


def _unverified_ctx(**kw):
    kw.setdefault("provider", "none")
    return _ctx(**kw)


def _envelope(**kw):
    defaults = {
        "envelope_id": "env_test",
        "handoff_id": "hof_test",
        "source_principal": "agent-a",
        "source_session_id": "session-source",
        "target_session_intent": "session-target",
        "store_namespace": "test-ns",
        "provider": "process-bound",
        "issuer": "test-harness",
        "policy_version": "1",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    defaults.update(kw)
    return HandoffSecurityEnvelope(**defaults)


@pytest.fixture
def policy():
    config = {"taa": {"namespace": "test-ns", "policy_version": "1"}}
    return HandoffPolicy(config)


class TestAuthorizeCreate:
    def test_valid_create_allowed(self, policy):
        ctx = _ctx()
        decision = policy.authorize_create(ctx, "session-target")
        assert decision.allowed is True

    def test_unverified_denied(self, policy):
        ctx = _unverified_ctx()
        decision = policy.authorize_create(ctx, "session-target")
        assert decision.allowed is False

    def test_expired_denied(self, policy):
        ctx = _expired_ctx()
        decision = policy.authorize_create(ctx, "session-target")
        assert decision.allowed is False

    def test_namespace_mismatch_denied(self, policy):
        ctx = _ctx(store_namespace="other-ns")
        decision = policy.authorize_create(ctx, "session-target")
        assert decision.allowed is False

    def test_missing_target_session_denied(self, policy):
        ctx = _ctx()
        decision = policy.authorize_create(ctx, "")
        assert decision.allowed is False

    def test_same_session_denied(self, policy):
        ctx = _ctx(session_id="session-target")
        decision = policy.authorize_create(ctx, "session-target")
        assert decision.allowed is False

    def test_generic_external_reason(self, policy):
        ctx = _unverified_ctx()
        decision = policy.authorize_create(ctx, "session-target")
        assert "not available" in decision.external_reason.lower()


class TestAuthorizeRead:
    def test_source_session_allowed(self, policy):
        ctx = _ctx(session_id="session-source")
        env = _envelope()
        decision = policy.authorize_read(ctx, env)
        assert decision.allowed is True

    def test_target_session_allowed(self, policy):
        ctx = _ctx(session_id="session-target")
        env = _envelope()
        decision = policy.authorize_read(ctx, env)
        assert decision.allowed is True

    def test_unrelated_session_denied(self, policy):
        ctx = _ctx(session_id="session-unrelated")
        env = _envelope()
        decision = policy.authorize_read(ctx, env)
        assert decision.allowed is False

    def test_missing_envelope_denied(self, policy):
        ctx = _ctx()
        decision = policy.authorize_read(ctx, None)
        assert decision.allowed is False

    def test_namespace_mismatch_denied(self, policy):
        ctx = _ctx(session_id="session-source")
        env = _envelope(store_namespace="other-ns")
        decision = policy.authorize_read(ctx, env)
        assert decision.allowed is False

    def test_three_namespace_read_denied(self):
        """All three namespaces must agree: ctx, envelope, and policy."""
        policy = HandoffPolicy({"taa": {"namespace": "expected-ns", "policy_version": "1"}})
        ctx = _ctx(store_namespace="other-ns")
        env = _envelope(store_namespace="other-ns")
        decision = policy.authorize_read(ctx, env)
        assert decision.allowed is False


class TestAuthorizeList:
    def test_own_session_allowed(self, policy):
        ctx = _ctx(session_id="session-target")
        decision = policy.authorize_list(ctx, "session-target")
        assert decision.allowed is True

    def test_other_session_denied(self, policy):
        ctx = _ctx(session_id="session-source")
        decision = policy.authorize_list(ctx, "session-target")
        assert decision.allowed is False

    def test_unverified_denied(self, policy):
        ctx = _unverified_ctx(session_id="session-target")
        decision = policy.authorize_list(ctx, "session-target")
        assert decision.allowed is False


class TestAuthorizeResolve:
    def test_target_session_allowed(self, policy):
        ctx = _ctx(session_id="session-target")
        env = _envelope()
        decision = policy.authorize_resolve(ctx, env)
        assert decision.allowed is True

    def test_source_session_denied(self, policy):
        ctx = _ctx(session_id="session-source")
        env = _envelope()
        decision = policy.authorize_resolve(ctx, env)
        assert decision.allowed is False

    def test_missing_envelope_denied(self, policy):
        ctx = _ctx(session_id="session-target")
        decision = policy.authorize_resolve(ctx, None)
        assert decision.allowed is False

    def test_three_namespace_resolve_denied(self):
        """All three namespaces must agree for resolve: ctx, envelope, and policy."""
        policy = HandoffPolicy({"taa": {"namespace": "expected-ns", "policy_version": "1"}})
        ctx = _ctx(store_namespace="other-ns", session_id="session-target")
        env = _envelope(store_namespace="other-ns")
        decision = policy.authorize_resolve(ctx, env)
        assert decision.allowed is False
