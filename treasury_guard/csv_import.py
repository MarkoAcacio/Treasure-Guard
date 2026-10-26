"""CSV upload: preview, column mapping, validation and import.

Flow:
  1. preview_csv(file_bytes)        -> columns, sample rows and a suggested column mapping
  2. owner confirms/edits the mapping
  3. import_csv(..., mapping)       -> validates every row and saves the good ones
"""
import csv
import io
import json
import re
from datetime import datetime

from .database import now

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
MAX_ERRORS_REPORTED = 50
REJECT_IF_INVALID_SHARE_ABOVE = 0.5  # more than half the rows bad usually means a wrong mapping

# field -> (required?, what it means to the owner)
FIELDS = {
    "date": (True, "When the payment happened"),
    "amount": (True, "How much was paid"),
    "reference": (False, "Transaction or receipt number"),
    "counterparty": (False, "Who was paid, or the account that was charged"),
    "merchant": (False, "Store or supplier name"),
    "category": (False, "Spending category"),
    "description": (False, "Notes or description"),
    "direction": (False, "Whether money went out (payment) or came in (income)"),
}

# Header names we recognise for each field (lowercase, spaces/underscores removed).
SYNONYMS = {
    "date": ["date", "timestamp", "datetime", "transactiondate", "time", "posteddate", "paymentdate"],
    "amount": ["amount", "amt", "value", "total", "transactionamount", "debit", "price"],
    "reference": ["reference", "ref", "transactionid", "txnid", "id", "receiptno", "orno", "invoiceno"],
    "counterparty": ["counterparty", "payee", "customerid", "customer", "account", "accountid", "vendor", "paidto"],
    "merchant": ["merchant", "merchantid", "merchantname", "store", "supplier", "shop"],
    "category": ["category", "type", "expensetype", "expensecategory", "classification"],
    "description": ["description", "details", "memo", "note", "notes", "particulars"],
    "direction": ["direction", "transactiontype", "debitcredit", "drcr", "inout"],
}

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%b %d, %Y", "%d %b %Y", "%B %d, %Y",
)
OUT_WORDS = {"out", "debit", "dr", "expense", "payment", "withdrawal", "outflow", "paid"}
IN_WORDS = {"in", "credit", "cr", "income", "deposit", "inflow", "received", "sale", "sales"}


class CsvImportError(ValueError):
    """A problem with the file or the mapping that the owner needs to fix."""


def _norm(header):
    return re.sub(r"[\s_\-./]", "", header.strip().lower())


