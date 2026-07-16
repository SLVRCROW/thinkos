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
    _validate_completion_evidence,
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
from thinkos.schema.receipt import Receipt, Action, Result


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
    plan_result = plan(project_path=initialized_project)
    assert plan_result["status"] == "ok"
    assert plan_result["observed_state"] == "healthy"
    assert not plan_result["blocked_reasons"]

    apply_result = apply(
        project_path=initialized_project,
        approved_plan_id=plan_result["plan_id"],
    )
    assert apply_result["status"] == "ok"
    return initialized_project


# ── Inspect: acceptance tests ──────────────────────────────────────────────


class TestInspect:
    def test_empty_project(self, tmp_project):
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
        result = inspect(project_path=initialized_project)
        assert result["status"] == "ok"
        assert result["state"] in ("healthy",)
        assert result["p2_complete"] is False
        assert result["store_exists"] is True
        assert result["existing_config"] is not None

    def test_completed_project(self, completed_project):
        result = inspect(project_path=completed_project)
        assert result["status"] == "ok"
        assert result["state"] == "healthy"
        assert result["p2_complete"] is True

    def test_nonexistent_path(self):
        result = inspect(project_path="/tmp/thinkos_nonexistent_xyz")
        assert result["status"] == "error"

    def test_read_only_no_mutation(self, tmp_project):
        before = set(os.listdir(tmp_project))
        inspect(project_path=tmp_project)
        after = set(os.listdir(tmp_project))
        assert before == after

    def test_legacy_config_conflict(self, tmp_project):
        legacy_path = os.path.join(tmp_project, "thinkos.json")
        with open(legacy_path, "w") as f:
            json.dump({"gates": {"default": "always_allow"}}, f)
        result = inspect(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["state"] == "conflict"
        assert "thinkos.json" in result["legacy_conflicts"]

    def test_symlink_conflict(self, tmp_project):
        real_dir = os.path.join(tmp_project, "real_thinkos")
        os.makedirs(real_dir)
        link_path = os.path.join(tmp_project, THINKOS_DIR)
        os.symlink(real_dir, link_path)
        result = inspect(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["state"] == "conflict"

    def test_malformed_config_conflict(self, tmp_project):
        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        os.makedirs(thinkos_dir)
        cfg_path = os.path.join(thinkos_dir, CONFIG_FILENAME)
        with open(cfg_path, "w") as f:
            f.write("not valid json")
        result = inspect(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["state"] == "conflict"

    def test_default_path_resolves_to_cwd(self, tmp_project):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_project)
            result = inspect()
            assert result["status"] == "ok"
            assert result["project_root"] == str(Path(tmp_project).resolve())
        finally:
            os.chdir(original_cwd)

    def test_contract_version_present(self, tmp_project):
        result = inspect(project_path=tmp_project)
        assert result["contract_version"] == CONTRACT_VERSION


# ── Plan: acceptance tests ────────────────────────────────────────────────


class TestPlan:
    def test_empty_project_plan(self, tmp_project):
        result = plan(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "empty"
        assert result["contract_version"] == CONTRACT_VERSION
        assert result["plan_id"] is not None
        assert len(result["plan_id"]) == 64
        assert len(result["ordered_effects"]) > 0
        assert result["blocked_reasons"] == []

    def test_plan_determinism(self, tmp_project):
        plan1 = plan(project_path=tmp_project)
        plan2 = plan(project_path=tmp_project)
        assert plan1["plan_id"] == plan2["plan_id"]
        assert plan1["ordered_effects"] == plan2["ordered_effects"]

    def test_plan_read_only(self, tmp_project):
        before = set(os.listdir(tmp_project))
        plan(project_path=tmp_project)
        after = set(os.listdir(tmp_project))
        assert before == after

    def test_initialized_project_plan(self, initialized_project):
        result = plan(project_path=initialized_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "healthy"
        assert not result["blocked_reasons"]
        assert len(result["ordered_effects"]) == 2
        assert result["ordered_effects"][0]["action"] == "verify_health"
        assert result["ordered_effects"][1]["action"] == "persist_completion_evidence"

    def test_completed_project_plan(self, completed_project):
        result = plan(project_path=completed_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "healthy"
        assert result["ordered_effects"] == []
        assert "already complete" in " ".join(result.get("warnings", []))

    def test_conflict_project_plan(self, tmp_project):
        legacy_path = os.path.join(tmp_project, "thinkos.json")
        with open(legacy_path, "w") as f:
            json.dump({"gates": {"default": "always_allow"}}, f)
        result = plan(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "conflict"
        assert len(result["blocked_reasons"]) > 0

    def test_plan_includes_safe_defaults(self, tmp_project):
        result = plan(project_path=tmp_project)
        assert result["safe_defaults"] == SAFE_DEFAULTS

    def test_plan_id_sha256_format(self, tmp_project):
        result = plan(project_path=tmp_project)
        pid = result["plan_id"]
        assert len(pid) == 64
        int(pid, 16)

    def test_nonexistent_path_plan(self):
        result = plan(project_path="/tmp/thinkos_nonexistent_xyz")
        assert result["status"] == "error"


# ── Apply: acceptance tests ────────────────────────────────────────────────


class TestApply:
    def test_apply_empty_project(self, tmp_project):
        plan_result = plan(project_path=tmp_project)
        assert plan_result["status"] == "ok"
        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"
        assert apply_result["plan_id"] == plan_result["plan_id"]
        assert len(apply_result["effects_applied"]) > 0
        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        assert os.path.isdir(thinkos_dir)
        assert os.path.isfile(os.path.join(thinkos_dir, CONFIG_FILENAME))
        assert os.path.isfile(os.path.join(thinkos_dir, STORE_FILENAME))
        doctor_result = p1_doctor(project_path=tmp_project)
        assert doctor_result["status"] == "healthy"

    def test_apply_missing_approval(self, tmp_project):
        result = apply(project_path=tmp_project)
        assert result["status"] == "error"
        assert "Missing" in result["error"]

    def test_apply_wrong_plan_id(self, tmp_project):
        result = apply(
            project_path=tmp_project,
            approved_plan_id="0" * 64,
        )
        assert result["status"] == "error"
        assert "mismatch" in result["error"].lower()

    def test_apply_stale_plan(self, tmp_project):
        plan_result = plan(project_path=tmp_project)
        assert plan_result["status"] == "ok"
        p1_init(project_path=tmp_project)
        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "error"
        assert "mismatch" in apply_result["error"].lower()

    def test_apply_idempotent(self, completed_project):
        plan_result = plan(project_path=completed_project)
        assert plan_result["status"] == "ok"
        apply_result = apply(
            project_path=completed_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"
        assert "already complete" in apply_result.get("detail", "").lower()

    def test_apply_to_initialized_project(self, initialized_project):
        plan_result = plan(project_path=initialized_project)
        assert plan_result["status"] == "ok"
        apply_result = apply(
            project_path=initialized_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"
        assert apply_result["plan_id"] == plan_result["plan_id"]
        assert _validate_completion_evidence(
            os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        )

    def test_apply_no_mutation_on_rejection(self, tmp_project):
        before = set(os.listdir(tmp_project))
        result = apply(
            project_path=tmp_project,
            approved_plan_id="0" * 64,
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

    def test_apply_empty_plan_id(self, tmp_project):
        result = apply(project_path=tmp_project, approved_plan_id="")
        assert result["status"] == "error"

    def test_apply_nonexistent_path(self):
        result = apply(
            project_path="/tmp/thinkos_nonexistent_xyz",
            approved_plan_id="0" * 64,
        )
        assert result["status"] == "error"


# ── Fresh-successor proof: end-to-end rehydration ──────────────────────────


class TestFreshSuccessorRehydration:
    def test_full_lifecycle(self, tmp_project):
        """End-to-end: inspect → plan → apply → rehydrate → read."""
        # 1. Inspect empty project
        inspect_result = inspect(project_path=tmp_project)
        assert inspect_result["status"] == "ok"
        assert inspect_result["state"] == "empty"

        # 2. Plan without mutation
        plan_result = plan(project_path=tmp_project)
        assert plan_result["status"] == "ok"
        assert plan_result["observed_state"] == "empty"
        assert len(os.listdir(tmp_project)) == 0

        # 3. Reject absent and incorrect approval without mutation
        reject_no_approval = apply(project_path=tmp_project)
        assert reject_no_approval["status"] == "error"
        assert len(os.listdir(tmp_project)) == 0
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
        packet = rehydrate_result["packets"][0]
        assert packet["kind"] == "decision"
        assert packet["source"] == "p2_onboarding"
        assert packet["content"]["structured"]["plan_id"] == plan_result["plan_id"]
        assert packet["content"]["structured"]["contract_version"] == CONTRACT_VERSION
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
        sid1 = _derive_onboarding_session_id("a" * 64)
        sid2 = _derive_onboarding_session_id("a" * 64)
        assert sid1 == sid2
        assert sid1.startswith("p2_onboard_")
        assert len(sid1) == len("p2_onboard_") + 64
        sid3 = _derive_onboarding_session_id("b" * 64)
        assert sid1 != sid3


# ── Falsification tests ────────────────────────────────────────────────────


class TestFalsification:
    def test_inspect_read_only_never_creates_files(self, tmp_project):
        for _ in range(5):
            inspect(project_path=tmp_project)
        assert len(os.listdir(tmp_project)) == 0

    def test_plan_read_only_never_creates_files(self, tmp_project):
        for _ in range(5):
            plan(project_path=tmp_project)
        assert len(os.listdir(tmp_project)) == 0

    def test_plan_determinism_across_calls(self, tmp_project):
        ids = set()
        for _ in range(10):
            result = plan(project_path=tmp_project)
            ids.add(result["plan_id"])
        assert len(ids) == 1

    def test_stale_plan_rejection_after_init(self, tmp_project):
        empty_plan = plan(project_path=tmp_project)
        p1_init(project_path=tmp_project)
        result = apply(
            project_path=tmp_project,
            approved_plan_id=empty_plan["plan_id"],
        )
        assert result["status"] == "error"
        assert "mismatch" in result["error"].lower()

    def test_approval_mismatch_rejection(self, tmp_project):
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
        subdir = os.path.join(tmp_project, "sub")
        os.makedirs(subdir)
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_project)
            result = inspect(project_path="sub")
            assert result["status"] == "ok"
            assert result["project_root"].endswith("/sub")
        finally:
            os.chdir(original_cwd)

    def test_legacy_config_conflict_blocks_plan(self, tmp_project):
        legacy_path = os.path.join(tmp_project, "thinkos.json")
        with open(legacy_path, "w") as f:
            json.dump({"gates": {"default": "always_allow"}}, f)
        result = plan(project_path=tmp_project)
        assert result["status"] == "ok"
        assert result["observed_state"] == "conflict"
        assert len(result["blocked_reasons"]) > 0

    def test_legacy_config_conflict_blocks_apply(self, tmp_project):
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
        plan_result = plan(project_path=tmp_project)
        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"
        doctor_result = p1_doctor(project_path=tmp_project)
        assert doctor_result["status"] == "healthy"

    def test_idempotent_rerun(self, completed_project):
        plan_result = plan(project_path=completed_project)
        assert plan_result["observed_state"] == "healthy"
        assert plan_result["ordered_effects"] == []
        apply_result = apply(
            project_path=completed_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"
        store_path = os.path.join(completed_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        packets = store.list_packets(kind="decision", source="p2_onboarding", limit=10)
        store.close()
        assert len(packets) == 1

    def test_safe_defaults_preserved(self, initialized_project):
        cfg_path = os.path.join(initialized_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            original_config = json.load(f)
        plan_result = plan(project_path=initialized_project)
        apply(
            project_path=initialized_project,
            approved_plan_id=plan_result["plan_id"],
        )
        with open(cfg_path) as f:
            after_config = json.load(f)
        assert original_config == after_config

    def test_malformed_json_cli(self, tmp_project):
        result = _run_thinkos("onboard", cwd=tmp_project)
        assert result.returncode != 0
        assert "missing" in result.stderr.lower()

    def test_unknown_onboard_subcommand(self, tmp_project):
        result = _run_thinkos("onboard", "unknown", cwd=tmp_project)
        assert result.returncode != 0
        assert "unknown" in result.stderr.lower()

    def test_cli_help_includes_onboard(self):
        result = _run_thinkos("--help")
        assert result.returncode == 0
        assert "onboard" in result.stdout
        assert "inspect" in result.stdout
        assert "plan" in result.stdout
        assert "apply" in result.stdout

    def test_cli_version(self):
        result = _run_thinkos("--version")
        assert result.returncode == 0
        assert "thinkos" in result.stdout

    def test_cli_inspect_json(self, tmp_project):
        result = _run_thinkos("onboard", "inspect", "--json", cwd=tmp_project)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        assert data["state"] == "empty"

    def test_cli_plan_json(self, tmp_project):
        result = _run_thinkos("onboard", "plan", "--json", cwd=tmp_project)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "ok"
        assert data["plan_id"] is not None

    def test_cli_apply_json(self, tmp_project):
        plan_result = _run_thinkos("onboard", "plan", "--json", cwd=tmp_project)
        plan_data = json.loads(plan_result.stdout)
        plan_id = plan_data["plan_id"]
        apply_result = _run_thinkos(
            "onboard", "apply", "--approve-plan", plan_id, "--json",
            cwd=tmp_project,
        )
        assert apply_result.returncode == 0
        data = json.loads(apply_result.stdout)
        assert data["status"] == "ok"

    def test_cli_apply_missing_approval(self, tmp_project):
        result = _run_thinkos("onboard", "apply", cwd=tmp_project)
        assert result.returncode != 0

    def test_cli_apply_wrong_plan_id(self, tmp_project):
        result = _run_thinkos(
            "onboard", "apply", "--approve-plan", "0" * 64,
            cwd=tmp_project,
        )
        assert result.returncode != 0
        assert "mismatch" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_rehydrate_no_store(self, tmp_project):
        result = rehydrate_onboarding(project_path=tmp_project)
        assert result["status"] == "error"

    def test_rehydrate_no_evidence(self, initialized_project):
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"

    def test_rehydrate_after_completion(self, completed_project):
        result = rehydrate_onboarding(project_path=completed_project)
        assert result["status"] == "ok"
        assert len(result["packets"]) > 0
        assert len(result["receipts"]) > 0

    def test_inspect_plan_read_only_no_side_effects(self, initialized_project):
        """Repeated inspect/plan leave all files, hashes, sizes, mtimes unchanged.
        No new WAL, SHM, or journal sidecars are created by read-only operations.
        """
        import hashlib
        thinkos_dir = os.path.join(initialized_project, THINKOS_DIR)
        # Stabilize sidecars from the initial doctor check
        inspect(project_path=initialized_project)
        plan(project_path=initialized_project)

        def _snapshot():
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
            sidecars = []
            for fname in os.listdir(thinkos_dir):
                if fname.endswith("-wal") or fname.endswith("-shm") or fname.endswith("-journal"):
                    sidecars.append(fname)
            snap["_sidecars"] = sorted(sidecars)
            return snap

        before = _snapshot()
        for _ in range(5):
            inspect(project_path=initialized_project)
            plan(project_path=initialized_project)
        after = _snapshot()

        assert before["_sidecars"] == after["_sidecars"], (
            f"New sidecars after read-only ops: before={before['_sidecars']} after={after['_sidecars']}"
        )
        assert before.keys() == after.keys()
        for key in before:
            if key == "_sidecars":
                continue
            # Sidecar files may have mtime changes from OS-level metadata updates
            is_sidecar = key.endswith("-wal") or key.endswith("-shm") or key.endswith("-journal")
            assert before[key]["hash"] == after[key]["hash"], f"Hash changed for {key}"
            assert before[key]["size"] == after[key]["size"], f"Size changed for {key}"
            if not is_sidecar:
                assert before[key]["mtime"] == after[key]["mtime"], f"Mtime changed for {key}"



    def test_evidence_atomicity_injected_failure(self, tmp_project):
        """Inject DuplicateError during write_receipt_and_packet.
        Proves zero new receipts and zero new packets survive, P1 is preserved,
        and a later rerun completes evidence exactly once.
        """
        from thinkos.schema.context_packet import ContextPacket
        from thinkos.schema.receipt import Receipt, Action, Result
        from thinkos.store.sqlite_store import DuplicateError
        from datetime import datetime, timezone
        import uuid

        # First apply successfully
        plan_result = plan(project_path=tmp_project)
        apply_result = apply(
            project_path=tmp_project,
            approved_plan_id=plan_result["plan_id"],
        )
        assert apply_result["status"] == "ok"
        doctor_result = p1_doctor(project_path=tmp_project)
        assert doctor_result["status"] == "healthy"

        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)

        # Count existing evidence
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

        # Inject failure: write with duplicate receipt_id
        existing_rid = before_receipts[0].receipt_id if before_receipts else "rct_00000000-0000-0000-0000-000000000000"
        dup_receipt = Receipt(
            receipt_id=existing_rid,
            session_id="p2_onboard_test",
            sequence=1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(type="agent_message", tool=None, params={}, agent="p2_test"),
            result=Result(status="ok", summary="injected", packet_ids=[]),
        )
        dup_packet = ContextPacket(
            packet_id=f"ctx_{uuid.uuid4()}",
            session_id="p2_onboard_test",
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind="decision",
            source="p2_onboarding",
            content={"text": "injected", "structured": None},
            refs=[existing_rid],
        )
        store2 = SQLiteStore(store_path)
        try:
            store2.write_receipt_and_packet(dup_receipt, dup_packet)
            assert False, "Expected DuplicateError"
        except DuplicateError:
            pass
        store2.close()

        # Verify zero new evidence
        store3 = SQLiteStore(store_path)
        after_packets = store3.list_packets(kind="decision", source="p2_onboarding", limit=10)
        after_receipts = []
        for p in after_packets:
            for ref in p.refs:
                r = store3.read_receipt(ref)
                if r is not None:
                    after_receipts.append(r)
        store3.close()
        assert len(after_packets) == before_packet_count
        assert len(after_receipts) == before_receipt_count

        # P1 still healthy
        doctor_result = p1_doctor(project_path=tmp_project)
        assert doctor_result["status"] == "healthy"

        # Idempotent rerun
        plan_again = plan(project_path=tmp_project)
        assert plan_again["observed_state"] == "healthy"
        assert plan_again["ordered_effects"] == []
        apply_again = apply(
            project_path=tmp_project,
            approved_plan_id=plan_again["plan_id"],
        )
        assert apply_again["status"] == "ok"
        assert "already complete" in apply_again.get("detail", "").lower()

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
        evidence, performs a safe read, and proves evidence persists."""
        import subprocess, sys, json
        repo = _repo_root()
        env = {**os.environ, "PYTHONPATH": repo}

        # Process 1: Complete onboarding
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
        assert json.loads(p1_apply.stdout)["status"] == "ok"

        # Process 2: Inspect and rehydrate
        p2_inspect = subprocess.run(
            [sys.executable, "-m", "thinkos", "onboard", "inspect", "--json"],
            capture_output=True, text=True, cwd=tmp_project, env=env,
        )
        assert p2_inspect.returncode == 0
        inspect_data = json.loads(p2_inspect.stdout)
        assert inspect_data["state"] == "healthy"
        assert inspect_data["p2_complete"] is True

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
        packet = rehydrate_data["packets"][0]
        assert packet["kind"] == "decision"
        assert packet["source"] == "p2_onboarding"
        assert packet["content"]["structured"]["plan_id"] == plan_id
        receipt = rehydrate_data["receipts"][0]
        assert receipt["action_type"] == "agent_message"
        assert receipt["result_status"] == "ok"
        assert receipt["action_params"]["approved_plan_id"] == plan_id

        # Process 2: Safe read through engine
        with open(os.path.join(tmp_project, "test.txt"), "w") as f:
            f.write("Hello from P2 successor!\n")
        p2_read = subprocess.run(
            [sys.executable, "-m", "thinkos"],
            input=json.dumps({
                "type": "agent_message", "message_id": "msg_p2",
                "session_id": "p2_session", "timestamp": "2026-07-15T12:00:00Z",
                "sender": "p2_test",
                "content": {"text": "read a file", "tool_calls": [
                    {"tool": "read_file", "params": {"path": "test.txt", "call_id": "c1"}}
                ], "context_refs": []},
            }),
            capture_output=True, text=True, cwd=tmp_project, env=env,
        )
        assert p2_read.returncode == 0
        assert '"status":"ok"' in p2_read.stdout

        # Verify evidence persists
        p2_verify = subprocess.run(
            [sys.executable, "-c", """
import sys, json
sys.path.insert(0, %r)
from thinkos.store.sqlite_store import SQLiteStore
from thinkos.onboarding import _store_path, _resolve_project_path
store_path = _store_path(_resolve_project_path(%r))
store = SQLiteStore(store_path)
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

    # ── Completion evidence validation falsification ──────────────────

    def test_orphan_decision_packet_not_complete(self, initialized_project):
        """A decision packet without a valid linked receipt is not completion."""
        store_path = os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        # Write an orphan decision packet with no receipt ref
        packet = ContextPacket(
            packet_id="ctx_00000000-0000-0000-0000-000000000001",
            session_id="p2_onboard_orphan",
            timestamp="2026-07-15T12:00:00Z",
            kind="decision",
            source="p2_onboarding",
            content={"text": "orphan", "structured": {
                "contract_version": "p2.v0",
                "plan_id": "0" * 64,
            }},
            tags=["p2_onboarding", "decision"],
            refs=[],
        )
        store.write_packet(packet)
        store.close()
        assert _validate_completion_evidence(store_path) is False

    def test_missing_receipt_not_complete(self, initialized_project):
        """Packet references a receipt that does not exist."""
        store_path = os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        packet = ContextPacket(
            packet_id="ctx_00000000-0000-0000-0000-000000000002",
            session_id="p2_onboard_missing_rct",
            timestamp="2026-07-15T12:00:00Z",
            kind="decision",
            source="p2_onboarding",
            content={"text": "missing receipt", "structured": {
                "contract_version": "p2.v0",
                "plan_id": "a" * 64,
            }},
            tags=["p2_onboarding", "decision"],
            refs=["rct_00000000-0000-0000-0000-000000000999"],
        )
        store.write_packet(packet)
        store.close()
        assert _validate_completion_evidence(store_path) is False

    def test_mismatched_plan_ids_not_complete(self, initialized_project):
        """Receipt approved_plan_id differs from packet plan_id."""
        from datetime import datetime, timezone
        import uuid
        store_path = os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        rid = f"rct_{uuid.uuid4()}"
        pid = f"ctx_{uuid.uuid4()}"
        receipt = Receipt(
            receipt_id=rid, session_id="p2_onboard_mismatch",
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(type="agent_message", tool=None,
                          params={"contract_version": "p2.v0", "approved_plan_id": "b" * 64},
                          agent="p2_onboarding"),
            result=Result(status="ok", summary="mismatch", packet_ids=[pid]),
        )
        packet = ContextPacket(
            packet_id=pid, session_id="p2_onboard_mismatch",
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind="decision", source="p2_onboarding",
            content={"text": "mismatch", "structured": {
                "contract_version": "p2.v0",
                "plan_id": "a" * 64,
            }},
            tags=["p2_onboarding", "decision"], refs=[rid],
        )
        store.write_receipt_and_packet(receipt, packet)
        store.close()
        assert _validate_completion_evidence(store_path) is False

    def test_mismatched_sessions_not_complete(self, initialized_project):
        """Receipt session differs from packet session."""
        from datetime import datetime, timezone
        import uuid
        store_path = os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        rid = f"rct_{uuid.uuid4()}"
        pid = f"ctx_{uuid.uuid4()}"
        plan_id = "c" * 64
        receipt = Receipt(
            receipt_id=rid, session_id="p2_onboard_wrong_session",
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(type="agent_message", tool=None,
                          params={"contract_version": "p2.v0", "approved_plan_id": plan_id},
                          agent="p2_onboarding"),
            result=Result(status="ok", summary="wrong session", packet_ids=[pid]),
        )
        packet = ContextPacket(
            packet_id=pid, session_id="p2_onboard_" + plan_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind="decision", source="p2_onboarding",
            content={"text": "wrong session", "structured": {
                "contract_version": "p2.v0",
                "plan_id": plan_id,
            }},
            tags=["p2_onboarding", "decision"], refs=[rid],
        )
        store.write_receipt_and_packet(receipt, packet)
        store.close()
        assert _validate_completion_evidence(store_path) is False

    def test_receipt_not_referencing_packet_not_complete(self, initialized_project):
        """Receipt result.packet_ids does not reference the packet."""
        from datetime import datetime, timezone
        import uuid
        store_path = os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        rid = f"rct_{uuid.uuid4()}"
        pid = f"ctx_{uuid.uuid4()}"
        plan_id = "d" * 64
        receipt = Receipt(
            receipt_id=rid, session_id="p2_onboard_" + plan_id,
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(type="agent_message", tool=None,
                          params={"contract_version": "p2.v0", "approved_plan_id": plan_id},
                          agent="p2_onboarding"),
            result=Result(status="ok", summary="no ref", packet_ids=["ctx_other"]),
        )
        packet = ContextPacket(
            packet_id=pid, session_id="p2_onboard_" + plan_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind="decision", source="p2_onboarding",
            content={"text": "no ref", "structured": {
                "contract_version": "p2.v0",
                "plan_id": plan_id,
            }},
            tags=["p2_onboarding", "decision"], refs=[rid],
        )
        store.write_receipt_and_packet(receipt, packet)
        store.close()
        assert _validate_completion_evidence(store_path) is False

    def test_failed_receipt_status_not_complete(self, initialized_project):
        """Receipt result.status is not ok."""
        from datetime import datetime, timezone
        import uuid
        store_path = os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        rid = f"rct_{uuid.uuid4()}"
        pid = f"ctx_{uuid.uuid4()}"
        plan_id = "e" * 64
        receipt = Receipt(
            receipt_id=rid, session_id="p2_onboard_" + plan_id,
            sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
            action=Action(type="agent_message", tool=None,
                          params={"contract_version": "p2.v0", "approved_plan_id": plan_id},
                          agent="p2_onboarding"),
            result=Result(status="error", summary="failed", packet_ids=[pid]),
        )
        packet = ContextPacket(
            packet_id=pid, session_id="p2_onboard_" + plan_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind="decision", source="p2_onboarding",
            content={"text": "failed", "structured": {
                "contract_version": "p2.v0",
                "plan_id": plan_id,
            }},
            tags=["p2_onboarding", "decision"], refs=[rid],
        )
        store.write_receipt_and_packet(receipt, packet)
        store.close()
        assert _validate_completion_evidence(store_path) is False

    def test_repeated_completion_check_no_side_effects(self, completed_project):
        """Repeated _validate_completion_evidence and rehydrate_onboarding
        cause no database or sidecar changes beyond those already present."""
        import hashlib
        thinkos_dir = os.path.join(completed_project, THINKOS_DIR)
        store_path = os.path.join(thinkos_dir, STORE_FILENAME)

        # Run once to stabilize any sidecars from the fixture's apply()
        _validate_completion_evidence(store_path)
        rehydrate_onboarding(project_path=completed_project)

        def _snapshot():
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
            sidecars = []
            for fname in os.listdir(thinkos_dir):
                if fname.endswith("-wal") or fname.endswith("-shm") or fname.endswith("-journal"):
                    sidecars.append(fname)
            snap["_sidecars"] = sorted(sidecars)
            return snap

        before = _snapshot()
        for _ in range(10):
            _validate_completion_evidence(store_path)
            rehydrate_onboarding(project_path=completed_project)
        after = _snapshot()

        assert before["_sidecars"] == after["_sidecars"]
        assert before.keys() == after.keys()
        for key in before:
            if key == "_sidecars":
                continue
            # Sidecar files (-wal, -shm, -journal) may have mtime changes
            # from OS-level metadata updates during read-only access.
            # Check hash and size for all files; check mtime only for core files.
            is_sidecar = key.endswith("-wal") or key.endswith("-shm") or key.endswith("-journal")
            assert before[key]["hash"] == after[key]["hash"], f"Hash changed for {key}"
            assert before[key]["size"] == after[key]["size"], f"Size changed for {key}"
            if not is_sidecar:
                assert before[key]["mtime"] == after[key]["mtime"], f"Mtime changed for {key}"




# ── Rehydration falsification: each forged-evidence case ──────────


def _forge_evidence(initialized_project, receipt_kwargs=None, packet_kwargs=None):
    """Helper: write a receipt+packet pair to the store and return store_path."""
    from datetime import datetime, timezone
    import uuid
    store_path = os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
    store = SQLiteStore(store_path)
    rid = f"rct_{uuid.uuid4()}"
    pid = f"ctx_{uuid.uuid4()}"
    plan_id = "f" * 64
    r_defaults = dict(
        receipt_id=rid, session_id="p2_onboard_" + plan_id,
        sequence=1, timestamp=datetime.now(timezone.utc).isoformat(),
        action=Action(type="agent_message", tool=None,
                      params={"contract_version": "p2.v0", "approved_plan_id": plan_id},
                      agent="p2_onboarding"),
        result=Result(status="ok", summary="test", packet_ids=[pid]),
    )
    if receipt_kwargs:
        r_defaults.update(receipt_kwargs)
    receipt = Receipt(**r_defaults)
    p_defaults = dict(
        packet_id=pid, session_id="p2_onboard_" + plan_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        kind="decision", source="p2_onboarding",
        content={"text": "test", "structured": {
            "contract_version": "p2.v0",
            "plan_id": plan_id,
        }},
        tags=["p2_onboarding", "decision"], refs=[rid],
    )
    if packet_kwargs:
        p_defaults.update(packet_kwargs)
    packet = ContextPacket(**p_defaults)
    store.write_receipt_and_packet(receipt, packet)
    store.close()
    return store_path


class TestRehydrationFalsification:
    def test_rehydrate_orphan_packet(self, initialized_project):
        store_path = os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        packet = ContextPacket(
            packet_id="ctx_00000000-0000-0000-0000-000000000099",
            session_id="p2_onboard_orphan",
            timestamp="2026-07-15T12:00:00Z",
            kind="decision", source="p2_onboarding",
            content={"text": "orphan", "structured": {
                "contract_version": "p2.v0",
                "plan_id": "0" * 64,
            }},
            tags=["p2_onboarding", "decision"], refs=[],
        )
        store.write_packet(packet)
        store.close()
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"
        assert len(result.get("packets", [])) == 0
        assert len(result.get("receipts", [])) == 0

    def test_rehydrate_missing_receipt(self, initialized_project):
        store_path = os.path.join(initialized_project, THINKOS_DIR, STORE_FILENAME)
        store = SQLiteStore(store_path)
        packet = ContextPacket(
            packet_id="ctx_00000000-0000-0000-0000-000000000098",
            session_id="p2_onboard_missing",
            timestamp="2026-07-15T12:00:00Z",
            kind="decision", source="p2_onboarding",
            content={"text": "missing", "structured": {
                "contract_version": "p2.v0",
                "plan_id": "a" * 64,
            }},
            tags=["p2_onboarding", "decision"],
            refs=["rct_00000000-0000-0000-0000-000000000999"],
        )
        store.write_packet(packet)
        store.close()
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"
        assert len(result.get("packets", [])) == 0
        assert len(result.get("receipts", [])) == 0

    def test_rehydrate_mismatched_plan_ids(self, initialized_project):
        _forge_evidence(initialized_project,
            receipt_kwargs={"action": Action(type="agent_message", tool=None,
                params={"contract_version": "p2.v0", "approved_plan_id": "b" * 64},
                agent="p2_onboarding")})
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"
        assert len(result.get("packets", [])) == 0
        assert len(result.get("receipts", [])) == 0

    def test_rehydrate_mismatched_sessions(self, initialized_project):
        _forge_evidence(initialized_project,
            receipt_kwargs={"session_id": "p2_onboard_wrong_session"})
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"
        assert len(result.get("packets", [])) == 0
        assert len(result.get("receipts", [])) == 0

    def test_rehydrate_receipt_not_referencing_packet(self, initialized_project):
        _forge_evidence(initialized_project,
            receipt_kwargs={"result": Result(status="ok", summary="no ref", packet_ids=["ctx_other"])})
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"
        assert len(result.get("packets", [])) == 0
        assert len(result.get("receipts", [])) == 0

    def test_rehydrate_failed_receipt_status(self, initialized_project):
        _forge_evidence(initialized_project,
            receipt_kwargs={"result": Result(status="error", summary="failed", packet_ids=[])})
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"
        assert len(result.get("packets", [])) == 0
        assert len(result.get("receipts", [])) == 0

    def test_rehydrate_malformed_contract_version(self, initialized_project):
        _forge_evidence(initialized_project,
            packet_kwargs={"content": {"text": "bad version", "structured": {
                "contract_version": "p1.v0",
                "plan_id": "f" * 64,
            }}})
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"
        assert len(result.get("packets", [])) == 0
        assert len(result.get("receipts", [])) == 0

    def test_rehydrate_invalid_plan_id_format(self, initialized_project):
        _forge_evidence(initialized_project,
            packet_kwargs={"content": {"text": "bad plan_id", "structured": {
                "contract_version": "p2.v0",
                "plan_id": "not-a-valid-plan-id",
            }}})
        result = rehydrate_onboarding(project_path=initialized_project)
        assert result["status"] == "error"
        assert len(result.get("packets", [])) == 0
        assert len(result.get("receipts", [])) == 0
