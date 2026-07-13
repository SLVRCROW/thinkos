"""ProcessBoundIdentityProvider — captures identity once at process startup.

One process = one principal + one session.
Multi-session-per-process is unsupported in v0.
"""

import os
import re
from datetime import datetime, timezone, timedelta

from thinkos.schema.verified_context import VerifiedExecutionContext

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_.\-:@]*$")
_MAX_PRINCIPAL_BYTES = 256
_MAX_SESSION_BYTES = 256
_MAX_NAMESPACE_BYTES = 128
_MAX_ISSUER_BYTES = 128
_MIN_TTL = 1
_MAX_TAA_TTL = 86400


def _validate_utf8_bytes(value: str, max_bytes: int, name: str) -> str | None:
    """Validate a string field. Returns an error message or None."""
    if not isinstance(value, str):
        return f"{name} must be a string"
    if not value:
        return f"{name} must not be empty"
    if value.strip() != value:
        return f"{name} must not have leading or trailing whitespace"
    encoded = value.encode("utf-8")
    if len(encoded) > max_bytes:
        return f"{name} must not exceed {max_bytes} UTF-8 bytes"
    for ch in value:
        if ord(ch) < 0x20:
            return f"{name} must not contain control characters"
    return None


def _validate_identifier(value: str, max_bytes: int, name: str) -> str | None:
    """Validate an identifier field (namespace, issuer)."""
    err = _validate_utf8_bytes(value, max_bytes, name)
    if err:
        return err
    if not _IDENTIFIER_RE.match(value):
        return f"{name} must match {_IDENTIFIER_RE.pattern}"
    return None


def _validate_ttl(value: str) -> tuple[int | None, str | None]:
    """Validate TTL seconds. Returns (int_value, error_message)."""
    try:
        ttl = int(value)
    except (ValueError, TypeError):
        return None, "TTL must be an integer"
    if ttl < _MIN_TTL:
        return None, f"TTL must be at least {_MIN_TTL}"
    if ttl > _MAX_TAA_TTL:
        return None, f"TTL must not exceed {_MAX_TAA_TTL}"
    return ttl, None


class ProcessBoundIdentityProvider:
    """v0 reference identity provider.

    Captures identity exactly once at process startup from environment
    variables set by the trusted launcher.

    One process = one principal + one session.
    Multi-session-per-process is unsupported in v0.

    Selection rules:
    - If any identity-bundle env var is present, the entire env bundle is required.
    - If none is present, the complete configured identity bundle is required.
    - Never combine individual identity fields from environment and config.
    """

    def __init__(self, config: dict | None = None):
        # Check which env vars are actually set (distinguishes "not set" from "set to empty")
        env_keys = ["THINKOS_PRINCIPAL", "THINKOS_SESSION_ID", "THINKOS_NAMESPACE",
                     "THINKOS_ISSUER", "THINKOS_TTL_SECONDS"]
        env_present = {k: k in os.environ for k in env_keys}
        env_principal = os.environ.get("THINKOS_PRINCIPAL", "")
        env_session = os.environ.get("THINKOS_SESSION_ID", "")
        env_namespace = os.environ.get("THINKOS_NAMESPACE", "")
        env_issuer = os.environ.get("THINKOS_ISSUER", "")
        env_ttl = os.environ.get("THINKOS_TTL_SECONDS", "")

        any_env = any(env_present.values())
        all_required_present = all([env_present["THINKOS_PRINCIPAL"],
                                    env_present["THINKOS_SESSION_ID"],
                                    env_present["THINKOS_NAMESPACE"],
                                    env_present["THINKOS_ISSUER"],
                                    env_present["THINKOS_TTL_SECONDS"]])
        all_required_nonempty = all([env_principal, env_session, env_namespace,
                                     env_issuer, env_ttl])

        if any_env and not all_required_present:
            missing = []
            if not env_principal:
                missing.append("THINKOS_PRINCIPAL")
            if not env_session:
                missing.append("THINKOS_SESSION_ID")
            if not env_namespace:
                missing.append("THINKOS_NAMESPACE")
            if not env_issuer:
                missing.append("THINKOS_ISSUER")
            if not env_ttl:
                missing.append("THINKOS_TTL_SECONDS")
            raise ValueError(
                f"Partial environment identity bundle: missing {', '.join(missing)}. "
                "All THINKOS_PRINCIPAL, THINKOS_SESSION_ID, THINKOS_NAMESPACE, "
                "THINKOS_ISSUER, and THINKOS_TTL_SECONDS are required."
            )

        if any_env:
            # Environment mode
            principal = env_principal
            session_id = env_session
            namespace = env_namespace
            issuer = env_issuer if env_issuer else "process-bound"
            ttl_str = env_ttl if env_ttl else "3600"
        elif config:
            # Config mode
            taa = config.get("taa", {})
            principal = taa.get("principal", "")
            session_id = taa.get("session_id", "")
            namespace = taa.get("namespace", "")
            issuer = taa.get("issuer", "process-bound")
            ttl_str = str(taa.get("ttl_seconds", 3600))
        else:
            raise ValueError("No identity configuration provided")

        # Validate
        errors = []

        err = _validate_utf8_bytes(principal, _MAX_PRINCIPAL_BYTES, "principal")
        if err:
            errors.append(err)

        err = _validate_utf8_bytes(session_id, _MAX_SESSION_BYTES, "session_id")
        if err:
            errors.append(err)

        err = _validate_identifier(namespace, _MAX_NAMESPACE_BYTES, "namespace")
        if err:
            errors.append(err)

        err = _validate_identifier(issuer, _MAX_ISSUER_BYTES, "issuer")
        if err:
            errors.append(err)

        ttl, err = _validate_ttl(ttl_str)
        if err:
            errors.append(err)

        if errors:
            raise ValueError("; ".join(errors))

        now = datetime.now(timezone.utc)
        self._ctx = VerifiedExecutionContext(
            principal=principal,
            session_id=session_id,
            store_namespace=namespace,
            provider="process-bound",
            issuer=issuer,
            issued_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=float(ttl))).isoformat(),
        )

    def get_context(self) -> VerifiedExecutionContext:
        """Returns the immutable startup context.

        Never re-reads os.environ. Never changes during process lifetime.
        """
        return self._ctx
