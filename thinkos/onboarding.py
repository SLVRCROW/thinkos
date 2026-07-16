"""ThinkOS onboarding — init and doctor commands for project setup and health.

This module provides safe, machine-readable primitives for agents to
initialize ThinkOS for a project and verify that the installation is healthy.
"""

import json
import os
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
        "path": ".thinkos/thinkos.sqlite",
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


def _canonicalize_path(p: str) -> str:
    """Resolve a path to its canonical real form, following symlinks."""
    return str(Path(p).resolve())


def _configs_equal(a: dict, b: dict, expected_allowed_root: str | None = None) -> bool:
    """Deep equality check for config dicts.

    When expected_allowed_root is provided, allowed_root must match it
    exactly (after canonicalization). A null allowed_root is always
    considered different from a non-null expected_allowed_root.
    """
    a_clean = {k: v for k, v in a.items() if k != "tools"}
    b_clean = {k: v for k, v in b.items() if k != "tools"}
    if a_clean != b_clean:
        return False
    # Compare tools key carefully
    a_tools = a.get("tools", {})
    b_tools = b.get("tools", {})
    for k in set(a_tools) | set(b_tools):
        if k == "allowed_root":
            if expected_allowed_root is not None:
                a_root = a_tools.get("allowed_root")
                b_root = b_tools.get("allowed_root")
                # A null allowed_root never matches a non-null expected root
                if a_root is None or b_root is None:
                    return False
                if _canonicalize_path(a_root) != _canonicalize_path(expected_allowed_root):
                    return False
                if _canonicalize_path(b_root) != _canonicalize_path(expected_allowed_root):
                    return False
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
            if _configs_equal(existing_config, expected, expected_allowed_root=resolved):
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

    # ── Legacy-config shadowing prevention ──────────────────────────
    # If thinkos.json or .thinkos.json already exists in the project root,
    # init must not silently create a higher-priority .thinkos/thinkos.json
    # that replaces its behavior.
    for legacy_name in ("thinkos.json", ".thinkos.json"):
        legacy_path = os.path.join(resolved, legacy_name)
        if os.path.isfile(legacy_path):
            return _init_result(
                False,
                f"Conflict: '{legacy_name}' already exists at '{resolved}'. "
                f"ThinkOS init would create '.thinkos/thinkos.json' which takes "
                f"discovery priority over '{legacy_name}'. Remove or rename "
                f"'{legacy_name}' first, or use it directly without 'thinkos init'.",
                json_output,
            )

    # ── Path-escape check ─────────────────────────────────────────────
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
    """Format the init result.

    already_initialized → exit 0.
    error → exit 1 (both human and JSON modes).
    """
    if already_initialized:
        status = "already_initialized"
    elif success:
        status = "ok"
    else:
        status = "error"

    result = {"status": status, "message": message}

    if json_output:
        print(json.dumps(result))
    else:
        if already_initialized:
            prefix = "✓"
        elif success:
            prefix = "✓"
        else:
            prefix = "✗"
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
            if not os.listdir(thinkos_dir_path):
                os.rmdir(thinkos_dir_path)
    except OSError:
        pass


# ── Doctor ──────────────────────────────────────────────────────────────────


