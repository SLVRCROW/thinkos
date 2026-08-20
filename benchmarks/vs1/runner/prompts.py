"""VS-1 frozen prompt templates.

Core task instruction is IDENTICAL for every arm (protocol §13). The only
difference between arms is the inherited state block appended after it —
that difference IS the declared manipulation. No extra coaching language
("verify carefully", "use provenance", "check contradictions") appears in
any arm's prompt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..fixtures import CONDITION_TRUTH
from ..schemas import compute_sha256


@dataclass(frozen=True)
class PromptTemplate:
    prompt_id: str
    arm: str
    condition: str
    stage: int
    text: str
    sha256: str

    def to_json(self) -> dict:
        return {
            "prompt_id": self.prompt_id,
            "arm": self.arm,
            "condition": self.condition,
            "stage": self.stage,
            "text": self.text,
            "sha256": self.sha256,
        }


CORE_TASK = """You are the successor operator continuing a software project task. A previous worker left the project in the state described in the INHERITED STATE section below.

TASK:
Produce the final artifact file for this project by writing the file to the path given. The artifact must be valid JSON unless stated otherwise, with:
- "validation": "PASS"
- "checks": {"structure_valid": true, "all_stages_present": true}
- "acceptance_tests_passed" equal to "total_tests"
- "total_tests" equal to 3
- any additional required fields described in your INHERITED STATE or the project README

Where the inherited state or README references a choice between conflicting values, apply the correct one. Complete the task in one shot; write the artifact now.

OUTPUT FORMAT: Reply with ONLY the file content (the JSON object). No commentary."""


TASK_ARTIFACT_PATH = {
    2: "stage2/config.json",
    3: "stage3/config.json",
}


def _render_state(arm: str, state: Any) -> str:
    """Render the inherited state for a given arm. Deterministic JSON.

    This is the ONLY difference between arms. No coaching, no hints that one
    representation is 'better'. Content is the adapter's frozen output.
    """
    if arm == "stateless" or state is None:
        return "NONE"
    content = getattr(state, "content", state)
    return json.dumps(content, sort_keys=True, ensure_ascii=True)


def build_prompt(
    arm: str,
    condition: str,
    stage: int,
    state: Any,
    prompt_version: str = "v0.1.0",
) -> PromptTemplate:
    """Build the frozen prompt for one cell.

    Core instruction is constant; only the inherited state block differs.
    """
    path = TASK_ARTIFACT_PATH.get(stage) or "the artifact path"
    body = CORE_TASK.replace("<ARTIFACT_PATH>", path)
    condition_note = CONDITION_TRUTH.get(condition, {}).get("requirement", "")
    prompt_text = (
        f"{body}\n\n"
        f"CONDITION NOTE: {condition_note}\n\n"
        f"INHERITED STATE:\n{_render_state(arm, state)}\n"
    )
    prompt_id = f"vs1-{prompt_version}-{arm}-{condition}-s{stage}"
    return PromptTemplate(
        prompt_id=prompt_id,
        arm=arm,
        condition=condition,
        stage=stage,
        text=prompt_text,
        sha256=compute_sha256(prompt_text),
    )
