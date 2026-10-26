"""Sprint 1 accounts and CSV import, the JSON API, and the existing pages."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from treasury_guard import csv_import

H = {"X-Requested-With": "TreasuryGuard"}

GOOD_CSV = b"""Date,Amount,Payee,Category,Ref
2026-01-05,"1,200.00",Supplier A,Inventory,R1
01/06/2026,(350.50),Meralco,Utilities,R2
2026-01-07 14:30,PHP 99,Supplier A,Inventory,R3
not a date,10,Supplier B,Inventory,R4
"""


class CsvImportUnitTests(unittest.TestCase):
    def test_suggests_mapping_from_headers(self):
        preview = csv_import.preview_csv(GOOD_CSV)
        self.assertEqual(preview["suggested_mapping"],
                         {"date": "Date", "amount": "Amount", "reference": "Ref", "counterparty": "Payee",
                          "category": "Category"})
        self.assertEqual(preview["row_count"], 4)

    def test_parses_money_formats(self):
        self.assertEqual(csv_import.parse_amount("₱1,234.50"), 1234.5)
        self.assertEqual(csv_import.parse_amount("(20)"), -20)
        self.assertIsNone(csv_import.parse_amount("abc"))

    def test_mapping_must_include_required_fields(self):
        with self.assertRaises(csv_import.CsvImportError):
            csv_import.check_mapping({"date": "Date"}, ["Date", "Amount"])
        with self.assertRaises(csv_import.CsvImportError):
            csv_import.check_mapping({"date": "Date", "amount": "Nope"}, ["Date", "Amount"])


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name) / "test.db")
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, client=None, email="owner@example.com"):
        return (client or self.client).post("/api/auth/register", headers=H, json={
            "email": email, "password": "password123", "full_name": "Ana Cruz",
            "business_name": "Ana's Lugawan", "business_type": "Food & beverage"})

    def test_api_requires_login_and_csrf_header(self):
        self.assertEqual(self.client.get("/api/flags").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/register", json={}).status_code, 403)

    def test_register_login_logout(self):
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 400)  # same email again
        self.client.post("/api/auth/logout", headers=H)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        bad = self.client.post("/api/auth/login", headers=H, json={"email": "owner@example.com", "password": "x"})
        self.assertEqual(bad.status_code, 401)
        ok = self.client.post("/api/auth/login", headers=H, json={"email": "owner@example.com", "password": "password123"})
        self.assertEqual(ok.get_json()["business"]["name"], "Ana's Lugawan")

    def test_upload_flow_with_validation(self):
        self.register()
        preview = self.client.post("/api/uploads/preview", headers=H,
                                   data={"file": (io.BytesIO(GOOD_CSV), "jan.csv")}).get_json()
        mapping = preview["suggested_mapping"]
        res = self.client.post("/api/uploads", headers=H, data={
            "file": (io.BytesIO(GOOD_CSV), "jan.csv"), "mapping": json.dumps(mapping)})
        body = res.get_json()
        self.assertEqual(res.status_code, 201)
        self.assertEqual((body["rows_imported"], body["rows_skipped_invalid"]), (3, 1))
        self.assertEqual(body["errors"][0]["line"], 5)
        # Uploading the same file again skips rows already imported (by reference number)
        again = self.client.post("/api/uploads", headers=H, data={
            "file": (io.BytesIO(GOOD_CSV), "jan.csv"), "mapping": json.dumps(mapping)}).get_json()
        self.assertEqual(again["rows_skipped_already_imported"], 3)
        txns = self.client.get("/api/transactions").get_json()
        self.assertEqual(txns["total"], 3)
        self.assertIn("Utilities", self.client.get("/api/categories").get_json()["categories"])

    def test_businesses_cannot_see_each_others_data(self):
        self.register()
        self.client.post("/api/demo/load", headers=H)
        other = self.app.test_client()
        self.register(other, email="other@example.com")
        self.assertEqual(other.get("/api/transactions").get_json()["total"], 0)
        self.assertEqual(other.get("/api/flags").get_json()["count"], 0)

    def test_demo_detection_settings_and_flag_review(self):
        self.register()
        demo = self.client.post("/api/demo/load", headers=H).get_json()
        self.assertEqual(demo["rows_imported"], 1000)
        self.assertEqual(demo["detection"]["counts"]["duplicate"], 3)
        flags = self.client.get("/api/flags").get_json()["flags"]
        scores = [f["severity_score"] for f in flags]
        self.assertEqual(scores, sorted(scores, reverse=True))  # most severe first
        key = flags[0]["flag_key"]
        self.client.patch(f"/api/flags/{key}", headers=H, json={"status": "confirmed"})
        # Re-running with new settings keeps the review status of flags that still trigger
        self.client.put("/api/detection/settings", headers=H, json={"sensitivity": "high"})
        self.assertEqual(self.client.get(f"/api/flags/{key}").get_json()["status_name"], "Confirmed issue")
        bad = self.client.put("/api/detection/settings", headers=H, json={"zscore_threshold": 99})
        self.assertEqual(bad.status_code, 400)
        summary = self.client.get("/api/summary").get_json()
        self.assertEqual(summary["transactions"], 1000)
        self.assertIn("high", summary["flags_by_severity"])

    def test_template_and_model_info(self):
        template = self.client.get("/api/uploads/template")
        self.assertEqual(template.status_code, 200)
        self.assertTrue(template.get_data(as_text=True).startswith("Date,Amount,Type"))
        model = self.client.get("/api/model").get_json()
        self.assertEqual(model["status"], "ready")
        self.assertIn("fraud_classifier", model["models"])

    def test_upload_page_two_steps(self):
        self.client.post("/signup", data={"full_name": "Ana", "email": "up@example.com", "password": "password123",
                                          "business_name": "Shop", "business_type": "Retail"})
        page = self.client.get("/upload").get_data(as_text=True)
        self.assertIn("Choose a file", page)
        step2 = self.client.post("/upload", data={"file": (io.BytesIO(GOOD_CSV), "jan.csv")},
                                 content_type="multipart/form-data").get_data(as_text=True)
        self.assertIn("Match your columns", step2)
        self.assertIn('value="Payee" selected', step2)
        done = self.client.post("/upload/import", data={
            "map_date": "Date", "map_amount": "Amount", "map_counterparty": "Payee",
            "map_category": "Category", "map_reference": "Ref"}).get_data(as_text=True)
        self.assertIn("Upload complete", done)
        self.assertIn("<td>3</td>", done)  # imported rows
        self.assertIn("jan.csv", self.client.get("/upload").get_data(as_text=True))
        again = self.client.post("/upload/import", data={"map_date": "Date", "map_amount": "Amount"})
        self.assertIn("expired", again.get_data(as_text=True))

    def test_all_pages_with_sample_data(self):
        self.client.post("/signup", data={"full_name": "Ana", "email": "pages@example.com", "password": "password123",
                                          "business_name": "Ana's Lugawan", "business_type": "Food & beverage"})
        demo = self.client.post("/upload/demo").get_data(as_text=True)
        self.assertIn("Upload complete", demo)
        dash = self.client.get("/").get_data(as_text=True)
        for text in ("Ana's Lugawan", "Possible fraud (AI model)", "Far from the usual amount", "Severity"):
            self.assertIn(text, dash.replace("&#39;", "'"))
        self.assertIn("High", self.client.get("/?severity=high").get_data(as_text=True))
        tx = self.client.get("/transactions?flagged=1").get_data(as_text=True)
        self.assertIn("transactions · page 1", tx)
        self.assertEqual(self.client.get("/transactions?page=2&category=Food").status_code, 200)
        self.assertIn("Test results", self.client.get("/model").get_data(as_text=True))

    def test_settings_page_presets_and_switches(self):
        self.client.post("/signup", data={"full_name": "Ana", "email": "set@example.com", "password": "password123",
                                          "business_name": "Shop", "business_type": "Retail"})
        page = self.client.get("/rules").get_data(as_text=True)
        self.assertIn("Overall sensitivity", page)
        form = {"sensitivity": "high", "dup_window_hours": "72", "dup_tolerance_pct": "5", "large_limit": "50000",
                "small_limit": "10", "spike_multiplier": "2", "zscore_threshold": "3", "iqr_multiplier": "1.5",
                "min_category_samples": "10", "ml_percentile": "99", "fraud_min_probability_pct": "",
                "statistical_enabled": "1", "fraud_model_enabled": "1"}
        self.assertEqual(self.client.post("/rules", data=form).status_code, 302)
        saved = self.client.get("/api/detection/settings").get_json()["settings"]
        self.assertEqual((saved["sensitivity"], saved["zscore_threshold"]), ("high", 2.5))  # preset won
        self.assertFalse(saved["ml_enabled"])  # unchecked box turns it off
        form.update(sensitivity="high", zscore_threshold="2.5", iqr_multiplier="1.0", spike_multiplier="1.5",
                    dup_tolerance_pct="8", ml_percentile="97", fraud_min_probability_pct="40")
        self.client.post("/rules", data=form)
        saved = self.client.get("/api/detection/settings").get_json()["settings"]
        self.assertEqual((saved["fraud_min_probability"], saved["sensitivity"]), (0.4, "custom"))
        bad = self.client.post("/rules", data={**form, "small_limit": "90000"}).get_data(as_text=True)
        self.assertIn("must be lower", bad)

    def test_business_profile_page(self):
        self.client.post("/signup", data={"full_name": "Ana", "email": "biz@example.com", "password": "password123",
                                          "business_name": "Shop", "business_type": "Retail"})
        saved = self.client.post("/business", data={"action": "profile", "name": "New Name", "business_type": "Services",
                                                    "currency": "USD", "address": "Cavite"}).get_data(as_text=True)
        self.assertIn("Saved.", saved)
        self.assertIn("New Name", saved)
        added = self.client.post("/business", data={"action": "add_category", "category": "Packaging"})
        self.assertIn("Packaging", added.get_data(as_text=True))

    def test_existing_pages_still_work(self):
        self.assertEqual(self.client.get("/").status_code, 302)  # sends to login
        signup = self.client.post("/signup", data={
            "full_name": "Ana", "email": "page@example.com", "password": "password123",
            "business_name": "Shop", "business_type": "Retail", "load_demo": "1"})
        self.assertEqual(signup.status_code, 302)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Possible double charge", page.get_data(as_text=True))
        self.assertEqual(self.client.get("/rules").status_code, 200)
        saved = self.client.post("/rules", data={"dup_window_hours": "24", "dup_tolerance_pct": "5",
                                                 "large_limit": "97", "small_limit": "12", "spike_multiplier": "2"})
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(self.client.get("/later/alerts").status_code, 200)
        self.assertEqual(self.client.get("/logout").status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 302)


if __name__ == "__main__":
    unittest.main()
