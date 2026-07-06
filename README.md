# ThinkOS

**Agent-native, harness-agnostic operating layer for externalizing project memory.**

ThinkOS gives every AI agent you use — regardless of harness — governed access to the same project memory, so context survives tool switches and your work compounds across sessions.

## Quickstart

```bash
# Run the engine (reads JSON-Lines from stdin)
echo '{"type":"agent_message","message_id":"msg_001","session_id":"sess_test","timestamp":"2026-07-06T12:00:00Z","sender":"test","content":{"text":"hello","tool_calls":[],"context_refs":[]}}' | python -m thinkos
```

## Status

**Phase:** 1B-C — Path sandboxing (public-product hardening)
**Code:** Python 3.11, stdlib only
**Git:** Not initialized

## Security

### Phase 1B-C: Path Sandboxing (Public-Product Hardening)

ThinkOS enforces **path sandboxing by default**. All file access via
`read_file` and `write_file` is restricted to the ThinkOS workspace root
(the directory containing the config file, or the current working directory).

- Absolute paths outside the workspace root are **denied**.
- `../` traversal is **denied** via canonical path resolution.
- Symlink escapes are **denied** — symlinks are resolved and checked.
- Relative paths resolve **inside** the workspace root.

This is a mandatory safety default for a public product. It was added in
Phase 1B-C; earlier bootstrap phases (1B-A) did not have sandboxing.

### Unsafe Mode (Developer Override)

To disable sandboxing, set `"allowed_root": null` in your `thinkos.json`:

```json
{"tools": {"allowed_root": null}}
```

This is **not recommended for production**. It re-enables the v0.1 bootstrap
behavior where any path on the filesystem is accessible. Use only in
controlled, single-user, trusted-agent environments.

### v0.1 Bootstrap Limitation (Phase 1B-A)

The initial implementation (Phase 1B-A) had no path sandboxing — any
absolute path was accessible. This was acceptable for internal bootstrap
but is **not** the public-product behavior. Phase 1B-C replaces that
behavior with the safe default described above.
