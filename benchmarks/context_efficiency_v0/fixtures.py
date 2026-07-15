"""Synthetic task fixtures for the benchmark harness.

Generates input files and known-good/bad stage artifacts for three tasks
across clean and drift conditions. No model, API, or network calls.
"""

from __future__ import annotations
import json
import os
import csv
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Task(Enum):
    A = "A"
    B = "B"
    C = "C"


class Condition(Enum):
    CLEAN = "clean"
    DRIFT = "drift"


@dataclass(frozen=True)
class StageArtifact:
    """Expected artifact for a stage."""
    path: str          # Relative path within the workdir
    content: str       # Known-good content
    sha256: str        # Precomputed SHA256 of content
    behavioral_tests: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FixtureSet:
    """Complete fixture set for one task × condition."""
    task: Task
    condition: Condition
    input_files: dict[str, str]          # filename -> content
    stage_artifacts: dict[int, StageArtifact]  # stage_number -> artifact
    bad_artifacts: dict[int, str]        # stage_number -> known-bad content
    stage_tests: dict[int, list[dict]]   # stage_number -> list of test descriptors

    def write_inputs(self, base_dir: str | Path) -> Path:
        """Write input files to base_dir and return the path. Enforces containment."""
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
        """Write a stage artifact to base_dir and return the path. Enforces containment."""
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
        """Write a known-bad artifact to base_dir and return the path. Enforces containment."""
        base = Path(base_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)
        bad_content = self.bad_artifacts.get(stage)
        if bad_content is None:
            raise ValueError(f"No bad artifact defined for stage {stage}")
        artifact = self.stage_artifacts.get(stage)
        if artifact is None:
            raise ValueError(f"No artifact defined for stage {stage}")
        path = (base / artifact.path).resolve()
        try:
            path.relative_to(base)
        except ValueError:
            raise ValueError(f"Path traversal detected: {artifact.path} escapes {base}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bad_content)
        return path


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ─── Task A: Log Parsing ────────────────────────────────────────────────

_LOG_LINES_CLEAN = """2026-01-15 08:30:00 INFO  User login successful user_id=42
2026-01-15 08:31:15 WARN  High memory usage user_id=42 memory_pct=87
2026-01-15 08:32:00 ERROR Disk full on /dev/sda1 user_id=0
2026-01-15 08:33:30 INFO  User logout user_id=42
2026-01-15 08:34:00 INFO  User login successful user_id=17
2026-01-15 08:35:15 WARN  Slow query detected user_id=17 query_time=4.2s
2026-01-15 08:36:00 ERROR Connection timeout user_id=17 host=db01
2026-01-15 08:37:30 INFO  User logout user_id=17
2026-01-15 08:38:00 INFO  User login successful user_id=99
2026-01-15 08:39:15 WARN  API rate limit approaching user_id=99 calls=95
2026-01-15 08:40:00 INFO  User logout user_id=99
"""

_LOG_LINES_DRIFT = """1747413000 INFO  User login successful user_id=42
1747413075 WARN  High memory usage user_id=42 memory_pct=87
1747413120 ERROR Disk full on /dev/sda1 user_id=0
1747413210 INFO  User logout user_id=42
1747413240 INFO  User login successful user_id=17
1747413315 WARN  Slow query detected user_id=17 query_time=4.2s
1747413360 ERROR Connection timeout user_id=17 host=db01
1747413450 INFO  User logout user_id=17
1747413480 INFO  User login successful user_id=99
1747413555 WARN  API rate limit approaching user_id=99 calls=95
1747413600 INFO  User logout user_id=99
"""

_STAGE1_GOOD_A = json.dumps([
    {"timestamp": "2026-01-15 08:30:00", "level": "INFO", "message": "User login successful", "user_id": 42},
    {"timestamp": "2026-01-15 08:31:15", "level": "WARN", "message": "High memory usage", "user_id": 42, "memory_pct": 87},
    {"timestamp": "2026-01-15 08:32:00", "level": "ERROR", "message": "Disk full on /dev/sda1", "user_id": 0},
    {"timestamp": "2026-01-15 08:33:30", "level": "INFO", "message": "User logout", "user_id": 42},
    {"timestamp": "2026-01-15 08:34:00", "level": "INFO", "message": "User login successful", "user_id": 17},
    {"timestamp": "2026-01-15 08:35:15", "level": "WARN", "message": "Slow query detected", "user_id": 17, "query_time": 4.2},
    {"timestamp": "2026-01-15 08:36:00", "level": "ERROR", "message": "Connection timeout", "user_id": 17, "host": "db01"},
    {"timestamp": "2026-01-15 08:37:30", "level": "INFO", "message": "User logout", "user_id": 17},
    {"timestamp": "2026-01-15 08:38:00", "level": "INFO", "message": "User login successful", "user_id": 99},
    {"timestamp": "2026-01-15 08:39:15", "level": "WARN", "message": "API rate limit approaching", "user_id": 99, "calls": 95},
    {"timestamp": "2026-01-15 08:40:00", "level": "INFO", "message": "User logout", "user_id": 99},
], indent=2)

_STAGE1_BAD_A = json.dumps([{"bad": "data"}], indent=2)

_STAGE2_GOOD_A = json.dumps({
    "total_entries": 11,
    "by_level": {"INFO": 6, "WARN": 3, "ERROR": 2},
    "unique_users": [0, 17, 42, 99],
    "time_range": {"start": "2026-01-15 08:30:00", "end": "2026-01-15 08:40:00"},
}, indent=2)

_STAGE2_BAD_A = json.dumps({"error": "no data"}, indent=2)

_STAGE3_GOOD_A = json.dumps({
    "report": "Log Analysis Report",
    "period": "2026-01-15 08:30:00 to 2026-01-15 08:40:00",
    "summary": {
        "total_entries": 11,
        "info_count": 6,
        "warn_count": 3,
        "error_count": 2,
        "unique_users": 4,
    },
    "alerts": [
        {"type": "high_error_rate", "value": "18.2%", "threshold": "10%"},
        {"type": "disk_full", "severity": "critical"},
    ],
}, indent=2)

_STAGE3_BAD_A = json.dumps({"report": "incomplete"}, indent=2)

_STAGE4_GOOD_A = json.dumps({
    "validation": "PASS",
    "checks": {
        "structure_valid": True,
        "all_stages_present": True,
        "acceptance_tests_passed": 5,
        "total_tests": 5,
    },
}, indent=2)

_STAGE4_BAD_A = json.dumps({
    "validation": "FAIL",
    "checks": {
        "structure_valid": True,
        "all_stages_present": False,
        "acceptance_tests_passed": 2,
        "total_tests": 5,
    },
}, indent=2)


# ─── Task B: CSV Analysis ──────────────────────────────────────────────

_CSV_CLEAN = """id,name,age,score,department
1,Alice,30,95.5,Engineering
2,Bob,25,82.3,Marketing
3,Charlie,35,91.0,Engineering
4,Diana,28,78.9,Sales
5,Eve,32,88.7,Engineering
6,Frank,40,65.2,Sales
7,Grace,22,97.1,Marketing
8,Henry,45,73.4,Engineering
9,Ivy,29,85.6,Sales
10,Jack,38,92.3,Marketing
"""

_CSV_DRIFT = """id,full_name,age,score,team
1,Alice,30,95.5,Engineering
2,Bob,25,82.3,Marketing
3,Charlie,35,91.0,Engineering
4,Diana,28,78.9,Sales
5,Eve,32,88.7,Engineering
6,Frank,40,65.2,Sales
7,Grace,22,97.1,Marketing
8,Henry,45,73.4,Engineering
9,Ivy,29,85.6,Sales
10,Jack,38,92.3,Marketing
"""

_STAGE1_GOOD_B = json.dumps([
    {"id": 1, "name": "Alice", "age": 30, "score": 95.5, "department": "Engineering"},
    {"id": 2, "name": "Bob", "age": 25, "score": 82.3, "department": "Marketing"},
    {"id": 3, "name": "Charlie", "age": 35, "score": 91.0, "department": "Engineering"},
    {"id": 4, "name": "Diana", "age": 28, "score": 78.9, "department": "Sales"},
    {"id": 5, "name": "Eve", "age": 32, "score": 88.7, "department": "Engineering"},
    {"id": 6, "name": "Frank", "age": 40, "score": 65.2, "department": "Sales"},
    {"id": 7, "name": "Grace", "age": 22, "score": 97.1, "department": "Marketing"},
    {"id": 8, "name": "Henry", "age": 45, "score": 73.4, "department": "Engineering"},
    {"id": 9, "name": "Ivy", "age": 29, "score": 85.6, "department": "Sales"},
    {"id": 10, "name": "Jack", "age": 38, "score": 92.3, "department": "Marketing"},
], indent=2)

_STAGE1_BAD_B = json.dumps([{"bad": "csv_data"}], indent=2)

_STAGE2_GOOD_B = json.dumps({
    "total_records": 10,
    "score_stats": {"mean": 84.99, "min": 65.2, "max": 97.1, "std": 9.87},
    "anomalies": [
        {"id": 6, "name": "Frank", "score": 65.2, "reason": "below threshold (mean - 2*std)"},
    ],
    "by_department": {
        "Engineering": {"count": 4, "mean_score": 87.15},
        "Marketing": {"count": 3, "mean_score": 90.57},
        "Sales": {"count": 3, "mean_score": 76.57},
    },
}, indent=2)

_STAGE2_BAD_B = json.dumps({"error": "parse failed"}, indent=2)

_STAGE3_GOOD_B = json.dumps({
    "report": "CSV Anomaly Detection Report",
    "dataset": "employee_scores",
    "records_analyzed": 10,
    "anomalies_found": 1,
    "anomaly_details": [
        {"id": 6, "name": "Frank", "score": 65.2, "department": "Sales", "z_score": -2.01},
    ],
    "recommendation": "Review Frank's score for data entry error",
}, indent=2)

_STAGE3_BAD_B = json.dumps({"report": "no anomalies found"}, indent=2)

_STAGE4_GOOD_B = json.dumps({
    "validation": "PASS",
    "checks": {
        "structure_valid": True,
        "all_stages_present": True,
        "acceptance_tests_passed": 5,
        "total_tests": 5,
    },
}, indent=2)

_STAGE4_BAD_B = json.dumps({
    "validation": "FAIL",
    "checks": {
        "structure_valid": True,
        "all_stages_present": False,
        "acceptance_tests_passed": 2,
        "total_tests": 5,
    },
}, indent=2)


# ─── Task C: JSON Config Normalization ──────────────────────────────────

_CONFIG_CLEAN = json.dumps({
    "app": "myapp",
    "version": "2.1.0",
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "mydb",
        "user": "admin",
        "pool_size": 10,
    },
    "logging": {
        "level": "INFO",
        "file": "/var/log/myapp.log",
        "max_size_mb": 100,
    },
    "features": {
        "auth": {"enabled": True, "provider": "oauth2"},
        "cache": {"enabled": True, "ttl_seconds": 300},
        "analytics": {"enabled": False},
    },
}, indent=2)

