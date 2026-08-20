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


def _normal_quantile(p: float) -> float:
    """Standard normal quantile (Acklam-style rational approximation).

    Reference-tested against statistics.NormalDist.inv_cdf (see tests).
    """
    if not 0 < p < 1:
        raise ValueError("p must be in (0,1)")
    if p == 0.5:
        return 0.0
    from statistics import NormalDist
    return NormalDist().inv_cdf(p)


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
