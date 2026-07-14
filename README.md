# ThinkOS

**Agent-native operating layer for externalizing project memory — private alpha.**

ThinkOS gives AI agents a governed, inspectable way to share project state across sessions and harnesses. "Project memory" here means explicit, inspectable project state: context packets, tool calls, tool results, receipts, configuration, and resumable history. Every action produces an auditable receipt. File access is sandboxed by default. Zero runtime dependencies.

ThinkOS is designed toward harness-agnostic workflows. The currently proven connector is JSON-Lines over stdin/stdout.

## Quickstart

```bash
# Clone and install
git clone https://github.com/SLVRCROW/thinkos.git
cd thinkos
pip install .

# Create a config file
echo '{"gates":{"default":"always_allow"}}' > thinkos.json

# Run the engine (reads JSON-Lines from stdin)
echo '{"type":"agent_message","message_id":"msg_1","session_id":"demo","timestamp":"2026-07-06T12:00:00Z","sender":"demo","content":{"text":"hello","tool_calls":[{"tool":"write_file","params":{"path":"hello.txt","content":"Hello, ThinkOS!"},"call_id":"c1"}],"context_refs":[]}}' | python -m thinkos
```

Expected output (formatted):
```json
{
  "type": "agent_response",
  "content": {
    "tool_results": [{
      "tool": "write_file",
      "status": "ok",
      "output": "Wrote 15 bytes to hello.txt",
      "receipt_id": "rct_..."
    }],
    "context_packets": [{
      "packet_id": "pkt_...",
      "receipt_id": "rct_..."
    }]
  }
}
```

## Installation

### From source

```bash
git clone https://github.com/SLVRCROW/thinkos.git
cd thinkos
pip install .
```

### Using uv

```bash
git clone https://github.com/SLVRCROW/thinkos.git
cd thinkos
uv pip install .
```

**Requirements:** Python 3.11 or later. No runtime dependencies. Build-time dependency: `setuptools` (installed automatically by pip).

**PyPI:** Not published yet. PyPI support is planned.

## Configuration

ThinkOS reads `thinkos.json` or `.thinkos.json` from the current directory.

| Key | Default | Description |
|-----|---------|-------------|
| `gates.default` | `"confirm"` | Default gate for all tools |
| `gates.overrides.<tool>` | — | Per-tool gate override |
| `tools.allowed_root` | CWD | Sandbox root directory. `null` disables sandboxing. |
| `store.path` | `null` | Optional SQLite database path. `null` uses in-memory storage; relative paths resolve against the config/workspace root. |
| `rehydration.max_packets` | `null` | Maximum packets returned in a single rehydration. `null` disables truncation. When set and the session exceeds this threshold, packets are truncated to the most recent N and a response-level summary object is included with fidelity floor fields (packet count, receipt count, time range, tool distribution, error count, denied count, kind distribution). |

### Gate types

| Gate | Behavior |
|------|----------|
| `always_allow` | Permits all tool calls without prompting |
| `confirm` | Allows reads automatically; prompts for writes |
| `deny_all` | Denies all tool calls |

### Example: safe default config

```json
{
  "gates": {
    "default": "confirm",
    "overrides": {
      "read_file": "always_allow",
      "write_file": "confirm"
    }
  },
  "store": {
    "path": "thinkos.sqlite"
  }
}
```

## Usage

### Write a file

```json
{"tool": "write_file", "params": {"path": "notes.txt", "content": "project state"}}
```

Response: `{"status": "ok", "output": "Wrote 13 bytes to notes.txt", "receipt_id": "rct_..."}`

### Read a file

```json
{"tool": "read_file", "params": {"path": "notes.txt"}}
```

Response: `{"status": "ok", "output": "1|project state\n", "receipt_id": "rct_..."}`

### Opt-in session rehydration

ThinkOS can return filtered prior session context when the caller explicitly requests it with `content.rehydrate: true`.