_CONFIG_DRIFT = json.dumps({
    "application": "myapp",
    "version": "2.1.0",
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "mydb",
        "user": "admin",
        "pool_size": 10,
    },
    "logging": {
        "level": "INFO",
        "file": "/var/log/myapp.log",
        "max_size_mb": 100,
    },
    "features": {
        "auth": {"enabled": True, "provider": "oauth2"},
        "cache": {"enabled": True, "ttl_seconds": 300},
        "analytics": {"enabled": False},
    },
}, indent=2)

_STAGE1_GOOD_C = json.dumps({
    "app_name": "myapp",
    "version": "2.1.0",
    "database_host": "localhost",
    "database_port": 5432,
    "database_name": "mydb",
    "database_user": "admin",
    "database_pool_size": 10,
    "log_level": "INFO",
    "log_file": "/var/log/myapp.log",
    "log_max_size_mb": 100,
    "auth_enabled": True,
    "auth_provider": "oauth2",
    "cache_enabled": True,
    "cache_ttl_seconds": 300,
    "analytics_enabled": False,
}, indent=2)

_STAGE1_BAD_C = json.dumps({"error": "invalid config"}, indent=2)

_STAGE2_GOOD_C = json.dumps({
    "app_name": "myapp",
    "version": "2.1.0",
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "mydb",
        "user": "admin",
        "pool_size": 10,
    },
    "logging": {
        "level": "INFO",
        "file": "/var/log/myapp.log",
        "max_size_mb": 100,
    },
    "features": {
        "auth": {"enabled": True, "provider": "oauth2"},
        "cache": {"enabled": True, "ttl_seconds": 300},
        "analytics": {"enabled": False},
    },
    "validation": {
        "all_required_keys_present": True,
        "port_in_range": True,
        "pool_size_positive": True,
        "log_level_valid": True,
    },
}, indent=2)

