# ThinkOS Succession Dogfood v0 — Cold Two-Process Handoff Proof

## 1. Purpose

This document proves that ThinkOS's TAA (Trusted Agent Authentication) v0 process-bound handoff boundary works across independent process lifetimes. A cold successor — someone with only this repository and its documentation — can install, configure, and verify the workflow without any private context from Marc, Jarvis, or Hand.

Institutional succession. Not PID identity.

## 2. What TAA-v0 Proves

- One process receives one **immutable** principal/session/namespace identity at startup.
- Identity comes from a **complete trusted launcher or configuration bundle** (environment variables or config dict).
- **PID is not authorization.** The process-bound identity uses env-var-driven principal, session, namespace, issuer, and TTL — not the operating system process ID.
- Source or target may **read** handoff metadata.
- Only the **target session** may **resolve** (recover evidence packets and receipts).
- **Evidence transfers; authority does not.** The handoff record carries `evidence_policy: "evidence_only"`, `authority_transfer: "none"`, and `requires_fresh_approval: True`.
- Caller-supplied privileged identity fields are **rejected**.
- Unrelated sessions and wrong namespaces are **denied** with a generic unavailable response.
- Expired and unverified contexts are **denied**.

## 3. Explicit Non-Claims

TAA-v0 does **not** provide:

- **Cryptographic attestation** — the process-bound boundary is enforced within the local OS, not by digital signatures.
- **Remote transport** — handoffs are local same-store only.
- **Cross-store operation** — Process A and Process B must share the same SQLite store.
- **MCP integration** — not in scope.
- **Compromised-harness resistance** — TAA does not resist a compromised launcher or harness that controls the environment variables.
- **Production readiness** — this is a private alpha proof.
- **Turnkey public installation** — PyPI publication has not occurred.

## 4. Installation

```bash
# From the repository root
git clone https://github.com/SLVRCROW/thinkos.git
cd thinkos
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .
```

**Prerequisites:** Python 3.11 or later. No runtime dependencies. Build-time dependency: `setuptools` (installed automatically by pip). Development dependency: `pytest`.

**Verification:**

```bash
python -B -c "import thinkos; print('OK')"
python -m pytest tests/test_succession_dogfood.py -v --tb=short
```

## 5. Durable-Store Configuration

ThinkOS reads `thinkos.json` or `.thinkos.json` from the current working directory. For the succession proof, you need a file-backed SQLite store and TAA enabled:

```json
{
  "store": {
    "path": "succession_store.sqlite"
  },
  "gates": {
    "default": "always_allow",
    "overrides": {
      "write_file": "always_allow",
      "read_file": "always_allow"
    }
  },
  "taa": {
    "enabled": true,
    "namespace": "succession-test",
    "policy_version": "1"
  }
}
```

## 6. Complete Trusted Identity Bundles

Identity is provided through environment variables. Every process needs all five:

| Variable | Description | Example |
|---|---|---|
| `THINKOS_PRINCIPAL` | Agent/human identifier | `agent-a` |
| `THINKOS_SESSION_ID` | Unique session identifier | `session-source` |
| `THINKOS_NAMESPACE` | Namespace for isolation | `succession-test` |
| `THINKOS_ISSUER` | Issuer of this identity bundle | `succession-test-launcher` |
| `THINKOS_TTL_SECONDS` | Time-to-live in seconds | `3600` |

**Process A (source):**

```bash
export THINKOS_PRINCIPAL=agent-a
export THINKOS_SESSION_ID=session-source
export THINKOS_NAMESPACE=succession-test
export THINKOS_ISSUER=succession-test-launcher
export THINKOS_TTL_SECONDS=3600
```

**Process B (target):**

```bash
export THINKOS_PRINCIPAL=agent-b
export THINKOS_SESSION_ID=session-target
export THINKOS_NAMESPACE=succession-test
export THINKOS_ISSUER=succession-test-launcher
export THINKOS_TTL_SECONDS=3600
```

## 7. Process A Workflow

Process A writes a known marker to a file, reads it back to create evidence, then creates a handoff referencing the read operation's packet and receipt.

**Step 1 — Write the marker** (agent_message with write_file):

```json
{"type":"agent_message","message_id":"msg_ev_1","session_id":"session-source","timestamp":"2026-07-14T12:00:00Z","sender":"test","content":{"text":"create evidence","tool_calls":[{"tool":"write_file","params":{"path":"/tmp/note.txt","content":"succession-dogfood-v0-source-marker"},"call_id":"c1"}],"context_refs":[]}}
```

**Step 2 — Read the file** (agent_message with read_file):

