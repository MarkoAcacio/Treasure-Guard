"""Sprint 4 dashboard numbers: spending summary, spending trend and top categories.

Only payments (money out) count as spending. A payment counts as "flagged" while it has at
least one flag that hasn't been marked OK.
"""
from collections import defaultdict
from datetime import datetime, timedelta


def _payments(conn, business_id):
    return [(datetime.strptime(r["occurred_at"], "%Y-%m-%d %H:%M:%S"), r["amount"], r["category"], r["id"])
            for r in conn.execute("SELECT id, occurred_at, amount, category FROM transactions "
                                  "WHERE business_id = ? AND direction = 'out' ORDER BY occurred_at", (business_id,))]


def flagged_ids(conn, business_id, statuses=("new", "confirmed")):
    marks = ",".join("?" * len(statuses))
    return {r[0] for r in conn.execute(
        f"""SELECT DISTINCT CAST(j.value AS INTEGER) FROM flags f, json_each(f.txn_ids) j
            WHERE f.business_id = ? AND f.status IN ({marks})""", (business_id, *statuses))}


def overview(conn, business_id):
    """Headline numbers for the dashboard, or None if there are no payments yet."""
    rows = _payments(conn, business_id)
    if not rows:
        return None
    latest = rows[-1][0]
    last30 = sum(a for d, a, _, _ in rows if d > latest - timedelta(days=30))
    # Only compare with the 30 days before when the data covers all of them; a partial month misleads.
    full_prev = rows[0][0] <= latest - timedelta(days=60)
    prev30 = sum(a for d, a, _, _ in rows if latest - timedelta(days=60) < d <= latest - timedelta(days=30)) if full_prev else 0
    waiting = flagged_ids(conn, business_id, ("new",))
    income = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE business_id = ? AND direction = 'in'",
                          (business_id,)).fetchone()[0]
    return {
        "total_spent": sum(a for _, a, _, _ in rows),
        "payments": len(rows),
        "income": income,
        "first_date": rows[0][0].date().isoformat(),
        "last_date": latest.date().isoformat(),
        "last30": last30,
        "prev30": prev30,
        "change_pct": round((last30 - prev30) / prev30 * 100) if prev30 else None,
        "waiting_amount": sum(a for _, a, _, i in rows if i in waiting),
        "waiting_payments": len(waiting),
    }


def trend(conn, business_id):
    """Spending per day, week or month (whichever fits the data), with the flagged part of each."""
    rows = _payments(conn, business_id)
    if not rows:
        return {"unit": "day", "buckets": []}
    span = (rows[-1][0] - rows[0][0]).days
    unit = "day" if span <= 62 else "week" if span <= 400 else "month"

    def bucket_start(d):
        d = d.date()
        if unit == "day":
            return d
        if unit == "week":
            return d - timedelta(days=d.weekday())
        return d.replace(day=1)

    flagged = flagged_ids(conn, business_id)
    spent, flagged_spent = defaultdict(float), defaultdict(float)
    for d, a, _, i in rows:
        key = bucket_start(d)
        spent[key] += a
        if i in flagged:
            flagged_spent[key] += a
    # Fill gaps so the chart's time axis is even.
    keys, k, last = [], bucket_start(rows[0][0]), bucket_start(rows[-1][0])
    while k <= last:
        keys.append(k)
        if unit == "day":
            k += timedelta(days=1)
        elif unit == "week":
            k += timedelta(days=7)
        else:
            k = (k.replace(day=28) + timedelta(days=4)).replace(day=1)
    label = (lambda k: f"{k:%b} {k.day}") if unit != "month" else (lambda k: f"{k:%b %Y}")
    return {"unit": unit, "buckets": [
        {"start": k.isoformat(), "label": label(k), "spent": round(spent[k], 2), "flagged": round(flagged_spent[k], 2)}
        for k in keys]}


def categories(conn, business_id, top=6):
    """Spending per category, biggest first; the rest are grouped as 'Everything else'."""
    flagged = flagged_ids(conn, business_id)
    spent, count_flagged = defaultdict(float), defaultdict(int)
    for _, a, c, i in _payments(conn, business_id):
        spent[c] += a
        count_flagged[c] += i in flagged
    total = sum(spent.values()) or 1
    ranked = sorted(spent.items(), key=lambda kv: -kv[1])
    out = [{"name": c, "spent": round(v, 2), "share": round(v / total * 100, 1), "flagged": count_flagged[c]}
           for c, v in ranked[:top]]
    rest = ranked[top:]
    if rest:
        v = sum(x for _, x in rest)
        out.append({"name": "Everything else", "spent": round(v, 2), "share": round(v / total * 100, 1),
                    "flagged": sum(count_flagged[c] for c, _ in rest)})
    return out
