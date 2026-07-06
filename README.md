# ThinkOS

**Agent-native, harness-agnostic operating layer for externalizing project memory.**

ThinkOS gives every AI agent you use — regardless of harness — governed access to the same project memory, so context survives tool switches and your work compounds across sessions.

## Quickstart

```bash
# Run the engine (reads JSON-Lines from stdin)
echo '{"type":"agent_message","message_id":"msg_001","session_id":"sess_test","timestamp":"2026-07-06T12:00:00Z","sender":"test","content":{"text":"hello","tool_calls":[],"context_refs":[]}}' | python -m thinkos
```

## Status

**Phase:** 1B-A — First code slice (core engine, schema, store, connector, tools, gates)
**Code:** Python 3.11, stdlib only
**Git:** Not initialized

## Security (v0.1)

ThinkOS assumes **trusted agents**. `read_file` and `write_file` have no path sandboxing beyond `..` traversal rejection. Absolute paths are allowed. Do not expose ThinkOS to untrusted agents in v0.1.
