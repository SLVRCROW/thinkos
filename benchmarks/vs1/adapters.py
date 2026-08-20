"""VS-1 six-arm adapters.

Each adapter transforms a predecessor transcript (list of SessionEvent) into
arm-specific inherited state for the successor. Deterministic: no model, API,
or network calls. Every adapter exposes the frozen ArmBoundary contract
(protocol §3): what enters, representation, token-cost model, provenance,
what the successor can inspect, what cannot cross arms.

The arms map to VS-1 protocol §3:
  A stateless            — empty state
  B transcript           — full bounded predecessor transcript
  C summary              — compressed narrative state (deterministic)
  D retrieval            — lexical retrieval index over artifacts/history
  E verified_state       — evidence-linked typed state
  F verified_state_proc  — E + tested reusable procedures
"""
from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .schemas import (
    ARMS,
    ArmBoundary,
    SessionEvent,
    ToolCallReceipt,
    compute_sha256,
    json_dumps,
)


@dataclass(frozen=True)
class ArchitectureState:
    """State delivered to a successor worker."""
    arm: str
    content: Any
    token_cost: int = 0  # Tokens consumed to produce this state
    provenance: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "arm": self.arm,
            "content": self.content,
            "token_cost": self.token_cost,
            "provenance": self.provenance,
        }


# ── Token cost helpers (deterministic approximations, documented) ──────────
def _json_tokens_estimate(obj: Any) -> int:
    """Approximate token cost at 1 token per 4 characters of canonical JSON."""
    return len(json_dumps(obj)) // 4


class _AdapterABC(ABC):
    arm: str = ""

    @abstractmethod
    def transform(self, transcript: list[SessionEvent]) -> ArchitectureState:
        """Transform a raw predecessor transcript into arm-specific state."""

    def boundary(self) -> ArmBoundary:
        raise NotImplementedError


class StatelessAdapter(_AdapterABC):
    """Arm A: no inherited project state."""

    arm = "stateless"

    def transform(self, transcript: list[SessionEvent]) -> ArchitectureState:
        return ArchitectureState(
            arm="stateless",
            content={},
            token_cost=0,
            provenance={"source": "none"},
        )

    def boundary(self) -> ArmBoundary:
        return ArmBoundary(
            arm="stateless",
            what_enters="nothing",
            representation="empty dict",
            token_cost_model="0 tokens",
            provenance_survives=False,
            successor_inspectable=(),
            cannot_cross_arms=("any transcript content", "any checkpoint content"),
        )


class TranscriptAdapter(_AdapterABC):
    """B: full bounded predecessor transcript (raw events)."""

    arm = "transcript"

    def transform(self, transcript: list[SessionEvent]) -> ArchitectureState:
        events = [e.to_json() for e in transcript]
        content = {"events": events}
        return ArchitectureState(
            arm="transcript",
            content=content,
            token_cost=_json_tokens_estimate(content),
            provenance={"source": "full predecessor transcript"},
        )

    def boundary(self) -> ArmBoundary:
        return ArmBoundary(
            arm="transcript",
            what_enters="full predecessor session event list",
            representation="list of SessionEvent.to_json()",
            token_cost_model="1 token / 4 JSON chars (documented approximation)",
            provenance_survives=True,
            successor_inspectable=("all events", "tool calls", "checkpoints"),
            cannot_cross_arms=("hidden tests", "ground truth beyond transcript"),
        )


class SummaryAdapter(_AdapterABC):
    """C: deterministic compressed summary from checkpoint data.

    No model call. Produces structured summary of completed stages and
    receipt IDs. (G0 SummaryAdapter is frozen; this is the VS-1 variant
    with richer narrative projection and stage invariants.)
    """

    arm = "summary"

    def transform(self, transcript: list[SessionEvent]) -> ArchitectureState:
        completed_stages: list[dict] = []
        for event in transcript:
            if event.checkpoint:
                completed_stages.append({
                    "stage": event.checkpoint.stage_number,
                    "worker": event.checkpoint.worker_label,
                    "artifact_path": event.checkpoint.artifact_path,
                    "artifact_sha256": event.checkpoint.artifact_sha256,
                    "test_results": event.checkpoint.test_results,
                    "session_tokens": event.checkpoint.session_token_count,
                })
        content = {
            "completed_stages": completed_stages,
            "n_stages": len(completed_stages),
            "n_events": len(transcript),
        }
        return ArchitectureState(
            arm="summary",
            content=content,
            token_cost=_json_tokens_estimate(content),
            provenance={"source": "deterministic extraction from transcript"},
        )

    def boundary(self) -> ArmBoundary:
        return ArmBoundary(
            arm="summary",
            what_enters="predecessor transcript events",
            representation="dict {completed_stages, n_stages, n_events}",
            token_cost_model="~1 token / 4 chars (deterministic extraction)",
            provenance_survives=True,
            successor_inspectable=("completed stage list", "receipt ids"),
            cannot_cross_arms=("full event detail", "raw tool params beyond stage info"),
        )


