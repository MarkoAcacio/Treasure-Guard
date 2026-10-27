"""Reading a business's transactions."""
from datetime import datetime

MAX_PAGE_SIZE = 200


def _row_to_dict(row):
    d = dict(row)
    d.pop("business_id", None)
    return d


def list_transactions(conn, business_id, category=None, date_from=None, date_to=None,
                      direction=None, flagged_only=False, page=1, per_page=50):
    """A page of transactions, newest first, each with its highest flag severity (if flagged)."""
    per_page = max(1, min(int(per_page), MAX_PAGE_SIZE))
    page = max(1, int(page))
    where, args = ["t.business_id = ?"], [business_id]
    if category:
        where.append("t.category = ?")
        args.append(category)
    if date_from:
        where.append("t.occurred_at >= ?")
        args.append(date_from)
    if date_to:
        where.append("t.occurred_at <= ?")
        args.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)
    if direction in ("in", "out"):
        where.append("t.direction = ?")
        args.append(direction)

    # Flags store their transaction IDs as a JSON list; json_each lets SQLite match them.
    flag_join = """
        LEFT JOIN (
            SELECT CAST(j.value AS INTEGER) AS txn_id,
                   MAX(f.severity_score) AS severity_score,
                   COUNT(*) AS flag_count
            FROM flags f, json_each(f.txn_ids) j
            WHERE f.business_id = ?
            GROUP BY txn_id
        ) fl ON fl.txn_id = t.id
    """
    if flagged_only:
        where.append("fl.txn_id IS NOT NULL")
    sql_where = " AND ".join(where)

    total = conn.execute(
        f"SELECT COUNT(*) FROM transactions t {flag_join} WHERE {sql_where}", (business_id, *args)
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT t.*, fl.severity_score, COALESCE(fl.flag_count, 0) AS flag_count
            FROM transactions t {flag_join}
            WHERE {sql_where}
            ORDER BY t.occurred_at DESC, t.id DESC
            LIMIT ? OFFSET ?""",
        (business_id, *args, per_page, (page - 1) * per_page),
    ).fetchall()
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "transactions": [_row_to_dict(r) for r in rows],
    }


def load_outflows_for_detection(conn, business_id):
    """All money-out transactions of a business, ready for the detection rules."""
    rows = conn.execute(
        """SELECT id, reference, occurred_at, amount, counterparty, merchant, category
           FROM transactions WHERE business_id = ? AND direction = 'out'
           ORDER BY occurred_at, id""",
        (business_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "reference": r["reference"],
            "timestamp": datetime.strptime(r["occurred_at"], "%Y-%m-%d %H:%M:%S"),
            "amount": r["amount"],
            "counterparty": r["counterparty"],
            "merchant": r["merchant"],
            "category": r["category"],
        }
        for r in rows
    ]


def references_for(conn, business_id, ids):
    """{transaction id: reference number (or '#id' when the file had none)} for display."""
    ids = list(ids)
    if not ids:
        return {}
    out = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT id, reference FROM transactions WHERE business_id = ? AND id IN ({marks})", (business_id, *chunk)
        ):
            out[r["id"]] = r["reference"] or f"#{r['id']}"
    return out


def by_ids(conn, business_id, ids):
    """Full transaction rows for these IDs, oldest first."""
    ids = list(ids)[:500]
    if not ids:
        return []
    rows = conn.execute(f"SELECT * FROM transactions WHERE business_id = ? AND id IN ({','.join('?' * len(ids))}) "
                        "ORDER BY occurred_at, id", (business_id, *ids))
    return [_row_to_dict(r) for r in rows]


def count_transactions(conn, business_id):
    return conn.execute("SELECT COUNT(*) FROM transactions WHERE business_id = ?", (business_id,)).fetchone()[0]