_STAGE2_BAD_C = json.dumps({"validation": {"all_required_keys_present": False}}, indent=2)

_STAGE3_GOOD_C = json.dumps({
    "app_name": "myapp",
    "version": "2.1.0",
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "mydb",
        "user": "admin",
        "pool_size": 10,
    },
    "logging": {
        "level": "INFO",
        "file": "/var/log/myapp.log",
        "max_size_mb": 100,
    },
    "features": {
        "auth": {"enabled": True, "provider": "oauth2"},
        "cache": {"enabled": True, "ttl_seconds": 300},
        "analytics": {"enabled": False},
    },
    "normalized": True,
    "normalization_timestamp": "2026-07-14T00:00:00Z",
}, indent=2)

_STAGE3_BAD_C = json.dumps({"normalized": False}, indent=2)

_STAGE4_GOOD_C = json.dumps({
    "validation": "PASS",
    "checks": {
        "structure_valid": True,
        "all_stages_present": True,
        "acceptance_tests_passed": 5,
        "total_tests": 5,
    },
}, indent=2)

_STAGE4_BAD_C = json.dumps({
    "validation": "FAIL",
    "checks": {
        "structure_valid": True,
        "all_stages_present": False,
        "acceptance_tests_passed": 2,
        "total_tests": 5,
    },
}, indent=2)


