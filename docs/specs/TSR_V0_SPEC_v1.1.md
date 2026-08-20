# FROZEN SPECIFICATION v1.1 — THINKOS THIN STATE RECONCILIATION (TSR v0)

Status: FROZEN v1.1 · 2026-08-20 · Supersedes v1.0 (v1.0 delta: §3 branch probe predicate, §6 doctor MUST-run resolution, §6/§7 non-evaluated probe shape, P2 clarifications, §9 tests 12-14). Marc-approved scope (THIN STATE RECONCILIATION v0). Authority: Marc · Orchestrator: Jarvis. Purpose: read-only recorded-vs-live reconciliation. Deterministic. No writes. No authority. No performance claims.

## 1. Purpose and non-goals
One tracked state file + one read-only command. Nothing else.
Frozen non-goals: No automatic observation, correction, or writeback. No intent inference; no objective/checkpoint/next-action fields. No new database objects, packet kinds, schema kinds, registry, hooks. No handoff changes. No authority changes. No performance claims. No age-based staleness (recorded_at never consulted for status).

## 2. State file
Path: <project>/.thinkos/project-state.json — tracked by design (gitignore only excludes thinkos.sqlite*; do NOT add this file to gitignore).
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
- File-level fail-closed: file unparseable or schema_version != "tsr.v0" → whole reconciliation is UNKNOWN.
- Write authority: NONE. .thinkos/project-state.json gets no special permission mechanism. It changes only manually or through already-approved existing write mechanisms (e.g., ordinary write_file through existing gates). thinkos status NEVER writes.

## 3. Probes (exactly five, frozen)
All git invoked via subprocess with argument lists only (no shell). No network. Probe outcomes:
- repository_presence: `git rev-parse --git-dir` exit 0 → {exists: true}; any failure → {exists: false} (deterministic result, evaluated).
- head_sha: `git rev-parse HEAD` exit 0 → {value: <40-lowercase-hex>}; failure → probe non-evaluated (not {value: null}).
- branch: `git symbolic-ref -q --short HEAD`: exit 0 → {detached: false, branch: <name>}; exit != 0 AND `git rev-parse HEAD` exit 0 → {detached: true, branch: null}; exit != 0 AND `git rev-parse HEAD` fails (unborn HEAD / non-repo) → probe non-evaluated.
- upstream: `git rev-parse --abbrev-ref @{upstream}` exit 0 AND `git rev-parse @{upstream}` exit 0 → {configured: true, ref: <name>, sha: <40-lowercase-hex>}; either exit != 0 (unconfigured or deleted upstream) → {configured: false} (deterministic evaluated).
- worktree_dirty: `git status --porcelain` output non-empty → {dirty: true}; empty → {dirty: false} (deterministic evaluated; porcelain exit always 0).

## 6. Reconciliation semantics
Probe is EVALUATED only when both sides usable: recorded present+well-formed (per §2) AND live value successfully obtained (per §3 — git succeeded or a defined deterministic result like configured:false / exists:false).
STALE = at least one evaluated recorded probe differs from live value (exact structural inequality). Marc's rule: 'at least one successfully evaluated recorded probe differs from live reality. No age threshold.'
CURRENT = at least one probe evaluated and zero differ.
UNKNOWN = fail-closed: no state file, malformed file, wrong schema_version, or zero probes evaluated (e.g., git unavailable).
doctor: thinkos status MUST invoke doctor(project_path, json_output=False, quiet=True) and map its status/findings into doctor_health verbatim. doctor_health.status is "not_run" (empty findings) ONLY if invoking doctor raises an unexpected exception. Doctor health NEVER affects reconciliation status, exit code, or reasons.

## 7. Output contract
Command: thinkos status [PROJECT_PATH] [--json] (mirrors _run_onboard arg handling).
Exit codes: 0 = CURRENT, 1 = STALE, 2 = UNKNOWN.
JSON output (frozen; probe entries are ALWAYS all five keys in the fixed order: repository_presence, head_sha, branch, upstream, worktree_dirty):
{
  "status": "CURRENT | STALE | UNKNOWN",
  "state_file": "<path or null>",
  "schema_version": "tsr.v0",
  "probes": [
    {"key": "repository_presence", "recorded": {"exists": true}, "live": {"exists": true}, "evaluated": true, "matches": true},
    {"key": "head_sha", "recorded": null, "live": null, "evaluated": false, "matches": false}
  ],
  "doctor_health": {"status": "healthy|unhealthy|not_run", "findings": []},
  "reasons": ["<one string per differing evaluated probe, wording is presentation>"]
}
Human output: minimal one-liner + probe lines; same information (probe entries always present as in JSON).
No content leakage: output never includes raw state-file text beyond the probe fields, never packets/receipts/structured content.

## 8. Determinism & side-effect freedom
Same inputs → identical output (git commands deterministic for unchanged state; probe array order fixed; doctor findings order stable). thinkos status MUST NOT create/modify/delete any file or directory. No WAL/SHM/tables/temp files. No git state mutations (read-only commands only).

## 9. Acceptance criteria (tests, frozen)
1. No .thinkos/ → UNKNOWN, exit 2, no files created.
2. Initialized project, no state file → UNKNOWN, exit 2.
3. State file matches live git → CURRENT, exit 0; doctor unhealthy reported but does not change status or exit code.
4. Live change without file update (HEAD, branch, upstream, dirty flip) → STALE, exit 1, per-probe reasons.
5. Malformed JSON → UNKNOWN, exit 2.
6. Wrong schema_version → UNKNOWN, exit 2.
7. Determinism: 5 runs → identical output.
8. Side-effect-free: temp project, repeated runs → directory tree unchanged.
9. git unavailable (PATH-manipulated, valid state file present) → git probes non-evaluated → zero evaluated → UNKNOWN, exit 2.
10. Non-repo dir with recorded repository_presence:true → evaluated, differs → STALE, exit 1.
11. Windows: pathlib paths, subprocess arg lists; extend windows smoke only if a probe proves platform-sensitive (after v1.1 branch amendment, expected: no extension needed).
12. Valid file with an omitted probe subset → only the present probes evaluated; CURRENT or STALE per subset.
13. Malformed probe value (e.g., head_sha: "abc") → that probe excluded, others still evaluate.
14. gitignore guardrail: `git check-ignore` does NOT ignore .thinkos/project-state.json (protects tracked-by-design).

## 10. Backward compatibility & touch map
Zero existing behavior changes. Additive only.
New module thinkos/status.py: status(project_path=None, json_output=False) -> dict; helpers _load_state_file, _probe_repository_presence/_head_sha/_branch/_upstream/_worktree_dirty, _reconcile, _render_json/_render_human.
thinkos/__main__.py: add `status` to help text/usage + dispatch `arg0 == "status"` → _run_status() (mirror _run_onboard arg walk); _run_status maps status → exit 0/1/2.
New tests tests/test_status.py (tests 1-14).
NO changes to: engine.py, sqlite_store.py, context_packet.py, receipt.py, handoff*, identity, policy, config, onboarding, existing tests.

## 11. Security/authority constraints
Read-only; no file writes; no authority grants; no secrets; no structured content; no git remote/credential access.

## 12. PREREQUISITE (before implementation, Marc-approved step)
PR-001 must be amended or explicitly scoped so its 'automatic, six-state, no user reminder' design cannot be mistaken for this approved v0 contract. The amendment is an independent repo document change requiring separate Marc approval.

====================================================================
