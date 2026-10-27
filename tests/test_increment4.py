"""Increment 4: dashboard trends, alerts (in-app and email), plain-language explanations, review
workflow, plus the combined risk score, reports, activity log and team roles."""
import tempfile
import unittest
from pathlib import Path

from app import create_app
from treasury_guard import reports

H = {"X-Requested-With": "TreasuryGuard"}


class Increment4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name) / "test.db")
        self.outbox = Path(self.tmp.name) / "outbox"
        self.owner = self.app.test_client()
        self.owner.post("/signup", data={"full_name": "Ana Cruz", "email": "ana@example.com", "password": "password123",
                                         "business_name": "Ana's Lugawan", "business_type": "Food & beverage"})

    def tearDown(self):
        self.tmp.cleanup()

    def load_demo(self):
        return self.owner.post("/upload/demo").get_data(as_text=True)

    def test_dashboard_api_has_summary_trend_and_categories(self):
        self.load_demo()
        d = self.owner.get("/api/dashboard").get_json()
        self.assertEqual(d["overview"]["payments"], 1000)
        self.assertEqual(d["trend"]["unit"], "day")  # demo data covers six weeks
        self.assertEqual(len(d["trend"]["buckets"]), 42)
        self.assertAlmostEqual(sum(b["spent"] for b in d["trend"]["buckets"]), d["overview"]["total_spent"], places=1)
        self.assertGreater(sum(b["flagged"] for b in d["trend"]["buckets"]), 0)
        self.assertEqual(round(sum(c["share"] for c in d["categories"])), 100)

    def test_new_flags_raise_alerts_and_email_goes_to_outbox_not_out(self):
        self.owner.post("/alerts/settings", data={"in_app_min_severity": "medium", "email_min_severity": "high",
                                                  "email_enabled": "1", "email_to": "ana@example.com"})
        page = self.load_demo()
        self.assertIn("new enough to raise an alert", page)
        alerts = self.owner.get("/api/alerts").get_json()
        self.assertGreater(alerts["unread"], 0)
        self.assertTrue(all(a["severity"] in ("high", "medium") for a in alerts["alerts"]))
        emails = list(self.outbox.glob("*.eml"))
        self.assertEqual(len(emails), 1)  # one digest per check-up
        self.assertIn("to review at Ana's Lugawan", emails[0].read_text())
        # Re-running finds no new flags, so no new alerts or emails
        self.owner.post("/run")
        self.assertEqual(self.owner.get("/api/alerts").get_json()["unread"], alerts["unread"])
        self.assertEqual(len(list(self.outbox.glob("*.eml"))), 1)
        # Opening a flag marks its alert read; "mark all" clears the rest
        self.owner.get(f"/flags/{alerts['alerts'][0]['flag_key']}")
        self.assertEqual(self.owner.get("/api/alerts").get_json()["unread"], alerts["unread"] - 1)
        self.owner.post("/alerts/read")
        self.assertEqual(self.owner.get("/api/alerts").get_json()["unread"], 0)

    def test_email_needs_an_address(self):
        self.owner.post("/alerts/settings", data={"in_app_min_severity": "high", "email_min_severity": "high",
                                                  "email_enabled": "1", "email_to": ""})
        self.assertFalse(self.owner.get("/api/alerts/settings").get_json()["settings"]["email_enabled"])

    def test_flag_page_explains_in_plain_language(self):
        self.load_demo()
        f = self.owner.get("/api/flags?type=duplicate").get_json()["flags"][0]
        page = self.owner.get(f"/flags/{f['flag_key']}").get_data(as_text=True).replace("&#39;", "'")
        for text in ("may have been paid twice", "Why it was flagged", "What to check", "The numbers behind it",
                     "Time between them", "Look for two receipts"):
            self.assertIn(text, page)
        ex = self.owner.get(f"/api/flags/{f['flag_key']}/explanation").get_json()
        self.assertTrue(ex["headline"].endswith("may have been paid twice"))
        self.assertTrue(ex["what_to_check"])

    def test_review_queue_with_notes(self):
        self.load_demo()
        first = self.owner.get("/review")
        self.assertIn("queue=1", first.location)
        key = first.location.split("/flags/")[1].split("?")[0]
        nxt = self.owner.post(f"/flags/{key}/status", data={"status": "confirmed", "note": "Refund requested",
                                                            "then": "next"})
        self.assertIn("queue=1", nxt.location)
        self.assertNotIn(key, nxt.location)
        saved = self.owner.get(f"/api/flags/{key}").get_json()
        self.assertEqual((saved["status"], saved["review_note"]), ("confirmed", "Refund requested"))
        self.assertIsNotNone(saved["reviewed_by"])
        # Bulk: mark every remaining flag OK, then the queue is empty
        keys = [f["flag_key"] for f in self.owner.get("/api/flags?status=new").get_json()["flags"]]
        self.owner.post("/flags/bulk", data={"keys": keys, "status": "dismissed"})
        self.assertTrue(self.owner.get("/review").location.endswith("/review/done"))
        log = self.owner.get("/api/activity").get_json()["activity"]
        self.assertIn("Reviewed flags in bulk", [a["action"] for a in log])
        self.assertIn("Loaded sample data", [a["action"] for a in log])

    def test_combined_risk_rewards_agreement_and_drops_marked_ok(self):
        self.load_demo()
        rows = self.owner.get("/api/risk").get_json()["transactions"]
        scores = [r["score"] for r in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))
        multi = next(r for r in rows if len(r["method_scores"]) >= 2)
        self.assertGreaterEqual(multi["score"], min(100, max(multi["method_scores"].values()) + 15))
        for key in rows[0]["flags"]:
            self.owner.patch(f"/api/flags/{key}", headers=H, json={"status": "dismissed"})
        ids = [r["transaction"]["id"] for r in self.owner.get("/api/risk").get_json()["transactions"]]
        self.assertNotIn(rows[0]["transaction"]["id"], ids)

    def test_reports_csv_and_date_range(self):
        self.load_demo()
        csv_text = self.owner.get("/reports/flags.csv").get_data(as_text=True)
        self.assertTrue(csv_text.lstrip("﻿").startswith("Date,Check,Method,Severity"))
        page = self.owner.get("/reports?from=2022-01-01&to=2022-01-07").get_data(as_text=True)
        self.assertIn("Anomaly report", page)
        self.assertEqual(reports._safe("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        tx = self.owner.get("/reports/transactions.csv?from=2022-02-01").get_data(as_text=True)
        self.assertNotIn("2022-01-", tx)

    def test_reused_row_numbers_from_another_file_are_imported(self):
        self.load_demo()  # sample data uses TransactionID 1..1000
        csv_bytes = b"TransactionID,Timestamp,Amount,CustomerID,MerchantID,Category\n1,2026-09-01 14:57:00,127.89,1018,2008,Food\n"
        import io, json
        mapping = {"reference": "TransactionID", "date": "Timestamp", "amount": "Amount", "category": "Category"}
        r = self.owner.post("/api/uploads", headers=H, data={"file": (io.BytesIO(csv_bytes), "mine.csv"),
                                                             "mapping": json.dumps(mapping)}).get_json()
        self.assertEqual(r["rows_imported"], 1)
        again = self.owner.post("/api/uploads", headers=H, data={"file": (io.BytesIO(csv_bytes), "mine.csv"),
                                                                 "mapping": json.dumps(mapping)}).get_json()
        self.assertEqual(again["rows_skipped_already_imported"], 1)  # the same row twice is still skipped

    def test_team_roles(self):
        self.load_demo()
        self.owner.post("/team", data={"action": "add", "full_name": "Ben", "email": "ben@example.com",
                                       "password": "password456", "role": "viewer"})
        viewer = self.app.test_client()
        viewer.post("/login", data={"email": "ben@example.com", "password": "password456"})
        self.assertEqual(viewer.get("/").status_code, 200)  # same business data
        key = viewer.get("/api/flags").get_json()["flags"][0]["flag_key"]
        self.assertEqual(viewer.post(f"/flags/{key}/status", data={"status": "dismissed"}).status_code, 403)
        self.assertEqual(viewer.patch(f"/api/flags/{key}", headers=H, json={"status": "dismissed"}).status_code, 403)
        self.assertEqual(viewer.get("/upload").status_code, 403)
        # Promote to reviewer: can review, still can't upload or change settings
        ben = next(m for m in self.owner.get("/api/team").get_json()["members"] if m["email"] == "ben@example.com")
        self.owner.post("/team", data={"action": "role", "user_id": ben["id"], "role": "reviewer"})
        self.assertEqual(viewer.post(f"/flags/{key}/status", data={"status": "dismissed"}).status_code, 302)
        self.assertEqual(viewer.post("/api/detection/run", headers=H).status_code, 403)
        self.assertEqual(viewer.post("/team", data={"action": "remove", "user_id": ben["id"]}).status_code, 403)
        # The owner can't be removed; removing Ben ends his access
        self.owner.post("/team", data={"action": "remove", "user_id": ben["id"]})
        self.assertEqual(viewer.get("/").status_code, 302)


if __name__ == "__main__":
    unittest.main()
