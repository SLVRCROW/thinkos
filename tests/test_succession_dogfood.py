"""Subprocess integration proof: cold two-process handoff succession.

Process A creates evidence via write_file + read_file, then creates a handoff
referencing the read_file's packet and receipt, then terminates.
Process B starts independently and lists/reads/resolves the evidence.
The continuation marker is extracted from the resolved packet's summary field,
which contains the file content returned by read_file.

Denial tests cover unrelated session, wrong namespace, source-resolve denial,
caller identity injection, unverified context, and store startup failure.

Harness-neutral: no AdamOS or Hermes imports.

Installed-package mode:
  Set THINKOS_DOGFOOD_USE_INSTALLED=1 to run subprocesses from the installed
  package rather than injecting the repository into PYTHONPATH. This proves
  the succession workflow works against a clean pip-installed ThinkOS.
"""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_THINKOS_PKG = str(_REPO_ROOT)

# ── Installed-package mode ────────────────────────────────────────────────

_USE_INSTALLED = os.environ.get("THINKOS_DOGFOOD_USE_INSTALLED") == "1"


def _run_thinkos(config_path: str, stdin_lines: list[dict],
                 env: dict | None = None,
                 timeout: int = 15,
                 cwd: str | None = None) -> tuple[str, str, int]:
    """Run python -m thinkos as a subprocess with JSON-Lines stdin.

    Returns (stdout, stderr, returncode).
    """
    input_bytes = "\n".join(json.dumps(m, separators=(",", ":")) for m in stdin_lines).encode()
    if not input_bytes.endswith(b"\n"):
        input_bytes += b"\n"

    cmd = [sys.executable, "-m", "thinkos"]
    merged_env = os.environ.copy()
    merged_env["PYTHONDONTWRITEBYTECODE"] = "1"
    merged_env["THINKOS_QUIET"] = "1"
    if not _USE_INSTALLED:
        merged_env["PYTHONPATH"] = f"{_THINKOS_PKG}:{merged_env.get('PYTHONPATH', '')}"
    if env:
        merged_env.update(env)

    proc = subprocess.run(
        cmd,
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        cwd=cwd or str(_REPO_ROOT),
        env=merged_env,
    )
    return proc.stdout.decode(), proc.stderr.decode(), proc.returncode


def _parse_stdout(stdout: str) -> list[dict]:
    """Parse newline-delimited JSON from stdout."""
    results = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        results.append(json.loads(line))
    return results


def _find_handoff_id(stdout_lines: list[dict]) -> str | None:
    """Extract handoff_id from the first successful create response."""
    for r in stdout_lines:
        if r.get("type") == "handoff_result" and r.get("status") == "ok":
            return r.get("handoff_id")
    return None


def _msg(session_id: str = "default", message_id: str | None = None,
         tool: str | None = None, params: dict | None = None) -> dict:
    """Build an agent_message JSON-Lines message with optional tool call."""
    m = {
        "type": "agent_message",
        "message_id": message_id or f"msg_{uuid.uuid4().hex[:8]}",
        "session_id": session_id,
        "timestamp": "2026-07-14T12:00:00Z",
        "sender": "test",
        "content": {
            "text": "test message",
            "tool_calls": [],
            "context_refs": [],
        },
    }
    if tool:
        m["content"]["tool_calls"] = [
            {"tool": tool, "params": params or {}, "call_id": "c1"}
        ]
    return m


def _handoff_create_msg(session_id: str,
                        target_session_id: str,
                        target_agent: str = "",
                        purpose: str = "succession test",
                        packet_ids: list[str] | None = None,
                        receipt_ids: list[str] | None = None) -> dict:
    return {
        "type": "handoff_create",
        "message_id": f"msg_{uuid.uuid4().hex[:8]}",
        "session_id": session_id,
        "timestamp": "2026-07-14T12:00:00Z",
        "handoff": {
            "target_session_id": target_session_id,
            "target_agent": target_agent,
            "purpose_summary": purpose,
            "packet_ids": packet_ids or [],
            "receipt_ids": receipt_ids or [],
        },
    }


def _handoff_list_msg(session_id: str, target_session_id: str) -> dict:
    return {
        "type": "handoff_list",
        "message_id": f"msg_{uuid.uuid4().hex[:8]}",
        "session_id": session_id,
        "timestamp": "2026-07-14T12:00:00Z",
        "target_session_id": target_session_id,
    }


