"""Agent-led onboarding — inspect, plan, and apply ThinkOS project setup.

P2 v0 provides a provider-neutral contract through which an agent can:

1. **inspect** — read-only project state classification
2. **plan** — deterministic, idempotent plan generation with SHA-256 binding
3. **apply** — approval-gated execution delegating to P1 init + P1 doctor

Safe defaults are explained rather than asked about:
- reads are automatically allowed inside the project
- writes require approval
- file access is sandboxed to the project
- resumable history is stored locally
"""

import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from thinkos.onboarding import (
    init as _p1_init,
    doctor as _p1_doctor,
    _resolve_project_path,
    _thinkos_dir,
    _config_path,
    _store_path,
    _load_config_file,
    _canonicalize_path,
    _resolve_actual_store_path,
    DEFAULT_CONFIG as P1_DEFAULT_CONFIG,
    THINKOS_DIR,
    CONFIG_FILENAME,
    STORE_FILENAME,
)
from thinkos.schema.context_packet import ContextPacket, validate as validate_packet
from thinkos.schema.receipt import Receipt, Action, Result, validate as validate_receipt
from thinkos.store.sqlite_store import SQLiteStore

# ── Constants ──────────────────────────────────────────────────────────────

CONTRACT_VERSION = "p2.v0"

SAFE_DEFAULTS = {
    "reads": "always_allow",
    "writes": "confirm",
    "sandbox": "project_root",
    "history": "local_persistent",
}

# ── Read-only SQLite helpers ──────────────────────────────────────────────


def _open_ro(store_db_path: str):
    """Open a SQLite database in read-only URI mode.

    Returns (connection, None) on success, (None, error_message) on failure.
    Never creates WAL, SHM, tables, or temp files.
    Rejects paths outside .thinkos/.
    """
    if not os.path.isfile(store_db_path):
        return None, "Store database not found"
    if ".thinkos" not in store_db_path.replace("\\", "/").split("/"):
        return None, "Store path is outside .thinkos/ directory"
    try:
        abs_path = str(Path(store_db_path).resolve())
        db_uri = Path(abs_path).as_uri()
        conn = sqlite3.connect(db_uri + "?mode=ro", uri=True)
        return conn, None
    except (sqlite3.Error, OSError, Exception) as e:
        return None, str(e)


def _close_ro(conn):
    """Safely close a read-only SQLite connection."""
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


# ── Completion evidence validation ────────────────────────────────────────


