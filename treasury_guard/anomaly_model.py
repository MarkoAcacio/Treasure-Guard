"""AI checks: scoring a business's payments with the trained models.

Two models, trained on the Kaggle credit card fraud dataset (see training.py and
models/model_card.json):

- Fraud model (gradient boosting, learned from labelled fraud): estimates how likely a
  payment is to be fraud. Flags payments at or above the business's probability setting.
- Isolation Forest (unsupervised): how unusual a payment is. Scores are ranked within the
  business, so the "AI sensitivity" percentile controls roughly what share gets flagged.
  Isolation Forest can't tell how far beyond its training data a value is, so a feature far
  outside the training range is always treated as highly unusual.

The app only loads the saved models; it never trains. If the model file is missing or was
made with another scikit-learn version, these checks are skipped and the reason is logged.
"""
import json
import logging
from bisect import bisect_left

import joblib
import sklearn

from .preprocessing import build_features, clean_transactions, has_times
from .training import BUNDLE_PATH, CARD_PATH, MODEL_VERSION

log = logging.getLogger(__name__)

MIN_PAYMENTS = 20           # a business needs this many payments before the models compare them
RANGE_MARGIN = 1.5          # IQRs beyond the training range that count as "far outside"
OUT_OF_RANGE_PERCENTILE = 99.5
NIGHT_HOURS = {22, 23, 0, 1, 2, 3, 4}

_bundle = None


class ModelUnavailable(RuntimeError):
    pass


def load_models():
    """Load the trained models once. Raises ModelUnavailable with a fix-it message."""
    global _bundle
    if _bundle is not None:
        return _bundle
    try:
        bundle = joblib.load(BUNDLE_PATH)  # our own file, written by training.py
    except (OSError, EOFError, ValueError) as e:
        raise ModelUnavailable(f"No trained model at {BUNDLE_PATH}. Run: python -m treasury_guard.training") from e
    if bundle.get("model_version") != MODEL_VERSION or bundle.get("sklearn_version") != sklearn.__version__:
        raise ModelUnavailable(
            f"The saved model was made with scikit-learn {bundle.get('sklearn_version')}, but "
            f"{sklearn.__version__} is installed. Install the version in requirements.txt or retrain.")
    _bundle = bundle
    return _bundle


def model_card():
    return json.loads(CARD_PATH.read_text())


def _percentile_of(sorted_scores, score):
    return 100.0 * bisect_left(sorted_scores, score) / len(sorted_scores)


def _far_outside_training(row, model):
    for name, value in zip(model["features"], row):
        st = model["feature_stats"][name]
        margin = RANGE_MARGIN * st["iqr"]
        if value < st["min"] - margin or value > st["max"] + margin:
            return True
    return False


def score_transactions(transactions):
    """Return (variant_name, model, [(txn, fraud_probability, unusual_percentile, row, context)])."""
    bundle = load_models()
    txns = clean_transactions(transactions)
    variant = "with_time" if has_times(txns) else "date_only"
    model = bundle["variants"][variant]
    rows, context = build_features(txns, use_time=variant == "with_time", fill_change=model["fill_change"])
    if not rows:
        return variant, model, []
    probabilities = model["classifier"].predict_proba(rows)[:, 1]
    unusual = [float(s) for s in -model["iforest"].score_samples(rows)]
    ranked = sorted(unusual)
    results = []
    for t, p, s, row, c in zip(txns, probabilities, unusual, rows, context):
        pct = _percentile_of(ranked, s)
        if _far_outside_training(row, model):
            pct = max(pct, OUT_OF_RANGE_PERCENTILE)
        results.append((t, float(p), pct, row, c))
    return variant, model, results


