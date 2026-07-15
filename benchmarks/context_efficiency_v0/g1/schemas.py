"""G1 schemas: ProviderCallReceipt, G1TrajectoryScore, G1PilotEvidence.

Standard library only. No provider, accounting, scoring, or runtime imports.
"""

from __future__ import annotations
import dataclasses
import re
from typing import Any

from . import hashing
from . import serialization


# ── ProviderCallReceipt ────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class ProviderCallReceipt:
    """Immutable record of one provider invocation.

    Matches contract §9 exactly. Raw request/response content is stored
    separately under the retention policy (§13).
    """

    # Identity
    receipt_id: str
    pilot_id: str
    run_id: str
    provider_invocation_id: str
    attempt_index: int
    trajectory_id: str
    logical_session_id: str
    model_session_id: str
    worker_label: str
    stage: int

    # Provider identity
    requested_provider: str
    requested_model: str
    returned_model: str | None
    model_identity_valid: bool

    # Request tracking
    provider_request_id: str | None
    request_dispatched: bool
    temperature_milli: int
    max_tokens: int

    # Request hashes
    system_prompt_sha256: str
    prompt_sha256: str
    tool_definitions_sha256: None  # Always null — no provider-native tools

    # Token usage
    prompt_tokens_total: int | None
    cached_input_tokens: int | None
    uncached_input_tokens: int | None
    completion_tokens: int | None
    provider_usage_status: str

    # Response
    response_sha256: str | None
    provider_finish_reason: str | None
    response_present: bool

    # Error handling
    sanitized_error_sha256: str | None
    normalized_execution_status: str

    # Timing
    start_timestamp: str
    end_timestamp: str
    duration_ms: int

    # Cost
    calculated_micro_usd_cost: int | None
    provider_reported_cost_micro_usd: int | None
    pricing_source: str | None
    call_accounting_valid: bool

    # Raw content hashes
    raw_prompt_sha256: str
    raw_response_sha256: str | None

    # Lineage
    shared_source_id: str | None
    parent_receipt_ids: tuple[str, ...] = ()
    tool_call_receipt_ids: tuple[str, ...] = ()

    # Integrity
    contamination_flags: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return _to_dict(self)

    @classmethod
    def from_json(cls, d: dict) -> ProviderCallReceipt:
        errors = validate_provider_call_receipt(d)
        if errors:
            raise ValueError(f"ProviderCallReceipt validation failed: {'; '.join(errors)}")
        return _from_dict(cls, d)


# ── G1TrajectoryScore ─────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class G1TrajectoryScore:
    """Structural diagnostic scores for one trajectory.

    Generic container for diagnostic sub-scores. Metric names and score
    ranges are not frozen here — they are defined by G1-C. Each metric
    is a key-value pair with a string name and optional float value.
    """

    trajectory_id: str
    architecture: str
    task: str
    condition: str

    # Generic diagnostic metrics (name → value or None)
    # Metric names and semantics are defined by G1-C, not G1-A.
    diagnostic_metrics: tuple[dict[str, Any], ...] = ()

    # Metadata
    worker_count: int = 0
    stages_attempted: int = 0
    stages_completed: int = 0
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return _to_dict(self)

    @classmethod
    def from_json(cls, d: dict) -> G1TrajectoryScore:
        errors = validate_trajectory_score(d)
        if errors:
            raise ValueError(f"G1TrajectoryScore validation failed: {'; '.join(errors)}")
        return _from_dict(cls, d)


# ── G1PilotEvidence ───────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class G1PilotEvidence:
    """Top-level manifest for one G1 pilot run.

    Covers identifiers, contract/base hashes, configuration hashes, and
    ordered receipt references. No accounting, scoring, or runtime state.
    """

    pilot_id: str
    run_id: str
    contract_sha256: str
    g0_base_commit: str
    g0_manifest_sha256: str
    pilot_config_sha256: str
    trajectory_ids: tuple[str, ...]
    provider_receipt_ids: tuple[str, ...]
    shared_source_receipt_ids: tuple[str, ...]
    trajectory_receipt_ids: tuple[str, ...]
    checksum: str

    def to_json(self) -> dict:
        return _to_dict(self)

    @classmethod
    def from_json(cls, d: dict) -> G1PilotEvidence:
        errors = validate_pilot_evidence(d)
        if errors:
            raise ValueError(f"G1PilotEvidence validation failed: {'; '.join(errors)}")
        return _from_dict(cls, d)