def _handoff_read_msg(session_id: str, handoff_id: str) -> dict:
    return {
        "type": "handoff_read",
        "message_id": f"msg_{uuid.uuid4().hex[:8]}",
        "session_id": session_id,
        "timestamp": "2026-07-14T12:00:00Z",
        "handoff_id": handoff_id,
    }


def _handoff_resolve_msg(session_id: str, handoff_id: str) -> dict:
    return {
        "type": "handoff_resolve",
        "message_id": f"msg_{uuid.uuid4().hex[:8]}",
        "session_id": session_id,
        "timestamp": "2026-07-14T12:00:00Z",
        "handoff_id": handoff_id,
    }


# ── Assertion helper for denial responses ─────────────────────────────────


def _assert_handoff_result_denial(response: dict):
    """Assert the exact public denial contract at the handoff_result layer.

    The engine wraps every handoff response in:
      {"type": "handoff_result", "in_response_to": ..., **service_result}

    The service_result for denials is:
      {"status": "unavailable", "handoff_id": null,
       "audit_id": null, "audit_status": null}

    The merged public response therefore has exactly these 6 keys:
      type, in_response_to, status, handoff_id, audit_id, audit_status

    This assertion checks exact key equality and exact values.
    """
    EXPECTED_KEYS = {
        "type", "in_response_to", "status",
        "handoff_id", "audit_id", "audit_status",
    }
    assert set(response.keys()) == EXPECTED_KEYS, (
        f"Expected keys {EXPECTED_KEYS}, got {set(response.keys())}"
    )
    assert response["type"] == "handoff_result"
    assert response["in_response_to"] != "", "in_response_to must be non-empty"
    assert response["status"] == "unavailable"
    assert response["handoff_id"] is None
    assert response["audit_id"] is None
    assert response["audit_status"] is None


# ── Helpers for config and identity ───────────────────────────────────────


def _write_config(path: str, overrides: dict | None = None) -> str:
    """Write a thinkos config file and return its path."""
    config = {
        "store": {"path": ":memory:"},
        "gates": {"default": "always_allow", "overrides": {}},
        "taa": {"enabled": False},
    }
    if overrides:
        config.update(overrides)
    config.setdefault("taa", {})
    config["taa"].setdefault("enabled", False)
    config["taa"].setdefault("policy_version", "1")
    with open(path, "w") as f:
        json.dump(config, f)
    return path


def _env_for(principal: str, session_id: str,
             namespace: str = "succession-test",
             issuer: str = "succession-test-launcher",
             ttl: str = "3600") -> dict:
    return {
        "THINKOS_PRINCIPAL": principal,
        "THINKOS_SESSION_ID": session_id,
        "THINKOS_NAMESPACE": namespace,
        "THINKOS_ISSUER": issuer,
        "THINKOS_TTL_SECONDS": ttl,
    }


def _config_with_store(store_path: str,
                       namespace: str = "succession-test") -> dict:
    return {
        "store": {"path": store_path},
        "gates": {
            "default": "always_allow",
            "overrides": {"write_file": "always_allow", "read_file": "always_allow"},
        },
        "taa": {
            "enabled": True,
            "namespace": namespace,
            "policy_version": "1",
        },
    }


# ── Positive flow test ─────────────────────────────────────────────────────


