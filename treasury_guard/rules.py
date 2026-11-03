"""Sprint 2: rule-based checks.

Each check takes a business's money-out transactions (see transactions.load_outflows_for_detection)
and its settings, and returns flag dicts. `fmt` formats money in the business's currency.

Flag dict fields: key, type, method, txn_ids, occurred_at, amount, counterparty, merchant,
category, reason, details (the numbers behind the flag, also used for severity scoring).
"""
from collections import defaultdict


def _stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def _flag(key, kind, txn, reason, details, txn_ids=None, amount=None):
    return {
        "key": key,
        "type": kind,
        "method": "rule",
        "txn_ids": txn_ids or [txn["id"]],
        "occurred_at": _stamp(txn["timestamp"]),
        "amount": txn["amount"] if amount is None else amount,
        "counterparty": txn["counterparty"],
        "merchant": txn["merchant"],
        "category": txn["category"],
        "reason": reason,
        "details": details,
    }


def find_double_charges(transactions, s, fmt):
    """Same account (or same merchant if no account), near-identical amount, within the time window."""
    groups = defaultdict(list)
    for t in transactions:
        if t["counterparty"]:
            groups[f"Account {t['counterparty']}"].append(t)
        elif t["merchant"]:
            groups[f"Merchant {t['merchant']}"].append(t)
        # without an account or merchant we can't tell who was charged, so it's skipped

    flags = []
    for who, group in groups.items():
        group.sort(key=lambda t: (t["timestamp"], t["id"]))
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                hours = (b["timestamp"] - a["timestamp"]).total_seconds() / 3600
                if hours > s["dup_window_hours"]:
                    break
                diff = abs(b["amount"] - a["amount"])
                diff_pct = diff / max(a["amount"], b["amount"]) * 100
                if diff_pct <= s["dup_tolerance_pct"]:
                    flags.append(_flag(
                        f"dup-{a['id']}-{b['id']}", "duplicate", b,
                        (f"{who} was charged {fmt(b['amount'])} only {round(hours, 1):g} hours after a charge of "
                         f"{fmt(a['amount'])}. The two amounts are {round(diff_pct, 1):g}% apart, so this may be "
                         f"the same payment made twice."),
                        {"hours_apart": round(hours, 2), "amount_diff_pct": round(diff_pct, 2),
                         "first_amount": a["amount"], "window_hours": s["dup_window_hours"],
                         "tolerance_pct": s["dup_tolerance_pct"]},
                        txn_ids=[a["id"], b["id"]],
                    ))
    return flags


def find_unusual_payment_sizes(transactions, s, fmt):
    """Payments at or above the large limit, or at or below the small limit."""
    flags = []
    for t in transactions:
        if t["amount"] >= s["large_limit"]:
            flags.append(_flag(
                f"large-{t['id']}", "large", t,
                f"This payment of {fmt(t['amount'])} is at or above your large-payment limit of {fmt(s['large_limit'])}.",
                {"limit": s["large_limit"]},
            ))
        elif t["amount"] <= s["small_limit"]:
            flags.append(_flag(
                f"small-{t['id']}", "small", t,
                (f"This payment of {fmt(t['amount'])} is at or below your small-payment limit of "
                 f"{fmt(s['small_limit'])}. Very small charges are sometimes used to test a card or account."),
                {"limit": s["small_limit"]},
            ))
    return flags


MIN_ACTIVE_DAYS = 3  # a category needs spending on this many days in the month before a jump can be judged


def find_category_spending_jumps(transactions, s, fmt):
    """A day's spending in a category compared with a usual day of spending in that category that month.

    Usual day = category total for the month / number of days that month with spending in that category.
    (Days with no spending in the category don't count: otherwise any ordinary purchase in a category
    bought only now and then looks like a jump. That made this check flag about half of all payments.)
    """
    active_days = defaultdict(set)
    month_total = defaultdict(float)
    day_total = defaultdict(float)
    day_txns = defaultdict(list)
    for t in transactions:
        day = t["timestamp"].date()
        key = (day.strftime("%Y-%m"), t["category"])
        active_days[key].add(day)
        month_total[key] += t["amount"]
        day_total[(day, t["category"])] += t["amount"]
        day_txns[(day, t["category"])].append(t)

    flags = []
    for (day, category), total in sorted(day_total.items()):
        month = day.strftime("%Y-%m")
        days = len(active_days[(month, category)])
        if days < MIN_ACTIVE_DAYS:
            continue
        average = month_total[(month, category)] / days
        ratio = total / average
        if ratio >= s["spike_multiplier"]:
            txns = day_txns[(day, category)]
            first = txns[0]
            flag = _flag(
                f"spike-{category}-{day.isoformat()}", "spike", first,
                (f"{category} spending on {day.strftime('%b %d, %Y')} was {fmt(total)} across {len(txns)} payments, "
                 f"{round(ratio, 2):g}x a usual {category} day in {day.strftime('%B %Y')} "
                 f"({fmt(average)} on the {days} days you spent on {category})."),
                {"day_total": round(total, 2), "daily_average": round(average, 2), "ratio": round(ratio, 2),
                 "multiplier": s["spike_multiplier"], "payments": len(txns), "active_days": days},
                txn_ids=[t["id"] for t in txns], amount=round(total, 2),
            )
            flag.update(occurred_at=day.isoformat(), counterparty=None, merchant=None)
            flags.append(flag)
    return flags


def run_rule_checks(transactions, settings, fmt):
    return (
        find_double_charges(transactions, settings, fmt)
        + find_unusual_payment_sizes(transactions, settings, fmt)
        + find_category_spending_jumps(transactions, settings, fmt)
    )
