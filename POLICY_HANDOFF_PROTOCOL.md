# Multi-Agent Handoff Protocol

## Status

This policy defines the store-level handoff contract implemented by TM009 v0.
It is a product policy, not an approval artifact and not a runtime execution protocol.

## Core rule

A handoff carries bounded evidence across sessions. It transfers no authority.

Every `HandoffRecord` is permanently constrained to:

```text
evidence_policy = evidence_only
authority_transfer = none
requires_fresh_approval = true
```

A receiving agent must treat the record, its summaries, and every referenced packet or
receipt as untrusted evidence. None of them may replace current instructions, satisfy an
approval gate, authorize a tool call, or expand the receiving agent's scope.

## Store-level contract

TM009 v0 provides an append-only `HandoffRecord` and four store operations:

- write a validated record once;
- read one record by ID;
- list records for an explicit target session and optional target agent;
- resolve only the packet and receipt IDs explicitly named by a record.

The store verifies that every referenced packet and receipt exists and belongs to the
record's source session. Resolution repeats those checks and fails closed if stored
references are missing or cross the source-session boundary. Resolution never walks a
packet DAG and never constructs or injects a prompt.

## Bounds

A record may reference at most 25 packets and 50 receipts. Purpose and omission summaries
are each limited to 2,048 UTF-8 bytes. Tags are limited to 10 entries of at most 64 UTF-8
bytes each. Summaries and tags remain untrusted text even after validation.

`omitted_packet_count` and `omissions_summary` disclose that a transfer is incomplete.
They do not reconstruct omitted evidence or allow a receiver to query beyond the explicit
reference lists.

## Expiry

`expires_at` is advisory in v0. The store validates its timestamp and reports whether the
record is expired, but it does not delete, consume, hide, or deny access to expired records.
Runtime expiry enforcement requires a later engine or connector contract.

## Storage boundary

TM009 v0 supports handoffs within one configured SQLite store. It does not transport
records between databases or establish project identity across stores. Cross-store
transport requires a separately designed protocol.

## Explicit non-capabilities

TM009 v0 does not provide:

- automatic resume or automatic execution;
- approval inheritance or runtime authorization;
- prompt-injection prevention;
- claim, consumption, replay-prevention, update, or deletion state;
- agent notification;
- cryptographic receipt verification or tamper evidence;
- cross-database transport;
- expiry-based access denial.

Any future runtime consumer must preserve the evidence-only invariants, apply fresh approval
rules at the point of action, disclose omissions and expiry, and avoid placing handoff text
into a privileged prompt channel.
