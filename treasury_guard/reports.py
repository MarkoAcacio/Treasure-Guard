"""Reports: a summary for a date range (printable as PDF from the browser) and CSV exports."""
import csv
import io
from collections import Counter

from . import flags as flag_store
from .labels import FLAG_TYPE_NAMES, METHOD_NAMES, SEVERITY_NAMES, STATUS_NAMES


def _in_range(stamp, date_from, date_to):
    day = stamp[:10]
    return (not date_from or day >= date_from) and (not date_to or day <= date_to)


def flags_in_range(conn, business_id, date_from=None, date_to=None):
    return [f for f in flag_store.list_flags(conn, business_id) if _in_range(f["occurred_at"], date_from, date_to)]


def summary(conn, business_id, date_from=None, date_to=None):
    where, args = "business_id = ? AND direction = 'out'", [business_id]
    if date_from:
        where += " AND occurred_at >= ?"
        args.append(date_from)
    if date_to:
        where += " AND occurred_at <= ?"
        args.append(date_to + " 23:59:59")
    spent, payments = conn.execute(f"SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM transactions WHERE {where}",
                                   args).fetchone()
    found = flags_in_range(conn, business_id, date_from, date_to)
    return {
        "spent": spent, "payments": payments, "flags": found,
        "by_type": Counter(f["type"] for f in found),
        "by_status": Counter(f["status"] for f in found),
        "by_severity": Counter(f["severity"] for f in found),
        "confirmed_amount": sum(f["amount"] for f in found if f["status"] == "confirmed"),
    }


def _safe(value):
    """Stop spreadsheet apps from running cell text as a formula (CSV injection)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _csv(header, rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for row in rows:
        w.writerow([_safe(v) for v in row])
    return "﻿" + out.getvalue()  # BOM so Excel reads ₱ correctly


def flags_csv(conn, business_id, date_from=None, date_to=None):
    rows = [(f["occurred_at"], FLAG_TYPE_NAMES.get(f["type"], f["type"]), METHOD_NAMES.get(f["method"], f["method"]),
             SEVERITY_NAMES[f["severity"]], f["severity_score"], STATUS_NAMES[f["status"]],
             f["counterparty"] or f["merchant"] or "", f["category"] or "", f["amount"], f["reason"],
             f["review_note"], (f["reviewed_at"] or "").replace("T", " "))
            for f in flags_in_range(conn, business_id, date_from, date_to)]
    return _csv(["Date", "Check", "Method", "Severity", "Score", "Status", "Payee", "Category", "Amount",
                 "Why it was flagged", "Review note", "Reviewed at"], rows)


def transactions_csv(conn, business_id, date_from=None, date_to=None):
    where, args = "business_id = ?", [business_id]
    if date_from:
        where += " AND occurred_at >= ?"
        args.append(date_from)
    if date_to:
        where += " AND occurred_at <= ?"
        args.append(date_to + " 23:59:59")
    rows = conn.execute(f"""SELECT occurred_at, reference, direction, counterparty, merchant, category, amount,
                                   description FROM transactions WHERE {where} ORDER BY occurred_at""", args)
    return _csv(["Date", "Reference", "Type", "Payee", "Merchant", "Category", "Amount", "Description"],
                [(r[0], r[1] or "", "Payment" if r[2] == "out" else "Income", *(v or "" for v in r[3:6]), r[6],
                  r[7] or "") for r in rows])
