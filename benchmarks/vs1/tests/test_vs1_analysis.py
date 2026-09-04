"""Reference-vector tests for VS-1 statistical primitives.

Institutional scar discipline (EE 2×2 BCa defect): every custom statistical
primitive must be validated against an independent implementation on
reference vectors. This suite checks each primitive against
statistics.NormalDist / hand-computed vectors.
"""
from __future__ import annotations

import unittest

from benchmarks.vs1.analysis import (
    DEFAULT_PASS_THRESHOLDS,
    _normal_quantile,
    evaluate_pass,
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

    def test_nontrivial_3_positive(self):
        # n=31 discordant, 3 pos / 28 neg: two-sided binomial p
        # = 2 * sum_{i=0}^{3} C(31,i) / 2^31
        # = 2 * 4992 / 2147483648 = 9984 / 2147483648 = 4.6487e-6 (hand-derived)
        a = [1] * 3 + [0] * 28
        b = [0] * 3 + [1] * 28
        r = exact_sign_test(a, b)
        self.assertEqual(r["pos"], 3)
        self.assertEqual(r["neg"], 28)
        self.assertAlmostEqual(r["p"], 9984 / 2**31, places=9)

    def test_nontrivial_6_positive(self):
        # n=31 discordant, 6 pos / 25 neg:
        # sum_{i=0}^{6} C(31,i) = 942649; p = 2*942649/2^31 = 8.779e-4 (hand-derived)
        a = [1] * 6 + [0] * 25
        b = [0] * 6 + [1] * 25
        r = exact_sign_test(a, b)
        self.assertEqual(r["pos"], 6)
        self.assertEqual(r["neg"], 25)
        self.assertAlmostEqual(r["p"], 2 * 942649 / 2**31, places=9)


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


class TestPassLogic(unittest.TestCase):
    def _base_scores(self) -> dict[str, dict[str, list[float]]]:
        """A metric map where E/F are strictly better than baseline on every conjunct."""
        return {
            "steps_to_productive_action": {
                "stateless": [5.0, 5.0], "verified_state": [3.0, 3.0], "verified_state_procedure": [2.0, 2.0],
            },
            "repeated_work_rate": {
                "stateless": [0.4, 0.4], "verified_state": [0.2, 0.2], "verified_state_procedure": [0.1, 0.1],
            },
            "final_task_quality": {
                "stateless": [0.6, 0.6], "verified_state": [0.8, 0.8], "verified_state_procedure": [0.9, 0.9],
            },
            "unsupported_claim_rate": {
                "stateless": [0.3, 0.3], "verified_state": [0.1, 0.1], "verified_state_procedure": [0.05, 0.05],
            },
            "contradiction_rate": {
                "stateless": [0.2, 0.2], "verified_state": [0.05, 0.05], "verified_state_procedure": [0.02, 0.02],
            },
            "stale_state_errors": {
                "stateless": [2.0, 2.0], "verified_state": [0.0, 0.0], "verified_state_procedure": [0.0, 0.0],
            },
            "poisoned_state_resistance": {
                "stateless": [0.5, 0.5], "verified_state": [0.9, 0.9], "verified_state_procedure": [0.95, 0.95],
            },
            "monetary_cost_micro_usd": {
                "stateless": [100, 100], "verified_state": [100, 100], "verified_state_procedure": [120, 120],
            },
            "cross_observer_transfer": {
                "stateless": [0.5, 0.5], "verified_state": [0.9, 0.9], "verified_state_procedure": [0.95, 0.95],
            },
        }

    def test_pass_when_all_conjuncts_satisfied(self):
        r = evaluate_pass(self._base_scores())
        self.assertTrue(r["verified_state"].passed)
        self.assertTrue(r["verified_state_procedure"].passed)

    def test_fail_when_quality_not_preserved(self):
        scores = self._base_scores()
        scores["final_task_quality"]["verified_state"] = [0.4, 0.4]  # below baseline 0.6
        r = evaluate_pass(scores)
        self.assertFalse(r["verified_state"].passed)
        # Find which conjunct failed
        failed = [c.name for c in r["verified_state"].conjuncts if not c.passed]
        self.assertIn("quality_preserved_or_improved", failed)

    def test_fail_when_cost_erases_benefit(self):
        scores = self._base_scores()
        scores["monetary_cost_micro_usd"]["verified_state"] = [500, 500]  # 5× baseline
        r = evaluate_pass(scores)
        self.assertFalse(r["verified_state"].passed)
        failed = [c.name for c in r["verified_state"].conjuncts if not c.passed]
        self.assertIn("costs_do_not_erase_benefit", failed)

    def test_fail_when_poison_tolerance_breached(self):
        scores = self._base_scores()
        scores["poisoned_state_resistance"]["verified_state"] = [0.2, 0.2]  # below 0.7 floor
        r = evaluate_pass(scores)
        self.assertFalse(r["verified_state"].passed)
        failed = [c.name for c in r["verified_state"].conjuncts if not c.passed]
        self.assertIn("poisoned_inheritance_within_tolerance", failed)

    def test_thresholds_are_frozen_parameters(self):
        # The default thresholds dict is a named constant, not magic numbers.
        self.assertIn("max_cost_ratio", DEFAULT_PASS_THRESHOLDS)
        self.assertIn("min_poison_resistance", DEFAULT_PASS_THRESHOLDS)
        self.assertIn("min_observer_transfer", DEFAULT_PASS_THRESHOLDS)
        self.assertIn("max_specificity_degradation", DEFAULT_PASS_THRESHOLDS)

    def test_ee_scar_sensitivity_specificity(self):
        # EE lesson: sensitivity gain must not buy specificity loss.
        # E improves sensitivity but degrades specificity → must NOT PASS.
        scores = self._base_scores()
        # Make E's specificity (1 - false_alarm proxies) collapse:
        scores["unsupported_claim_rate"]["verified_state"] = [0.9, 0.9]   # high false alarms
        scores["contradiction_rate"]["verified_state"] = [0.8, 0.8]       # high false alarms
        r = evaluate_pass(scores)
        self.assertFalse(r["verified_state"].passed)
        failed = [c.name for c in r["verified_state"].conjuncts if not c.passed]
        self.assertIn("sensitivity_not_at_specificity_expense", failed)
