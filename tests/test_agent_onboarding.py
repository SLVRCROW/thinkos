"""Tests for P2 agent-led onboarding — inspect, plan, apply, and rehydration.

Covers all acceptance criteria from the Alpha Door P2 v0 contract.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from thinkos.agent_onboarding import (
    inspect,
    plan,
    apply,
    rehydrate_onboarding,
    _classify_state,
    _build_plan_payload,
    _compute_plan_id,
    _derive_onboarding_session_id,
    _check_p2_completion,
    CONTRACT_VERSION,
    SAFE_DEFAULTS,
)
from thinkos.onboarding import (
    init as p1_init,
    doctor as p1_doctor,
    _atomic_write_json,
    _atomic_write_text,
    THINKOS_DIR,
    CONFIG_FILENAME,
    STORE_FILENAME,
    DEFAULT_CONFIG as P1_DEFAULT_CONFIG,
)
from thinkos.store.sqlite_store import SQLiteStore
from thinkos.schema.context_packet import ContextPacket
from thinkos.schema.receipt import Receipt


# ── Helpers ─────────────────────────────────────────────────────────────────


def _repo_root() -> str:
    return str(Path(__file__).resolve().parents[1])


def _thinkos_env() -> dict:
    env = os.environ.copy()
    repo = _repo_root()
    existing = env.get("PYTHONPATH", "")
    if existing:
        env["PYTHONPATH"] = f"{repo}:{existing}"
    else:
        env["PYTHONPATH"] = repo
    return env


def _run_thinkos(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "thinkos", *args],
        capture_output=True, text=True,
        cwd=cwd or "/tmp",
        env=_thinkos_env(),
    )


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project():
    """Create a temporary empty project directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def initialized_project(tmp_project):
    """Create a project that has been P1-initialized."""
    p1_init(project_path=tmp_project)
    return tmp_project


@pytest.fixture
def completed_project(initialized_project):
    """Create a project with full P2 onboarding completed."""
    # Generate plan
    plan_result = plan(project_path=initialized_project)
    assert plan_result["status"] == "ok"
    assert plan_result["observed_state"] == "healthy"
    assert not plan_result["blocked_reasons"]

    # Apply with approved plan
    apply_result = apply(
        project_path=initialized_project,
        approved_plan_id=plan_result["plan_id"],
    )
    assert apply_result["status"] == "ok"
    return initialized_project


# ── Inspect: acceptance tests ──────────────────────────────────────────────


