"""Pre-processing and the trained AI models (fraud model + Isolation Forest)."""
import math
import random
import unittest
from datetime import datetime, timedelta

from treasury_guard import anomaly_model, preprocessing
from treasury_guard.detection import find_anomalies
from tests.helpers import FMT, settings

T0 = datetime(2026, 1, 1)

try:
    anomaly_model.load_models()
    MODELS_READY = True
except anomaly_model.ModelUnavailable:
    MODELS_READY = False


def business(seed=1, n=400, with_time=True):
    """Realistic-looking spending: very different amounts per category, repeat suppliers, daytime."""
    rng = random.Random(seed)
    cats = {"Inventory": (3000, 0.6), "Utilities": (2500, 0.3), "Food": (400, 0.5),
            "Transport": (150, 0.4), "Rent": (15000, 0.05)}
    txns = []
    for i in range(n):
        cat = rng.choice(list(cats))
        mu, sigma = cats[cat]
        day = T0 + timedelta(days=rng.randint(0, 120))
        when = day.replace(hour=rng.randint(7, 19), minute=rng.randint(0, 59)) if with_time else day
        txns.append({"id": i, "timestamp": when, "amount": round(mu * math.exp(rng.gauss(0, sigma)), 2),
                     "counterparty": f"{cat}-{rng.randint(1, 6)}", "merchant": None, "category": cat})
    return txns


def night_burst(start_id=900, with_time=True):
    when = datetime(2026, 3, 15, 1, 30) if with_time else datetime(2026, 3, 15)
    return [{"id": start_id + i, "timestamp": when + timedelta(minutes=7 * i) if with_time else when,
             "amount": 45000 + 500 * i, "counterparty": f"Unknown shop {i}", "merchant": None,
             "category": "Inventory"} for i in range(4)]


class PreprocessingTests(unittest.TestCase):
    def test_training_row_cleaning(self):
        row = preprocessing.clean_training_row(7, "2019-01-01 00:00:18", "4.97", "fraud_Rippin, Kub and Mann", "misc_net")
        self.assertEqual(row["merchant"], "Rippin, Kub and Mann")          # 'fraud_' prefix removed
        self.assertEqual(row["category"], "Miscellaneous (online)")
        self.assertAlmostEqual(row["amount"], round(4.97 * preprocessing.USD_TO_PHP, 2))

    def test_personal_columns_are_dropped(self):
        used = set(preprocessing.TRAINING_COLUMNS_USED)
        for personal in ("first", "last", "street", "dob", "job", "lat"):
            self.assertNotIn(personal, used)

    def test_cleaning_drops_unusable_rows(self):
        rows = [{"id": 1, "timestamp": T0, "amount": 10, "counterparty": " A ", "merchant": None, "category": ""},
                {"id": 2, "timestamp": None, "amount": 10, "counterparty": "A", "merchant": None, "category": "x"},
                {"id": 3, "timestamp": T0, "amount": 0, "counterparty": "A", "merchant": None, "category": "x"}]
        cleaned = preprocessing.clean_transactions(rows)
        self.assertEqual([t["id"] for t in cleaned], [1])
        self.assertEqual((cleaned[0]["counterparty"], cleaned[0]["category"]), ("A", "Uncategorized"))

    def test_features_do_not_depend_on_currency_or_business_size(self):
        txns = preprocessing.clean_transactions(business())
        scaled = [{**t, "amount": t["amount"] * 60} for t in txns]
        a, _ = preprocessing.build_features(txns, True, 0.5)
        b, _ = preprocessing.build_features(scaled, True, 0.5)
        for ra, rb in zip(a, b):
            for x, y in zip(ra, rb):
                self.assertAlmostEqual(x, y, places=9)

    def test_detects_whether_data_has_times(self):
        self.assertTrue(preprocessing.has_times(business(with_time=True)))
        self.assertFalse(preprocessing.has_times(business(with_time=False)))


@unittest.skipUnless(MODELS_READY, "trained model not available")
class ModelTests(unittest.TestCase):
    def test_model_card_reports_test_results(self):
        card = anomaly_model.model_card()
        result = card["evaluation"]["with_time"]["fraud_classifier"]
        self.assertGreater(result["roc_auc"], 0.9)

    def test_few_flags_on_a_normal_business(self):
        for with_time in (True, False):
            txns = business(seed=3, with_time=with_time)
            flags = anomaly_model.run_ai_checks(txns, settings(), FMT)
            self.assertLess(len(flags) / len(txns), 0.03)

    def test_catches_a_late_night_burst_of_large_payments(self):
        for with_time in (True, False):
            txns = business(seed=4, with_time=with_time) + night_burst(with_time=with_time)
            flagged = {f["txn_ids"][0] for f in anomaly_model.run_ai_checks(txns, settings(), FMT)
                       if f["type"] == "fraud"}
            self.assertTrue({900, 901, 902, 903} <= flagged, with_time)

    def test_reasons_are_plain_language(self):
        txns = business(seed=4) + night_burst()
        fraud = [f for f in anomaly_model.run_ai_checks(txns, settings(), FMT) if f["txn_ids"] == [900]]
        self.assertIn("late at night", fraud[0]["reason"])
        self.assertIn("% fraud risk", fraud[0]["reason"])

    def test_needs_enough_payments_and_can_be_turned_off(self):
        self.assertEqual(anomaly_model.run_ai_checks(business(n=10), settings(), FMT), [])
        off = settings(ml_enabled=False, fraud_model_enabled=False)
        self.assertEqual(anomaly_model.run_ai_checks(business(), off, FMT), [])

    def test_ai_flags_get_severity(self):
        found = [f for f in find_anomalies(business(seed=5) + night_burst(), settings())
                 if f["method"] == "machine_learning"]
        self.assertTrue(found)
        self.assertTrue(all(0 <= f["severity_score"] <= 100 for f in found))


if __name__ == "__main__":
    unittest.main()
