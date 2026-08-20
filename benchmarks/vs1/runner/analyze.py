#!/usr/bin/env python3
"""VS-1 analysis launcher — reads ONLY sealed raw evidence, runs the frozen
analysis, writes the analysis report. No provider calls.

Reads: <evidence_root>/raw/*.outcome.json (sealed, manifest-verified)
Writes: <evidence_root>/analysis_report.json

Uses the frozen analysis.py primitives (Acklam quantile, paired Wald CI,
exact sign test, sensitivity/specificity, evaluate_pass, primary_contrasts).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from benchmarks.vs1.analysis import (
    evaluate_pass,
    exact_sign_test,
    paired_wald_ci,
    risk_difference,
    sensitivity_specificity_report,
)
from benchmarks.vs1.schemas import ARMS, CONDITIONS, json_dumps
from benchmarks.vs1.runner.sealer import EvidenceSealer


def load_outcomes(root: Path) -> list[dict]:
    if not EvidenceSealer.verify(root):
        raise RuntimeError("Evidence seal verification FAILED — refusing to analyze")
    # R4 forensic fix: the sealer writes outcomes twice — incrementally per
    # call (call-*.outcome.json, one per provider invocation) and at seal
    # time under trajectory_id (which OVERWRITES for multi-call interruption
    # trajectories, keeping only the last cell). The call-named files are
    # the canonical per-invocation records (126, matching the ledger); the
    # trajectory-named files are redundant and must not be read.
    outs = []
    for p in sorted((root / "raw").glob("call-*.outcome.json")):
        outs.append(json.loads(p.read_text()))
    return outs


def paired_by_rep(outcomes: list[dict], arm_a: str, arm_b: str, metric: str = "final_task_quality"):
    """Paired by (condition, replicate) — the replication unit."""
    pairs: dict[tuple[str, int], list] = {}
    for o in outcomes:
        key = (o["condition"], o["replicate"])
        if o["arm"] == arm_a:
            pairs.setdefault(key, [None, None])[0] = float(o["score"].get(metric, 0.0))
        elif o["arm"] == arm_b:
            pairs.setdefault(key, [None, None])[1] = float(o["score"].get(metric, 0.0))
    a_vals, b_vals = [], []
    for (cond, rep), (a, b) in sorted(pairs.items()):
        if a is not None and b is not None:
            a_vals.append(a)
            b_vals.append(b)
    return a_vals, b_vals


def build_metric_map(outcomes: list[dict]) -> dict[str, dict[str, list[float]]]:
    metric_map: dict[str, dict[str, list[float]]] = {}
    for o in outcomes:
        arm = o["arm"]
        for metric, value in o["score"].items():
            if metric in ("trajectory_id", "arm", "condition", "task", "method_failure_reason"):
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            metric_map.setdefault(metric, {}).setdefault(arm, []).append(float(value))
    return metric_map


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    args = parser.parse_args()

    root = args.run_root
    outcomes = load_outcomes(root)
    metric = "final_task_quality"

    # ── Arm-level means ────────────────────────────────────────────────
    arm_means = {}
    arm_sd = {}
    arm_n = {}
    for a in ARMS:
        vals = [float(o["score"].get(metric, 0.0)) for o in outcomes if o["arm"] == a]
        arm_means[a] = statistics.fmean(vals) if vals else None
        arm_sd[a] = statistics.stdev(vals) if len(vals) > 1 else None
        arm_n[a] = len(vals)

    # ── Primary contrasts (paired by replicate × condition) ─────────────
    contrasts = []
    for label, a, b in (("E_vs_B", "verified_state", "transcript"),
                        ("E_vs_D", "verified_state", "retrieval"),
                        ("F_vs_E", "verified_state_procedure", "verified_state")):
        av, bv = paired_by_rep(outcomes, a, b, metric)
        if len(av) >= 2:
            diff = risk_difference(av, bv)
            lo, hi = paired_wald_ci(av, bv)
            n = len(av)
        elif len(av) == 1:
            diff = risk_difference(av, bv)
            lo, hi = float("nan"), float("nan")
            n = 1
        else:
            diff = lo = hi = float("nan")
            n = 0
        st = exact_sign_test(av, bv) if len(av) else None
        contrasts.append({
            "contrast": label,
            "diff": diff,
            "ci_low": lo,
            "ci_high": hi,
            "n_pairs": n,
            "sign_test": st,
        })

    # ── Condition grid ──────────────────────────────────────────────────
    grid = {a: {c: [] for c in CONDITIONS} for a in ARMS}
    for o in outcomes:
        grid[o["arm"]][o["condition"]].append(float(o["score"].get(metric, 0.0)))
    grid_report = {a: {c: (round(statistics.fmean(v), 4) if v else None) for c, v in conds.items()} for a, conds in grid.items()}

    # ── Component metrics per arm ────────────────────────────────────────
    comp_metrics = [
        "hidden_test_passed", "stale_state_correction", "poisoned_state_resistance",
        "recovery_after_interruption", "recovery_after_requirement_change",
        "repeated_work_rate", "unsupported_claim_rate", "contradiction_rate",
        "handoff_reconstruction_accuracy", "steps_to_productive_action",
        "method_failure",
    ]
    comp_report = {}
    for m in comp_metrics:
        comp_report[m] = {}
        for a in ARMS:
            vals = [o["score"].get(m) for o in outcomes if o["arm"] == a]
            # None (N/A) values are structurally not-applicable for that arm
            # (e.g., unsupported_claim_rate for non-evidence arms); exclude
            # them rather than coercing.
            numeric = [float(v) for v in vals if v is not None]
            comp_report[m][a] = round(statistics.fmean(numeric), 4) if numeric else None

    # ── Sensitivity / specificity (EE scar) ─────────────────────────────
    metric_map = build_metric_map(outcomes)
    ss = sensitivity_specificity_report(metric_map)

    # ── PASS logic ──────────────────────────────────────────────────────
    pass_result = evaluate_pass(metric_map)

    report = {
        "model": "deepseek-v4-pro:0813",
        "run_root": str(root),
        "n_outcomes": len(outcomes),
        "method_failures": sum(1 for o in outcomes if o["method_failure"]),
        "arm_means": arm_means,
        "arm_sd": arm_sd,
        "arm_n": arm_n,
        "primary_contrasts": contrasts,
        "condition_grid": grid_report,
        "component_metrics": comp_report,
        "sensitivity_specificity": ss,
        "pass_logic": {a: pr.to_json() for a, pr in pass_result.items()},
    }
    out = root / "analysis_report.json"
    out.write_text(json_dumps(_sanitize(report)))
    print(f"Analysis written: {out}")
    print(f"Arm means ({metric}):", {a: (round(v, 4) if v is not None else None) for a, v in arm_means.items()})
    for c in contrasts:
        print(f"  {c['contrast']}: diff={c['diff']:.4f} CI=[{c['ci_low']:.4f},{c['ci_high']:.4f}] n={c['n_pairs']}")
    return 0


def _sanitize(v: Any) -> Any:
    """Replace non-finite floats with None (canonical JSON allow_nan=False)."""
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v
    if isinstance(v, dict):
        return {k: _sanitize(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_sanitize(x) for x in v]
    return v


if __name__ == "__main__":
    sys.exit(main())
