"""User registration, login and business profiles."""
import re

from werkzeug.security import check_password_hash, generate_password_hash

from .database import now

BUSINESS_TYPES = ("Retail", "Food & beverage", "Services", "Manufacturing", "Other")
CURRENCIES = ("PHP", "USD")
DEFAULT_CATEGORIES = (
    "Inventory & supplies", "Food", "Utilities", "Rent", "Payroll",
    "Transport", "Online", "Travel", "Retail", "Other",
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


class AccountError(ValueError):
    """A problem the user can fix (shown to them as-is)."""


def _clean(value):
    return (value or "").strip()


def register_owner(conn, email, password, full_name, business_name, business_type="Other"):
    """Create a user and their business. Returns (user_id, business_id)."""
    email = _clean(email).lower()
    full_name = _clean(full_name)
    business_name = _clean(business_name)
    business_type = _clean(business_type) or "Other"

    if not EMAIL_RE.match(email):
        raise AccountError("Enter a valid email address.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Use a password with at least {MIN_PASSWORD_LENGTH} characters.")
    if not full_name:
        raise AccountError("Enter your full name.")
    if not business_name:
        raise AccountError("Enter your business name.")
    if business_type not in BUSINESS_TYPES:
        raise AccountError(f"Business type must be one of: {', '.join(BUSINESS_TYPES)}.")
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise AccountError("An account with this email already exists. Log in instead.")

    created = now()
    user_id = conn.execute(
        "INSERT INTO users (email, password_hash, full_name, created_at) VALUES (?, ?, ?, ?)",
        (email, generate_password_hash(password), full_name, created),
    ).lastrowid
    business_id = conn.execute(
        "INSERT INTO businesses (owner_user_id, name, business_type, created_at) VALUES (?, ?, ?, ?)",
        (user_id, business_name, business_type, created),
    ).lastrowid
    conn.executemany(
        "INSERT INTO categories (business_id, name) VALUES (?, ?)",
        [(business_id, name) for name in DEFAULT_CATEGORIES],
    )
    conn.commit()
    return user_id, business_id


def authenticate(conn, email, password):
    """Return the user row if the email and password match, else None."""
    row = conn.execute("SELECT * FROM users WHERE email = ?", (_clean(email).lower(),)).fetchone()
    if row and check_password_hash(row["password_hash"], password or ""):
        return row
    return None


def get_user(conn, user_id):
    row = conn.execute("SELECT id, email, full_name, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_business_for_user(conn, user_id):
    row = conn.execute(
        "SELECT * FROM businesses WHERE owner_user_id = ? ORDER BY id LIMIT 1", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def update_business(conn, business_id, changes):
    """Update the business profile. Only name, business_type, currency and address can change."""
    allowed = {}
    if "name" in changes:
        name = _clean(changes["name"])
        if not name:
            raise AccountError("Business name can't be empty.")
        allowed["name"] = name
    if "business_type" in changes:
        if changes["business_type"] not in BUSINESS_TYPES:
            raise AccountError(f"Business type must be one of: {', '.join(BUSINESS_TYPES)}.")
        allowed["business_type"] = changes["business_type"]
    if "currency" in changes:
        if changes["currency"] not in CURRENCIES:
            raise AccountError(f"Currency must be one of: {', '.join(CURRENCIES)}.")
        allowed["currency"] = changes["currency"]
    if "address" in changes:
        allowed["address"] = _clean(changes["address"])
    if allowed:
        sets = ", ".join(f"{k} = ?" for k in allowed)
        conn.execute(f"UPDATE businesses SET {sets} WHERE id = ?", (*allowed.values(), business_id))
        conn.commit()
    return dict(conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone())


def list_categories(conn, business_id):
    return [r["name"] for r in conn.execute(
        "SELECT name FROM categories WHERE business_id = ? ORDER BY name", (business_id,)
    )]


def add_category(conn, business_id, name):
    name = _clean(name)
    if not name:
        raise AccountError("Enter a category name.")
    if len(name) > 60:
        raise AccountError("Keep category names under 60 characters.")
    conn.execute("INSERT OR IGNORE INTO categories (business_id, name) VALUES (?, ?)", (business_id, name))
    conn.commit()
    return name
