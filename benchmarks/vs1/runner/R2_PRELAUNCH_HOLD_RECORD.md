# VS-1 R2 Pre-Launch Gate — HOLD (verified binding defects)

**Status:** HOLD_BINDING_DEFECT — R2 NOT LAUNCHED
**Date:** 2026-08-20 · **Authority:** `AUTHORIZE_VS1_BINDING_PATH_REPAIR_AND_FULL_POWERED_RERUN_V2`
**Branch / HEAD:** `vs1-preparation` @ `7b8745d` (path-repair commits `3f205f8`, `7b8745d`)

## What was done

The authorized bounded repair (fixture-derived artifact paths) was applied and verified:
- `prompts.py` + `executor.py` now derive prompt target, write target, and successor-event
  path from `get_fixture(task, condition).stage_artifacts[stage].path`.
- Regression suite enumerates the FULL schedule (36-cell topology × all conditions): prompt
  target == write target == fixture path; no hardcoded stage3/config.json remains.
- Daedalus affected-surface review: **PASS_WITH_LIMITATIONS** (18 runner + 65 VS-1 tests pass;
  diff confined to authorized scope).
- Full suite: **727 passed, 1 skipped**.

## Pre-launch hostile review (Atlas) — 4 binding findings, all MECHANICALLY VERIFIED

| # | Finding | Verification | Class |
|---|---|---|---|
| F1 | Executor never materializes predecessor artifacts in workdir; hidden tests for stages 1–2 are structurally impossible to pass | `build_predecessor_events` returns `stage1/records.csv`/`stage1/config.json` but `_run_cell` never writes them; R1 valid cells all show `stage1_present: False, stage2_present: False` | BINDING — verified |
| F2 | Motif `inject_predecessor_state` REPLACES the stage-1 write_file with a `run` call (`path: None`) — stage-1 artifact erased | `build_predecessor_events('t','motif')` → `[('run', None)]` | BINDING — verified |
| F3 | Interruption prompt declares stage3/final.json but never tells the model about stage1/2 CSV schema; can't resume correctly | Prompt text has no predecessor-artifact block | BINDING — verified as design gap |
| F4 | Motif prompt never exposes the reusable procedure (run command, output, procedure.json content) | Prompt text has no procedure block | BINDING — verified as design gap |
| F5 | Single-call stage-3 successor cannot produce stage-2 records for interruption; schedule assumes one call per cell | schedule.py cell `stage=3` | BINDING — verified as design gap |

## Why R2 was NOT launched

Per the act's explicit gate: "If any binding gate fails: HOLD_BINDING_DEFECT. Do not start powered R2."
The fixes for F1–F5 would change **fixture semantics** (materializing predecessor artifacts changes
hidden-test outcomes; fixing motif changes `inject_predecessor_state`) and/or **schedule semantics**
(two-call interruption), both explicitly frozen: "No fixture semantics may change... No scoring
semantics may change... No conditions may change."

The authorized repair (path derivation) is complete and correct; the remaining defects are
outside this act's authority.

## Evidence preserved

- R1: sealed, immutable, 218-file manifest — `INVALID_OR_INCOMPLETE_FOR_THESIS_ADJUDICATION`
  (preserved per act; its 72 "valid" cells are now known to be stage-3-only scores, not full
  hidden-test scores — a measurement limitation disclosed here).
- R2: NOT started; `vs1_powered_run_20260820_r2` NOT created.

## Smallest next authorization required (recommended)

```text
MARC AUTHORIZES VS-1 R3 RUNNER COMPLETION AND FULL POWERED RUN
- Permit minimal fixture/schedule changes limited to:
  (a) executor materializes predecessor artifacts in the workdir before
      hidden evaluation (fixes F1);
  (b) inject_predecessor_state(motif) preserves the stage-1 write_file
      alongside the run procedure call (fixes F2);
  (c) prompt adds a PREDECESSOR ARTIFACTS block for interruption (CSV schema)
    and motif (procedure receipt) without arm-coaching (fixes F3/F4);
  (d) schedule permits interruption cells to write stage-2 then stage-3
    (two provider calls per interruption cell) OR the fixture hidden test
    for interruption stage-2 is re-scoped to the stage-3 final artifact
    only (fixes F5).
- Re-run Daedalus + Atlas + full gate; then one fresh 108-call run.
- No other parameter changes.
```
