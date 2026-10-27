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
    conn.execute("INSERT INTO members (business_id, user_id, role, added_at) VALUES (?, ?, 'owner', ?)",
                 (business_id, user_id, created))
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
    """The user's business, with their `role` in it (owner, reviewer or viewer)."""
    row = conn.execute(
        "SELECT b.*, m.role FROM businesses b JOIN members m ON m.business_id = b.id WHERE m.user_id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


# ---------- Team members and roles ----------

ROLES = ("owner", "reviewer", "viewer")  # most to least access


def can(role, needed):
    """True if `role` has at least the access of `needed`."""
    return role in ROLES and ROLES.index(role) <= ROLES.index(needed)


def list_members(conn, business_id):
    return [dict(r) for r in conn.execute(
        """SELECT u.id, u.email, u.full_name, m.role, m.added_at FROM members m JOIN users u ON u.id = m.user_id
           WHERE m.business_id = ? ORDER BY m.role = 'owner' DESC, u.full_name""", (business_id,))]


def add_member(conn, business_id, email, full_name, password, role):
    """Create an account for a team member. They log in with the email and password the owner gives them."""
    if role not in ("reviewer", "viewer"):
        raise AccountError("Team members can be reviewers or viewers.")
    email = _clean(email).lower()
    full_name = _clean(full_name)
    if not EMAIL_RE.match(email):
        raise AccountError("Enter a valid email address.")
    if not full_name:
        raise AccountError("Enter the person's name.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Give them a password with at least {MIN_PASSWORD_LENGTH} characters.")
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise AccountError("Someone already uses this email in Treasury Guard.")
    created = now()
    user_id = conn.execute(
        "INSERT INTO users (email, password_hash, full_name, created_at) VALUES (?, ?, ?, ?)",
        (email, generate_password_hash(password), full_name, created),
    ).lastrowid
    conn.execute("INSERT INTO members (business_id, user_id, role, added_at) VALUES (?, ?, ?, ?)",
                 (business_id, user_id, role, created))
    conn.commit()
    return user_id


def set_member_role(conn, business_id, user_id, role):
    if role not in ("reviewer", "viewer"):
        raise AccountError("Team members can be reviewers or viewers.")
    cur = conn.execute("UPDATE members SET role = ? WHERE business_id = ? AND user_id = ? AND role != 'owner'",
                       (role, business_id, user_id))
    conn.commit()
    if cur.rowcount != 1:
        raise AccountError("That person isn't on your team.")


def remove_member(conn, business_id, user_id):
    """Removes the member and their login. The owner can't be removed."""
    cur = conn.execute("DELETE FROM members WHERE business_id = ? AND user_id = ? AND role != 'owner'",
                       (business_id, user_id))
    if cur.rowcount != 1:
        raise AccountError("That person isn't on your team.")
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


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
