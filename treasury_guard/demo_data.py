"""Load the Kaggle Fraud Detection Dataset into a business as sample data.

Source: https://www.kaggle.com/datasets/goyaladi/fraud-detection-dataset
It is joined into one CSV and imported through the normal CSV import, so it goes
through the same validation and column mapping as an owner's own file.
"""
import csv
import io
from pathlib import Path

from .csv_import import import_csv

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEMO_FILENAME = "kaggle_fraud_detection_demo.csv"
DEMO_MAPPING = {
    "reference": "TransactionID",
    "date": "Timestamp",
    "amount": "Amount",
    "counterparty": "CustomerID",
    "merchant": "MerchantID",
    "category": "Category",
}


def _read(name):
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return {row["TransactionID"]: row for row in csv.DictReader(f)}


def build_demo_csv():
    """Join the three dataset files into one CSV (bytes)."""
    records = _read("transaction_records.csv")
    metadata = _read("transaction_metadata.csv")
    categories = _read("transaction_category_labels.csv")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["TransactionID", "Timestamp", "Amount", "CustomerID", "MerchantID", "Category"])
    for txn_id in sorted(records, key=int):
        if txn_id in metadata and txn_id in categories:
            writer.writerow([
                txn_id, metadata[txn_id]["Timestamp"], records[txn_id]["Amount"],
                records[txn_id]["CustomerID"], metadata[txn_id]["MerchantID"], categories[txn_id]["Category"],
            ])
    return out.getvalue().encode("utf-8")


def load_demo_data(conn, business_id):
    return import_csv(conn, business_id, DEMO_FILENAME, build_demo_csv(), DEMO_MAPPING)
