# FROZEN SPECIFICATION v1.3 — THINKOS THIN STATE RECONCILIATION (TSR v0)

**Status:** FROZEN v1.3 · 2026-08-20 · Supersedes v1.2 (delta: §2 state file becomes repo-local operational state excluded by the nested `.thinkos/.gitignore`; §9 test 14 guardrail inverted; §9 adds lifecycle regression test 20; §10 continuity note). Prepared under Marc authorization (TSR-V0 CONTAINMENT REPAIR + STATE-LIFECYCLE ADJUDICATION). v1.1 and v1.2 remain frozen and byte-stable as prior records.
**Governance note (2026-09-02):** Adopted prospectively by Marc as the controlling TSR v0 contract for ThinkOS Alpha 0.2 Patch 1, specifically the operational-state lifecycle in which `project-state.json` is project-local and gitignored. This adoption does not retroactively claim that the earlier v1.3 draft, the design decision, the doctor-side repair work order, or the implementation was previously authorized. The earlier header phrase "Marc approved Alternative 2" is corrected: the design decision was DRAFT at the time; the adoption is prospective. v1.2 is a superseded uncommitted intermediate artifact, not a previously governed or authoritative record; its "FROZEN" header is a historical self-claim, not governance.
**Authority:** Marc · **Orchestrator:** Jarvis · **Purpose:** read-only recorded-vs-live reconciliation. Deterministic. No writes. No authority. No performance claims.

