"""VerifiedExecutionContext — immutable identity claims captured at startup.

Contains only identity claims established by the trusted launcher.
No authorization booleans, no capability flags, no policy logic.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


def _parse_timestamp(value: str) -> datetime | None:
    """Parse a timezone-aware ISO-8601 timestamp, accepting a trailing Z."""
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


@dataclass(frozen=True)
class VerifiedExecutionContext:
    """Immutable identity context captured once at process startup.

    Contains only identity claims. Policy decisions are separate.
    """

    principal: str
    session_id: str
    store_namespace: str
    provider: str          # "process-bound" | "none"
    issuer: str
    issued_at: str         # ISO-8601
    expires_at: str | None  # ISO-8601 or None (no expiry)

    @property
    def is_verified(self) -> bool:
        return self.provider != "none"

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        parsed = _parse_timestamp(self.expires_at)
        if parsed is None:
            return True  # malformed expiry → treat as expired
        return parsed <= datetime.now(timezone.utc)
