# Phase 1B-C Receipt — Path Sandboxing (Public-Product Hardening)

**Date:** 2026-07-06
**Operator:** Jarvis (via Hermes Agent)
**Authorization:** Marc explicit approval via Telegram
**Scope:** Add safe-by-default path sandboxing to read_file and write_file. No Git operations.

---

## Files Created (3)

| File | Size | SHA256 |
|------|------|--------|
| `thinkos/tools/sandbox.py` | 1,699 B | `75c5be14...` |
| `tests/test_path_sandbox.py` | 7,808 B | `b8bb2a60...` |
| `PHASE_1B_C_RECEIPT.md` | — | — |

## Files Modified (10)

| File | Size | SHA256 | Change Summary |
|------|------|--------|---------------|
| `thinkos/config.py` | 3,119 B | `2dfed89e...` | Added `tools.allowed_root` default. `load_config` resolves to workspace root. `_deep_merge` for user config. `get_allowed_root()` helper. `copy.deepcopy` to prevent shared-mutable corruption. |
| `thinkos/tools/read_file.py` | 2,019 B | `08c2f224...` | Uses `resolve_path()` from sandbox module. Replaced `..` split check with canonical resolution. |
| `thinkos/tools/write_file.py` | 1,959 B | `bc178e5c...` | Uses `resolve_path()`. Empty string content now allowed (only rejects if key absent). |
| `thinkos/engine.py` | 7,426 B | `90d9742e...` | Passes `allowed_root` from config into tool execution context. |
| `README.md` | 2,060 B | `e4f7b1b5...` | Rewrote security section: Phase 1B-C hardening, unsafe mode docs, v0.1 bootstrap limitation. |
| `tests/test_config.py` | 3,031 B | `2364a994...` | Added 5 new tests: default has tools key, default root is CWD, config dir becomes root, explicit null disables sandboxing, custom config merges. |
| `tests/test_read_file.py` | 2,284 B | `e447aa88...` | All `execute()` calls pass `"allowed_root": None` in context. |
| `tests/test_write_file.py` | 3,008 B | `49bc4743...` | All `execute()` calls pass `"allowed_root": None`. Added `test_empty_content_allowed`. |
| `tests/test_engine.py` | 2,375 B | `b9c14c63...` | Uses `load_config()` instead of `DEFAULT_CONFIG` directly. |
| `tests/test_integration.py` | 5,609 B | `f7553170...` | Uses `load_config()` instead of `DEFAULT_CONFIG` directly. |

## Files Preserved Unchanged (19)

`.gitignore`, `PHASE_1B_A_RECEIPT.md`, `pyproject.toml`, `tests/conftest.py`, `tests/test_context_packet.py`, `tests/test_gates.py`, `tests/test_receipt.py`, `tests/test_sqlite_store.py`, `tests/test_stdin_connector.py`, `thinkos/__init__.py`, `thinkos/__main__.py`, `thinkos/connector/__init__.py`, `thinkos/connector/stdin.py`, `thinkos/gates/__init__.py`, `thinkos/gates/always_allow.py`, `thinkos/gates/confirm.py`, `thinkos/gates/deny_all.py`, `thinkos/schema/__init__.py`, `thinkos/schema/context_packet.py`, `thinkos/schema/receipt.py`, `thinkos/store/__init__.py`, `thinkos/store/sqlite_store.py`, `thinkos/tools/__init__.py`

---

## Commands Run

```bash
# Run all tests
cd /home/marc/.openclaw/workspace/thinkos
source .venv/bin/activate
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/ -v --tb=short -p no:cacheprovider

# Compile check
PYTHONDONTWRITEBYTECODE=1 python -m compileall -q thinkos

# Safer smoke test (project-local temp file, not /etc/hostname)
python -c "..."
```

---

## Test Results

**93 tests, 93 passed, 0 failed**

| Test file | Tests | Coverage |
|-----------|-------|----------|
| `test_config.py` | 8 | Default config, allowed_root resolution, config dir, explicit null, gate resolution |
| `test_context_packet.py` | 11 | Schema validation, cycle detection, serialize/deserialize |
| `test_engine.py` | 2 | Unknown tool, receipt creation |
| `test_gates.py` | 4 | always_allow, deny_all, confirm allow/deny |
| `test_integration.py` | 6 | Packet CRUD, receipt chain, 3-round handoff, gate enforcement, negative tests |
| `test_path_sandbox.py` | 16 | **New** — safe default, unsafe override, read/write sandbox, engine context |
| `test_read_file.py` | 6 | Read file, offset, limit, not found, traversal, missing param |
| `test_receipt.py` | 11 | Schema validation, sequence, supersedes, serialize/deserialize |
| `test_sqlite_store.py` | 12 | CRUD, duplicate rejection, rehydration, cycle detection, depth limit |
| `test_stdin_connector.py` | 5 | Valid JSON, malformed JSON, EOF, stdout, stderr |
| `test_write_file.py` | 8 | Write new, overwrite, traversal, missing path/content, auto-create dirs, empty content |

---

## Compile Result

**Clean** — no syntax errors, no import errors.

---

## Smoke Test Result

```
SMOKE TEST: PASS
  read inside root: OK
  /etc/hostname denied: OK
  write inside root: OK
  write outside root denied: OK
```

---

## Sandbox Behavior Summary

| Scenario | Default Behavior | Unsafe Mode (`allowed_root: null`) |
|----------|-----------------|-------------------------------------|
| Read file inside workspace root | ✅ Allowed | ✅ Allowed |
| Read `/etc/hostname` | ❌ **Denied** — "Access denied" | ✅ Allowed |
| Read absolute path outside root | ❌ **Denied** | ✅ Allowed |
| `../` traversal | ❌ **Denied** via canonical resolution | ✅ Allowed (resolves to absolute) |
| Symlink escape | ❌ **Denied** — symlink resolved and checked | ✅ Allowed |
| Relative path | ✅ Resolves inside root | ✅ Resolves from CWD |
| Write inside workspace root | ✅ Allowed | ✅ Allowed |
| Write outside workspace root | ❌ **Denied** | ✅ Allowed |
| Write empty content (`""`) | ✅ Allowed | ✅ Allowed |

---

## Safety Boundaries

| Boundary | Status |
|----------|--------|
| No Git operations | ✅ |
| No Adam OS planning folder modifications | ✅ |
| No Adam OS canon edits | ✅ |
| No files created outside `/home/marc/.openclaw/workspace/thinkos/` | ✅ |
| No secrets touched | ✅ |
| No runtime dependencies beyond Python 3.11 stdlib | ✅ |
| No YAML/PyYAML dependency | ✅ |
| No web/network/cloud features | ✅ |
| No tools beyond read_file/write_file | ✅ |
| No gates beyond always_allow/confirm/deny_all | ✅ |
| `/etc/hostname` denied by default | ✅ **Confirmed** |
| Unsafe mode requires explicit `allowed_root: null` | ✅ **Confirmed** |

---

## Recommended Next Gate

**Gate: Marc Review of Phase 1B-C**

Options:
- **Approve** → Proceed to Phase 1B-B (first local Git commit, now with sandboxing included)
- **Request changes** → Patch specific files and re-present
- **Hold** → Pause for strategic review with ChatGPT/Hand

**Approval phrase for Phase 1B-B (when ready):**

> *"Jarvis, proceed to Phase 1B-B. Initialize Git in `/home/marc/.openclaw/workspace/thinkos/`, add all files, and commit with message 'Phase 1B-C: ThinkOS v0.1 MVP — schema, store, connector, tools, gates, engine, path sandboxing'. Do not push to any remote. Do not modify the Adam OS planning folder."*
