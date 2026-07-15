"""Checkpoint evaluator for the benchmark harness."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any

from .fixtures import FixtureSet
from .schemas import CheckpointReceipt, compute_sha256, make_receipt_id


def _run_test(test: dict, data: Any) -> bool:
    name = test["name"]
    params = test.get("params", {})

    if name == "is_valid_json":
        return isinstance(data, (dict, list))
    if name == "has_key":
        key = params.get("key", "")
        return isinstance(data, dict) and key in data
    if name == "has_records_key":
        key = params.get("key", "")
        if not isinstance(data, list):
            return False
        if len(data) == 0:
            return False
        return all(isinstance(r, dict) and key in r for r in data)
    if name == "correct_record_count":
        expected = params.get("expected", 0)
        return isinstance(data, list) and len(data) == expected
    if name == "total_entries_matches":
        expected = params.get("expected", 0)
        return isinstance(data, dict) and data.get("total_entries") == expected
    if name == "total_records_matches":
        expected = params.get("expected", 0)
        return isinstance(data, dict) and data.get("total_records") == expected
    if name == "summary_has_key":
        key = params.get("key", "")
        return isinstance(data, dict) and isinstance(data.get("summary"), dict) and key in data["summary"]
    if name == "validation_is_pass":
        return isinstance(data, dict) and data.get("validation") == "PASS"
    if name == "all_checks_present":
        if not isinstance(data, dict):
            return False
        checks = data.get("checks")
        if not isinstance(checks, dict):
            return False
        if len(checks) == 0:
            return False
        expected_keys = params.get("expected_keys", [])
        if expected_keys:
            return all(k in checks for k in expected_keys)
        return True
    if name == "acceptance_tests_match":
        """Semantic validation: validation=='PASS', structure_valid==True,
        all_stages_present==True, acceptance_tests_passed and total_tests are
        non-boolean integers, total_tests > 0, acceptance_tests_passed == total_tests."""
        if not isinstance(data, dict):
            return False
        if data.get("validation") != "PASS":
            return False
        checks = data.get("checks")
        if not isinstance(checks, dict):
            return False
        sv = checks.get("structure_valid")
        if sv is not True:
            return False
        asp = checks.get("all_stages_present")
        if asp is not True:
            return False
        atp = checks.get("acceptance_tests_passed")
        tt = checks.get("total_tests")
        if not isinstance(atp, int) or isinstance(atp, bool):
            return False
        if not isinstance(tt, int) or isinstance(tt, bool):
            return False
        if tt <= 0:
            return False
        if atp != tt:
            return False
        return True
    if name == "anomalies_found_positive":
        return isinstance(data, dict) and isinstance(data.get("anomalies_found"), (int, float)) and data["anomalies_found"] > 0
    if name == "normalized_is_true":
        return isinstance(data, dict) and data.get("normalized") is True
    return False


def evaluate_checkpoint(
    stage: int,
    artifact_dir: str | Path,
    fixture: FixtureSet,
    worker_label: str = "?",
    session_token_count: int = 0,
    trajectory_id: str = "unknown",
) -> CheckpointReceipt | None:
    artifact_dir = Path(artifact_dir)
    artifact_def = fixture.stage_artifacts.get(stage)
    if artifact_def is None:
        return None

    artifact_path = (artifact_dir / artifact_def.path).resolve()
    allowed_root = artifact_dir.resolve()

    try:
        artifact_path.relative_to(allowed_root)
    except ValueError:
        return None

    if not artifact_path.exists():
        return None
    try:
        content = artifact_path.read_text()
    except (OSError, PermissionError):
        return None

    sha256 = compute_sha256(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None

    tests = fixture.stage_tests.get(stage, [])
    test_results: dict[str, bool] = {}
    all_pass = True
    for test in tests:
        result = _run_test(test, data)
        test_results[test["name"]] = result
        if not result:
            all_pass = False

    if not all_pass:
        return None

    receipt_id = make_receipt_id("cp", trajectory_id, worker_label, stage)
    receipt = CheckpointReceipt(
        receipt_id=receipt_id,
        stage_number=stage,
        worker_label=worker_label,
        artifact_path=str(artifact_path),
        artifact_sha256=sha256,
        test_results=test_results,
        timestamp=time.time(),
        session_token_count=session_token_count,
    )
    return receipt


def evaluate_all_stages(
    artifact_dir: str | Path,
    fixture: FixtureSet,
    worker_labels: dict[int, str] | None = None,
    session_token_counts: dict[int, int] | None = None,
    trajectory_id: str = "unknown",
) -> dict[int, CheckpointReceipt | None]:
    if worker_labels is None:
        worker_labels = {1: "A", 2: "B", 3: "C", 4: "D"}
    if session_token_counts is None:
        session_token_counts = {}
    results: dict[int, CheckpointReceipt | None] = {}
    for stage in range(1, 5):
        results[stage] = evaluate_checkpoint(
            stage=stage,
            artifact_dir=artifact_dir,
            fixture=fixture,
            worker_label=worker_labels.get(stage, "?"),
            session_token_count=session_token_counts.get(stage, 0),
            trajectory_id=trajectory_id,
        )
    return results