# ─── Fixture definitions ────────────────────────────────────────────────

def _stage_tests_a() -> dict[int, list[dict]]:
    return {
        1: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_records_key", "params": {"key": "timestamp"}},
            {"name": "has_records_key", "params": {"key": "level"}},
            {"name": "has_records_key", "params": {"key": "message"}},
            {"name": "correct_record_count", "params": {"expected": 11}},
        ],
        2: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "total_entries"}},
            {"name": "has_key", "params": {"key": "by_level"}},
            {"name": "has_key", "params": {"key": "unique_users"}},
            {"name": "total_entries_matches", "params": {"expected": 11}},
        ],
        3: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "report"}},
            {"name": "has_key", "params": {"key": "summary"}},
            {"name": "has_key", "params": {"key": "alerts"}},
            {"name": "summary_has_key", "params": {"key": "total_entries"}},
        ],
        4: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "validation"}},
            {"name": "has_key", "params": {"key": "checks"}},
            {"name": "validation_is_pass", "params": {}},
            {"name": "all_checks_present", "params": {"expected_keys": ["structure_valid", "all_stages_present", "acceptance_tests_passed", "total_tests"]}},
            {"name": "acceptance_tests_match", "params": {}},
        ],
    }


def _stage_tests_b() -> dict[int, list[dict]]:
    return {
        1: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_records_key", "params": {"key": "id"}},
            {"name": "has_records_key", "params": {"key": "name"}},
            {"name": "has_records_key", "params": {"key": "score"}},
            {"name": "correct_record_count", "params": {"expected": 10}},
        ],
        2: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "total_records"}},
            {"name": "has_key", "params": {"key": "score_stats"}},
            {"name": "has_key", "params": {"key": "anomalies"}},
            {"name": "total_records_matches", "params": {"expected": 10}},
        ],
        3: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "report"}},
            {"name": "has_key", "params": {"key": "anomalies_found"}},
            {"name": "has_key", "params": {"key": "anomaly_details"}},
            {"name": "anomalies_found_positive", "params": {}},
        ],
        4: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "validation"}},
            {"name": "has_key", "params": {"key": "checks"}},
            {"name": "validation_is_pass", "params": {}},
            {"name": "all_checks_present", "params": {"expected_keys": ["structure_valid", "all_stages_present", "acceptance_tests_passed", "total_tests"]}},
            {"name": "acceptance_tests_match", "params": {}},
        ],
    }


