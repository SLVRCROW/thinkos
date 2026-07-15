# Context Efficiency Benchmark v0 — G0 Chassis

Deterministic measurement chassis for evaluating context-efficiency across
architecture regimes. The harness has no runtime dependencies and uses the
Python standard library only. **No model, API, or network calls are made.**

Running the benchmark test suite requires `pytest` as a development dependency.

## Purpose

This benchmark measures how efficiently successor agents can resume work using
different context-passing architectures. It does not call any model — it validates
the measurement chassis itself. Real model-backed evaluation is deferred to G1
(parked; not implemented).

## Tasks

Three synthetic tasks, each with 4 stages (Worker A → B → C → D):

| Task | Description | Input |
|------|-------------|-------|
| **A** | Log parsing | `app.log` — timestamped log lines |
| **B** | CSV analysis | `data.csv` — employee records with scores |
| **C** | JSON config normalization | `config.json` — nested application config |

## Conditions

| Condition | Behavior |
|-----------|----------|
| **clean** | Input format matches expected schema |
| **drift** | Input format differs (e.g., unix timestamps vs ISO dates, renamed columns) |

## Architecture Adapters

Three pilot adapters transform a Worker-A transcript into successor state:

| Adapter | Regime | Behavior |
|---------|--------|----------|
| **stateless** | No state | Returns empty dict. Successor starts fresh. |
| **summary** | Deterministic summary | Extracts completed stages and checkpoint IDs from transcript. No model call. |
| **verified_state** | Receipt-backed claims | Every claim references receipt IDs. No free-text model-generated state. |

## Shared Worker-A Baseline

Worker A runs once per task × condition × replicate. Its checkpoint is cloned
into each architecture arm, ensuring every arm starts from identical evidence.

**Pilot accounting:**
- 2 shared Worker-A source sessions
- × 3 architecture arms
- = 6 logical trajectories
- 6 trajectories × 4 worker records = 24 logical session records
- 2 Worker-A sources + 18 successor sessions = 20 unique model-session equivalents

## G0 Gates (14 total)

1. **Fixture generation** — 6 fixture sets (3 tasks × 2 conditions)
2. **Drift detection** — drift inputs differ from clean
3. **Good checkpoint acceptance** — 24/24 known-good artifacts accepted
4. **Bad checkpoint rejection** — 24/24 known-bad artifacts rejected
5. **SHA256 recording** — 6/6 checkpoints record artifact hash
6. **Adapter exercise** — all 3 adapters produce valid ArchitectureState
7. **Verified state receipt-backed claims** — claims reference receipt IDs
8. **Unsupported claims omitted** — no free-text summary/analysis in verified state
9. **Adapter isolation** — each adapter produces independent state
10. **Traversal rejection** — a safe path is accepted and `../` traversal is rejected
11. **Shared Worker-A baseline** — 2 unique checkpoints, 6 clones
12. **24 vs 20 accounting** — generic and pilot accounting validated
13. **Deterministic scoring** — identical inputs produce identical scores
14. **No network/model calls** — checks that suspicious provider/network modules
    are not imported. The test suite independently patches
    `socket.create_connection` and `socket.socket.connect`, runs the complete
    dry run, and asserts zero calls.

## Commands

```bash
# Run benchmark unit tests (70 tests + 6 subtests)
python -m pytest benchmarks/context_efficiency_v0/tests/ -q

# Run the complete G0 dry-run (14 gates)
python -m benchmarks.context_efficiency_v0

# Compile check
python -m compileall -q benchmarks/context_efficiency_v0/
```

## Output

The dry-run CLI writes to a temporary directory created with
`tempfile.mkdtemp(prefix="g0_dry_run_")`, or to a caller-selected path set
via the `G0_OUTPUT_DIR` environment variable.

The output directory contains:

- `g0_summary.json` — gate results, pass/fail, evidence
- `work/` — generated fixtures, checkpoints, clones, and scoring artifacts

## Security / Isolation

- Fixture input and artifact writers enforce containment using
  `Path.relative_to()` — paths that escape the base directory raise
  `ValueError`.
- Isolation helpers validate trajectory IDs (alphanumeric, hyphens, underscores
  only; `..` rejected).
- Path traversal and sibling-prefix escape are detected and rejected.
- Symlink escape is detected and rejected — symlink targets are resolved
  against the symlink's parent directory.
- Cross-architecture leakage receives dedicated verification
  (`verify_no_leakage`) and test coverage.

## Non-Claims

- G0 does **not** select a winning architecture.
- G0 does **not** call any model, provider, API, or network endpoint.
- G0 does **not** measure real model performance — it validates the measurement
  chassis.
- G0 does **not** implement G1. G1 (model-backed evaluation) is not implemented
  and remains parked. No provider has been selected. No model-backed pilot has
  run.

## G1 Boundary

G1 (model-backed evaluation) is not implemented and remains parked pending a
separately verified contract and authorization. No provider has been selected.
No model-backed pilot has run. G0 is the current and only active benchmark
milestone.
