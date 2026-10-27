"""Combined risk score: one 0-100 number per payment from all three kinds of checks.

1. For each kind of check (rule, statistical, AI model), take the strongest flag on the
   payment, using the flag's base score (before severity's agreement bonus).
2. Risk = the strongest of those, plus a bonus when different kinds agree:
   +15 when two kinds flagged it, +25 when all three did. Capped at 100.
3. Same levels as severity: 75+ High, 50-74 Medium, below 50 Low.

Flags marked OK don't count, so a payment whose flags were all marked OK drops off.
"""
from collections import defaultdict

from . import flags as flag_store
from .severity import level_for

METHODS = ("rule", "statistical", "machine_learning")
KIND_BONUS = {1: 0, 2: 15, 3: 25}


def scores_by_transaction(conn, business_id):
    """{transaction id: {"score", "level", "methods": {method: score}, "flags": [flag, ...]}}"""
    per_txn = defaultdict(lambda: {"methods": {}, "flags": []})
    for f in flag_store.list_flags(conn, business_id):
        if f["status"] == "dismissed":
            continue
        base = f["details"].get("base_score", f["severity_score"])
        for txn_id in f["txn_ids"]:
            entry = per_txn[txn_id]
            entry["methods"][f["method"]] = max(base, entry["methods"].get(f["method"], 0))
            entry["flags"].append(f)
    for entry in per_txn.values():
        score = min(100, max(entry["methods"].values()) + KIND_BONUS[len(entry["methods"])])
        entry.update(score=score, level=level_for(score))
    return dict(per_txn)


def ranked(conn, business_id, level=None, limit=200):
    """Flagged payments, highest risk first, with their transaction details."""
    scores = scores_by_transaction(conn, business_id)
    ids = sorted(scores, key=lambda i: -scores[i]["score"])
    if level:
        ids = [i for i in ids if scores[i]["level"] == level]
    total = len(ids)
    ids = ids[:limit]
    txns = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for r in conn.execute(f"SELECT * FROM transactions WHERE business_id = ? AND id IN ({','.join('?' * len(chunk))})",
                              (business_id, *chunk)):
            txns[r["id"]] = dict(r)
    return total, [{**scores[i], "txn": txns[i]} for i in ids if i in txns]
