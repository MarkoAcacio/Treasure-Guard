"""Pre-processing and the Isolation Forest model."""
import math
import random
import unittest
from datetime import datetime, timedelta

from treasury_guard import anomaly_model, preprocessing
from treasury_guard.detection import find_anomalies
from tests.helpers import FMT, settings

T0 = datetime(2026, 1, 1)


def msme_month(seed=1, n=400):
    """Realistic-looking spending: very different amounts per category, repeat suppliers."""
    rng = random.Random(seed)
    cats = {"Inventory": (3000, 0.6), "Utilities": (2500, 0.3), "Food": (400, 0.5),
            "Transport": (150, 0.4), "Rent": (15000, 0.05)}
    txns = []
    for i in range(n):
        cat = rng.choice(list(cats))
        mu, sigma = cats[cat]
        txns.append({"id": i, "timestamp": T0 + timedelta(hours=rng.uniform(0, 24 * 90)),
                     "amount": round(mu * math.exp(rng.gauss(0, sigma)), 2),
                     "counterparty": f"{cat}-{rng.randint(1, 6)}", "merchant": None, "category": cat})
    return txns


class PreprocessingTests(unittest.TestCase):
    def test_only_needed_kaggle_columns_are_used(self):
        txns, labels = preprocessing.load_kaggle_training_data()
        self.assertEqual(len(txns), 1000)
        self.assertEqual(set(txns[0]), set(preprocessing.STANDARD_FIELDS))
        self.assertIn("customer_data.csv", preprocessing.KAGGLE_COLUMNS_DROPPED)
        self.assertEqual(len(labels), 1000)  # labels kept apart for evaluation only

    def test_cleaning_drops_unusable_rows(self):
        rows = [{"id": 1, "timestamp": T0, "amount": 10, "counterparty": " A ", "merchant": None, "category": ""},
                {"id": 2, "timestamp": None, "amount": 10, "counterparty": "A", "merchant": None, "category": "x"},
                {"id": 3, "timestamp": T0, "amount": 0, "counterparty": "A", "merchant": None, "category": "x"}]
        cleaned = preprocessing.clean_transactions(rows)
        self.assertEqual([t["id"] for t in cleaned], [1])
        self.assertEqual((cleaned[0]["counterparty"], cleaned[0]["category"]), ("A", "Uncategorized"))

    def test_features_do_not_depend_on_currency_or_business_size(self):
        txns = preprocessing.clean_transactions(msme_month())
        scaled = [{**t, "amount": t["amount"] * 60} for t in txns]
        a, _ = preprocessing.build_features(txns)
        b, _ = preprocessing.build_features(scaled)
        for ra, rb in zip(a, b):
            for x, y in zip(ra, rb):
                self.assertAlmostEqual(x, y, places=9)


class ModelTests(unittest.TestCase):
    def test_model_card_has_evaluation(self):
        _, card = anomaly_model.load_model()
        self.assertEqual(card["features"], list(preprocessing.FEATURES))
        self.assertGreaterEqual(card["evaluation"]["injected_anomalies"]["roc_auc"], 0.95)
        self.assertIsNotNone(card["evaluation"]["fraud_labels"])

    def test_flags_about_the_chosen_share_of_a_normal_business(self):
        txns = msme_month(seed=3)
        flags = anomaly_model.find_unusual_patterns(txns, settings(ml_percentile=97), FMT)
        self.assertLess(len(flags) / len(txns), 0.06)

    def test_catches_obvious_problems_in_realistic_data(self):
        txns = msme_month(seed=4)
        txns += [
            {"id": 900, "timestamp": T0 + timedelta(days=40), "amount": 45000, "counterparty": "Inventory-2",
             "merchant": None, "category": "Inventory"},          # 15x the usual inventory payment
            {"id": 901, "timestamp": T0 + timedelta(days=41), "amount": 3.0, "counterparty": "NewCo",
             "merchant": None, "category": "Food"},               # tiny test charge
        ]
        flags = {f["txn_ids"][0]: f for f in anomaly_model.find_unusual_patterns(txns, settings(), FMT)}
        self.assertIn(900, flags)
        self.assertIn(901, flags)
        self.assertIn("typical Inventory payment", flags[900]["reason"])

    def test_needs_enough_payments_and_can_be_turned_off(self):
        txns = msme_month(n=10)
        self.assertEqual(anomaly_model.find_unusual_patterns(txns, settings(), FMT), [])
        self.assertEqual(anomaly_model.find_unusual_patterns(msme_month(), settings(ml_enabled=False), FMT), [])

    def test_ml_flags_get_severity(self):
        found = [f for f in find_anomalies(msme_month(seed=5), settings()) if f["type"] == "ml"]
        self.assertTrue(found)
        self.assertTrue(all(0 <= f["severity_score"] <= 100 for f in found))


if __name__ == "__main__":
    unittest.main()
