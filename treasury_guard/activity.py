"""Activity log (audit trail): who did what, and when."""
from .database import now


def log(conn, business_id, user_id, action, detail=""):
    conn.execute("INSERT INTO activity (business_id, user_id, at, action, detail) VALUES (?, ?, ?, ?, ?)",
                 (business_id, user_id, now(), action, str(detail)[:500]))
    conn.commit()


def list_activity(conn, business_id, limit=50, offset=0):
    rows = conn.execute(
        """SELECT a.at, a.action, a.detail, COALESCE(u.full_name, 'Removed user') AS who
           FROM activity a LEFT JOIN users u ON u.id = a.user_id
           WHERE a.business_id = ? ORDER BY a.id DESC LIMIT ? OFFSET ?""", (business_id, limit, offset))
    return [dict(r) for r in rows]


def count(conn, business_id):
    return conn.execute("SELECT COUNT(*) FROM activity WHERE business_id = ?", (business_id,)).fetchone()[0]
