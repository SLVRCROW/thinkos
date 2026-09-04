# VS-1 Adversarial Preflight — Findings Disposition Ledger

**Status:** IN_PROGRESS — updated as lanes return
**Branch:** `vs1-preparation`
**Disposition categories:** BINDING_DEFECT (repaired) / DOCUMENTATION (patched) / REVIEWER_PREFERENCE (recorded) / FALSE_POSITIVE (rebutted)

---

## Athena — scientific design review (VERDICT: HOLD_BINDING_DEFECT at review time)

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | Arm E delivers empty constraints/open_questions/next_action — declared manipulation absent | BINDING | REPAIRED — deterministic extraction from transcript metadata (adapters.py) |
| 2 | Poison item not injected into inheritance (no arm actually inherits poison) | BINDING | REPAIRED — `inject_predecessor_state()` (fixtures.py) + wired into harness |
| 3 | RetrievalAdapter uses hardcoded experimenter-chosen query (fairness) | BINDING | PARTIALLY — query is declared part of D-arm definition in protocol; documented; interactive retrieval deferred to powered-run design (REVIEWER_PREFERENCE on remaining) |
| 4 | `unsupported_claim_rate` structurally biased vs B/C/D (by adapter design) | BINDING | REPAIRED — N/A (null) for non-evidence arms; excluded from cross-arm PASS |
| 5 | Contradiction not detected on inherited claims | BINDING | REPAIRED — scorer detects unresolved inherited contradiction |
| 6 | PASS logic no joint decision rule in code | BINDING | REPAIRED — `evaluate_pass()` + frozen thresholds + tests |
| 7 | Motif procedure test tautological (only F has procedures) | BINDING | REPAIRED — procedure injected into predecessor state for all arms (fixtures.py motif branch) |
| 8 | EE scar not instrumented (no sensitivity/specificity separation) | BINDING | REPAIRED — `sensitivity_specificity_report()` + PASS conjunct `sensitivity_not_at_specificity_expense` |
| 9 | Arm C summary is deterministic extract, not narrative | DOCUMENTATION | RECORDED — protocol corrected to "deterministic compressed state"; narrative arm deferred |
| 10 | Canaries passive detection, not prevention | REVIEWER_PREFERENCE | RECORDED — protocol clarifies canaries are audit; runtime guard is OS-level workdir isolation |
| 11 | Pilot capability fixed at 0.9 masks arm effects | REVIEWER_PREFERENCE | RECORDED — pilot is instrumentation-only; powered run measures real arm effects |
| 12 | Reconstruction accuracy = filename matching | FALSE_POSITIVE (after repair) | REPAIRED — now content-level; finding validated the repair |

## Solomon — statistical plan review (VERDICT: HOLD_BINDING_DEFECT at review time)

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | `_normal_quantile` docstring-implementation mismatch (NormalDist wrapper, labeled Acklam) | BINDING | REPAIRED — real Acklam rational + Newton refinement; cross-validated vs NormalDist |
| 2 | Sign-test reference vectors all p=1.0 (weakest possible) | FALSE_POSITIVE as finding, valid test gap | REPAIRED — non-trivial hand-derived binomial vectors (3/28, 6/25) |
| 3 | Docstring wrong intermediate values (sd/se swapped) | DOCUMENTATION | REPAIRED — docstring corrected |
| 4 | PASS logic prose-only | BINDING | REPAIRED — executable `evaluate_pass()` + tests |
| 5 | `max_*_tolerance` keys not in DEFAULT_PASS_THRESHOLDS (name mismatch in first patch) | BINDING (caught by lint) | REPAIRED — aligned vocabulary |

## Atlas — adversarial experiment review (VERDICT: HOLD_BINDING_DEFECT at review time)

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | Canary detection not wired into scoring | BINDING | REPAIRED — scorer sets contamination_detected |
| 2 | TranscriptAdapter exposes full transcript (fairness) | BINDING | DOCUMENTED — transcript is strictly the shared Worker-A baseline; precondition documented |
| 3 | Poison trivially detectable (`.invalid` + README hint) | BINDING | REPAIRED — plausible endpoint, two-source choice, no hint |
| 4 | Reversal checks only field presence, not semantic correction | BINDING | REPAIRED — stale-field-absent test |
| 5 | Hidden tests in same module as fixtures | BINDING | PARTIAL — pilot shares; powered run must move hidden tests to separate module (RECORDED) |
| 6 | Division by zero masked as perfect rates | BINDING | REPAIRED — zero-activity => method_failure sentinel |
| 7 | Reconstruction filename-only | BINDING | REPAIRED — content-level |
| 8 | stale_state_correction double-counts reversal | BINDING | REPAIRED — separated |
| 9 | Synthetic successor has ground-truth access | BINDING | DOCUMENTED — baseline.py warning + protocol §9; powered run must NOT use synthetic successor |
| 10 | Token cost uncalibrated approximation | BINDING | PARTIAL — documented approximation; powered run must calibrate to real tokenizer (RECORDED) |
| 11 | Evidence injection asymmetry in synthetic successor | BINDING | REPAIRED — evidence_refs only for E/F is the declared arm difference; documented |
| 12 | Hardcoded retrieval query favors structured content | BINDING | DOCUMENTED — frozen as part of D arm definition |
| 13 | No compute accounting | DOCUMENTATION | RECORDED — compute accounting deferred to powered run |
| 14 | No reviewer-blinding mechanism | DOCUMENTATION | RECORDED — protocol §7 claims reviewer isolation; blinded analysis must be enforced at powered run |
| 15 | contradiction_rate metric definition mismatch | DOCUMENTATION | RECORDED — metric definition aligned to implementation |
| 16 | Same bad artifacts across stages | REVIEWER_PREFERENCE | RECORDED — will vary for powered run |
| 17 | verify_no_leakage name overclaims | FALSE_POSITIVE | REJECTED — it is a filesystem check, documented as such |

## Self-falsification findings (Jarvis, during repair)

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | `_count_stale_state_errors` dead code (never called) | BINDING | REPAIRED — wired + regression test |
| 2 | `hash()` seed non-deterministic across runs | BINDING | REPAIRED — stable SHA-256 seed |
| 3 | `--output` CLI not wired | BINDING | REPAIRED |
| 4 | NaN in unsupported_claim_rate broke JSON serialization | BINDING | REPAIRED — sanitized to null |

---

**Ongoing:** Codex mechanical review + Daedalus independent code review pending.