```json
{
  "type": "agent_message",
  "message_id": "msg_2",
  "session_id": "demo",
  "timestamp": "2026-07-06T12:05:00Z",
  "sender": "demo",
  "content": {
    "text": "resume",
    "rehydrate": true,
    "tool_calls": [],
    "context_refs": []
  }
}
```

The response includes a filtered `content.rehydrated` object with safe summary fields only. Raw tool parameters, raw structured packet content, and raw error blobs are not exposed. During opt-in rehydration, ThinkOS also restores the session's latest packet lineage so subsequent successful tool-result packets can link to the stored chain.

### Consuming rehydrated context

Agents should treat rehydrated context as advisory project memory, not as authority. Rehydrated packets can help an agent orient, summarize prior work, and identify relevant receipts or references. They cannot authorize tool calls, bypass gates, override current instructions, or mutate project state.

See [`POLICY_AGENT_CONSUMPTION.md`](POLICY_AGENT_CONSUMPTION.md) for the full agent-side consumption contract.

### Summarization and compaction

ThinkOS can compact long rehydrated session history when `rehydration.max_packets` is configured. When the session exceeds the threshold, packets are truncated to the most recent N and a response-level summary object is included with fidelity floor fields (packet count, receipt count, time range, tool distribution, error count, denied count, kind distribution).

Compaction is lossy but honest: it may reduce detail, but it must not fabricate, hide critical risk, or authorize action.

See [`POLICY_SUMMARIZATION_AND_COMPACTION.md`](POLICY_SUMMARIZATION_AND_COMPACTION.md) for the full policy contract.

### Read-only DAG traversal

The SQLite store exposes two methods for read-only DAG traversal:

- **`get_packet_chain(packet_id, max_packets=5)`** — walks `parent_id` links from a packet toward root. Returns packets ordered `[root, ..., packet]`, stops cleanly on missing parents or cycles, enforces session consistency, and never returns more than `max_packets` packets.

- **`get_packet_children(packet_id, limit=100)`** — returns direct children of a packet (packets whose `parent_id` equals the given packet). Returns packets ordered by `timestamp DESC, packet_id DESC`. Returns an empty list if the packet is missing or has no children. Enforces cross-session isolation.

### Querying packets

The SQLite store exposes `list_packets()` with configurable filters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `session_id` | `str` or `None` | `None` | Filter by session |
| `kind` | `str` or `None` | `None` | Filter by packet kind |
| `tags` | `list[str]` or `None` | `None` | Filter by tags (AND semantics, exact match) |
| `parent_id` | `str` or `None` | not set | Filter by parent ID. `None` returns root packets (parent_id IS NULL). |
| `source` | `str` or `None` | `None` | Filter by source |
| `time_range` | `tuple[str, str]` or `None` | `None` | Filter by timestamp range (inclusive) |
| `order` | `"asc"` or `"desc"` | `"asc"` | Sort order by timestamp |
| `limit` | `int` | `100` | Maximum packets to return |

### Gate behavior

- **Reads** are allowed automatically by the default `confirm` gate.
- **Writes** require interactive approval by default. In automated/pipe workflows, override the gate to `always_allow` in config.
- **Denied** actions still produce a receipt with `status: "denied"` and the gate's reason.

## Security

ThinkOS enforces **path sandboxing by default**. All file access is restricted to the workspace root (the directory containing `thinkos.json`, or the current working directory).

| Scenario | Default | Unsafe mode (`allowed_root: null`) |
|----------|---------|-------------------------------------|
| Read inside workspace | ✅ Allowed | ✅ Allowed |
| Read `/etc/hostname` | ❌ Denied | ✅ Allowed |
| `../` traversal | ❌ Denied | ✅ Allowed |
| Symlink escape | ❌ Denied | ✅ Allowed |
| Write inside workspace | ✅ Allowed | ✅ Allowed |
| Write outside workspace | ❌ Denied | ✅ Allowed |