class TestSuccessionDogfoodPositive:
    """Process A creates evidence + handoff → terminates → Process B resumes.

    The continuation marker is extracted from the resolved packet's summary
    field, which contains the file content returned by read_file. The test
    fails if the resolved packet summary is absent, empty, replaced, or
    belongs to another packet.
    """

    _KNOWN_MARKER = "succession-dogfood-v0-source-marker"

    @pytest.fixture
    def tmp_dir(self):
        with tempfile.TemporaryDirectory(prefix="thinkos-sd-") as d:
            yield d

    def test_process_a_creates_handoff_then_b_resumes(self, tmp_dir):
        """Full succession: A creates evidence+handoff, B reads and resolves."""
        store_path = os.path.join(tmp_dir, "test_store.sqlite")
        config_path = os.path.join(tmp_dir, "thinkos.json")
        _write_config(config_path, _config_with_store(store_path))

        note_path = os.path.join(tmp_dir, "note.txt")

        # ── Process A, Step 1: Write the marker to a file ──────────────────
        a_env = _env_for("agent-a", "session-source")
        a1_stdin = [
            _msg(session_id="session-source",
                 tool="write_file",
                 params={"path": note_path, "content": self._KNOWN_MARKER}),
        ]
        a1_stdout, a1_stderr, a1_rc = _run_thinkos(
            config_path, a1_stdin, env=a_env, cwd=tmp_dir)
        assert a1_rc == 0, f"Process A step 1 failed:\nSTDERR:\n{a1_stderr}"

        # ── Process A, Step 2: Read the file to create evidence with
        #    the marker in the tool output (and thus in the packet summary) ─
        a2_stdin = [
            _msg(session_id="session-source",
                 tool="read_file",
                 params={"path": note_path}),
        ]
        a2_stdout, a2_stderr, a2_rc = _run_thinkos(
            config_path, a2_stdin, env=a_env, cwd=tmp_dir)
        assert a2_rc == 0, f"Process A step 2 failed:\nSTDERR:\n{a2_stderr}"

        a2_results = _parse_stdout(a2_stdout)
        assert len(a2_results) >= 1

        msg2_content = a2_results[0].get("content", {})
        a_packet_ids = msg2_content.get("context_packets", [])
        a_receipt_ids = msg2_content.get("receipts", [])
        assert len(a_packet_ids) >= 1, (
            f"Expected >=1 packet from read_file\n"
            f"Results: {json.dumps(a2_results, indent=2)}"
        )

        # ── Process A, Step 3: Create handoff referencing the read evidence ─
        a3_stdin = [
            _handoff_create_msg(
                session_id="session-source",
                target_session_id="session-target",
                target_agent="agent-b",
                purpose="Succession dogfood v0: cold two-process handoff proof",
                packet_ids=a_packet_ids,
                receipt_ids=a_receipt_ids,
            ),
        ]
        a3_stdout, a3_stderr, a3_rc = _run_thinkos(
            config_path, a3_stdin, env=a_env, cwd=tmp_dir)
        assert a3_rc == 0, f"Process A step 3 failed:\nSTDERR:\n{a3_stderr}"

        a3_results = _parse_stdout(a3_stdout)
        handoff_id = _find_handoff_id(a3_results)
        assert handoff_id is not None, (
            f"No handoff_id in Process A step 3.\n"
            f"Results: {json.dumps(a3_results, indent=2)}\n"
            f"STDERR: {a3_stderr}"
        )

        # ── Process B: same store, different identity ──────────────────────
        b_env = _env_for("agent-b", "session-target")
        b_stdin = [
            _handoff_list_msg(session_id="session-target",
                              target_session_id="session-target"),
            _handoff_read_msg(session_id="session-target",
                              handoff_id=handoff_id),
            _handoff_resolve_msg(session_id="session-target",
                                 handoff_id=handoff_id),
        ]
        b_stdout, b_stderr, b_rc = _run_thinkos(
            config_path, b_stdin, env=b_env, cwd=tmp_dir)
        assert b_rc == 0, f"Process B failed:\nSTDERR:\n{b_stderr}"

        b_results = _parse_stdout(b_stdout)
        assert len(b_results) >= 3, (
            f"Expected >=3 responses from B, got {len(b_results)}\n"
            f"Results: {json.dumps(b_results, indent=2)}\n"
            f"STDERR: {b_stderr}"
        )

        # ── Verify list ────────────────────────────────────────────────────
        list_resp = b_results[0]
        assert list_resp.get("type") == "handoff_result"
        assert list_resp.get("status") == "ok", f"list failed: {list_resp}"
        handoffs = list_resp.get("handoffs", [])
        assert len(handoffs) >= 1, f"Expected >=1 handoff in list, got {handoffs}"
        listed_ids = [h["handoff_id"] for h in handoffs]
        assert handoff_id in listed_ids, (
            f"Listed handoffs {listed_ids} do not contain {handoff_id}"
        )

        # ── Verify read ────────────────────────────────────────────────────
        read_resp = b_results[1]
        assert read_resp.get("type") == "handoff_result"
        assert read_resp.get("status") == "ok", f"read failed: {read_resp}"
        read_handoff = read_resp.get("handoff", {})
        assert read_handoff.get("handoff_id") == handoff_id
        assert read_handoff.get("target_session_id") == "session-target"
        assert read_handoff.get("target_agent") == "agent-b"

        # ── Verify resolve ─────────────────────────────────────────────────
        resolve_resp = b_results[2]
        assert resolve_resp.get("type") == "handoff_result"
        assert resolve_resp.get("status") == "ok", f"resolve failed: {resolve_resp}"
        assert resolve_resp.get("handoff_id") == handoff_id
        assert resolve_resp.get("source_principal") == "agent-a"

        packets = resolve_resp.get("packets", [])
        assert len(packets) >= 1, f"Expected >=1 packet in resolve, got {packets}"

        receipts = resolve_resp.get("receipts", [])
        assert len(receipts) >= 1, f"Expected >=1 receipt in resolve, got {receipts}"

        # ── Evidence-derived continuation marker ────────────────────────────
        # Locate the resolved packet by the exact read packet ID.
        # Extract the summary from that packet.
        # Verify it begins with the expected read-tool prefix.
        # Extract the content marker from that summary.
        # Assert the extracted marker equals _KNOWN_MARKER.
        # Locate the receipt by the exact read receipt ID.
        # Extract its status.
        # Construct: SUCCESSOR_CONTINUED:<extracted-content-marker>:<resolved-receipt-status>
        # Assert exactly: SUCCESSOR_CONTINUED:succession-dogfood-v0-source-marker:ok
        #
        # The continuation expression must not contain _KNOWN_MARKER, the
        # packet ID, or a hardcoded marker substring. Those values may appear
        # only in independent expected-value assertions.
        # The test must fail if:
        #   - the resolved packet is missing;
        #   - the wrong packet is returned;
        #   - its summary contains different content;
        #   - the receipt is missing;
        #   - the receipt status differs.

        # 1. Locate the packet by the exact read packet ID
        resolved_packet = None
        for p in packets:
            if p["id"] == a_packet_ids[0]:
                resolved_packet = p
                break
        assert resolved_packet is not None, (
            f"Packet {a_packet_ids[0]} not found in resolved packets.\n"
            f"Resolved IDs: {[p['id'] for p in packets]}"
        )

        # 2. Extract the summary from that packet
        raw_summary = resolved_packet.get("summary", "")
        assert raw_summary != "", "Resolved packet summary is empty"

        # 3. Verify it begins with the expected read-tool prefix
        assert raw_summary.startswith("Tool 'read_file' completed:"), (
            f"Resolved packet summary does not start with read_file prefix: "
            f"{raw_summary!r}"
        )

        # 4. Extract the content marker from that summary.
        #    The engine produces: "Tool 'read_file' completed: <line_num>|<content>"
        #    The marker is the file content after the "<line_num>|" prefix.
        pipe_prefix = "|"
        pipe_pos = raw_summary.find(pipe_prefix)
        assert pipe_pos >= 0, (
            f"Could not find pipe separator in summary: {raw_summary!r}"
        )
        extracted_marker = raw_summary[pipe_pos + len(pipe_prefix):].strip()
        assert extracted_marker != "", "Extracted marker is empty"

        # 5. Assert the extracted marker equals _KNOWN_MARKER
        assert extracted_marker == self._KNOWN_MARKER, (
            f"Extracted marker {extracted_marker!r} does not match "
            f"expected {self._KNOWN_MARKER!r}"
        )

        # 6. Locate the receipt by the exact read receipt ID
        resolved_receipt = None
        for r in receipts:
            if r["id"] == a_receipt_ids[0]:
                resolved_receipt = r
                break
        assert resolved_receipt is not None, (
            f"Receipt {a_receipt_ids[0]} not found in resolved receipts.\n"
            f"Resolved IDs: {[r['id'] for r in receipts]}"
        )

        # 7. Extract its status
        receipt_status = resolved_receipt.get("status", "")
        assert receipt_status == "ok", (
            f"Resolved receipt status '{receipt_status}' expected 'ok'"
        )

        # 8. Construct the continuation marker exclusively from extracted values
        #    The continuation expression must not contain _KNOWN_MARKER, the
        #    packet ID, or a hardcoded marker substring.
        continuation = f"SUCCESSOR_CONTINUED:{extracted_marker}:{receipt_status}"

        # 9. Assert exactly the expected value
        assert continuation == "SUCCESSOR_CONTINUED:succession-dogfood-v0-source-marker:ok", (
            f"Continuation marker mismatch: {continuation!r}"
        )


