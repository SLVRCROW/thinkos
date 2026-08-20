"""Reference-vector tests for VS-1 statistical primitives.

Institutional scar discipline (EE 2×2 BCa defect): every custom statistical
primitive must be validated against an independent implementation on
reference vectors. This suite checks each primitive against
statistics.NormalDist / hand-computed vectors.
"""
from __future__ import annotations

import unittest

from benchmarks.vs1.analysis import (
    _normal_quantile,
    exact_sign_test,
    paired_wald_ci,
    primary_contrasts,
    risk_difference,
)


class TestNormalQuantile(unittest.TestCase):
    """Reference vectors vs statistics.NormalDist.inv_cdf."""

    def test_reference_points(self):
        from statistics import NormalDist
        ref = NormalDist().inv_cdf
        for p in (0.5, 0.975, 0.025, 0.999, 0.001, 0.99, 0.01):
            self.assertAlmostEqual(_normal_quantile(p), ref(p), places=6)

    def test_symmetry(self):
        for p in (0.1, 0.05, 0.2, 0.7, 0.9, 0.999):
            self.assertAlmostEqual(_normal_quantile(1 - p) + _normal_quantile(p), 0.0, places=9)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            _normal_quantile(0.0)
        with self.assertRaises(ValueError):
            _normal_quantile(1.0)


class TestRiskDifference(unittest.TestCase):
    def test_simple(self):
        # mean([1,1,0]) = 2/3 ; mean([0,0,1]) = 1/3 ; diff = 1/3
        self.assertAlmostEqual(risk_difference([1, 1, 0], [0, 0, 1]), 1 / 3, places=9)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            risk_difference([], [1])


class TestPairedWaldCI(unittest.TestCase):
    def test_known_vector(self):
        # a=[1,1,0], b=[0,0,1]: diffs [1,1,-1], mean=1/3, sd=sqrt(4/3)=1.1547, se=0.6667
        lo, hi = paired_wald_ci([1, 1, 0], [0, 0, 1])
        self.assertAlmostEqual((lo + hi) / 2, 1 / 3, places=6)
        # CI width approx 2*1.96*0.6667 = 2.613; exact z from inv_cdf
        from statistics import NormalDist
        z = NormalDist().inv_cdf(0.975)
        self.assertAlmostEqual(hi - lo, 2 * z * 0.6666666667, places=2)

    def test_unequal_length_raises(self):
        with self.assertRaises(ValueError):
            paired_wald_ci([1], [1, 2])


class TestExactSignTest(unittest.TestCase):
    def test_ee_2x2_pattern(self):
        # Mirrors the EE 2×2: 31 discordant, 15 pos / 16 neg → p ≈ 1.0
        a = [1] * 15 + [0] * 16 + [1] * 77
        b = [0] * 15 + [1] * 16 + [1] * 77
        r = exact_sign_test(a, b)
        self.assertEqual(r["pos"], 15)
        self.assertEqual(r["neg"], 16)
        self.assertEqual(r["n_discordant"], 31)
        self.assertAlmostEqual(r["p"], 1.0, places=6)

    def test_all_equal(self):
        r = exact_sign_test([1, 1, 1], [1, 1, 1])
        self.assertEqual(r["n_discordant"], 0)
        self.assertEqual(r["p"], 1.0)


class TestPrimaryContrasts(unittest.TestCase):
    def test_empty_scores_do_not_crash(self):
        c = primary_contrasts({})
        self.assertEqual(len(c), 3)
        self.assertEqual(c[0].n, 0)

    def test_known_ordering(self):
        scores = {
            "verified_state": [0.9, 0.8, 0.7],
            "transcript": [0.5, 0.4, 0.3],
            "retrieval": [0.6, 0.5, 0.4],
            "verified_state_procedure": [0.95, 0.9, 0.85],
        }
        c = primary_contrasts(scores)
        self.assertGreater(c[0].diff, 0)  # E > B
        self.assertGreater(c[1].diff, 0)  # E > D
        self.assertGreater(c[2].diff, 0)  # F > E
