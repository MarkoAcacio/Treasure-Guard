"""Pre-processing for the AI models.

Two jobs:
1. Keep only the columns the models need (from the training dataset or from an owner's
   uploaded CSV) and clean them into one standard transaction format.
2. Turn those transactions into numeric features.

The same feature code is used for training and for scoring each business's uploads, so
the models always see data prepared the same way.

Training data: Kaggle "Credit Card Transactions Fraud Detection Dataset" (kartik2112,
simulated with Sparkov). Each card is treated like one business: the card's purchases are
its payments and the merchants are its payees.

Features are "scale-free": they compare each payment with the same business's own
history (its usual amount, the same category, the same payee) instead of using raw
amounts, so a model trained on US card data works for a Philippine business of any size.
"""
import math
from bisect import bisect_left, insort
from collections import defaultdict
from datetime import datetime
from statistics import median, quantiles

# ---------- 1. Columns: what is used and what is dropped ----------

USD_TO_PHP = 62.879                  # European Central Bank reference rate, 2026-10-14
USD_TO_PHP_DATE = "2026-10-14"

TRAINING_DATASET = "kartik2112/fraud-detection"
TRAINING_COLUMNS_USED = {
    "trans_date_trans_time": "Date and time of the payment",
    "amt": "Amount (USD, converted to PHP)",
    "cc_num": "Card number; groups payments into one 'business' (never stored or shown)",
    "merchant": "Payee",
    "category": "Category",
    "is_fraud": "Fraud label; the target for the fraud model and for evaluation only",
}
TRAINING_COLUMNS_DROPPED = {
    "first, last, gender, dob, job": "Personal details of the cardholder, not payment behaviour.",
    "street, city, state, zip, lat, long, city_pop": "Home address and location; not in MSME payment records.",
    "merch_lat, merch_long": "Merchant location; not in MSME payment records.",
    "trans_num, unix_time, Unnamed: 0": "IDs and a duplicate of the timestamp.",
}

CATEGORY_NAMES = {
    "entertainment": "Entertainment", "food_dining": "Food & dining", "gas_transport": "Gas & transport",
    "grocery_net": "Grocery (online)", "grocery_pos": "Grocery (in store)", "health_fitness": "Health & fitness",
    "home": "Home", "kids_pets": "Kids & pets", "misc_net": "Miscellaneous (online)",
    "misc_pos": "Miscellaneous (in store)", "personal_care": "Personal care",
    "shopping_net": "Shopping (online)", "shopping_pos": "Shopping (in store)", "travel": "Travel",
}

# Standard transaction format used everywhere after pre-processing.
STANDARD_FIELDS = ("id", "timestamp", "amount", "counterparty", "merchant", "category")


