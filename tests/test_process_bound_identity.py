"""Tests for ProcessBoundIdentityProvider."""

import os
import pytest
from thinkos.identity.process_bound import ProcessBoundIdentityProvider


def _set_env(principal="agent-a", session="session-1", namespace="test-ns",
             issuer="test-harness", ttl="3600"):
    """Set all required env vars. Returns a cleanup function."""
    if principal is not None:
        os.environ["THINKOS_PRINCIPAL"] = principal
    if session is not None:
        os.environ["THINKOS_SESSION_ID"] = session
    if namespace is not None:
        os.environ["THINKOS_NAMESPACE"] = namespace
    if issuer is not None:
        os.environ["THINKOS_ISSUER"] = issuer
    if ttl is not None:
        os.environ["THINKOS_TTL_SECONDS"] = ttl


def _clear_env():
    for k in ["THINKOS_PRINCIPAL", "THINKOS_SESSION_ID", "THINKOS_NAMESPACE",
              "THINKOS_ISSUER", "THINKOS_TTL_SECONDS"]:
        os.environ.pop(k, None)


class TestProcessBoundIdentity:
    def test_complete_env_bundle(self):
        """Complete environment bundle succeeds."""
        _set_env()
        try:
            provider = ProcessBoundIdentityProvider()
            ctx = provider.get_context()
            assert ctx.principal == "agent-a"
            assert ctx.session_id == "session-1"
            assert ctx.store_namespace == "test-ns"
            assert ctx.issuer == "test-harness"
            assert ctx.provider == "process-bound"
            assert ctx.is_verified is True
            assert ctx.is_expired() is False
        finally:
            _clear_env()

    def test_complete_config_bundle(self):
        """Complete config bundle succeeds."""
        config = {
            "taa": {
                "principal": "agent-b",
                "session_id": "session-2",
                "namespace": "other-ns",
                "issuer": "config-harness",
                "ttl_seconds": 7200,
            }
        }
        provider = ProcessBoundIdentityProvider(config)
        ctx = provider.get_context()
        assert ctx.principal == "agent-b"
        assert ctx.session_id == "session-2"
        assert ctx.store_namespace == "other-ns"
        assert ctx.issuer == "config-harness"

    def test_partial_env_bundle_fails(self):
        """Partial environment bundle raises ValueError."""
        _set_env(principal="agent-a", session=None, namespace=None)
        try:
            with pytest.raises(ValueError, match="Partial environment identity bundle"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_env_config_mixing_not_supported(self):
        """When env vars are present, config values are not used as fallback."""
        _set_env()
        try:
            config = {"taa": {"principal": "config-agent"}}
            provider = ProcessBoundIdentityProvider(config)
            ctx = provider.get_context()
            assert ctx.principal == "agent-a"
        finally:
            _clear_env()

    def test_empty_principal_fails(self):
        """Empty principal raises ValueError."""
        _set_env(principal="")
        try:
            with pytest.raises(ValueError, match="principal"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_whitespace_principal_fails(self):
        """Whitespace-only principal raises ValueError."""
        _set_env(principal="  ")
        try:
            with pytest.raises(ValueError, match="principal"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_leading_trailing_whitespace_fails(self):
        """Leading/trailing whitespace is rejected."""
        _set_env(principal=" agent-a ")
        try:
            with pytest.raises(ValueError, match="whitespace"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_control_characters_fail(self):
        """Control characters in principal are rejected.

        Uses a non-null control character since os.environ rejects null bytes.
        """
        _set_env(principal="agent\x01a")
        try:
            with pytest.raises(ValueError, match="control"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_tab_character_fails(self):
        """Tab character in principal is rejected."""
        _set_env(principal="agent\ta")
        try:
            with pytest.raises(ValueError, match="control"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_newline_character_fails(self):
        """Newline character in principal is rejected."""
        _set_env(principal="agent\na")
        try:
            with pytest.raises(ValueError, match="control"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_oversized_principal_fails(self):
        """Principal exceeding 256 UTF-8 bytes is rejected."""
        _set_env(principal="a" * 300)
        try:
            with pytest.raises(ValueError, match="256"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_invalid_namespace_fails(self):
        """Namespace with invalid characters is rejected."""
        _set_env(namespace="test ns!")
        try:
            with pytest.raises(ValueError, match="namespace"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_ttl_zero_fails(self):
        """TTL of 0 is rejected."""
        _set_env(ttl="0")
        try:
            with pytest.raises(ValueError, match="TTL"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_ttl_negative_fails(self):
        """Negative TTL is rejected."""
        _set_env(ttl="-1")
        try:
            with pytest.raises(ValueError, match="TTL"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_ttl_malformed_fails(self):
        """Non-integer TTL is rejected."""
        _set_env(ttl="abc")
        try:
            with pytest.raises(ValueError, match="TTL"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_ttl_above_max_fails(self):
        """TTL exceeding 86400 is rejected."""
        _set_env(ttl="86401")
        try:
            with pytest.raises(ValueError, match="86400"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_context_captured_once(self):
        """get_context() returns the same object for process lifetime."""
        _set_env()
        try:
            provider = ProcessBoundIdentityProvider()
            ctx1 = provider.get_context()
            ctx2 = provider.get_context()
            assert ctx1 is ctx2
        finally:
            _clear_env()

    def test_missing_issuer_fails(self):
        """Missing THINKOS_ISSUER in env bundle fails."""
        _set_env(issuer=None)
        try:
            with pytest.raises(ValueError, match="THINKOS_ISSUER"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()

    def test_missing_ttl_fails(self):
        """Missing THINKOS_TTL_SECONDS in env bundle fails."""
        _set_env(ttl=None)
        try:
            with pytest.raises(ValueError, match="THINKOS_TTL_SECONDS"):
                ProcessBoundIdentityProvider()
        finally:
            _clear_env()
