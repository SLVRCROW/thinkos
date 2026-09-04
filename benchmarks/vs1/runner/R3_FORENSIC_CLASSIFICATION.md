# VS-1 R3 Forensic Failure Classification — Final Report

**Authority:** `AUTHORIZE_VS1_R3_ZERO_PROVIDER_FORENSIC_FAILURE_CLASSIFICATION`
**Date:** 2026-08-20 · **Provider calls made:** 0 · **R4:** NOT authorized · **Threshold:** unchanged
**Evidence root:** `/mnt/d/AI/AI_Research/lanes/emergence_engineering/vs1_powered_run_20260820_r3/`

---

## EXECUTIVE ANSWER

**The 7 interruption stage-2 failures are UNCLASSIFIABLE from the sealed evidence.**
The raw model completions were never persisted. The halt record's "task difficulty, not a
runner defect" conclusion was **wrong** — the forensic pass reveals a genuine instrument
persistence defect: the executor discards raw model output on parse failure, and the sealer
only runs at end-of-run. The evidence needed to distinguish MODEL_TASK_FAILURE from
INSTRUMENT_FAILURE does not exist on disk.

---

## 1. FROZEN CONTRACT — interruption stage-2 (reconstructed from frozen artifacts)

```text
TARGET PATH:      stage2/records.csv
ARTIFACT TYPE:    CSV (frozen fixture: stage_artifacts[2].path = 'stage2/records.csv')
REQUIRED SCHEMA:  columns id, score, status (frozen TASK_SUBSTRATE)
REQUIRED ROWS:    fixture stage-2 content = 'id,score,status\na1,90,ok\na2,85,ok\n'
                  (2 data rows; frozen fixture is the authority)
ALLOWED FORMAT:   CSV per frozen fixture; parser accepts raw text with a header
                  containing a comma (F5 CSV-aware parser)
FORBIDDEN:        nothing explicitly frozen beyond the schema
PARSER:           parse_artifact(content, 'stage2/records.csv') — accepts raw CSV
                  text, strips markdown fences, rejects empty/no-comma-header
HIDDEN EVALUATOR: fixture.run_hidden_test reads stage2/records.csv, checks
                  stage2_present + stage2_has_records_key (id column)
```

**Critical rule honored:** the parser may not impose a stricter contract than the prompt/
fixture defined. The prompt says "produce stage2/records.csv (same columns)" — the frozen
contract is CSV with id/score/status. The parser accepts exactly that.

## 2. FAILURE MATRIX — the 7 CSV failures

| Failure | Call | Trajectory (blinded) | Raw completion in sealed evidence? | Classification |
|---|---|---|---|---|
| 01 | 17 | BLINDED | **NO** | UNCLASSIFIABLE |
| 02 | 49 | BLINDED | **NO** | UNCLASSIFIABLE |
| 03 | 53 | BLINDED | **NO** | UNCLASSIFIABLE |
| 04 | 59 | BLINDED | **NO** | UNCLASSIFIABLE |
| 05 | 91 | BLINDED | **NO** | UNCLASSIFIABLE |
| 06 | 93 | BLINDED | **NO** | UNCLASSIFIABLE |
| 07 | 95 | BLINDED | **NO** | UNCLASSIFIABLE |

**Why:** the executor's `_run_cell` writes the model's artifact ONLY when
`parse_artifact` returns ok. On parse failure, `artifact_path = ""` and the raw
`provider_res.content` is **never written to disk**. The sealer (`sealer.seal`) runs
ONLY after `executor.run()` completes all 126 calls — the launcher was killed at call 95,
so the sealer never ran. The sealed `R3_HALT_MANIFEST.json` (264 files) contains only
`work/` materialized artifacts — no raw completions, no provider receipts, no outcomes.

**The stage2/records.csv files present in failed cells' workdirs are FIXTURE content, not
model output** — they were written by the stage-3 call's `_materialize_stage2_output`
fallback (which materializes the frozen stage-2 fixture when the stage-2 call failed).
Verified byte-identical to fixture content for all 6 cells with files present.

## 3. The provider empty completion (r2-reversal-summary, call 63)

```text
PROVIDER STATUS: error
ERROR:           empty completion (usage returned, content empty)
RAW COMPLETION:  NOT PERSISTED (same persistence defect)
```

The provider receipt (status=error, empty completion) was held in the executor's
in-memory `CellOutcome` but never sealed. The classification "provider/runtime failure"
is **probable** (status=error with empty content is a provider-side signal) but the raw
response body is unavailable. Classified: **PROBABLE_PROVIDER_RUNTIME_FAILURE, evidence
incomplete** — not mechanically confirmable from sealed evidence.

## 4. DENOMINATOR VERDICT