# ── Internal helpers ──────────────────────────────────────────────────


VALID_EXECUTION_STATUSES = frozenset({
    "completed", "timeout", "provider_error", "policy_denied", "no_response",
})

VALID_USAGE_STATUSES = frozenset({
    "reported", "partial", "missing", "error",
})

VALID_FINISH_REASONS = frozenset({
    "stop", "length", "tool_calls", "error",
})

VALID_WORKER_LABELS = frozenset({"A", "B", "C", "D"})

VALID_ARCHITECTURES = frozenset({"stateless", "summary", "verified_state"})

VALID_TASKS = frozenset({"A", "B", "C"})

VALID_CONDITIONS = frozenset({"clean", "drift"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def validate_provider_call_receipt(d: dict) -> list[str]:
    """Validate a ProviderCallReceipt dict. Returns list of error strings."""
    errors = []

    # Required string fields
    for field in ("receipt_id", "pilot_id", "run_id", "provider_invocation_id",
                  "trajectory_id", "logical_session_id", "model_session_id",
                  "worker_label", "requested_provider", "requested_model",
                  "provider_usage_status", "normalized_execution_status",
                  "start_timestamp", "end_timestamp", "raw_prompt_sha256"):
        val = d.get(field)
        if not isinstance(val, str) or not val:
            errors.append(f"{field}: required non-empty string")

    # Required int fields
    for field in ("attempt_index", "temperature_milli", "max_tokens", "duration_ms", "stage"):
        val = d.get(field)
        if not isinstance(val, int) or isinstance(val, bool):
            errors.append(f"{field}: required int")

    # Required bool fields
    for field in ("request_dispatched", "response_present", "model_identity_valid",
                  "call_accounting_valid"):
        val = d.get(field)
        if not isinstance(val, bool):
            errors.append(f"{field}: required bool")

    # Nullable string fields
    for field in ("returned_model", "provider_request_id", "response_sha256",
                  "provider_finish_reason", "sanitized_error_sha256",
                  "pricing_source", "raw_response_sha256", "shared_source_id"):
        val = d.get(field)
        if val is not None and not isinstance(val, str):
            errors.append(f"{field}: must be string or null")

    # Nullable int fields
    for field in ("prompt_tokens_total", "cached_input_tokens", "uncached_input_tokens",
                  "completion_tokens", "calculated_micro_usd_cost",
                  "provider_reported_cost_micro_usd"):
        val = d.get(field)
        if val is not None and (not isinstance(val, int) or isinstance(val, bool)):
            errors.append(f"{field}: must be int or null")

    # tool_definitions_sha256 must be None
    if d.get("tool_definitions_sha256") is not None:
        errors.append("tool_definitions_sha256: must be null")

    # Enum validation
    if d.get("provider_usage_status") not in VALID_USAGE_STATUSES:
        errors.append(f"provider_usage_status: must be one of {sorted(VALID_USAGE_STATUSES)}")

    if d.get("normalized_execution_status") not in VALID_EXECUTION_STATUSES:
        errors.append(f"normalized_execution_status: must be one of {sorted(VALID_EXECUTION_STATUSES)}")

    if d.get("worker_label") not in VALID_WORKER_LABELS:
        errors.append(f"worker_label: must be one of {sorted(VALID_WORKER_LABELS)}")

    finish = d.get("provider_finish_reason")
    if finish is not None and finish not in VALID_FINISH_REASONS:
        errors.append(f"provider_finish_reason: must be one of {sorted(VALID_FINISH_REASONS)} or null")

    # SHA-256 hash format for hash fields
    for field in ("receipt_id", "system_prompt_sha256", "prompt_sha256",
                  "raw_prompt_sha256"):
        val = d.get(field)
        if isinstance(val, str) and val and not _SHA256_RE.match(val):
            errors.append(f"{field}: must be 64-char lowercase hex")

    for field in ("response_sha256", "sanitized_error_sha256", "raw_response_sha256"):
        val = d.get(field)
        if val is not None and not _SHA256_RE.match(val):
            errors.append(f"{field}: must be 64-char lowercase hex or null")

    # RFC 3339 timestamps
    for field in ("start_timestamp", "end_timestamp"):
        val = d.get(field)
        if isinstance(val, str) and val and not _RFC3339_RE.match(val):
            errors.append(f"{field}: must be RFC 3339 UTC timestamp")

    # Numeric range checks
    stage = d.get("stage")
    if isinstance(stage, int) and not isinstance(stage, bool):
        if stage < 1 or stage > 4:
            errors.append("stage: must be 1-4")

    attempt = d.get("attempt_index")
    if isinstance(attempt, int) and not isinstance(attempt, bool):
        if attempt < 0:
            errors.append("attempt_index: must be non-negative")

    temp = d.get("temperature_milli")
    if isinstance(temp, int) and not isinstance(temp, bool):
        if temp < 0 or temp > 100_000:
            errors.append("temperature_milli: must be 0-100000")

    dur = d.get("duration_ms")
    if isinstance(dur, int) and not isinstance(dur, bool):
        if dur <= 0:
            errors.append("duration_ms: must be positive")

    mt = d.get("max_tokens")
    if isinstance(mt, int) and not isinstance(mt, bool):
        if mt <= 0:
            errors.append("max_tokens: must be positive")

    # Non-negative token and cost fields
    for field in ("prompt_tokens_total", "cached_input_tokens", "uncached_input_tokens",
                  "completion_tokens"):
        val = d.get(field)
        if isinstance(val, int) and not isinstance(val, bool):
            if val < 0:
                errors.append(f"{field}: must be non-negative")

    for field in ("calculated_micro_usd_cost", "provider_reported_cost_micro_usd"):
        val = d.get(field)
        if isinstance(val, int) and not isinstance(val, bool):
            if val < 0:
                errors.append(f"{field}: must be non-negative")

    # Tuple fields
    for field in ("parent_receipt_ids", "tool_call_receipt_ids",
                  "contamination_flags", "warnings"):
        val = d.get(field)
        if not isinstance(val, (list, tuple)):
            errors.append(f"{field}: must be a list/tuple")
        elif any(not isinstance(v, str) for v in val):
            errors.append(f"{field}: all elements must be strings")

    # receipt_id must match its canonical self-excluding hash
    rid = d.get("receipt_id")
    if isinstance(rid, str) and rid:
        computed = hashing.compute_receipt_hash(d)
        if rid != computed:
            errors.append(
                f"receipt_id does not match canonical hash: "
                f"got {rid[:16]}..., expected {computed[:16]}..."
            )

    # Cross-field invariants
    # call_accounting_valid=True requires all token and cost fields non-null
    if d.get("call_accounting_valid") is True:
        for field in ("prompt_tokens_total", "cached_input_tokens",
                      "uncached_input_tokens", "completion_tokens",
                      "calculated_micro_usd_cost"):
            if d.get(field) is None:
                errors.append(
                    f"call_accounting_valid=True requires {field} to be non-null"
                )

    # model_identity_valid=True requires returned_model non-null
    if d.get("model_identity_valid") is True and d.get("returned_model") is None:
        errors.append("model_identity_valid=True requires returned_model to be non-null")

    return errors


def validate_trajectory_score(d: dict) -> list[str]:
    """Validate a G1TrajectoryScore dict."""
    errors = []

    for field in ("trajectory_id", "architecture", "task", "condition"):
        val = d.get(field)
        if not isinstance(val, str) or not val:
            errors.append(f"{field}: required non-empty string")

    if d.get("architecture") not in VALID_ARCHITECTURES:
        errors.append(f"architecture: must be one of {sorted(VALID_ARCHITECTURES)}")

    if d.get("task") not in VALID_TASKS:
        errors.append(f"task: must be one of {sorted(VALID_TASKS)}")

    if d.get("condition") not in VALID_CONDITIONS:
        errors.append(f"condition: must be one of {sorted(VALID_CONDITIONS)}")

    # diagnostic_metrics: generic container, validated as list of dicts
    metrics = d.get("diagnostic_metrics")
    if metrics is not None:
        if not isinstance(metrics, (list, tuple)):
            errors.append("diagnostic_metrics: must be a list/tuple")
        else:
            for i, m in enumerate(metrics):
                if not isinstance(m, dict):
                    errors.append(f"diagnostic_metrics[{i}]: must be a dict")

    for field in ("worker_count", "stages_attempted", "stages_completed"):
        val = d.get(field)
        if not isinstance(val, int) or isinstance(val, bool):
            errors.append(f"{field}: required int")
        elif val < 0:
            errors.append(f"{field}: must be non-negative")

    return errors


def validate_pilot_evidence(d: dict) -> list[str]:
    """Validate a G1PilotEvidence dict."""
    errors = []

    for field in ("pilot_id", "run_id", "contract_sha256", "g0_base_commit",
                  "g0_manifest_sha256", "pilot_config_sha256"):
        val = d.get(field)
        if not isinstance(val, str) or not val:
            errors.append(f"{field}: required non-empty string")

    for field in ("trajectory_ids", "provider_receipt_ids",
                  "shared_source_receipt_ids", "trajectory_receipt_ids"):
        val = d.get(field)
        if not isinstance(val, (list, tuple)):
            errors.append(f"{field}: must be a list/tuple")
        elif any(not isinstance(v, str) for v in val):
            errors.append(f"{field}: all elements must be strings")

    # SHA-256 format check
    for field in ("contract_sha256", "g0_manifest_sha256", "pilot_config_sha256"):
        val = d.get(field)
        if isinstance(val, str) and val and not _SHA256_RE.match(val):
            errors.append(f"{field}: must be 64-char lowercase hex")

    # g0_base_commit is a 40-char git commit SHA
    val = d.get("g0_base_commit")
    if isinstance(val, str) and val and not _COMMIT_SHA_RE.match(val):
        errors.append("g0_base_commit: must be 40-char lowercase hex")

    # Validate checksum — mandatory, non-empty, must match canonical hash
    ck = d.get("checksum")
    if not ck:
        errors.append("checksum: required non-empty string")
    elif not isinstance(ck, str):
        errors.append("checksum: must be a string")
    elif not _SHA256_RE.match(ck):
        errors.append("checksum: must be 64-char lowercase hex")
    else:
        computed = hashing.compute_manifest_hash(d)
        if ck != computed:
            errors.append(
                f"checksum does not match canonical hash: "
                f"got {ck[:16]}..., expected {computed[:16]}..."
            )

    return errors


def _to_dict(obj: Any) -> dict:
    """Convert a dataclass instance to a plain dict for JSON serialization."""
    d = {}
    for field in dataclasses.fields(obj):
        val = getattr(obj, field.name)
        if isinstance(val, tuple):
            d[field.name] = list(val)
        else:
            d[field.name] = val
    return d


def _from_dict(cls: type, d: dict) -> Any:
    """Reconstruct a dataclass from a plain dict."""
    field_names = {f.name for f in dataclasses.fields(cls)}
    extra = set(d.keys()) - field_names
    if extra:
        raise ValueError(f"Unexpected fields: {sorted(extra)}")
    missing = field_names - set(d.keys())
    if missing:
        raise ValueError(f"Missing fields: {sorted(missing)}")
    kwargs = {}
    for f in dataclasses.fields(cls):
        val = d[f.name]
        # Convert lists back to tuples for tuple-typed fields
        if isinstance(val, list) and "tuple" in str(f.type):
            kwargs[f.name] = tuple(val)
        else:
            kwargs[f.name] = val
    return cls(**kwargs)
