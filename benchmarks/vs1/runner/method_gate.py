"""VS-1 R4 method-failure gate — frozen prospectively (act §6).

The prior ">5%" language had no denominator and no mechanical enforcement.
This gate is the executable definition, frozen BEFORE R4.

FORMULA (frozen):
  method-failure rate = true_method_failures / attempted_calls
  where:
    attempted_calls = provider invocations actually made (not planned)
    true_method_failures = PROVIDER_RUNTIME_FAILURE + INSTRUMENT_FAILURE
                            + MIXED_AMBIGUOUS (cannot validly observe)
    SUBJECT_TASK_FAILURE is NOT a method failure (scientific outcome)

GATE RULES (frozen):
  1. MIN_SAMPLE = 10 attempted calls before the 5% threshold can trigger
     (a single early transient must not halt the run).
  2. After min_sample: if rate > 5% -> HALT (METHOD_FAILURE_TOLERANCE_EXCEEDED).
  3. CATASTROPHIC_BURST = 3 consecutive true method failures at any point
     -> HALT immediately, regardless of percentage (covers the act's
     6-of-first-6 scenario before min_sample is reached).
  4. Immediate validity failures (contamination, hidden-test leakage,
     evidence loss, model mismatch, scoring corruption, cross-arm leakage)
     -> HALT immediately regardless of percentage.

The gate is evaluated after every call. It is call-level (each provider
invocation is one observation; multi-call interruption trajectories count
per call, matching the statistical unit of provider invocation).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from benchmarks.vs1.runner.classification import (
    INSTRUMENT_FAILURE,
    MIXED_AMBIGUOUS,
    PROVIDER_RUNTIME_FAILURE,
    SUBJECT_TASK_FAILURE,
)

# Frozen parameters
METHOD_FAILURE_THRESHOLD = 0.05  # 5%
MIN_SAMPLE = 10  # attempted calls before threshold can trigger
CATASTROPHIC_BURST = 3  # consecutive method failures -> immediate halt

# Categories that count as true method failures
METHOD_FAILURE_CATEGORIES = {
    PROVIDER_RUNTIME_FAILURE,
    INSTRUMENT_FAILURE,
    MIXED_AMBIGUOUS,
}


@dataclass
class MethodGateState:
    attempted_calls: int = 0
    method_failures: int = 0
    subject_task_failures: int = 0
    consecutive_method_failures: int = 0
    halted: bool = False
    halt_reason: str = ""
    history: list[dict] = field(default_factory=list)

    @property
    def rate(self) -> float:
        if self.attempted_calls == 0:
            return 0.0
        return self.method_failures / self.attempted_calls

    def record(self, classification: dict) -> None:
        """Record one call's classification and evaluate the gate."""
        self.attempted_calls += 1
        cat = classification["category"]
        entry = {
            "attempt": self.attempted_calls,
            "category": cat,
            "reason": classification.get("reason", ""),
        }
        self.history.append(entry)

        if cat in METHOD_FAILURE_CATEGORIES:
            self.method_failures += 1
            self.consecutive_method_failures += 1
        elif cat == SUBJECT_TASK_FAILURE:
            self.subject_task_failures += 1
            self.consecutive_method_failures = 0
        else:  # OK
            self.consecutive_method_failures = 0

        self._evaluate()

    def _evaluate(self) -> None:
        if self.halted:
            return
        # Rule 3: catastrophic burst
        if self.consecutive_method_failures >= CATASTROPHIC_BURST:
            self.halted = True
            self.halt_reason = (
                f"CATASTROPHIC_BURST: {self.consecutive_method_failures} consecutive "
                f"method failures at attempt {self.attempted_calls}"
            )
            return
        # Rule 2: threshold after min sample
        if self.attempted_calls >= MIN_SAMPLE and self.rate > METHOD_FAILURE_THRESHOLD:
            self.halted = True
            self.halt_reason = (
                f"METHOD_FAILURE_TOLERANCE_EXCEEDED: {self.method_failures}/"
                f"{self.attempted_calls} = {self.rate:.2%} > {METHOD_FAILURE_THRESHOLD:.0%} "
                f"(min sample {MIN_SAMPLE} reached)"
            )

    def to_json(self) -> dict:
        return {
            "attempted_calls": self.attempted_calls,
            "method_failures": self.method_failures,
            "subject_task_failures": self.subject_task_failures,
            "consecutive_method_failures": self.consecutive_method_failures,
            "rate": round(self.rate, 4),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "threshold": METHOD_FAILURE_THRESHOLD,
            "min_sample": MIN_SAMPLE,
            "catastrophic_burst": CATASTROPHIC_BURST,
            "history": self.history,
        }


def immediate_validity_halt(reason: str) -> dict:
    """Rule 4: catastrophic validity failures halt regardless of percentage."""
    return {
        "halted": True,
        "halt_reason": f"IMMEDIATE_VALIDITY_FAILURE: {reason}",
        "category": "VALIDITY_FAILURE",
    }
