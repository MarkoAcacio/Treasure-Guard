"""Sprint 3: Z-score, IQR, severity scoring and settings."""
import unittest

from treasury_guard.detection import find_anomalies
from treasury_guard.settings import SettingsError, apply_changes, suggest_limits
from treasury_guard.severity import level_for, score_flags
from treasury_guard.statistical import find_amounts_far_from_average, find_amounts_outside_normal_range
from tests.helpers import FMT, settings, txn


def normal_month(n=30, outlier=None):
    amounts = [100 + (i % 5) * 2 for i in range(n)]  # 100..108
    txns = [txn(i, i * 24, a, account=f"A{i}") for i, a in enumerate(amounts)]
    if outlier is not None:
        txns.append(txn(999, n * 24, outlier, account="X"))
    return txns


class ZScoreTests(unittest.TestCase):
    def test_flags_payment_far_above_average(self):
        flags = find_amounts_far_from_average(normal_month(outlier=500), settings(), FMT)
        self.assertEqual([f["key"] for f in flags], ["zscore-999"])
        self.assertIn("much higher than your usual Food payments", flags[0]["reason"])

    def test_nothing_flagged_when_all_similar(self):
        self.assertEqual(find_amounts_far_from_average(normal_month(), settings(), FMT), [])

    def test_small_category_compared_with_all_payments(self):
        txns = normal_month(outlier=None) + [txn(500, 1, 900, category="Rare", account="R")]
        flags = find_amounts_far_from_average(txns, settings(), FMT)
        self.assertEqual([f["key"] for f in flags], ["zscore-500"])
        self.assertEqual(flags[0]["details"]["compared_with"], "all your payments")


class IqrTests(unittest.TestCase):
    def test_flags_above_upper_fence(self):
        flags = find_amounts_outside_normal_range(normal_month(outlier=130), settings(), FMT)
        self.assertEqual([f["key"] for f in flags], ["iqr-999"])

    def test_multiplier_changes_sensitivity(self):
        txns = normal_month(outlier=118)
        self.assertEqual(find_amounts_outside_normal_range(txns, settings(iqr_multiplier=3.0), FMT), [])
        self.assertTrue(find_amounts_outside_normal_range(txns, settings(iqr_multiplier=1.0), FMT))


class SeverityTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual([level_for(s) for s in (90, 75, 60, 50, 49)], ["high", "high", "medium", "medium", "low"])

    def test_agreement_bonus_when_several_checks_flag_same_payment(self):
        found = find_anomalies(normal_month(outlier=500), settings(large_limit=400, small_limit=1, ml_enabled=False))
        on_outlier = [f for f in found if f["txn_ids"] == [999]]
        # The outlier is alone on its day, so the category-jump check also catches it.
        self.assertEqual(sorted(f["type"] for f in on_outlier), ["iqr", "large", "spike", "zscore"])
        for f in on_outlier:
            self.assertEqual(f["details"]["agreement_bonus"], 30)
            self.assertEqual(f["severity"], "high")

    def test_scores_stay_in_range(self):
        for f in score_flags(find_anomalies(normal_month(outlier=10_000), settings())):
            self.assertTrue(0 <= f["severity_score"] <= 100)


class SettingsTests(unittest.TestCase):
    def test_sensitivity_preset_sets_thresholds(self):
        new = apply_changes(settings(), {"sensitivity": "high"})
        self.assertEqual((new["zscore_threshold"], new["iqr_multiplier"]), (2.5, 1.0))

    def test_changing_a_threshold_switches_to_custom(self):
        new = apply_changes(settings(), {"zscore_threshold": 2.8})
        self.assertEqual(new["sensitivity"], "custom")

    def test_unchanged_values_keep_preset(self):
        s = settings()
        self.assertEqual(apply_changes(s, {"zscore_threshold": s["zscore_threshold"]})["sensitivity"], "medium")

    def test_rejects_bad_values(self):
        with self.assertRaises(SettingsError):
            apply_changes(settings(), {"small_limit": 500, "large_limit": 100})
        with self.assertRaises(SettingsError):
            apply_changes(settings(), {"zscore_threshold": "abc"})
        with self.assertRaises(SettingsError):
            apply_changes(settings(), {"sensitivity": "extreme"})

    def test_suggest_limits_from_data(self):
        limits = suggest_limits([float(x) for x in range(1, 101)])
        self.assertLess(limits["small_limit"], 3)
        self.assertGreater(limits["large_limit"], 98)


if __name__ == "__main__":
    unittest.main()
