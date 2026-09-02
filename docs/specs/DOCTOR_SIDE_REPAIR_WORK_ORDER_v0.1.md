# DOCTOR-SIDE REPAIR — WORK ORDER v0.1 (prepared for Marc review — NOT YET AUTHORIZED)

**Status:** DRAFT WORK ORDER · 2026-08-20 · Prepared by Jarvis under Marc authorization (TSR-V0 CONTAINMENT REPAIR)
**Applies to:** `thinkos/onboarding.py` doctor SQLite integrity check (onboarding.py:709–747)
**Purpose:** Supply a genuinely filesystem-side-effect-free inspection path for the mandatory doctor integration used by `thinkos status` (TSR spec v1.1 §6/§8; v1.2 §6).
**NOT applied in this round.** This document is the bounded work order; its execution requires a separate Marc authorization. No commit, push, or scope expansion is authorized now.
**Provenance note (2026-09-02):** The doctor side-effect-free path described here is present in the worktree (`thinkos/onboarding.py`) and is part of the TSR v0 v1.3 contract adopted prospectively by Marc on 2026-09-02. This note does not retroactively claim the work order was previously authorized; the "NOT YET AUTHORIZED" label remains historically accurate for 2026-08-20.

---

## 1. Problem (verified)

`doctor`'s SQLite integrity check opens the project store with `sqlite3.connect(db_uri + "?mode=ro", uri=True)` then runs `PRAGMA integrity_check` (onboarding.py:713–721). Against a WAL-mode database (which `thinkos init` creates), even a read-only open creates `.thinkos/thinkos.sqlite-wal` and `.thinkos/thinkos.sqlite-shm` sidecars. `thinkos status` MUST invoke doctor (spec §6) and MUST NOT create/modify/delete any filesystem object (spec §8) — the current doctor path violates §8. `status.py` itself contains zero write calls; the side effect originates in doctor. This is an internal contradiction between §6 (MUST invoke doctor) and §8 (side-effect freedom), resolvable only on the doctor side or via spec carve-out. Marc's directive: **preserve §8; do NOT accept WAL/SHM as an exception.**

## 2. Authorized surface (ONLY when this work order is authorized)

- MODIFY: `thinkos/onboarding.py` (doctor SQLite check + its signature/docstring)
- MODIFY: `tests/` (add/adjust a doctor-side test proving side-effect-free behavior)
- CREATE/MODIFY: nothing else. `thinkos/status.py`, `thinkos/__main__.py`, `tests/test_status.py` (the TSR surface) MUST NOT change in this work order.

## 3. Required behavior (frozen in this work order)

1. `thinkos status` against a real initialized ThinkOS project (with `thinkos.sqlite`) must create/modify/delete **zero** filesystem objects — including `-wal`/`-shm` — verified by the existing TSR test 16 (filesystem immutability, already added to tests/test_status.py).
2. Default `thinkos doctor` behavior for humans (config presence, sandbox, store dir, sqlite integrity) must remain available — the repair must not silently weaken the normal doctor path.
3. The `status`-invoked doctor call must remain honest: if the integrity check cannot run side-effect-free, the finding must say so explicitly (e.g., `status: ok`, detail: "integrity check skipped in side-effect-free mode") rather than claiming an integrity check ran.
4. No change to reconciliation status, exit codes, `doctor_health` shape, or the TSR output contract.

## 4. Prohibited

- **Do NOT use SQLite `immutable=1` against a live/WAL-capable database as the repair** (Marc directive). Reason: `immutable=1` asserts the file cannot change, can suppress WAL-recovery reads, and is unsafe for a live store.
- Do NOT modify `thinkos/status.py`, `thinkos/__main__.py`, or existing `tests/test_status.py` tests except the newly-added immutability test 16 if it needs to reference the repair.
- Do NOT silently change the normal (non-status) doctor behavior without an explicit Marc approval.

## 4a. Candidate repair shapes (evaluated, recommended, but final choice is the builder's with review)

- **A (recommended): side-effect-free mode parameter.** Add `side_effect_free: bool = False` (or equivalent) to `doctor()`. When True: skip the SQLite `PRAGMA integrity_check` open; instead perform a stat/header-only check (file exists; first 16 bytes == b"SQLite format 3\0"; non-zero size) that opens no connection and creates no sidecars; report `sqlite_integrity: ok` with detail "side-effect-free check passed (header/stat)" or `unhealthy` on failure. `thinkos status` calls `doctor(..., side_effect_free=True)`. Default doctor behavior unchanged.
- **B: check only via `sqlite3` in a transaction-free, truly-read-only mode that provably creates no sidecars** — if such a mode is proven on this platform; do not regress WAL databases.
- **C: separate `doctor_side_effect_free()` helper** (no signature change to doctor) that implements the stateless check; status calls the helper.

## 5. Acceptance (when authorized)

- `tests/test_status.py::test_16_status_zero_fs_objects_created_with_real_initialized_project` PASSES (the regression already added; currently fails without this repair).
- Full TSR suite (1–16) passes; full regression (662 baseline + TSR tests) passes; compileall OK; spec hash `eb15926a…` unchanged; no change to `status.py`; worktree otherwise clean; single commit message: `fix: doctor side-effect-free integration for thinkos status`.
- Independent adversarial post-repair review: PASS or PASS_WITH_LIMITATIONS.

## 6. Stop conditions

- Any attempt that invents a semantic (e.g., silently degrading the normal doctor check, or claiming a check that did not run) → STOP + HOLD.
- If `immutable=1` is proposed → STOP + HOLD (explicitly prohibited).
- Any scope expansion (touching status.py, engine, store, schema, handoff, policy, config, specs) → STOP.
- Baseline drift or spec-hash mismatch → STOP.

## 7. After this work order is applied (future, separate authorization)

- Marc re-runs the full verification battery (frozen TSR tests 1–16, adversarial tests, full regression, compile, spec/hash validation, filesystem immutability).
- On PASS: TSR-V0-IMPLEMENTATION final commit becomes eligible (no push without fresh authorization).
