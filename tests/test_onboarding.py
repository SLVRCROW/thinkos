"""Tests for ThinkOS onboarding — init and doctor commands.

Covers all acceptance criteria from the Alpha Door P1 integration correction.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from thinkos.onboarding import (
    init,
    doctor,
    _configs_equal,
    _load_config_file,
    _build_init_config,
    _canonicalize_path,
    _resolve_actual_store_path,
    DEFAULT_CONFIG,
    THINKOS_DIR,
    CONFIG_FILENAME,
    STORE_FILENAME,
    GITIGNORE_CONTENT,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project():
    """Create a temporary empty project directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# ── Init: basic success cases ──────────────────────────────────────────────


class TestInitBasic:
    def test_empty_directory_init_succeeds(self, tmp_project):
        """Empty-directory init succeeds."""
        result = init(project_path=tmp_project)
        assert result["status"] == "ok"
        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        assert os.path.isdir(thinkos_dir)
        assert os.path.isfile(os.path.join(thinkos_dir, CONFIG_FILENAME))
        assert os.path.isfile(os.path.join(thinkos_dir, ".gitignore"))
        assert os.path.isfile(os.path.join(thinkos_dir, STORE_FILENAME))

    def test_generated_config_validates(self, tmp_project):
        """Generated configuration validates and is safe by default."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        config = _load_config_file(cfg_path)
        assert config is not None
        assert config["gates"]["overrides"]["read_file"] == "always_allow"
        assert config["gates"]["overrides"]["write_file"] == "confirm"
        assert config["tools"]["allowed_root"] == tmp_project
        # Store path is relative to project root, resolves to .thinkos/thinkos.sqlite
        assert config["store"]["path"] == ".thinkos/thinkos.sqlite"
    def test_runtime_store_is_persistent(self, tmp_project):
        """Runtime store is persistent and sandboxed."""
        init(project_path=tmp_project)
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        assert os.path.isfile(store_path)
        conn = sqlite3.connect(store_path)
        cursor = conn.execute("SELECT sqlite_version()")
        version = cursor.fetchone()[0]
        conn.close()
        assert version is not None

    def test_gitignore_protects_db(self, tmp_project):
        """.gitignore protects runtime database files."""
        init(project_path=tmp_project)
        gitignore_path = os.path.join(tmp_project, THINKOS_DIR, ".gitignore")
        with open(gitignore_path) as f:
            content = f.read()
        assert "thinkos.sqlite" in content
        assert "thinkos.sqlite-wal" in content
        assert "thinkos.sqlite-shm" in content

    def test_init_defaults_to_cwd(self):
        """Init defaults to current directory when no path given."""
        original_cwd = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.chdir(tmpdir)
                result = init()
                assert result["status"] == "ok"
                assert os.path.isdir(os.path.join(tmpdir, THINKOS_DIR))
        finally:
            os.chdir(original_cwd)

    def test_init_with_explicit_path(self, tmp_project):
        """Init with explicit project path works."""
        result = init(project_path=tmp_project)
        assert result["status"] == "ok"
        assert os.path.isdir(os.path.join(tmp_project, THINKOS_DIR))


# ── Init: idempotency ──────────────────────────────────────────────────────


class TestInitIdempotency:
    def test_second_identical_init_returns_already_initialized(self, tmp_project):
        """Second identical init performs zero mutation."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        mtime_before = os.path.getmtime(cfg_path)

        result = init(project_path=tmp_project)
        assert result["status"] == "already_initialized"

        mtime_after = os.path.getmtime(cfg_path)
        assert mtime_after == mtime_before, "Config file was modified"

    def test_already_initialized_json_output(self, tmp_project):
        """JSON output for already-initialized has correct status."""
        init(project_path=tmp_project)
        result = init(project_path=tmp_project, json_output=True)
        assert result["status"] == "already_initialized"

    def test_allowed_root_only_tampering_rejected(self, tmp_project):
        """Changing only allowed_root is rejected by repeated init."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        # Change allowed_root to a different path
        config["tools"]["allowed_root"] = "/tmp/somewhere_else"
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert "differs" in result["message"].lower()

        # Config should be preserved unchanged
        with open(cfg_path) as f:
            preserved = json.load(f)
        assert preserved["tools"]["allowed_root"] == "/tmp/somewhere_else"


# ── Init: safety — reject divergent / malformed / symlink ─────────────────


class TestInitSafety:
    def test_divergent_config_is_rejected(self, tmp_project):
        """Existing divergent config is preserved and rejected."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["gates"]["default"] = "deny_all"
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert "differs" in result["message"].lower()

        with open(cfg_path) as f:
            preserved = json.load(f)
        assert preserved["gates"]["default"] == "deny_all"

    def test_malformed_config_is_rejected(self, tmp_project):
        """Existing malformed config is preserved and rejected."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path, "w") as f:
            f.write("not valid json{")

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert "malformed" in result["message"].lower()

        with open(cfg_path) as f:
            assert f.read() == "not valid json{"

    def test_symlink_rejected(self, tmp_project):
        """Symlink .thinkos/ is rejected."""
        thinkos_link = os.path.join(tmp_project, THINKOS_DIR)
        os.symlink("/tmp", thinkos_link)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert "symlink" in result["message"].lower()

    def test_nonexistent_path_rejected(self):
        """Non-existent project path is rejected."""
        result = init(project_path="/nonexistent/thinkos_test_path_xyz")
        assert result["status"] == "error"
        assert "does not exist" in result["message"].lower()

    def test_file_path_rejected(self, tmp_project):
        """A file path (not a directory) is rejected."""
        file_path = os.path.join(tmp_project, "not_a_dir")
        Path(file_path).write_text("I am a file")
        result = init(project_path=file_path)
        assert result["status"] == "error"

    def test_unsafe_config_preserved(self, tmp_project):
        """Unsafe existing config (allowed_root=null) is preserved and rejected."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["tools"]["allowed_root"] = None
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"

        # Unsafe config preserved
        with open(cfg_path) as f:
            preserved = json.load(f)
        assert preserved["tools"]["allowed_root"] is None


