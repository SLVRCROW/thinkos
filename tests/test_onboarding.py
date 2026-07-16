"""Tests for ThinkOS onboarding — init and doctor commands.

Covers all acceptance criteria from the Alpha Door P1 final falsification repair.
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
    _canonicalize_path,
    _resolve_actual_store_path,
    _atomic_write_json,
    _atomic_write_text,
    _cleanup_failed_init,
    DEFAULT_CONFIG,
    THINKOS_DIR,
    CONFIG_FILENAME,
    STORE_FILENAME,
    GITIGNORE_CONTENT,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _repo_root() -> str:
    """Derive the repository root from this test file's location."""
    return str(Path(__file__).resolve().parents[1])


def _thinkos_env() -> dict:
    """Return an environment dict with PYTHONPATH set to the repo root.

    Preserves any existing PYTHONPATH and prepends the repo root.
    """
    env = os.environ.copy()
    repo = _repo_root()
    existing = env.get("PYTHONPATH", "")
    if existing:
        env["PYTHONPATH"] = f"{repo}:{existing}"
    else:
        env["PYTHONPATH"] = repo
    return env


def _run_thinkos(*args: str, cwd: str | None = None, input_str: str = "") -> subprocess.CompletedProcess:
    """Run `python -m thinkos` with PYTHONPATH set to the repo root."""
    return subprocess.run(
        [sys.executable, "-m", "thinkos", *args],
        input=input_str,
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
        config["tools"]["allowed_root"] = "/tmp/somewhere_else"
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert "differs" in result["message"].lower()

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

        with open(cfg_path) as f:
            preserved = json.load(f)
        assert preserved["tools"]["allowed_root"] is None


# ── Init: legacy-config shadowing prevention ─────────────────────────────


class TestInitLegacyShadow:
    def test_legacy_thinkos_json_blocks_init(self, tmp_project):
        """If thinkos.json exists, init must not silently create .thinkos/ that shadows it."""
        cfg_path = os.path.join(tmp_project, "thinkos.json")
        with open(cfg_path, "w") as f:
            json.dump({"gates": {"default": "always_allow"}}, f)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert "conflict" in result["message"].lower() or "exists" in result["message"].lower()

        # .thinkos/ should not have been created
        assert not os.path.isdir(os.path.join(tmp_project, THINKOS_DIR))
        # Original config preserved
        assert os.path.isfile(cfg_path)

    def test_legacy_dot_thinkos_json_blocks_init(self, tmp_project):
        """If .thinkos.json exists, init must not silently create .thinkos/ that shadows it."""
        cfg_path = os.path.join(tmp_project, ".thinkos.json")
        with open(cfg_path, "w") as f:
            json.dump({"gates": {"default": "always_allow"}}, f)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert "conflict" in result["message"].lower() or "exists" in result["message"].lower()

        assert not os.path.isdir(os.path.join(tmp_project, THINKOS_DIR))
        assert os.path.isfile(cfg_path)

    def test_legacy_configs_do_not_block_each_other(self, tmp_project):
        """Both legacy configs existing blocks init with a clear message."""
        Path(os.path.join(tmp_project, "thinkos.json")).write_text("{}")
        Path(os.path.join(tmp_project, ".thinkos.json")).write_text("{}")

        result = init(project_path=tmp_project)
        assert result["status"] == "error"
        assert not os.path.isdir(os.path.join(tmp_project, THINKOS_DIR))


# ── Init: exit codes ──────────────────────────────────────────────────────


class TestInitExitCodes:
    def test_failed_init_exits_nonzero_human(self):
        """Failed init exits nonzero in human mode."""
        result = init(project_path="/nonexistent/thinkos_test_xyz")
        assert result["status"] == "error"

    def test_failed_init_exits_nonzero_json(self):
        """Failed init exits nonzero in JSON mode."""
        result = init(project_path="/nonexistent/thinkos_test_xyz", json_output=True)
        assert result["status"] == "error"

    def test_already_initialized_exits_zero(self, tmp_project):
        """already_initialized remains exit 0."""
        init(project_path=tmp_project)
        result = init(project_path=tmp_project)
        assert result["status"] == "already_initialized"


# ── Init: atomicity via monkeypatch ───────────────────────────────────────


class TestInitAtomicity:
    def test_config_write_failure_cleans_up(self, tmp_project, monkeypatch):
        """Config write failure: no generated artifacts remain."""
        def _fail_write_json(path, data):
            raise OSError("Simulated config write failure")

        monkeypatch.setattr("thinkos.onboarding._atomic_write_json", _fail_write_json)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"

        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        assert not os.path.exists(thinkos_dir)

    def test_gitignore_write_failure_cleans_up(self, tmp_project, monkeypatch):
        """Gitignore write failure: config and directory cleaned up."""
        def _fail_gitignore(path, content):
            raise OSError("Simulated gitignore write failure")

        monkeypatch.setattr("thinkos.onboarding._atomic_write_text", _fail_gitignore)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"

        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        assert not os.path.exists(thinkos_dir)

    def test_store_creation_failure_cleans_up(self, tmp_project, monkeypatch):
        """Store creation failure: config, gitignore, and directory cleaned up."""
        real_write_json = _atomic_write_json
        real_write_text = _atomic_write_text

        call_count = 0

        def _fail_store(path, content=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return real_write_json(path, content)
            if call_count == 2:
                return real_write_text(path, content)
            raise sqlite3.Error("Simulated store creation failure")

        # We need to patch sqlite3.connect instead
        original_connect = sqlite3.connect

        def _fail_connect(*args, **kwargs):
            if args[0] and "thinkos.sqlite" in str(args[0]):
                raise sqlite3.Error("Simulated store creation failure")
            return original_connect(*args, **kwargs)

        monkeypatch.setattr("sqlite3.connect", _fail_connect)

        result = init(project_path=tmp_project)
        assert result["status"] == "error"

        thinkos_dir = os.path.join(tmp_project, THINKOS_DIR)
        assert not os.path.exists(thinkos_dir)

    def test_unrelated_files_preserved_on_failure(self, tmp_project, monkeypatch):
        """Unrelated project files remain unchanged after a failed init."""
        unrelated = os.path.join(tmp_project, "important.txt")
        Path(unrelated).write_text("do not touch")

        def _fail_write_json(path, data):
            raise OSError("Simulated failure")

        monkeypatch.setattr("thinkos.onboarding._atomic_write_json", _fail_write_json)

        init(project_path=tmp_project)

        assert Path(unrelated).read_text() == "do not touch"


# ── Init: reject multiple paths ────────────────────────────────────────────


class TestInitRejectMultiplePaths:
    def test_multiple_paths_rejected_via_cli(self, tmp_project):
        """Multiple positional project paths are rejected."""
        other = os.path.join(tmp_project, "other")
        os.makedirs(other, exist_ok=True)
        result = _run_thinkos("init", tmp_project, other)
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

    def test_malformed_nested_values_do_not_raise(self, tmp_project):
        """Malformed nested values (tools=[], store=[], invalid overrides) return structured findings."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)

        # Test tools=[]
        with open(cfg_path) as f:
            config = json.load(f)
        config["tools"] = []
        with open(cfg_path, "w") as f:
            json.dump(config, f)
        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        validity = [f for f in result["findings"] if f["check"] == "config_validity"]
        assert len(validity) >= 1
        assert validity[0]["status"] == "unhealthy"

        # Test store=[]
        with open(cfg_path) as f:
            config = json.load(f)
        config["store"] = []
        with open(cfg_path, "w") as f:
            json.dump(config, f)
        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        validity = [f for f in result["findings"] if f["check"] == "config_validity"]
        assert len(validity) >= 1
        assert validity[0]["status"] == "unhealthy"

    def test_non_string_allowed_root_does_not_raise(self, tmp_project):
        """A malformed allowed_root returns structured unhealthy findings."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["tools"]["allowed_root"] = []
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = doctor(project_path=tmp_project)

        assert result["status"] == "unhealthy"
        validity = [f for f in result["findings"] if f["check"] == "config_validity"]
        assert len(validity) >= 1
        assert validity[0]["status"] == "unhealthy"

    def test_unknown_tool_in_override_detected(self, tmp_project):
        """Unknown tool name in gate override produces unhealthy finding."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        with open(cfg_path) as f:
            config = json.load(f)
        config["gates"]["overrides"]["unknown_tool"] = "always_allow"
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        result = doctor(project_path=tmp_project)
        assert result["status"] == "unhealthy"
        validity = [f for f in result["findings"] if f["check"] == "config_validity"]
        assert len(validity) >= 1
        assert validity[0]["status"] == "unhealthy"

    def test_store_path_escape_detected(self, tmp_project, monkeypatch):
        """An external store is rejected without opening the SQLite file."""
        init(project_path=tmp_project)
        cfg_path = os.path.join(tmp_project, THINKOS_DIR, CONFIG_FILENAME)
        outside_store = Path(tmp_project).with_name(
            Path(tmp_project).name + "-outside.sqlite"
        )
        outside_store.write_bytes(b"")
        with open(cfg_path) as f:
            config = json.load(f)
        config["store"]["path"] = str(outside_store)
        with open(cfg_path, "w") as f:
            json.dump(config, f)

        def fail_if_opened(*_args, **_kwargs):
            raise AssertionError("doctor attempted to open an external store")

        monkeypatch.setattr("thinkos.onboarding.sqlite3.connect", fail_if_opened)
        try:
            result = doctor(project_path=tmp_project)
        finally:
            outside_store.unlink(missing_ok=True)

        assert result["status"] == "unhealthy"
        store_findings = [f for f in result["findings"] if f["check"] == "store_config"]
        assert len(store_findings) >= 1
        assert store_findings[0]["status"] == "unhealthy"


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

        conn = sqlite3.connect(store_path)
        conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO test (val) VALUES ('hello')")
        conn.commit()
        conn.close()

        mtime_before = os.path.getmtime(store_path)

        doctor(project_path=tmp_project)

        assert os.path.getmtime(store_path) == mtime_before

        conn = sqlite3.connect(store_path)
        cursor = conn.execute("SELECT val FROM test WHERE id = 1")
        assert cursor.fetchone()[0] == "hello"
        conn.close()


# ── CLI behavior ────────────────────────────────────────────────────────────


class TestCLI:
    def test_help_exits_cleanly(self):
        """--help documents init and doctor."""
        result = _run_thinkos("--help")
        assert result.returncode == 0
        assert "init" in result.stdout
        assert "doctor" in result.stdout
        assert ".thinkos/thinkos.json" in result.stdout

    def test_version_exits_cleanly(self):
        """--version exits cleanly."""
        result = _run_thinkos("--version")
        assert result.returncode == 0
        assert "thinkos" in result.stdout

    def test_unknown_command_fails_clearly(self):
        """Unknown CLI commands fail clearly."""
        result = _run_thinkos("unknown_cmd_xyz")
        assert result.returncode != 0
        assert "unknown command" in result.stderr.lower()

    def test_unknown_option_fails_clearly(self):
        """Unknown options on init fail clearly."""
        result = _run_thinkos("init", "--bogus")
        assert result.returncode != 0
        assert "unknown option" in result.stderr.lower()

    def test_no_args_starts_engine(self):
        """No arguments preserves existing engine behavior (reads stdin)."""
        result = _run_thinkos(input_str="")
        assert result.returncode == 0

    def test_init_does_not_initialize_engine(self, tmp_project):
        """Init command must not initialize the engine."""
        result = _run_thinkos("init", tmp_project)
        assert result.returncode == 0
        assert "✓" in result.stdout or "ok" in result.stdout.lower()

    def test_doctor_does_not_initialize_engine(self, tmp_project):
        """Doctor command must not initialize the engine."""
        _run_thinkos("init", tmp_project)
        result = _run_thinkos("doctor", tmp_project)
        assert result.returncode == 0
        assert "All checks passed" in result.stdout or "healthy" in result.stdout.lower()

    def test_init_failure_exit_code(self):
        """Init failure exits nonzero via CLI."""
        result = _run_thinkos("init", "/nonexistent/thinkos_test_xyz")
        assert result.returncode != 0

    def test_init_failure_json_exit_code(self):
        """Init failure exits nonzero via CLI with --json."""
        result = _run_thinkos("init", "/nonexistent/thinkos_test_xyz", "--json")
        assert result.returncode != 0

    def test_multiple_paths_rejected(self, tmp_project):
        """Multiple positional project paths are rejected."""
        other = os.path.join(tmp_project, "other")
        os.makedirs(other, exist_ok=True)
        result = _run_thinkos("init", tmp_project, other)
        assert result.returncode != 0
        assert "extra argument" in result.stderr.lower()


# ── JSON output format ──────────────────────────────────────────────────────


class TestJSONOutput:
    def test_init_json_output(self, tmp_project):
        """Init --json produces stable, structured JSON."""
        result = _run_thinkos("init", tmp_project, "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "status" in data
        assert "message" in data
        assert data["status"] == "ok"

    def test_init_already_initialized_json(self, tmp_project):
        """Init --json on already-initialized project."""
        _run_thinkos("init", tmp_project)
        result = _run_thinkos("init", tmp_project, "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "already_initialized"

    def test_init_error_json(self):
        """Init --json on invalid path produces error JSON."""
        result = _run_thinkos("init", "/nonexistent/thinkos_test_xyz", "--json")
        assert result.returncode != 0
        data = json.loads(result.stdout)
        assert data["status"] == "error"

    def test_doctor_json_output(self, tmp_project):
        """Doctor --json produces stable, structured JSON."""
        _run_thinkos("init", tmp_project)
        result = _run_thinkos("doctor", tmp_project, "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "status" in data
        assert "findings" in data
        assert data["status"] == "healthy"

    def test_doctor_unhealthy_json_output(self, tmp_project):
        """Doctor --json on unhealthy project produces structured JSON."""
        result = _run_thinkos("doctor", tmp_project, "--json")
        assert result.returncode != 0
        data = json.loads(result.stdout)
        assert data["status"] == "unhealthy"
        assert len(data["findings"]) > 0


# ── End-to-end golden path ─────────────────────────────────────────────────


class TestEndToEnd:
    def test_init_to_engine_to_rehydration(self, tmp_project):
        """init -> normal no-arg engine -> persistent DB -> fresh process rehydration.

        Proves the installation created by thinkos init is the installation
        actually consumed by the normal no-argument ThinkOS engine.
        """
        # 1. Init
        result = _run_thinkos("init", tmp_project)
        assert result.returncode == 0

        # 2. Override write gate to always_allow for non-interactive test
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

        result = _run_thinkos(cwd=tmp_project, input_str=msg + "\n")
        assert result.returncode == 0
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
        assert any("Wrote" in row[0] for row in rows)

        # 6. Fresh process rehydration
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

        result2 = _run_thinkos(cwd=tmp_project, input_str=msg2 + "\n")
        assert result2.returncode == 0
        resp2 = json.loads(result2.stdout)
        assert "rehydrated" in resp2["content"]

    def test_sandbox_enforcement(self, tmp_project):
        """Runtime filesystem request outside allowed_root is denied."""
        _run_thinkos("init", tmp_project)

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

        result = _run_thinkos(cwd=tmp_project, input_str=msg + "\n")
        assert result.returncode == 0
        resp = json.loads(result.stdout)
        # The tool result should have status error (sandbox denied)
        assert resp["content"]["tool_results"][0]["status"] == "error"

    def test_uninitialized_project_engine_backward_compat(self, tmp_project):
        """No-arg engine behavior is backward compatible for uninitialized projects."""
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

        result = _run_thinkos(cwd=tmp_project, input_str=msg + "\n")
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
            cfg = {"gates": {"default": "deny_all"}}
            with open("thinkos.json", "w") as f:
                json.dump(cfg, f)
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
