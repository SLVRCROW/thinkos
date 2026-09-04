# PR-001 AMENDMENT — DRAFT v0.1 (for Marc review — NOT YET APPLIED)

**Status:** HISTORICAL REVIEW EVIDENCE · 2026-08-20 · SUPERSEDED BY APPLICATION (see below)
**Application status:** The PR-001 Scope Note from this draft was **applied and committed** to `docs/PRODUCT_REQUIREMENTS.md` on 2026-08-20 under Marc authorization (TSR v0 documentation/governance gate closure). This file is retained as historical review evidence; the live governing text is the **PR-001 Scope Note — Thin State Reconciliation v0 (TSR v0)** in `docs/PRODUCT_REQUIREMENTS.md`.
**Target:** `docs/PRODUCT_REQUIREMENTS.md` — PR-001 (Native Project State Stewardship)
**Purpose:** Resolve the §12 prerequisite of TSR v0 Spec v1.1: remove the conflict between
PR-001's automatic/six-state design and the approved Thin State Reconciliation v0 contract.
**Scope of this draft:** documentation/governance change only. No product code, schema,
database, CLI, tests, or runtime changes. No new authority. No scope expansion.

---

## 1. The conflict (verbatim, current PR-001)

> **PR-001 — Native Project State Stewardship**
> Invariant: "The agent automatically checks and maintains project status. The user does
> not have to remember to update the project map."
> Required Future Capabilities include:
> - Structured `ProjectState`.
> - Registered-project identity.
> - Live-versus-recorded derived-fact comparison.
> - `CURRENT`, `STALE`, `CONTEXT_GAP`, `DRIFT`, `BLOCKED`, and `UNKNOWN` states.
> - Safe derived-fact refresh.
> - Governed semantic updates.
> ...
> Status: `REQUIREMENT_APPROVED; IMPLEMENTATION_DEFERRED`

If read literally, PR-001 authorizes: automatic observation, a six-state vocabulary,
a registry, semantic updates — none of which is in the Marc-approved Thin State
Reconciliation (TSR) v0 contract, and much of which VS-1 R4 did not support
(NO_MEANINGFUL_ADVANTAGE for verified-state representation sophistication).

## 2. The approved v0 contract (what this amendment scopes)

Per TSR Spec v1.1 (docs/specs/TSR_V0_SPEC_v1.1.md), Marc-approved THIN STATE
RECONCILIATION v0:

- One tracked state file (`.thinkos/project-state.json`) + one read-only command
  (`thinkos status`).
- Reconciliation states: **CURRENT | STALE | UNKNOWN** (three, not six).
- STALE = at least one successfully evaluated recorded probe differs from live reality.
  No age threshold. No time-based staleness.
- Five probes only: repository presence, HEAD SHA, branch/detached, upstream ref+sha,
  worktree-dirty. Probe set is frozen for v0.
- **On-demand only.** No automatic observation, no background maintenance, no "no user
  reminder dependency".
- No correction, writeback, intent inference, objective/checkpoint/next-action fields.
- No new database objects, packet kinds, registry, hooks, handoff changes, authority
  changes, or performance claims.
- `.thinkos/project-state.json` receives **no special write authority**. Changes only
  manually or via already-approved existing write mechanisms.

## 3. Proposed amendment text (adds a "v0 scope note" to PR-001)

Add the following block to `docs/PRODUCT_REQUIREMENTS.md` under PR-001, before the
"Status" line, without deleting or rewriting any existing requirement text:

> **SCOPE NOTE — THIN STATE RECONCILIATION v0 (TSR v0) (Marc-approved, 2026-08-20):**
> The only currently approved implementation of PR-001 is the Thin State Reconciliation
> v0 contract in `docs/specs/TSR_V0_SPEC_v1.1.md` (frozen). TSR v0 deliberately does
> **not** implement the full PR-001 surface: it is on-demand (never automatic), uses
> three states (`CURRENT | STALE | UNKNOWN`), performs no correction, no governed
> semantic updates, no registered-project registry, no automatic observation, and
> grants no write authority beyond existing gates. The six-state vocabulary
> (`CONTEXT_GAP | DRIFT | BLOCKED`) and the "agent automatically checks and maintains
> project status / no user reminder dependency" invariant remain **deferred goals of
> the full PR-001**, NOT requirements of TSR v0. Future implementations of the full
> PR-001 (automatic observation, registry, semantic updates, six states) require:
> (a) new Marc authorization; (b) new evidence (VS-1 R4 provides no support for
> representation-sophistication machinery); (c) a separate frozen specification. TSR
> v0 does not authorize, imply, or depend on any of that surface.

## 4. What this amendment does NOT change

- Does not delete or rewrite any existing PR-001 requirement text.
- Does not implement TSR v0 (implementation remains a separate, Marc-authorized gate).
- Does not expand scope: it narrows the *reading* of the current requirement to the
  approved contract, and states clearly that the full form is deferred and
  evidence-gated.
- Does not change authority: no state-derived authority, no new write mechanism.

## 5. What this amendment is FOR

- A future implementer (or agent) reading `PRODUCT_REQUIREMENTS.md` cannot mistake
  the automatic/six-state PR-001 design for the approved v0 contract.
- The frozen spec becomes the single authoritative reference for TSR v0 (fixing the
  conversation-dependent state problem).
- The R4 evidence posture ("complexity must earn promotion") is preserved in the
  requirement text.

## 6. Open review questions for Marc

1. Is the **three-state vs six-state** scoping wording correct (v0 = three; six-state
   remains deferred, not deleted)?
2. Should the scope note also explicitly state the **five frozen probes** (recommended:
   yes, mirroring the spec) or reference the spec only?
3. Where should the note live: inline under PR-001 (recommended), or as a separate
   `docs/specs/PR001_V0_SCOPE_NOTE.md` referenced from PR-001?
4. Should the amendment be applied by editing `docs/PRODUCT_REQUIREMENTS.md` directly
   (Marc approves the diff), or as a new standalone note file plus a one-line pointer?

## 7. What happens next (if Marc approves the amendment)

1. Apply the approved diff to `docs/PRODUCT_REQUIREMENTS.md` (only the scope-note
   block; nothing else).
2. Commit/push per the existing approval gates (Marc-controlled git mutation).
3. Re-run Lane E/Codex (or equivalent) against the amended PRD + frozen spec to confirm
   no new ambiguity — then, and only then, implementation becomes authorized on a
   separate work order.
