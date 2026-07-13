"""AdapterAuditRecord — immutable audit evidence for handoff operations.

Audit records are evidence only. They grant no authority.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class AdapterAuditRecord:
    """Immutable audit record for a handoff adapter operation.

    Persistence is best-effort. A successful handoff operation remains
    successful even if audit persistence fails.
    """

    audit_id: str
    operation: str            # "create_handoff" | "read_handoff" | "list_handoffs" | "resolve_handoff"
    handoff_id: str | None
    principal: str
    session_id: str
    store_namespace: str
    provider: str
    issuer: str
    policy_version: str
    result_status: str        # "ok" | "denied" | "error"
    result_reason: str
    timestamp: str

    @staticmethod
    def create(
        operation: str,
        handoff_id: str | None,
        principal: str,
        session_id: str,
        store_namespace: str,
        provider: str,
        issuer: str,
        policy_version: str,
        result_status: str,
        result_reason: str = "",
    ) -> "AdapterAuditRecord":
        return AdapterAuditRecord(
            audit_id=f"aud_{uuid.uuid4()}",
            operation=operation,
            handoff_id=handoff_id,
            principal=principal,
            session_id=session_id,
            store_namespace=store_namespace,
            provider=provider,
            issuer=issuer,
            policy_version=policy_version,
            result_status=result_status,
            result_reason=result_reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
