"""Experiment record schema — a single experiment attempt with metric and decision."""

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

SCHEMA_VERSION = 1
VALID_DECISIONS = {"keep", "discard", "unknown", "pending"}


@dataclass
class ExperimentRecord:
    experiment_id: str = ""
    schema_version: int = SCHEMA_VERSION
    session_id: str = ""
    timestamp: str = ""
    tool_name: str = ""
    params_summary: Optional[str] = None
    metric_name: str = ""
    metric_value: float = 0.0
    baseline_value: Optional[float] = None
    baseline_experiment_id: Optional[str] = None
    decision: str = "unknown"
    decision_reason: Optional[str] = None
    receipt_id: Optional[str] = None
    packet_ids: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def validate_experiment_id(eid: str) -> list[str]:
    errors = []
    if not eid.startswith("exp_"):
        errors.append("experiment_id must start with 'exp_'")
    else:
        uuid_part = eid[4:]
        try:
            uuid.UUID(uuid_part)
        except ValueError:
            errors.append(f"experiment_id suffix '{uuid_part}' is not a valid UUID")
    return errors


def validate(record: ExperimentRecord) -> list[str]:
    errors = []

    # schema_version
    if record.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    # experiment_id
    errors.extend(validate_experiment_id(record.experiment_id))

    # session_id
    if not record.session_id:
        errors.append("session_id is required")

    # timestamp
    if not record.timestamp:
        errors.append("timestamp is required")

    # tool_name
    if not record.tool_name:
        errors.append("tool_name is required")

    # params_summary — must be str or None
    if record.params_summary is not None and not isinstance(record.params_summary, str):
        errors.append("params_summary must be a string or None")

    # metric_name
    if not record.metric_name:
        errors.append("metric_name is required")

    # metric_value — must be int or float, reject bool
    if isinstance(record.metric_value, bool):
        errors.append("metric_value must be numeric (int or float), not bool")
    elif not isinstance(record.metric_value, (int, float)):
        errors.append("metric_value must be numeric (int or float)")

    # baseline_value — must be int, float, or None; reject bool
    if record.baseline_value is not None:
        if isinstance(record.baseline_value, bool):
            errors.append("baseline_value must be numeric or None, not bool")
        elif not isinstance(record.baseline_value, (int, float)):
            errors.append("baseline_value must be numeric or None")

    # decision
    if record.decision not in VALID_DECISIONS:
        errors.append(f"decision must be one of {sorted(VALID_DECISIONS)}, got '{record.decision}'")

    # receipt_id — must be str or None
    if record.receipt_id is not None and not isinstance(record.receipt_id, str):
        errors.append("receipt_id must be a string or None")

    # packet_ids — must be list of strings
    if not isinstance(record.packet_ids, list):
        errors.append("packet_ids must be a list")
    else:
        for pid in record.packet_ids:
            if not isinstance(pid, str):
                errors.append(f"packet_ids element '{pid}' must be a string")

    # tags — must be list of strings
    if not isinstance(record.tags, list):
        errors.append("tags must be a list")
    else:
        for tag in record.tags:
            if not isinstance(tag, str):
                errors.append(f"tags element '{tag}' must be a string")

    # metadata — must be dict
    if not isinstance(record.metadata, dict):
        errors.append("metadata must be a dict")

    return errors


def normalize(record: ExperimentRecord) -> ExperimentRecord:
    """Normalize numeric fields in-place. Returns the record for chaining."""
    if isinstance(record.metric_value, int) and not isinstance(record.metric_value, bool):
        record.metric_value = float(record.metric_value)
    if record.baseline_value is not None and isinstance(record.baseline_value, int) and not isinstance(record.baseline_value, bool):
        record.baseline_value = float(record.baseline_value)
    return record


def serialize(record: ExperimentRecord) -> str:
    d = asdict(record)
    return json.dumps(d, separators=(",", ":"))


def deserialize(data: str) -> ExperimentRecord:
    d = json.loads(data)
    return ExperimentRecord(**d)
