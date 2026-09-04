# VS-1 Six-Arm Succession Benchmark — Frozen Protocol (Candidate v0.1.0)

**Status:** CANDIDATE_FROZEN_FOR_REVIEW — pending independent adversarial review and Marc acceptance
**Author:** Jarvis (ThinkOS bounded operator) · **Authority:** Marc (principal investigator, final authority)
**Governing sources:** EMERGENCE_ENGINEERING_HANDOFF_v1.28.0.md; VERIFIED_SUCCESSION_RESEARCH_PROGRAM_v0.1.0.md (§10.2); ThinkOS G1 contract v1.0 (SHA 4400ae315386812049431f359447dfbce74fb208caf9f0e0625b77826172d6f6)
**Repo branch:** `vs1-preparation` (base `23c9c52cdaee568b58725f8de2c1d29c6a0c55a4`)
**Package:** `benchmarks/vs1/` — isolated; does not modify G0 frozen files, product runtime, or G1 contracts
**GitHub:** SLVRCROW/thinkos (remote present; push/PR require separate Marc act)

## 1. Research question (frozen)

> Does verified, provenance-preserving, correctable, reusable inherited state measurably raise the
> starting capability of successor AI agents compared with cheaper alternatives (no inheritance,
> transcript replay, summaries, ordinary retrieval)?

The experiment must distinguish: memory from useful inheritance; organization from improved
performance; sensitivity from correctness; reliability from intelligence; documentation from
capability; retrieval from succession; procedure reuse from transcript replay.

ThinkOS Alpha 0.2.0 architecture remains downstream of evidence. Nothing in this protocol
prescribes a ThinkOS product change.

## 2. Scientific scar (frozen design constraint)

EE 2×2 result (handoff v1.28.0, classification SENSITIVITY_ONLY) is institutional scar tissue:

- E (verified state) increased contradiction sensitivity but reduced specificity and raised false escalation.
- The state-organization-alone explanation did not survive R2 (note removal).

Therefore the VS-1 protocol treats as design constraints:

```text
MORE STRUCTURE != MORE INTELLIGENCE
MORE MEMORY != MORE INTELLIGENCE
BEHAVIOR CHANGE != PERFORMANCE IMPROVEMENT
```

The powered VS-1 run must measure sensitivity and specificity of each arm, not only
a single aggregate score. No arm is favored by the harness. The analysis does not assume
E/F win.

## 3. Six arms (frozen)

| Arm | Name | Inherited state | Adapter |
|---|---|---|---|
| A | STATELESS | No inherited state | `stateless` |
| B | TRANSCRIPT | Full bounded predecessor transcript/history | `transcript` |
| C | SUMMARY | Compressed narrative project state | `summary` |
| D | RETRIEVAL | Ordinary lexical retrieval over prior artifacts/history | `retrieval` |
| E | VERIFIED_STATE | Typed, evidence-linked state (claims, evidence, decisions, contradictions, provenance, invalidations, constraints, open questions, exact next action) | `verified_state` |
| F | VERIFIED_STATE_PROCEDURE | Everything in E plus tested reusable procedures (scope, inputs, outputs, failure conditions, verification, reuse history, adversarial review notes) | `verified_state_procedure` |

Fairness rule (frozen): **No arm may receive information unavailable in principle to the others
unless that difference is itself the declared manipulation.** The comparison tests organization
and succession machinery, not secretly unequal knowledge. All arms start from the same
predecessor checkpoint (shared Worker-A baseline) exactly as G0/G1 do.

Condition-specific epistemic state (poison item, contradictory claims, reusable procedure) is
injected into the INHERITED predecessor transcript by `inject_predecessor_state()` so every arm
receives it through its own representation — the manipulation is the state itself, not a
privileged channel (Athena F2/F5/F7 resolved).

### Adapter boundary contract (each adapter must expose)

```json
{
  "arm": "…",
  "what_enters": "…",
  "representation": "…",
  "token_cost_model": "…",
  "provenance_survives": true/false,
  "successor_inspectable": ["…"],
  "cannot_cross_arms": ["…"]
}
```

## 3. Hypotheses (frozen, from work order)

| Hypothesis | Prediction |
|---|---|
| H0 null | After controlling for base model, task, token budget, compute, tools, information availability, verified succession provides no meaningful advantage over cheaper inheritance |
| H1 verified succession | E improves successor performance/recovery/reliability vs cheaper memory after accounting for cost |
| H2 procedures | F produces greater succession value than E alone |
| H3 reliability-only | E/F improve reliability/safety but not capability — a valid, narrowable result |
| H4 lock-in | persistent verified state preserves errors so strongly that costs equal/exceed benefits |
| H5 observer-dependence | gains fail to survive replacement of original model/session/agent |

Primary comparisons (frozen; from canon §10.6):
1. E vs B (verified vs transcript)
2. E vs D (verified vs retrieval)
3. F vs E (marginal value of procedures + adversarial review)
4. Cross-observer vs same-observer (H5)
5. Clean vs poisoned-state conditions

## 4. Metrics (frozen; component metrics preserved, no premature composite)

