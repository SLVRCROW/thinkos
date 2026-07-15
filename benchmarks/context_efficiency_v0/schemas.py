"""Neutral event and receipt schemas for the benchmark harness."""
from __future__ import annotations
import dataclasses
import json
import hashlib
import uuid
import threading
from typing import Any

# Monotonic counter for guaranteed unique receipt IDs within this process
_receipt_counter = 0
_receipt_lock = threading.Lock()

def _next_seq() -> int:
    global _receipt_counter
    with _receipt_lock:
        _receipt_counter += 1
        return _receipt_counter


@dataclasses.dataclass(frozen=True)
class TrajectoryID:
    task: str
    condition: str
    architecture: str
    replicate: int

    def __str__(self) -> str:
        return f"{self.task}-{self.condition}-{self.architecture}-r{self.replicate}"

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> TrajectoryID:
        return cls(**d)


@dataclasses.dataclass(frozen=True)
class SessionID:
    trajectory: TrajectoryID
    worker: str

    def __str__(self) -> str:
        return f"{self.trajectory}-{self.worker}"

    def to_json(self) -> dict:
        return {"trajectory": self.trajectory.to_json(), "worker": self.worker}

    @classmethod
    def from_json(cls, d: dict) -> SessionID:
        return cls(trajectory=TrajectoryID.from_json(d["trajectory"]), worker=d["worker"])


@dataclasses.dataclass(frozen=True)
class EvidenceReference:
    receipt_id: str
    claim_type: str
    claim_value: str

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> EvidenceReference:
        return cls(**d)


def make_receipt_id(prefix: str, trajectory_id: str, worker: str, stage: int, seq: int = 0) -> str:
    """Generate a guaranteed-unique receipt ID using full UUID + monotonic counter."""
    unique = uuid.uuid4().hex
    counter = _next_seq()
    raw = f"{prefix}_{trajectory_id}_{worker}_s{stage}_{counter}_{unique}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


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
    def from_json(cls, d: dict) -> ToolCallReceipt:
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
    def from_json(cls, d: dict) -> CheckpointReceipt:
        return cls(**d)


@dataclasses.dataclass(frozen=True)
class SessionEvent:
    type: str
    session_id: str
    trajectory_id: str
    architecture: str
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
    def from_json(cls, d: dict) -> SessionEvent:
        tcs = tuple(ToolCallReceipt.from_json(tc) for tc in d.get("tool_calls", []))
        cp = CheckpointReceipt.from_json(d["checkpoint"]) if d.get("checkpoint") else None
        return cls(
            type=d["type"],
            session_id=d["session_id"],
            trajectory_id=d["trajectory_id"],
            architecture=d["architecture"],
            worker_label=d["worker_label"],
            stage=d["stage"],
            timestamp=d["timestamp"],
            tool_calls=tcs,
            checkpoint=cp,
            metadata=d.get("metadata", {}),
        )


def compute_sha256(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def serialize_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def serialize_jsonl(events: list[SessionEvent]) -> str:
    return "\n".join(json.dumps(e.to_json(), sort_keys=True, default=str) for e in events)
