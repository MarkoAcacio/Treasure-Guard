"""Sprint 2 rule checks. Run all tests: python -m unittest"""
import unittest

from treasury_guard.rules import (find_category_spending_jumps, find_double_charges,
                                  find_unusual_payment_sizes)
from tests.helpers import FMT, settings, txn


class DoubleChargeTests(unittest.TestCase):
    def test_flags_same_account_close_amount_within_window(self):
        flags = find_double_charges([txn(1, 0, 50.0), txn(2, 3, 51.0)], settings(), FMT)
        self.assertEqual([f["key"] for f in flags], ["dup-1-2"])
        self.assertIn("Account A1", flags[0]["reason"])

    def test_ignores_far_apart_or_different_account(self):
        self.assertEqual(find_double_charges([txn(1, 0, 50.0), txn(2, 100, 50.0)], settings(), FMT), [])
        self.assertEqual(find_double_charges([txn(1, 0, 50.0), txn(2, 1, 50.0, account="B2")], settings(), FMT), [])

    def test_falls_back_to_merchant_when_no_account(self):
        flags = find_double_charges([txn(1, 0, 50.0, account=None), txn(2, 1, 50.0, account=None)], settings(), FMT)
        self.assertEqual(len(flags), 1)
        self.assertIn("Merchant M1", flags[0]["reason"])


class PaymentSizeTests(unittest.TestCase):
    def test_large_and_small(self):
        flags = find_unusual_payment_sizes([txn(1, 0, 98.0), txn(2, 1, 11.0), txn(3, 2, 50.0)], settings(), FMT)
        self.assertEqual(sorted(f["key"] for f in flags), ["large-1", "small-2"])
        self.assertIn("₱98.00", flags[0]["reason"])


class CategoryJumpTests(unittest.TestCase):
    def test_spike_against_monthly_daily_average(self):
        # 10 days of Food at 10/day, then one day at 100 -> average = 200/11 ≈ 18.2, ratio ≈ 5.5
        txns = [txn(i, 24 * i, 10.0) for i in range(10)] + [txn(99, 24 * 10, 100.0)]
        flags = find_category_spending_jumps(txns, settings(), FMT)
        self.assertEqual([f["key"] for f in flags], ["spike-Food-2022-01-11"])
        self.assertEqual(flags[0]["txn_ids"], [99])


if __name__ == "__main__":
    unittest.main()
