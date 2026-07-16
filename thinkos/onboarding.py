"""ThinkOS onboarding — init and doctor commands for project setup and health.

This module provides safe, machine-readable primitives for agents to
initialize ThinkOS for a project and verify that the installation is healthy.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from importlib.metadata import version as _pkg_version
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────

THINKOS_DIR = ".thinkos"
CONFIG_FILENAME = "thinkos.json"
GITIGNORE_FILENAME = ".gitignore"
STORE_FILENAME = "thinkos.sqlite"

DEFAULT_CONFIG = {
    "gates": {
        "default": "confirm",
        "overrides": {
            "read_file": "always_allow",
            "write_file": "confirm",
        },
    },
    "tools": {
        "allowed_root": None,  # resolved at init time to the project root
    },
    "store": {
        "path": STORE_FILENAME,
    },
    "limits": {
        "max_line_bytes": 1048576,
        "max_write_content_bytes": 10485760,
        "max_read_output_bytes": 1048576,
        "max_tool_calls_per_message": 10,
    },
    "rehydration": {
        "max_packets": None,
    },
    "taa": {
        "enabled": False,
        "principal": None,
        "session_id": None,
        "namespace": None,
        "issuer": "process-bound",
        "ttl_seconds": 3600,
        "policy_version": "1",
    },
}

GITIGNORE_CONTENT = """# ThinkOS runtime database — auto-generated, do not commit
thinkos.sqlite
thinkos.sqlite-wal
thinkos.sqlite-shm
"""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _thinkos_version() -> str:
    """Return the installed ThinkOS version string."""
    try:
        return _pkg_version("thinkos")
    except Exception:
        return "0.1.0"


def _resolve_project_path(project_path: str | None) -> str:
    """Resolve the project path; default to CWD."""
    if project_path is None:
        return os.path.abspath(os.getcwd())
    return os.path.abspath(project_path)


def _thinkos_dir(project_path: str) -> str:
    return os.path.join(project_path, THINKOS_DIR)


def _config_path(project_path: str) -> str:
    return os.path.join(_thinkos_dir(project_path), CONFIG_FILENAME)


def _gitignore_path(project_path: str) -> str:
    return os.path.join(_thinkos_dir(project_path), GITIGNORE_FILENAME)


def _store_path(project_path: str) -> str:
    return os.path.join(_thinkos_dir(project_path), STORE_FILENAME)


def _load_config_file(config_path: str) -> dict | None:
    """Load a config JSON file. Returns None if file doesn't exist or is invalid."""
    if not os.path.isfile(config_path):
        return None
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _configs_equal(a: dict, b: dict) -> bool:
    """Deep equality check for config dicts, ignoring allowed_root resolution."""
    a_clean = {k: v for k, v in a.items() if k != "tools"}
    b_clean = {k: v for k, v in b.items() if k != "tools"}
    if a_clean != b_clean:
        return False
    # Compare tools key carefully — allowed_root may differ by resolution
    a_tools = a.get("tools", {})
    b_tools = b.get("tools", {})
    for k in set(a_tools) | set(b_tools):
        if k == "allowed_root":
            continue
        if a_tools.get(k) != b_tools.get(k):
            return False
    return True


def _atomic_write_json(path: str, data: dict) -> None:
    """Write a JSON file atomically via tempfile + rename."""
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix="thinkos_",
        dir=dir_path,
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_text(path: str, content: str) -> None:
    """Write a text file atomically via tempfile + rename."""
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix="thinkos_",
        dir=dir_path,
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Init ─────────────────────────────────────────────────────────────────────


