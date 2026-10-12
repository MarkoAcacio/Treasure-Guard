"""SQLite storage for anomaly flags, rule settings and detection runs."""
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "instance" / "treasury_guard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,          -- duplicate | large | small | spike
    txn_ids      TEXT NOT NULL,          -- JSON list of transaction IDs
    timestamp    TEXT NOT NULL,
    amount       REAL NOT NULL,
    customer_id  INTEGER,
    merchant_id  INTEGER,
    category     TEXT,
    reason       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'new',   -- new | confirmed | dismissed
    detected_at  TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    reviewed_at  TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    value TEXT NOT NULL                  -- JSON of rule settings
);
CREATE TABLE IF NOT EXISTS runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at   TEXT NOT NULL,
    scanned  INTEGER NOT NULL,
    total    INTEGER NOT NULL,
    counts   TEXT NOT NULL,              -- JSON {type: count}
    settings TEXT NOT NULL               -- JSON of settings used
);
"""

STATUSES = ("new", "confirmed", "dismissed")


def connect(path=DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _ts(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def get_settings(conn, defaults):
    row = conn.execute("SELECT value FROM settings WHERE id = 1").fetchone()
    return {**defaults, **json.loads(row["value"])} if row else dict(defaults)


def save_settings(conn, settings):
    conn.execute(
        "INSERT INTO settings (id, value) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET value = excluded.value",
        (json.dumps(settings),),
    )
    conn.commit()


def save_flags(conn, flags, scanned, settings):
    """Store a detection run. Flags that still trigger keep their review status;
    flags that no longer trigger are removed."""
    now = _now()
    new_ids = {f["id"] for f in flags}
    for (old_id,) in conn.execute("SELECT id FROM flags").fetchall():
        if old_id not in new_ids:
            conn.execute("DELETE FROM flags WHERE id = ?", (old_id,))

    for f in flags:
        conn.execute(
            """
            INSERT INTO flags (id, type, txn_ids, timestamp, amount, customer_id, merchant_id,
                               category, reason, status, detected_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                txn_ids = excluded.txn_ids, timestamp = excluded.timestamp, amount = excluded.amount,
                customer_id = excluded.customer_id, merchant_id = excluded.merchant_id,
                category = excluded.category, reason = excluded.reason, last_seen_at = excluded.last_seen_at
            """,
            (f["id"], f["type"], json.dumps(f["txn_ids"]), _ts(f["timestamp"]), f["amount"],
             f["customer_id"], f["merchant_id"], f["category"], f["reason"], now, now),
        )

    counts = {}
    for f in flags:
        counts[f["type"]] = counts.get(f["type"], 0) + 1
    conn.execute(
        "INSERT INTO runs (ran_at, scanned, total, counts, settings) VALUES (?, ?, ?, ?, ?)",
        (now, scanned, len(flags), json.dumps(counts), json.dumps(settings)),
    )
    conn.commit()


def list_flags(conn, flag_type=None, status=None):
    sql, args = "SELECT * FROM flags WHERE 1=1", []
    if flag_type:
        sql += " AND type = ?"
        args.append(flag_type)
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY timestamp DESC, id"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        r["txn_ids"] = json.loads(r["txn_ids"])
    return rows


def count_by_type(conn):
    return {r["type"]: r["n"] for r in conn.execute("SELECT type, COUNT(*) AS n FROM flags GROUP BY type")}


def last_run(conn):
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def set_status(conn, flag_id, status):
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")
    conn.execute("UPDATE flags SET status = ?, reviewed_at = ? WHERE id = ?", (status, _now(), flag_id))
    conn.commit()