```json
{"type":"agent_message","message_id":"msg_ev_2","session_id":"session-source","timestamp":"2026-07-14T12:00:00Z","sender":"test","content":{"text":"read evidence","tool_calls":[{"tool":"read_file","params":{"path":"/tmp/note.txt"},"call_id":"c2"}],"context_refs":[]}}
```

The read_file tool returns the file contents in its tool output. The engine creates a ContextPacket whose summary contains the returned content. This is the evidence that will be transferred.

**Step 3 — Create handoff** (handoff_create referencing the read packet and read receipt):

```json
{"type":"handoff_create","message_id":"msg_hc_1","session_id":"session-source","timestamp":"2026-07-14T12:00:00Z","handoff":{"target_session_id":"session-target","target_agent":"agent-b","purpose_summary":"Succession dogfood v0","packet_ids":["ctx_<uuid>"],"receipt_ids":["rct_<uuid>"]}}
```

Process A exits after receiving the `handoff_id` (e.g., `hof_<uuid>`) from the handoff result.

## 8. Process B Workflow

Process B starts independently, configured with the **target** identity and the **same** durable store.

**Step 1 — List handoffs** for the target session:

```json
{"type":"handoff_list","message_id":"msg_hl_1","session_id":"session-target","timestamp":"2026-07-14T12:00:00Z","target_session_id":"session-target"}
```

Expected response includes the handoff created by Process A.

**Step 2 — Read the handoff:**

```json
{"type":"handoff_read","message_id":"msg_hr_1","session_id":"session-target","timestamp":"2026-07-14T12:00:00Z","handoff_id":"hof_<uuid>"}
```

Expected response includes the handoff record metadata (packet_ids, receipt_ids, purpose, etc.).

**Step 3 — Resolve the handoff:**

```json
{"type":"handoff_resolve","message_id":"msg_hres_1","session_id":"session-target","timestamp":"2026-07-14T12:00:00Z","handoff_id":"hof_<uuid>"}
```

Expected response includes:
- `source_principal`: `agent-a`
- `packets`: projected packet data (ID, kind, and summary capped at 500 characters — not full serialized ContextPacket objects)
- `receipts`: projected receipt data (ID, result status, and tool — not full serialized Receipt objects)

## 9. Exact Continuation Marker

Process B derives an exact deterministic continuation marker from the resolved evidence. The marker is constructed exclusively from values extracted from Process B's resolved response — not from class constants, packet IDs, or handoff metadata.

**Extraction steps:**

1. Locate the resolved packet by the exact read packet ID transferred from Process A.
2. Extract the packet's projected `summary` field.
3. Verify it begins with the expected read-tool prefix (`Tool 'read_file' completed:`).
4. Parse the content after the first `|` delimiter (the line-number separator in read_file output).
5. The parsed value is the **extracted content marker**.
6. Locate the resolved receipt by the exact read receipt ID transferred from Process A.
7. Extract the receipt's `status` field.

**Construction:**

```
SUCCESSOR_CONTINUED:<extracted-content-marker>:<resolved-receipt-status>
```

**Expected value:**

```
SUCCESSOR_CONTINUED:succession-dogfood-v0-source-marker:ok
```

**What each field represents:**

| Field | Source | Description |
|---|---|---|
| Packet ID | Resolved packet `id` | Reference identity — proves the correct packet was transferred |
| Packet summary | Resolved packet `summary` | Projected evidence content — contains the read_file tool output |
| Extracted marker | Parsed from summary after `\|` | The actual file content written by Process A |
| Receipt ID | Resolved receipt `id` | Reference identity — proves the correct receipt was transferred |
| Receipt status | Resolved receipt `status` | The result status of Process A's read_file call |

The test fails if:
- The resolved packet is missing or belongs to another packet.
- The packet summary contains different content.
- The receipt is missing or its status differs.

## 10. Denial Behavior

| Scenario | Operation | Result | Coverage |
|---|---|---|---|
| Unrelated session read | `handoff_read` | `{"status":"unavailable","handoff_id":null,...}` | `test_unrelated_session_read` (new) |
| Unrelated session resolve | `handoff_resolve` | `{"status":"unavailable","handoff_id":null,...}` | `test_unrelated_session_resolve` (new) |
| Unrelated session list | `handoff_list` | `{"status":"unavailable","handoff_id":null,...}` | `test_unrelated_session_list` (new) |
| Wrong namespace | `handoff_read` | `{"status":"unavailable","handoff_id":null,...}` | `test_wrong_namespace_read` (new) |
| Source session resolve | `handoff_resolve` | `{"status":"unavailable","handoff_id":null,...}` | `test_source_session_read_allowed_resolve_denied` (new) |
| Caller privileged-field injection | `handoff_create` | `{"status":"unavailable","handoff_id":null,...}` | `test_caller_privileged_field_injection` (new) |
| Unverified context | any handoff | Process init failure (exit code 1) | `test_unverified_context_startup_failure` (new) |
| Expired context | any handoff | Policy-level denial | `test_handoff_policy.py::TestAuthorizeCreate::test_expired_denied` (existing) |
| Legacy handoff (no envelope) | read/resolve | Store-level denial | `test_store_handoff_auth.py::TestStoreReadHandoffAuth::test_legacy_record_denied` (existing) |
| Store failure (startup) | any handoff | Engine fails at startup, no handoff response | `test_store_startup_failure` (new) |
| Store failure (contained) | any handoff | Generic unavailable, engine continues | `test_connector_handoff_messages.py::TestEngineHandoffContainment::test_injected_store_exception_contained_engine_loop_continues` (new) |

