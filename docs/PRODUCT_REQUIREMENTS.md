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

### Scope Note — Thin State Reconciliation v0 (TSR v0)

**Marc-approved, 2026-08-20; controlling contract adopted prospectively 2026-09-02.** The only currently approved implementation of PR-001 is the **Thin State Reconciliation v0 (TSR v0)** contract, frozen in `docs/specs/TSR_V0_SPEC_v1.3.md` (adopted prospectively by Marc on 2026-09-02 as the controlling contract for Alpha 0.2 Patch 1; v1.1 remains the prior committed controlling contract). TSR v0 deliberately implements a **subset** of PR-001 and **does not** implement the full PR-001 surface:

- Reconciliation states are exactly **`CURRENT | STALE | UNKNOWN`** — a three-state set. TSR v0 **does not** use or inherit the superseded six-state vocabulary (`CURRENT | STALE | CONTEXT_GAP | DRIFT | BLOCKED | UNKNOWN`); those additional states remain **deferred goals of the full PR-001**, not requirements of TSR v0.
- Reconciliation is **on-demand** (read-only `thinkos status`). TSR v0 does **not** automatically check or maintain project status, and the "no user reminder dependency" invariant is **not** satisfied by TSR v0.
- TSR v0 performs **no** correction, writeback, intent inference, objective/checkpoint/next-action fields, registered-project registry, governed semantic updates, or new write authority. It adds no database objects, packet kinds, hooks, or handoff changes. It makes no performance claims.
- STALE is evidence-based only: at least one successfully evaluated recorded probe differs from live reality. **No age threshold.**

The full PR-001 automatic/six-state design remains **deferred** and requires: (a) new Marc authorization, (b) new evidence (VS-1 R4 provides no support for representation-sophistication machinery), and (c) a separate frozen specification. Nothing in TSR v0 authorizes, implies, or depends on that surface.

### Status

`REQUIREMENT_APPROVED; IMPLEMENTATION_DEFERRED`

Adam OS Project State Steward v0 is a reference implementation, not proof that ThinkOS natively implements PR-001. See `docs/specs/TSR_V0_SPEC_v1.3.md` for the approved TSR v0 implementation contract.