def _resolve_actual_store_path(config: dict, project_root: str) -> str | None:
    """Resolve the actual store path from a loaded config, relative to project root."""
    store_path_val = config.get("store", {}).get("path")
    if store_path_val is None:
        return None
    if os.path.isabs(store_path_val):
        return store_path_val
    return os.path.join(project_root, store_path_val)


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

    # ── 3. Config presence ────────────────────────────────────────────
    if not os.path.isdir(thinkos_dir_path):
        all_healthy = False
        findings.append({
            "check": "config_presence",
            "status": "unhealthy",
            "detail": f"'.thinkos/' directory not found at '{resolved}'",
        })
        # Cannot proceed with further checks that depend on config
        return _doctor_output(findings, all_healthy, resolved, json_output)

    if not os.path.isfile(cfg_path):
        all_healthy = False
        findings.append({
            "check": "config_presence",
            "status": "unhealthy",
            "detail": f"Config file not found at '{cfg_path}'",
        })
        return _doctor_output(findings, all_healthy, resolved, json_output)

    findings.append({
        "check": "config_presence",
        "status": "ok",
        "detail": f"Config found at '{cfg_path}'",
    })

    # ── 4. Config validity (semantic) ─────────────────────────────────
    config = _load_config_file(cfg_path)
    if config is None:
        all_healthy = False
        findings.append({
            "check": "config_validity",
            "status": "unhealthy",
            "detail": f"Config at '{cfg_path}' is malformed or unreadable",
        })
        return _doctor_output(findings, all_healthy, resolved, json_output)

    # Semantic validation
    semantic_errors: list[str] = []

    # Check gates structure
    gates = config.get("gates")
    if not isinstance(gates, dict):
        semantic_errors.append("'gates' must be a dict")
    else:
        default_gate = gates.get("default")
        if default_gate not in ("always_allow", "confirm", "deny_all"):
            semantic_errors.append(f"gates.default='{default_gate}' is not a recognised gate")
        overrides = gates.get("overrides", {})
        if not isinstance(overrides, dict):
            semantic_errors.append("gates.overrides must be a dict")
        else:
            for tool_name, gate_name in overrides.items():
                if not isinstance(tool_name, str):
                    semantic_errors.append(f"gate override key must be a string, got {type(tool_name).__name__}")
                    continue
                if not isinstance(gate_name, str):
                    semantic_errors.append(f"gate '{gate_name}' (override for tool '{tool_name}') must be a string, got {type(gate_name).__name__}")
                    continue
                if gate_name not in ("always_allow", "confirm", "deny_all"):
                    semantic_errors.append(f"gate '{gate_name}' (override for tool '{tool_name}') is not recognised")
                # Validate tool name is known
                if tool_name not in ("read_file", "write_file"):
                    semantic_errors.append(f"override references unknown tool '{tool_name}'")

    # Check store structure
    store = config.get("store")
    if not isinstance(store, dict):
        semantic_errors.append("'store' must be a dict")
    else:
        store_path_val = store.get("path")
        if store_path_val is not None and not isinstance(store_path_val, str):
            semantic_errors.append(f"store.path must be a string or None, got {type(store_path_val).__name__}")

    # Check tools structure
    tools = config.get("tools")
    if not isinstance(tools, dict):
        semantic_errors.append("'tools' must be a dict")
    else:
        allowed_root_value = tools.get("allowed_root")
        if allowed_root_value is not None and not isinstance(allowed_root_value, str):
            semantic_errors.append(
                "tools.allowed_root must be a string or None, got "
                f"{type(allowed_root_value).__name__}"
            )

    if semantic_errors:
        all_healthy = False
        findings.append({
            "check": "config_validity",
            "status": "unhealthy",
            "detail": "; ".join(semantic_errors),
        })
    else:
        findings.append({
            "check": "config_validity",
            "status": "ok",
            "detail": "Config is valid with recognised gate names and required structure",
        })

    # ── 5. Sandbox status ────────────────────────────────────────────
    tools_config = config.get("tools", {})
    if not isinstance(tools_config, dict):
        all_healthy = False
        findings.append({
            "check": "sandbox",
            "status": "unhealthy",
            "detail": "Cannot determine sandbox status — 'tools' is not a dict",
        })
    else:
        allowed_root = tools_config.get("allowed_root")
        if allowed_root is None:
            all_healthy = False
            findings.append({
                "check": "sandbox",
                "status": "unhealthy",
                "detail": "Sandbox is disabled (allowed_root is null). "
                          "File access is unrestricted.",
            })
        else:
            try:
                allowed_canonical = _canonicalize_path(allowed_root)
                project_canonical = _canonicalize_path(resolved)
                if allowed_canonical == project_canonical:
                    findings.append({
                        "check": "sandbox",
                        "status": "ok",
                        "detail": f"Sandbox is active (allowed_root: '{allowed_root}')",
                    })
                else:
                    all_healthy = False
                    findings.append({
                        "check": "sandbox",
                        "status": "unhealthy",
                        "detail": f"Sandbox allowed_root '{allowed_root}' resolves to "
                                  f"'{allowed_canonical}' but project root is "
                                  f"'{project_canonical}'",
                    })
            except (OSError, RuntimeError, TypeError, ValueError):
                all_healthy = False
                findings.append({
                    "check": "sandbox",
                    "status": "unhealthy",
                    "detail": f"Sandbox allowed_root '{allowed_root}' cannot be resolved",
                })

    # ── 6. Store configuration ───────────────────────────────────────
    store_config = config.get("store", {})
    safe_store_path = None
    if not isinstance(store_config, dict):
        all_healthy = False
        findings.append({
            "check": "store_config",
            "status": "unhealthy",
            "detail": "'store' is not a dict — cannot determine store configuration",
        })
    else:
        store_path_val = store_config.get("path")
        if store_path_val is None:
            all_healthy = False
            findings.append({
                "check": "store_config",
                "status": "unhealthy",
                "detail": "Store is ephemeral (store.path is null). "
                          "Data will not persist across sessions.",
            })
        else:
            actual_store_path = _resolve_actual_store_path(config, resolved)
            # Check if store path escapes the project boundary
            try:
                if actual_store_path is None:
                    raise OSError("Store path resolved to None")
                store_canonical = _canonicalize_path(actual_store_path)
                project_canonical = _canonicalize_path(resolved)
                if not store_canonical.startswith(project_canonical + "/") and store_canonical != project_canonical:
                    all_healthy = False
                    findings.append({
                        "check": "store_config",
                        "status": "unhealthy",
                        "detail": f"Store path '{store_path_val}' resolves to "
                                  f"'{store_canonical}' which is outside the "
                                  f"project boundary '{project_canonical}'",
                    })
                else:
                    safe_store_path = store_canonical
                    findings.append({
                        "check": "store_config",
                        "status": "ok",
                        "detail": f"Store is persistent (path: '{store_path_val}', "
                                  f"resolves to: '{actual_store_path}')",
                    })
            except (OSError, RuntimeError):
                all_healthy = False
                findings.append({
                    "check": "store_config",
                    "status": "unhealthy",
                    "detail": f"Store path '{store_path_val}' cannot be resolved",
                })

    # ── 7. Store directory ───────────────────────────────────────────
    if isinstance(store_config, dict):
        store_dir_path = safe_store_path
        if store_dir_path:
            store_dir = os.path.dirname(store_dir_path)
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

    # ── 8. SQLite integrity (read-only, only if DB exists) ──────────
    db_path_for_check = safe_store_path
    if db_path_for_check and os.path.isfile(db_path_for_check):
        try:
            # Open read-only via URI — no journal, no WAL, no mutation
            abs_path = str(Path(db_path_for_check).resolve())
            db_uri = Path(abs_path).as_uri()
            conn = sqlite3.connect(db_uri + "?mode=ro", uri=True)
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

    return _doctor_output(findings, all_healthy, resolved, json_output)


def _doctor_output(
    findings: list[dict],
    all_healthy: bool,
    resolved: str,
    json_output: bool,
) -> dict:
    """Format and print doctor output."""
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

