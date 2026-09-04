# TSR V1.3 DESIGN DECISION — STATE-LIFECYCLE ADJUDICATION

**Status:** DRAFT DESIGN DECISION for Marc review · 2026-08-20 · **NO commit authorized** (historically accurate for 2026-08-20)
**Governance note (2026-09-02):** Adopted by Marc as the governing design-decision record for TSR v0, prospectively. The "NO commit authorized" label is superseded for the purpose of this record by the 2026-09-02 adoption; it remains historically accurate for the 2026-08-20 state.
**Controlling context:** TSR v0 containment repair + lifecycle adjudication (Marc order 2026-08-20, Phase 4)
**Verdict this document supports:** `PASS_AB_REPAIRED_C_REQUIRES_SPEC_V1_3`

---

## 1. Confirmed contradiction (Phase 3 empirical result)

A Git-tracked `project-state.json` records a HEAD SHA, but committing or updating that same state file changes the worktree dirtiness or the HEAD SHA itself. The lifecycle transcript proves the loop:

```
T0  git init + commit                    → clean, HEAD=S0
T1  thinkos init                         → .thinkos/ untracked (thinkos.json + inner .gitignore)
T2  record state {head:S0, dirty:true}   → status CURRENT (honest dirty)     [CURRENT reachable]
T3  commit .thinkos/ (state + config)    → HEAD=S1; state still says S0; dirty flips false
T4  status                               → STALE (head_sha S0≠S1, dirty true≠false)
T5  re-record {head:S0, dirty:false}     → state file modified → live dirty true again
T6  commit state update                  → HEAD=S2; state says S0 → STALE (head only)
T7  re-record {head:S2, dirty:false}+commit → HEAD=S3; state says S2 → STALE (head only)
T8  3× status                            → STALE forever (exit 1)
```

**The four adjudication questions, answered:**

| Question | Answer |
|---|---|
| Is CURRENT reachable without hiding a contractually tracked state surface? | **NO** (v1.2 contract). CURRENT is reachable only by recording `dirty:true` against an uncommitted `.thinkos/` (T2) — a state that is not the committed reality — or momentarily after a commit before any state update (and then the next state update breaks it again). |
| After a legitimate state update, can CURRENT remain reachable? | **NO.** Updating the tracked state file makes the worktree dirty (T5); committing the update moves HEAD (T6). Every legitimate state write is self-invalidating. |
| After committing `project-state.json`, does HEAD necessarily invalidate the SHA stored inside it? | **YES — proven.** The state file's own commit changes HEAD (T3, T6, T7). `head_sha` can never equal the live HEAD while the file containing it is a tracked file whose commit is the HEAD. |
| Does the current specification therefore contain a lifecycle contradiction? | **YES — confirmed.** The tracked-state-object design cannot satisfy both §2 (tracked by design) and §3/§6 (head_sha + worktree_dirty as live-truth probes). The probes are observational; a tracked state object is self-referential. |

---

## 2. Alternatives (smallest first)

### ALTERNATIVE 2 — project-state.json is repo-local operational state (RECOMMENDED)

Move `project-state.json` out of Git tracking: the nested `.thinkos/.gitignore` (already written by `thinkos init`) additionally ignores `project-state.json`. The state file becomes the **local operational record** of the last recorded live truth — exactly what a reconciliation command should compare against. Runtime config (`thinkos.json`, inner `.gitignore`) is committed once as normal project config. No init/tracking/HEAD/worktree-semantics changes to probes.

**Empirically validated (full lifecycle, real init):** CURRENT stable rc=0 across T1–T4; state updates never require commits; porcelain stays clean; no self-reference.

