"""Runs every check for one business and stores the results."""
from . import flags as flag_store
from .anomaly_model import find_unusual_patterns
from .labels import money_formatter
from .rules import run_rule_checks
from .settings import fill_limits_from_data
from .severity import score_flags
from .statistical import run_statistical_checks
from .transactions import load_outflows_for_detection


def find_anomalies(transactions, settings, currency="PHP"):
    """Pure function: run rule and statistical checks, then score severity. No database access."""
    fmt = money_formatter(currency)
    found = (run_rule_checks(transactions, settings, fmt)
             + run_statistical_checks(transactions, settings, fmt)
             + find_unusual_patterns(transactions, settings, fmt))
    return score_flags(found)


def run_detection(conn, business_id):
    business = conn.execute("SELECT currency FROM businesses WHERE id = ?", (business_id,)).fetchone()
    transactions = load_outflows_for_detection(conn, business_id)
    settings = fill_limits_from_data(conn, business_id, [t["amount"] for t in transactions])
    found = find_anomalies(transactions, settings, business["currency"] if business else "PHP")
    return flag_store.save_detection_results(conn, business_id, found, len(transactions), settings)