# ── Denial tests ──────────────────────────────────────────────────────────


class TestSuccessionDogfoodDenials:
    """Boundary coverage for handoff authorization.

    Every denial returns the same exact 6-key shape at the handoff_result
    layer. Assertions use _assert_handoff_result_denial() which checks
    exact key equality and exact values.
    """

    @pytest.fixture
    def tmp_dir(self):
        with tempfile.TemporaryDirectory(prefix="thinkos-sddeny-") as d:
            yield d

    def _create_handoff(self, store_path: str, config_path: str,
                        target_session: str = "session-target",
                        cwd: str | None = None) -> str:
        """Create a handoff and return its ID."""
        env = _env_for("agent-a", "session-a")
        stdin = [
            _handoff_create_msg(
                session_id="session-a",
                target_session_id=target_session,
                target_agent="agent-b",
                purpose="denial test fixture",
            ),
        ]
        stdout, stderr, rc = _run_thinkos(config_path, stdin, env=env,
                                           cwd=cwd)
        assert rc == 0, f"Fixture creation failed:\n{stderr}"
        results = _parse_stdout(stdout)
        h_id = _find_handoff_id(results)
        assert h_id is not None, f"No handoff in fixture:\n{results}"
        return h_id

    # ── 1. Unrelated session: read ─────────────────────────────────────────

    def test_unrelated_session_read(self, tmp_dir):
        """Boundary: unrelated session read."""
        store_path = os.path.join(tmp_dir, "store.sqlite")
        config_path = os.path.join(tmp_dir, "thinkos.json")
        _write_config(config_path, _config_with_store(store_path))

        handoff_id = self._create_handoff(store_path, config_path, cwd=tmp_dir)

        env = _env_for("agent-c", "session-unrelated")
        stdin = [_handoff_read_msg(session_id="session-unrelated",
                                   handoff_id=handoff_id)]
        stdout, stderr, rc = _run_thinkos(config_path, stdin, env=env,
                                          cwd=tmp_dir)
        assert rc == 0, f"Unrelated read:\n{stderr}"
        results = _parse_stdout(stdout)
        assert len(results) >= 1
        _assert_handoff_result_denial(results[0])

    # ── 2. Unrelated session: resolve ──────────────────────────────────────

    def test_unrelated_session_resolve(self, tmp_dir):
        """Boundary: unrelated session resolve."""
        store_path = os.path.join(tmp_dir, "store.sqlite")
        config_path = os.path.join(tmp_dir, "thinkos.json")
        _write_config(config_path, _config_with_store(store_path))

        handoff_id = self._create_handoff(store_path, config_path, cwd=tmp_dir)

        env = _env_for("agent-c", "session-unrelated")
        stdin = [_handoff_resolve_msg(session_id="session-unrelated",
                                      handoff_id=handoff_id)]
        stdout, stderr, rc = _run_thinkos(config_path, stdin, env=env,
                                          cwd=tmp_dir)
        assert rc == 0, f"Unrelated resolve:\n{stderr}"
        results = _parse_stdout(stdout)
        assert len(results) >= 1
        _assert_handoff_result_denial(results[0])

    # ── 3. Unrelated session: list ─────────────────────────────────────────

    def test_unrelated_session_list(self, tmp_dir):
        """Boundary: unrelated session list."""
        store_path = os.path.join(tmp_dir, "store.sqlite")
        config_path = os.path.join(tmp_dir, "thinkos.json")
        _write_config(config_path, _config_with_store(store_path))

        env = _env_for("agent-c", "session-unrelated")
        stdin = [_handoff_list_msg(session_id="session-unrelated",
                                   target_session_id="session-target")]
        stdout, stderr, rc = _run_thinkos(config_path, stdin, env=env,
                                          cwd=tmp_dir)
        assert rc == 0
        results = _parse_stdout(stdout)
        assert len(results) >= 1
        _assert_handoff_result_denial(results[0])

    # ── 4. Wrong namespace: read ───────────────────────────────────────────

    def test_wrong_namespace_read(self, tmp_dir):
        """Boundary: correct target session but wrong namespace."""
        store_path = os.path.join(tmp_dir, "store.sqlite")
        config_path = os.path.join(tmp_dir, "thinkos.json")
        _write_config(config_path, _config_with_store(store_path))

        handoff_id = self._create_handoff(store_path, config_path, cwd=tmp_dir)

        env = _env_for("agent-b", "session-target", namespace="other-namespace")
        stdin = [_handoff_read_msg(session_id="session-target",
                                   handoff_id=handoff_id)]
        stdout, stderr, rc = _run_thinkos(config_path, stdin, env=env,
                                          cwd=tmp_dir)
        assert rc == 0
        results = _parse_stdout(stdout)
        assert len(results) >= 1
        _assert_handoff_result_denial(results[0])

    # ── 5. Source resolve (read allowed, resolve denied) ───────────────────

    def test_source_session_read_allowed_resolve_denied(self, tmp_dir):
        """Boundary: source session can read but not resolve."""
        store_path = os.path.join(tmp_dir, "store.sqlite")
        config_path = os.path.join(tmp_dir, "thinkos.json")
        _write_config(config_path, _config_with_store(store_path))

        handoff_id = self._create_handoff(store_path, config_path, cwd=tmp_dir)

        env = _env_for("agent-a", "session-a")
        read_stdin = [_handoff_read_msg(session_id="session-a",
                                        handoff_id=handoff_id)]
        stdout, stderr, rc = _run_thinkos(config_path, read_stdin, env=env,
                                          cwd=tmp_dir)
        assert rc == 0
        results = _parse_stdout(stdout)
        assert len(results) >= 1
        assert results[0].get("status") == "ok"

        resolve_stdin = [_handoff_resolve_msg(session_id="session-a",
                                              handoff_id=handoff_id)]
        stdout2, stderr2, rc2 = _run_thinkos(config_path, resolve_stdin,
                                              env=env, cwd=tmp_dir)
        assert rc2 == 0
        results2 = _parse_stdout(stdout2)
        assert len(results2) >= 1
        _assert_handoff_result_denial(results2[0])

    # ── 6. Caller privileged-field injection ───────────────────────────────

    def test_caller_privileged_field_injection(self, tmp_dir):
        """Boundary: caller supplies prohibited privileged fields in create."""
        store_path = os.path.join(tmp_dir, "store.sqlite")
        config_path = os.path.join(tmp_dir, "thinkos.json")
        _write_config(config_path, _config_with_store(store_path))

        env = _env_for("agent-a", "session-a")
        injection_msg = {
            "type": "handoff_create",
            "message_id": "msg_inject",
            "session_id": "session-a",
            "timestamp": "2026-07-14T12:00:00Z",
            "principal": "impostor",
            "issuer": "impostor",
            "namespace": "impostor",
            "store_namespace": "impostor",
            "source_session_id": "session-impostor",
            "source_agent": "impostor",
            "handoff": {
                "target_session_id": "session-target",
                "target_agent": "agent-b",
                "purpose_summary": "injection attempt",
                "packet_ids": [],
                "receipt_ids": [],
            },
        }
        stdin = [injection_msg]
        stdout, stderr, rc = _run_thinkos(config_path, stdin, env=env,
                                          cwd=tmp_dir)
        assert rc == 0
        results = _parse_stdout(stdout)
        assert len(results) >= 1
        _assert_handoff_result_denial(results[0])

    # ── 7. Unverified context (startup failure) ──────────────────────────

    def test_unverified_context_startup_failure(self, tmp_dir):
        """Boundary: no identity bundle → process init failure.
        Proves fail-closed at startup: no handoff operation can proceed
        without a verified context. Does NOT prove a generic unavailable
        response or internal-detail suppression.
        """
        store_path = os.path.join(tmp_dir, "store.sqlite")
        config_path = os.path.join(tmp_dir, "thinkos.json")
        _write_config(config_path, _config_with_store(store_path))

        stdin = [_handoff_create_msg(session_id="anon",
                                     target_session_id="session-target")]
        stdout, stderr, rc = _run_thinkos(config_path, stdin, env={},
                                          cwd=tmp_dir)
        assert rc != 0, "Expected TAA init failure for unverified context"
        assert "TAA initialization failed" in stderr

    # ── 8. Store startup failure ─────────────────────────────────────────

    def test_store_startup_failure(self, tmp_dir):
        """Boundary: invalid store path → process init failure.
        Proves fail-closed at startup: no handoff operation can proceed
        when the store cannot be initialized. Does NOT prove a generic
        unavailable response or internal-detail suppression.
        """
        store_path = os.path.join(tmp_dir, "nonexistent", "store.sqlite")
        config_path = os.path.join(tmp_dir, "thinkos.json")
        _write_config(config_path, _config_with_store(store_path))

        env = _env_for("agent-a", "session-a")
        stdin = [_handoff_create_msg(session_id="session-a",
                                     target_session_id="session-target")]
        stdout, stderr, rc = _run_thinkos(config_path, stdin, env=env,
                                          cwd=tmp_dir)
        assert rc != 0, "Expected engine failure for invalid store path"
        assert stderr.strip() != ""
