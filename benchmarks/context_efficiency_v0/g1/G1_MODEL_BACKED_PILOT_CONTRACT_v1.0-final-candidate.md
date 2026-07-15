# G1 Model-Backed Pilot Contract v1.0

## 1. Status and Provenance

| Field | Value |
|---|---|
| Document class | Final candidate — pending Marc's explicit hash acceptance |
| Version | v1.0 |
| Author | Jarvis (ThinkOS bounded operator) |
| Date | 2026-07-15 |
| Supersedes | G1 v1.0-rc through v1.0-rc5 |
| Superseded by | Future accepted version |
| Provenance | Transparent reconstruction from G0 implementation, G0 documentation, Adam OS governance doctrine, Marc's stated objective, Hand's RC1 review, Marc's RC3/RC4/RC5 corrections. No prior G1 contract was recovered. |
| Repository home | `benchmarks/context_efficiency_v0/g1/` (proposed) |
| Governing authority | Marc (final). Hand (review). Jarvis (proposal, no acceptance authority). |

**Authority grant:** This document becomes the accepted G1 v1.0 contract only when Marc explicitly accepts its exact SHA-256 in a separate acceptance receipt. Acceptance grants zero implementation, repository-write, provider-call, network, or spending authority. Every G1 slice requires a separate bounded work order from Marc.

---

## 2. Research Question

Can the deterministic G0 chassis be extended to execute one fixed real model across a predeclared six-trajectory pilot while producing complete provider-bound receipts, internally consistent token and cost accounting, isolated architecture state, deterministic scoring of recorded artifacts, and reproducible evidence packets without contamination or unaccounted provider calls?

---

## 3. G1 Calibration Purpose and Explicit Non-Claims

### Purpose

G1 calibrates the instrumentation pipeline — provider call recording, token accounting, trajectory scoring with real model output — on a fixed, small-scale pilot. It does not select a winning architecture, tune prompts, or produce publishable benchmark numbers.

### Explicit non-claims

- G1 does **not** select a winning architecture.
- G1 does **not** produce generalizable benchmark results.
- G1 does **not** validate any model provider's fitness for production.
- G1 does **not** establish a baseline for future G2+ comparisons unless the same provider, model, prompt, temperature, and budget are frozen identically.
- G1 does **not** replace G0. G0 remains the deterministic reference harness.
- G1 does **not** authorize any architecture for production use.
- G1 does **not** authorize provider spend beyond the explicitly approved budget.
- G1 does **not** authorize silent model selection — provider/model choice requires explicit Marc approval at G1-D.
- G1 does **not** require repeated provider outputs to be identical. The score function is deterministic for identical recorded artifacts; stochastic generation is expected and accepted.
- Architecture comparisons are descriptive and exploratory only. No statistical hypothesis test, p-value threshold, or architecture-ranking requirement is imposed.

---

## 4. Experimental Units and Terminology

| Term | Definition |
|---|---|
| **Trajectory** | One complete run of all 4 workers (A→B→C→D) on one task × condition × architecture. 6 logical trajectories total. |
| **Logical session** | One worker's portion of a trajectory. 24 logical session records total (6 trajectories × 4 workers). |
| **Model-session equivalent** | A unique combination of model invocation context. 20 unique model-session equivalents total (see §11 for allocation). |
| **Architecture condition** | One of the three adapter types: `stateless`, `summary`, `verified_state` |
| **Task** | One of the three task definitions: A, B, C |
| **Drift condition** | `clean` (no drift) or `drift` (injected state divergence) |
| **Provider invocation** | One LLM API call with its full request/response pair. Each attempt gets a unique `provider_invocation_id`. |
| **ProviderCallReceipt** | Immutable record of one provider invocation (see §9) |
| **Pilot** | The full G1-E2 execution: 3 architectures × 2 conditions = 6 trajectories, 24 logical sessions, 20 model-session equivalents |
| **Smoke** | The G1-E1 execution: 1 architecture × 1 condition = 1 trajectory, 4 logical sessions |

---

## 5. Fixed Pilot Topology

```
┌──────────────────────────────────────────────────────────────────┐
│                      G1 Pilot Topology                           │
│                                                                  │
│  2 physical shared Worker-A source sessions                      │
│  (one per condition: clean, drift)                               │
│                                                                  │
│  ┌──────────────────┐                                            │
│  │ Worker-A source  │─── shared baseline for all 3 architectures │
│  │ (condition: X)  │                                            │
│  └────────┬─────────┘                                            │
│           │                                                      │
│    ┌──────┼──────┐                                               │
│    ▼      ▼      ▼                                               │
│ ┌─────┐ ┌─────┐ ┌─────┐                                         │
│ │Arch │ │Arch │ │Arch │  ← 3 architecture arms                   │
│ │  A  │ │  B  │ │  C  │                                         │
│ └──┬──┘ └──┬──┘ └──┬──┘                                         │
│    │       │       │                                             │
│    ▼       ▼       ▼                                             │
│ ┌─────┐ ┌─────┐ ┌─────┐                                         │
│ │ Wkr │ │ Wkr │ │ Wkr │  ← Worker B (stage 2)                   │
│ │  B  │ │  B  │ │  B  │                                         │
│ └──┬──┘ └──┬──┘ └──┬──┘                                         │
│    │       │       │                                             │
│    ▼       ▼       ▼                                             │
│ ┌─────┐ ┌─────┐ ┌─────┐                                         │
│ │ Wkr │ │ Wkr │ │ Wkr │  ← Worker C (stage 3)                   │
│ │  C  │ │  C  │ │  C  │                                         │
│ └──┬──┘ └──┬──┘ └──┬──┘                                         │
│    │       │       │                                             │
│    ▼       ▼       ▼                                             │
│ ┌─────┐ ┌─────┐ ┌─────┐                                         │
│ │ Wkr │ │ Wkr │ │ Wkr │  ← Worker D (stage 4)                   │
│ │  D  │ │  D  │ │  D  │                                         │
│ └─────┘ └─────┘ └─────┘                                         │
│                                                                  │
│  Per condition (clean, drift):                                   │
│    1 shared Worker-A source → 3 architecture arms               │
│    = 3 trajectories per condition                                 │
│    = 6 trajectories total                                        │
│    = 24 logical session records                                  │
│    = 20 unique model-session equivalents                          │
│    = 20 physical provider invocations (with zero retries)         │
│                                                                  │
│  No statistical replicates in G1.                                 │
│  The 54-trajectory design is reserved for a later powered        │
│  benchmark.                                                      │
└──────────────────────────────────────────────────────────────────┘
```