def _read_rows(file_bytes):
    if not file_bytes:
        raise CsvImportError("The file is empty.")
    if len(file_bytes) > MAX_FILE_BYTES:
        raise CsvImportError(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB. Split it into smaller files.")
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise CsvImportError("The file has no rows.")
    header = [h.strip() for h in rows[0]]
    if len(set(header)) != len(header) or any(not h for h in header):
        raise CsvImportError("Every column needs a unique, non-empty header in the first row.")
    body = rows[1:]
    if len(body) > MAX_ROWS:
        raise CsvImportError(f"The file has more than {MAX_ROWS:,} rows. Split it into smaller files.")
    return header, body


def suggest_mapping(columns):
    """Guess which CSV column holds each field, based on the header names."""
    normalized = {_norm(c): c for c in columns}
    mapping = {}
    used = set()
    for field, names in SYNONYMS.items():
        for name in names:
            col = normalized.get(name)
            if col and col not in used:
                mapping[field] = col
                used.add(col)
                break
    return mapping


def preview_csv(file_bytes, sample_size=5):
    header, body = _read_rows(file_bytes)
    return {
        "columns": header,
        "row_count": len(body),
        "sample_rows": [dict(zip(header, r)) for r in body[:sample_size]],
        "suggested_mapping": suggest_mapping(header),
        "fields": {f: {"required": req, "description": desc} for f, (req, desc) in FIELDS.items()},
    }


def check_mapping(mapping, columns):
    """Make sure required fields are mapped to real columns. Returns a cleaned mapping."""
    if not isinstance(mapping, dict):
        raise CsvImportError("Column mapping must be an object like {\"date\": \"Date\", \"amount\": \"Amount\"}.")
    cleaned = {}
    for field, col in mapping.items():
        if field not in FIELDS:
            raise CsvImportError(f"Unknown field '{field}'. Use: {', '.join(FIELDS)}.")
        if col in (None, ""):
            continue
        if col not in columns:
            raise CsvImportError(f"Column '{col}' (for {field}) isn't in the file.")
        cleaned[field] = col
    missing = [f for f, (req, _) in FIELDS.items() if req and f not in cleaned]
    if missing:
        raise CsvImportError(f"Choose which column holds: {', '.join(missing)}.")
    return cleaned


def parse_date(value):
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_amount(value):
    """Return a float, or None if it isn't a number. Handles ₱, $, commas and (negatives)."""
    text = (value or "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"(?i)php|usd|[₱$,\s()]", "", text)
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def parse_direction(value):
    word = (value or "").strip().lower()
    if word in OUT_WORDS:
        return "out"
    if word in IN_WORDS:
        return "in"
    return None


def parse_rows(header, body, mapping):
    """Validate rows. Returns (transactions, errors)."""
    index = {field: header.index(col) for field, col in mapping.items()}
    transactions, errors = [], []

    def cell(row, field):
        i = index.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    for line_no, row in enumerate(body, start=2):  # line 1 is the header
        problems = []
        when = parse_date(cell(row, "date"))
        if when is None:
            problems.append(f"date '{cell(row, 'date')}' isn't a recognised date")
        amount = parse_amount(cell(row, "amount"))
        if amount is None:
            problems.append(f"amount '{cell(row, 'amount')}' isn't a number")
        elif amount == 0:
            problems.append("amount is zero")
        direction = "out"
        if "direction" in index:
            direction = parse_direction(cell(row, "direction"))
            if direction is None:
                problems.append(f"type '{cell(row, 'direction')}' should be payment/expense or income/deposit")
        if problems:
            errors.append({"line": line_no, "problems": problems})
            continue
        transactions.append({
            "reference": cell(row, "reference") or None,
            "occurred_at": when.strftime("%Y-%m-%d %H:%M:%S"),
            "amount": round(abs(amount), 2),
            "direction": direction,
            "counterparty": cell(row, "counterparty") or None,
            "merchant": cell(row, "merchant") or None,
            "category": cell(row, "category") or "Uncategorized",
            "description": cell(row, "description") or None,
        })
    return transactions, errors


def import_csv(conn, business_id, filename, file_bytes, mapping):
    """Validate and save a CSV for one business. Returns a summary dict."""
    header, body = _read_rows(file_bytes)
    mapping = check_mapping(mapping, header)
    transactions, errors = parse_rows(header, body, mapping)

    if not transactions:
        raise CsvImportError("No valid rows found. Check the column mapping and the file.", )
    if body and len(errors) / len(body) > REJECT_IF_INVALID_SHARE_ABOVE:
        raise CsvImportError(
            f"{len(errors)} of {len(body)} rows have problems, so nothing was imported. "
            "This usually means a column is mapped wrong. First problem: line "
            f"{errors[0]['line']}: {'; '.join(errors[0]['problems'])}."
        )

    # Skip rows whose reference number was already imported for this business.
    existing = {r[0] for r in conn.execute(
        "SELECT reference FROM transactions WHERE business_id = ? AND reference IS NOT NULL", (business_id,)
    )}
    already = [t for t in transactions if t["reference"] and t["reference"] in existing]
    new = [t for t in transactions if not (t["reference"] and t["reference"] in existing)]

    upload_id = conn.execute(
        """INSERT INTO uploads (business_id, filename, uploaded_at, rows_total, rows_imported, rows_skipped,
                                column_mapping) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (business_id, filename, now(), len(body), len(new), len(errors) + len(already), json.dumps(mapping)),
    ).lastrowid
    conn.executemany(
        """INSERT INTO transactions (business_id, upload_id, reference, occurred_at, amount, direction,
                                     counterparty, merchant, category, description)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(business_id, upload_id, t["reference"], t["occurred_at"], t["amount"], t["direction"],
          t["counterparty"], t["merchant"], t["category"], t["description"]) for t in new],
    )
    # Add any new categories found in the file to the business's category list.
    categories = sorted({t["category"] for t in new})
    conn.executemany(
        "INSERT OR IGNORE INTO categories (business_id, name) VALUES (?, ?)",
        [(business_id, c) for c in categories],
    )
    conn.commit()
    return {
        "upload_id": upload_id,
        "filename": filename,
        "rows_total": len(body),
        "rows_imported": len(new),
        "rows_skipped_invalid": len(errors),
        "rows_skipped_already_imported": len(already),
        "errors": errors[:MAX_ERRORS_REPORTED],
        "column_mapping": mapping,
    }


def list_uploads(conn, business_id):
    rows = conn.execute(
        "SELECT * FROM uploads WHERE business_id = ? ORDER BY id DESC", (business_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["column_mapping"] = json.loads(d["column_mapping"])
        out.append(d)
    return out
