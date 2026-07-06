"""Context packet schema — the fundamental unit of project memory."""

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

VALID_KINDS = {"observation", "tool_result", "user_message", "agent_message", "summary", "decision"}
SCHEMA_VERSION = 1
MAX_DAG_DEPTH = 5


@dataclass
class ContextPacket:
    packet_id: str
    schema_version: int = SCHEMA_VERSION
    session_id: str = ""
    parent_id: Optional[str] = None
    timestamp: str = ""
    kind: str = "observation"
    source: str = ""
    content: dict = field(default_factory=lambda: {"text": "", "structured": None})
    tags: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def validate_packet_id(pid: str) -> list[str]:
    errors = []
    if not pid.startswith("ctx_"):
        errors.append("packet_id must start with 'ctx_'")
    else:
        uuid_part = pid[4:]
        try:
            uuid.UUID(uuid_part)
        except ValueError:
            errors.append(f"packet_id suffix '{uuid_part}' is not a valid UUID")
    return errors


def validate(packet: ContextPacket) -> list[str]:
    errors = []

    # schema_version
    if packet.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    # packet_id
    errors.extend(validate_packet_id(packet.packet_id))

    # timestamp
    if not packet.timestamp:
        errors.append("timestamp is required")

    # kind
    if packet.kind not in VALID_KINDS:
        errors.append(f"kind must be one of {sorted(VALID_KINDS)}, got '{packet.kind}'")

    # content.text
    if not isinstance(packet.content, dict) or not packet.content.get("text"):
        errors.append("content.text must be a non-empty string")

    # parent_id format if present
    if packet.parent_id is not None:
        if not packet.parent_id.startswith("ctx_"):
            errors.append("parent_id must start with 'ctx_'")

    return errors


def check_cycle(packet: ContextPacket, existing_ids: set) -> bool:
    """Return True if writing this packet would create a cycle."""
    if packet.parent_id is None:
        return False
    current = packet.parent_id
    depth = 0
    while current is not None and depth <= MAX_DAG_DEPTH:
        if current == packet.packet_id:
            return True
        # We can't traverse the full DAG without the store, so we check
        # if the parent_id equals the new packet's own ID (direct cycle)
        depth += 1
    return False


def check_dag_depth(packet: ContextPacket, get_parent_depth) -> list[str]:
    """Check that the parent chain does not exceed MAX_DAG_DEPTH."""
    if packet.parent_id is None:
        return []
    parent_depth = get_parent_depth(packet.parent_id) if callable(get_parent_depth) else 0
    if parent_depth >= MAX_DAG_DEPTH:
        return [f"DAG depth exceeds maximum of {MAX_DAG_DEPTH}"]
    return []


def serialize(packet: ContextPacket) -> str:
    d = asdict(packet)
    return json.dumps(d, separators=(",", ":"))


def deserialize(data: str) -> ContextPacket:
    d = json.loads(data)
    return ContextPacket(**d)