**Key topology rules:**
- One physical Worker-A source session per condition (clean, drift). Its provider invocation is shared across all 3 architecture arms.
- Workers B, C, D are independent per architecture arm (3 invocations each per condition).
- All workers within one trajectory share the same model provider and model.
- Exactly one real provider/model throughout G1-E1 and G1-E2. The mock provider at G1-D is excluded from this rule.
- With zero retries: 20 unique logical model sessions = 20 physical provider invocations.

---

## 6. Architecture Conditions

Exactly three, inherited from G0, unchanged:

| Architecture | Adapter | State representation | token_cost |
|---|---|---|---|
| `stateless` | `StatelessAdapter` | Empty dict `{}` | `0` |
| `summary` | `SummaryAdapter` | Dict with `completed_stages`, `checkpoint_receipt_ids` | `len(json.dumps(content, sort_keys=True)) // 4` |
| `verified_state` | `VerifiedStateAdapter` | Dict with `claims` (each receipt-backed) | `len(json.dumps(content, sort_keys=True)) // 4` |

**G1 clarification:** The `token_cost` field in `ArchitectureState` is a deterministic approximate state-transfer token count. It is not a provider-reported token count and is not guaranteed to be fixed for every stage or claim. Actual provider usage and monetary cost live only in G1 `ProviderCallReceipt` records and G1 accounting. No `model_call_cost` field is added to `ArchitectureState`.

---

## 7. Task and Trajectory Design

### Tasks (inherited from G0, unchanged)

| Task | Input file | Description |
|---|---|---|
| A | `app.log` | Log parsing |
| B | `data.csv` | CSV analysis |
| C | `config.json` | JSON configuration normalization |

### G1 task selection

G1 uses exactly:
- **Task A clean** — one shared Worker-A source fixture
- **Task A drift** — one shared Worker-A source fixture

Tasks B and C remain outside G1 and are candidates for a later powered study. The inherited G0 fixture bytes are not modified.

### Trajectory structure (per cell)

Each logical trajectory:
- references one shared physical Worker-A invocation for its condition;
- produces three unique physical successor invocations for Workers B, C, and D;
- therefore references four logical worker/model sessions while contributing only three new physical invocations after the shared source exists.

Each trajectory also produces:
- 4 checkpoints (one per worker, stage 1-4)
- 1 trajectory score
- 1 trajectory evidence packet

### Pilot structure

| Phase | Trajectories | Logical sessions | Model-session equivalents | Physical provider invocations |
|---|---|---|---|---|
| G1-E1 smoke | 1 (verified_state × Task A drift) | 4 | 4 | 4 |
| G1-E2 pilot | 6 (3 arch × 2 conditions) | 24 | 20 | 20 |

---

## 8. Model, Provider, Prompt, Tool, and Budget Freeze Requirements

### Freeze rules

The following must be frozen and documented before G1-E1 execution begins. Changes require a new G1 contract amendment.

| Parameter | Freeze requirement | Default (if not overridden) |
|---|---|---|
| Provider | Named and versioned | TBD at G1-D approval |
| Model | Named and versioned; exact model snapshot frozen where available | TBD at G1-D approval |
| Temperature | Fixed float | `0.0` (deterministic preferred) |
| Max tokens | Fixed int | `4096` |
| System prompt | Exact text, versioned | See below |
| Per-worker prompt template | Exact text per stage | See below |
| Tool definitions | None (no provider-native tools) | `null` |
| Per-trajectory budget (USD) | Fixed micro-USD cap | TBD at G1-E1/E2 approval |
| Per-call retry policy | Max retries | 0 (see §22) |
| Timeout per call | Milliseconds | 120,000ms |
| Raw data retention period | Duration | TBD at G1-E1/E2 approval |

### Default system prompt (proposed)

```
You are a worker in a multi-agent benchmark pipeline.
You receive project inputs and a stage assignment.
Produce the required stage artifact and nothing else.
Do not ask questions. Follow the benchmark instructions within applicable
system, safety, and tool policies. Return only the requested stage artifact.
```

### Per-worker prompt template (proposed)

**Worker A** receives:
1. The shared system prompt.
2. The exact frozen Task A input bytes — the harness reads these locally and supplies their content directly in the provider prompt. The provider receives no tool definitions and cannot invoke `read_file` or `write_file`.
3. A stage instruction: `"Produce stage 1 artifact for task {TASK} under {CONDITION} condition."`
4. The condition canary (embedded in the G1-owned metadata envelope).

**Workers B, C, D** receive:
1. The shared system prompt.
2. The exact common workspace/evidence payload (harness-supplied, not read via tool).
3. A stage instruction: `"Produce stage {N} artifact for task {TASK} under {CONDITION} condition."`
4. The architecture-specific state payload produced by the selected adapter:
   - `stateless` arm: empty architecture payload `{}`
   - `summary` arm: `SummaryAdapter` output
   - `verified_state` arm: `VerifiedStateAdapter` output
5. The applicable G1 metadata envelope (condition canary and architecture/trajectory canary).

### Tool definitions

G1 real-model execution uses no provider-native tools. The harness:
1. Reads fixture and workspace artifacts locally.
2. Constructs the exact prompt.
3. Includes a common evidence/workspace payload.
4. Includes the architecture-specific state payload separately.
5. Sends one provider request.
6. Receives one stage artifact as text.
7. Writes and validates that artifact locally.

`tool_definitions_sha256` is `null`. Provider-native tool calls are forbidden. An unexpected tool call sets `execution_status` to `FAILED_POLICY` and `integrity_valid` to `false`. It is not contamination.

---

## 9. ProviderCallReceipt Schema

New schema, lives in `benchmarks/context_efficiency_v0/g1/schemas.py`.

The `ProviderCallReceipt` records invocation metadata, usage, hashes, lineage, and status. Raw request/response content is stored separately under the retention policy (see §13).

