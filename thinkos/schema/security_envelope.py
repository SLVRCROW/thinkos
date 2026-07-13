"""HandoffSecurityEnvelope — authoritative security binding for a handoff record.

Written atomically with the HandoffRecord in one SQLite transaction.
A HandoffRecord without a matching envelope is UNVERIFIED_LEGACY
and is denied for all connector read/list/resolve operations.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HandoffSecurityEnvelope:
    """Authoritative security binding for a handoff record.

    Authorization and trusted provenance use the envelope,
    never HandoffRecord identity strings.
    """

    envelope_id: str          # "env_" + uuid
    handoff_id: str            # Links to HandoffRecord (hof_*)
    source_principal: str
    source_session_id: str
    target_session_intent: str
    store_namespace: str
    provider: str              # "process-bound"
    issuer: str
    policy_version: str
    created_at: str            # ISO-8601
    schema_version: int = 1