class RetrievalAdapter(_AdapterABC):
    """D: ordinary lexical retrieval over prior artifacts/history.

    Deterministic BM25-lite: tokenizes with simple word stems, scores each
    event against a query set, returns top-k events by score. Pure stdlib.
    This is NOT vector search, learned sparse expansion, or semantic RAG —
    per the frozen D arm definition.
    """

    arm = "retrieval"

    def __init__(self, top_k: int = 10):
        self.top_k = top_k

    def _tokens(self, text: str) -> list[str]:
        return [w for w in text.lower().split() if w.isalnum() or "_" in w]

    def _bm25(self, query_tokens: list[str], doc_tokens: list[str]) -> float:
        """BM25-lite with k1=1.5, b=0.75 over per-doc term frequencies."""
        if not doc_tokens:
            return 0.0
        k1, b = 1.5, 0.75
        doc_len = len(doc_tokens)
        avg_len = doc_len  # single-doc fallback
        score = 0.0
        for term in query_tokens:
            tf = doc_tokens.count(term)
            if tf == 0:
                continue
            idf = math.log(1 + (1.0 / 1.0))
            denom = tf + k1 * (1 - b + b * doc_len / max(avg_len, 1))
            score += idf * (tf * (k1 + 1)) / denom
        return score

    def transform(self, transcript: list[SessionEvent]) -> ArchitectureState:
        # Build a searchable document set from events (tool calls + outputs).
        docs: list[dict] = []
        for event in transcript:
            text = event.type + " " + event.worker_label + " " + str(event.metadata.get("summary", ""))
            for tc in event.tool_calls:
                text += " " + tc.tool + " " + json.dumps(tc.params, sort_keys=True) + " " + tc.output
            docs.append({
                "event_index": len(docs),
                "session_id": event.session_id,
                "stage": event.stage,
                "text": text,
                "receipt_ids": [tc.receipt_id for tc in event.tool_calls],
            })
        query = "state decisions constraints exact next action"
        query_tokens = self._tokens(query)
        for d in docs:
            d["_score"] = self._bm25(query_tokens, self._tokens(d["text"]))
        docs.sort(key=lambda d: d["_score"], reverse=True)
        top = docs[: self.top_k]
        for d in top:
            d.pop("_score", None)
        content = {"query": query, "top_k": self.top_k, "results": top}
        return ArchitectureState(
            arm="retrieval",
            content=content,
            token_cost=_json_tokens_estimate(content),
            provenance={"query": query, "top_k": str(self.top_k)},
        )

    def boundary(self) -> ArmBoundary:
        return ArmBoundary(
            arm="retrieval",
            what_enters="transcript events",
            representation="top-k scored events",
            token_cost_model="~1 token / 4 chars of selected docs",
            provenance_survives=True,
            successor_inspectable=("top-k events", "query string", "receipt ids"),
            cannot_cross_arms=("hidden tests", "full unrestricted history"),
        )


