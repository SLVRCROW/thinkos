# ThinkOS Specifications — Index

| File | Status | Authority |
|---|---|---|
| `TSR_V0_SPEC_v1.3.md` | **FROZEN v1.3** (2026-08-20) · **CONTROLLING** (adopted prospectively 2026-09-02) | **Authoritative implementation specification** — Thin State Reconciliation v0 (TSR v0), Marc-approved |
| `TSR_V0_SPEC_v1.1.md` | **FROZEN v1.1** (2026-08-20) | Prior committed controlling contract (superseded by v1.3 adoption) |
| `TSR_V0_SPEC_v1.3_DESIGN_DECISION.md` | **Governing design-decision record** (adopted prospectively 2026-09-02) | State-lifecycle adjudication; Alternative 2 is the binding decision |
| `DOCTOR_SIDE_REPAIR_WORK_ORDER_v0.1.md` | **Historical work-order record** (2026-08-20) | Doctor side-effect-free path provenance; see governance note |
| `PR001_AMENDMENT_DRAFT_v0.1.md` | **Historical review evidence** (2026-08-20) | Superseded by application of the PR-001 Scope Note (see `docs/PRODUCT_REQUIREMENTS.md`) |

Note: `TSR_V0_SPEC_v1.2.md` is a superseded uncommitted intermediate artifact, not a previously governed or authoritative record; its "FROZEN" header is a historical self-claim, not governance. It is not listed as a governed spec.

## Governing contract

**TSR v0 is the only currently approved implementation of PR-001** (Native Project State Stewardship). Governing pointer: the **PR-001 Scope Note — Thin State Reconciliation v0 (TSR v0)** in [`docs/PRODUCT_REQUIREMENTS.md`](../PRODUCT_REQUIREMENTS.md).

TSR v0 in one line: one repo-local operational state file (`.thinkos/project-state.json`, gitignored) + one read-only `thinkos status` command; reconciliation states exactly `CURRENT | STALE | UNKNOWN`; STALE is evidence-based (≥1 evaluated recorded probe differs from live reality, no age threshold); five frozen probes (repository presence, HEAD SHA, branch/detached, upstream ref+sha, worktree-dirty); on-demand only; no correction, writeback, intent fields, registry, hooks, new write authority, or performance claims.

## Explicit non-goals (do not infer authorization)

The full automatic/six-state PR-001 design (`CURRENT | STALE | CONTEXT_GAP | DRIFT | BLOCKED | UNKNOWN`, automatic observation, "no user reminder dependency", governed semantic updates, registered-project registry) remains **deferred** and requires: (a) new Marc authorization, (b) new evidence, (c) a separate frozen specification. TSR v0 does not use or inherit that vocabulary.

## Change discipline

- The controlling spec file `TSR_V0_SPEC_v1.3.md` is byte-stable: SHA-256 `52702a420a4c087fde2db6eb0e2af93ea2b6173aca0cb83e2efc5e2ae03f81ce` (as amended by the 2026-09-02 governance note). Any change must be escalated as a spec amendment (v1.4+), never edited in place silently.
- The prior committed controlling contract `TSR_V0_SPEC_v1.1.md` remains byte-stable: SHA-256 `eb15926a63027a295029cb9bde9f8235c40728862cf3fd8c1493f85f29afdf6a`.
- New approved specs should be added here as they are frozen.