def init(
    project_path: str | None = None,
    json_output: bool = False,
) -> dict:
    """Initialize ThinkOS for a project.

    Creates a safe, persistent ThinkOS configuration in .thinkos/ under the
    project directory.

    Returns a dict with status and message fields.
    """
    resolved = _resolve_project_path(project_path)
    thinkos_dir_path = _thinkos_dir(resolved)
    cfg_path = _config_path(resolved)
    gitignore_path = _gitignore_path(resolved)
    store_db_path = _store_path(resolved)

    # ── Safety: check for existing configuration ──────────────────────
    if os.path.isdir(thinkos_dir_path) or os.path.islink(thinkos_dir_path):
        # Symlink check
        if os.path.islink(thinkos_dir_path):
            return _init_result(
                False,
                "ThinkOS directory '.thinkos/' is a symlink. Refusing to initialise "
                "over a symlink. Remove the symlink and retry.",
                json_output,
            )

        # Check if existing config matches
        existing_config = _load_config_file(cfg_path)
        if existing_config is not None:
            expected = _build_init_config(resolved)
            if _configs_equal(existing_config, expected):
                return _init_result(
                    True,
                    f"ThinkOS is already initialised for '{resolved}'.",
                    json_output,
                    already_initialized=True,
                )
            else:
                return _init_result(
                    False,
                    f"Existing configuration at '{cfg_path}' differs from the "
                    f"default. Refusing to overwrite. Remove or rename the "
                    f"existing '.thinkos/' directory and retry.",
                    json_output,
                )

        # Config exists but is malformed
        if os.path.isfile(cfg_path):
            return _init_result(
                False,
                f"Existing configuration at '{cfg_path}' is malformed or "
                f"unreadable. Refusing to overwrite. Remove or rename the "
                f"existing '.thinkos/' directory and retry.",
                json_output,
            )

        # .thinkos/ exists but no config — unexpected state
        return _init_result(
            False,
            f"Directory '.thinkos/' already exists at '{resolved}' but contains "
            f"no valid configuration. Refusing to initialise over an existing "
            f"directory. Remove or rename '.thinkos/' and retry.",
            json_output,
        )

    # ── Path-escape check ─────────────────────────────────────────────
    # Ensure the resolved path doesn't escape via symlinks in parent chain
    try:
        resolved_real = Path(resolved).resolve()
    except (OSError, RuntimeError):
        return _init_result(
            False,
            f"Project path '{resolved}' cannot be resolved. Check that the "
            f"directory exists and is accessible.",
            json_output,
        )

    if not resolved_real.is_dir():
        return _init_result(
            False,
            f"Project path '{resolved}' does not exist or is not a directory.",
            json_output,
        )

    # ── Build and write config ───────────────────────────────────────
    config = _build_init_config(resolved)

    try:
        _atomic_write_json(cfg_path, config)
    except OSError as e:
        # Clean up partial .thinkos/ if it was just created
        _cleanup_failed_init(thinkos_dir_path, cfg_path, gitignore_path, store_db_path)
        return _init_result(
            False,
            f"Failed to write configuration: {e}",
            json_output,
        )

    # ── Write .gitignore ──────────────────────────────────────────────
    try:
        _atomic_write_text(gitignore_path, GITIGNORE_CONTENT)
    except OSError as e:
        _cleanup_failed_init(thinkos_dir_path, cfg_path, gitignore_path, store_db_path)
        return _init_result(
            False,
            f"Failed to write .gitignore: {e}",
            json_output,
        )

    # ── Create empty SQLite database ─────────────────────────────────
    try:
        conn = sqlite3.connect(store_db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("VACUUM")
        conn.close()
    except sqlite3.Error as e:
        _cleanup_failed_init(thinkos_dir_path, cfg_path, gitignore_path, store_db_path)
        return _init_result(
            False,
            f"Failed to create persistent store: {e}",
            json_output,
        )

    return _init_result(
        True,
        f"ThinkOS initialised for '{resolved}'.",
        json_output,
    )


def _build_init_config(project_path: str) -> dict:
    """Build the default init config with allowed_root resolved."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    config["tools"]["allowed_root"] = project_path
    return config


def _init_result(
    success: bool,
    message: str,
    json_output: bool,
    already_initialized: bool = False,
) -> dict:
    """Format the init result."""
    result = {
        "status": "already_initialized" if already_initialized else ("ok" if success else "error"),
        "message": message,
    }
    if json_output:
        if already_initialized:
            print(json.dumps({"status": "already_initialized", "message": message}))
        elif success:
            print(json.dumps({"status": "ok", "message": message}))
        else:
            print(json.dumps({"status": "error", "message": message}))
    else:
        prefix = "✓" if success else "✗"
        if already_initialized:
            prefix = "✓"
        print(f"{prefix} {message}")

    return result


def _cleanup_failed_init(
    thinkos_dir_path: str,
    cfg_path: str,
    gitignore_path: str,
    store_db_path: str,
) -> None:
    """Remove any files created during a failed init attempt."""
    for path in [store_db_path, store_db_path + "-wal", store_db_path + "-shm",
                 gitignore_path, cfg_path]:
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.unlink(path)
        except OSError:
            pass
    try:
        if os.path.isdir(thinkos_dir_path):
            # Only remove if empty (should be, but be safe)
            if not os.listdir(thinkos_dir_path):
                os.rmdir(thinkos_dir_path)
    except OSError:
        pass


# ── Doctor ──────────────────────────────────────────────────────────────────


def doctor(
    project_path: str | None = None,
    json_output: bool = False,
) -> dict:
    """Check ThinkOS installation health for a project.

    Read-only. Returns a findings dict and prints human or JSON output.
    Exits with code 0 only when all checks pass.
    """
    resolved = _resolve_project_path(project_path)
    thinkos_dir_path = _thinkos_dir(resolved)
    cfg_path = _config_path(resolved)
    store_db_path = _store_path(resolved)

    findings: list[dict] = []
    all_healthy = True

    # ── 1. Python version ────────────────────────────────────────────
    py_version = sys.version_info
    py_ok = py_version.major >= 3 and py_version.minor >= 11
    if not py_ok:
        all_healthy = False
    findings.append({
        "check": "python_version",
        "status": "ok" if py_ok else "unhealthy",
        "detail": f"Python {py_version.major}.{py_version.minor}.{py_version.micro} "
                  f"({'compatible' if py_ok else 'requires >= 3.11'})",
    })

    # ── 2. ThinkOS version ───────────────────────────────────────────
    try:
        tk_version = _thinkos_version()
        version_ok = True
    except Exception:
        tk_version = "unknown"
        version_ok = False
    if not version_ok:
        all_healthy = False
    findings.append({
        "check": "thinkos_version",
        "status": "ok" if version_ok else "unhealthy",
        "detail": f"thinkos {tk_version}",
    })

    # ── 3. Config presence and validity ──────────────────────────────
    if not os.path.isdir(thinkos_dir_path):
        all_healthy = False
        findings.append({
            "check": "config_presence",
            "status": "unhealthy",
            "detail": f"'.thinkos/' directory not found at '{resolved}'",
        })
    elif not os.path.isfile(cfg_path):
        all_healthy = False
        findings.append({
            "check": "config_presence",
            "status": "unhealthy",
            "detail": f"Config file not found at '{cfg_path}'",
        })
    else:
        config = _load_config_file(cfg_path)
        if config is None:
            all_healthy = False
            findings.append({
                "check": "config_validity",
                "status": "unhealthy",
                "detail": f"Config at '{cfg_path}' is malformed or unreadable",
            })
        else:
            # Check required keys
            missing_keys = []
            for key in ["gates", "store", "tools"]:
                if key not in config:
                    missing_keys.append(key)
            if missing_keys:
                all_healthy = False
                findings.append({
                    "check": "config_validity",
                    "status": "unhealthy",
                    "detail": f"Config missing required keys: {', '.join(missing_keys)}",
                })
            else:
                findings.append({
                    "check": "config_presence",
                    "status": "ok",
                    "detail": f"Config found at '{cfg_path}'",
                })
                findings.append({
                    "check": "config_validity",
                    "status": "ok",
                    "detail": "Config is valid JSON with required keys",
                })

    # ── 4. Sandbox status ────────────────────────────────────────────
    config = _load_config_file(cfg_path) if os.path.isfile(cfg_path) else None
    if config is not None:
        allowed_root = config.get("tools", {}).get("allowed_root")
        if allowed_root is None:
            all_healthy = False
            findings.append({
                "check": "sandbox",
                "status": "unhealthy",
                "detail": "Sandbox is disabled (allowed_root is null). "
                          "File access is unrestricted.",
            })
        else:
            findings.append({
                "check": "sandbox",
                "status": "ok",
                "detail": f"Sandbox is active (allowed_root: '{allowed_root}')",
            })
    else:
        findings.append({
            "check": "sandbox",
            "status": "unhealthy",
            "detail": "Cannot determine sandbox status — config not available",
        })

    # ── 5. Store configuration ───────────────────────────────────────
    if config is not None:
        store_path_val = config.get("store", {}).get("path")
        if store_path_val is None:
            all_healthy = False
            findings.append({
                "check": "store_config",
                "status": "unhealthy",
                "detail": "Store is ephemeral (store.path is null). "
                          "Data will not persist across sessions.",
            })
        else:
            findings.append({
                "check": "store_config",
                "status": "ok",
                "detail": f"Store is persistent (path: '{store_path_val}')",
            })
    else:
        findings.append({
            "check": "store_config",
            "status": "unhealthy",
            "detail": "Cannot determine store config — config not available",
        })

    # ── 6. Store directory ───────────────────────────────────────────
    store_dir = os.path.dirname(store_db_path)
    if os.path.isdir(store_dir):
        findings.append({
            "check": "store_directory",
            "status": "ok",
            "detail": f"Store directory exists at '{store_dir}'",
        })
    else:
        all_healthy = False
        findings.append({
            "check": "store_directory",
            "status": "unhealthy",
            "detail": f"Store directory does not exist at '{store_dir}'",
        })

    # ── 7. SQLite integrity (only if DB exists) ─────────────────────
    if os.path.isfile(store_db_path):
        try:
            conn = sqlite3.connect(store_db_path)
            cursor = conn.execute("PRAGMA integrity_check")
            integrity_result = cursor.fetchone()
            conn.close()
            if integrity_result and integrity_result[0] == "ok":
                findings.append({
                    "check": "sqlite_integrity",
                    "status": "ok",
                    "detail": "SQLite integrity check passed",
                })
            else:
                all_healthy = False
                findings.append({
                    "check": "sqlite_integrity",
                    "status": "unhealthy",
                    "detail": f"SQLite integrity check failed: {integrity_result}",
                })
        except sqlite3.Error as e:
            all_healthy = False
            findings.append({
                "check": "sqlite_integrity",
                "status": "unhealthy",
                "detail": f"SQLite integrity check error: {e}",
            })
    else:
        findings.append({
            "check": "sqlite_integrity",
            "status": "ok",
            "detail": "No existing database to check (will be created on first use)",
        })

    # ── Output ───────────────────────────────────────────────────────
    if json_output:
        output = {
            "status": "healthy" if all_healthy else "unhealthy",
            "findings": findings,
        }
        print(json.dumps(output, indent=2))
    else:
        status_line = "✓ All checks passed" if all_healthy else "✗ Some checks failed"
        print(f"ThinkOS Doctor — {resolved}")
        print(f"Status: {status_line}")
        print()
        for f in findings:
            icon = "✓" if f["status"] == "ok" else "✗"
            print(f"  {icon} {f['check']}: {f['detail']}")

    return {
        "status": "healthy" if all_healthy else "unhealthy",
        "findings": findings,
    }