```python
@dataclasses.dataclass(frozen=True)
class ProviderCallReceipt:
    # Identity
    receipt_id: str                    # SHA-256 of canonical JSON (self-excluding)
    pilot_id: str                      # e.g. "g1-e2-20260715"
    run_id: str                        # Unique run identifier within the pilot
    provider_invocation_id: str        # Unique per invocation attempt
    attempt_index: int                 # 0 = first attempt, 1 = first retry, etc.
    trajectory_id: str                 # e.g. "A-clean-verified_state"
    logical_session_id: str            # e.g. "A-clean-verified_state-B"
    model_session_id: str              # e.g. "A-clean-verified_state-B-m0"
    worker_label: str                  # "A", "B", "C", or "D"
    stage: int                         # 1-4

    # Provider identity
    requested_provider: str            # e.g. "openai", "anthropic", "ollama"
    requested_model: str               # e.g. "gpt-4o", "claude-sonnet-4"
    returned_model: str | None         # Provider-reported model identifier, if available
    model_identity_valid: bool         # True if returned_model matches requested_model
                                       # within the approved alias/snapshot relationship

    # Request tracking
    provider_request_id: str | None    # Provider-assigned request ID, if available
    request_dispatched: bool           # True if the request was sent to the provider
    temperature_milli: int              # Temperature in millidegrees (e.g. 0 = 0.0)
    max_tokens: int

    # Request hashes
    system_prompt_sha256: str          # SHA-256 of system prompt text
    prompt_sha256: str                 # SHA-256 of full prompt (system + messages)
    tool_definitions_sha256: None      # Always null — no provider-native tools

    # Token usage (int | None — null means provider did not report)
    prompt_tokens_total: int | None
    cached_input_tokens: int | None   # Where applicable; null otherwise
    uncached_input_tokens: int | None  # Computed: prompt_tokens_total - cached_input_tokens
    completion_tokens: int | None
    provider_usage_status: str         # "reported", "partial", "missing", "error"

    # Response
    response_sha256: str | None         # SHA-256 of full response content; null on timeout/error
    provider_finish_reason: str | None  # "stop", "length", "tool_calls", "error", null
    response_present: bool              # True if a response was received

    # Error handling
    sanitized_error_sha256: str | None  # SHA-256 of sanitized error message; null on success
    normalized_execution_status: str    # "completed", "timeout", "provider_error",
                                        # "policy_denied", "no_response"

    # Timing (RFC 3339 UTC + monotonic integer milliseconds)
    start_timestamp: str               # RFC 3339 UTC
    end_timestamp: str                 # RFC 3339 UTC
    duration_ms: int                   # Monotonic clock, integer milliseconds

    # Cost
    calculated_micro_usd_cost: int | None  # Computed from token counts and frozen prices
    provider_reported_cost_micro_usd: int | None  # Provider-reported cost, if available
    pricing_source: str | None          # Reference to pricing_catalog.json entry
    call_accounting_valid: bool         # True only if all required token counts are non-null
                                         # and decomposition is consistent

    # Raw content hashes (content stored separately under retention policy)
    raw_prompt_sha256: str             # SHA-256 of raw prompt content
    raw_response_sha256: str | None    # SHA-256 of raw response content; null on timeout/error

    # Lineage
    shared_source_id: str | None       # If this call is shared across architectures
    parent_receipt_ids: tuple[str, ...] = ()
    tool_call_receipt_ids: tuple[str, ...] = ()  # G0 tool call receipts that triggered this call

    # Integrity
    contamination_flags: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict: ...
    @classmethod
    def from_json(cls, d: dict) -> ProviderCallReceipt: ...
```

---

## 10. Token, Time, and Integer Micro-USD Accounting

### Integer pricing

All prices are expressed as integer micro-USD per million tokens:

```
price_per_million_uncached_input_tokens: int   # e.g. 2_500_000 for $2.50/1M
price_per_million_cached_input_tokens: int     # e.g. 1_250_000 for $1.25/1M
price_per_million_output_tokens: int           # e.g. 10_000_000 for $10.00/1M
```

Each price entry includes:
- provider, model, category (uncached input / cached input / output)
- effective date (RFC 3339 UTC)
- source provenance (URL or document reference)

### Cached-token decomposition

The provider adapter created at G1-D must declare whether raw `prompt_tokens_total` includes `cached_input_tokens`.

If `prompt_tokens_total` includes cached input:
```
uncached_input_tokens = prompt_tokens_total - cached_input_tokens
```

If `prompt_tokens_total` does not include cached input:
```
uncached_input_tokens = prompt_tokens_total
```

If the provider explicitly reports no cached usage, normalize `cached_input_tokens` to `0`. A `null` value means unknown or unavailable, not zero.

**Never charge `prompt_tokens_total` and `cached_input_tokens` simultaneously.** The calculated cost uses decomposed fields:

```
calculated_micro_usd_cost = (
    (uncached_input_tokens * price_per_million_uncached_input_tokens + 999_999) // 1_000_000
    + (cached_input_tokens * price_per_million_cached_input_tokens + 999_999) // 1_000_000
    + (completion_tokens * price_per_million_output_tokens + 999_999) // 1_000_000
)
```

If decomposition is negative, missing, or ambiguous:
- `calculated_micro_usd_cost = null`
- `call_accounting_valid = false`

### Per-call cost computation (ceiling division)

Ceiling division ensures that even a single token is billed at minimum 1 micro-USD. No floating-point arithmetic is used anywhere in the cost pipeline.

### Accounting rules

1. **Token counts** come from the provider's reported usage fields. If the provider does not report token counts, the corresponding field is `null`, `provider_usage_status` is set to `"missing"`, `calculated_micro_usd_cost` is `null`, and `call_accounting_valid` is `false`.

2. **Duration** is measured with a monotonic clock (`time.monotonic_ns()`) and recorded as integer milliseconds.

3. **Timestamps** are RFC 3339 UTC strings (e.g., `"2026-07-15T12:00:00Z"`).

4. **Integer arithmetic only.** All cost computations use integer micro-USD with ceiling division. No floating-point cost accumulation.

5. **`call_accounting_valid`** applies only to that physical invocation. Trajectory and pilot accounting validity remain aggregate fields.

6. **`calculated_micro_usd_cost`** is the computed cost from token counts and frozen prices. It is not called the exact provider-billed amount unless provider billing evidence supports that claim. The optional `provider_reported_cost_micro_usd` field records the provider's own cost figure when available.

### Physical and logical accounting invariants

**Physical accounting:**
- Sum each physical `ProviderCallReceipt.calculated_micro_usd_cost` exactly once.
- Require 20 unique physical invocation IDs in E2 with zero retries.
- Require the sum to equal `total_calculated_pilot_cost`.