- CURRENT reachable? **YES — stably** (validated).
- State survives agent/repo succession? **Partial — by explicit continuity/export mechanism** (v1.3 adds: `status --export` or doc'd copy of `project-state.json` for a successor; the file itself is not in Git history).
- Status strictly observational? **YES — unchanged.**
- Complexity added: **≈0** (one ignore line; probe semantics untouched).
- Git semantics: state file absent from history; config remains tracked; `git status` clean.
- Migration impact: existing tracked state files become untracked-and-ignored on next `init`/manual update; one-time re-record. Documented.
- Falsification test: lifecycle regression (init → record → commit work → re-record → status CURRENT ×3, porcelain clean).
- Conflicts with existing TSR clauses: **§2 "tracked by design" and §9 t14 (guardrail) must be amended in v1.3.** The guardrail test's intent (state file must not be accidentally ignored) inverts: it must now assert `project-state.json` IS ignored while `thinkos.sqlite*` remains ignored. This is the only substantive spec change.

### ALTERNATIVE 1 — keep tracked; define non-self-referential repository identity

Keep `project-state.json` Git-tracked, but redefine what `head_sha` records so the state file's own commit does not invalidate it. Options for identity: initial-commit SHA (immutable), or a "project identity" hash of `HEAD^{tree}` of a stable anchor, or record the state commit's *parent* HEAD. Each is more machinery and weaker live-truth semantics.

**Empirically validated:** exclude-pathspec `-- . ':(exclude).thinkos'` fixes the *dirty* half but NOT the *head_sha* half — a state commit still moves HEAD, so recorded sha ≠ live HEAD. A non-self-referential identity is a new semantic invention, not a probe fix.

- CURRENT reachable? **Only with an invented identity anchor**; the sha becomes "anchor" not "live HEAD" — a semantics change to the frozen probe.
- State survives succession? **YES** (tracked).
- Strictly observational? **Weakened** — head_sha would no longer mean "live HEAD".
- Complexity added: **HIGH** (new identity concept, migration, anchor rules).
- Git semantics: state commits interleave with work commits (history noise).
- Migration: redefinition of a frozen probe — breaks v1.1/v1.2 compatibility.
- Falsification test: state-commit-then-status-CURRENT (requires identity anchor).
- Conflicts: **§3 head_sha semantics, §2 tracked-by-design, §6 determinism** — the probe no longer compares recorded-vs-live HEAD.

### ALTERNATIVE 3 — commit-time hook / post-commit auto-update

Keep tracked, add a mechanism that updates the state file as part of every commit (hook or wrapper), so recorded sha always equals live HEAD.

- CURRENT reachable? **YES after each commit** — but the update is a *write* by the status-adjacent mechanism; violates the read-only spirit and creates write amplification on every commit; hook install is a new subsystem.
- State survives succession? **YES** (tracked).
- Strictly observational? **NO** — introduces automatic writes (frozen non-goal: "No automatic observation, correction, or writeback").
- Complexity added: **HIGH** (hooks, wrapper, failure modes, history churn).
- Git semantics: every commit changes two files; rebase/amend/cherry-pick break the invariant.
- Migration: hook install across environments.
- Falsification test: commit → status CURRENT (passes), but amend/rebase → STALE (fails).
- Conflicts: **§1 non-goals (no automatic writeback), §8 determinism (commit-dependent), §12 touch map (hooks prohibited).**

---

## 3. Recommendation

**Alternative 2**, with the smallest spec delta:

1. §2: "tracked by design" → "repo-local operational state, excluded from Git (nested `.thinkos/.gitignore` ignores `project-state.json`); continuity across succession via explicit export/copy documented in §10".
2. §9 t14: guardrail test inverts — assert `project-state.json` IS ignored and `thinkos.sqlite*` remains ignored (protects operational-status from accidental commits).
3. Add lifecycle regression (init → record → work commit → re-record → status CURRENT ×3; porcelain clean).
4. Add `--export` (optional, future) or document manual copy as the continuity mechanism.

No init, tracking, HEAD, or worktree semantics change. No probe semantics change. Status remains strictly observational.

---

## 4. Evidence files

- Lifecycle transcript (this order, Phase 3): full T0–T8 trace with SHAs and probe-by-probe outputs (reproducible).
- Alt 1 mechanics: exclude-pathspec hides dirty, not head_sha (proven).
- Alt 2b validation: full lifecycle CURRENT-stable (proven).
- Regression battery: TSR 21/21; full 683 passed / 1 skipped; compileall OK; fail-closed A/B manual repros green.

*End of TSR v1.3 design decision (draft for Marc). No commit authorized.*