> **Amendments vs v1.2 (sole delta):**
> 1. §2: `project-state.json` is **repo-local operational state**, NOT a Git-tracked state object. It is excluded by the nested `.thinkos/.gitignore` written by `thinkos init` (which now also ignores `project-state.json`). This removes the lifecycle contradiction (a tracked state object cannot satisfy the observational `head_sha`/`worktree_dirty` probes: committing or updating the tracked file changes HEAD or dirtiness, making CURRENT unreachable/stale-by-construction).
> 2. §9 test 14 (gitignore guardrail) is **inverted**: the guardrail now asserts `project-state.json` IS ignored by the nested `.thinkos/.gitignore` (protecting operational state from accidental commits) while `thinkos.sqlite*` remains ignored.
> 3. §9 adds acceptance test 20 (lifecycle regression): real `thinkos init` → record state → commit real work → re-record state → `thinkos status` returns CURRENT, exit 0, stable across repeated runs, with a clean worktree — proving CURRENT is reachable and stable without hiding a contractually tracked surface.
> 4. §10 adds a continuity note: because the state file is operational (not in Git history), succession across agent/repo copies is served by an explicit export/copy mechanism (documented `cp .thinkos/project-state.json` into the successor's `.thinkos/` before first status, or a future `status --export`).
> All other sections are carried verbatim from v1.2.

---

## 1. Purpose and non-goals
One tracked state file + one read-only command. Nothing else.
Frozen non-goals: No automatic observation, correction, or writeback. No intent inference; no objective/checkpoint/next-action fields. No new database objects, packet kinds, schema kinds, registry, hooks. No handoff changes. No authority changes. No performance claims. No age-based staleness (recorded_at never consulted for status).

## 2. State file
Path: `<project>/.thinkos/project-state.json` — **repo-local operational state** (v1.3), excluded from Git by the nested `.thinkos/.gitignore` that `thinkos init` writes (which ignores `thinkos.sqlite*` and `project-state.json`). Do NOT commit this file. In the product shape (no root `.thinkos/` rule), the nested rule governs exclusion of the state file; note that a user-supplied root `.gitignore` rule for `.thinkos/` would take precedence for `git add` operations on config files — the nested rule is the product's own guardrail, not a Git-level override of explicit user ignore rules.
Exact schema (frozen):
{
  "schema_version": "tsr.v0",
  "recorded_at": "<ISO-8601 UTC>",
  "probes": {
    "repository_presence": {"exists": true},
    "head_sha": {"value": "<40-hex or null>"},
    "branch": {"detached": false, "branch": "<name>"},
    "upstream": {"configured": true, "ref": "<upstream-ref>", "sha": "<40-hex or null>"},
    "worktree_dirty": {"dirty": true}
  }
}
Rules:
- recorded_at is informational only; NEVER validated and NEVER consulted for staleness. A file missing or mis-typed recorded_at still reconciles.
- Any probe may be omitted from the file — omitted = not evaluated = excluded.
- Malformed recorded value: non-lowercase-40-hex sha, wrong types, extra/missing keys inside a recorded probe object, unparseable JSON → that probe excluded. (Structural equality of the probe object is the comparison; extra or missing keys inside a probe = malformed.)
- File-level fail-closed: file unparseable, non-UTF-8, or schema_version != "tsr.v0" → whole reconciliation is UNKNOWN.
- Write authority: NONE. .thinkos/project-state.json gets no special permission mechanism. It changes only manually or through already-approved existing write mechanisms. thinkos status NEVER writes.

## 3. Probes (exactly five, frozen)
All git invoked via subprocess with argument lists only (no shell). No network. Probe outcomes:
- repository_presence: git rev-parse --git-dir exit 0 → {exists: true}; any failure → {exists: false} (deterministic result, evaluated).
- head_sha: git rev-parse HEAD exit 0 → {value: <40-lowercase-hex>}; failure → probe non-evaluated (not {value: null}).
- branch: git symbolic-ref -q --short HEAD: exit 0 → {detached: false, branch: <name>}; exit != 0 AND git rev-parse HEAD exit 0 → {detached: true, branch: null}; exit != 0 AND git rev-parse HEAD fails (unborn HEAD / non-repo) → probe non-evaluated.
- upstream: git rev-parse --abbrev-ref @{upstream} exit 0 AND git rev-parse @{upstream} exit 0 → {configured: true, ref: <name>, sha: <40-lowercase-hex>}; either exit != 0 (unconfigured or deleted upstream) → {configured: false} (deterministic evaluated).
- worktree_dirty: git status --porcelain non-empty → {dirty: true}; empty → {dirty: false} (deterministic evaluated; porcelain exit always 0).
All git output is captured as bytes and decoded as UTF-8 strictly. If decoding cannot be performed faithfully, the affected probe is unevaluable (fail-closed; never normalized with replacement characters that could manufacture a false CURRENT/STALE judgment).

## 6. Reconciliation semantics
Probe is EVALUATED only when both sides usable: recorded present+well-formed (per §2) AND live value successfully obtained (per §3 — git succeeded or a defined deterministic result).
STALE = at least one evaluated recorded probe differs from live value (exact structural inequality).
CURRENT = at least one probe evaluated and zero differ.
UNKNOWN = fail-closed: no state file, malformed/unparseable/non-UTF-8 file, wrong schema_version, or zero probes evaluated (e.g., git unavailable).
doctor: thinkos status MUST invoke doctor(project_path, json_output=False, quiet=True, side_effect_free=True) and map its status/findings into doctor_health verbatim. doctor_health.status is "not_run" (empty findings) ONLY if invoking doctor raises an unexpected exception. Doctor health NEVER affects status, exit code, or reasons.
doctor side-effect-free clause (v1.2 amendment, retained): the mandatory doctor integration used by thinkos status MUST itself be filesystem-side-effect-free (spec §8). Sidecar file creation (WAL/SHM/temp) by the doctor path is NOT an accepted exception.

## 7. Output contract
Command: thinkos status [PROJECT_PATH] [--json] (mirrors _run_onboard arg handling).
Exit codes: 0 = CURRENT, 1 = STALE, 2 = UNKNOWN.
JSON output (frozen; probe entries ALWAYS all five keys in the fixed order: repository_presence, head_sha, branch, upstream, worktree_dirty):
{
  "status": "CURRENT | STALE | UNKNOWN",
  "state_file": "<path or null>",
  "schema_version": "tsr.v0",
  "probes": [
    {"key": "repository_presence", "recorded": {"exists": true}, "live": {"exists": true}, "evaluated": true, "matches": true},
    {"key": "head_sha", "recorded": null, "live": null, "evaluated": false, "matches": false}
  ],
  "doctor_health": {"status": "healthy|unhealthy|not_run", "findings": []},
  "reasons": ["<one string per differing evaluated probe>"]
}
Human output: minimal one-liner + probe lines; same information.
No content leakage.

## 8. Determinism & side-effect freedom
Same inputs → identical output. thinkos status MUST NOT create/modify/delete any file or directory. No WAL/SHM/tables/temp files. No git state mutations. (Enforced by acceptance test 16.)

## 9. Acceptance criteria (tests, frozen)
1. No .thinkos/ → UNKNOWN, exit 2, no files created.
2. Initialized project, no state file → UNKNOWN, exit 2.
3. State file matches live git → CURRENT, exit 0; doctor unhealthy reported but does not change status or exit.
4. Live change without file update (HEAD, branch, upstream, dirty flip) → STALE, exit 1, per-probe reasons.
5. Malformed JSON → UNKNOWN, exit 2.
6. Wrong schema_version → UNKNOWN, exit 2.
7. Determinism: 5 runs → identical output.
8. Side-effect-free: temp project, repeated runs → directory tree unchanged.
9. git unavailable (PATH-manipulated) → git probes non-evaluated → zero evaluated → UNKNOWN, exit 2.
10. Non-repo dir with recorded repository_presence:true → evaluated, differs → STALE, exit 1.
11. Windows: pathlib paths, subprocess argument lists; extend windows smoke only if a probe proves platform-sensitive (after v1.1 branch amendment, expected: no extension needed).
12. Valid file with an omitted probe subset → only present probes evaluated; CURRENT or STALE per subset.
13. Malformed probe value (e.g., head_sha: "abc") → that probe excluded, others still evaluate.
14. **(v1.3, inverted)** Operational-state guardrail: `git check-ignore .thinkos/project-state.json` returns exit 0 (ignored) in a repo produced by real `thinkos init` (the nested `.thinkos/.gitignore` excludes it), while `thinkos.sqlite`, `thinkos.sqlite-wal`, `thinkos.sqlite-shm` remain ignored. The state file must never be committed accidentally.
15. Non-UTF-8 / unparseable state file → fail-closed UNKNOWN, exit 2, contract-compliant JSON, no traceback.
16. Filesystem immutability on a real initialized ThinkOS project: repeated `thinkos status` runs create/modify/delete ZERO filesystem objects, including WAL/SHM files.
17. Deeply nested JSON → fail-closed UNKNOWN, exit 2, contract-compliant JSON, no traceback.
18. Non-UTF-8 Git output (real ref with invalid UTF-8 bytes) → affected probe unevaluable; no state file → UNKNOWN, exit 2, contract JSON, empty stderr, no traceback, zero fs mutation.
19. Constrained-memory state file (MemoryError) → fail-closed UNKNOWN, exit 2, contract JSON, empty stderr, no traceback, zero fs mutation.
20. **(v1.3)** Lifecycle regression on a real initialized ThinkOS project: git init → thinkos init → record state → commit real work → re-record state → thinkos status returns CURRENT, exit 0, stable across repeated runs, worktree clean (state file ignored, never committed).

## 10. Backward compatibility & touch map
Zero existing behavior changes. Additive only.
New module thinkos/status.py; `__main__.py` status dispatch; tests.
**Doctor-side repair (already authorized and applied):** side-effect-free inspection path (header/stat) wired to the status path only; default doctor semantics unchanged; `immutable=1` against a live/WAL-capable database PROHIBITED.
**v1.3 state-lifecycle repair (already authorized and applied):** `thinkos init`'s nested `.thinkos/.gitignore` additionally ignores `project-state.json` (operational state). Existing `init` idempotency and refusal semantics unchanged. **Legacy migration (exact steps for projects that tracked `project-state.json` under v1.1/v1.2):** (1) `git rm --cached .thinkos/project-state.json` and commit (tracked files override ignore rules, so this step is required first); (2) manually add a `project-state.json` line to the existing `.thinkos/.gitignore` (`thinkos init` will NOT rewrite an existing nested ignore — it reports already-initialized); (3) re-record the state file against current reality; (4) verify `git status --porcelain` is clean and `thinkos status` returns CURRENT. Until the migration is complete, such projects read STALE on an honest record with dirty porcelain — fail-safe direction (never a false CURRENT), not a silent repair.
**Continuity note (v1.3):** because the state file is operational and not in Git history, agent/repo succession carries the recorded state explicitly: copy `.thinkos/project-state.json` into the successor's `.thinkos/` before the successor's first status, or use a future documented export mechanism. The state file remains the minimum trustworthy record of the last verified live truth.
NO changes to: engine.py, store, packet schema, receipt, handoff*, identity, policy, config (beyond the init gitignore content above), existing tests (beyond fixture realignment to the real init shape).

## 11. Security/authority constraints
Read-only; no file writes; no authority grants; no secrets; no structured content; no git remote/credential access.

## 12. PREREQUISITE (before implementation, Marc-approved steps)
PR-001 amended/scoped (done in docs/PRODUCT_REQUIREMENTS.md via PR-001 Scope Note); doctor-side repair authorized and applied (acceptance test 16 passes); state-lifecycle repair authorized (Marc approval of TSR v1.3 Alternative 2, 2026-08-20) so acceptance test 20 passes.

---
*End of TSR v0 Spec v1.3. v1.1 (sha256 eb15926a…) and v1.2 (sha256 e4570623…) remain frozen and byte-stable as prior records.*