```text
FROZEN_DENOMINATOR:   UNDEFINED. EXECUTION_PLAN_v0.1.0.md §2 says
                      "method-failure rate ≤ 5% (frozen)" — no denominator specified
                      (planned calls / attempted calls / trajectories / completed).
                      The R3 act §16 says "failure tolerance ≤ 5%" — also undefined.
IMPLEMENTED_DENOMINATOR: NO AUTOMATED GATE. launch.py and executor.py contain NO
                      method-failure tolerance check. The halt at call 95 was
                      OPERATOR JUDGMENT (Jarvis), not code-enforced.
MATCH:                NO — the frozen contract is ambiguous AND the implementation
                      does not enforce any denominator.
CLASSIFICATION:       BINDING_METHOD_GATE_DEFECT
```

Descriptive rates (clearly labeled, NOT the frozen definition):
```text
8 / 126 planned calls   = 6.35%   (halt record's figure)
8 / 95 attempted calls  = 8.42%   (calls actually made)
8 / 81 trajectories     = 9.88%   (trajectories started)
```

## 5. GUILD VERDICTS

| Lane | Question | Verdict |
|---|---|---|
| Daedalus | Is instrumentation rejecting valid outputs? | **CANNOT DETERMINE** — raw completions absent; parser itself is permissive (CSV-aware, fence-stripping) and matches the frozen contract |
| Athena | Is format compliance part of the measured capability? | **AMBIGUOUS** — the frozen construct treats output-format compliance as task performance (the model must produce the artifact), but the instrument cannot distinguish "model produced garbage" from "model produced valid CSV the parser rejected" without raw output |
| Solomon | Method-failure semantics + denominator | **BINDING_METHOD_GATE_DEFECT** — denominator undefined in frozen contract; no automated gate; threshold change after R3 would be post-hoc bias, so the 5% must be re-frozen with an explicit denominator |
| Atlas | Is a proposed R4 rule post-hoc goalpost moving? | **N/A** — no R4 rule proposed yet; Atlas's dual-attack confirms the evidence is insufficient to blame either model or parser |

## 6. SCIENTIFIC CONSEQUENCE

1. **The halt record's classification was wrong.** It stated "task difficulty, not a
   runner defect." The forensic pass proves the runner HAS a defect: raw model output is
   not persisted on parse failure, and the sealer is end-of-run only. The halt was
   operator judgment on an undefined denominator, not a code-enforced gate.
2. **R3's 8 method failures are unclassifiable.** They cannot be counted as scientific
   outcome evidence (model failure) NOR as instrumentation failure — the evidence is
   structurally absent.
3. **R3 remains INCOMPLETE / NO THESIS VERDICT** — and additionally its halt rationale
   is now known to be methodologically unsound (undefined denominator, no automated gate).
4. **The instrument needs a persistence + gate repair before any R4**, regardless of
   whether the model or parser was at fault.

## 7. R4 RECOMMENDATION

```text
R4_MIXED_METHOD_REPAIR
```

The forensic pass found a genuine instrument defect (raw-completion persistence +
end-of-run-only sealing + undefined/unenforced tolerance gate). The model-vs-parser
question is UNRESOLVED and can only be answered by a run that persists raw completions
per cell. This is not goalpost moving — it is making the instrument capable of
distinguishing subject failure from measurement failure, which the act's prime directive
requires.

## 8. EXACT NEXT MARC ACT (smallest authorization — NOT executed)

```text
MARC AUTHORIZES VS-1 R4 INSTRUMENT REPAIR AND FRESH RUN
1. Repair the runner (bounded, regression-tested):
   (a) persist EVERY raw model completion + provider receipt + outcome per cell,
       incrementally, before any tolerance check (fixes the forensic blindness);
   (b) seal evidence incrementally (per-cell raw files) so a mid-run halt still
       yields classifiable evidence;
   (c) implement the method-failure tolerance gate IN CODE with the frozen
       denominator: method-failure rate = method_failures / PLANNED_PROVIDER_CALLS
       (126), halt automatically at >5%.
2. Re-freeze the denominator definition in EXECUTION_PLAN (planned calls).
3. Re-run the identical frozen design: 108 trajectories / 126 calls,
   deepseek-v4-pro:0813, temp 0, retries 0, replacements 0.
4. After the run, perform the SAME forensic classification on the now-persisted
   raw completions (blinded) before issuing any thesis verdict.
No other changes. No threshold change beyond making the existing 5% enforceable.
```

## 9. COMPLETION STATUS

```text
INCONCLUSIVE
```

The failures are unclassifiable from sealed evidence. The instrument's persistence
defect is now a verified BINDING finding. The smallest justified R4 act is the
instrument repair above — not a threshold change, not a parser change, not a model
change. Reality's vote on the thesis still awaits an instrument that can record what
the model actually said.