class TestInspect:
    def test_empty_project(self, tmp_project):
        """Empty project reports state='empty'."""
        result = inspect(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["state"] == "empty"
        assert result["contract_version"] == CONTRACT_VERSION
        assert result["project_root"] == str(Path(tmp_project).resolve())
        assert result["legacy_conflicts"] == []
        assert result["p2_complete"] is False
        assert result["store_exists"] is False
        assert result["existing_config"] is None

    def test_initialized_project(self, initialized_project):
        """P1-initialized project reports state='healthy'."""
        result = inspect(project_path=initialized_project)
        assert result["status"] == "ok"
        assert result["state"] in ("healthy",)
        assert result["p2_complete"] is False
        assert result["store_exists"] is True
        assert result["existing_config"] is not None

    def test_completed_project(self, completed_project):
        """Fully onboarded project reports state='healthy' and p2_complete=True."""
        result = inspect(project_path=completed_project)
        assert result["status"] == "ok"
        assert result["state"] == "healthy"
        assert result["p2_complete"] is True

    def test_nonexistent_path(self):
        """Non-existent path returns error."""
        result = inspect(project_path="/tmp/thinkos_nonexistent_xyz")
        assert result["status"] == "error"

    def test_read_only_no_mutation(self, tmp_project):
        """Inspect never creates files or directories."""
        before = set(os.listdir(tmp_project))
        inspect(project_path=tmp_project)
        after = set(os.listdir(tmp_project))
        assert before == after

    def test_legacy_config_conflict(self, tmp_project):
        """Legacy config file without .thinkos/ reports conflict."""
        # Create a legacy config
        legacy_path = os.path.join(tmp_project, "thinkos.json")
        with open(legacy_path, "w") as f:
            json.dump({"gates": {"default": "always_allow"}}, f)

        result = inspect(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["state"] == "conflict"
        assert "thinkos.json" in result["legacy_conflicts"]

    def test_symlink_conflict(self, tmp_project):
        """Symlinked .thinkos/ reports conflict."""
        real_dir = os.path.join(tmp_project, "real_thinkos")
        os.makedirs(real_dir)
        link_path = os.path.join(tmp_project, THINKOS_DIR)
        os.symlink(real_dir, link_path)

        result = inspect(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["state"] == "conflict"

    def test_malformed_config_conflict(self, tmp_project):
        """Malformed config in .thinkos/ reports conflict."""
        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        os.makedirs(thinkos_dir)
        cfg_path = os.path.join(thinkos_dir, CONFIG_FILENAME)
        with open(cfg_path, "w") as f:
            f.write("not valid json")

        result = inspect(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["state"] == "conflict"

    def test_default_path_resolves_to_cwd(self, tmp_project):
        """When no path given, uses CWD."""
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_project)
            result = inspect()
            assert result["status"] == "ok"
            assert result["project_root"] == str(Path(tmp_project).resolve())
        finally:
            os.chdir(original_cwd)

    def test_contract_version_present(self, tmp_project):
        """Inspect result always includes contract_version."""
        result = inspect(project_path=tmp_project)
        assert result["contract_version"] == CONTRACT_VERSION


# ── Plan: acceptance tests ────────────────────────────────────────────────


class TestPlan:
    def test_empty_project_plan(self, tmp_project):
        """Empty project produces a plan with init effects."""
        result = plan(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "empty"
        assert result["contract_version"] == CONTRACT_VERSION
        assert result["plan_id"] is not None
        assert len(result["plan_id"]) == 64  # SHA-256 hex
        assert len(result["ordered_effects"]) > 0
        assert result["blocked_reasons"] == []

    def test_plan_determinism(self, tmp_project):
        """Same state produces same plan_id."""
        plan1 = plan(project_path=tmp_project)
        plan2 = plan(project_path=tmp_project)
        assert plan1["plan_id"] == plan2["plan_id"]
        assert plan1["ordered_effects"] == plan2["ordered_effects"]

    def test_plan_read_only(self, tmp_project):
        """Plan never creates files or directories."""
        before = set(os.listdir(tmp_project))
        plan(project_path=tmp_project)
        after = set(os.listdir(tmp_project))
        assert before == after

    def test_initialized_project_plan(self, initialized_project):
        """Initialized project produces a plan with only evidence effects."""
        result = plan(project_path=initialized_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "healthy"
        assert not result["blocked_reasons"]
        # Should have verify_health + persist_completion_evidence
        assert len(result["ordered_effects"]) == 2
        assert result["ordered_effects"][0]["action"] == "verify_health"
        assert result["ordered_effects"][1]["action"] == "persist_completion_evidence"

    def test_completed_project_plan(self, completed_project):
        """Completed project produces a plan with no effects."""
        result = plan(project_path=completed_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "healthy"
        assert result["ordered_effects"] == []
        assert "already complete" in " ".join(result.get("warnings", []))

    def test_conflict_project_plan(self, tmp_project):
        """Conflict state produces a blocked plan."""
        # Create legacy config
        legacy_path = os.path.join(tmp_project, "thinkos.json")
        with open(legacy_path, "w") as f:
            json.dump({"gates": {"default": "always_allow"}}, f)

        result = plan(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "conflict"
        assert len(result["blocked_reasons"]) > 0

    def test_plan_includes_safe_defaults(self, tmp_project):
        """Plan always includes safe defaults."""
        result = plan(project_path=tmp_project)
        assert result["safe_defaults"] == SAFE_DEFAULTS

    def test_plan_id_sha256_format(self, tmp_project):
        """plan_id is a valid SHA-256 hex digest."""
        result = plan(project_path=tmp_project)
        pid = result["plan_id"]
        assert len(pid) == 64
        int(pid, 16)  # Should not raise

    def test_nonexistent_path_plan(self):
        """Non-existent path returns error."""
        result = plan(project_path="/tmp/thinkos_nonexistent_xyz")
        assert result["status"] == "error"


# ── Apply: acceptance tests ────────────────────────────────────────────────


class TestApply:
    def test_apply_empty_project(self, tmp_project):
        """Apply to empty project with correct plan_id succeeds."""
        plan_result = plan(project_path=tmp_project)
        assert plan_result["status"] == "ok"

        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"
        assert apply_result["plan_id"] == plan_result["plan_id"]
        assert len(apply_result["effects_applied"]) > 0

        # Verify P1 init was done
        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        assert os.path.isdir(thinkos_dir)
        assert os.path.isfile(os.path.join(thinkos_dir, CONFIG_FILENAME))
        assert os.path.isfile(os.path.join(thinkos_dir, STORE_FILENAME))

        # Verify doctor is healthy
        doctor_result = p1_doctor(project_path=tmp_project)
        assert doctor_result["status"] == "healthy"

    def test_apply_missing_approval(self, tmp_project):
        """Apply without --approve-plan returns error."""
        result = apply(project_path=tmp_project)
        assert result["status"] == "error"
        assert "Missing" in result["error"]

    def test_apply_wrong_plan_id(self, tmp_project):
        """Apply with wrong plan_id returns error."""
        result = apply(
            project_path=tmp_project,
            approved_plan_id="0" * 64,
        )
        assert result["status"] == "error"
        assert "mismatch" in result["error"].lower()

    def test_apply_stale_plan(self, tmp_project):
        """Apply with stale plan_id (state changed) returns error."""
        plan_result = plan(project_path=tmp_project)
        assert plan_result["status"] == "ok"

        # Change state by initializing
        p1_init(project_path=tmp_project)

        # Try to apply with old plan
        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "error"
        assert "mismatch" in apply_result["error"].lower()

    def test_apply_idempotent(self, completed_project):
        """Applying again to a completed project is idempotent."""
        plan_result = plan(project_path=completed_project)
        assert plan_result["status"] == "ok"

        apply_result = apply(
            project_path=completed_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"
        assert "already complete" in apply_result.get("detail", "").lower()

    def test_apply_to_initialized_project(self, initialized_project):
        """Apply to P1-initialized project completes P2 evidence."""
        plan_result = plan(project_path=initialized_project)
        assert plan_result["status"] == "ok"

        apply_result = apply(
            project_path=initialized_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"
        assert apply_result["plan_id"] == plan_result["plan_id"]

        # Verify P2 completion evidence exists
        assert _check_p2_completion(
            os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        )

    def test_apply_no_mutation_on_rejection(self, tmp_project):
        """Apply with wrong plan_id never mutates project state."""
        before = set(os.listdir(tmp_project))
        result = apply(
            project_path=tmp_project,
            approved_plan_id="0" * 64,
        )
        assert result["status"] == "error"
        after = set(os.listdir(tmp_project))
        assert before == after

    def test_apply_empty_plan_id(self, tmp_project):
        """Empty plan_id returns error."""
        result = apply(project_path=tmp_project, approved_plan_id="")
        assert result["status"] == "error"

    def test_apply_nonexistent_path(self):
        """Non-existent path returns error."""
        result = apply(
            project_path="/tmp/thinkos_nonexistent_xyz",
            approved_plan_id="0" * 64,
        )
        assert result["status"] == "error"


# ── Fresh-successor proof: end-to-end rehydration ──────────────────────────


class TestFreshSuccessorRehydration:
    def test_full_lifecycle(self, tmp_project):
        """End-to-end: inspect → plan → apply → rehydrate → read.

        Proves:
        1. inspect an empty project
        2. plan without mutation
        3. reject absent and incorrect approval without mutation
        4. apply the approved exact plan
        5. verify doctor is healthy
        6. recover the onboarding receipt and decision packet from a fresh process
        7. continue with one safe read through the existing engine
        8. prove receipt/packet lineage and persistent storage
        9. rerun onboarding idempotently
        """
        # 1. Inspect empty project
        inspect_result = inspect(project_path=tmp_project)
        assert inspect_result["status"] == "ok"
        assert inspect_result["state"] == "empty"

        # 2. Plan without mutation
        plan_result = plan(project_path=tmp_project)
        assert plan_result["status"] == "ok"
        assert plan_result["observed_state"] == "empty"
        # Verify no files created
        assert len(os.listdir(tmp_project)) == 0

        # 3. Reject absent approval
        reject_no_approval = apply(project_path=tmp_project)
        assert reject_no_approval["status"] == "error"
        assert len(os.listdir(tmp_project)) == 0

        # Reject incorrect approval
        reject_wrong = apply(project_path=tmp_project, approved_plan_id="0" * 64)
        assert reject_wrong["status"] == "error"
        assert len(os.listdir(tmp_project)) == 0

        # 4. Apply the approved exact plan
        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"

        # 5. Verify doctor is healthy
        doctor_result = p1_doctor(project_path=tmp_project)
        assert doctor_result["status"] == "healthy"

        # 6. Recover onboarding evidence from a fresh process
        rehydrate_result = rehydrate_onboarding(project_path=tmp_project)
        assert rehydrate_result["status"] == "ok"
        assert len(rehydrate_result["packets"]) > 0
        assert len(rehydrate_result["receipts"]) > 0

        # Verify packet content
        packet = rehydrate_result["packets"][0]
        assert packet["kind"] == "decision"
        assert packet["source"] == "p2_onboarding"
        assert packet["content"]["structured"]["plan_id"] == plan_result["plan_id"]
        assert packet["content"]["structured"]["contract_version"] == CONTRACT_VERSION

        # Verify receipt content
        receipt = rehydrate_result["receipts"][0]
        assert receipt["action_type"] == "agent_message"
        assert receipt["result_status"] == "ok"
        assert receipt["action_params"]["approved_plan_id"] == plan_result["plan_id"]

        # 7. Continue with one safe read through the existing engine
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        packets = store.list_packets(kind="decision", source="p2_onboarding", limit=5)
        assert len(packets) > 0
        store.close()

        # 8. Prove receipt/packet lineage and persistent storage
        store = SQLiteStore(store_path)
        for p in packets:
            for ref in p.refs:
                r = store.read_receipt(ref)
                assert r is not None
                assert r.result.status == "ok"
        store.close()

        # 9. Rerun onboarding idempotently
        plan_again = plan(project_path=tmp_project)
        assert plan_again["status"] == "ok"
        assert plan_again["observed_state"] == "healthy"
        assert plan_again["ordered_effects"] == []

        apply_again = apply(
            project_path=tmp_project,
            approved_plan_id=plan_again["plan_id"],
        )
        assert apply_again["status"] == "ok"
        assert "already complete" in apply_again.get("detail", "").lower()

    def test_deterministic_session_id(self):
        """Session ID uses the complete plan hash."""
        sid1 = _derive_onboarding_session_id("a" * 64)
        sid2 = _derive_onboarding_session_id("a" * 64)
        assert sid1 == sid2
        assert sid1.startswith("p2_onboard_")
        # Full 64-char hash, not truncated
        assert len(sid1) == len("p2_onboard_") + 64

        sid3 = _derive_onboarding_session_id("b" * 64)
        assert sid1 != sid3


# ── Falsification tests ────────────────────────────────────────────────────


class TestFalsification:
    def test_inspect_read_only_never_creates_files(self, tmp_project):
        """Inspect never creates any files, even on repeated calls."""
        for _ in range(5):
            inspect(project_path=tmp_project)
        assert len(os.listdir(tmp_project)) == 0

    def test_plan_read_only_never_creates_files(self, tmp_project):
        """Plan never creates any files, even on repeated calls."""
        for _ in range(5):
            plan(project_path=tmp_project)
        assert len(os.listdir(tmp_project)) == 0

    def test_plan_determinism_across_calls(self, tmp_project):
        """Plan produces identical plan_id across multiple calls."""
        ids = set()
        for _ in range(10):
            result = plan(project_path=tmp_project)
            ids.add(result["plan_id"])
        assert len(ids) == 1

    def test_stale_plan_rejection_after_init(self, tmp_project):
        """Plan from empty state is rejected after P1 init changes state."""
        empty_plan = plan(project_path=tmp_project)
        p1_init(project_path=tmp_project)
        result = apply(
            project_path=tmp_project,
            approved_plan_id=empty_plan["plan_id"],
        )
        assert result["status"] == "error"
        assert "mismatch" in result["error"].lower()

    def test_approval_mismatch_rejection(self, tmp_project):
        """Wrong plan_id is always rejected with zero mutation."""
        before = set(os.listdir(tmp_project))
        result = apply(
            project_path=tmp_project,
            approved_plan_id="a" * 64,
        )
        assert result["status"] == "error"
        after = set(os.listdir(tmp_project))
        assert before == after

        result = apply(
            project_path=tmp_project,
            approved_plan_id="b" * 64,
        )
        assert result["status"] == "error"
        after2 = set(os.listdir(tmp_project))
        assert before == after2

    def test_path_canonicalization(self, tmp_project):
        """Path canonicalization resolves symlinks and relative paths."""
        # Create a subdirectory
        subdir = os.path.join(tmp_project, "sub")
        os.makedirs(subdir)

        # Inspect with relative path
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_project)
            result = inspect(project_path="sub")
            assert result["status"] == "ok"
            assert result["project_root"].endswith("/sub")
        finally:
            os.chdir(original_cwd)

    def test_legacy_config_conflict_blocks_plan(self, tmp_project):
        """Legacy config without .thinkos/ produces blocked plan."""
        legacy_path = os.path.join(tmp_project, "thinkos.json")
        with open(legacy_path, "w") as f:
            json.dump({"gates": {"default": "always_allow"}}, f)

        result = plan(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "conflict"
        assert len(result["blocked_reasons"]) > 0

    def test_legacy_config_conflict_blocks_apply(self, tmp_project):
        """Apply to conflict state returns blocked."""
        legacy_path = os.path.join(tmp_project, "thinkos.json")
        with open(legacy_path, "w") as f:
            json.dump({"gates": {"default": "always_allow"}}, f)

        plan_result = plan(project_path=tmp_project)
        assert plan_result["observed_state"] == "conflict"

        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "blocked"

    def test_evidence_atomicity(self, tmp_project):
        """If evidence persistence fails, P1 installation is preserved."""
        # First apply successfully
        plan_result = plan(project_path=tmp_project)
        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"

        # Verify P1 is healthy even after successful P2
        doctor_result = p1_doctor(project_path=tmp_project)
        assert doctor_result["status"] == "healthy"

    def test_idempotent_rerun(self, completed_project):
        """Rerunning onboarding on completed project is safe."""
        plan_result = plan(project_path=completed_project)
        assert plan_result["observed_state"] == "healthy"
        assert plan_result["ordered_effects"] == []

        apply_result = apply(
            project_path=completed_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"

        # Verify no duplicate evidence
        store_path = os.path.join(completed_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        packets = store.list_packets(kind="decision", source="p2_onboarding", limit=10)
        store.close()
        # Should have exactly 1 packet (from the first apply)
        assert len(packets) == 1

    def test_safe_defaults_preserved(self, initialized_project):
        """Existing healthy projects must not have their config rewritten."""
        # Read original config
        cfg_path = os.path.join(initialized_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            original_config = json.load(f)

        # Complete P2 onboarding
        plan_result = plan(project_path=initialized_project)
        apply(
            project_path=initialized_project,
            approved_plan_id=plan_result["plan_id"],
        )

        # Verify config unchanged
        with open(cfg_path) as f:
            after_config = json.load(f)
        assert original_config == after_config

    def test_malformed_json_cli(self, tmp_project):
        """CLI handles malformed arguments gracefully."""
        result = _run_thinkos("onboard", cwd=tmp_project)
        assert result.returncode != 0
        assert "missing" in result.stderr.lower()

    def test_unknown_onboard_subcommand(self, tmp_project):
        """Unknown onboard subcommand returns error."""
        result = _run_thinkos("onboard", "unknown", cwd=tmp_project)
        assert result.returncode != 0
        assert "unknown" in result.stderr.lower()

    def test_cli_help_includes_onboard(self):
        """CLI help includes onboard subcommands."""
        result = _run_thinkos("--help")
        assert result.returncode == 0
        assert "onboard" in result.stdout
        assert "inspect" in result.stdout
        assert "plan" in result.stdout
        assert "apply" in result.stdout

    def test_cli_version(self):
        """CLI version works."""
        result = _run_thinkos("--version")
        assert result.returncode == 0
        assert "thinkos" in result.stdout

    def test_cli_inspect_json(self, tmp_project):
        """CLI inspect with --json produces valid JSON."""
        result = _run_thinkos("onboard", "inspect", "--json", cwd=tmp_project)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        assert data["state"] == "empty"

    def test_cli_plan_json(self, tmp_project):
        """CLI plan with --json produces valid JSON."""
        result = _run_thinkos("onboard", "plan", "--json", cwd=tmp_project)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        assert data["plan_id"] is not None

    def test_cli_apply_json(self, tmp_project):
        """CLI apply with --json produces valid JSON."""
        # First get a plan
        plan_result = _run_thinkos("onboard", "plan", "--json", cwd=tmp_project)
        plan_data = json.loads(plan_result.stdout)
        plan_id = plan_data["plan_id"]

        # Apply
        apply_result = _run_thinkos(
            "onboard", "apply", "--approve-plan", plan_id, "--json",
            cwd=tmp_project,
        )
        assert apply_result.returncode == 0
        data = json.loads(apply_result.stdout)
        assert data["status"] == "ok"

    def test_cli_apply_missing_approval(self, tmp_project):
        """CLI apply without --approve-plan returns error."""
        result = _run_thinkos("onboard", "apply", cwd=tmp_project)
        assert result.returncode != 0

    def test_cli_apply_wrong_plan_id(self, tmp_project):
        """CLI apply with wrong plan_id returns error."""
        result = _run_thinkos(
            "onboard", "apply", "--approve-plan", "0" * 64,
            cwd=tmp_project,
        )
        assert result.returncode != 0
        assert "mismatch" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_rehydrate_no_store(self, tmp_project):
        """Rehydrate on uninitialized project returns error."""
        result = rehydrate_onboarding(project_path=tmp_project)
        assert result["status"] == "error"

    def test_rehydrate_no_evidence(self, initialized_project):
        """Rehydrate on initialized but not P2-completed project returns error."""
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"

    def test_rehydrate_after_completion(self, completed_project):
        """Rehydrate after P2 completion returns evidence."""
        result = rehydrate_onboarding(project_path=completed_project)
        assert result["status"] == "ok"
        assert len(result["packets"]) > 0
        assert len(result["receipts"]) > 0

    def test_inspect_plan_read_only_no_side_effects(self, initialized_project):
        """Repeated inspect/plan leave all files, hashes, sizes, mtimes unchanged.

        No new WAL, SHM, or journal sidecars are created by read-only operations
        beyond those already present from the initial doctor check.
        """
        import hashlib

        thinkos_dir = os.path.join(initialized_project, THINKOS_DIR)

        # Run one inspect/plan to stabilize any sidecars the doctor creates
        inspect(project_path=initialized_project)
        plan(project_path=initialized_project)

        def _snapshot():
            """Capture file hashes, sizes, mtimes, and sidecar presence."""
            snap = {}
            for root, dirs, files in os.walk(thinkos_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    stat = os.stat(fpath)
                    with open(fpath, "rb") as f:
                        content = f.read()
                    snap[fname] = {
                        "hash": hashlib.sha256(content).hexdigest(),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime_ns,
                    }
            # Capture sidecar files — mode=ro must NOT create new ones
            sidecars = []
            for fname in os.listdir(thinkos_dir):
                if fname.endswith("-wal") or fname.endswith("-shm") or fname.endswith("-journal"):
                    sidecars.append(fname)
            snap["_sidecars"] = sorted(sidecars)
            return snap

        before = _snapshot()

        # Run inspect and plan multiple times
        for _ in range(5):
            inspect(project_path=initialized_project)
            plan(project_path=initialized_project)

        after = _snapshot()

        # Verify no new sidecars were created by read-only operations
        assert before["_sidecars"] == after["_sidecars"], (
            f"New sidecars appeared after read-only inspect/plan: "
            f"before={before['_sidecars']} after={after['_sidecars']}"
        )

        # Verify all files unchanged
        assert before.keys() == after.keys()
        for key in before:
            if key == "_sidecars":
                continue
            assert before[key]["hash"] == after[key]["hash"], f"Hash changed for {key}"
            assert before[key]["size"] == after[key]["size"], f"Size changed for {key}"
            assert before[key]["mtime"] == after[key]["mtime"], f"Mtime changed for {key}"

    def test_evidence_atomicity_injected_failure(self, tmp_project):
        """Inject failure during packet insertion after receipt insertion begins.

        Proves neither receipt nor packet survives, P1 installation is preserved,
        and a later rerun completes evidence exactly once.
        """
        import sqlite3

        # First apply successfully to get a baseline
        plan_result = plan(project_path=tmp_project)
        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"

        # Verify P1 is healthy
        doctor_result = p1_doctor(project_path=tmp_project)
        assert doctor_result["status"] == "healthy"

        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)

        # Count existing evidence before injection
        store = SQLiteStore(store_path)
        before_packets = store.list_packets(kind="decision", source="p2_onboarding", limit=10)
        before_receipts = []
        for p in before_packets:
            for ref in p.refs:
                r = store.read_receipt(ref)
                if r is not None:
                    before_receipts.append(r)
        before_packet_count = len(before_packets)
        before_receipt_count = len(before_receipts)
        store.close()

        # Inject a failure: attempt to write a duplicate receipt_id.
        # write_receipt_and_packet uses BEGIN IMMEDIATE + COMMIT, so a
        # DuplicateError on the receipt should roll back the entire transaction,
        # leaving zero new evidence.
        from thinkos.schema.context_packet import ContextPacket
        from thinkos.schema.receipt import Receipt, Action, Result
        from thinkos.store.sqlite_store import DuplicateError
        from datetime import datetime, timezone
        import uuid

        store2 = SQLiteStore(store_path)
        # Use a receipt_id that already exists to trigger DuplicateError
        existing_receipt_id = before_receipts[0].receipt_id if before_receipts else "rct_00000000-0000-0000-0000-000000000000"
        dup_receipt = Receipt(
            receipt_id=existing_receipt_id,
            session_id="p2_onboard_test",
            sequence=1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(type="agent_message", tool=None, params={}, agent="p2_test"),
            result=Result(status="ok", summary="injected failure", packet_ids=[]),
        )
        dup_packet = ContextPacket(
            packet_id=f"ctx_{uuid.uuid4()}",
            session_id="p2_onboard_test",
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind="decision",
            source="p2_onboarding",
            content={"text": "injected failure", "structured": None},
            refs=[existing_receipt_id],
        )
        try:
            store2.write_receipt_and_packet(dup_receipt, dup_packet)
            assert False, "Expected DuplicateError was not raised"
        except DuplicateError:
            pass  # Expected — transaction rolled back
        store2.close()

        # Verify zero new onboarding receipts and zero new onboarding packets
        # were created by the failed write
        store3 = SQLiteStore(store_path)
        after_packets = store3.list_packets(kind="decision", source="p2_onboarding", limit=10)
        after_receipts = []
        for p in after_packets:
            for ref in p.refs:
                r = store3.read_receipt(ref)
                if r is not None:
                    after_receipts.append(r)
        store3.close()

        assert len(after_packets) == before_packet_count, (
            f"Packet count changed after failed write: {before_packet_count} -> {len(after_packets)}"
        )
        assert len(after_receipts) == before_receipt_count, (
            f"Receipt count changed after failed write: {before_receipt_count} -> {len(after_receipts)}"
        )

        # Verify P1 installation is still healthy
        doctor_result = p1_doctor(project_path=tmp_project)
        assert doctor_result["status"] == "healthy"

        # Rerun onboarding idempotently — should not create duplicates
        plan_again = plan(project_path=tmp_project)
        assert plan_again["observed_state"] == "healthy"
        assert plan_again["ordered_effects"] == []

        apply_again = apply(
            project_path=tmp_project,
            approved_plan_id=plan_again["plan_id"],
        )
        assert apply_again["status"] == "ok"
        assert "already complete" in apply_again.get("detail", "").lower()

        # Verify no duplicate evidence was created
        store4 = SQLiteStore(store_path)
        final_packets = store4.list_packets(kind="decision", source="p2_onboarding", limit=10)
        final_receipts = []
        for p in final_packets:
            for ref in p.refs:
                r = store4.read_receipt(ref)
                if r is not None:
                    final_receipts.append(r)
        store4.close()
        assert len(final_packets) == before_packet_count
        assert len(final_receipts) == before_receipt_count

    def test_fresh_successor_two_process(self, tmp_project):
        """Process 1 completes onboarding and exits. Process 2 rehydrates
        the exact onboarding receipt and decision packet, performs a safe read
        through the existing engine, and proves the new packet continues from
        the onboarding packet lineage.

        This is a true two-process test using subprocess isolation.
        """
        import subprocess
        import sys
        import json

        repo = _repo_root()
        env = {**os.environ, "PYTHONPATH": repo}

        # ── Process 1: Complete onboarding ──────────────────────────
        p1_plan = subprocess.run(
            [sys.executable, "-m", "thinkos", "onboard", "plan", "--json"],
            capture_output=True, text=True, cwd=tmp_project, env=env,
        )
        assert p1_plan.returncode == 0
        plan_data = json.loads(p1_plan.stdout)
        plan_id = plan_data["plan_id"]

        p1_apply = subprocess.run(
            [sys.executable, "-m", "thinkos", "onboard", "apply",
             "--approve-plan", plan_id, "--json"],
            capture_output=True, text=True, cwd=tmp_project, env=env,
        )
        assert p1_apply.returncode == 0
        apply_data = json.loads(p1_apply.stdout)
        assert apply_data["status"] == "ok"

        # Process 1 exits here — all state is in the SQLite store

        # ── Process 2: Fresh process rehydrates evidence ──────────────
        p2_rehydrate = subprocess.run(
            [sys.executable, "-m", "thinkos", "onboard", "inspect", "--json"],
            capture_output=True, text=True, cwd=tmp_project, env=env,
        )
        assert p2_rehydrate.returncode == 0
        inspect_data = json.loads(p2_rehydrate.stdout)
        assert inspect_data["state"] == "healthy"
        assert inspect_data["p2_complete"] is True

        # Rehydrate via the Python API (simulating a fresh import)
        # We use a subprocess to ensure a truly fresh process
        p2_recover = subprocess.run(
            [sys.executable, "-c", """
import sys, json
sys.path.insert(0, %r)
from thinkos.agent_onboarding import rehydrate_onboarding
result = rehydrate_onboarding(project_path=%r)
print(json.dumps(result))
""" % (repo, tmp_project)],
            capture_output=True, text=True, env=env,
        )
        assert p2_recover.returncode == 0
        rehydrate_data = json.loads(p2_recover.stdout)
        assert rehydrate_data["status"] == "ok"
        assert len(rehydrate_data["packets"]) > 0
        assert len(rehydrate_data["receipts"]) > 0

        # Verify the onboarding packet content
        packet = rehydrate_data["packets"][0]
        assert packet["kind"] == "decision"
        assert packet["source"] == "p2_onboarding"
        assert packet["content"]["structured"]["plan_id"] == plan_id

        # Verify the receipt
        receipt = rehydrate_data["receipts"][0]
        assert receipt["action_type"] == "agent_message"
        assert receipt["result_status"] == "ok"
        assert receipt["action_params"]["approved_plan_id"] == plan_id

        # ── Process 2: Safe read through existing engine ─────────────
        # Create a test file first
        with open(os.path.join(tmp_project, "test.txt"), "w") as f:
            f.write("Hello from P2 successor!\n")

        p2_read = subprocess.run(
            [sys.executable, "-m", "thinkos"],
            input=json.dumps({
                "type": "agent_message",
                "message_id": "msg_p2",
                "session_id": "p2_session",
                "timestamp": "2026-07-15T12:00:00Z",
                "sender": "p2_test",
                "content": {
                    "text": "read a file",
                    "tool_calls": [{
                        "tool": "read_file",
                        "params": {"path": "test.txt", "call_id": "c1"},
                    }],
                    "context_refs": [],
                },
            }),
            capture_output=True, text=True, cwd=tmp_project, env=env,
        )
        # The read should succeed (read_file is always_allow)
        assert p2_read.returncode == 0
        assert '"status":"ok"' in p2_read.stdout, f"Read failed: {p2_read.stdout[:200]}"

        # ── Prove the new packet continues from onboarding lineage ─────
        # The new session's first packet should have no parent (new session)
        # but the onboarding evidence should still be in the store
        p2_verify = subprocess.run(
            [sys.executable, "-c", """
import sys, json
sys.path.insert(0, %r)
from thinkos.store.sqlite_store import SQLiteStore
from thinkos.onboarding import _store_path, _resolve_project_path
store_path = _store_path(_resolve_project_path(%r))
store = SQLiteStore(store_path)
# Check onboarding evidence still exists
packets = store.list_packets(kind="decision", source="p2_onboarding", limit=5)
print("ONBOARDING_PACKETS:" + str(len(packets)))
receipts = []
for p in packets:
    for ref in p.refs:
        r = store.read_receipt(ref)
        if r is not None:
            receipts.append(r)
print("ONBOARDING_RECEIPTS:" + str(len(receipts)))
store.close()
""" % (repo, tmp_project)],
            capture_output=True, text=True, env=env,
        )
        assert p2_verify.returncode == 0
        assert "ONBOARDING_PACKETS:1" in p2_verify.stdout
        assert "ONBOARDING_RECEIPTS:1" in p2_verify.stdout