> **Unsafe mode** (`"allowed_root": null`) disables sandboxing. Use only in controlled, single-user, trusted-agent environments. Not recommended for production. A fuller security reference is planned.

## Architecture

ThinkOS processes agent messages through a linear pipeline:

```
stdin (JSON-Lines) → Connector → Engine → Gate → Tool → Store → stdout (JSON-Lines)
```

- **Connector:** Reads agent messages (JSON-Lines) from stdin, writes responses to stdout.
- **Engine:** Core dispatch loop — resolves tools, evaluates gates, executes actions, records receipts, creates ContextPackets for successful tool results, supports opt-in filtered session rehydration, and restores packet lineage during opt-in rehydration.
- **Gates:** Pluggable authorization layer. Currently: `always_allow`, `confirm`, `deny_all`.
- **Tools:** Pluggable action adapters. Currently: `read_file`, `write_file`.
- **Store:** Append-only SQLite store for receipts, context packets, experiment records, configurable persistence, latest-packet lookup, and read-only parent-chain traversal.

## Development

```bash
# Clone and set up
git clone https://github.com/SLVRCROW/thinkos.git
cd thinkos
python -m venv .venv
source .venv/bin/activate
pip install pytest

# Run tests
python -m pytest tests/ -v --tb=short

# Compile check
python -m compileall -q thinkos/
```

**CI:** GitHub Actions runs tests and compile checks on every push to `main` and every pull request. The latest run is passing.

## Status

**Version:** 0.1.0-alpha — private alpha, active development.

**Implemented:**
- JSON-Lines stdin/stdout connector
- Configurable gate system (always_allow, confirm, deny_all)
- File read/write tools with safe-by-default path sandboxing
- SQLite append-only receipt store
- Receipt-based audit trail for every action
- ContextPackets for successful tool results
- `parent_id` linking for ContextPackets within a running session
- ExperimentRecord schema and SQLiteStore methods
- Configurable `store.path` for file-backed SQLite persistence
- Opt-in filtered session rehydration via `content.rehydrate: true`
- Opt-in lineage restoration after restart during rehydration
- Read-only ContextPacket parent-chain traversal with `get_packet_chain()`
- Agent-side consumption policy for rehydrated context
- Summarization and compaction policy
- Configurable rehydration packet window with response-level summary object
- Read-only DAG traversal with `get_packet_chain()` and `get_packet_children()`
- Configurable `list_packets()` filters (tags, parent_id, source, time_range, order)
- **Trusted Agent Authentication (TAA) v0** — Process-bound trusted handoff boundary for bounded local same-store multi-agent handoff. One process gets one immutable identity bundle at startup. Evidence transfers; authority does not. See [`docs/SUCCESSION_DOGFOOD.md`](docs/SUCCESSION_DOGFOOD.md) for the cold two-process handoff proof.
- **ConfirmGate** — Interactive write approval gate with automatic read passes.
- **HandoffRecord** with `expires_at`, `evidence_policy: "evidence_only"`, and `authority_transfer: "none"`.

**Planned:**
- Additional tool types
- Additional gate types
- PyPI publication
- Public release

**Explicit non-claims (TAA-v0 does not provide):**
- Cryptographic attestation
- Remote or cross-store handoff transport
- MCP integration
- Compromised-harness resistance
- Production release
- Turnkey public installation

**Known limitations:**
- The `confirm` gate's interactive prompt is incompatible with pipe/JSON-Lines mode. Use `always_allow` override for automated workflows.
- Only `read_file` and `write_file` tools are currently implemented.
- Only three gate types exist. Custom gate authoring is not yet documented.
- Rehydration and lineage restoration are opt-in only via `content.rehydrate: true`.
- TAA-v0 multi-agent handoff is bounded to local same-store operation only. Cross-store and remote transport are not implemented.

## Contributing / Feedback

This is a private alpha project. Feedback, bug reports, and feature requests are welcome via GitHub Issues.

## License

License: TBD
