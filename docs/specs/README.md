# ThinkOS Specifications — Index

| File | Status | Authority |
|---|---|---|
| `TSR_V0_SPEC_v1.1.md` | **FROZEN v1.1** (2026-08-20) | **Authoritative implementation specification** — Thin State Reconciliation v0 (TSR v0), Marc-approved |
| `PR001_AMENDMENT_DRAFT_v0.1.md` | **Historical review evidence** (2026-08-20) | Superseded by application of the PR-001 Scope Note (see `docs/PRODUCT_REQUIREMENTS.md`) |

## Governing contract

**TSR v0 is the only currently approved implementation of PR-001** (Native Project State Stewardship). Governing pointer: the **PR-001 Scope Note — Thin State Reconciliation v0 (TSR v0)** in [`docs/PRODUCT_REQUIREMENTS.md`](../PRODUCT_REQUIREMENTS.md).

TSR v0 in one line: one tracked state file (`.thinkos/project-state.json`) + one read-only `thinkos status` command; reconciliation states exactly `CURRENT | STALE | UNKNOWN`; STALE is evidence-based (≥1 evaluated recorded probe differs from live reality, no age threshold); five frozen probes (repository presence, HEAD SHA, branch/detached, upstream ref+sha, worktree-dirty); on-demand only; no correction, writeback, intent fields, registry, hooks, new write authority, or performance claims.

## Explicit non-goals (do not infer authorization)

The full automatic/six-state PR-001 design (`CURRENT | STALE | CONTEXT_GAP | DRIFT | BLOCKED | UNKNOWN`, automatic observation, "no user reminder dependency", governed semantic updates, registered-project registry) remains **deferred** and requires: (a) new Marc authorization, (b) new evidence, (c) a separate frozen specification. TSR v0 does not use or inherit that vocabulary.

## Change discipline

- The frozen spec file `TSR_V0_SPEC_v1.1.md` is byte-stable: SHA-256 `eb15926a63027a295029cb9bde9f8235c40728862cf3fd8c1493f85f29afdf6a`. Any change must be escalated as a spec amendment (v1.2+), never edited in place silently.
- New approved specs should be added here as they are frozen.
