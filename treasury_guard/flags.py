"""Storage for anomaly flags and detection runs."""
import json

from .database import now

STATUSES = ("new", "confirmed", "dismissed")
SEVERITIES = ("high", "medium", "low")


def save_detection_results(conn, business_id, flags, scanned, settings):
    """Store a detection run for one business.

    Flags that still trigger keep their review status; flags that no longer trigger are removed.
    """
    stamp = now()
    new_keys = {f["key"] for f in flags}
    old_keys = {r[0] for r in conn.execute("SELECT flag_key FROM flags WHERE business_id = ?", (business_id,))}
    conn.executemany(
        "DELETE FROM flags WHERE business_id = ? AND flag_key = ?",
        [(business_id, k) for k in old_keys - new_keys],
    )
    conn.executemany(
        """INSERT INTO flags (business_id, flag_key, type, method, txn_ids, occurred_at, amount, counterparty,
                              merchant, category, reason, details, severity_score, severity, status,
                              detected_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
           ON CONFLICT(business_id, flag_key) DO UPDATE SET
               type = excluded.type, method = excluded.method, txn_ids = excluded.txn_ids,
               occurred_at = excluded.occurred_at, amount = excluded.amount,
               counterparty = excluded.counterparty, merchant = excluded.merchant,
               category = excluded.category, reason = excluded.reason, details = excluded.details,
               severity_score = excluded.severity_score, severity = excluded.severity,
               last_seen_at = excluded.last_seen_at""",
        [(business_id, f["key"], f["type"], f["method"], json.dumps(f["txn_ids"]), f["occurred_at"], f["amount"],
          f["counterparty"], f["merchant"], f["category"], f["reason"], json.dumps(f["details"]),
          f["severity_score"], f["severity"], stamp, stamp) for f in flags],
    )
    counts = {}
    for f in flags:
        counts[f["type"]] = counts.get(f["type"], 0) + 1
    conn.execute(
        "INSERT INTO detection_runs (business_id, ran_at, scanned, total, counts, settings) VALUES (?, ?, ?, ?, ?, ?)",
        (business_id, stamp, scanned, len(flags), json.dumps(counts), json.dumps(settings)),
    )
    conn.commit()
    return {"ran_at": stamp, "scanned": scanned, "total": len(flags), "counts": counts}


def _to_dict(row):
    d = dict(row)
    d.pop("business_id", None)
    d["txn_ids"] = json.loads(d["txn_ids"])
    d["details"] = json.loads(d["details"])
    return d


def list_flags(conn, business_id, flag_type=None, status=None, severity=None, min_score=None,
               sort="severity"):
    sql, args = "SELECT * FROM flags WHERE business_id = ?", [business_id]
    if flag_type:
        sql += " AND type = ?"
        args.append(flag_type)
    if status:
        sql += " AND status = ?"
        args.append(status)
    if severity:
        sql += " AND severity = ?"
        args.append(severity)
    if min_score is not None:
        sql += " AND severity_score >= ?"
        args.append(int(min_score))
    sql += (" ORDER BY severity_score DESC, occurred_at DESC" if sort == "severity"
            else " ORDER BY occurred_at DESC, flag_key")
    return [_to_dict(r) for r in conn.execute(sql, args)]


def get_flag(conn, business_id, flag_key):
    row = conn.execute("SELECT * FROM flags WHERE business_id = ? AND flag_key = ?", (business_id, flag_key)).fetchone()
    return _to_dict(row) if row else None


def set_status(conn, business_id, flag_key, status):
    if status not in STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(STATUSES)}.")
    cur = conn.execute(
        "UPDATE flags SET status = ?, reviewed_at = ? WHERE business_id = ? AND flag_key = ?",
        (status, now(), business_id, flag_key),
    )
    conn.commit()
    return cur.rowcount == 1


def count_by(conn, business_id, column):
    if column not in ("type", "severity", "status", "method"):
        raise ValueError(f"Can't count flags by {column}.")
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT {column}, COUNT(*) FROM flags WHERE business_id = ? GROUP BY {column}", (business_id,)
    )}


def last_run(conn, business_id):
    row = conn.execute(
        "SELECT * FROM detection_runs WHERE business_id = ? ORDER BY id DESC LIMIT 1", (business_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d.pop("business_id", None)
    d["counts"] = json.loads(d["counts"])
    d["settings"] = json.loads(d["settings"])
    return d