All denial responses use the same generic unavailable shape — no internal reason is distinguishable from the public response.

**Runtime containment test details:**
- The test invokes `Engine.run()` with a capture connector and two queued `handoff_read` messages.
- The first request encounters an injected `RuntimeError("SIMULATED_STORE_FAILURE")` from `store.read_envelope`.
- The engine returns the exact six-key generic unavailable response with no exception detail.
- The same engine loop processes the second request successfully.
- The simulated failure is proven to fire exactly once.

**Startup failure tests** prove only that the engine fails closed at startup (exit code 1, no handoff response emitted). They do not prove generic unavailable response or internal-detail suppression — those properties are proven by the service-layer and engine-loop tests.

## 11. Automated Test Commands

### Source-tree mode (default)

```bash
cd /path/to/thinkos
python -m pytest tests/test_succession_dogfood.py -v --tb=short
```

### Installed-package mode

```bash
cd /path/to/thinkos
pip install .
THINKOS_DOGFOOD_USE_INSTALLED=1 python -m pytest tests/test_succession_dogfood.py -v --tb=short
```

This mode proves the succession workflow works against a clean pip-installed ThinkOS. The subprocesses resolve `thinkos` from the active environment's installed package rather than injecting the repository into PYTHONPATH.

**Installed-package verification:** The evaluation must use a non-editable wheel installation. After installation, `thinkos.__file__` must resolve inside the fresh venv's `site-packages` directory, not inside the repository:

```bash
python -I -c "import thinkos; print(thinkos.__file__)"
# Expected: /path/to/venv/lib/python3.12/site-packages/thinkos/__init__.py
```

### Expected output (both modes)

```
tests/test_succession_dogfood.py::TestSuccessionDogfoodPositive::test_process_a_creates_handoff_then_b_resumes PASSED
tests/test_succession_dogfood.py::TestSuccessionDogfoodDenials::test_unrelated_session_read PASSED
tests/test_succession_dogfood.py::TestSuccessionDogfoodDenials::test_unrelated_session_resolve PASSED
tests/test_succession_dogfood.py::TestSuccessionDogfoodDenials::test_unrelated_session_list PASSED
tests/test_succession_dogfood.py::TestSuccessionDogfoodDenials::test_wrong_namespace_read PASSED
tests/test_succession_dogfood.py::TestSuccessionDogfoodDenials::test_source_session_read_allowed_resolve_denied PASSED
tests/test_succession_dogfood.py::TestSuccessionDogfoodDenials::test_caller_privileged_field_injection PASSED
tests/test_succession_dogfood.py::TestSuccessionDogfoodDenials::test_unverified_context_startup_failure PASSED
tests/test_succession_dogfood.py::TestSuccessionDogfoodDenials::test_store_startup_failure PASSED

9 passed in ~12s
```

## 12. Expected Results

- **9 tests pass** — positive flow + 8 denial/store-failure boundaries
- **0 tests skip** — expired context and legacy handoff are covered by existing unit tests
- All tests complete within 30 seconds
- No network access required
- No AdamOS or Hermes coupling

## 13. Troubleshooting

### If a governance or security control blocks a command

Stop and escalate. Do not use alternate command construction, shell variables, aliases, or invocation paths to bypass the control. A blocked command means the policy requires review — not a workaround.

### Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `TAA initialization failed` | Missing or incomplete env vars | Set all five `THINKOS_*` environment variables |
| `store not found` | Wrong working directory | Run from the directory containing `thinkos.json` |
| Handoff create returns unavailable | TAA disabled in config | Set `"taa": {"enabled": true}` |
| Handoff list returns empty | Different store path between process A and B | Use the same file-backed store path |
| Resolve returns empty packets | No packet_ids in handoff_create | Include packet_ids from prior tool calls |
| Tests fail with timeouts | Subprocess timeout too low | Increase `timeout` parameter in `_run_thinkos` |

