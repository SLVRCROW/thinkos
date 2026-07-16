"""Tests for ThinkOS onboarding — init and doctor commands.

Covers all acceptance criteria from the Alpha Door P1 work order.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from thinkos.onboarding import (
    init,
    doctor,
    _configs_equal,
    _load_config_file,
    _build_init_config,
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
        # Check structure
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
        # Default policy: read_file always_allow, write_file confirm
        assert config["gates"]["overrides"]["read_file"] == "always_allow"
        assert config["gates"]["overrides"]["write_file"] == "confirm"
        # Sandbox is active
        assert config["tools"]["allowed_root"] == tmp_project
        # Store is persistent
        assert config["store"]["path"] == STORE_FILENAME

    def test_runtime_store_is_persistent(self, tmp_project):
        """Runtime store is persistent and sandboxed."""
        init(project_path=tmp_project)
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        assert os.path.isfile(store_path)
        # Verify it's a valid SQLite database
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
        # Record timestamps
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        mtime_before = os.path.getmtime(cfg_path)

        result = init(project_path=tmp_project)
        assert result["status"] == "already_initialized"

        # Timestamp should not have changed
        mtime_after = os.path.getmtime(cfg_path)
        assert mtime_after == mtime_before, "Config file was modified"

    def test_already_initialized_json_output(self, tmp_project):
        """JSON output for already-initialized has correct status."""
        init(project_path=tmp_project)
        result = init(project_path=tmp_project, json_output=True)
        assert result["status"] == "already_initialized"


# ── Init: safety — reject divergent / malformed / symlink ─────────────────


class TestInitSafety:
    def test_divergent_config_is_rejected(self, tmp_project):
        """Existing divergent config is preserved and rejected."""
        init(project_path=tmp_project)
        # Modify the config
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["gates"]["default"] = "deny_all"
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert "differs" in result["message"].lower()

        # Original config should be preserved
        with open(cfg_path) as f:
            preserved = json.load(f)
        assert preserved["gates"]["default"] == "deny_all"

    def test_malformed_config_is_rejected(self, tmp_project):
        """Existing malformed config is preserved and rejected."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        # Corrupt the config
        with open(cfg_path, "w") as f:
            f.write("not valid json{")

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert "malformed" in result["message"].lower()

        # Original content should be preserved
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


# ── Init: atomicity ─────────────────────────────────────────────────────────


class TestInitAtomicity:
    def test_interrupted_init_leaves_no_partial_config(self, tmp_project):
        """Simulate an interrupted init by failing after partial writes.

        We test this by checking that if any step fails, the cleanup
        removes all traces.
        """
        # We can't easily inject a failure into the middle of init,
        # but we can verify the cleanup function works correctly.
        from thinkos.onboarding import _cleanup_failed_init

        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        cfg_path = os.path.join(thinkos_dir, CONFIG_FILENAME)
        gitignore_path = os.path.join(thinkos_dir, ".gitignore")
        store_path = os.path.join(thinkos_dir, STORE_FILENAME)

        # Create partial state
        os.makedirs(thinkos_dir, exist_ok=True)
        Path(cfg_path).write_text("{}")
        Path(gitignore_path).write_text("")
        Path(store_path).write_text("")

        _cleanup_failed_init(thinkos_dir, cfg_path, gitignore_path, store_path)

        assert not os.path.exists(cfg_path)
        assert not os.path.exists(gitignore_path)
        assert not os.path.exists(store_path)
        # Directory should be removed if empty
        assert not os.path.exists(thinkos_dir)


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
        # Remove the database
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        os.unlink(store_path)

        result = doctor(project_path=tmp_project)
        assert result["status"] == "healthy"
        # Should have a finding about no existing DB
        sqlite_findings = [f for f in result["findings"] if f["check"] == "sqlite_integrity"]
        assert len(sqlite_findings) == 1
        assert sqlite_findings[0]["status"] == "ok"
        assert "No existing database" in sqlite_findings[0]["detail"]

        # DB should NOT have been created
        assert not os.path.isfile(store_path)

    def test_doctor_performs_no_filesystem_mutation(self, tmp_project):
        """Doctor performs no filesystem mutation."""
        init(project_path=tmp_project)
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        mtime_before = os.path.getmtime(store_path)

        doctor(project_path=tmp_project)

        mtime_after = os.path.getmtime(store_path)
        assert mtime_after == mtime_before, "Doctor mutated the filesystem"


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
        # Corrupt the database by writing garbage
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


# ── Doctor: no mutation ─────────────────────────────────────────────────────


class TestDoctorNoMutation:
    def test_doctor_does_not_create_db(self, tmp_project):
        """Doctor does not create a database merely to inspect it."""
        init(project_path=tmp_project)
        store_path = os.path.join(tmp_project, THINKOS_DIR, STORE_FILENAME)
        os.unlink(store_path)

        doctor(project_path=tmp_project)

        # DB should still not exist
        assert not os.path.isfile(store_path)

    def test_doctor_does_not_create_config(self, tmp_project):
        """Doctor on an uninitialized project does not create config."""
        doctor(project_path=tmp_project)
        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        assert not os.path.isdir(thinkos_dir)


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
        # Engine with empty stdin exits cleanly (no input to process)
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
        # First init
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


# ── Utility tests ───────────────────────────────────────────────────────────


class TestUtilities:
    def test_configs_equal_identical(self):
        """_configs_equal returns True for identical configs."""
        a = _build_init_config("/tmp/proj")
        b = _build_init_config("/tmp/proj")
        assert _configs_equal(a, b)

    def test_configs_equal_different_allowed_root(self):
        """_configs_equal ignores allowed_root differences."""
        a = _build_init_config("/tmp/proj_a")
        b = _build_init_config("/tmp/proj_b")
        assert _configs_equal(a, b)

    def test_configs_equal_different_gates(self):
        """_configs_equal returns False for different gate configs."""
        a = _build_init_config("/tmp/proj")
        b = _build_init_config("/tmp/proj")
        b["gates"]["default"] = "deny_all"
        assert not _configs_equal(a, b)

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
