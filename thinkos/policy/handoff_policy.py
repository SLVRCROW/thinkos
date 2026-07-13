"""HandoffPolicy — authorization policy for handoff operations.

Policy is configured at startup from trusted configuration.
Policy version is recorded in every security envelope and audit record.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from thinkos.schema.verified_context import VerifiedExecutionContext
from thinkos.schema.security_envelope import HandoffSecurityEnvelope


@dataclass(frozen=True)
class AuthorizationDecision:
    """Result of a policy authorization check."""

    allowed: bool
    external_reason: str       # Generic reason returned to caller
    internal_reason: str       # Protected internal detail (logged, not returned)
    policy_version: str
    evaluated_at: str


@dataclass(frozen=True)
class HandoffResource:
    """Immutable policy input derived from a verified envelope or validated create intent.

    Created by HandoffPolicy, never supplied by caller.
    """

    handoff_id: str | None
    namespace: str
    source_session_id: str | None
    target_session_id: str | None
    verified_source_principal: str | None


class HandoffPolicy:
    """Authorization policy for handoff operations.

    Evaluates context, operation, and resource.
    Does not mutate identity or storage.
    External denial reason remains generic.
    Internal details are never returned through the connector.
    """

    _GENERIC_DENIED = "Requested handoff operation is not available"

    def __init__(self, config: dict):
        taa = config.get("taa", {})
        self._namespace = taa.get("namespace", "")
        self._policy_version = taa.get("policy_version", "1")

    @property
    def policy_version(self) -> str:
        return self._policy_version

    @property
    def namespace(self) -> str:
        return self._namespace

    def _denied(self, internal: str) -> AuthorizationDecision:
        return AuthorizationDecision(
            allowed=False,
            external_reason=self._GENERIC_DENIED,
            internal_reason=internal,
            policy_version=self._policy_version,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _allowed(self) -> AuthorizationDecision:
        return AuthorizationDecision(
            allowed=True,
            external_reason="",
            internal_reason="",
            policy_version=self._policy_version,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )

    def authorize_create(
        self,
        ctx: VerifiedExecutionContext,
        target_session_id: str,
    ) -> AuthorizationDecision:
        """Authorize a handoff create operation."""
        if not ctx.is_verified:
            return self._denied("unverified context")
        if ctx.is_expired():
            return self._denied("expired context")
        if ctx.store_namespace != self._namespace:
            return self._denied("namespace mismatch")
        if not target_session_id:
            return self._denied("missing target session")
        if target_session_id == ctx.session_id:
            return self._denied("target session must differ from source session")
        return self._allowed()

    def authorize_read(
        self,
        ctx: VerifiedExecutionContext,
        envelope: HandoffSecurityEnvelope | None,
    ) -> AuthorizationDecision:
        """Authorize a handoff read operation."""
        if not ctx.is_verified:
            return self._denied("unverified context")
        if ctx.is_expired():
            return self._denied("expired context")
        if envelope is None:
            return self._denied("missing envelope")
        if ctx.store_namespace != envelope.store_namespace:
            return self._denied("namespace mismatch between context and envelope")
        if ctx.store_namespace != self._namespace:
            return self._denied("context namespace does not match policy namespace")
        if envelope.store_namespace != self._namespace:
            return self._denied("envelope namespace does not match policy namespace")
        if ctx.session_id != envelope.source_session_id and ctx.session_id != envelope.target_session_intent:
            return self._denied("session not authorized for this handoff")
        return self._allowed()

    def authorize_list(
        self,
        ctx: VerifiedExecutionContext,
        target_session_id: str,
    ) -> AuthorizationDecision:
        """Authorize a handoff list operation."""
        if not ctx.is_verified:
            return self._denied("unverified context")
        if ctx.is_expired():
            return self._denied("expired context")
        if ctx.store_namespace != self._namespace:
            return self._denied("namespace mismatch")
        if ctx.session_id != target_session_id:
            return self._denied("session mismatch")
        return self._allowed()

    def authorize_resolve(
        self,
        ctx: VerifiedExecutionContext,
        envelope: HandoffSecurityEnvelope | None,
    ) -> AuthorizationDecision:
        """Authorize a handoff resolve operation."""
        if not ctx.is_verified:
            return self._denied("unverified context")
        if ctx.is_expired():
            return self._denied("expired context")
        if envelope is None:
            return self._denied("missing envelope")
        if ctx.store_namespace != envelope.store_namespace:
            return self._denied("namespace mismatch between context and envelope")
        if ctx.store_namespace != self._namespace:
            return self._denied("context namespace does not match policy namespace")
        if envelope.store_namespace != self._namespace:
            return self._denied("envelope namespace does not match policy namespace")
        if ctx.session_id != envelope.target_session_intent:
            return self._denied("only target session may resolve")
        return self._allowed()

    def build_resource_from_envelope(
        self,
        envelope: HandoffSecurityEnvelope,
    ) -> HandoffResource:
        """Build a HandoffResource from a verified envelope."""
        return HandoffResource(
            handoff_id=envelope.handoff_id,
            namespace=envelope.store_namespace,
            source_session_id=envelope.source_session_id,
            target_session_id=envelope.target_session_intent,
            verified_source_principal=envelope.source_principal,
        )

    def build_resource_for_create(
        self,
        handoff_id: str,
        target_session_id: str,
        source_session_id: str,
        source_principal: str,
    ) -> HandoffResource:
        """Build a HandoffResource for a create intent."""
        return HandoffResource(
            handoff_id=handoff_id,
            namespace=self._namespace,
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            verified_source_principal=source_principal,
        )
