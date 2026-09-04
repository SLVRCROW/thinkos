# TSR V0 SPEC v1.4.1 — PROSPECTIVE AMENDMENT

**Status:** FROZEN v1.4.1 prospective amendment

**Supersedes:** TSR v1.4 for the threat-model/security interpretation necessary to reconcile Git configuration-source reads, prospectively. TSR v1.4 remains an immutable historical record.

**Controlling decision:** Marc's selection of OPTION A (explicit trust boundary) for ThinkOS Alpha 0.2 Patch 1 (2026-09-04).

---

## 1. Amendment scope

v1.4.1 supersedes ONLY the threat-model/security interpretation necessary to reconcile Git configuration-source reads. All other v1.4 and v1.3 provisions remain unchanged.

This amendment does NOT reopen:
- the five-probe shape
- reconciliation semantics
- hazard-gate implementation
- batching
- gitlink handling
- filter-driver handling
- exit codes
- authority
- write behavior

## 2. Trust-boundary text (controlling, verbatim)

> "Git configuration files, configuration includes, and configured attribute-source paths, including core.attributesFile and include.path, are trusted environmental inputs for read access during one `thinkos status` observation.
>
> They are assumed stable, finite, locally readable, non-special regular files, non-secret, and non-network for the duration of that observation.
>
> ThinkOS Alpha 0.2 Patch 1 does not defend against a hostile Git configuration source path that targets a FIFO, device, network share, secret file, or equivalent external resource."

## 3. Reconciliation of the existing absolute claims

### 3a. No-network claim (amends v1.4 §3 "The hazard gate itself MUST ... access no network")

The v1.4 statement "The hazard gate itself MUST ... access no network" is amended prospectively so it no longer makes an unconditional claim that ordinary Git configuration loading cannot violate. Its interpretation is replaced with:

> "Within the trusted-config-source threat model, ThinkOS does not intentionally initiate network access and MUST NOT execute repository-configured filters, helpers, fsmonitor machinery, or submodule machinery as part of the worktree observation."

### 3b. No-secrets claim (reconciles the retained v1.3 security clause prospectively)

> "Within the trusted-config-source threat model, ThinkOS does not intentionally read secret material. Git configuration and attribute-source paths are assumed non-secret inputs. Hostile source paths targeting secret material are outside the Alpha 0.2 Patch 1 threat model."

### 3c. Explicit non-claims

This amendment does NOT claim:
- absolute network isolation
- absolute protection from hostile Git config paths
- sandboxing
- hostile-config-source containment
- bounded execution when the trust assumption is violated

## 4. Residual risk (accepted)

If the trust assumption is violated, a Git subprocess may read or block on an external configuration/attribute source before TSR can inspect the effective configuration.

This residual exposure is accepted for Alpha 0.2 Patch 1.

It grants no authority and permits no ThinkOS writes or automatic correction.

## 5. Implementation conclusion

No implementation change is required for v1.4.1.

Current head `d5cbf749809cbc519544029c449ec2a89e9617f9` is technically coherent under the amended threat model, subject to final review and unchanged successful CI.

This prospective amendment does NOT retroactively prove the earlier implementation process compliant. The existing Work Unit B governance caveat (the implementation executor changed exact hazard-gate command vectors during Work Unit B after discovering additional fsmonitor/helper behavior and macro-resolution facts) is preserved.

---

**END OF TSR V1.4.1 SPEC**