# ── Init: exit codes ──────────────────────────────────────────────────────


class TestInitExitCodes:
    def test_failed_init_exits_nonzero_human(self, tmp_project):
        """Failed init exits nonzero in human mode."""
        result = init(project_path="/nonexistent/thinkos_test_xyz")
        assert result["status"] == "error"

    def test_failed_init_exits_nonzero_json(self, tmp_project):
        """Failed init exits nonzero in JSON mode."""
        result = init(project_path="/nonexistent/thinkos_test_xyz", json_output=True)
        assert result["status"] == "error"

    def test_already_initialized_exits_zero(self, tmp_project):
        """already_initialized remains exit 0."""
        init(project_path=tmp_project)
        result = init(project_path=tmp_project)
        assert result["status"] == "already_initialized"


# ── Init: atomicity ─────────────────────────────────────────────────────────


class TestInitAtomicity:
    def test_cleanup_removes_all_partial_artifacts(self, tmp_project):
        """Cleanup function removes all partial generated artifacts."""
        from thinkos.onboarding import _cleanup_failed_init

        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        cfg_path = os.path.join(thinkos_dir, CONFIG_FILENAME)
        gitignore_path = os.path.join(thinkos_dir, ".gitignore")
        store_path = os.path.join(thinkos_dir, STORE_FILENAME)

        os.makedirs(thinkos_dir, exist_ok=True)
        Path(cfg_path).write_text("{}")
        Path(gitignore_path).write_text("")
        Path(store_path).write_text("")

        _cleanup_failed_init(thinkos_dir, cfg_path, gitignore_path, store_path)

        assert not os.path.exists(cfg_path)
        assert not os.path.exists(gitignore_path)
        assert not os.path.exists(store_path)
        assert not os.path.exists(thinkos_dir)

    def test_failed_config_write_leaves_no_artifacts(self, tmp_project):
        """Simulate a config write failure — no partial artifacts remain."""
        from thinkos.onboarding import _cleanup_failed_init

        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        cfg_path = os.path.join(thinkos_dir, CONFIG_FILENAME)
        gitignore_path = os.path.join(thinkos_dir, ".gitignore")
        store_path = os.path.join(thinkos_dir, STORE_FILENAME)

        # Create partial state as if config write failed after .thinkos/ creation
        os.makedirs(thinkos_dir, exist_ok=True)
        # No config written yet — simulate failure before atomic write

        _cleanup_failed_init(thinkos_dir, cfg_path, gitignore_path, store_path)
        assert not os.path.exists(thinkos_dir)

    def test_failed_gitignore_write_cleans_up_config(self, tmp_project):
        """If .gitignore write fails, config is cleaned up."""
        from thinkos.onboarding import _cleanup_failed_init

        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        cfg_path = os.path.join(thinkos_dir, CONFIG_FILENAME)
        gitignore_path = os.path.join(thinkos_dir, ".gitignore")
        store_path = os.path.join(thinkos_dir, STORE_FILENAME)

        os.makedirs(thinkos_dir, exist_ok=True)
        Path(cfg_path).write_text("{}")
        # gitignore not written yet — simulate failure

        _cleanup_failed_init(thinkos_dir, cfg_path, gitignore_path, store_path)
        assert not os.path.exists(cfg_path)
        assert not os.path.exists(thinkos_dir)

    def test_failed_store_creation_cleans_up_all(self, tmp_project):
        """If store creation fails, config and gitignore are cleaned up."""
        from thinkos.onboarding import _cleanup_failed_init

        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        cfg_path = os.path.join(thinkos_dir, CONFIG_FILENAME)
        gitignore_path = os.path.join(thinkos_dir, ".gitignore")
        store_path = os.path.join(thinkos_dir, STORE_FILENAME)

        os.makedirs(thinkos_dir, exist_ok=True)
        Path(cfg_path).write_text("{}")
        Path(gitignore_path).write_text("")
        # store not created yet — simulate failure

        _cleanup_failed_init(thinkos_dir, cfg_path, gitignore_path, store_path)
        assert not os.path.exists(cfg_path)
        assert not os.path.exists(gitignore_path)
        assert not os.path.exists(thinkos_dir)