def _stage_tests_c() -> dict[int, list[dict]]:
    return {
        1: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "app_name"}},
            {"name": "has_key", "params": {"key": "version"}},
            {"name": "has_key", "params": {"key": "database_host"}},
            {"name": "has_key", "params": {"key": "database_port"}},
        ],
        2: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "validation"}},
            {"name": "has_key", "params": {"key": "database"}},
            {"name": "has_key", "params": {"key": "logging"}},
            {"name": "has_key", "params": {"key": "features"}},
        ],
        3: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "app_name"}},
            {"name": "has_key", "params": {"key": "normalized"}},
            {"name": "has_key", "params": {"key": "database"}},
            {"name": "normalized_is_true", "params": {}},
        ],
        4: [
            {"name": "is_valid_json", "params": {}},
            {"name": "has_key", "params": {"key": "validation"}},
            {"name": "has_key", "params": {"key": "checks"}},
            {"name": "validation_is_pass", "params": {}},
            {"name": "all_checks_present", "params": {"expected_keys": ["structure_valid", "all_stages_present", "acceptance_tests_passed", "total_tests"]}},
            {"name": "acceptance_tests_match", "params": {}},
        ],
    }


# ─── Public API ─────────────────────────────────────────────────────────

FIXTURES: dict[tuple[str, str], FixtureSet] = {}