def _validate_completion_evidence(store_db_path: str) -> bool:
    """Validate that a complete, internally consistent P2 onboarding
    evidence pair exists in the store.

    A decision packet alone does not count as completion.  Requires:
    - packet kind=decision and source=p2_onboarding
    - packet content.contract_version == p2.v0
    - packet content.plan_id is a valid non-empty string
    - session_id matches p2_onboard_{plan_id}
    - referenced receipt exists in the same session
    - receipt action.type == agent_message
    - receipt action.params.approved_plan_id matches the packet plan_id
    - receipt result.status == ok
    - receipt result.packet_ids references the packet

    Strictly read-only.  Never creates or modifies the database.
    """
    conn, err = _open_ro(store_db_path)
    if conn is None:
        return False
    try:
        # Find candidate decision packets
        rows = conn.execute(
            "SELECT packet_id, session_id, content_structured, refs "
            "FROM packets WHERE kind = ? AND source = ? ORDER BY timestamp DESC LIMIT 5",
            ("decision", "p2_onboarding"),
        ).fetchall()
        if not rows:
            return False

        for row in rows:
            packet_id, session_id, content_json, refs_json = row
            if not content_json:
                continue
            try:
                content = json.loads(content_json)
            except (json.JSONDecodeError, TypeError):
                continue

            # Check contract version
            if content.get("contract_version") != CONTRACT_VERSION:
                continue

            # Check plan_id is present and non-empty
            plan_id = content.get("plan_id", "")
            if not isinstance(plan_id, str) or not plan_id:
                continue

            # Check session_id matches p2_onboard_{plan_id}
            expected_session = f"p2_onboard_{plan_id}"
            if session_id != expected_session:
                continue

            # Check refs list is non-empty
            if not refs_json:
                continue
            try:
                refs = json.loads(refs_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(refs, list) or not refs:
                continue

            # Check each referenced receipt
            for receipt_id in refs:
                if not isinstance(receipt_id, str) or not receipt_id.startswith("rct_"):
                    continue
                rrow = conn.execute(
                    "SELECT session_id, action_type, action_params, result_status, result_packet_ids "
                    "FROM receipts WHERE receipt_id = ?", (receipt_id,)
                ).fetchone()
                if rrow is None:
                    continue

                r_session, r_action_type, r_params_json, r_status, r_packet_ids_json = rrow

                # Same session
                if r_session != session_id:
                    continue

                # action.type == agent_message
                if r_action_type != "agent_message":
                    continue

                # result.status == ok
                if r_status != "ok":
                    continue

                # result.packet_ids references this packet
                if not r_packet_ids_json:
                    continue
                try:
                    r_packet_ids = json.loads(r_packet_ids_json)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(r_packet_ids, list) or packet_id not in r_packet_ids:
                    continue

                # action.params.approved_plan_id matches
                if r_params_json:
                    try:
                        r_params = json.loads(r_params_json)
                    except (json.JSONDecodeError, TypeError):
                        r_params = {}
                else:
                    r_params = {}
                if r_params.get("approved_plan_id") == plan_id:
                    return True

        return False
    except (sqlite3.Error, OSError, Exception):
        return False
    finally:
        _close_ro(conn)


# ── State classification ───────────────────────────────────────────────────


def _classify_state(project_path: str) -> dict:
    """Classify project state as empty, healthy, unhealthy, or conflict.

    Strictly read-only. Never creates directories, databases, or files.
    """
    resolved = _canonicalize_path(project_path)
    thinkos_dir_path = os.path.join(resolved, THINKOS_DIR)
    cfg_path = os.path.join(thinkos_dir_path, CONFIG_FILENAME)
    store_db_path = os.path.join(thinkos_dir_path, STORE_FILENAME)

    # Check for legacy config files that would shadow .thinkos/ config
    legacy_conflicts = []
    for legacy_name in ("thinkos.json", ".thinkos.json"):
        legacy_path = os.path.join(resolved, legacy_name)
        if os.path.isfile(legacy_path):
            legacy_conflicts.append(legacy_name)

    # Check for existing .thinkos/ directory
    thinkos_exists = os.path.isdir(thinkos_dir_path) or os.path.islink(thinkos_dir_path)
    if not thinkos_exists:
        if legacy_conflicts:
            return {
                "state": "conflict",
                "project_root": resolved,
                "detail": "Legacy config files present but no .thinkos/ directory",
                "legacy_conflicts": legacy_conflicts,
                "existing_config": None,
                "store_exists": False,
                "p2_complete": False,
            }
        return {
            "state": "empty",
            "project_root": resolved,
            "detail": "No ThinkOS configuration found",
            "legacy_conflicts": [],
            "existing_config": None,
            "store_exists": False,
            "p2_complete": False,
        }

    # .thinkos/ exists — check for symlink
    if os.path.islink(thinkos_dir_path):
        return {
            "state": "conflict",
            "project_root": resolved,
            "detail": ".thinkos/ is a symlink — refusing to operate over symlinks",
            "legacy_conflicts": legacy_conflicts,
            "existing_config": None,
            "store_exists": False,
            "p2_complete": False,
        }

    # Load existing config
    existing_config = _load_config_file(cfg_path)
    if existing_config is None:
        return {
            "state": "conflict",
            "project_root": resolved,
            "detail": ".thinkos/ exists but config is missing or malformed",
            "legacy_conflicts": legacy_conflicts,
            "existing_config": None,
            "store_exists": False,
            "p2_complete": False,
        }

    # Check store
    store_exists = os.path.isfile(store_db_path)

    # Check for P2 completion evidence
    p2_complete = _validate_completion_evidence(store_db_path)

    # Run doctor to determine health
    doctor_result = _p1_doctor(project_path=resolved, json_output=False, quiet=True)
    is_healthy = doctor_result["status"] == "healthy"

    if is_healthy and p2_complete:
        return {
            "state": "healthy",
            "project_root": resolved,
            "detail": "ThinkOS is healthy and P2 onboarding is complete",
            "legacy_conflicts": legacy_conflicts,
            "existing_config": existing_config,
            "store_exists": store_exists,
            "p2_complete": True,
        }
    elif is_healthy and not p2_complete:
        return {
            "state": "healthy",
            "project_root": resolved,
            "detail": "ThinkOS is healthy but P2 onboarding evidence is missing",
            "legacy_conflicts": legacy_conflicts,
            "existing_config": existing_config,
            "store_exists": store_exists,
            "p2_complete": False,
        }
    else:
        return {
            "state": "unhealthy",
            "project_root": resolved,
            "detail": "ThinkOS installation has health issues",
            "legacy_conflicts": legacy_conflicts,
            "existing_config": existing_config,
            "store_exists": store_exists,
            "p2_complete": False,
            "doctor_findings": doctor_result.get("findings", []),
        }


# ── Inspect ────────────────────────────────────────────────────────────────


def inspect(project_path: str | None = None) -> dict:
    """Read-only inspection of project state.

    Args:
        project_path: Path to the project directory. Defaults to CWD.

    Returns:
        Dict with state classification and full project metadata.
    """
    resolved = _canonicalize_path(_resolve_project_path(project_path))

    # Validate the path exists and is a directory
    if not os.path.isdir(resolved):
        return {
            "status": "error",
            "error": f"Project path '{resolved}' does not exist or is not a directory",
        }

    state_info = _classify_state(resolved)

    return {
        "status": "ok",
        "contract_version": CONTRACT_VERSION,
        "project_root": resolved,
        "state": state_info["state"],
        "detail": state_info["detail"],
        "legacy_conflicts": state_info.get("legacy_conflicts", []),
        "existing_config": state_info.get("existing_config"),
        "store_exists": state_info.get("store_exists", False),
        "p2_complete": state_info.get("p2_complete", False),
        "doctor_findings": state_info.get("doctor_findings"),
    }


# ── Plan ───────────────────────────────────────────────────────────────────


def _build_plan_payload(state_info: dict) -> dict:
    """Build the deterministic plan payload for a project.

    This is the portion that gets hashed for plan_id.
    No timestamps or nondeterministic values are included.
    """
    resolved = state_info["project_root"]
    state = state_info["state"]

    effects = []
    blocked_reasons = []
    warnings = []

    if state == "empty":
        effects = [
            {"order": 1, "action": "create_thinkos_directory", "target": os.path.join(resolved, THINKOS_DIR)},
            {"order": 2, "action": "write_config", "target": os.path.join(resolved, THINKOS_DIR, CONFIG_FILENAME)},
            {"order": 3, "action": "write_gitignore", "target": os.path.join(resolved, THINKOS_DIR, ".gitignore")},
            {"order": 4, "action": "create_store", "target": os.path.join(resolved, THINKOS_DIR, STORE_FILENAME)},
            {"order": 5, "action": "verify_health", "description": "Run P1 doctor to verify installation"},
            {"order": 6, "action": "persist_completion_evidence", "description": "Record P2 onboarding receipt and decision packet"},
        ]
    elif state == "healthy" and not state_info.get("p2_complete"):
        effects = [
            {"order": 1, "action": "verify_health", "description": "Run P1 doctor to verify existing installation"},
            {"order": 2, "action": "persist_completion_evidence", "description": "Record P2 onboarding receipt and decision packet"},
        ]
        warnings.append("Existing healthy installation — no configuration changes needed")
    elif state == "healthy" and state_info.get("p2_complete"):
        effects = []
        warnings.append("P2 onboarding is already complete — no action needed")
    elif state == "conflict":
        blocked_reasons.append(state_info.get("detail", "Unknown conflict"))
    elif state == "unhealthy":
        blocked_reasons.append("Existing installation has health issues that must be resolved first")
        if state_info.get("doctor_findings"):
            for f in state_info["doctor_findings"]:
                if f["status"] != "ok":
                    blocked_reasons.append(f"{f['check']}: {f['detail']}")

    return {
        "contract_version": CONTRACT_VERSION,
        "project_root": resolved,
        "observed_state": state,
        "safe_defaults": dict(SAFE_DEFAULTS),
        "ordered_effects": effects,
        "warnings": warnings,
        "blocked_reasons": blocked_reasons,
    }


def _compute_plan_id(payload: dict) -> str:
    """Compute a deterministic plan_id from the plan payload.

    Excludes timestamps and nondeterministic values.
    Repeated planning against unchanged state produces the same plan_id.
    """
    # Create a clean copy with only deterministic fields
    clean = {
        "contract_version": payload["contract_version"],
        "project_root": payload["project_root"],
        "observed_state": payload["observed_state"],
        "safe_defaults": payload["safe_defaults"],
        "ordered_effects": payload["ordered_effects"],
        "warnings": payload["warnings"],
        "blocked_reasons": payload["blocked_reasons"],
    }
    raw = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def plan(project_path: str | None = None) -> dict:
    """Generate a deterministic onboarding plan.

    Strictly read-only. Never mutates project state.

    Args:
        project_path: Path to the project directory. Defaults to CWD.

    Returns:
        Dict with plan_id, contract_version, project_root, observed_state,
        safe_defaults, ordered_effects, warnings, blocked_reasons.
    """
    resolved = _canonicalize_path(_resolve_project_path(project_path))

    if not os.path.isdir(resolved):
        return {
            "status": "error",
            "error": f"Project path '{resolved}' does not exist or is not a directory",
        }

    state_info = _classify_state(resolved)
    payload = _build_plan_payload(state_info)
    plan_id = _compute_plan_id(payload)

    return {
        "status": "ok",
        "plan_id": plan_id,
        "contract_version": payload["contract_version"],
        "project_root": payload["project_root"],
        "observed_state": payload["observed_state"],
        "safe_defaults": payload["safe_defaults"],
        "ordered_effects": payload["ordered_effects"],
        "warnings": payload["warnings"],
        "blocked_reasons": payload["blocked_reasons"],
    }


# ── Apply ──────────────────────────────────────────────────────────────────


def _derive_onboarding_session_id(plan_id: str) -> str:
    """Derive a deterministic onboarding session identifier from the plan_id.

    Uses the complete plan hash so a fresh successor can request rehydration
    without user protocol knowledge.
    """
    return f"p2_onboard_{plan_id}"


def apply(project_path: str | None = None, approved_plan_id: str | None = None,
          json_output: bool = False) -> dict:
    """Apply an approved onboarding plan.

    Requires the exact plan_id from a prior plan() call.
    Reinspects and recomputes the plan before any mutation.
    Delegates initialization to P1 init and health verification to P1 doctor.

    Args:
        project_path: Path to the project directory. Defaults to CWD.
        approved_plan_id: The exact plan_id to approve. Required.
        json_output: If True, suppress P1 human-readable output.

    Returns:
        Dict with status and detailed results.
    """
    resolved = _canonicalize_path(_resolve_project_path(project_path))

    # ── Validate approval ────────────────────────────────────────────
    if not approved_plan_id:
        return {
            "status": "error",
            "error": "Missing --approve-plan. A plan_id is required to apply.",
        }

    if not isinstance(approved_plan_id, str) or not approved_plan_id:
        return {
            "status": "error",
            "error": "Approved plan_id must be a non-empty string",
        }

    # ── Reinspect and recompute plan ──────────────────────────────────
    if not os.path.isdir(resolved):
        return {
            "status": "error",
            "error": f"Project path '{resolved}' does not exist or is not a directory",
        }

    state_info = _classify_state(resolved)
    payload = _build_plan_payload(state_info)
    current_plan_id = _compute_plan_id(payload)

    # ── Reject stale or mismatched approval ───────────────────────────
    if current_plan_id != approved_plan_id:
        return {
            "status": "error",
            "error": (
                f"Approval mismatch: approved plan_id '{approved_plan_id}' "
                f"does not match current plan_id '{current_plan_id}'. "
                "The project state has changed since the plan was generated. "
                "Run 'thinkos onboard plan' again and approve the new plan_id."
            ),
        }

    # ── Reject blocked plans ──────────────────────────────────────────
    if payload["blocked_reasons"]:
        return {
            "status": "blocked",
            "error": "Plan is blocked",
            "blocked_reasons": payload["blocked_reasons"],
            "plan_id": current_plan_id,
        }

    # ── No-op for already-complete ────────────────────────────────────
    if payload["observed_state"] == "healthy" and state_info.get("p2_complete"):
        return {
            "status": "ok",
            "detail": "P2 onboarding is already complete — no action needed",
            "plan_id": current_plan_id,
            "effects_applied": [],
        }

    # ── Execute effects ────────────────────────────────────────────────
    effects_applied = []

    # Effect 1-4: P1 init (for empty projects)
    if payload["observed_state"] == "empty":
        init_result = _p1_init(project_path=resolved, json_output=json_output, quiet=json_output)
        if init_result["status"] == "error":
            return {
                "status": "error",
                "error": f"P1 init failed: {init_result['message']}",
                "plan_id": current_plan_id,
                "effects_applied": effects_applied,
            }
        effects_applied.append({"action": "p1_init", "status": "ok"})

    # Effect: verify health via P1 doctor
    doctor_result = _p1_doctor(project_path=resolved, json_output=json_output, quiet=json_output)
    if doctor_result["status"] != "healthy":
        return {
            "status": "error",
            "error": "P1 doctor reported unhealthy after initialization",
            "plan_id": current_plan_id,
            "effects_applied": effects_applied,
            "doctor_findings": doctor_result.get("findings", []),
        }
    effects_applied.append({"action": "p1_doctor", "status": "healthy"})

    # Effect: persist completion evidence
    evidence_result = _persist_completion_evidence(resolved, current_plan_id, payload)
    if evidence_result["status"] != "ok":
        # Partial failure: P1 installation is healthy but evidence not persisted
        return {
            "status": "partial",
            "error": evidence_result.get("error", "Failed to persist completion evidence"),
            "detail": "P1 installation is healthy. Run 'thinkos onboard plan' again to complete evidence.",
            "plan_id": current_plan_id,
            "effects_applied": effects_applied,
        }
    effects_applied.append({"action": "persist_completion_evidence", "status": "ok"})

    return {
        "status": "ok",
        "detail": "P2 onboarding complete",
        "plan_id": current_plan_id,
        "effects_applied": effects_applied,
    }


def _persist_completion_evidence(project_path: str, plan_id: str, plan_payload: dict) -> dict:
    """Persist P2 onboarding completion evidence as a receipt + context packet pair.

    Uses the existing atomic write_receipt_and_packet operation.
    Uses existing schemas: Receipt (action.type=agent_message) and ContextPacket (kind=decision).
    """
    store_db_path = _store_path(project_path)
    if not os.path.isfile(store_db_path):
        return {"status": "error", "error": "Store database not found"}

    session_id = _derive_onboarding_session_id(plan_id)
    now = datetime.now(timezone.utc).isoformat()

    # Build receipt
    receipt_id = f"rct_{uuid.uuid4()}"
    receipt = Receipt(
        receipt_id=receipt_id,
        session_id=session_id,
        sequence=1,
        timestamp=now,
        action=Action(
            type="agent_message",
            tool=None,
            params={
                "contract_version": CONTRACT_VERSION,
                "approved_plan_id": plan_id,
            },
            agent="p2_onboarding",
        ),
        result=Result(
            status="ok",
            summary=f"P2 onboarding complete for {project_path}",
            packet_ids=[],
        ),
        gate=None,
    )

    # Build context packet
    packet_id = f"ctx_{uuid.uuid4()}"
    packet = ContextPacket(
        packet_id=packet_id,
        session_id=session_id,
        timestamp=now,
        kind="decision",
        source="p2_onboarding",
        content={
            "text": f"P2 onboarding decision for {project_path}",
            "structured": {
                "contract_version": CONTRACT_VERSION,
                "plan_id": plan_id,
                "project_root": plan_payload["project_root"],
                "safe_defaults": plan_payload["safe_defaults"],
                "ordered_effects": plan_payload["ordered_effects"],
                "warnings": plan_payload["warnings"],
            },
        },
        tags=["p2_onboarding", "decision"],
        refs=[receipt_id],
    )

    # Validate both objects
    receipt_errors = validate_receipt(receipt)
    if receipt_errors:
        return {"status": "error", "error": f"Receipt validation failed: {'; '.join(receipt_errors)}"}

    packet_errors = validate_packet(packet)
    if packet_errors:
        return {"status": "error", "error": f"Packet validation failed: {'; '.join(packet_errors)}"}

    # Link receipt to packet
    receipt.result.packet_ids = [packet_id]

    # Atomic write
    try:
        store = SQLiteStore(store_db_path)
        store.write_receipt_and_packet(receipt, packet)
        store.close()
    except Exception as e:
        return {"status": "error", "error": f"Failed to persist evidence: {e}"}

    return {
        "status": "ok",
        "receipt_id": receipt_id,
        "packet_id": packet_id,
        "session_id": session_id,
    }


# ── Rehydration helper ────────────────────────────────────────────────────


def rehydrate_onboarding(project_path: str | None = None) -> dict:
    """Rehydrate P2 onboarding evidence from a fresh process.

    Strictly read-only. Uses SQLite URI mode=ro.
    Never creates or modifies the database.

    Uses the deterministic session ID derived from the plan to find
    the completion evidence without user protocol knowledge.

    Args:
        project_path: Path to the project directory. Defaults to CWD.

    Returns:
        Dict with onboarding evidence or error.
    """
    resolved = _canonicalize_path(_resolve_project_path(project_path))
    store_db_path = _store_path(resolved)

    conn, err = _open_ro(store_db_path)
    if conn is None:
        return {"status": "error", "error": err or "Store database not found"}

    try:
        # Find candidate decision packets
        rows = conn.execute(
            "SELECT packet_id, session_id, timestamp, kind, source, "
            "content_text, content_structured, tags, refs "
            "FROM packets WHERE kind = ? AND source = ? ORDER BY timestamp DESC LIMIT 5",
            ("decision", "p2_onboarding"),
        ).fetchall()

        if not rows:
            return {"status": "error", "error": "No P2 onboarding evidence found in store"}

        packets_out = []
        receipts_out = []

        for row in rows:
            packet_id, session_id, timestamp, kind, source, content_text, content_json, tags_json, refs_json = row

            # Parse content
            content = {"text": content_text, "structured": None}
            if content_json:
                try:
                    content["structured"] = json.loads(content_json)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Parse tags
            tags = []
            if tags_json:
                try:
                    tags = json.loads(tags_json)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Parse refs
            refs = []
            if refs_json:
                try:
                    refs = json.loads(refs_json)
                except (json.JSONDecodeError, TypeError):
                    pass

            packets_out.append({
                "packet_id": packet_id,
                "session_id": session_id,
                "timestamp": timestamp,
                "kind": kind,
                "source": source,
                "content": content,
                "tags": tags,
                "refs": refs,
            })

            # Look up referenced receipts
            for ref in refs:
                if not isinstance(ref, str) or not ref.startswith("rct_"):
                    continue
                rrow = conn.execute(
                    "SELECT receipt_id, session_id, timestamp, action_type, action_params, result_status "
                    "FROM receipts WHERE receipt_id = ?", (ref,)
                ).fetchone()
                if rrow is None:
                    continue
                r_params = None
                if rrow[4]:
                    try:
                        r_params = json.loads(rrow[4])
                    except (json.JSONDecodeError, TypeError):
                        pass
                receipts_out.append({
                    "receipt_id": rrow[0],
                    "session_id": rrow[1],
                    "timestamp": rrow[2],
                    "action_type": rrow[3],
                    "action_params": r_params,
                    "result_status": rrow[5],
                })

        return {
            "status": "ok",
            "packets": packets_out,
            "receipts": receipts_out,
        }
    except (sqlite3.Error, OSError, Exception) as e:
        return {"status": "error", "error": f"Failed to rehydrate onboarding evidence: {e}"}
    finally:
        _close_ro(conn)
