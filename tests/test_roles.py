"""Owner (admin) and employee accounts: employees upload files, only the owner side sees the results."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from app import create_app

H = {"X-Requested-With": "TreasuryGuard"}
CSV = (Path(__file__).resolve().parent.parent / "docs" / "test_transactions.csv").read_bytes()
MAPPING = {"date": "Date", "amount": "Amount", "direction": "Type", "counterparty": "Payee", "merchant": "Merchant",
           "category": "Category", "reference": "Reference", "description": "Description"}


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name) / "test.db")
        self.owner = self.app.test_client()
        self.owner.post("/signup", data={"account_type": "owner", "full_name": "Ana Cruz", "email": "ana@example.com",
                                         "password": "password123", "business_name": "Ana's Lugawan"})
        self.code = self.owner.get("/api/auth/me").get_json()["business"]["join_code"]

    def tearDown(self):
        self.tmp.cleanup()

    def employee(self, code=None, email="ben@example.com"):
        client = self.app.test_client()
        r = client.post("/signup", data={"account_type": "employee", "full_name": "Ben Reyes", "email": email,
                                         "password": "password456", "join_code": code or self.code})
        return client, r

    def test_owner_gets_a_readable_business_code(self):
        self.assertRegex(self.code, r"^[A-HJ-NP-Z2-9]{8}$")
        self.assertIn(self.code, self.owner.get("/team").get_data(as_text=True))

    def test_employee_signup_needs_a_valid_code(self):
        _, r = self.employee(code="NOPE1234")
        self.assertIn("No business uses that code", r.get_data(as_text=True))
        client, r = self.employee(code=self.code.lower()[:4] + "-" + self.code.lower()[4:])  # forgiving format
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/upload"))
        me = client.get("/api/auth/me").get_json()
        self.assertEqual((me["business"]["name"], me["business"]["role"]), ("Ana's Lugawan", "employee"))

    def test_employee_cannot_see_the_dashboard_or_results(self):
        client, _ = self.employee()
        self.assertTrue(client.get("/").headers["Location"].endswith("/upload"))
        for page in ("/flags", "/risk", "/transactions", "/alerts", "/reports", "/rules", "/team", "/business",
                     "/model", "/activity", "/reports/flags.csv"):
            r = client.get(page)
            self.assertEqual(r.status_code, 302, page)
            self.assertTrue(r.headers["Location"].endswith("/upload"), page)
        for path in ("/api/flags", "/api/dashboard", "/api/summary", "/api/transactions", "/api/risk", "/api/team",
                     "/api/alerts", "/api/activity", "/api/detection/settings"):
            self.assertEqual(client.get(path).status_code, 403, path)
        self.assertEqual(client.post("/api/detection/run", headers=H).status_code, 403)
        page = client.get("/upload").get_data(as_text=True)
        self.assertIn("Upload files", page)
        self.assertNotIn("Flagged payments", page)          # no dashboard links in the menu
        self.assertNotIn("Load sample data", page)

    def test_employee_upload_reaches_the_owner(self):
        client, _ = self.employee()
        r = client.post("/api/uploads", headers=H, data={"file": (io.BytesIO(CSV), "september.csv"),
                                                         "mapping": json.dumps(MAPPING)})
        self.assertEqual(r.status_code, 201)
        self.assertNotIn("detection", r.get_json())        # employees don't get the anomaly results
        self.assertEqual([u["filename"] for u in client.get("/api/uploads").get_json()["uploads"]], ["september.csv"])

        dashboard = self.owner.get("/").get_data(as_text=True)
        self.assertIn("Uploads from your team", dashboard)
        self.assertIn("september.csv", dashboard)
        self.assertIn("Ben Reyes", dashboard)
        upload_id = self.owner.get("/api/uploads").get_json()["uploads"][0]["id"]
        txns = self.owner.get(f"/transactions?upload={upload_id}").get_data(as_text=True)
        self.assertIn("Showing payments from <b>september.csv</b>", txns)

    def test_employee_upload_through_the_pages(self):
        client, _ = self.employee()
        page = client.post("/upload", data={"file": (io.BytesIO(CSV), "sept.csv")},
                           content_type="multipart/form-data").get_data(as_text=True)
        self.assertIn("Step 2", page)
        page = client.post("/upload/import", data={f"map_{k}": v for k, v in MAPPING.items()}).get_data(as_text=True)
        self.assertIn("Sent: sept.csv", page)
        self.assertNotIn("Start reviewing", page)
        self.assertEqual(client.post("/upload/demo").status_code, 302)  # sample data is for owners

    def test_employees_only_see_their_own_uploads(self):
        ben, _ = self.employee()
        cara, _ = self.employee(email="cara@example.com")
        ben.post("/api/uploads", headers=H, data={"file": (io.BytesIO(CSV), "ben.csv"), "mapping": json.dumps(MAPPING)})
        self.assertEqual(cara.get("/api/uploads").get_json()["uploads"], [])
        self.assertEqual(len(self.owner.get("/api/uploads").get_json()["uploads"]), 1)

    def test_new_code_stops_the_old_one(self):
        self.owner.post("/team", data={"action": "new_code"})
        new = self.owner.get("/api/auth/me").get_json()["business"]["join_code"]
        self.assertNotEqual(new, self.code)
        _, r = self.employee()
        self.assertIn("No business uses that code", r.get_data(as_text=True))
        _, r = self.employee(code=new)
        self.assertEqual(r.status_code, 302)

    def test_owner_can_make_someone_an_employee_and_back(self):
        client, _ = self.employee()
        ben = next(m for m in self.owner.get("/api/team").get_json()["members"] if m["email"] == "ben@example.com")
        self.owner.post("/team", data={"action": "role", "user_id": ben["id"], "role": "reviewer"})
        self.assertEqual(client.get("/flags").status_code, 200)
        self.owner.post("/team", data={"action": "role", "user_id": ben["id"], "role": "employee"})
        self.assertEqual(client.get("/flags").status_code, 302)

    def test_login_sends_each_role_home(self):
        self.employee()
        r = self.app.test_client().post("/login", data={"email": "ben@example.com", "password": "password456"})
        self.assertTrue(r.headers["Location"].endswith("/upload"))
        r = self.app.test_client().post("/login", data={"email": "ana@example.com", "password": "password123"})
        self.assertTrue(r.headers["Location"].endswith("/"))

    def test_api_register_employee(self):
        client = self.app.test_client()
        r = client.post("/api/auth/register", headers=H, json={
            "account_type": "employee", "full_name": "Dina", "email": "dina@example.com", "password": "password789",
            "join_code": self.code})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["business"]["role"], "employee")


if __name__ == "__main__":
    unittest.main()
