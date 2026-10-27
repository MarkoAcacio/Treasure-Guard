"""Sprint 4: in-app and email alerts for new flags.

After each detection run, flags that weren't there before become alerts if they reach the
business's in-app severity level. If email alerts are on, the ones that reach the email level
are sent together in one email (see mailer.py: saved to a local outbox unless SMTP is set up).
"""
import json
from pathlib import Path

from . import mailer
from .database import now
from .labels import SEVERITY_NAMES, headline, money_formatter

DEFAULT_ALERT_SETTINGS = {
    "in_app_min_severity": "medium",   # high | medium | low
    "email_enabled": False,
    "email_to": "",
    "email_min_severity": "high",
}
LEVELS = ("low", "medium", "high")


class AlertSettingsError(ValueError):
    pass


def reaches(severity, minimum):
    return LEVELS.index(severity) >= LEVELS.index(minimum)


def outbox_for(conn):
    """Saved emails live next to the database file."""
    db_file = conn.execute("PRAGMA database_list").fetchone()["file"]
    return Path(db_file).parent / "outbox"


def get_settings(conn, business_id):
    row = conn.execute("SELECT value FROM alert_settings WHERE business_id = ?", (business_id,)).fetchone()
    return {**DEFAULT_ALERT_SETTINGS, **(json.loads(row[0]) if row else {})}


def update_settings(conn, business_id, changes):
    s = get_settings(conn, business_id)
    for key in ("in_app_min_severity", "email_min_severity"):
        if key in changes:
            if changes[key] not in LEVELS:
                raise AlertSettingsError("Pick High, Medium or Low.")
            s[key] = changes[key]
    if "email_enabled" in changes:
        s["email_enabled"] = bool(changes["email_enabled"])
    if "email_to" in changes:
        s["email_to"] = (changes["email_to"] or "").strip()
    if s["email_enabled"] and "@" not in s["email_to"]:
        raise AlertSettingsError("Enter the email address that should get alerts.")
    conn.execute("INSERT INTO alert_settings (business_id, value) VALUES (?, ?) "
                 "ON CONFLICT(business_id) DO UPDATE SET value = excluded.value", (business_id, json.dumps(s)))
    conn.commit()
    return s


def _link(flag_key):
    try:
        from flask import has_request_context, url_for
        if has_request_context():
            return url_for("flag_page", flag_key=flag_key, _external=True)
    except (ImportError, RuntimeError):
        pass
    return f"/flags/{flag_key}"


def create_for_new_flags(conn, business_id, new_flags):
    """Store alerts for new flags and email the important ones. Returns how many alerts were made."""
    business = conn.execute("SELECT name, currency FROM businesses WHERE id = ?", (business_id,)).fetchone()
    fmt = money_formatter(business["currency"])
    s = get_settings(conn, business_id)
    stamp = now()
    made = []
    for f in sorted(new_flags, key=lambda f: -f["severity_score"]):
        if not reaches(f["severity"], s["in_app_min_severity"]):
            continue
        alert_id = conn.execute(
            "INSERT INTO alerts (business_id, flag_key, severity, title, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (business_id, f["key"], f["severity"], headline(f, fmt), f["reason"], stamp),
        ).lastrowid
        made.append((alert_id, f))
    conn.commit()

    to_email = [(i, f) for i, f in made if reaches(f["severity"], s["email_min_severity"])]
    if s["email_enabled"] and s["email_to"] and to_email:
        send_digest(conn, business_id, business["name"], s["email_to"], [f for _, f in to_email], fmt)
        conn.executemany("UPDATE alerts SET emailed_to = ? WHERE id = ?", [(s["email_to"], i) for i, _ in to_email])
        conn.commit()
    return len(made)


def send_digest(conn, business_id, business_name, to, flags, fmt):
    n = len(flags)
    lines = [f"Hi,", "",
             f"Treasury Guard found {n} new payment{'s' if n != 1 else ''} at {business_name} worth a look:", ""]
    for f in flags[:20]:
        lines += [f"- {SEVERITY_NAMES[f['severity']]}: {headline(f, fmt)}", f"  {_link(f['key'])}", ""]
    if n > 20:
        lines += [f"...and {n - 20} more in the app.", ""]
    lines += ["Open each one to see why it was flagged and mark it as an issue or OK.",
              "You can change or turn off these emails on the Alerts page."]
    subject = f"{n} new flag{'s' if n != 1 else ''} to review at {business_name}"
    return mailer.send(outbox_for(conn), to, subject, "\n".join(lines))


def list_alerts(conn, business_id, unread_only=False, limit=200):
    sql = "SELECT * FROM alerts WHERE business_id = ?" + (" AND read_at IS NULL" if unread_only else "")
    order = (" ORDER BY created_at DESC, CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id"
             " LIMIT ?")
    return [dict(r) for r in conn.execute(sql + order, (business_id, limit))]


def unread_count(conn, business_id):
    return conn.execute("SELECT COUNT(*) FROM alerts WHERE business_id = ? AND read_at IS NULL",
                        (business_id,)).fetchone()[0]


def mark_read(conn, business_id, alert_ids=None):
    """Mark some alerts (or all, when alert_ids is None) as read."""
    if alert_ids is None:
        conn.execute("UPDATE alerts SET read_at = ? WHERE business_id = ? AND read_at IS NULL", (now(), business_id))
    else:
        conn.executemany("UPDATE alerts SET read_at = ? WHERE business_id = ? AND id = ? AND read_at IS NULL",
                         [(now(), business_id, int(i)) for i in alert_ids])
    conn.commit()


def mark_read_for_flag(conn, business_id, flag_key):
    conn.execute("UPDATE alerts SET read_at = ? WHERE business_id = ? AND flag_key = ? AND read_at IS NULL",
                 (now(), business_id, flag_key))
    conn.commit()
