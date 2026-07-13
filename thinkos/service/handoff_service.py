"""TrustedHandoffService — scoped facade for handoff operations.

This is the only interface exposed for handoff operations.
It is never exposed to tool adapters or third-party code.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from thinkos.schema.handoff_record import HandoffRecord, validate as validate_handoff
from thinkos.schema.security_envelope import HandoffSecurityEnvelope
from thinkos.schema.adapter_audit import AdapterAuditRecord
from thinkos.schema.verified_context import VerifiedExecutionContext
from thinkos.policy.handoff_policy import HandoffPolicy, AuthorizationDecision
from thinkos.store.sqlite_store import HandoffReferenceError, DuplicateError

# Fields that callers must not supply (privileged security fields)
PROHIBITED_CREATE_FIELDS = frozenset({
    "source_agent",
    "source_session_id",
    "principal",
    "issuer",
    "namespace",
    "store_namespace",
    "provider",
    "policy_version",
    "authorization",
    "capability",
    "verified_context",
    "security_envelope",
})

# Fields that callers must not supply in any handoff message
PROHIBITED_READ_FIELDS = frozenset({
    "principal",
    "issuer",
    "namespace",
    "store_namespace",
    "provider",
    "policy_version",
    "authorization",
    "capability",
    "verified_context",
    "security_envelope",
})

_GENERIC_UNAVAILABLE = {
    "status": "unavailable",
    "handoff_id": None,
    "audit_id": None,
    "audit_status": None,
}


def _check_prohibited_fields(body: dict, prohibited: frozenset) -> str | None:
    """Check for prohibited fields. Returns field name or None."""
    for field in prohibited:
        if field in body:
            return field
    return None


class TrustedHandoffService:
    """Scoped handoff facade. Only exposed interface for handoff operations.

    Never exposed to tool adapters. Never passed through tool context.
    """

    def __init__(
        self,
        store: Any,  # SQLiteStore
        ctx: VerifiedExecutionContext,
        policy: HandoffPolicy,
    ):
        self._store = store
        self._ctx = ctx
        self._policy = policy

    def _make_audit(
        self,
        operation: str,
        handoff_id: str | None,
        result_status: str,
        result_reason: str = "",
    ) -> AdapterAuditRecord:
        return AdapterAuditRecord.create(
            operation=operation,
            handoff_id=handoff_id,
            principal=self._ctx.principal,
            session_id=self._ctx.session_id,
            store_namespace=self._ctx.store_namespace,
            provider=self._ctx.provider,
            issuer=self._ctx.issuer,
            policy_version=self._policy.policy_version,
            result_status=result_status,
            result_reason=result_reason,
        )

    def _persist_audit(self, audit: AdapterAuditRecord) -> tuple[str | None, str | None]:
        """Persist an audit record. Best-effort.

        Returns (audit_id, audit_status).
        """
        try:
            self._store.write_adapter_audit(audit)
            return audit.audit_id, "persisted"
        except Exception:
            return None, "unavailable"

    def create_handoff(self, body: dict) -> dict:
        """Process a handoff_create message.

        Identity and authorization never come from request fields.
        """
        # Check for prohibited privileged fields
        prohibited = _check_prohibited_fields(body, PROHIBITED_CREATE_FIELDS)
        if prohibited:
            audit = self._make_audit("create_handoff", None, "denied", f"prohibited field: {prohibited}")
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        handoff_data = body.get("handoff", {})
        if not isinstance(handoff_data, dict):
            audit = self._make_audit("create_handoff", None, "denied", "missing handoff data")
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        # Extract target session from request (permitted destination intent)
        target_session_id = handoff_data.get("target_session_id", "")
        if not isinstance(target_session_id, str) or not target_session_id:
            audit = self._make_audit("create_handoff", None, "denied", "missing target_session_id")
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        # Authorize
        decision = self._policy.authorize_create(self._ctx, target_session_id)
        if not decision.allowed:
            audit = self._make_audit("create_handoff", None, "denied", decision.internal_reason)
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        # Build HandoffRecord with source identity from context, not request
        handoff_id = f"hof_{uuid.uuid4()}"
        now = datetime.now(timezone.utc).isoformat()

        record = HandoffRecord(
            handoff_id=handoff_id,
            source_session_id=self._ctx.session_id,
            target_session_id=target_session_id,
            source_agent=self._ctx.principal,
            target_agent=handoff_data.get("target_agent", ""),
            timestamp=now,
            purpose_summary=handoff_data.get("purpose_summary", ""),
            packet_ids=handoff_data.get("packet_ids", []),
            receipt_ids=handoff_data.get("receipt_ids", []),
            omitted_packet_count=handoff_data.get("omitted_packet_count", 0),
            omissions_summary=handoff_data.get("omissions_summary"),
            tags=handoff_data.get("tags", []),
            expires_at=handoff_data.get("expires_at"),
        )

        # Build security envelope
        envelope = HandoffSecurityEnvelope(
            envelope_id=f"env_{uuid.uuid4()}",
            handoff_id=handoff_id,
            source_principal=self._ctx.principal,
            source_session_id=self._ctx.session_id,
            target_session_intent=target_session_id,
            store_namespace=self._ctx.store_namespace,
            provider=self._ctx.provider,
            issuer=self._ctx.issuer,
            policy_version=self._policy.policy_version,
            created_at=now,
        )

        # Atomic write: record + envelope in one transaction
        try:
            self._store.write_handoff_with_envelope(record, envelope, self._ctx)
        except Exception as e:
            audit = self._make_audit("create_handoff", handoff_id, "error", str(e))
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        # Audit (best-effort, separate from atomic write)
        audit = self._make_audit("create_handoff", handoff_id, "ok")
        audit_id, audit_status = self._persist_audit(audit)

        return {
            "status": "ok",
            "handoff_id": handoff_id,
            "audit_id": audit_id,
            "audit_status": audit_status,
        }

    def read_handoff(self, body: dict) -> dict:
        """Process a handoff_read message."""
        prohibited = _check_prohibited_fields(body, PROHIBITED_READ_FIELDS)
        if prohibited:
            audit = self._make_audit("read_handoff", None, "denied", f"prohibited field: {prohibited}")
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        handoff_id = body.get("handoff_id", "")
        if not isinstance(handoff_id, str) or not handoff_id:
            return dict(_GENERIC_UNAVAILABLE)

        # Read envelope first (authoritative)
        try:
            envelope = self._store.read_envelope(handoff_id)
        except Exception:
            return dict(_GENERIC_UNAVAILABLE)
        decision = self._policy.authorize_read(self._ctx, envelope)
        if not decision.allowed:
            audit = self._make_audit("read_handoff", handoff_id, "denied", decision.internal_reason)
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        # Read record
        try:
            record = self._store.read_handoff(handoff_id, self._ctx)
        except Exception:
            return dict(_GENERIC_UNAVAILABLE)
        if record is None:
            return dict(_GENERIC_UNAVAILABLE)

        audit = self._make_audit("read_handoff", handoff_id, "ok")
        audit_id, audit_status = self._persist_audit(audit)

        # Return metadata (no source identity — caller already knows their own)
        return {
            "status": "ok",
            "handoff": {
                "handoff_id": record.handoff_id,
                "target_session_id": record.target_session_id,
                "target_agent": record.target_agent,
                "purpose_summary": record.purpose_summary,
                "packet_ids": record.packet_ids,
                "receipt_ids": record.receipt_ids,
                "omitted_packet_count": record.omitted_packet_count,
                "omissions_summary": record.omissions_summary,
                "tags": record.tags,
                "expires_at": record.expires_at,
                "created_at": record.timestamp,
            },
            "audit_id": audit_id,
            "audit_status": audit_status,
        }

    def list_handoffs(self, body: dict) -> dict:
        """Process a handoff_list message."""
        prohibited = _check_prohibited_fields(body, PROHIBITED_READ_FIELDS)
        if prohibited:
            audit = self._make_audit("list_handoffs", None, "denied", f"prohibited field: {prohibited}")
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        target_session_id = body.get("target_session_id", "")
        if not isinstance(target_session_id, str) or not target_session_id:
            return dict(_GENERIC_UNAVAILABLE)

        decision = self._policy.authorize_list(self._ctx, target_session_id)
        if not decision.allowed:
            audit = self._make_audit("list_handoffs", None, "denied", decision.internal_reason)
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        limit = body.get("limit", 100)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return dict(_GENERIC_UNAVAILABLE)
        if limit > 1000:
            limit = 1000

        records = []
        try:
            records = self._store.list_handoffs_for_target(target_session_id, limit=limit, ctx=self._ctx)
        except Exception:
            return dict(_GENERIC_UNAVAILABLE)

        audit = self._make_audit("list_handoffs", None, "ok")
        audit_id, audit_status = self._persist_audit(audit)

        return {
            "status": "ok",
            "handoffs": [
                {
                    "handoff_id": r.handoff_id,
                    "target_session_id": r.target_session_id,
                    "target_agent": r.target_agent,
                    "purpose_summary": r.purpose_summary,
                    "created_at": r.timestamp,
                }
                for r in records
            ],
            "audit_id": audit_id,
            "audit_status": audit_status,
        }

    def resolve_handoff(self, body: dict) -> dict:
        """Process a handoff_resolve message."""
        prohibited = _check_prohibited_fields(body, PROHIBITED_READ_FIELDS)
        if prohibited:
            audit = self._make_audit("resolve_handoff", None, "denied", f"prohibited field: {prohibited}")
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        handoff_id = body.get("handoff_id", "")
        if not isinstance(handoff_id, str) or not handoff_id:
            return dict(_GENERIC_UNAVAILABLE)

        try:
            envelope = self._store.read_envelope(handoff_id)
        except Exception:
            return dict(_GENERIC_UNAVAILABLE)
        decision = self._policy.authorize_resolve(self._ctx, envelope)
        if not decision.allowed:
            audit = self._make_audit("resolve_handoff", handoff_id, "denied", decision.internal_reason)
            self._persist_audit(audit)
            return dict(_GENERIC_UNAVAILABLE)

        try:
            resolved = self._store.resolve_handoff(handoff_id, self._ctx)
        except Exception:
            return dict(_GENERIC_UNAVAILABLE)

        audit = self._make_audit("resolve_handoff", handoff_id, "ok")
        audit_id, audit_status = self._persist_audit(audit)

        # Use envelope for trusted source principal
        resource = self._policy.build_resource_from_envelope(envelope)

        return {
            "status": "ok",
            "handoff_id": handoff_id,
            "source_principal": resource.verified_source_principal,
            "packets": [
                {
                    "id": p.packet_id,
                    "kind": p.kind,
                    "summary": (p.content.get("text") or "")[:500],
                }
                for p in resolved.get("packets", [])
            ],
            "receipts": [
                {
                    "id": r.receipt_id,
                    "status": r.result.status,
                    "tool": r.action.tool,
                }
                for r in resolved.get("receipts", [])
            ],
            "expired": resolved.get("expired", False),
            "audit_id": audit_id,
            "audit_status": audit_status,
        }
