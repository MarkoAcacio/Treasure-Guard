"""Sprint 2 rule-based anomaly detection.

Three rules, each returning a list of flag dicts:
  1. duplicate charges
  2. unusual (too large / too small) outflows
  3. category spending spikes against the monthly average
"""
from collections import defaultdict

DEFAULT_SETTINGS = {
    "dup_window_hours": 72,     # max hours between two charges to count as a possible duplicate
    "dup_tolerance_pct": 5.0,   # max difference between the two amounts, in percent
    "large_limit": 97.0,        # flag outflows at or above this amount
    "small_limit": 12.0,        # flag outflows at or below this amount
    "spike_multiplier": 2.0,    # flag a category-day at or above this many times the monthly daily average
}


def _fmt(n):
    return f"{n:,.2f}"


def detect_duplicates(transactions, s):
    """Same customer, near-identical amount, within the time window."""
    flags = []
    by_customer = defaultdict(list)
    for t in transactions:
        by_customer[t["customer_id"]].append(t)

    for group in by_customer.values():
        group.sort(key=lambda t: (t["timestamp"], t["id"]))
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                hours = (b["timestamp"] - a["timestamp"]).total_seconds() / 3600
                if hours > s["dup_window_hours"]:
                    break
                diff = abs(b["amount"] - a["amount"])
                if diff <= s["dup_tolerance_pct"] / 100 * max(a["amount"], b["amount"]):
                    flags.append({
                        "id": f"dup-{a['id']}-{b['id']}",
                        "type": "duplicate",
                        "txn_ids": [a["id"], b["id"]],
                        "timestamp": b["timestamp"],
                        "amount": b["amount"],
                        "customer_id": b["customer_id"],
                        "merchant_id": b["merchant_id"],
                        "category": b["category"],
                        "reason": (
                            f"Customer {b['customer_id']} was charged {_fmt(b['amount'])} only {round(hours, 2):g} hours "
                            f"after a charge of {_fmt(a['amount'])} (transaction #{a['id']}). The amounts differ by "
                            f"{_fmt(diff)}, within the {s['dup_tolerance_pct']:g}% tolerance, so this may be a repeated charge."
                        ),
                    })
    return flags


def detect_unusual_outflows(transactions, s):
    """Amounts at or above the large limit, or at or below the small limit."""
    flags = []
    for t in transactions:
        if t["amount"] >= s["large_limit"]:
            kind, reason = "large", (
                f"This outflow of {_fmt(t['amount'])} is at or above the large-outflow limit of {_fmt(s['large_limit'])}."
            )
        elif t["amount"] <= s["small_limit"]:
            kind, reason = "small", (
                f"This outflow of {_fmt(t['amount'])} is at or below the small-outflow limit of {_fmt(s['small_limit'])}. "
                "Very small charges are sometimes used to test a card or account."
            )
        else:
            continue
        flags.append({
            "id": f"{kind}-{t['id']}",
            "type": kind,
            "txn_ids": [t["id"]],
            "timestamp": t["timestamp"],
            "amount": t["amount"],
            "customer_id": t["customer_id"],
            "merchant_id": t["merchant_id"],
            "category": t["category"],
            "reason": reason,
        })
    return flags


def detect_category_spikes(transactions, s):
    """A day's spend in a category vs. that category's average daily spend for the month.

    Monthly daily average = category total for the month / number of days in the data for that month.
    """
    days_in_month = defaultdict(set)
    month_total = defaultdict(float)
    day_total = defaultdict(float)
    day_ids = defaultdict(list)

    for t in transactions:
        day = t["timestamp"].date()
        month = day.strftime("%Y-%m")
        days_in_month[month].add(day)
        month_total[(month, t["category"])] += t["amount"]
        day_total[(day, t["category"])] += t["amount"]
        day_ids[(day, t["category"])].append(t["id"])

    flags = []
    for (day, category), total in sorted(day_total.items()):
        month = day.strftime("%Y-%m")
        avg = month_total[(month, category)] / len(days_in_month[month])
        ratio = total / avg
        if ratio >= s["spike_multiplier"]:
            ids = day_ids[(day, category)]
            flags.append({
                "id": f"spike-{category}-{day.isoformat()}",
                "type": "spike",
                "txn_ids": ids,
                "timestamp": day,
                "amount": round(total, 2),
                "customer_id": None,
                "merchant_id": None,
                "category": category,
                "reason": (
                    f"{category} spending on {day.isoformat()} was {_fmt(total)} across {len(ids)} transactions, "
                    f"{round(ratio, 2):g}x the average daily {category} spend for {month} ({_fmt(avg)})."
                ),
            })
    return flags


def run_all_rules(transactions, settings=None):
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    return (
        detect_duplicates(transactions, s)
        + detect_unusual_outflows(transactions, s)
        + detect_category_spikes(transactions, s)
    )