def clean_transactions(rows):
    """Keep only the needed fields and drop rows the models can't use.

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


def clean_training_row(row_id, when, amount_usd, merchant, category):
    """One training-dataset row in the standard format (USD -> PHP, readable names)."""
    merchant = merchant[6:] if merchant.startswith("fraud_") else merchant  # every merchant has this prefix
    return {
        "id": row_id,
        "timestamp": datetime.strptime(when, "%Y-%m-%d %H:%M:%S"),
        "amount": round(float(amount_usd) * USD_TO_PHP, 2),
        "counterparty": merchant,
        "merchant": merchant,
        "category": CATEGORY_NAMES.get(category, category),
    }


# ---------- 2. Features ----------

BASE_FEATURES = (
    "amount_vs_usual",           # log(amount / the business's median payment)
    "amount_vs_category",        # robust z-score of log(amount) within its category
    "hours_since_same_payee",    # log(1 + hours since the previous payment to the same payee), capped at 3 days
    "change_from_last_payment",  # |log(amount / previous amount to the same payee)|
    "payments_same_payee_24h",   # log(1 + payments to the same payee in the 24 hours before)
    "burst_vs_usual_day",        # log(1 + payments in the last 24h) - log(1 + usual payments per day)
    "new_payee",                 # 1 if this is the first payment to this payee, else 0
    "amount_vs_payee",           # log(amount / median of earlier payments to the same payee); 0 for a new payee
    "day_of_week",               # 0 = Monday ... 6 = Sunday
    # Last 7 days / 24 hours (fraud tends to come in bursts over a few days)
    "spend_7d_vs_usual",         # log(spending in the last 7 days, this payment included / usual 7-day spending)
    "payments_7d_vs_usual",      # log(1 + payments in the 7 days before) - log(1 + usual payments per 7 days)
    "new_payees_24h",            # log(1 + payments to first-time payees in the 24 hours before)
)
TIME_FEATURES = (
    "hour_sin", "hour_cos",      # time of day, only when the data has times
    "night_payments_7d",         # log(1 + payments between 10 PM and 5 AM in the 7 days before)
)
NIGHT_HOURS = {22, 23, 0, 1, 2, 3, 4}
WEEK_SECONDS = 7 * 86400
# In a business's first 7 days there is no earlier week to compare with (every payee is new, recent
# spending looks tiny), so the 7-day and new-payee features are set to neutral values until then.
GAP_CAP_HOURS = 72
MIN_CATEGORY_SIZE = 5
_EPOCH = datetime(1970, 1, 1)


def feature_names(use_time):
    return BASE_FEATURES + (TIME_FEATURES if use_time else ())


def has_times(transactions):
    """True if the data has times of day (not just dates)."""
    return any(t["timestamp"].hour or t["timestamp"].minute or t["timestamp"].second for t in transactions)


def _seconds(dt):
    return (dt - _EPOCH).total_seconds()


def _robust_scale(values):
    """Median and IQR (falls back to 1.0 when the spread is zero or there are too few values)."""
    if len(values) < 4:
        return (median(values) if values else 0.0), 1.0
    q1, q2, q3 = quantiles(values, n=4, method="inclusive")
    return q2, (q3 - q1) or 1.0


def build_features(transactions, use_time, fill_change):
    """Features for ONE business's cleaned transactions (sorted by time).

    Returns (rows, context): one feature row and one context dict (for explanations) per
    transaction. `fill_change` is used for change_from_last_payment when a payee has no
    earlier payment; it comes from training so scoring matches.
    """
    if not transactions:
        return [], []
    logs = [math.log(t["amount"]) for t in transactions]
    usual = median(logs)
    by_category = defaultdict(list)
    for t, lg in zip(transactions, logs):
        by_category[t["category"]].append(lg)
    overall_scale = _robust_scale(logs)
    category_scale = {c: (_robust_scale(v) if len(v) >= MIN_CATEGORY_SIZE else overall_scale)
                      for c, v in by_category.items()}

    times = [_seconds(t["timestamp"]) for t in transactions]
    span_days = max((times[-1] - times[0]) / 86400, 1.0)
    usual_per_day = len(transactions) / span_days
    usual_week_spend = sum(t["amount"] for t in transactions) / span_days * 7
    cum_amount, cum_new, cum_night = [0.0], [0], [0]   # running totals, for 7-day and 24-hour windows

    last_payment = {}                  # payee -> (time, amount)
    payee_times = defaultdict(list)    # payee -> sorted times
    payee_logs = defaultdict(list)     # payee -> sorted log amounts of earlier payments
    rows, context = [], []
    for i, (t, lg, now) in enumerate(zip(transactions, logs, times)):
        payee = t["counterparty"] or t["merchant"] or "_unknown"
        cutoff = now - 86400
        if payee in last_payment:
            prev_time, prev_amount = last_payment[payee]
            gap = min((now - prev_time) / 3600, GAP_CAP_HOURS)
            change = abs(math.log(t["amount"] / prev_amount))
            same_payee_24h = len(payee_times[payee]) - bisect_left(payee_times[payee], cutoff)
        else:
            gap, change, same_payee_24h = GAP_CAP_HOURS, fill_change, 0
        business_24h = i - bisect_left(times, cutoff, 0, i)
        week_start = bisect_left(times, now - WEEK_SECONDS, 0, i)
        day_start = bisect_left(times, cutoff, 0, i)
        spend_7d = cum_amount[i] - cum_amount[week_start] + t["amount"]
        earlier = payee_logs[payee]
        new_payee = not earlier
        payee_mid = None if new_payee else median(earlier)
        last_payment[payee] = (now, t["amount"])
        payee_times[payee].append(now)
        insort(earlier, lg)

        mid, spread = category_scale[t["category"]]
        row = [
            lg - usual,
            (lg - mid) / spread,
            math.log1p(max(gap, 0.0)),
            change,
            math.log1p(same_payee_24h),
            math.log1p(business_24h) - math.log1p(usual_per_day),
            1.0 if new_payee else 0.0,
            0.0 if new_payee else lg - payee_mid,
            float(t["timestamp"].weekday()),
            math.log(spend_7d / usual_week_spend),
            math.log1p(i - week_start) - math.log1p(usual_per_day * 7),
            math.log1p(cum_new[i] - cum_new[day_start]),
        ]
        if use_time:
            hour = t["timestamp"].hour + t["timestamp"].minute / 60
            row += [math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24),
                    math.log1p(cum_night[i] - cum_night[week_start])]
        if now - times[0] < WEEK_SECONDS:  # not enough history yet: neutral values
            row[9:12] = [0.0, 0.0, 0.0]
            if use_time:
                row[14] = 0.0
        rows.append(row)
        cum_amount.append(cum_amount[-1] + t["amount"])
        cum_new.append(cum_new[-1] + (1 if new_payee else 0))
        cum_night.append(cum_night[-1] + (1 if t["timestamp"].hour in NIGHT_HOURS else 0))
        context.append({
            "gap_hours": None if gap == GAP_CAP_HOURS else gap,
            "same_payee_24h": same_payee_24h,
            "business_24h": business_24h,
            "usual_per_day": usual_per_day,
            "usual_amount": math.exp(usual),
            "category_typical": math.exp(mid),
            "new_payee": new_payee,
            "payee_typical": None if new_payee else math.exp(payee_mid),
            "spend_7d": spend_7d,
            "usual_week_spend": usual_week_spend,
            "new_payees_24h": cum_new[i] - cum_new[day_start],
            "night_7d": cum_night[i] - cum_night[week_start],
        })
    return rows, context
