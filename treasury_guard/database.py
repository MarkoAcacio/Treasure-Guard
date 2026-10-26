"""SQLite database: connection and schema.

Every business's data is kept separate by `business_id`.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "instance" / "treasury_guard.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS businesses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    business_type TEXT NOT NULL DEFAULT 'Other',
    currency      TEXT NOT NULL DEFAULT 'PHP',
    address       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    UNIQUE (business_id, name)
);

CREATE TABLE IF NOT EXISTS uploads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id    INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    uploaded_at    TEXT NOT NULL,
    rows_total     INTEGER NOT NULL,
    rows_imported  INTEGER NOT NULL,
    rows_skipped   INTEGER NOT NULL,
    column_mapping TEXT NOT NULL            -- JSON {field: csv column}
);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id  INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    upload_id    INTEGER REFERENCES uploads(id) ON DELETE SET NULL,
    reference    TEXT,                      -- the transaction ID from the file, if any
    occurred_at  TEXT NOT NULL,             -- 'YYYY-MM-DD HH:MM:SS'
    amount       REAL NOT NULL,             -- always positive
    direction    TEXT NOT NULL DEFAULT 'out',   -- 'out' = money paid, 'in' = money received
    counterparty TEXT,                      -- who was paid / the account charged
    merchant     TEXT,
    category     TEXT NOT NULL DEFAULT 'Uncategorized',
    description  TEXT
);
CREATE INDEX IF NOT EXISTS ix_txn_business_time ON transactions (business_id, occurred_at);

CREATE TABLE IF NOT EXISTS detection_settings (
    business_id INTEGER PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
    value       TEXT NOT NULL               -- JSON of settings
);

CREATE TABLE IF NOT EXISTS flags (
    business_id   INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    flag_key      TEXT NOT NULL,            -- stable ID, e.g. 'dup-587-590'
    type          TEXT NOT NULL,            -- duplicate | large | small | spike | zscore | iqr
    method        TEXT NOT NULL,            -- rule | statistical
    txn_ids       TEXT NOT NULL,            -- JSON list of transactions.id
    occurred_at   TEXT NOT NULL,
    amount        REAL NOT NULL,
    counterparty  TEXT,
    merchant      TEXT,
    category      TEXT,
    reason        TEXT NOT NULL,
    details       TEXT NOT NULL DEFAULT '{}',   -- JSON numbers behind the flag
    severity_score INTEGER NOT NULL DEFAULT 0,  -- 0-100
    severity      TEXT NOT NULL DEFAULT 'low',  -- low | medium | high
    status        TEXT NOT NULL DEFAULT 'new',  -- new | confirmed | dismissed
    detected_at   TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    reviewed_at   TEXT,
    PRIMARY KEY (business_id, flag_key)
);

CREATE TABLE IF NOT EXISTS detection_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    ran_at      TEXT NOT NULL,
    scanned     INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    counts      TEXT NOT NULL,              -- JSON {type: count}
    settings    TEXT NOT NULL               -- JSON of settings used
);
"""


SCHEMA_VERSION = 2

# Tables from the single-business Increment 2 MVP. They held only flags generated from the
# demo dataset, so they are dropped and re-created per business.
_OLD_TABLES = ("flags", "settings", "runs")


def connect(path=DEFAULT_DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        _upgrade_from_mvp(conn)
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def _upgrade_from_mvp(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(flags)")}
    if cols and "business_id" not in cols:
        for table in _OLD_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()


def now():
    return datetime.now().isoformat(timespec="seconds")