**Logical accounting:**
- Each logical trajectory total equals its three unique successor calculated costs plus its allocated Worker-A share.
- The sum of all six logical trajectory totals must equal `total_calculated_pilot_cost`.
- This equality is valid only when every call cost is non-null, every call is accounting-valid, shared-source IDs are unique per condition, and allocation invariants pass.

**Deduplication:**
- Repeated references to one shared Worker-A receipt never count as additional physical cost.
- Physical accounting deduplicates by `provider_invocation_id` and rejects conflicting duplicate IDs.

### Accounting verification

The G1 accounting module must verify:
- No trajectory exceeds its budget
- All non-null token counts are non-negative
- All durations are positive
- Shared Worker-A costs are allocated correctly (see §11)
- Cached-token decomposition is consistent and non-negative

---

## 11. Shared Worker-A Lineage and Cost Allocation

### Lineage

One physical Worker-A source session exists per condition (clean, drift). Its single provider invocation produces one checkpoint that is cloned to all 3 architecture arms.

```
Physical Worker-A invocation (condition: clean)
  → cloned to architecture: stateless
  → cloned to architecture: summary
  → cloned to architecture: verified_state
```

### Identity

- The physical Worker-A invocation gets one `provider_invocation_id`.
- Each logical trajectory gets its own `logical_session_id` for Worker-A, referencing the shared `provider_invocation_id` via `shared_source_id`.
- Each architecture arm's Worker-A checkpoint receipt references the same physical source.

### Cost separation

Five distinct cost values are tracked:

| Cost term | Definition |
|---|---|
| `physical_calculated_cost` | Calculated cost for one physical provider invocation |
| `shared_source_cost` | Same as `physical_calculated_cost` for the shared Worker-A call |
| `allocated_shared_source_cost` | Portion of `shared_source_cost` assigned to one architecture arm. Sum across 3 arms = `shared_source_cost`. |
| `logical_trajectory_cost` | Sum of three unique successor-call calculated costs plus the allocated share of Worker-A |
| `total_calculated_pilot_cost` | The sum of `calculated_micro_usd_cost` for every unique physical provider invocation, counted exactly once. This is a calculated experimental cost, not a claim about the provider's final invoice. |

### Allocation rule

Frozen allocation order: stateless, summary, verified_state.

```
base = shared_source_cost // 3
remainder = shared_source_cost % 3
```

- stateless: `base + (1 if remainder > 0 else 0)`
- summary: `base + (1 if remainder > 1 else 0)`
- verified_state: `base`

The sum of `allocated_shared_source_cost` across all 3 arms equals `shared_source_cost` exactly.

### Model-session equivalents

| Component | Unique model-session equivalents | Physical invocations |
|---|---|---|
| Worker-A (shared, per condition) | 1 per condition = 2 | 2 |
| Worker-B (per architecture, per condition) | 3 per condition = 6 | 6 |
| Worker-C (per architecture, per condition) | 3 per condition = 6 | 6 |
| Worker-D (per architecture, per condition) | 3 per condition = 6 | 6 |
| **Total** | **20** | **20** |

---

## 12. Architecture Isolation and Semantic Canaries

### Isolation rules

