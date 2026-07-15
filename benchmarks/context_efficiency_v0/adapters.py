"""Pilot adapter interfaces for the benchmark harness.

Each adapter transforms a raw session transcript into architecture-specific
state for the successor. No model, API, or network calls.

Exactly three pilot adapters: StatelessAdapter, SummaryAdapter, VerifiedStateAdapter.
"""

from __future__ import annotations
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .schemas import (
    SessionEvent,
    ToolCallReceipt,
    EvidenceReference,
    compute_sha256,
)


@dataclass(frozen=True)
class ArchitectureState:
    """State delivered to a successor worker."""
    architecture: str
    content: Any  # Architecture-specific content
    token_cost: int = 0  # Tokens consumed to produce this state


class _AdapterABC(ABC):
    """Abstract base for all architecture adapters."""

    @abstractmethod
    def transform(
        self,
        transcript: list[SessionEvent],
    ) -> ArchitectureState:
        """Transform a raw transcript into architecture-specific state."""
        ...


class StatelessAdapter(_AdapterABC):
    """Regime 1: No state passed to successor."""

    def transform(
        self,
        transcript: list[SessionEvent],
    ) -> ArchitectureState:
        return ArchitectureState(
            architecture="stateless",
            content={},
            token_cost=0,
        )


class SummaryAdapter(_AdapterABC):
    """Regime 4: Deterministic synthetic summary — no model call.

    Produces a dict with completed_stages and checkpoint_receipt_ids
    derived from transcript checkpoint data. Deterministic across runs.
    """

    def transform(
        self,
        transcript: list[SessionEvent],
    ) -> ArchitectureState:
        completed_stages = []
        checkpoint_receipt_ids = []

        for event in transcript:
            if event.checkpoint:
                completed_stages.append(event.checkpoint.stage_number)
                checkpoint_receipt_ids.append(event.checkpoint.receipt_id)

        content = {
            "completed_stages": completed_stages,
            "checkpoint_receipt_ids": checkpoint_receipt_ids,
        }

        # Token cost: approximate at 1 token per 4 characters
        token_cost = len(json.dumps(content, sort_keys=True)) // 4

        return ArchitectureState(
            architecture="summary",
            content=content,
            token_cost=token_cost,
        )


class VerifiedStateAdapter(_AdapterABC):
    """Regime 5: Programmatic verified state from receipts.

    Every claim references receipt IDs. Unsupported claims are omitted
    or marked unverified. No free-text model-generated state.
    Output shape: {"claims": [{"receipt_ids": [...], ...}]}
    """

    def transform(
        self,
        transcript: list[SessionEvent],
    ) -> ArchitectureState:
        claims = []

        for event in transcript:
            for tc in event.tool_calls:
                # Only include claims backed by evidence references
                if tc.evidence_refs:
                    claim = {
                        "receipt_ids": [tc.receipt_id],
                        "tool": tc.tool,
                        "status": tc.status,
                        "params": tc.params,
                        "evidence_refs": [
                            {"receipt_id": r.receipt_id, "claim_type": r.claim_type, "claim_value": r.claim_value}
                            for r in tc.evidence_refs
                        ],
                    }
                    claims.append(claim)

            if event.checkpoint:
                claims.append({
                    "receipt_ids": [event.checkpoint.receipt_id],
                    "stage": event.checkpoint.stage_number,
                    "test_results": event.checkpoint.test_results,
                })

        content = {
            "claims": claims,
        }

        # Token cost: approximate JSON size
        token_cost = len(json.dumps(content, sort_keys=True)) // 4

        return ArchitectureState(
            architecture="verified_state",
            content=content,
            token_cost=token_cost,
        )


# Adapter registry — exactly three adapters
ADAPTERS: dict[str, _AdapterABC] = {
    "stateless": StatelessAdapter(),
    "summary": SummaryAdapter(),
    "verified_state": VerifiedStateAdapter(),
}


def get_adapter(architecture: str) -> _AdapterABC:
    """Get an adapter by architecture name."""
    if architecture not in ADAPTERS:
        raise KeyError(f"Unknown architecture: {architecture}. Available: {list(ADAPTERS.keys())}")
    return ADAPTERS[architecture]
