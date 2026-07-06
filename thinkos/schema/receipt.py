"""Receipt schema — immutable records of actions in a ThinkOS session."""

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

SCHEMA_VERSION = 1
VALID_ACTION_TYPES = {"tool_call", "packet_write", "gate_evaluation", "agent_message"}
VALID_RESULT_STATUSES = {"ok", "error", "denied"}
VALID_GATE_DECISIONS = {"allow", "deny"}


@dataclass
class Action:
    type: str = "tool_call"
    tool: Optional[str] = None
    params: Optional[dict] = None
    agent: str = ""


@dataclass
class Result:
    status: str = "ok"
    summary: str = ""
    packet_ids: list = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class GateInfo:
    gate_name: Optional[str] = None
    decision: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class Receipt:
    receipt_id: str = ""
    schema_version: int = SCHEMA_VERSION
    session_id: str = ""
    sequence: int = 0
    timestamp: str = ""
    action: Action = field(default_factory=Action)
    result: Result = field(default_factory=Result)
    gate: Optional[GateInfo] = None
    supersedes: Optional[str] = None


def validate_receipt_id(rid: str) -> list[str]:
    errors = []
    if not rid.startswith("rct_"):
        errors.append("receipt_id must start with 'rct_'")
    else:
        uuid_part = rid[4:]
        try:
            uuid.UUID(uuid_part)
        except ValueError:
            errors.append(f"receipt_id suffix '{uuid_part}' is not a valid UUID")
    return errors


def validate(receipt: Receipt) -> list[str]:
    errors = []

    if receipt.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    errors.extend(validate_receipt_id(receipt.receipt_id))

    if not receipt.session_id:
        errors.append("session_id is required")

    if receipt.sequence < 1:
        errors.append("sequence must be >= 1")

    if not receipt.timestamp:
        errors.append("timestamp is required")

    if receipt.action.type not in VALID_ACTION_TYPES:
        errors.append(f"action.type must be one of {sorted(VALID_ACTION_TYPES)}")

    if receipt.result.status not in VALID_RESULT_STATUSES:
        errors.append(f"result.status must be one of {sorted(VALID_RESULT_STATUSES)}")

    if receipt.gate is not None and receipt.gate.decision is not None:
        if receipt.gate.decision not in VALID_GATE_DECISIONS:
            errors.append(f"gate.decision must be one of {sorted(VALID_GATE_DECISIONS)}")

    return errors


def serialize(receipt: Receipt) -> str:
    d = asdict(receipt)
    return json.dumps(d, separators=(",", ":"))


def deserialize(data: str) -> Receipt:
    d = json.loads(data)
    if "action" in d and isinstance(d["action"], dict):
        d["action"] = Action(**d["action"])
    if "result" in d and isinstance(d["result"], dict):
        d["result"] = Result(**d["result"])
    if "gate" in d and isinstance(d["gate"], dict):
        d["gate"] = GateInfo(**d["gate"])
    return Receipt(**d)