1. Each architecture condition runs in its own isolated working directory (inherited from G0's `create_isolated_workdir`).
2. No architecture's artifacts, receipts, or state are accessible to another architecture's workers.
3. The shared Worker-A baseline is cloned, not shared by reference. Each architecture gets its own copy.
4. Provider calls for different architectures use separate provider client instances.

### Semantic canaries

Semantic canaries are hidden test values embedded in a G1-owned metadata envelope. G0 fixture files are not modified to embed canaries.

**Acknowledgment:** Canaries are embedded in the prompt text that the model receives. They may influence the model's output. The design uses high-entropy deterministic values from the frozen pilot manifest rather than ordinary semantic words.

**Canary visibility by worker:**

| Worker | Canaries visible |
|---|---|
| Worker A (shared) | Condition canary only |
| Workers B, C, D (per arm) | Condition canary + architecture/trajectory canary unique to their arm |

**Canary definitions:**

| Canary | Value format | Visible to | Forbidden visibility |
|---|---|---|---|
| `CANARY_CONDITION` | High-entropy deterministic value from pilot manifest | All workers in the condition | Visible in the wrong condition's artifacts |
| `CANARY_ARCHITECTURE` | High-entropy deterministic value from pilot manifest | Workers B, C, D in the architecture arm | Visible in another architecture's artifacts |
| `CANARY_TRAJECTORY` | High-entropy deterministic value from pilot manifest | Workers B, C, D in the trajectory | Visible in another trajectory's artifacts |

**Detection:** After each trajectory completes, the evidence packet is scanned for canary values that belong to other trajectories or conditions. Detection is deterministic — a string match against the known canary values for all trajectories in the pilot.

Cross-arm or cross-condition canary visibility sets `contamination_detected = True`.

Failure to emit one's own canary is not automatically cross-arm contamination. It is recorded as `canary_observation_incomplete` unless the scoring contract explicitly requires emission.

---

## 13. Raw Prompt/Output Retention and Deletion Policy

### Retention rules

1. **Credentials and authorization headers are never retained.** The provider client strips these before writing raw files.
2. **Paths are relative sandboxed paths only.** No absolute paths appear in receipts.
3. **SHA-256 hashes** of the raw prompt and raw response are recorded in the `ProviderCallReceipt` (`raw_prompt_sha256`, `raw_response_sha256`) before the raw files are written.

| Artifact | Retention | Location |
|---|---|---|
| `ProviderCallReceipt` | Permanent | In trajectory evidence packet |
| Raw prompt (system + messages) | Until pilot disposition is finalized | `G1_RUN_ROOT/<pilot_id>/raw/` (relative path) |
| Raw response (full output) | Until pilot disposition is finalized | Same as above |
| Aggregated scores | Permanent | In pilot evidence packet |
| Per-trajectory scores | Permanent | In trajectory evidence packet |

**Location rules:**
- Source code remains under `benchmarks/context_efficiency_v0/g1/`.
- Runtime evidence (raw prompts, responses, run database, provider receipts, pilot evidence packets) lives under a separately approved external run root: `G1_RUN_ROOT/<pilot_id>/`.
- `G1_RUN_ROOT` is outside the ThinkOS repository, frozen before E1/E2, and path-sandboxed.
- Receipts store paths relative to `G1_RUN_ROOT`.
- No raw prompt, response, run database, provider receipt, or pilot evidence packet is written into the Git worktree.
- Repository status verification must prove no run artifacts entered the repo.
- The final absolute run root is not invented in this contract. It is deferred to the E1 approval after lane compatibility is verified.

### Retention period

The retention period for raw prompts and responses is frozen before G1-E1 and G1-E2 approval. Default: 30 days from pilot disposition.

### Deletion procedure

1. SHA-256 hashes in the receipts are preserved (immutable evidence that the call happened).
2. The raw files are deleted through the authorized filesystem mechanism.
3. Absence is verified.
4. A `RawArtifactDeletionReceipt` is appended to the trajectory evidence packet, referencing:
   - the original provider receipt ID
   - the original relative path
   - the original hash
   - the deletion timestamp (RFC 3339 UTC)
   - the actor
   - the authority reference
   - the verification result
5. **The `ProviderCallReceipt` remains immutable.** Fields are not set to null after deletion.
6. **Authorized post-disposition deletion does not invalidate the pilot.** The SHA-256 hashes in the receipts provide integrity evidence.
7. The contract does not claim "secure deletion" unless secure erasure is independently proven. The term used is "authorized deletion with absence verification."

---

## 14. Orthogonal Result Dimensions

Each trajectory receives exactly one value in each of these orthogonal dimensions. They are not collapsed into a single disposition.

### execution_status

| Value | Meaning |
|---|---|
| `COMPLETE` | All 4 workers completed all stages |
| `PARTIAL` | Some workers completed, some did not |
| `FAILED_POLICY` | A policy violation occurred (e.g., unexpected tool call) |
| `ABORTED` | Explicitly stopped by operator or Marc |
| `NOT_STARTED` | Trajectory was never launched |

### integrity_valid

| Value | Meaning |
|---|---|
| `TRUE` | All integrity checks passed (isolation, canary, file integrity) |
| `FALSE` | At least one integrity check failed |

### contamination_detected

| Value | Meaning |
|---|---|
| `TRUE` | Cross-trajectory, cross-architecture, or cross-condition information leakage detected; or a foreign canary was observed |
| `FALSE` | No contamination detected |

Contamination flags are limited to:
- cross-trajectory information leakage
- cross-architecture information leakage
- cross-condition information leakage
- foreign canary detection

The following are **not** contamination:
- timeout (execution event)
- provider error (execution event)
- retry exhaustion (execution event)
- incomplete response (execution event)
- unexpected tool call (policy/integrity event)
- parameter drift (policy/integrity event)
- model identity mismatch (policy/integrity event)
- suspected prompt injection (security observation)
- manual security review (security observation)

### accounting_valid

| Value | Meaning |
|---|---|
| `TRUE` | All provider invocations reported non-null token counts, sum-of-parts verified |
| `FALSE` | Missing token counts or accounting verification failed |

### task_score_valid

| Value | Meaning |
|---|---|
| `TRUE` | Task scoring completed without error |
| `FALSE` | Task scoring could not be computed |

### warnings

A tuple of zero or more warning strings. Warnings are informational and do not invalidate scores unless Marc decides otherwise.

---

## 15. Integrity and Contamination Flags

Flags are additive. A trajectory may carry multiple flags.

### Contamination flags (set contamination_detected = True)

| Flag | Meaning | Trigger |
|---|---|---|
| `CONTAMINATION_CROSS_TRAJECTORY_LEAKAGE` | Artifact from another trajectory found in working directory | Automated check |
| `CONTAMINATION_CROSS_ARCHITECTURE_LEAKAGE` | Artifact from another architecture found in working directory | Automated check |
| `CONTAMINATION_CROSS_CONDITION_LEAKAGE` | Artifact from the wrong condition found in working directory | Automated check |
| `CONTAMINATION_FOREIGN_CANARY` | Canary value from another trajectory, architecture, or condition detected | Automated check |

### Execution event flags (do not set contamination_detected)

| Flag | Meaning | Trigger |
|---|---|---|
| `EVENT_TIMEOUT` | Provider call exceeded timeout | Automated check |
| `EVENT_PROVIDER_ERROR` | Provider returned an error | Automated check |
| `EVENT_RETRY_EXHAUSTED` | Provider call exhausted retries | Automated check |
| `EVENT_INCOMPLETE_RESPONSE` | Response was truncated or incomplete | Automated check |

### Policy/integrity flags (do not set contamination_detected)

| Flag | Meaning | Trigger |
|---|---|---|
| `POLICY_UNEXPECTED_TOOL_CALL` | Model made a tool call outside the allowed set | Automated check |
| `POLICY_PARAMETER_DRIFT` | Frozen parameter changed between trajectories | Automated check |
| `POLICY_MODEL_IDENTITY_MISMATCH` | Returned model differs from requested model | Automated check |

### Security observation flags (do not set contamination_detected)

| Flag | Meaning | Trigger |
|---|---|---|
| `SECURITY_SUSPECTED_PROMPT_INJECTION` | Model output contains prompt-injection-like patterns | Automated + manual review |
| `SECURITY_MANUAL_REVIEW` | Flagged for human review | Manual |

### Warning flags

| Flag | Meaning | Trigger |
|---|---|---|
| `WARNING_HIGH_TEMPERATURE` | Temperature > 0.0 (non-deterministic) | Automated check |
| `WARNING_PROVIDER_DEGRADATION` | Provider reported degradation or partial outage during run | Manual |
| `WARNING_RETRY_OCCURRED` | At least one provider call required a retry | Automated |

---

## 16. Stop and Invalidation Conditions

### Immediate stop (all provider calls halt, pilot enters ABORTED)

1. Total pilot spend exceeds approved budget (zero overrun tolerance).
2. Any trajectory exceeds its per-trajectory budget.
3. A provider returns authentication errors (invalid API key, expired account).
4. Marc explicitly orders a stop.
5. A red-zone governance denial occurs.
6. A pre-dispatch worst-case cost check shows that the next authorized call could exceed either the trajectory or pilot ceiling.

### Invalidation (post-hoc)

1. Any isolation breach invalidates the entire pilot.
2. Any unaccounted provider invocation invalidates accounting.
3. Post-hoc review reveals that the frozen parameters were not actually frozen across all trajectories.
4. Post-hoc review reveals that raw prompt/response files were tampered with or lost before disposition without authorized deletion receipts.

**Task failure is recorded as performance data, not infrastructure failure.** A worker that produces an invalid checkpoint does not invalidate the pilot — it produces a lower task score. Missingness and aborted trajectories remain visible in the evidence packet.

---

## 17. G1-A Through G1-E Boundaries

| Phase | Scope | Network/Spend | Approval required |
|---|---|---|---|
| **G1-A** | Schemas (`ProviderCallReceipt`, `G1TrajectoryScore`, `G1PilotEvidence`), serialization and canonical-hash contracts, schema validation tests, contract-boundary tests, network-denial tests | Zero network, zero spend | Separate Marc-authorized G1-A work order |
| **G1-B** | Accounting schemas, calculation logic, synthetic pricing fixtures, evidence-packet construction. Does not freeze a real pricing catalog or selected-provider cached-token mapping (provider selection occurs at G1-D). | Zero network, zero spend | Separate Marc-authorized G1-B work order after G1-A PASS |
| **G1-C** | Isolation, canaries, and deterministic scoring of recorded artifacts. Canary embedding and detection in G1-owned metadata envelope. Scoring module for recorded (non-live) artifacts. | Zero network, zero spend | Separate Marc-authorized G1-C work order after G1-B PASS |
| **G1-D** | Mock provider integration. Freeze selected provider usage mapping, selected model, real pricing catalog with provenance, alias/snapshot relationship, conservative pre-dispatch budget calculation. Mock suite covering: minimal response, realistic response, malformed response, missing usage, timeout, error, retry, ambiguous-result. | Mocks only, zero real spend | Separate Marc-authorized G1-D work order after G1-C PASS |
| **G1-E1** | Smoke test: 1 trajectory (verified_state × Task A drift) with real provider. Same provider/model intended for E2. | Real spend, small budget | Fresh provider/model/network/spend authorization |
| **G1-E2** | Calibration pilot: 6 trajectories (3 arch × 2 conditions) with real provider. | Real spend, full budget | Fresh pilot/network/spend authorization |

**G1-A must pass before G1-B begins.**
**G1-B must pass before G1-C begins.**
**G1-C must pass before G1-D begins.**
**G1-D must pass before G1-E1 begins.**
**G1-E1 must pass before G1-E2 begins.**

---

## 18. Separate G1-E1 Smoke and G1-E2 Pilot Approvals

### G1-E1 smoke cell

G1-E1 is frozen to exactly:

| Parameter | Value |
|---|---|
| Architecture | `verified_state` |
| Task | Task A |
| Condition | `drift` |
| Logical sessions | 4 |
| Physical provider invocations | 4 with zero retries |

**Rationale:** This exercises the largest/most structured state payload (`verified_state` with receipt-backed claims) and the drift path (injected state divergence). G1-D mocks remain responsible for testing all architectures and isolation boundaries before real spend.

### G1-E1 smoke approval requirements

Before G1-E1 can begin, Marc must explicitly approve:
1. The exact provider and model to use (same as intended for E2).
2. The per-trajectory budget in micro-USD.
3. The total smoke budget in micro-USD.
4. The exact system prompt and per-worker prompt templates.
5. The temperature and max_tokens values.
6. The retention period for raw prompts/responses.
7. The retry policy (default: zero automatic retries).

### G1-E2 pilot approval requirements

Before G1-E2 can begin, Marc must explicitly approve:
1. The same provider/model (no change from E1 without rationale).
2. The per-trajectory budget in micro-USD.
3. The total pilot budget in micro-USD.
4. Confirmation that G1-E1 smoke passed with:
   - all 4 logical worker/model sessions complete
   - every physical provider attempt accounted for
   - provider usage present (non-null token counts)
   - `call_accounting_valid = true` for all invocations
   - `model_identity_valid = true`
   - no contamination detected
   - all hard budgets respected
5. Any changes to prompts, temperature, or max_tokens from G1-E1.

---

## 19. Acceptance Criteria

### G1-A acceptance

- [ ] `ProviderCallReceipt` schema is defined, frozen, and tested.
- [ ] `G1TrajectoryScore` schema is defined, frozen, and tested.
- [ ] `G1PilotEvidence` packet schema is defined, frozen, and tested.
- [ ] Serialization and canonical-hash contracts are defined and tested (UTF-8, sorted keys, fixed separators, no NaN/Infinity, self-exclusion per schema).
- [ ] Schema validation tests pass for valid and invalid inputs.
- [ ] Contract-boundary tests verify field types, ranges, and constraints.
- [ ] Network-denial tests prove zero network calls are made.
- [ ] All G1-A tests pass with zero network calls and zero spend.
- [ ] G0 frozen-file manifest hashes are recorded and verified unchanged against `G0_BASE_COMMIT`.

### G1-B acceptance

- [ ] Accounting schemas are defined and frozen.
- [ ] Calculation logic (ceiling division, cached-token decomposition, shared-cost allocation) is implemented and tested.
- [ ] Synthetic pricing fixtures exercise all cost paths.
- [ ] Evidence-packet construction produces valid, complete packets.
- [ ] Sum-of-parts verification passes for all aggregation levels.
- [ ] All G1-B tests pass with zero network calls and zero spend.

### G1-C acceptance

- [ ] G1-owned metadata envelope contains embedded semantic canaries.
- [ ] Canary visibility rules (Worker A vs Workers B/C/D) are enforced.
- [ ] Canary detection is deterministic and tested.
- [ ] Isolation module creates isolated workdirs per architecture and verifies no cross-architecture leakage.
- [ ] Scoring module produces deterministic scores for identical recorded artifacts.
- [ ] All G1-C tests pass with zero network calls and zero spend.

### G1-D acceptance

- [ ] Mock provider adapter returns controlled synthetic responses.
- [ ] Mock suite covers: minimal response, realistic response, malformed response, missing token usage, timeout, error, retry exhaustion, ambiguous-result.
- [ ] Selected provider usage mapping is frozen (whether `prompt_tokens_total` includes `cached_input_tokens`).
- [ ] Selected model is frozen with alias/snapshot relationship.
- [ ] Real pricing catalog with provenance is frozen.
- [ ] Conservative pre-dispatch budget calculation is implemented and tested.
- [ ] Mock-based integration tests exercise all 4 workers and all 4 stages.
- [ ] Mock-based integration tests verify canary detection.
- [ ] Mock-based integration tests verify budget enforcement (pre-dispatch check).
- [ ] Mock-based integration tests verify retry semantics (unique IDs, all attempts counted).
- [ ] All G1-D tests pass with zero real spend.

### G1-E1 acceptance

- [ ] 1 trajectory (verified_state × Task A drift) completes successfully.
- [ ] All 4 logical worker/model sessions complete.
- [ ] Every physical provider attempt is accounted for.
- [ ] Provider usage is present (non-null token counts).
- [ ] `call_accounting_valid = true` for all invocations.
- [ ] `model_identity_valid = true`.
- [ ] No contamination detected.
- [ ] Raw prompts and responses are retained per policy.
- [ ] `execution_status = COMPLETE`.
- [ ] All hard budgets respected.
- [ ] Total spend is within approved smoke budget.

### G1-E2 acceptance

- [ ] All 6 trajectories complete or are explicitly accounted for.
- [ ] No isolation breach occurred (pilot-level integrity).
- [ ] All provider invocations are accounted for (no unaccounted invocations).
- [ ] Task failure is recorded as performance data, not infrastructure failure.
- [ ] Missingness and aborted trajectories remain visible in the evidence packet.
- [ ] Total spend is within approved pilot budget.
- [ ] Full evidence packet is assembled and receipted.
- [ ] Raw prompts/responses are retained per policy.
- [ ] No arbitrary p-value or winner requirement.

---

## 20. Required Receipts and Final Evidence Packet

### Shared-source evidence (pilot level)

Each physical shared Worker-A receipt exists exactly once at the pilot level:

```
pilot_{ID}/
├── pilot_config.json
├── shared_sources/
│   ├── clean/
│   │   ├── provider_call_receipt.json
│   │   ├── checkpoint_receipt.json
│   │   └── raw/
│   └── drift/
│       ├── provider_call_receipt.json
│       ├── checkpoint_receipt.json
│       └── raw/
├── trajectories/
│   └── trajectory_{ID}/
│       ├── config.json
│       ├── worker_A_shared_source_ref.json
│       ├── worker_B/
│       │   ├── provider_call_receipt.json
│       │   ├── checkpoint_receipt.json
│       │   └── raw/
│       ├── worker_C/
│       │   ├── provider_call_receipt.json
│       │   ├── checkpoint_receipt.json
│       │   └── raw/
│       ├── worker_D/
│       │   ├── provider_call_receipt.json
│       │   ├── checkpoint_receipt.json
│       │   └── raw/
│       ├── trajectory_score.json
│       ├── trajectory_result.json
│       └── trajectory_receipt.json
├── pilot_accounting.json
├── pilot_scores.json
├── pilot_result.json
├── pricing_catalog.json
├── provider_selection.md
└── pilot_receipt.json
```

`worker_A_shared_source_ref.json` contains:
- `shared_source_id`
- `provider_invocation_id`
- `provider_receipt_id`
- `checkpoint_receipt_id`
- `condition`
- `allocated_shared_source_cost`

**Requirements:**
- Exactly two physical shared-source provider receipts in E2 (one per condition).
- Exactly three trajectory references to each condition's shared source.
- Reference hashes resolve to the single pilot-level shared receipt.
- No copied Worker-A provider receipt inside trajectory directories.
- Conflicting or unresolved references fail evidence-packet validation.

---

## 21. Canonical Receipt Hashing

### Algorithm

All receipt hashes use SHA-256 with lowercase hexadecimal encoding.

### Canonical JSON serialization

1. **UTF-8 encoding** — all JSON is UTF-8.
2. **Sorted keys** — dictionary keys are sorted lexicographically.
3. **Fixed separators** — `(',', ':')` with no whitespace (compact encoding).
4. **No NaN or Infinity** — JSON must not contain NaN or Infinity values.
5. **Stable array ordering** — arrays must maintain insertion order. Unordered set serialization is rejected.

### Self-exclusion rule

The excluded self-hash field is defined per schema:

- For receipt schemas (`ProviderCallReceipt`, `CheckpointReceipt`, `ToolCallReceipt`): the `receipt_id` field is excluded from its own digest.
- For manifest schemas (`pilot_receipt.json`, `trajectory_receipt.json`): the `checksum` field is excluded from its own digest.

```
# For receipt schemas:
content = {k: v for k, v in receipt.items() if k != "receipt_id"}
canonical_json = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
receipt_id = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

# For manifest schemas:
content = {k: v for k, v in manifest.items() if k != "checksum"}
canonical_json = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
checksum = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
```

---

## 22. Retry Semantics

### Default: zero automatic retries

The default retry policy for real-provider calls is zero automatic retries. A timeout or ambiguous outcome stops that trajectory.

### Identity

Every physical attempt receives a unique `provider_invocation_id` and its own `ProviderCallReceipt`. The `attempt_index` field records the attempt number (0 = first attempt, 1 = first retry, etc.).

### Cost

All billable attempts count toward the trajectory and pilot cost. Every physical attempt remains receipted and counted.

### Zero-retry conditions

- **Authentication or governance denial:** Zero retries. An authentication error (invalid API key, expired account) or governance denial (red-zone block) stops the trajectory immediately.
- **Ambiguous provider mutation result:** Zero retries. If a provider call returns a result where it is unclear whether a mutation was executed, the trajectory stops pending separate verification.

### Non-zero retry policy

Any non-zero retry policy requires later explicit provider-specific approval and proven idempotency semantics. This is not authorized by this contract.

---

## 23. Provider/Model Drift

### Recording

Each `ProviderCallReceipt` records both `requested_model` (the model identifier sent in the API request) and `returned_model` (the model identifier returned by the provider in the response, if available).

### Freeze

The exact model snapshot is frozen where available (e.g., `gpt-4o-2026-05-15` rather than `gpt-4o`). If the provider does not expose snapshot identifiers, the `returned_model` field records what the provider reports.

### Allowed relationship

A provider alias resolving to an approved snapshot is not automatically a mismatch. The allowed relationship between `requested_model` and `returned_model` is frozen before G1-E1:

- If `requested_model` is an alias (e.g., `gpt-4o`), the provider's resolved model must be an approved snapshot (e.g., `gpt-4o-2026-05-15`).
- If `requested_model` is a specific snapshot, `returned_model` must match exactly.
- Unverifiable identity (provider does not return a model identifier) prevents clean PASS.

### Mismatch

If `returned_model` differs from `requested_model` outside the approved alias relationship, the trajectory is flagged with `POLICY_MODEL_IDENTITY_MISMATCH` and `model_identity_valid` is set to `false`.

### One real provider/model only

G1 uses exactly one real provider and one real model across G1-E1 and G1-E2. The mock provider at G1-D is excluded from this rule. No real-provider switching within G1.

---

## 24. G0 Boundary

### Location

G1 lives under `benchmarks/context_efficiency_v0/g1/`.

### Consumption

G1 may consume documented G0 interfaces (schemas, adapters, fixtures, isolation, accounting, scoring) but may not modify frozen G0 files.

### G0 base commit

```
G0_BASE_COMMIT: 9222c0a66a9e786ca9a9f54194d074b42158b783
```

The 13 frozen-file hashes must be computed from that exact commit.

### Frozen G0 files (13 files)

```
benchmarks/context_efficiency_v0/__init__.py
benchmarks/context_efficiency_v0/__main__.py
benchmarks/context_efficiency_v0/accounting.py
benchmarks/context_efficiency_v0/adapters.py
benchmarks/context_efficiency_v0/baseline.py
benchmarks/context_efficiency_v0/checkpoint.py
benchmarks/context_efficiency_v0/fixtures.py
benchmarks/context_efficiency_v0/isolation.py
benchmarks/context_efficiency_v0/schemas.py
benchmarks/context_efficiency_v0/scorer.py
benchmarks/context_efficiency_v0/tests/__init__.py
benchmarks/context_efficiency_v0/tests/test_g0_harness.py
benchmarks/context_efficiency_v0/README.md
```

### Verification

Before G1-A:
1. Verify the commit `9222c0a66a9e786ca9a9f54194d074b42158b783` remains an ancestor of the intended implementation base.
2. Compute and record SHA-256 hashes of all 13 frozen files from `G0_BASE_COMMIT`.
3. Compare hashes after G1-A implementation.
4. Require exact equality.
5. Stop on any mismatch.
6. Do not silently rebase the contract onto changed G0 files.

A later `main` commit may contain the accepted contract or unrelated authorized documentation, but the G0 byte boundary remains bound to `G0_BASE_COMMIT`.

Changes are allowed only under `benchmarks/context_efficiency_v0/g1/` within the separately authorized phase boundary.

---

## 25. Calibration Failure Gates

G1 calibration fails if any of the following conditions are met:

1. **Missing receipt:** Any expected provider receipt is missing from the evidence packet.
2. **Unaccounted invocation:** Any dispatched provider invocation is unaccounted in the accounting records.
3. **Unavailable usage:** Provider usage required for accounting is unavailable (null token counts where the provider was expected to report them).
4. **Cost invariant violation:** Physical and allocated cost invariants disagree (sum of allocated shares does not equal shared physical calculated cost).
5. **Hash failure:** Any canonical hash fails verification.
6. **Scoring non-determinism:** The score function produces different results for identical recorded artifacts.
7. **Isolation failure:** Architecture isolation is breached (artifacts from one architecture appear in another's working directory).
8. **Foreign canary:** A canary value from another trajectory, architecture, or condition is observed.
9. **Model identity failure:** Model identity is invalid or unverifiable.
10. **Parameter drift:** Frozen prompts, parameters, provider, or model drift between trajectories.
11. **Budget failure:** Pre-dispatch budget enforcement fails to prevent an overrun.
12. **Evidence packet failure:** Evidence packet reconstruction fails (missing files, malformed JSON, hash mismatch).

---

## 26. Open Decisions (Resolved)

| Decision | Resolution |
|---|---|
| Should G1-E1 smoke use the same provider as E2? | **Yes.** E1 uses the same provider/model intended for E2. |
| Should the mock provider return realistic or minimal responses? | **Both.** Mock suite covers minimal, realistic, malformed, missing usage, timeout, error, retry, and ambiguous-result cases. |
| Should G1 support multiple real providers? | **No.** One real provider only in G1. The mock provider at G1-D is excluded from this rule. |
| What is the acceptable per-trajectory budget? | **Deferred to G1-D.** Budget amount is determined during provider selection. |
| Should G1 produce a composite score or sub-scores? | **Separate diagnostic sub-scores.** An optional predeclared secondary composite may be defined but is not required. |
| What is the default retry policy? | **Zero automatic retries.** Any non-zero policy requires separate provider-specific approval. |
| Which tasks does G1 use? | **Task A only** (clean and drift). Tasks B and C are deferred. |
| Does ArchitectureState get a model_call_cost field? | **No.** The frozen G0 interface is preserved. Provider costs live only in G1 receipts. |
| Are provider-native tools used? | **No.** Tool definitions are null. Unexpected tool calls are policy violations. |
| How are canaries embedded? | **G1-owned metadata envelope.** G0 fixture files are not modified. |
| What is the G1-E1 smoke cell? | **verified_state × Task A drift.** Largest state payload + drift path. |
| What is the G0 base commit? | **9222c0a66a9e786ca9a9f54194d074b42158b783.** Frozen-file hashes bound to this commit. |

---

## 27. Open Questions (Intentionally Deferred)

| Question | Deferred to | Rationale |
|---|---|---|
| Exact provider and model | G1-D approval | Cannot be decided without mock validation first |
| Exact per-trajectory budget | G1-D approval | Depends on provider pricing |
| Exact per-pilot budget | G1-E1 approval | Depends on smoke test actual cost |
| System prompt exact wording | G1-D approval | May be refined during mock testing |
| Per-worker prompt exact wording | G1-D approval | May be refined during mock testing |
| Temperature > 0.0 consideration | G1-D approval | Non-determinism tradeoff requires discussion |
| Raw prompt/response retention period | G1-E1 approval | Depends on pilot duration and Marc's preference |
| Whether to define a secondary composite score | G1-D approval | Can be decided after seeing sub-score structure |
| Cached-token decomposition rule for selected provider | G1-D approval | Depends on provider's reported usage format |
| Allowed alias-to-snapshot relationship | G1-D approval | Depends on provider's model versioning scheme |
| Absolute G1_RUN_ROOT path | G1-E1 approval | Lane compatibility must be verified first |
