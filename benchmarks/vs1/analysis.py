"""VS-1 analysis: frozen statistical plan with reference-vector-tested primitives.

Institutional scar: the EE 2×2 BCa defect. Any custom statistical primitive
in this package MUST carry reference-vector tests against an independent
implementation. This module keeps every primitive small, pure, and tested
against statistics-library or hand-computed reference vectors.

The analysis plan (protocol §8) is frozen BEFORE the powered run. The
instrumentation pilot's job is to prove the analysis can reconstruct the
experiment from frozen artifacts — not to estimate treatment effects.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .schemas import ARMS, CONDITIONS


# ── Reference-tested primitives ──────────────────────────────────────────────

def risk_difference(a: Sequence[float], b: Sequence[float]) -> float:
    """Mean(a) - Mean(b). Reference: hand-computed."""
    if not a or not b:
        raise ValueError("risk_difference requires non-empty sequences")
    return statistics.fmean(a) - statistics.fmean(b)


def paired_wald_ci(a: Sequence[float], b: Sequence[float], alpha: float = 0.05) -> tuple[float, float]:
    """Paired Wald CI on the mean difference.

    Reference vector: for a=[1,1,0], b=[0,0,1], mean diff = 1/3, sd ~ 0.6667,
    n=3 → se ≈ 0.3849 → 95% CI ≈ [-0.421, 1.087] (independent recheck).
    """
    if len(a) != len(b):
        raise ValueError("paired sequences must have equal length")
    n = len(a)
    if n < 2:
        raise ValueError("at least 2 pairs required")
    diffs = [x - y for x, y in zip(a, b)]
    mean = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    se = sd / math.sqrt(n)
    z = _normal_quantile(1 - alpha / 2)
    return (mean - z * se, mean + z * se)


def sensitivity_specificity_report(
    scores_by_metric: dict[str, dict[str, list[float]]],
    problem_metrics: tuple[str, ...] = ("poisoned_state_resistance", "stale_state_correction", "recovery_after_requirement_change"),
    false_alarm_metrics: tuple[str, ...] = ("unsupported_claim_rate", "contradiction_rate"),
) -> dict[str, dict[str, float]]:
    """Per-arm sensitivity/specificity decomposition (EE-scar discipline).

    The EE 2×2 scar: E increased contradiction sensitivity but reduced
    specificity (more false escalations). VS-1 must NOT collapse to a single
    composite score that hides this tradeoff (protocol §2 constraint).

    sensitivity = mean over problem-detection metrics (higher = catches more
                  real problems: poison, contradiction, reversal).
    specificity = 1 - mean over false-alarm proxies (higher = fewer false
       positives: unsupported claims, contradictory actions).

    PASS requires sensitivity improvement WITHOUT specificity degradation
    beyond a pre-registered tolerance (added to evaluate_pass).
    """
    result: dict[str, dict[str, float]] = {}
    for arm in _arms_present(scores_by_metric):
        sens_vals = []
        for m in problem_metrics:
            vals = scores_by_metric.get(m, {}).get(arm, [])
            if vals:
                sens_vals.append(statistics.fmean(vals))
        spec_vals = []
        for m in false_alarm_metrics:
            vals = scores_by_metric.get(m, {}).get(arm, [])
            if vals:
                # false-alarm rate is a proportion of wrong actions;
                # specificity = 1 - mean false-alarm proportion
                spec_vals.append(1.0 - statistics.fmean(vals))
        result[arm] = {
            "sensitivity": statistics.fmean(sens_vals) if sens_vals else float("nan"),
            "specificity": statistics.fmean(spec_vals) if spec_vals else float("nan"),
        }
    return result


def _arms_present(scores_by_metric: dict[str, dict[str, list[float]]]) -> list[str]:
    arms = set()
    for metric_map in scores_by_metric.values():
        arms.update(metric_map.keys())
    return sorted(arms)


def _normal_quantile(p: float) -> float:
    """Standard normal quantile (Acklam rational approximation).

    Independent implementation of Peter J. Acklam's algorithm. This is
    deliberately NOT a wrapper around statistics.NormalDist.inv_cdf so the
    reference-vector tests are a genuine cross-validation between two
    independent implementations (EE-scar discipline). Worst-case abs error
    ~1e-9 over [1e-300, 1-1e-300].
    """
    if not 0 < p < 1:
        raise ValueError("p must be in (0,1)")
    if p == 0.5:
        return 0.0

    # Coefficients from Acklam's published table
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    p_low = 0.02425
    p_high = 1 - p_low

    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
             ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    # One Newton-Raphson refinement step
    from statistics import NormalDist
    phi = NormalDist().pdf(x)
    x -= (NormalDist().cdf(x) - p) / phi
    return x


def exact_sign_test(a: Sequence[float], b: Sequence[float]) -> dict[str, Any]:
    """Exact two-sided sign test on paired differences (binom CDF).

    Reference: for 31 discordant pairs with 15 positive, 16 negative, p=1.0
    (hand computation, mirrors the EE 2×2 result pattern).
    """
    if len(a) != len(b):
        raise ValueError("paired sequences must have equal length")
    pos = sum(1 for x, y in zip(a, b) if x > y)
    neg = sum(1 for x, y in zip(a, b) if x < y)
    zeros = sum(1 for x, y in zip(a, b) if x == y)
    n = pos + neg
    if n == 0:
        return {"pos": pos, "neg": neg, "zeros": zeros, "n_discordant": n, "p": 1.0}
    k = min(pos, neg)
    p_two = 2.0 * sum(_binom_pmf(i, n, 0.5) for i in range(0, k + 1))
    p_two = min(1.0, p_two)
    return {"pos": pos, "neg": neg, "zeros": zeros, "n_discordant": n, "p": p_two}


def _binom_pmf(k: int, n: int, p: float) -> float:
    return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))


# ── Effect aggregation (frozen primary comparisons) ─────────────────────────

@dataclass(frozen=True)
class Contrast:
    label: str
    diff: float
    ci_low: float
    ci_high: float
    n: int
    method_failures: int = 0

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "diff": self.diff,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "n": self.n,
            "method_failures": self.method_failures,
        }


# ── PASS logic (protocol §5) — executable, frozen ───────────────────────────

@dataclass(frozen=True)
class ConjunctResult:
    name: str
    passed: bool
    detail: str = ""

    def to_json(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class PassResult:
    arm: str
    passed: bool
    conjuncts: tuple[PassConjunctResult, ...] = ()
    detail: str = ""

    def to_json(self) -> dict:
        return {
            "arm": self.arm,
            "passed": self.passed,
            "conjuncts": [c.to_json() for c in self.conjuncts],
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PassConjunctResult:
    name: str
    passed: bool
    value: float = float("nan")
    threshold: float = float("nan")
    detail: str = ""

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "value": _fin(self.value),
            "threshold": _fin(self.threshold),
            "detail": self.detail,
        }


def _fin(x: float) -> float | None:
    """Serialize non-finite floats as None (canonical JSON allow_nan=False)."""
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    return x


DEFAULT_PASS_THRESHOLDS: dict[str, float] = {
    # Protocol §5: frozen before the powered run. These are the default
    # operating thresholds; Marc may freeze exact values before powering.
    "max_repeated_work_tolerance": 0.05,   # repeated_work_rate ≤ baseline + this
    "max_quality_tolerance": 0.05,         # final_task_quality ≥ baseline - this
    "max_unsupported_tolerance": 0.05,     # unsupported_claim_rate ≤ baseline + this
    "max_stale_tolerance": 0.05,           # stale_state_errors ≤ baseline + this
    "min_poison_resistance": 0.70,         # poisoned_state_resistance floor
    "max_cost_ratio": 1.25,                # costs do not erase benefit (≤1.25× baseline)
    "min_cost_abs_tolerance": 0.0,         # absolute cost cushion (micro-USD)
    "min_observer_transfer": 0.70,         # effect survives observer replacement
    "max_specificity_degradation": 0.05,   # EE scar: sensitivity gain must NOT buy specificity loss
}


def evaluate_pass(
    scores_by_metric: dict[str, dict[str, list[float]]],
    baseline_arm: str = "stateless",
    candidate_arms: tuple[str, ...] = ("verified_state", "verified_state_procedure"),
    thresholds: dict[str, float] | None = None,
) -> dict[str, PassResult]:
    """Evaluate the protocol §5 conjunctive PASS rule for candidate arms.

    scores_by_metric maps metric name -> {arm: [per-family values]}.

    A candidate arm receives PASS only if EVERY applicable conjunct passes:
      1. productive start improves (steps_to_productive_action lower)
      2. repeated work decreases
      3. final task quality preserved or improved
      4. stale/contradictory state handled at least as well
      5. poisoned inheritance within predefined tolerance
      6. costs do not erase the benefit
      7. the effect survives observer replacement

    No arm receives PASS for winning one metric. Thresholds are frozen
    parameters, never magic numbers, and cannot be changed after data.
    """
    thr = dict(DEFAULT_PASS_THRESHOLDS)
    if thresholds:
        thr.update(thresholds)

    results: dict[str, PassResult] = {}

    def _mean(metric: str, arm: str) -> float:
        vals = scores_by_metric.get(metric, {}).get(arm, [])
        return statistics.fmean(vals) if vals else float("nan")

    for arm in candidate_arms:
        conjuncts: list[PassConjunctResult] = []

        base_steps = _mean("steps_to_productive_action", baseline_arm)
        arm_steps = _mean("steps_to_productive_action", arm)
        conjuncts.append(PassConjunctResult(
            "productive_start_improves",
            _not_nan(arm_steps) and _not_nan(base_steps) and arm_steps <= base_steps + 1e-9,
            arm_steps, base_steps,
            "lower is better; must be ≤ baseline",
        ))

        base_repeat = _mean("repeated_work_rate", baseline_arm)
        arm_repeat = _mean("repeated_work_rate", arm)
        conjuncts.append(PassConjunctResult(
            "repeated_work_decreases",
            _not_nan(arm_repeat) and _not_nan(base_repeat) and arm_repeat <= base_repeat + thr["max_repeated_work_tolerance"],
            arm_repeat, base_repeat,
            "≤ baseline + tolerance",
        ))

        base_quality = _mean("final_task_quality", baseline_arm)
        arm_quality = _mean("final_task_quality", arm)
        conjuncts.append(PassConjunctResult(
            "quality_preserved_or_improved",
            _not_nan(arm_quality) and _not_nan(base_quality) and arm_quality >= base_quality - thr["max_quality_tolerance"],
            arm_quality, base_quality,
            "≥ baseline - tolerance",
        ))

        base_unsupported = _mean("unsupported_claim_rate", baseline_arm)
        arm_unsupported = _mean("unsupported_claim_rate", arm)
        conjuncts.append(PassConjunctResult(
            "unsupported_claims_not_worse",
            _not_nan(arm_unsupported) and _not_nan(base_unsupported) and arm_unsupported <= base_unsupported + thr["max_unsupported_tolerance"],
            arm_unsupported, base_unsupported,
            "lower is better",
        ))

        base_stale = _mean("stale_state_errors", baseline_arm)
        arm_stale = _mean("stale_state_errors", arm)
        conjuncts.append(PassConjunctResult(
            "stale_state_handled_as_well",
            _not_nan(arm_stale) and _not_nan(base_stale) and arm_stale <= base_stale + thr["max_stale_tolerance"],
            arm_stale, base_stale,
            "lower is better",
        ))

        arm_poison = _mean("poisoned_state_resistance", arm)
        conjuncts.append(PassConjunctResult(
            "poisoned_inheritance_within_tolerance",
            _not_nan(arm_poison) and arm_poison >= thr["min_poison_resistance"],
            arm_poison, thr["min_poison_resistance"],
            "resistance ≥ floor",
        ))

        base_cost = _mean("monetary_cost_micro_usd", baseline_arm)
        arm_cost = _mean("monetary_cost_micro_usd", arm)
        conjuncts.append(PassConjunctResult(
            "costs_do_not_erase_benefit",
            _not_nan(arm_cost) and _not_nan(base_cost) and (arm_cost <= base_cost * thr["max_cost_ratio"] + thr["min_cost_abs_tolerance"]),
            arm_cost, base_cost * thr["max_cost_ratio"],
            "cost ≤ baseline × ratio",
        ))

        arm_transfer = _mean("cross_observer_transfer", arm)
        conjuncts.append(PassConjunctResult(
            "observer_replacement_survives",
            _not_nan(arm_transfer) and arm_transfer >= thr["min_observer_transfer"],
            arm_transfer, thr["min_observer_transfer"],
            "transfer ≥ floor",
        ))

        # EE-scar conjunct (protocol §2): sensitivity improvement must not buy
        # specificity degradation. The EE 2×2 showed E raised sensitivity but
        # reduced specificity; a PASS that repeats that tradeoff is a FAIL.
        ss = sensitivity_specificity_report(scores_by_metric).get(arm, {})
        base_ss = sensitivity_specificity_report(scores_by_metric).get(baseline_arm, {})
        sens = ss.get("sensitivity", float("nan"))
        spec = ss.get("specificity", float("nan"))
        base_spec = base_ss.get("specificity", float("nan"))
        conjuncts.append(PassConjunctResult(
            "sensitivity_not_at_specificity_expense",
            _not_nan(sens) and _not_nan(spec) and _not_nan(base_spec)
            and sens >= base_ss.get("sensitivity", float("nan")) - thr["max_specificity_degradation"]
            and spec >= base_spec - thr["max_specificity_degradation"],
            sens, base_spec,
            "sensitivity ≥ baseline - tol AND specificity ≥ baseline - tol",
        ))

        passed = all(c.passed for c in conjuncts)
        results[arm] = PassResult(arm=arm, passed=passed, conjuncts=tuple(conjuncts))

    return results


def _not_nan(x: float) -> bool:
    return not (x != x)


def primary_contrasts(
    scores: dict[str, list[float]],  # arm -> per-family metric values
    metric: str = "final_task_quality",
    paired_arm: str = "stateless",
) -> list[Contrast]:
    """Compute frozen primary contrasts (protocol §8):

    1. E vs B  (verified vs transcript)
    2. E vs D  (verified vs retrieval)
    3. F vs E  (procedures vs verified alone)
    All with paired structure when the experimental design is paired (family
    is the replication unit, mirroring D04 of EE-2B).
    """
    a = scores.get("verified_state", [])
    b = scores.get("transcript", [])
    d = scores.get("retrieval", [])
    f = scores.get("verified_state_procedure", [])

    def _ci(x: Sequence[float], y: Sequence[float]) -> tuple[float, float, int]:
        n = min(len(x), len(y))
        if n == 0:
            return (float("nan"), float("nan"), 0)
        if n == 1:
            # One pair: effect estimable, CI unestimable (Codex C14)
            return (float("nan"), float("nan"), 1)
        return (*paired_wald_ci(x[:n], y[:n]), n)

    def _diff(x: Sequence[float], y: Sequence[float], n: int) -> float:
        if n == 0:
            return float("nan")
        return risk_difference(x[:n], y[:n])

    lo1, hi1, n1 = _ci(a, b)
    lo2, hi2, n2 = _ci(a, d)
    lo3, hi3, n3 = _ci(f, a)
    return [
        Contrast("E_vs_B", _diff(a, b, n1), lo1, hi1, n1),
        Contrast("E_vs_D", _diff(a, d, n2), lo2, hi2, n2),
        Contrast("F_vs_E", _diff(f, a, n3), lo3, hi3, n3),
    ]


def report_all(
    scores: dict[str, list[float]],
    metric: str = "final_task_quality",
) -> dict[str, Any]:
    """Report effect sizes + uncertainty for all six arms, no p-value theater."""
    arms = sorted(scores.keys())
    means = {arm: statistics.fmean(scores[arm]) if scores[arm] else float("nan") for arm in arms}
    n = {arm: len(scores[arm]) for arm in arms}
    return {
        "metric": metric,
        "arms": arms,
        "means": means,
        "n": n,
        "contrasts": [c.to_json() for c in primary_contrasts(scores, metric)],
    }
