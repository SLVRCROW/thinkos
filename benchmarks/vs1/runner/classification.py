"""VS-1 R4 failure classification — frozen prospectively (act §4-5).

SUBJECT / TASK FAILURE:
  The model successfully returned a completion, but the completion did not
  satisfy the frozen task contract. These are scientific outcomes, scored
  by the frozen evaluation. They DO NOT count as experimental method failures.

METHOD / INSTRUMENT FAILURE:
  The experiment could not validly observe the intended subject performance
  because of infrastructure failure. These threaten experimental validity.

A parse failure alone is NOT a method failure. Because raw content is now
preserved, classification is:
  CASE A: raw completion does not satisfy the frozen artifact contract
          -> SUBJECT_TASK_FAILURE
  CASE B: raw completion satisfies the frozen contract but parser rejects it
          -> INSTRUMENT_FAILURE
  CASE C: cannot determine because contract/parser/evaluator disagree
          -> HOLD_CONTEXT_CONFLICT or BINDING_METHOD_DEFECT
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from benchmarks.vs1.schemas import compute_sha256


# ── Frozen classification vocabulary ─────────────────────────────────────────
SUBJECT_TASK_FAILURE = "SUBJECT_TASK_FAILURE"
INSTRUMENT_FAILURE = "INSTRUMENT_FAILURE"
PROVIDER_RUNTIME_FAILURE = "PROVIDER_RUNTIME_FAILURE"
MIXED_AMBIGUOUS = "MIXED_AMBIGUOUS"
OK = "OK"

# Provider statuses that are infrastructure failures by definition
PROVIDER_FAILURE_STATUSES = {"error", "timeout", "rate_limited", "connection_error"}


@dataclass(frozen=True)
class Classification:
    category: str  # one of the vocabulary above
    reason: str
    raw_hash: str
    parse_ok: bool
    contract_ok: bool | None = None  # None = not determinable

    def to_json(self) -> dict:
        return {
            "category": self.category,
            "reason": self.reason,
            "raw_hash": self.raw_hash,
            "parse_ok": self.parse_ok,
            "contract_ok": self.contract_ok,
        }


def classify_outcome(
    *,
    provider_status: str,
    provider_error: str,
    raw_content: str,
    parse_ok: bool,
    contract_ok: bool | None,
    target_path: str,
) -> Classification:
    """Classify one provider invocation per the frozen R4 rules.

    Order of checks (frozen):
    1. Provider failure status -> PROVIDER_RUNTIME_FAILURE (infrastructure).
    2. Empty completion -> PROVIDER_RUNTIME_FAILURE (provider returned no
       usable content; per act §7, empty completions are provider-side
       unless raw metadata proves otherwise).
    3. Parse failure:
       - contract_ok is True  -> INSTRUMENT_FAILURE (parser rejected valid)
       - contract_ok is False -> SUBJECT_TASK_FAILURE (model failed task)
       - contract_ok is None -> MIXED_AMBIGUOUS (cannot determine)
    4. Parse success -> OK (subject performed; scored by frozen evaluation).
    """
    raw_hash = compute_sha256(raw_content)

    if provider_status in PROVIDER_FAILURE_STATUSES:
        return Classification(
            category=PROVIDER_RUNTIME_FAILURE,
            reason=f"provider status={provider_status}: {provider_error}",
            raw_hash=raw_hash,
            parse_ok=False,
            contract_ok=None,
        )

    if not raw_content.strip():
        return Classification(
            category=PROVIDER_RUNTIME_FAILURE,
            reason="empty completion (provider returned no content)",
            raw_hash=raw_hash,
            parse_ok=False,
            contract_ok=None,
        )

    if parse_ok:
        # Atlas F1/F3: use the ACTUAL contract result, not a hardcoded True.
        # If the parser accepted but the contract check says the artifact is
        # substantively invalid (e.g., header-only CSV), that is a subject
        # task failure, not OK.
        if contract_ok is False:
            return Classification(
                category=SUBJECT_TASK_FAILURE,
                reason=f"parsed but does not satisfy frozen contract for {target_path}",
                raw_hash=raw_hash,
                parse_ok=True,
                contract_ok=False,
            )
        return Classification(
            category=OK,
            reason="parsed successfully",
            raw_hash=raw_hash,
            parse_ok=True,
            contract_ok=True,
        )

    # Parse failed. Distinguish subject vs instrument via contract check.
    if contract_ok is True:
        return Classification(
            category=INSTRUMENT_FAILURE,
            reason=f"parser rejected contract-valid output for {target_path}",
            raw_hash=raw_hash,
            parse_ok=False,
            contract_ok=True,
        )
    if contract_ok is False:
        return Classification(
            category=SUBJECT_TASK_FAILURE,
            reason=f"raw completion does not satisfy frozen contract for {target_path}",
            raw_hash=raw_hash,
            parse_ok=False,
            contract_ok=False,
        )
    return Classification(
        category=MIXED_AMBIGUOUS,
        reason=f"cannot determine contract compliance for {target_path}",
        raw_hash=raw_hash,
        parse_ok=False,
        contract_ok=None,
    )


def contract_check_csv(raw_content: str, target_path: str) -> bool | None:
    """Determine whether raw content satisfies the frozen CSV contract.

    Returns True (satisfies), False (does not), or None (cannot determine).
    Frozen contract for CSV artifacts: non-empty, has a header line with a
    comma, and at least one data row. This mirrors the parser's acceptance
    rule — a contract-valid CSV must be parseable by the frozen parser.
    Markdown fences are stripped exactly as the parser strips them, so a
    fenced CSV is contract-valid (consistency with parse_artifact).
    """
    if not target_path.endswith(".csv"):
        return None
    import re
    text = raw_content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    if not text:
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    header = lines[0]
    if "," not in header:
        return False
    # At least one data row beyond the header
    return len(lines) >= 2


def contract_check_json(raw_content: str, target_path: str) -> bool | None:
    """Determine whether raw content satisfies the frozen JSON contract.

    Returns True (satisfies), False (does not), or None (cannot determine).
    Frozen contract for JSON artifacts: contains a balanced JSON object.
    Atlas F2: brace counting must track string state so braces inside
    string values (e.g. {"key": "value {nested}"}) are not miscounted.
    Markdown fences are stripped exactly as the parser strips them.
    """
    if not target_path.endswith(".json"):
        return None
    import re
    text = raw_content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        return False
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    json.loads(text[start : i + 1])
                    return True
                except json.JSONDecodeError:
                    return False
    return False


def contract_check(raw_content: str, target_path: str) -> bool | None:
    """Dispatch to the format-specific contract check."""
    if target_path.endswith(".csv"):
        return contract_check_csv(raw_content, target_path)
    if target_path.endswith(".json"):
        return contract_check_json(raw_content, target_path)
    return None
