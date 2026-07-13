# ThinkOS Product Requirements

## Authority and Evidence

These requirements define durable product intent. Requirements are not implementation proof. Receipts prove work but grant no authority. Marc remains final product authority.

## PR-001 — Native Project State Stewardship

### Invariant

The agent automatically checks and maintains project status. The user does not have to remember to update the project map.

### Required Future Capabilities

- Structured `ProjectState`.
- Registered-project identity.
- Live-versus-recorded derived-fact comparison.
- `CURRENT`, `STALE`, `CONTEXT_GAP`, `DRIFT`, `BLOCKED`, and `UNKNOWN` states.
- Safe derived-fact refresh.
- Governed semantic updates.
- Resume, status, checkpoint, mutation, milestone, handoff, and closeout integration.
- Immutable receipts and machine-readable health.
- Historical-state labeling.
- Fail-closed treatment of contradictory or missing evidence.
- No authority inheritance from state, memory, receipts, or handoffs.
- No user reminder dependency.
- Architecture stewardship evaluation for organ-level work.

### Status

`REQUIREMENT_APPROVED; IMPLEMENTATION_DEFERRED`

Adam OS Project State Steward v0 is a reference implementation, not proof that ThinkOS natively implements PR-001.
