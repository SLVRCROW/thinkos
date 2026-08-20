"""VS-1 neutral schemas: arms, conditions, events, receipts.

Isolated from G0/G1 frozen files (which remain byte-identical at their frozen
manifest hashes). VS-1 defines its own condition vocabulary (six conditions,
not G0's two) and its own six-arm vocabulary (not G0's three).

No model, API, or network calls. Pure data structures + hashing.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import threading
import uuid
from typing import Any

# ── Six arms (frozen; VS-1 protocol §3) ──────────────────────────────────────
ARMS: tuple[str, ...] = (
    "stateless",            # A
    "transcript",           # B
    "summary",              # C
    "retrieval",            # D
    "verified_state",       # E
    "verified_state_procedure",  # F
)
ARM_LABELS: dict[str, str] = {
    "stateless": "A",
    "transcript": "B",
    "summary": "C",
    "retrieval": "D",
    "verified_state": "E",
    "verified_state_procedure": "F",
}

# ── Conditions (frozen; VS-1 protocol §6) ────────────────────────────────────
CONDITIONS: tuple[str, ...] = (
    "clean",
    "interruption",
    "reversal",
    "contradiction",
    "poison",
    "motif",
)

# ── Stable vocabulary for downstream checks ──────────────────────────────────
ARM_RE = re.compile(r"^[a-z_]+$")
CONDITION_RE = re.compile(r"^[a-z_]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_monotonic_counter = 0
_lock = threading.Lock()


def _next_seq() -> int:
    global _monotonic_counter
    with _lock:
        _monotonic_counter += 1
        return _monotonic_counter


def compute_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def make_receipt_id(prefix: str, trajectory_id: str, worker: str, stage: int, seq: int = 0) -> str:
    """Guaranteed-unique receipt ID (UUID + monotonic counter, content-hash prefix)."""
    unique = uuid.uuid4().hex
    counter = _next_seq()
    raw = f"{prefix}_{trajectory_id}_{worker}_s{stage}_{counter}_{unique}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def canonical_json(data: Any) -> str:
    """Canonical serialization: UTF-8, sorted keys, fixed separators, no NaN/Infinity."""
    def _clean(v: Any) -> Any:
        if isinstance(v, float):
            if v != v or v in (float("inf"), float("-inf")):
                raise ValueError("NaN/Infinity not allowed in canonical JSON")
            return v
        if isinstance(v, dict):
            return {str(k): _clean(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
        if isinstance(v, (list, tuple)):
            return [_clean(x) for x in v]
        return v

    cleaned = _clean(data)
    return json_dumps(cleaned)


def json_dumps(data: Any) -> str:
    import json
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@dataclasses.dataclass(frozen=True)
class EvidenceReference:
    receipt_id: str
    claim_type: str
    claim_value: str

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "EvidenceReference":
        return cls(**d)


@dataclasses.dataclass(frozen=True)
class ToolCallReceipt:
    receipt_id: str
    tool: str
    params: dict[str, Any]
    status: str
    output: str
    evidence_refs: tuple[EvidenceReference, ...] = ()
    timestamp: float = 0.0

    def to_json(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "tool": self.tool,
            "params": self.params,
            "status": self.status,
            "output": self.output,
            "evidence_refs": [r.to_json() for r in self.evidence_refs],
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_json(cls, d: dict) -> "ToolCallReceipt":
        refs = tuple(EvidenceReference.from_json(r) for r in d.get("evidence_refs", []))
        return cls(
            receipt_id=d["receipt_id"],
            tool=d["tool"],
            params=d["params"],
            status=d["status"],
            output=d["output"],
            evidence_refs=refs,
            timestamp=d.get("timestamp", 0.0),
        )


@dataclasses.dataclass(frozen=True)
class CheckpointReceipt:
    receipt_id: str
    stage_number: int
    worker_label: str
    artifact_path: str
    artifact_sha256: str
    test_results: dict[str, bool]
    timestamp: float = 0.0
    session_token_count: int = 0

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "CheckpointReceipt":
        return cls(**d)


@dataclasses.dataclass(frozen=True)
class SessionEvent:
    type: str
    session_id: str
    trajectory_id: str
    arm: str
    condition: str
    worker_label: str
    stage: int
    timestamp: float
    tool_calls: tuple[ToolCallReceipt, ...] = ()
    checkpoint: CheckpointReceipt | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_json(self) -> dict:
        d = dataclasses.asdict(self)
        d["tool_calls"] = [tc.to_json() for tc in self.tool_calls]
        d["checkpoint"] = self.checkpoint.to_json() if self.checkpoint else None
        return d

    @classmethod
    def from_json(cls, d: dict) -> "SessionEvent":
        tcs = tuple(ToolCallReceipt.from_json(tc) for tc in d.get("tool_calls", []))
        cp = CheckpointReceipt.from_json(d["checkpoint"]) if d.get("checkpoint") else None
        return cls(
            type=d["type"],
            session_id=d["session_id"],
            trajectory_id=d["trajectory_id"],
            arm=d["arm"],
            condition=d["condition"],
            worker_label=d["worker_label"],
            stage=d["stage"],
            timestamp=d["timestamp"],
            tool_calls=tcs,
            checkpoint=cp,
            metadata=d.get("metadata", {}),
        )


# ── VS-1 arm boundary contract (protocol §3) ─────────────────────────────────
@dataclasses.dataclass(frozen=True)
class ArmBoundary:
    arm: str
    what_enters: str
    representation: str
    token_cost_model: str
    provenance_survives: bool
    successor_inspectable: tuple[str, ...]
    cannot_cross_arms: tuple[str, ...]

    def to_json(self) -> dict:
        return {
            "arm": self.arm,
            "what_enters": self.what_enters,
            "representation": self.representation,
            "token_cost_model": self.token_cost_model,
            "provenance_survives": self.provenance_survives,
            "successor_inspectable": list(self.successor_inspectable),
            "cannot_cross_arms": list(self.cannot_cross_arms),
        }
