"""Pre-processing for the anomaly model.

Two jobs:
1. Keep only the columns the model needs (from the Kaggle training files or from an
   owner's uploaded CSV) and clean them into one standard transaction format.
2. Turn those transactions into numeric features.

The same feature code is used for training (Kaggle data) and for scoring (each
business's uploads), so the model always sees data prepared the same way.

Features are "scale-free": they compare each payment with the same business's other
payments (in the same category, or to the same account) instead of using raw peso
amounts. That lets a model trained on the Kaggle data (amounts 10-100) score a business
whose payments are in the thousands, with very different amounts per category.
"""
import csv
import math
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median, quantiles

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------- 1. Columns: what is used and what is dropped ----------

# Kaggle "Fraud Detection Dataset" (goyaladi): only these files/columns are used for training.
KAGGLE_COLUMNS_USED = {
    "transaction_records.csv": ["TransactionID", "Amount", "CustomerID"],
    "transaction_metadata.csv": ["TransactionID", "Timestamp", "MerchantID"],
    "transaction_category_labels.csv": ["TransactionID", "Category"],
}
# Used only to evaluate the model afterwards, never as a training input.
KAGGLE_LABEL_FILE = ("fraud_indicators.csv", "FraudIndicator")
# Not used, and why.
KAGGLE_COLUMNS_DROPPED = {
    "customer_data.csv": "Name, age and address are personal details, not payment behaviour.",
    "account_activity.csv": "Account balance and last login are not in an MSME's payment records.",
    "suspicious_activity.csv": "A customer-level label, not a feature of each payment.",
    "amount_data.csv": "A second, unrelated amount column; transaction_records.Amount is used instead.",
    "anomaly_scores.csv": "A precomputed score; using it would copy someone else's answer.",
}

# Standard transaction format used everywhere after pre-processing.
STANDARD_FIELDS = ("id", "timestamp", "amount", "counterparty", "merchant", "category")


def clean_transactions(rows):
    """Keep only the needed fields and drop rows the model can't use.

    `rows` are dicts with at least: id, timestamp (datetime), amount, counterparty,
    merchant, category. Extra keys are ignored. Returns new dicts sorted by time.
    """
    cleaned = []
    for r in rows:
        amount = r.get("amount")
        when = r.get("timestamp")
        if not isinstance(when, datetime) or amount is None or amount <= 0:
            continue
        category = (r.get("category") or "Uncategorized").strip() or "Uncategorized"
        cleaned.append({
            "id": r["id"],
            "timestamp": when,
            "amount": float(amount),
            "counterparty": (str(r["counterparty"]).strip() if r.get("counterparty") else None),
            "merchant": (str(r["merchant"]).strip() if r.get("merchant") else None),
            "category": category,
        })
    cleaned.sort(key=lambda t: (t["timestamp"], t["id"]))
    return cleaned


def _read(name):
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return {row["TransactionID"]: row for row in csv.DictReader(f)}


def load_kaggle_training_data():
    """Read only the used Kaggle columns and return (transactions, fraud_labels)."""
    records = _read("transaction_records.csv")
    metadata = _read("transaction_metadata.csv")
    categories = _read("transaction_category_labels.csv")
    rows = []
    for txn_id, rec in records.items():
        if txn_id not in metadata or txn_id not in categories:
            continue
        rows.append({
            "id": int(txn_id),
            "timestamp": datetime.strptime(metadata[txn_id]["Timestamp"], "%Y-%m-%d %H:%M:%S"),
            "amount": float(rec["Amount"]),
            "counterparty": rec["CustomerID"],
            "merchant": metadata[txn_id]["MerchantID"],
            "category": categories[txn_id]["Category"],
        })
    labels = {}
    label_path = DATA_DIR / KAGGLE_LABEL_FILE[0]
    if label_path.exists():
        with open(label_path, newline="", encoding="utf-8") as f:
            labels = {int(r["TransactionID"]): int(r[KAGGLE_LABEL_FILE[1]]) for r in csv.DictReader(f)}
    return clean_transactions(rows), labels


# ---------- 2. Features ----------

FEATURES = (
    "amount_vs_category",        # robust z-score of log(amount) within its category
    "hours_since_same_account",  # log(1 + hours since the previous payment to the same account), capped at 3 days
    "change_from_last_payment",  # |log(amount / previous amount to the same account)|
    "payments_same_account_24h", # log(1 + payments to the same account in the 24 hours before)
)
GAP_CAP_HOURS = 72               # gaps longer than 3 days all count as "no recent payment"
MIN_CATEGORY_SIZE = 5            # smaller categories are compared with all payments


def _robust_scale(values):
    """Median and IQR (falls back to 1.0 when the spread is zero or there are too few values)."""
    if len(values) < 4:
        return (median(values) if values else 0.0), 1.0
    q1, q2, q3 = quantiles(values, n=4, method="inclusive")
    return q2, (q3 - q1) or 1.0


def build_features(transactions, fill_change=None):
    """Return (rows_of_feature_values, context) for cleaned transactions (one row per transaction).

    `fill_change` is the value used for change_from_last_payment when an account has no
    earlier payment; training stores its median so scoring uses the same value.
    """
    if not transactions:
        return [], {}
    logs = [math.log(t["amount"]) for t in transactions]

    by_category = defaultdict(list)
    for t, lg in zip(transactions, logs):
        by_category[t["category"]].append(lg)
    overall_scale = _robust_scale(logs)
    category_scale = {c: (_robust_scale(v) if len(v) >= MIN_CATEGORY_SIZE else overall_scale)
                      for c, v in by_category.items()}

    # Payment history per account (counterparty, else merchant), in time order.
    history = defaultdict(list)  # account -> list of (timestamp, amount)
    history_times = defaultdict(list)  # account -> list of POSIX times (sorted)
    raw_changes = []
    rows, context = [], []
    for t, lg in zip(transactions, logs):
        account = t["counterparty"] or t["merchant"] or "_unknown"
        past = history[account]
        if past:
            prev_time, prev_amount = past[-1]
            gap = min((t["timestamp"] - prev_time).total_seconds() / 3600, GAP_CAP_HOURS)
            change = abs(math.log(t["amount"] / prev_amount))
            raw_changes.append(change)
            times = history_times[account]
            cutoff = t["timestamp"].timestamp() - 24 * 3600
            recent = len(times) - bisect_left(times, cutoff)
        else:
            gap, change, recent = GAP_CAP_HOURS, None, 0
        past.append((t["timestamp"], t["amount"]))
        history_times[account].append(t["timestamp"].timestamp())

        mid, spread = category_scale[t["category"]]
        rows.append([
            (lg - mid) / spread,
            math.log1p(max(gap, 0.0)),
            change,
            math.log1p(recent),
        ])
        context.append({"account": account, "gap_hours": None if gap == GAP_CAP_HOURS else gap,
                        "recent_count": recent, "category_typical": math.exp(mid)})

    fill = fill_change if fill_change is not None else (median(raw_changes) if raw_changes else math.log(2))
    for row in rows:
        if row[2] is None:
            row[2] = fill
    return rows, {"per_row": context, "fill_change": fill}
