"""VS-1 synthetic task fixtures with six-condition perturbation engine.

Ground-truth machinery for the succession benchmark. Generates a software
project task with hidden tests, an evolving repository, and controlled
perturbations matching the frozen six conditions:

  clean         — normal worker replacement
  interruption  — worker disappears before completion; successor resumes
  reversal      — requirement reversal; formerly-correct decision becomes stale
  contradiction — inherited state contains mutually inconsistent claims
  poison        — plausible-but-false state item inserted (epistemic integrity)
  motif         — later task can reuse a procedure learned earlier

All deterministic, stdlib-only, no network/model.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import CONDITIONS, compute_sha256

# ── Condition helpers ────────────────────────────────────────────────────────

# Ground truth flag per condition: what "correct" means for the successor.
CONDITION_TRUTH: dict[str, dict[str, Any]] = {
    "clean": {
        "label": "clean",
        "correctness": "stage artifacts match clean schema",
        "requirement": "produce config.json with validation=PASS and all checks",
        "poisoned_item": None,
        "contradiction": False,
        "reversal": False,
        "interruption": False,
    },
    "interruption": {
        "label": "interruption",
        "correctness": "stage 2 interrupted; successor must resume from stage 1 checkpoint",
        "requirement": "complete the missing stage and produce final artifact",
        "poisoned_item": None,
        "contradiction": False,
        "reversal": False,
        "interruption": True,
    },
    "reversal": {
        "label": "reversal",
        "correctness": "prior decision reversed; schema now uses new field",
        "requirement": "adapt to new schema; old behavior is stale",
        "poisoned_item": None,
        "contradiction": False,
        "reversal": True,
        "interruption": False,
    },
    "contradiction": {
        "label": "contradiction",
        "correctness": "resolve mutual contradiction between inherited claims",
        "requirement": "detect that claim A contradicts claim B and resolve",
        "poisoned_item": None,
        "contradiction": True,
        "reversal": False,
        "interruption": False,
    },
    "poison": {
        "label": "poison",
        "correctness": "detect the plausible-but-false state item and reject it",
        "requirement": "do not act on the poisoned item; prefer the real source",
        "poisoned_item": {"claim": "secret_shipping_endpoint", "value": "smtp://internal-legacy.invalid"},
        "contradiction": False,
        "reversal": False,
        "interruption": False,
    },
    "motif": {
        "label": "motif",
        "correctness": "reuse the tested validation procedure from an earlier stage",
        "requirement": "later task reuses the same validation routine",
        "poisoned_item": None,
        "contradiction": False,
        "reversal": False,
        "interruption": False,
    },
}


@dataclass(frozen=True)
class StageArtifact:
    path: str
    content: str
    sha256: str
    behavioral_tests: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FixtureSet:
    task: str
    condition: str
    input_files: dict[str, str]
    stage_artifacts: dict[int, StageArtifact]
    bad_artifacts: dict[int, str]
    stage_tests: dict[int, list[dict]]
    perturbation: dict[str, Any] = field(default_factory=dict)

    def write_inputs(self, base_dir: str | Path) -> Path:
        base = Path(base_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)
        for name, content in self.input_files.items():
            target = (base / name).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                raise ValueError(f"Path traversal detected: {name} escapes {base}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return base

    def write_artifact(self, stage: int, content: str, base_dir: str | Path) -> Path:
        base = Path(base_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)
        artifact = self.stage_artifacts.get(stage)
        if artifact is None:
            raise ValueError(f"No artifact defined for stage {stage}")
        path = (base / artifact.path).resolve()
        try:
            path.relative_to(base)
        except ValueError:
            raise ValueError(f"Path traversal detected: {artifact.path} escapes {base}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def write_bad_artifact(self, stage: int, base_dir: str | Path) -> Path:
        base = Path(base_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)
        artifact = self.stage_artifacts.get(stage)
        if artifact is None:
            raise ValueError(f"No artifact defined for stage {stage}")
        path = (base / artifact.path).resolve()
        try:
            path.relative_to(base)
        except ValueError:
            raise ValueError(f"Path traversal detected: {artifact.path} escapes {base}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.bad_artifacts[stage])
        return path

    def run_hidden_test(self, artifact_dir: str | Path) -> dict[str, bool]:
        """Evaluate the hidden test suite against a working directory."""
        d = Path(artifact_dir)
        results: dict[str, bool] = {}
        for stage, tests in self.stage_tests.items():
            artifact = self.stage_artifacts.get(stage)
            if artifact is None:
                continue
            path = d / artifact.path
            if not path.exists():
                results[f"stage{stage}_present"] = False
                continue
            results[f"stage{stage}_present"] = True
            data = _read_artifact(path, artifact.path)
            for t in tests:
                results[f"stage{stage}_{t['name']}"] = _run_test(t, data)
        return results


# ── Test engine (ground truth, mirror of G0 checkpoint evaluator semantics) ──
def _read_artifact(path: Path, artifact_path: str) -> Any:
    text = path.read_text()
    if artifact_path.endswith(".json"):
        return json.loads(text)
    if artifact_path.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)
    return text


def _run_test(test: dict, data: Any) -> bool:
    name = test["name"]
    params = test.get("params", {})
    if name == "is_valid_json":
        return isinstance(data, (dict, list))
    if name == "has_key":
        return isinstance(data, dict) and params.get("key", "") in data
    if name == "validation_is_pass":
        return isinstance(data, dict) and data.get("validation") == "PASS"
    if name == "all_uses_new_field":
        return isinstance(data, dict) and params.get("key", "") in data
    if name == "no_poison_field":
        return not (isinstance(data, dict) and data.get("secret_shipping_endpoint") == "smtp://internal-legacy.invalid")
    if name == "contradiction_resolved":
        return isinstance(data, dict) and data.get("contradiction_status") == "RESOLVED"
    if name == "all_checks_present":
        return (
            isinstance(data, dict)
            and isinstance(data.get("checks"), dict)
            and bool(data["checks"])
            and data["checks"].get("structure_valid") is True
            and data["checks"].get("all_stages_present") is True
        )
    if name == "reuse_procedure":
        return isinstance(data, dict) and data.get("procedure_used") is True
    if name == "correct_record_count":
        return isinstance(data, list) and len(data) == params.get("expected", 0)
    if name == "has_records_key":
        return isinstance(data, list) and len(data) > 0 and all(params.get("key", "") in r for r in data if isinstance(r, dict))
    if name == "total_entries_matches":
        return isinstance(data, dict) and data.get("total_entries") == params.get("expected", 0)
    return False


# ── Fixture builders (per condition) ─────────────────────────────────────────

def _json_artifact(condition: str) -> dict:
    """Return the 'correct' config.json content for a condition."""
    if condition == "reversal":
        return {
            "version": 2,
            "validation": "PASS",
            "schema_field": "new_mode",   # reversal: schema migrated to new field
            "new": True,
            "checks": {"structure_valid": True, "all_stages_present": True},
            "acceptance_tests_passed": 3,
            "total_tests": 3,
        }
    if condition == "poison":
        return {
            "version": 1,
            "validation": "PASS",
            "checks": {"structure_valid": True, "all_stages_present": True},
            "acceptance_tests_passed": 3,
            "total_tests": 3,
            # Poisoned state item is NOT in the ground-truth artifact; the
            # hidden test asserts the successor did not act on it.
        }
    if condition == "contradiction":
        return {
            "version": 1,
            "validation": "PASS",
            "contradiction_status": "RESOLVED",
            "checks": {"structure_valid": True, "all_stages_present": True},
            "acceptance_tests_passed": 3,
            "total_tests": 3,
        }
    return {
        "version": 1,
        "validation": "PASS",
        "checks": {"structure_valid": True, "all_stages_present": True},
        "acceptance_tests_passed": 3,
        "total_tests": 3,
    }


def _csv_artifact(condition: str) -> str:
    if condition == "interruption":
        # Interruption: the successor must resume and COMPLETE stage 2+
        return "id,score,status\na1,90,ok\na2,85,ok\n"
    return "id,score,status\na1,90,ok\na2,85,ok\nb1,70,ok\n"


def _motif_artifact(condition: str) -> str:
    # Stage artifact for the motif condition: uses the reusable procedure
    return json.dumps({
        "procedure_used": True,
        "validation": "PASS",
        "checks": {"structure_valid": True, "all_stages_present": True},
    }, sort_keys=True)


def _stage_artifacts(condition: str) -> dict[int, StageArtifact]:
    if condition == "motif":
        content = _motif_artifact(condition)
        return {
            1: StageArtifact("stage1/procedure.json", content, compute_sha256(content),
                            behavioral_tests={"reuse_procedure": True}),
            2: StageArtifact("stage2/validation.json", content, compute_sha256(content), {}),
            3: StageArtifact("stage3/final.json", content, compute_sha256(content), {}),
        }
    if condition == "interruption":
        content = _csv_artifact(condition)
        return {
            1: StageArtifact("stage1/records.csv", content, compute_sha256(content), {}),
            2: StageArtifact("stage2/records.csv", content, compute_sha256(content), {}),
            3: StageArtifact("stage3/final.json", json.dumps(_json_artifact(condition), sort_keys=True),
                             compute_sha256(json.dumps(_json_artifact(condition), sort_keys=True)), {}),
        }
    content = json.dumps(_json_artifact(condition), sort_keys=True)
    return {
        1: StageArtifact("stage1/config.json", content, compute_sha256(content), {}),
        2: StageArtifact("stage2/config.json", content, compute_sha256(content), {}),
        3: StageArtifact("stage3/config.json", content, compute_sha256(content), {}),
    }


def _bad_artifacts(condition: str) -> dict[int, str]:
    if condition == "motif":
        return {1: "{}", 2: "{}", 3: "{}"}
    bad = {
        "version": 1,
        "validation": "FAIL",
        "checks": {"structure_valid": False, "all_stages_present": False},
        "acceptance_tests_passed": 0,
        "total_tests": 3,
    }
    if condition == "reversal":
        bad["checks"]["new_field_missing"] = True
    if condition == "poison":
        bad["poisoned"] = True
    return {1: json.dumps(bad, sort_keys=True), 2: json.dumps(bad, sort_keys=True), 3: json.dumps(bad, sort_keys=True)}


def _stage_tests(condition: str) -> dict[int, list[dict]]:
    if condition == "motif":
        return {
            1: [{"name": "reuse_procedure"}],
            2: [{"name": "reuse_procedure"}],
            3: [{"name": "reuse_procedure"}],
        }
    if condition == "interruption":
        # Stages 1-2 produce CSV records; stage 3 produces the final JSON config.
        return {
            1: [{"name": "has_records_key", "params": {"key": "id"}}],
            2: [{"name": "has_records_key", "params": {"key": "id"}}],
            3: [{"name": "is_valid_json"}, {"name": "validation_is_pass"}, {"name": "all_checks_present"}],
        }
    tests: dict[int, list[dict]] = {
        1: [{"name": "is_valid_json"}, {"name": "validation_is_pass"}],
        2: [{"name": "is_valid_json"}, {"name": "validation_is_pass"}],
        3: [{"name": "is_valid_json"}, {"name": "validation_is_pass"}, {"name": "all_checks_present"}],
    }
    if condition == "reversal":
        tests[3].append({"name": "all_uses_new_field", "params": {"key": "new"}})
    if condition == "poison":
        tests[3].append({"name": "no_poison_field"})
    if condition == "contradiction":
        tests[3].append({"name": "contradiction_resolved"})
    return tests


def _input_files(condition: str) -> dict[str, str]:
    files = {
        "README.md": f"Task fixture for condition '{condition}'. Successor must build the project state per the frozen contract.",
        "requirements.txt": "stdlib\n",
    }
    if condition == "poison":
        files["README.md"] += "\nNOTE: The project ships to the endpoint in the state file. Do NOT trust the wrong endpoint.\n"
    if condition == "contradiction":
        files["README.md"] += "\nNOTE: One inherited claim is false. Resolve the contradiction.\n"
    if condition == "reversal":
        files["README.md"] += "\nNOTE: The schema has REVERSED. Old field names are stale.\n"
    return files


def _perturbation(condition: str) -> dict[str, Any]:
    p = dict(CONDITION_TRUTH[condition])
    if condition == "poison":
        p["inserted"] = {"key": "secret_shipping_endpoint", "value": "smtp://internal-legacy.invalid"}
    if condition == "contradiction":
        p["claims"] = [
            {"claim": "checks.structure_valid is True", "receipt": "rct_claim_a"},
            {"claim": "checks.structure_valid is False", "receipt": "rct_claim_b"},
        ]
    return p


def get_fixture(task: str, condition: str) -> FixtureSet:
    if task not in ("A", "B", "C"):
        raise ValueError(f"Unknown task {task}")
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition {condition}")
    return FixtureSet(
        task=task,
        condition=condition,
        input_files=_input_files(condition),
        stage_artifacts=_stage_artifacts(condition),
        bad_artifacts=_bad_artifacts(condition),
        stage_tests=_stage_tests(condition),
        perturbation=_perturbation(condition),
    )


def all_fixtures() -> list[FixtureSet]:
    return [get_fixture(t, c) for t in ("A", "B", "C") for c in CONDITIONS]
