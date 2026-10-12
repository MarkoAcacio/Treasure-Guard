"""Load the Kaggle Fraud Detection Dataset and join it into one list of transactions.

Uses three files from https://www.kaggle.com/datasets/goyaladi/fraud-detection-dataset:
  - transaction_records.csv          TransactionID, Amount, CustomerID
  - transaction_metadata.csv         TransactionID, Timestamp, MerchantID
  - transaction_category_labels.csv  TransactionID, Category
"""
import csv
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _read(name):
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return {int(row["TransactionID"]): row for row in csv.DictReader(f)}


def load_transactions():
    records = _read("transaction_records.csv")
    metadata = _read("transaction_metadata.csv")
    categories = _read("transaction_category_labels.csv")

    transactions = []
    for txn_id in sorted(records):
        if txn_id not in metadata or txn_id not in categories:
            continue  # skip rows that can't be joined
        ts = datetime.strptime(metadata[txn_id]["Timestamp"], "%Y-%m-%d %H:%M:%S")
        transactions.append({
            "id": txn_id,
            "timestamp": ts,
            "amount": round(float(records[txn_id]["Amount"]), 2),
            "customer_id": int(records[txn_id]["CustomerID"]),
            "merchant_id": int(metadata[txn_id]["MerchantID"]),
            "category": categories[txn_id]["Category"],
        })
    return transactions
