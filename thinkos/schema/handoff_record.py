"""Handoff record schema for bounded, evidence-only cross-session transfer."""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

SCHEMA_VERSION = 1
MAX_SESSION_ID_BYTES = 256
MAX_AGENT_BYTES = 256
MAX_PURPOSE_BYTES = 2048
MAX_PACKET_IDS = 25
MAX_RECEIPT_IDS = 50
MAX_OMISSIONS_BYTES = 2048
MAX_TAGS = 10
MAX_TAG_BYTES = 64


@dataclass
class HandoffRecord:
    handoff_id: str
    source_session_id: str
    target_session_id: str
    source_agent: str
    target_agent: str
    timestamp: str
    purpose_summary: str
    schema_version: int = SCHEMA_VERSION
    expires_at: Optional[str] = None
    packet_ids: list[str] = field(default_factory=list)
    receipt_ids: list[str] = field(default_factory=list)
    omitted_packet_count: int = 0
    omissions_summary: Optional[str] = None
    evidence_policy: str = "evidence_only"
    authority_transfer: str = "none"
    requires_fresh_approval: bool = True
    tags: list[str] = field(default_factory=list)


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def parse_timestamp(value: str) -> datetime | None:
    """Parse a timezone-aware ISO-8601 timestamp, accepting a trailing ``Z``."""
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


def validate_handoff_id(handoff_id: str) -> list[str]:
    if not isinstance(handoff_id, str) or not handoff_id.startswith("hof_"):
        return ["handoff_id must start with 'hof_'"]
    try:
        uuid.UUID(handoff_id[4:])
    except ValueError:
        return ["handoff_id suffix must be a valid UUID"]
    return []


def _validate_bounded_text(
    name: str, value: object, max_bytes: int, *, required: bool = True
) -> list[str]:
    if not isinstance(value, str):
        return [f"{name} must be a string"]
    if required and not value.strip():
        return [f"{name} is required"]
    if _utf8_len(value) > max_bytes:
        return [f"{name} must not exceed {max_bytes} UTF-8 bytes"]
    return []


def _validate_reference_ids(
    name: str, values: object, max_items: int, prefix: str
) -> list[str]:
    if not isinstance(values, list):
        return [f"{name} must be a list"]
    errors = []
    if len(values) > max_items:
        errors.append(f"{name} must not contain more than {max_items} entries")
    if all(isinstance(value, str) for value in values) and len(set(values)) != len(values):
        errors.append(f"{name} must not contain duplicate entries")
    for value in values:
        if not isinstance(value, str) or not value.startswith(prefix):
            errors.append(f"each {name} entry must be a string starting with '{prefix}'")
    return errors


def validate(record: HandoffRecord) -> list[str]:
    errors = []

    if record.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    errors.extend(validate_handoff_id(record.handoff_id))

    errors.extend(_validate_bounded_text(
        "source_session_id", record.source_session_id, MAX_SESSION_ID_BYTES
    ))
    errors.extend(_validate_bounded_text(
        "target_session_id", record.target_session_id, MAX_SESSION_ID_BYTES
    ))
    if (
        isinstance(record.source_session_id, str)
        and isinstance(record.target_session_id, str)
        and record.source_session_id
        and record.source_session_id == record.target_session_id
    ):
        errors.append("target_session_id must differ from source_session_id")

    errors.extend(_validate_bounded_text(
        "source_agent", record.source_agent, MAX_AGENT_BYTES
    ))
    errors.extend(_validate_bounded_text(
        "target_agent", record.target_agent, MAX_AGENT_BYTES
    ))

    if parse_timestamp(record.timestamp) is None:
        errors.append("timestamp must be a timezone-aware ISO-8601 string")
    if record.expires_at is not None and parse_timestamp(record.expires_at) is None:
        errors.append("expires_at must be a timezone-aware ISO-8601 string or None")

    errors.extend(_validate_bounded_text(
        "purpose_summary", record.purpose_summary, MAX_PURPOSE_BYTES
    ))
    errors.extend(_validate_reference_ids(
        "packet_ids", record.packet_ids, MAX_PACKET_IDS, "ctx_"
    ))
    errors.extend(_validate_reference_ids(
        "receipt_ids", record.receipt_ids, MAX_RECEIPT_IDS, "rct_"
    ))

    if (
        isinstance(record.omitted_packet_count, bool)
        or not isinstance(record.omitted_packet_count, int)
        or record.omitted_packet_count < 0
    ):
        errors.append("omitted_packet_count must be a non-negative integer")
    if record.omissions_summary is not None:
        errors.extend(_validate_bounded_text(
            "omissions_summary", record.omissions_summary,
            MAX_OMISSIONS_BYTES, required=False
        ))

    if record.evidence_policy != "evidence_only":
        errors.append("evidence_policy must be 'evidence_only'")
    if record.authority_transfer != "none":
        errors.append("authority_transfer must be 'none'")
    if record.requires_fresh_approval is not True:
        errors.append("requires_fresh_approval must be True")

    if not isinstance(record.tags, list):
        errors.append("tags must be a list")
    else:
        if len(record.tags) > MAX_TAGS:
            errors.append(f"tags must not contain more than {MAX_TAGS} entries")
        if all(isinstance(tag, str) for tag in record.tags) and len(set(record.tags)) != len(record.tags):
            errors.append("tags must not contain duplicate entries")
        for tag in record.tags:
            errors.extend(_validate_bounded_text(
                "tag", tag, MAX_TAG_BYTES
            ))

    return errors


def serialize(record: HandoffRecord) -> str:
    return json.dumps(asdict(record), separators=(",", ":"))


def deserialize(data: str) -> HandoffRecord:
    return HandoffRecord(**json.loads(data))