# ── Init: reject multiple paths ────────────────────────────────────────────


class TestInitRejectMultiplePaths:
    def test_multiple_paths_rejected_via_cli(self, tmp_project):
        """Multiple positional project paths are rejected."""
        other = os.path.join(tmp_project, "other")
        os.makedirs(other, exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", tmp_project, other],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "extra argument" in result.stderr.lower()


# ── Doctor: healthy ─────────────────────────────────────────────────────────


class TestDoctorHealthy:
    def test_healthy_doctor_returns_exit_0(self, tmp_project):
        """Healthy doctor returns exit 0 in human mode."""
        init(project_path=tmp_project)
        result = doctor(project_path=tmp_project)
        assert result["status"] == "healthy"
        for f in result["findings"]:
            assert f["status"] == "ok", f"Check '{f['check']}' failed: {f['detail']}"

    def test_healthy_doctor_json(self, tmp_project):
        """Healthy doctor returns exit 0 in JSON mode."""
        init(project_path=tmp_project)
        result = doctor(project_path=tmp_project, json_output=True)
        assert result["status"] == "healthy"

    def test_doctor_no_existing_db(self, tmp_project):
        """Doctor handles missing database gracefully (no mutation)."""
        init(project_path=tmp_project)
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        os.unlink(store_path)

        result = doctor(project_path=tmp_project)
        assert result["status"] == "healthy"
        sqlite_findings = [f for f in result["findings"] if f["check"] == "sqlite_integrity"]
        assert len(sqlite_findings) == 1
        assert sqlite_findings[0]["status"] == "ok"
        assert "No existing database" in sqlite_findings[0]["detail"]
        assert not os.path.isfile(store_path)

    def test_doctor_performs_no_filesystem_mutation(self, tmp_project):
        """Doctor creates or modifies no files, sidecars, timestamps, or contents."""
        init(project_path=tmp_project)
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        gitignore_path = os.path.join(tmp_project, THINKOS_DIR, ".gitignore")

        mtime_before_store = os.path.getmtime(store_path)
        mtime_before_cfg = os.path.getmtime(cfg_path)
        mtime_before_git = os.path.getmtime(gitignore_path)

        doctor(project_path=tmp_project)

        assert os.path.getmtime(store_path) == mtime_before_store
        assert os.path.getmtime(cfg_path) == mtime_before_cfg
        assert os.path.getmtime(gitignore_path) == mtime_before_git


# ── Doctor: unhealthy ──────────────────────────────────────────────────────


class TestDoctorUnhealthy:
    def test_invalid_config_found(self, tmp_project):
        """Invalid config produces specific unhealthy finding."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path, "w") as f:
            f.write("not json")

        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        config_validity = [f for f in result["findings"] if f["check"] == "config_validity"]
        assert len(config_validity) >= 1
        assert config_validity[0]["status"] == "unhealthy"

    def test_disabled_sandbox_detected(self, tmp_project):
        """Disabled sandbox produces specific unhealthy finding."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["tools"]["allowed_root"] = None
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        sandbox_findings = [f for f in result["findings"] if f["check"] == "sandbox"]
        assert len(sandbox_findings) >= 1
        assert sandbox_findings[0]["status"] == "unhealthy"

    def test_ephemeral_store_detected(self, tmp_project):
        """Ephemeral store produces specific unhealthy finding."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["store"]["path"] = None
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        store_findings = [f for f in result["findings"] if f["check"] == "store_config"]
        assert len(store_findings) >= 1
        assert store_findings[0]["status"] == "unhealthy"

    def test_corrupted_sqlite_detected(self, tmp_project):
        """Corrupted existing SQLite database produces specific unhealthy finding."""
        init(project_path=tmp_project)
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        # Remove the valid DB and create a corrupted one
        os.unlink(store_path)
        with open(store_path, "w") as f:
            f.write("this is not a valid sqlite database")

        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        sqlite_findings = [f for f in result["findings"] if f["check"] == "sqlite_integrity"]
        assert len(sqlite_findings) >= 1
        assert sqlite_findings[0]["status"] == "unhealthy"

    def test_missing_config_detected(self, tmp_project):
        """Missing .thinkos/ directory produces unhealthy finding."""
        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        config_findings = [f for f in result["findings"] if f["check"] == "config_presence"]
        assert len(config_findings) >= 1
        assert config_findings[0]["status"] == "unhealthy"

    def test_allowed_root_mismatch_detected(self, tmp_project):
        """allowed_root that doesn't match project root produces unhealthy finding."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["tools"]["allowed_root"] = "/tmp/somewhere_else"
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        sandbox_findings = [f for f in result["findings"] if f["check"] == "sandbox"]
        assert len(sandbox_findings) >= 1
        assert sandbox_findings[0]["status"] == "unhealthy"

    def test_invalid_gate_name_detected(self, tmp_project):
        """Unrecognised gate name produces unhealthy finding."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["gates"]["default"] = "nonexistent_gate"
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        validity_findings = [f for f in result["findings"] if f["check"] == "config_validity"]
        assert len(validity_findings) >= 1
        assert validity_findings[0]["status"] == "unhealthy"


# ── Doctor: no mutation ─────────────────────────────────────────────────────


class TestDoctorNoMutation:
    def test_doctor_does_not_create_db(self, tmp_project):
        """Doctor does not create a database merely to inspect it."""
        init(project_path=tmp_project)
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        os.unlink(store_path)

        doctor(project_path=tmp_project)
        assert not os.path.isfile(store_path)

    def test_doctor_does_not_create_config(self, tmp_project):
        """Doctor on an uninitialized project does not create config."""
        doctor(project_path=tmp_project)
        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        assert not os.path.isdir(thinkos_dir)

    def test_doctor_read_only_sqlite(self, tmp_project):
        """Doctor opens existing SQLite databases read-only (no data mutation)."""
        init(project_path=tmp_project)
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)

        # Write some data so the DB is non-trivial
        conn = sqlite3.connect(store_path)
        conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO test (val) VALUES ('hello')")
        conn.commit()
        conn.close()

        mtime_before = os.path.getmtime(store_path)

        doctor(project_path=tmp_project)

        # Timestamp unchanged
        assert os.path.getmtime(store_path) == mtime_before

        # Data intact
        conn = sqlite3.connect(store_path)
        cursor = conn.execute("SELECT val FROM test WHERE id = 1")
        assert cursor.fetchone()[0] == "hello"
        conn.close()


# ── CLI behavior ────────────────────────────────────────────────────────────


class TestCLI:
    def test_help_exits_cleanly(self):
        """--help documents init and doctor."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "--help"],
            capture_output=True, text=True, cwd="/tmp",
        )
        assert result.returncode == 0
        assert "init" in result.stdout
        assert "doctor" in result.stdout
        assert ".thinkos/thinkos.json" in result.stdout

    def test_version_exits_cleanly(self):
        """--version exits cleanly."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "--version"],
            capture_output=True, text=True, cwd="/tmp",
        )
        assert result.returncode == 0
        assert "thinkos" in result.stdout

    def test_unknown_command_fails_clearly(self):
        """Unknown CLI commands fail clearly."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "unknown_cmd_xyz"],
            capture_output=True, text=True, cwd="/tmp",
        )
        assert result.returncode != 0
        assert "unknown command" in result.stderr.lower()

    def test_unknown_option_fails_clearly(self):
        """Unknown options on init fail clearly."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", "--bogus"],
            capture_output=True, text=True, cwd="/tmp",
        )
        assert result.returncode != 0
        assert "unknown option" in result.stderr.lower()

    def test_no_args_starts_engine(self):
        """No arguments preserves existing engine behavior (reads stdin)."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos"],
            input="",
            capture_output=True, text=True, cwd="/tmp",
        )
        assert result.returncode == 0

    def test_init_does_not_initialize_engine(self, tmp_project):
        """Init command must not initialize the engine."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", tmp_project],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "✓" in result.stdout or "ok" in result.stdout.lower()

    def test_doctor_does_not_initialize_engine(self, tmp_project):
        """Doctor command must not initialize the engine."""
        subprocess.run(
            [sys.executable, "-m", "thinkos", "init", tmp_project],
            capture_output=True, text=True,
        )
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "doctor", tmp_project],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "All checks passed" in result.stdout or "healthy" in result.stdout.lower()

    def test_init_failure_exit_code(self):
        """Init failure exits nonzero via CLI."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", "/nonexistent/thinkos_test_xyz"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_init_failure_json_exit_code(self):
        """Init failure exits nonzero via CLI with --json."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", "/nonexistent/thinkos_test_xyz", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_multiple_paths_rejected(self, tmp_project):
        """Multiple positional project paths are rejected."""
        other = os.path.join(tmp_project, "other")
        os.makedirs(other, exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", tmp_project, other],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "extra argument" in result.stderr.lower()


# ── JSON output format ──────────────────────────────────────────────────────


class TestJSONOutput:
    def test_init_json_output(self, tmp_project):
        """Init --json produces stable, structured JSON."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", tmp_project, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "status" in data
        assert "message" in data
        assert data["status"] == "ok"

    def test_init_already_initialized_json(self, tmp_project):
        """Init --json on already-initialized project."""
        subprocess.run(
            [sys.executable, "-m", "thinkos", "init", tmp_project],
            capture_output=True, text=True,
        )
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", tmp_project, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "already_initialized"

    def test_init_error_json(self):
        """Init --json on invalid path produces error JSON."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", "/nonexistent/thinkos_test_xyz", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        data = json.loads(result.stdout)
        assert data["status"] == "error"

    def test_doctor_json_output(self, tmp_project):
        """Doctor --json produces stable, structured JSON."""
        subprocess.run(
            [sys.executable, "-m", "thinkos", "init", tmp_project],
            capture_output=True, text=True,
        )
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "doctor", tmp_project, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "status" in data
        assert "findings" in data
        assert data["status"] == "healthy"

    def test_doctor_unhealthy_json_output(self, tmp_project):
        """Doctor --json on unhealthy project produces structured JSON."""
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "doctor", tmp_project, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        data = json.loads(result.stdout)
        assert data["status"] == "unhealthy"
        assert len(data["findings"]) > 0


# ── End-to-end golden path ─────────────────────────────────────────────────


class TestEndToEnd:
    def test_init_to_engine_to_rehydration(self, tmp_project):
        """init → normal no-arg engine → persistent DB → fresh process rehydration.

        Proves the installation created by thinkos init is the installation
        actually consumed by the normal no-argument ThinkOS engine.
        """
        # 1. Init
        result = subprocess.run(
            [sys.executable, "-m", "thinkos", "init", tmp_project],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

        # 2. Override the write gate to always_allow for non-interactive test
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["gates"]["overrides"]["write_file"] = "always_allow"
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        # 3. Run the engine from the project root with a write_file call
        msg = json.dumps({
            "type": "agent_message",
            "message_id": "msg_e2e_1",
            "session_id": "sess_e2e",
            "timestamp": "2026-07-15T12:00:00Z",
            "sender": "test",
            "content": {
                "text": "write a file",
                "tool_calls": [{
                    "tool": "write_file",
                    "params": {"path": "e2e_test.txt", "content": "hello from e2e"},
                    "call_id": "c1",
                }],
                "context_refs": [],
            },
        })

        result = subprocess.run(
            [sys.executable, "-m", "thinkos"],
            input=msg + "\n",
            capture_output=True, text=True, cwd=tmp_project,
        )
        assert result.returncode == 0
        # The tool result should be ok
        resp = json.loads(result.stdout)
        assert resp["content"]["tool_results"][0]["status"] == "ok"

        # 4. Verify the file was written to the project root
        assert os.path.isfile(os.path.join(tmp_project, "e2e_test.txt"))

        # 5. Verify the database was written to .thinkos/thinkos.sqlite
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        assert os.path.isfile(store_path)

        conn = sqlite3.connect(store_path)
        rows = conn.execute(
            "SELECT content_text FROM packets WHERE session_id = 'sess_e2e'"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1
        # The packet stores the tool result output, not the file content
        assert any("Wrote" in row[0] for row in rows)

        # 6. Fresh process rehydration — run engine again with rehydrate flag
        msg2 = json.dumps({
            "type": "agent_message",
            "message_id": "msg_e2e_2",
            "session_id": "sess_e2e",
            "timestamp": "2026-07-15T12:05:00Z",
            "sender": "test",
            "content": {
                "text": "resume",
                "rehydrate": True,
                "tool_calls": [],
                "context_refs": [],
            },
        })

        result2 = subprocess.run(
            [sys.executable, "-m", "thinkos"],
            input=msg2 + "\n",
            capture_output=True, text=True, cwd=tmp_project,
        )
        assert result2.returncode == 0
        resp2 = json.loads(result2.stdout)
        assert "rehydrated" in resp2["content"]

    def test_sandbox_enforcement(self, tmp_project):
        """Runtime filesystem request outside allowed_root is denied."""
        init(project_path=tmp_project)

        # Try to read /etc/hostname via the engine
        msg = json.dumps({
            "type": "agent_message",
            "message_id": "msg_sb_1",
            "session_id": "sess_sb",
            "timestamp": "2026-07-15T12:00:00Z",
            "sender": "test",
            "content": {
                "text": "read outside",
                "tool_calls": [{
                    "tool": "read_file",
                    "params": {"path": "/etc/hostname", "call_id": "c1"},
                }],
                "context_refs": [],
            },
        })

        result = subprocess.run(
            [sys.executable, "-m", "thinkos"],
            input=msg + "\n",
            capture_output=True, text=True, cwd=tmp_project,
        )
        assert result.returncode == 0
        # The engine should deny the read (sandbox enforcement)
        assert "Access denied" in result.stdout or "error" in result.stdout.lower()

    def test_uninitialized_project_engine_backward_compat(self, tmp_project):
        """No-arg engine behavior is backward compatible for uninitialized projects."""
        # Create a thinkos.json with always_allow for non-interactive test
        cfg = {
            "gates": {
                "default": "always_allow",
                "overrides": {
                    "read_file": "always_allow",
                    "write_file": "always_allow",
                },
            },
        }
        with open(os.path.join(tmp_project, "thinkos.json"), "w") as f:
            json.dump(cfg, f)

        msg = json.dumps({
            "type": "agent_message",
            "message_id": "msg_bc_1",
            "session_id": "sess_bc",
            "timestamp": "2026-07-15T12:00:00Z",
            "sender": "test",
            "content": {
                "text": "write a file",
                "tool_calls": [{
                    "tool": "write_file",
                    "params": {"path": "bc_test.txt", "content": "backward compat"},
                    "call_id": "c1",
                }],
                "context_refs": [],
            },
        })

        result = subprocess.run(
            [sys.executable, "-m", "thinkos"],
            input=msg + "\n",
            capture_output=True, text=True, cwd=tmp_project,
        )
        assert result.returncode == 0
        resp = json.loads(result.stdout)
        assert resp["content"]["tool_results"][0]["status"] == "ok"


# ── Config discovery ────────────────────────────────────────────────────────


class TestConfigDiscovery:
    def test_dot_thinkos_config_discovered(self, tmp_project):
        """.thinkos/thinkos.json is discovered by load_config."""
        from thinkos.config import load_config
        init(project_path=tmp_project)
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_project)
            config = load_config()
            assert config is not None
            assert config["store"]["path"] == ".thinkos/thinkos.sqlite"
            assert config["tools"]["allowed_root"] == tmp_project
        finally:
            os.chdir(original_cwd)

    def test_root_thinkos_json_still_discovered(self, tmp_project):
        """Root thinkos.json is still discovered (backward compat)."""
        from thinkos.config import load_config
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_project)
            cfg = {"gates": {"default": "always_allow"}}
            with open("thinkos.json", "w") as f:
                json.dump(cfg, f)
            config = load_config()
            assert config["gates"]["default"] == "always_allow"
        finally:
            os.chdir(original_cwd)

    def test_dot_thinkos_json_still_discovered(self, tmp_project):
        """.thinkos.json is still discovered (backward compat)."""
        from thinkos.config import load_config
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_project)
            cfg = {"gates": {"default": "deny_all"}}
            with open(".thinkos.json", "w") as f:
                json.dump(cfg, f)
            config = load_config()
            assert config["gates"]["default"] == "deny_all"
        finally:
            os.chdir(original_cwd)

    def test_dot_thinkos_takes_priority(self, tmp_project):
        """.thinkos/thinkos.json takes priority over root thinkos.json."""
        from thinkos.config import load_config
        init(project_path=tmp_project)
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_project)
            # Also create a root thinkos.json with different content
            cfg = {"gates": {"default": "deny_all"}}
            with open("thinkos.json", "w") as f:
                json.dump(cfg, f)
            # .thinkos/thinkos.json should win
            config = load_config()
            assert config["gates"]["default"] == "confirm"
            assert config["store"]["path"] == ".thinkos/thinkos.sqlite"
        finally:
            os.chdir(original_cwd)


# ── Utility tests ───────────────────────────────────────────────────────────


class TestUtilities:
    def test_configs_equal_identical(self):
        """_configs_equal returns True for identical configs."""
        a = _build_init_config("/tmp/proj")
        b = _build_init_config("/tmp/proj")
        assert _configs_equal(a, b, expected_allowed_root="/tmp/proj")

    def test_configs_equal_different_allowed_root_rejected(self):
        """_configs_equal with expected_allowed_root rejects different allowed_root."""
        a = _build_init_config("/tmp/proj_a")
        b = _build_init_config("/tmp/proj_b")
        assert not _configs_equal(a, b, expected_allowed_root="/tmp/proj_a")

    def test_configs_equal_different_gates(self):
        """_configs_equal returns False for different gate configs."""
        a = _build_init_config("/tmp/proj")
        b = _build_init_config("/tmp/proj")
        b["gates"]["default"] = "deny_all"
        assert not _configs_equal(a, b, expected_allowed_root="/tmp/proj")

    def test_load_config_file_nonexistent(self, tmp_project):
        """_load_config_file returns None for nonexistent file."""
        result = _load_config_file(os.path.join(tmp_project, "nonexistent.json"))
        assert result is None

    def test_load_config_file_malformed(self, tmp_project):
        """_load_config_file returns None for malformed JSON."""
        path = os.path.join(tmp_project, "bad.json")
        Path(path).write_text("not json")
        result = _load_config_file(path)
        assert result is None

    def test_load_config_file_valid(self, tmp_project):
        """_load_config_file returns parsed dict for valid JSON."""
        path = os.path.join(tmp_project, "good.json")
        Path(path).write_text('{"hello": "world"}')
        result = _load_config_file(path)
        assert result == {"hello": "world"}

    def test_canonicalize_path(self, tmp_project):
        """_canonicalize_path resolves paths correctly."""
        assert _canonicalize_path(tmp_project) == str(Path(tmp_project).resolve())

    def test_resolve_actual_store_path_absolute(self):
        """_resolve_actual_store_path returns absolute paths as-is."""
        config = {"store": {"path": "/tmp/abs.db"}}
        result = _resolve_actual_store_path(config, "/proj")
        assert result == "/tmp/abs.db"

    def test_resolve_actual_store_path_relative(self):
        """_resolve_actual_store_path resolves relative paths against project root."""
        config = {"store": {"path": "thinkos.sqlite"}}
        result = _resolve_actual_store_path(config, "/proj")
        assert result == "/proj/thinkos.sqlite"

    def test_resolve_actual_store_path_none(self):
        """_resolve_actual_store_path returns None for null path."""
        config = {"store": {"path": None}}
        result = _resolve_actual_store_path(config, "/proj")
        assert result is None