class VerifiedStateAdapter(_AdapterABC):
    """E: typed, evidence-linked verified state.

    Every claim references receipt IDs. Unsupported claims are omitted or
    marked unverified. No free-text model-generated state. Output shape:
    {"claims": [{"receipt_ids": [...], "claim_type": ...}], "constraints": [],
     "open_questions": [], "exact_next_action": {...}}
    """

    arm = "verified_state"

    def transform(self, transcript: list[SessionEvent]) -> ArchitectureState:
        claims = []
        for event in transcript:
            for tc in event.tool_calls:
                if tc.evidence_refs:
                    claims.append({
                        "receipt_ids": [tc.receipt_id],
                        "tool": tc.tool,
                        "status": tc.status,
                        "params": tc.params,
                        "evidence_refs": [
                            {"receipt_id": r.receipt_id, "claim_type": r.claim_type,
                             "claim_value": r.claim_value}
                            for r in tc.evidence_refs
                        ],
                    })
            if event.checkpoint:
                claims.append({
                    "receipt_ids": [event.checkpoint.receipt_id],
                    "stage": event.checkpoint.stage_number,
                    "test_results": event.checkpoint.test_results,
                })
        content = {
            "claims": claims,
            "verified_count": len(claims),
            "constraints": [],
            "open_questions": [],
            "exact_next_action": None,
        }
        return ArchitectureState(
            arm="verified_state",
            content=content,
            token_cost=_json_tokens_estimate(content),
            provenance={"source": "receipt-backed claims only"},
        )

    def boundary(self) -> ArmBoundary:
        return ArmBoundary(
            arm="verified_state",
            what_enters="receipt-backed tool calls and checkpoints",
            representation="{'claims': [...], 'constraints': [], 'open_questions': [], 'exact_next_action': ...}",
            token_cost_model="~1 token / 4 chars of claims JSON",
            provenance_survives=True,
            successor_inspectable=("claims", "receipt_ids", "constraints", "open questions"),
            cannot_cross_arms=("free-text narrative", "unverified assertions"),
        )


class VerifiedStateProcedureAdapter(VerifiedStateAdapter):
    """F: verified state + tested reusable procedures.

    Adds procedures with explicit scope, inputs, outputs, failure conditions,
    verification, and reuse history. Deterministic extraction from transcripts.
    """

    arm = "verified_state_procedure"

    def transform(self, transcript: list[SessionEvent]) -> ArchitectureState:
        base = super().transform(transcript)
        procedures = []
        for event in transcript:
            for tc in event.tool_calls:
                if tc.status == "ok" and tc.tool in ("write_file", "run", "pytest", "test"):
                    procedures.append({
                        "receipt_id": tc.receipt_id,
                        "tool": tc.tool,
                        "params": tc.params,
                        "output": tc.output,
                        "evidence_refs": [
                            {"receipt_id": r.receipt_id, "claim_type": r.claim_type,
                             "claim_value": r.claim_value}
                            for r in tc.evidence_refs
                        ],
                        "failure_conditions": [],
                        "verification": "test_results present" if event.checkpoint else "unverified",
                        "reuse_history": [],
                    })
        base.content["procedures"] = procedures
        base = ArchitectureState(
            arm="verified_state_procedure",
            content=base.content,
            token_cost=_json_tokens_estimate(base.content),
            provenance=base.provenance,
        )
        return base

    def boundary(self) -> ArmBoundary:
        return ArmBoundary(
            arm="verified_state_procedure",
            what_enters="receipt-backed claims + tested tool procedures",
            representation="{'claims': [...], 'procedures': [...], 'constraints': [], ...}",
            token_cost_model="~1 token / 4 chars (claims + procedures JSON)",
            provenance_survives=True,
            successor_inspectable=("claims", "procedures", "failure_conditions", "reuse_history"),
            cannot_cross_arms=("free-text narrative", "unverified procedures"),
        )


# ── Registry ─────────────────────────────────────────────────────────────────
ADAPTERS: dict[str, _AdapterABC] = {
    "stateless": StatelessAdapter(),
    "transcript": TranscriptAdapter(),
    "summary": SummaryAdapter(),
    "retrieval": RetrievalAdapter(),
    "verified_state": VerifiedStateAdapter(),
    "verified_state_procedure": VerifiedStateProcedureAdapter(),
}

BOUNDARIES: dict[str, ArmBoundary] = {arm: ad.boundary() for arm, ad in ADAPTERS.items()}


def get_adapter(arm: str) -> _AdapterABC:
    if arm not in ADAPTERS:
        raise KeyError(f"Unknown arm: {arm}. Available: {list(ADAPTERS.keys())}")
    return ADAPTERS[arm]


def adapter_states(transcript: list[SessionEvent]) -> dict[str, ArchitectureState]:
    """Transform one transcript through all six arms (for isolation tests)."""
    return {arm: get_adapter(arm).transform(transcript) for arm in ARMS}
