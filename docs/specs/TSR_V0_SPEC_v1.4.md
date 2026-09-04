# TSR V0 SPEC v1.4 — PROSPECTIVE AMENDMENT

**Status:** FROZEN v1.4
prospectively controlling for the worktree-observation contract

**Supersedes:** TSR v1.3 for the worktree-observation contract only, prospectively. TSR v1.3 remains the immutable historical record.
**Controlling decision:** Marc's prospective approval of the ThinkOS TSR v1.4 architecture decision for Alpha 0.2 Patch 1 (2026-09-03).

---

## 1. Amendment scope

This amendment changes ONLY the worktree-observation contract (§3 Probes — the `worktree_dirty` probe) and the reconciliation semantics (§6). All other TSR v1.3 provisions (state set, exit codes, other probes, doctor integration, schema, authority model, §8 determinism/side-effect-freedom, §9 acceptance criteria) remain in force unless explicitly amended here.

## 2. Worktree probe — hazard gate (amends §3 Probe specification)

`_probe_worktree_dirty` MUST first evaluate a read-only hazard gate over the repository's effective attribute and filter configuration:

- Sources inspected (read-only, no execution): repository root `.gitattributes`, nested `.gitattributes`, `.git/info/attributes`, global attributes / `core.attributesFile`, attribute macros affecting filter resolution, and `filter.<driver>.clean|process|required` configuration.
- If any execution-capable conversion/filter configuration is present, OR the effective configuration cannot be safely determined, the worktree probe is **UNEVALUABLE** and `git status` MUST NOT be invoked for that probe.
- Only when the gate reports SAFE may the probe invoke:
  `git -c core.fsmonitor= --no-optional-locks status --porcelain`
  (both global options before the subcommand; empty `core.fsmonitor=` value for legacy-compatible suppression).

## 3. Hazard-gate result contract

```text
SAFE:        absence of execution-capable filter configuration has been positively established
UNSAFE:      an effective driver can execute through clean or process behavior
UNEVALUABLE: any launch failure, nonzero result where meaning is uncertain, decode failure,
             parse ambiguity, unsupported configuration shape, incomplete enumeration, or
             otherwise uncertain observation
```

SAFE must be established before `git status` is reachable. If UNSAFE or UNEVALUABLE, `git status` MUST NOT be invoked.

The hazard gate itself MUST: execute no filter/helper, write nothing, access no network, and mutate no Git state.

### 3a. Bounded batching

Tracked-path enumeration and attribute inspection MUST use bounded batching so the worktree probe does not become UNEVALUABLE solely because a repository has many tracked files (argv/command-line size limit). The implementation work unit defines the exact batch bounds and partition rule. Every tracked path MUST be inspected exactly once; any batch failure, malformed output, decode failure, or path omission/duplication makes the ENTIRE hazard gate UNEVALUABLE. SAFE may be returned only after every tracked path has been successfully inspected and every referenced filter driver has been adjudicated. The stable-configuration threat-model assumption applies across the full multi-batch observation.

### 3b. Tracked submodules / gitlinks — conservative hazard

Tracked submodules/gitlinks are a conservative worktree-observation hazard.

If any tracked gitlink is present, `worktree_dirty` is **UNEVALUABLE** and `git status` MUST NOT be invoked.

Reason: tracked gitlinks introduce nested repository state and configuration outside the bounded v1.4 superproject worktree hazard model. TSR v1.4 does not recursively inspect, sandbox, or reason about nested repository worktree semantics. Therefore, if any tracked gitlink is present, `worktree_dirty` is UNEVALUABLE and `git status` MUST NOT be invoked for that probe.

This is a scope/safety boundary, not a claim that hidden filter execution has been universally reproduced.

Preserved semantics:
- CURRENT requires every well-formed RECORDED probe evaluated + matching;
- therefore submodule-induced UNEVALUABLE → UNKNOWN unless another evaluated mismatch makes the verdict STALE;
- no false CURRENT.

The stable-input threat-model assumption covers repository inputs used by the observation, including tracked gitlink/index structure. ThinkOS does not claim protection against a concurrent writer adding/removing a submodule during the observation.

## 4. Reconciliation semantics (amends §6)

**CURRENT requires every RECORDED probe to be evaluated and matching.**

For purposes of the all-recorded-probes-evaluated CURRENT rule, a RECORDED probe means a probe that is present AND well-formed under the existing TSR v1.3 recorded-value validation rules. A malformed individual recorded probe remains excluded exactly as v1.3 specifies and does not by itself force UNKNOWN.

Preserved semantics:

- an omitted probe is excluded from the CURRENT requirement;
- a malformed individual recorded probe is excluded exactly as v1.3 specifies;
- a well-formed recorded probe whose LIVE observation is unevaluable prohibits CURRENT;
- any evaluated mismatch → STALE;
- otherwise, if a well-formed recorded probe is unevaluable → UNKNOWN.

This eliminates false CURRENT from an unevaluable probe by construction.

## 5. No-execution rule

Within the stable-configuration threat model, `thinkos status` MUST NOT execute repository-configured conversion filters or helpers. If their absence cannot be safely established, the worktree probe is UNEVALUABLE.

## 6. Threat-model assumption (controlling, verbatim)

> "Repository configuration and attribute inputs are assumed stable for the duration of one thinkos status observation. ThinkOS does not defend against a concurrent writer actively modifying .gitattributes, Git configuration, or equivalent repository inputs between the hazard gate and the worktree observation."

This assumption narrows the concurrency threat model only. It does NOT authorize filesystem writes, Git-state mutation, hidden execution, network access, authority expansion, automatic correction, or automatic writeback by ThinkOS.

## 7. Git-version compatibility

Git versions lacking `--no-optional-locks` may make the worktree probe unevaluable and therefore degrade safely under the v1.4 reconciliation rule. No runtime Git-version branching is authorized by this amendment.

## 8. Determinism & side-effect freedom (unchanged from v1.3)

`thinkos status` MUST NOT create/modify/delete any file or directory. No WAL/SHM/tables/temp files. No git state mutations. Same inputs → identical output. (Enforced by acceptance test 16 as amended by the implementation work unit.)

## 9. Fail-closed semantics

- Unsafe or uncertain configuration → worktree probe UNEVALUABLE → verdict degrades (STALE/UNKNOWN), never CURRENT.
- The degrade is modeled as fail-closed behavior, not as a repair.
- No false CURRENT is possible from an unevaluable recorded probe.

## 10. §9 acceptance-criterion note

§9 test 16 in v1.3 is an acceptance criterion and may be amended separately as a test requirement by the implementation work unit. This amendment does not itself revise §9.

## 11. Non-goals (explicit)

- No sandboxing subsystem.
- No manual reimplementation of Git dirty semantics.
- No generic filter suppression (none exists in Git).
- No runtime Git-version detection or branching.
- No change to the other four probes, state schema, exit codes, doctor integration, §8 side-effect-freedom, or authority model.

---

**END OF TSR V1.4 SPEC**
