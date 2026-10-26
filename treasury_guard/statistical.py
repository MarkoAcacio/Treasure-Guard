"""Sprint 3: transaction-level statistical checks (Z-score and IQR).

Each payment is compared with the business's other payments in the same category.
Categories with fewer than `min_category_samples` payments are compared with all
of the business's payments instead, so small categories still get checked.
"""
import statistics
from collections import defaultdict


def _groups(transactions, min_samples):
    """Yield (label, group_transactions, members_to_check)."""
    by_category = defaultdict(list)
    for t in transactions:
        by_category[t["category"]].append(t)
    small = []
    for category, group in by_category.items():
        if len(group) >= min_samples:
            yield f"your usual {category} payments", group, group
        else:
            small.extend(group)
    if small and len(transactions) >= min_samples:
        yield "all your payments", transactions, small


def _flag(key, kind, t, reason, details):
    return {
        "key": key,
        "type": kind,
        "method": "statistical",
        "txn_ids": [t["id"]],
        "occurred_at": t["timestamp"].strftime("%Y-%m-%d %H:%M"),
        "amount": t["amount"],
        "counterparty": t["counterparty"],
        "merchant": t["merchant"],
        "category": t["category"],
        "reason": reason,
        "details": details,
    }


def find_amounts_far_from_average(transactions, s, fmt):
    """Z-score: how many standard deviations a payment is from the group's average."""
    flags = []
    for label, group, members in _groups(transactions, s["min_category_samples"]):
        amounts = [t["amount"] for t in group]
        mean = statistics.fmean(amounts)
        std = statistics.pstdev(amounts)
        if std == 0:
            continue
        for t in members:
            z = (t["amount"] - mean) / std
            if abs(z) >= s["zscore_threshold"]:
                side = "higher" if z > 0 else "lower"
                flags.append(_flag(
                    f"zscore-{t['id']}", "zscore", t,
                    (f"This payment of {fmt(t['amount'])} is much {side} than {label} "
                     f"(average {fmt(mean)}). It is {abs(z):.1f} standard deviations from the average; "
                     f"your limit is {s['zscore_threshold']:g}."),
                    {"z_score": round(z, 2), "mean": round(mean, 2), "std_dev": round(std, 2),
                     "threshold": s["zscore_threshold"], "compared_with": label, "group_size": len(group)},
                ))
    return flags


def find_amounts_outside_normal_range(transactions, s, fmt):
    """IQR: flag payments beyond Q1 - k*IQR or Q3 + k*IQR of the group."""
    flags = []
    k = s["iqr_multiplier"]
    for label, group, members in _groups(transactions, s["min_category_samples"]):
        amounts = [t["amount"] for t in group]
        q1, _, q3 = statistics.quantiles(amounts, n=4, method="inclusive")
        iqr = q3 - q1
        if iqr == 0:
            continue
        low, high = q1 - k * iqr, q3 + k * iqr
        for t in members:
            if t["amount"] > high:
                distance = (t["amount"] - high) / iqr
                side = "above"
            elif low > 0 and t["amount"] < low:
                distance = (low - t["amount"]) / iqr
                side = "below"
            else:
                continue
            flags.append(_flag(
                f"iqr-{t['id']}", "iqr", t,
                (f"This payment of {fmt(t['amount'])} is {side} the normal range for {label} "
                 f"({fmt(max(low, 0))} to {fmt(high)})."),
                {"q1": round(q1, 2), "q3": round(q3, 2), "iqr": round(iqr, 2), "lower_fence": round(low, 2),
                 "upper_fence": round(high, 2), "multiplier": k, "distance_in_iqr": round(distance, 2),
                 "compared_with": label, "group_size": len(group)},
            ))
    return flags


def run_statistical_checks(transactions, settings, fmt):
    if not settings["statistical_enabled"] or len(transactions) < settings["min_category_samples"]:
        return []
    return (
        find_amounts_far_from_average(transactions, settings, fmt)
        + find_amounts_outside_normal_range(transactions, settings, fmt)
    )
