"""Tests for VerifiedExecutionContext."""

import time
from datetime import datetime, timezone, timedelta

import pytest
from thinkos.schema.verified_context import VerifiedExecutionContext


def _ctx(**kw):
    defaults = {
        "principal": "agent-a",
        "session_id": "session-1",
        "store_namespace": "test-ns",
        "provider": "process-bound",
        "issuer": "test-harness",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
    defaults.update(kw)
    return VerifiedExecutionContext(**defaults)


class TestVerifiedContext:
    def test_is_verified_for_process_bound(self):
        ctx = _ctx()
        assert ctx.is_verified is True

    def test_is_not_verified_for_none(self):
        ctx = _ctx(provider="none")
        assert ctx.is_verified is False

    def test_is_not_expired_when_future(self):
        ctx = _ctx()
        assert ctx.is_expired() is False

    def test_is_expired_when_past(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        ctx = _ctx(expires_at=past)
        assert ctx.is_expired() is True

    def test_not_expired_when_none(self):
        ctx = _ctx(expires_at=None)
        assert ctx.is_expired() is False

    def test_is_expired_when_malformed(self):
        ctx = _ctx(expires_at="not-a-timestamp")
        assert ctx.is_expired() is True

    def test_immutable(self):
        ctx = _ctx()
        with pytest.raises(Exception):
            ctx.principal = "other"  # frozen dataclass

    def test_no_policy_booleans(self):
        ctx = _ctx()
        assert not hasattr(ctx, "can_create_handoff")
        assert not hasattr(ctx, "can_read_handoff")
        assert not hasattr(ctx, "can_list_handoffs")
        assert not hasattr(ctx, "can_resolve_handoff")