## 14. PASS/PARTIAL/FAIL Rubric

### PASS

- All 9 active tests pass
- Continuation marker derived from transferred evidence content (extracted from resolved packet summary, not from class constants or packet IDs)
- Every denial returns only the exact 6-key shape with no distinguishable reason
- A cold successor with no chat history can reproduce the workflow from this document
- No AdamOS or Hermes runtime code is imported or referenced
- Full test suite (including pre-existing tests) passes

### PARTIAL

- Core flow (list/read/resolve) passes but one denial test fails with wrong assertion shape (not a security leak)
- Cold successor can reproduce the flow but needs to infer one configuration step not documented
- Full test suite passes but a non-security validation step fails due to infrastructure

### FAIL

- Core flow fails (Process B cannot read/resolve Process A's handoff)
- Any denial test that should deny instead allows the operation
- Tests depend on AdamOS or Hermes runtime code
- Cold successor cannot reproduce the workflow from documentation alone
- Any test leaks internal store details in error messages
- Tests, import, or compilation fail
- Product security code was modified

## 15. Authority Warning

**Evidence transfers; authority does not.**

- A handoff record carries `evidence_policy: "evidence_only"` and `authority_transfer: "none"`.
- The target session receives evidence packets and receipts — not permission to act as the source.
- `requires_fresh_approval: True` means the target must obtain its own fresh approval before any authority-gated operation.
- Receiving a handoff does not grant access to the source session's identity, permits, or credentials.
- The handoff is an evidence bridge, not a delegation chain.

## 16. Independent Cold-Successor Evaluation

**EVALUATOR PACKET PREPARED.**
**FEATURE COMMIT EXISTS REMOTELY: `a25ca9d3eef12c9915991c5e28f6d01b72e39d0c`**

A later correction commit may supersede `a25ca9d`; the evaluator must use the final reviewed branch HEAD supplied after this pass.

### Repository and Branch

- **Canonical base commit:** `aab57045d51632c534bdbe2ae70ee78d923c8a4b`
- **Current feature commit:** `a25ca9d3eef12c9915991c5e28f6d01b72e39d0c`
- **Branch:** `codex/succession-dogfood-v0`

The evaluator must verify that the feature commit descends from the canonical base. It must not expect branch HEAD to equal the base commit after implementation is committed.

### Evaluator Workflow

The evaluator workflow must begin with clean installation from the committed branch — not PYTHONPATH.

1. Clone the repository and check out the feature branch.
2. Verify the feature commit descends from `aab57045...`.
3. Read `docs/SUCCESSION_DOGFOOD.md`.
4. Build a wheel: `python -m build --wheel --outdir /tmp/wheel`
5. Create a fresh evaluation venv: `python -m venv /tmp/eval-venv`
6. Install the wheel non-editably: `/tmp/eval-venv/bin/pip install /tmp/wheel/thinkos-*.whl`
7. Verify the module path is in site-packages:
   `/tmp/eval-venv/bin/python -I -c "import thinkos; print(thinkos.__file__)"`
8. Run the succession suite with installed-package mode:
   `THINKOS_DOGFOOD_USE_INSTALLED=1 /tmp/eval-venv/bin/python -m pytest tests/test_succession_dogfood.py -v --tb=short`
9. Run import, compilation, and full-suite validation:
   `/tmp/eval-venv/bin/python -B -c "import thinkos; print('OK')"`
   `/tmp/eval-venv/bin/python -m compileall -q thinkos/`
   `/tmp/eval-venv/bin/python -m pytest tests/ -q`

### Prohibited Context

The independent evaluator must NOT receive:
- This conversation transcript
- Jarvis's session history
- Hand's review
- Any private explanation from Marc

### Required Evidence Return Format

```json
{
  "evaluator": "<name or ID>",
  "repository": "SLVRCROW/thinkos",
  "branch": "codex/succession-dogfood-v0",
  "base_commit": "aab57045...",
  "feature_commit": "<commit SHA>",
  "descends_from_base": true,
  "installed_module_path": "/path/to/venv/lib/python3.*/site-packages/thinkos/__init__.py",
  "succession_tests": {
    "collected": 9,
    "passed": 9,
    "failed": 0
  },
  "full_suite": {
    "collected": 486,
    "passed": 486,
    "failed": 0
  },
  "import": "OK",
  "compilation": "OK",
  "cold_successor_reproducible": true/false,
  "documentation_troubleshooting_governance_note_present": true/false,
  "notes": "<any observations>"
}
```

### Status

**INDEPENDENT WORKER-DEATH EVALUATION NOT YET PERFORMED.**
