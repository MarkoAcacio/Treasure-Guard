"""Runs every check for one business, stores the results and raises alerts for new flags."""
from . import flags as flag_store
from .alerts import create_for_new_flags
from .anomaly_model import run_ai_checks
from .labels import money_formatter
from .rules import run_rule_checks
from .settings import fill_limits_from_data
from .severity import score_flags
from .statistical import run_statistical_checks
from .transactions import load_outflows_for_detection


def find_anomalies(transactions, settings, currency="PHP"):
    """Pure function: run rule, statistical and AI checks, then score severity. No database access."""
    fmt = money_formatter(currency)
    found = (run_rule_checks(transactions, settings, fmt)
             + run_statistical_checks(transactions, settings, fmt)
             + run_ai_checks(transactions, settings, fmt))
    return score_flags(keep_supported_unusual_flags(found))


def keep_supported_unusual_flags(found):
    """Show an "Unusual pattern" (Isolation Forest) flag only when another check also flagged the payment.

    On its own, Isolation Forest raised far more false alarms than real problems (precision about 4%
    on the test data), so it now backs up the other checks instead of raising alarms by itself.
    """
    supported = {txn_id for f in found if f["type"] != "ml" for txn_id in f["txn_ids"]}
    return [f for f in found if f["type"] != "ml" or supported.intersection(f["txn_ids"])]


def run_detection(conn, business_id):
    business = conn.execute("SELECT currency FROM businesses WHERE id = ?", (business_id,)).fetchone()
    transactions = load_outflows_for_detection(conn, business_id)
    settings = fill_limits_from_data(conn, business_id, [t["amount"] for t in transactions])
    found = find_anomalies(transactions, settings, business["currency"] if business else "PHP")
    result = flag_store.save_detection_results(conn, business_id, found, len(transactions), settings)
    result["alerts"] = create_for_new_flags(conn, business_id, result.pop("new_flags"))
    return result