| Metric | Meaning |
|---|---|
| `hidden_test_passed` | Binary; final task quality against hidden tests |
| `final_task_quality` | 0..1 continuous; hidden-test score |
| `steps_to_productive_action` | Work steps before first correct contribution |
| `repeated_work_rate` | Fraction of predecessor work duplicated |
| `contradiction_rate` | Fraction of successor actions contradicting inherited state |
| `unsupported_claim_rate` | Fraction of successor claims without evidence reference |
| `recovery_after_interruption` | Binary/score for resume-from-interruption scenario |
| `recovery_after_requirement_change` | Binary/score for requirement-reversal scenario |
| `stale_state_correction` | Whether successor detected + corrected injected staleness |
| `poisoned_state_resistance` | Whether successor rejected injected poison |
| `handoff_reconstruction_accuracy` | Accuracy of reconstructing required state from inherit |
| `human_intervention_burden` | Count of escalations/corrections |
| `token_usage` | Total tokens |
| `provider_calls` | Physical provider invocations |
| `latency` | Seconds (recorded, not scored) |
| `monetary_cost` | Micro-USD where available |
| `cross_observer_transfer` | Transfer metric for model-session equivalents |

Threshold freeze happens before the powered run by Marc after reviewing this protocol + the
instrumentation pilot output. No threshold may be changed after seeing confirmatory results.

## 5. PASS logic (frozen)

Strong PASS requires evidence E and/or F provide useful succession value **while not** buying
that advantage through unacceptable degradation elsewhere. At minimum test:

```text
productive start improves
AND repeated work decreases
AND final task quality preserved or improved
AND stale/contradictory state handled at least as well
AND poisoned inheritance within predefined tolerance
AND costs do not erase benefit
AND effect survives observer replacement
```

No arm receives PASS merely for winning one metric.

## 6. Conditions (frozen)

| Condition | Scenario |
|---|---|
| clean | Normal worker replacement |
| interruption | Worker disappears before completion; successor resumes |
| reversal | Requirement reversal; formerly correct decision becomes stale |
| contradiction | Inherited state contains mutually inconsistent claims |
| poison | Plausible but false state item inserted (no exploit payload; epistemic integrity only) |
| motif | A later task can reuse a procedure learned earlier |

## 7. Contamination controls (frozen)

Frozen protections (executed by the harness):
- different hidden information between arms — prohibited
- different tool access — prohibited
- different base prompts — prohibited
- different task bytes — prohibited
- different compute budgets — accounted, reported
- different retry rules — prohibited
- cross-arm state leakage — rejected
- reviewer leakage — rejected
- analysis leakage into execution — rejected
- manual rescue of favored arms — prohibited
- unequal context windows — reported, not silently equalized
- changing scoring after results — prohibited (this protocol is the freeze)
- changing prompts after results — prohibited
- silent provider/model substitution — rejected

Mechanism: semantic canaries embedded per arm in a metadata envelope; canary detection is
deterministic and tested; every trajectory carries isolation verification.

## 8. Analysis freeze (frozen for powered run)

- Primary: E vs B, E vs D, F vs E on the frozen metrics, with paired structure per family
- Secondary: cross-observer vs same-observer; clean vs poisoned
- Reporting: effect sizes + uncertainty intervals (never p-value theater)
- Custom statistical primitives: reference-vector tests against an independent implementation
  (EE BCa defect is institutional scar tissue; any custom primitive is validated against an
  independent implementation before the powered run)
- No threshold selected after data
- EE-scar analysis (frozen): per-arm sensitivity/specificity decomposition via
  `sensitivity_specificity_report()`; PASS requires sensitivity improvement WITHOUT
  specificity degradation beyond `max_specificity_degradation` (0.05 default). The EE 2×2
  failure mode (E raises sensitivity but degrades specificity) is instrumented, not hidden.
- PASS logic is executable: `evaluate_pass()` implements the protocol §5 conjunctive rule
  with frozen `DEFAULT_PASS_THRESHOLDS`; thresholds cannot be changed after data. No arm
  receives PASS for winning one metric.

## 9. Small pilot (instrumentation)

The instrumentation pilot is a **deterministic dry-run** of all six arms across the six
conditions, exercising: adapter transforms, isolation, canaries, scoring, accounting,
evidence assembly, analysis reconstruction. It makes zero provider calls and zero spend
(§17 — no new external spend is authorized for calibration). Its purpose is to validate the
measurement chassis, not to estimate treatment effects. Any measurement defect found is
repaired via smallest bounded repair; architecture prompts are not tuned on arm outcomes.

## 10. Powered VS-1 run — NOT authorized

The powered experiment (real provider calls, budget, per-trajectory prompts, hidden task
execution by real successors) is NOT authorized by this protocol. Its launch requires a
separate explicit Marc authorization containing: provider, model, budgets, prompts,
temperature, max_tokens, retry policy, retention, and the spend ceiling.

## 11. Non-claims (frozen)

- This package does NOT estimate treatment effects.
- The instrumentation pilot is NOT a thesis answer.
- No arm is pre-selected. No provider is selected.
- This does NOT authorize any real provider spend.
- This does NOT change ThinkOS product architecture, version, or schemas.
- This does NOT update the Verified Succession canonical paper (v0.1.0 remains canonical).
