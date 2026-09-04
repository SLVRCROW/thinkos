# VS-1 R3 — F4 Procedure-Routing Audit Table (frozen)

**Authority:** `AUTHORIZE_VS1_R3_INSTRUMENT_COMPLETION_AND_FULL_POWERED_EXECUTION` §8
**Date:** 2026-08-20 · **Branch:** `vs1-preparation`
**Principle:** F may receive the structured reusable verified procedure (declared procedural-succession manipulation). Other arms receive only the historical information their frozen architecture is entitled to represent. E must NOT silently receive F's structured procedure object. No arm is artificially crippled; no arm is given F's structured object.

## Motif condition — what each arm's model-visible content contains

| ARM | COMMON TASK SUBSTRATE | HISTORICAL INFO AVAILABLE | INHERITED REPRESENTATION | PROCEDURE REPRESENTATION | MODEL-VISIBLE CONTENT |
|---|---|---|---|---|---|
| A stateless | identical (motif substrate) | none | `{}` | none | substrate only |
| B transcript | identical | full predecessor event list (write_file + run procedure event) | `{"events": [1 event]}` | procedure run appears as raw history | substrate + raw event incl. procedure run |
| C summary | identical | compressed stage/claim/procedure projection | `{"claims": [1], "procedures": [1]}` | procedure claim projected as historical summary | substrate + summary incl. procedure claim |
| D retrieval | identical | top-k retrieval over event text | `{"results": [1]}` | procedure event retrievable per frozen BM25 rule | substrate + retrieved event |
| E verified_state | identical | receipt-backed claims only | `{"claims": [1], "procedures": 0}` | **NOT present** — E does not receive F's structured procedure object | substrate + verified claims (no procedure object) |
| F verified_state_procedure | identical | claims + typed procedure records | `{"claims": [1], "procedures": [1]}` | structured reusable verified procedure (scope/inputs/outputs/failure/verification/reuse) | substrate + claims + structured procedure |

## Fairness verification (mechanical)

- `verified_state` adapter output for motif: `procedures=0` — E does NOT receive F's structured procedure. ✓
- `verified_state_procedure` adapter output for motif: `procedures=1` — F receives the structured procedure (declared manipulation). ✓
- B/C/D retain the procedure event as historical information their architecture legitimately permits (transcript raw history; summary projection; retrieval index). ✓
- No arm receives coaching language ("verify carefully", "use provenance", "reuse the procedure" as instruction). The substrate is neutral task information. ✓
- The only difference between E and F is the structured procedure object — the E-vs-F manipulation is preserved. ✓

## Interruption condition — common substrate equality (F3)

The TASK_SUBSTRATE block (CSV schema, stage structure, required outputs) is byte-identical across all six arms for interruption. Verified by regression test D (common-substrate equality). No arm coaching.

---
*This table is the auditable record required by §8. It is frozen for R3.*