def explain(t, row, c, model, fmt):
    """Plain-language reasons: the features furthest from typical training payments."""
    stats = model["feature_stats"]
    ranked = sorted(
        ((abs(v - stats[n]["median"]) / stats[n]["iqr"], n, v, v > stats[n]["median"])
         for n, v in zip(model["features"], row)),
        reverse=True,
    )
    phrases = []
    hour = t["timestamp"].hour
    if "hour_sin" in model["features"] and hour in NIGHT_HOURS:
        phrases.append(f"it was made at {t['timestamp'].strftime('%I:%M %p').lstrip('0')}, late at night")
    amount_said = False
    for distance, name, value, above in ranked:
        if distance < 1.5 or len(phrases) >= 3:
            break
        if name in ("amount_vs_usual", "amount_vs_category"):
            if amount_said:
                continue  # one sentence about the amount is enough
            amount_said = True
        if name == "amount_vs_usual" and above:
            phrases.append(f"the amount is {t['amount'] / c['usual_amount']:.1f}x your usual payment "
                           f"of {fmt(c['usual_amount'])}")
        elif name == "amount_vs_category":
            ratio = t["amount"] / c["category_typical"]
            phrases.append(f"it is {ratio:.1f}x your typical {t['category']} payment of {fmt(c['category_typical'])}"
                           if above else
                           f"it is only {ratio * 100:.0f}% of your typical {t['category']} payment")
        elif name == "hours_since_same_payee" and not above and c["gap_hours"] is not None:
            phrases.append(f"the same payee was paid only {c['gap_hours']:.1f} hours before")
        elif name == "change_from_last_payment" and above:
            phrases.append("it is very different from the last payment to this payee")
        elif name == "payments_same_payee_24h" and above:
            phrases.append(f"it is payment number {c['same_payee_24h'] + 1} to this payee within 24 hours")
        elif name == "burst_vs_usual_day" and above:
            phrases.append(f"it is one of {c['business_24h'] + 1} payments in 24 hours "
                           f"(you usually make about {c['usual_per_day']:.0f} a day)")
    if not phrases:
        phrases.append("its mix of amount, payee history and timing is rare")
    return " and ".join(dict.fromkeys(phrases))  # keep order, drop repeats


def _flag(key, kind, t, reason, details):
    return {
        "key": key, "type": kind, "method": "machine_learning", "txn_ids": [t["id"]],
        "occurred_at": t["timestamp"].strftime("%Y-%m-%d %H:%M"), "amount": t["amount"],
        "counterparty": t["counterparty"], "merchant": t["merchant"], "category": t["category"],
        "reason": reason, "details": details,
    }


def run_ai_checks(transactions, settings, fmt):
    """Flags from both models. Skipped (empty) if disabled, too few payments, or no model."""
    wants_fraud = settings.get("fraud_model_enabled", True)
    wants_unusual = settings.get("ml_enabled", True)
    if not (wants_fraud or wants_unusual) or len(transactions) < MIN_PAYMENTS:
        return []
    try:
        variant, model, results = score_transactions(transactions)
    except ModelUnavailable as e:
        log.warning("AI checks skipped: %s", e)
        return []

    min_probability = settings.get("fraud_min_probability") or model["threshold"]
    flags = []
    for t, prob, pct, row, c in results:
        why = None
        if wants_fraud and prob >= min_probability:
            why = explain(t, row, c, model, fmt)
            flags.append(_flag(
                f"fraud-{t['id']}", "fraud", t,
                f"The fraud model gives this payment a {prob * 100:.0f}% fraud risk, because {why}.",
                {"probability": round(prob, 4), "threshold": round(min_probability, 4), "model_variant": variant,
                 "features": {n: round(v, 3) for n, v in zip(model["features"], row)}},
            ))
        if wants_unusual and pct >= settings["ml_percentile"]:
            why = why or explain(t, row, c, model, fmt)
            flags.append(_flag(
                f"ml-{t['id']}", "ml", t,
                (f"The AI model rates this payment as more unusual than {int(min(pct, 99))}% of your payments, "
                 f"because {why}."),
                {"percentile": round(pct, 2), "threshold": settings["ml_percentile"], "model_variant": variant,
                 "features": {n: round(v, 3) for n, v in zip(model["features"], row)}},
            ))
    return flags