def _build_fixtures() -> None:
    """Build all fixture sets."""
    # Task A
    FIXTURES[("A", "clean")] = FixtureSet(
        task=Task.A,
        condition=Condition.CLEAN,
        input_files={"app.log": _LOG_LINES_CLEAN},
        stage_artifacts={
            1: StageArtifact(path="stage_1/records.json", content=_STAGE1_GOOD_A, sha256=_sha256(_STAGE1_GOOD_A)),
            2: StageArtifact(path="stage_2/stats.json", content=_STAGE2_GOOD_A, sha256=_sha256(_STAGE2_GOOD_A)),
            3: StageArtifact(path="stage_3/report.json", content=_STAGE3_GOOD_A, sha256=_sha256(_STAGE3_GOOD_A)),
            4: StageArtifact(path="stage_4/validation.json", content=_STAGE4_GOOD_A, sha256=_sha256(_STAGE4_GOOD_A)),
        },
        bad_artifacts={
            1: _STAGE1_BAD_A,
            2: _STAGE2_BAD_A,
            3: _STAGE3_BAD_A,
            4: _STAGE4_BAD_A,
        },
        stage_tests=_stage_tests_a(),
    )
    FIXTURES[("A", "drift")] = FixtureSet(
        task=Task.A,
        condition=Condition.DRIFT,
        input_files={"app.log": _LOG_LINES_DRIFT},
        stage_artifacts={
            1: StageArtifact(path="stage_1/records.json", content=_STAGE1_GOOD_A, sha256=_sha256(_STAGE1_GOOD_A)),
            2: StageArtifact(path="stage_2/stats.json", content=_STAGE2_GOOD_A, sha256=_sha256(_STAGE2_GOOD_A)),
            3: StageArtifact(path="stage_3/report.json", content=_STAGE3_GOOD_A, sha256=_sha256(_STAGE3_GOOD_A)),
            4: StageArtifact(path="stage_4/validation.json", content=_STAGE4_GOOD_A, sha256=_sha256(_STAGE4_GOOD_A)),
        },
        bad_artifacts={
            1: _STAGE1_BAD_A,
            2: _STAGE2_BAD_A,
            3: _STAGE3_BAD_A,
            4: _STAGE4_BAD_A,
        },
        stage_tests=_stage_tests_a(),
    )

    # Task B
    FIXTURES[("B", "clean")] = FixtureSet(
        task=Task.B,
        condition=Condition.CLEAN,
        input_files={"data.csv": _CSV_CLEAN},
        stage_artifacts={
            1: StageArtifact(path="stage_1/records.json", content=_STAGE1_GOOD_B, sha256=_sha256(_STAGE1_GOOD_B)),
            2: StageArtifact(path="stage_2/stats.json", content=_STAGE2_GOOD_B, sha256=_sha256(_STAGE2_GOOD_B)),
            3: StageArtifact(path="stage_3/report.json", content=_STAGE3_GOOD_B, sha256=_sha256(_STAGE3_GOOD_B)),
            4: StageArtifact(path="stage_4/validation.json", content=_STAGE4_GOOD_B, sha256=_sha256(_STAGE4_GOOD_B)),
        },
        bad_artifacts={
            1: _STAGE1_BAD_B,
            2: _STAGE2_BAD_B,
            3: _STAGE3_BAD_B,
            4: _STAGE4_BAD_B,
        },
        stage_tests=_stage_tests_b(),
    )
    FIXTURES[("B", "drift")] = FixtureSet(
        task=Task.B,
        condition=Condition.DRIFT,
        input_files={"data.csv": _CSV_DRIFT},
        stage_artifacts={
            1: StageArtifact(path="stage_1/records.json", content=_STAGE1_GOOD_B, sha256=_sha256(_STAGE1_GOOD_B)),
            2: StageArtifact(path="stage_2/stats.json", content=_STAGE2_GOOD_B, sha256=_sha256(_STAGE2_GOOD_B)),
            3: StageArtifact(path="stage_3/report.json", content=_STAGE3_GOOD_B, sha256=_sha256(_STAGE3_GOOD_B)),
            4: StageArtifact(path="stage_4/validation.json", content=_STAGE4_GOOD_B, sha256=_sha256(_STAGE4_GOOD_B)),
        },
        bad_artifacts={
            1: _STAGE1_BAD_B,
            2: _STAGE2_BAD_B,
            3: _STAGE3_BAD_B,
            4: _STAGE4_BAD_B,
        },
        stage_tests=_stage_tests_b(),
    )

    # Task C
    FIXTURES[("C", "clean")] = FixtureSet(
        task=Task.C,
        condition=Condition.CLEAN,
        input_files={"config.json": _CONFIG_CLEAN},
        stage_artifacts={
            1: StageArtifact(path="stage_1/parsed.json", content=_STAGE1_GOOD_C, sha256=_sha256(_STAGE1_GOOD_C)),
            2: StageArtifact(path="stage_2/validated.json", content=_STAGE2_GOOD_C, sha256=_sha256(_STAGE2_GOOD_C)),
            3: StageArtifact(path="stage_3/normalized.json", content=_STAGE3_GOOD_C, sha256=_sha256(_STAGE3_GOOD_C)),
            4: StageArtifact(path="stage_4/validation.json", content=_STAGE4_GOOD_C, sha256=_sha256(_STAGE4_GOOD_C)),
        },
        bad_artifacts={
            1: _STAGE1_BAD_C,
            2: _STAGE2_BAD_C,
            3: _STAGE3_BAD_C,
            4: _STAGE4_BAD_C,
        },
        stage_tests=_stage_tests_c(),
    )
    FIXTURES[("C", "drift")] = FixtureSet(
        task=Task.C,
        condition=Condition.DRIFT,
        input_files={"config.json": _CONFIG_DRIFT},
        stage_artifacts={
            1: StageArtifact(path="stage_1/parsed.json", content=_STAGE1_GOOD_C, sha256=_sha256(_STAGE1_GOOD_C)),
            2: StageArtifact(path="stage_2/validated.json", content=_STAGE2_GOOD_C, sha256=_sha256(_STAGE2_GOOD_C)),
            3: StageArtifact(path="stage_3/normalized.json", content=_STAGE3_GOOD_C, sha256=_sha256(_STAGE3_GOOD_C)),
            4: StageArtifact(path="stage_4/validation.json", content=_STAGE4_GOOD_C, sha256=_sha256(_STAGE4_GOOD_C)),
        },
        bad_artifacts={
            1: _STAGE1_BAD_C,
            2: _STAGE2_BAD_C,
            3: _STAGE3_BAD_C,
            4: _STAGE4_BAD_C,
        },
        stage_tests=_stage_tests_c(),
    )


_build_fixtures()


def get_fixture(task: Task | str, condition: Condition | str) -> FixtureSet:
    """Get a fixture set by task and condition."""
    t = task.value if isinstance(task, Task) else task
    c = condition.value if isinstance(condition, Condition) else condition
    key = (t, c)
    if key not in FIXTURES:
        raise KeyError(f"No fixture for task={t}, condition={c}")
    return FIXTURES[key]


def all_fixtures() -> list[tuple[str, str, FixtureSet]]:
    """Return all (task, condition, fixture) tuples."""
    return [(t, c, f) for (t, c), f in FIXTURES.items()]


def drift_differs_from_clean(task: Task | str) -> bool:
    """Check whether drift input files differ from clean for a given task."""
    t = task.value if isinstance(task, Task) else task
    clean = get_fixture(t, "clean")
    drift = get_fixture(t, "drift")
    for key in clean.input_files:
        if clean.input_files.get(key) != drift.input_files.get(key):
            return True
    return False
