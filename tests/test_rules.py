"""Unit tests for the Sprint 2 rules. Run: python -m unittest"""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from treasury_guard import storage
from treasury_guard.data_loader import load_transactions
from treasury_guard.rules import (DEFAULT_SETTINGS, detect_category_spikes, detect_duplicates,
                                  detect_unusual_outflows, run_all_rules)

T0 = datetime(2022, 1, 1, 9, 0)


def txn(id, hours, amount, customer=1, category="Food"):
    return {"id": id, "timestamp": T0 + timedelta(hours=hours), "amount": amount,
            "customer_id": customer, "merchant_id": 100, "category": category}


class RuleTests(unittest.TestCase):
    def test_duplicate_within_window_and_tolerance(self):
        flags = detect_duplicates([txn(1, 0, 50.0), txn(2, 3, 51.0)], DEFAULT_SETTINGS)
        self.assertEqual([f["id"] for f in flags], ["dup-1-2"])

    def test_no_duplicate_when_too_far_apart_or_different_customer(self):
        s = DEFAULT_SETTINGS
        self.assertEqual(detect_duplicates([txn(1, 0, 50.0), txn(2, 100, 50.0)], s), [])
        self.assertEqual(detect_duplicates([txn(1, 0, 50.0), txn(2, 1, 50.0, customer=2)], s), [])

    def test_large_and_small_outflows(self):
        flags = detect_unusual_outflows([txn(1, 0, 98.0), txn(2, 1, 11.0), txn(3, 2, 50.0)], DEFAULT_SETTINGS)
        self.assertEqual(sorted(f["id"] for f in flags), ["large-1", "small-2"])

    def test_category_spike(self):
        # 10 days of Food at 10/day, then one day at 100 -> avg = 200/11 ≈ 18.2, ratio ≈ 5.5
        txns = [txn(i, 24 * i, 10.0) for i in range(10)] + [txn(99, 24 * 10, 100.0)]
        flags = detect_category_spikes(txns, DEFAULT_SETTINGS)
        self.assertEqual([f["id"] for f in flags], ["spike-Food-2022-01-11"])


class DatasetTests(unittest.TestCase):
    def test_default_run_on_kaggle_dataset(self):
        txns = load_transactions()
        self.assertEqual(len(txns), 1000)
        counts = {}
        for f in run_all_rules(txns):
            counts[f["type"]] = counts.get(f["type"], 0) + 1
        self.assertEqual(counts, {"duplicate": 3, "large": 30, "small": 18, "spike": 3})


class StorageTests(unittest.TestCase):
    def test_rerun_keeps_review_status(self):
        with tempfile.TemporaryDirectory() as d:
            conn = storage.connect(Path(d) / "test.db")
            flags = detect_unusual_outflows([txn(1, 0, 98.0), txn(2, 1, 11.0)], DEFAULT_SETTINGS)
            storage.save_flags(conn, flags, 2, DEFAULT_SETTINGS)
            storage.set_status(conn, "large-1", "confirmed")
            storage.save_flags(conn, flags[:1], 2, DEFAULT_SETTINGS)  # small-2 no longer triggers
            rows = storage.list_flags(conn)
            self.assertEqual([(r["id"], r["status"]) for r in rows], [("large-1", "confirmed")])
            conn.close()


if __name__ == "__main__":
    unittest.main()
