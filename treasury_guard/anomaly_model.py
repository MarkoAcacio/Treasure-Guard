"""Machine-learning check: an Isolation Forest trained on the Kaggle dataset.

Isolation Forest is unsupervised: it learns what typical payments look like and scores
how easy a payment is to "isolate" from the rest. It needs no fraud labels, which matters
because MSME uploads have none (and the Kaggle labels turned out to be random; see the
model card).

How a business's payments are scored:
1. Pre-process and build scale-free features (preprocessing.py).
2. The Kaggle-trained model gives each payment an anomaly score.
3. Scores are ranked within the business: a payment at the 97th percentile is more unusual
   than 97% of that business's own payments. The owner's "AI model sensitivity" setting is
   this percentile, so it controls roughly what share of payments get flagged.
4. Range check: Isolation Forest can't tell how far beyond its training data a value is
   (a payment 100x the usual looks like the largest training payment), so a feature far
   outside the training range is always treated as highly unusual.

Train:  python -m treasury_guard.anomaly_model
Files:  models/isolation_forest.joblib and models/model_card.json
If the saved model is missing or was made with a different scikit-learn version, it is
retrained automatically from data/ (takes about a second).
"""
import json
import math
import random
from bisect import bisect_left
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median, quantiles

import joblib
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score

from .preprocessing import (FEATURES, KAGGLE_COLUMNS_DROPPED, KAGGLE_COLUMNS_USED, build_features,
                            clean_transactions, load_kaggle_training_data)

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_PATH = MODEL_DIR / "isolation_forest.joblib"
CARD_PATH = MODEL_DIR / "model_card.json"
MODEL_VERSION = 3
PARAMS = {"n_estimators": 200, "max_samples": "auto", "contamination": "auto", "random_state": 42}
EVAL_PERCENTILE = 97.0
# A feature more than RANGE_MARGIN IQRs outside the training range counts as at least this unusual.
RANGE_MARGIN = 1.5
OUT_OF_RANGE_PERCENTILE = 99.5
MIN_PAYMENTS = 20  # a business needs this many payments before the model can compare them

_loaded = None  # (model, card)


# ---------- Training ----------

def _percentile_of(sorted_scores, score):
    return 100.0 * bisect_left(sorted_scores, score) / len(sorted_scores)


def _inject_anomalies(transactions, count=60, seed=7):
    """Copy the data and add clearly unusual payments, to check the model catches them."""
    rng = random.Random(seed)
    usual = median(t["amount"] for t in transactions)
    base = list(transactions)
    next_id = max(t["id"] for t in base) + 1
    injected = []
    for i in range(count):
        t = rng.choice(transactions)
        kind = i % 3
        if kind == 0:    # far larger than usual
            amount = usual * rng.uniform(6, 15)
            when = t["timestamp"] + timedelta(minutes=30)
            account = t["counterparty"]
        elif kind == 1:  # tiny test-style charge
            amount = usual * rng.uniform(0.005, 0.03)
            when = t["timestamp"] + timedelta(minutes=30)
            account = f"new-{i}"
        else:            # rapid repeat to the same account with a very different amount
            amount = t["amount"] * rng.choice([0.1, 8.0])
            when = t["timestamp"] + timedelta(minutes=rng.randint(5, 90))
            account = t["counterparty"]
        injected.append({"id": next_id, "timestamp": when, "amount": amount, "counterparty": account,
                         "merchant": t["merchant"], "category": t["category"]})
        next_id += 1
    return clean_transactions(base + injected), {t["id"] for t in injected}


def _percentile_with_range_check(pct, row, feature_stats):
    for name, value in zip(FEATURES, row):
        st = feature_stats[name]
        margin = RANGE_MARGIN * st["iqr"]
        if value < st["min"] - margin or value > st["max"] + margin:
            return max(pct, OUT_OF_RANGE_PERCENTILE)
    return pct


def train_model(save=True):
    transactions, labels = load_kaggle_training_data()
    X, ctx = build_features(transactions)
    model = IsolationForest(**PARAMS).fit(X)
    train_scores = sorted(float(s) for s in -model.score_samples(X))

    feature_stats = {}
    for i, name in enumerate(FEATURES):
        column = [row[i] for row in X]
        q1, q2, q3 = quantiles(column, n=4, method="inclusive")
        feature_stats[name] = {"median": q2, "iqr": (q3 - q1) or 1.0, "min": min(column), "max": max(column)}

    # Evaluation 1: the dataset's own fraud labels.
    scores = [float(s) for s in -model.score_samples(X)]
    y = [labels.get(t["id"]) for t in transactions]
    label_eval = None
    if labels and len(set(y)) == 2:
        cutoff = train_scores[int(len(train_scores) * EVAL_PERCENTILE / 100)]
        flagged = [lab for lab, s in zip(y, scores) if s >= cutoff]
        label_eval = {
            "roc_auc": round(roc_auc_score(y, scores), 3),
            "fraud_rate_overall": round(sum(y) / len(y), 3),
            "fraud_rate_in_flagged": round(sum(flagged) / len(flagged), 3) if flagged else None,
            "note": ("The Kaggle fraud labels were generated at random, independent of every column, "
                     "so no model can predict them (a supervised Random Forest also scored about 0.43). "
                     "An AUC near 0.5 is expected here and is not a sign the model is broken."),
        }

    # Evaluation 2: injected unusual payments.
    mixed, injected_ids = _inject_anomalies(transactions)
    Xm, _ = build_features(mixed, fill_change=ctx["fill_change"])
    mixed_raw = [float(s) for s in -model.score_samples(Xm)]
    mixed_sorted = sorted(mixed_raw)
    mixed_pct = [_percentile_with_range_check(_percentile_of(mixed_sorted, s), row, feature_stats)
                 for s, row in zip(mixed_raw, Xm)]
    is_injected = [1 if t["id"] in injected_ids else 0 for t in mixed]
    caught = sum(1 for flag, p in zip(is_injected, mixed_pct) if flag and p >= EVAL_PERCENTILE)
    injected_eval = {
        "injected": len(injected_ids),
        "caught_at_97th_percentile": caught,
        "recall": round(caught / len(injected_ids), 3),
        "roc_auc": round(roc_auc_score(is_injected, mixed_pct), 3),
        "kinds": ["6-15x the usual amount", "0.5-3% of the usual amount (test charge)",
                  "same account paid again within 90 minutes with a very different amount"],
    }

    card = {
        "model": "IsolationForest",
        "model_version": MODEL_VERSION,
        "sklearn_version": sklearn.__version__,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "parameters": PARAMS,
        "training_data": {
            "source": "https://www.kaggle.com/datasets/goyaladi/fraud-detection-dataset",
            "rows": len(transactions),
            "columns_used": KAGGLE_COLUMNS_USED,
            "files_dropped": KAGGLE_COLUMNS_DROPPED,
        },
        "features": list(FEATURES),
        "feature_stats": feature_stats,
        "fill_change": ctx["fill_change"],
        "train_scores_sorted": train_scores,
        "evaluation": {"fraud_labels": label_eval, "injected_anomalies": injected_eval},
    }
    if save:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": model, "model_version": MODEL_VERSION}, MODEL_PATH)
        CARD_PATH.write_text(json.dumps(card, indent=2))
    return model, card


def load_model():
    """Load the saved model, retraining it if it's missing or out of date."""
    global _loaded
    if _loaded:
        return _loaded
    try:
        card = json.loads(CARD_PATH.read_text())
        saved = joblib.load(MODEL_PATH)  # our own file, written by train_model()
        if (card.get("sklearn_version") != sklearn.__version__ or card.get("features") != list(FEATURES)
                or saved.get("model_version") != MODEL_VERSION):
            raise ValueError("model out of date")
        _loaded = (saved["model"], card)
    except (OSError, ValueError, KeyError, EOFError, json.JSONDecodeError):
        try:
            _loaded = train_model(save=True)
        except OSError:  # folder not writable: keep it in memory only
            _loaded = train_model(save=False)
    return _loaded


# ---------- Scoring a business's payments ----------

def score_transactions(transactions):
    """Return [(transaction, percentile, feature_row, context)] for cleaned transactions."""
    model, card = load_model()
    X, ctx = build_features(transactions, fill_change=card["fill_change"])
    if not X:
        return []
    scores = [float(s) for s in -model.score_samples(X)]
    ranked = sorted(scores)  # percentile within this business's own payments
    stats = card["feature_stats"]
    return [(t, _percentile_with_range_check(_percentile_of(ranked, s), row, stats), row, c)
            for t, s, row, c in zip(transactions, scores, X, ctx["per_row"])]


def _explain(t, row, c, card, fmt):
    """Plain-language reasons: the features that are furthest from what the model learned."""
    stats = card["feature_stats"]
    candidates = []
    for name, value in zip(FEATURES, row):
        st = stats[name]
        candidates.append((abs(value - st["median"]) / st["iqr"], name, value, value > st["median"]))
    candidates.sort(reverse=True)
    phrases = []
    for distance, name, value, above in candidates:
        if distance < 1.5 or len(phrases) == 2:
            break
        if name == "amount_vs_category":
            ratio = t["amount"] / c["category_typical"]
            phrases.append(
                f"the amount is {ratio:.1f}x your typical {t['category']} payment of {fmt(c['category_typical'])}"
                if above else
                f"the amount is only {ratio * 100:.0f}% of your typical {t['category']} payment of "
                f"{fmt(c['category_typical'])}")
        elif name == "hours_since_same_account" and not above and c["gap_hours"] is not None:
            phrases.append(f"the same account was paid only {c['gap_hours']:.1f} hours before")
        elif name == "change_from_last_payment" and above:
            phrases.append("it is very different from the last payment to this account")
        elif name == "payments_same_account_24h" and above:
            phrases.append(f"it is payment number {c['recent_count'] + 1} to this account within 24 hours")
    if not phrases:
        phrases.append("its mix of amount, account history and timing is rare")
    return " and ".join(phrases)


def find_unusual_patterns(transactions, settings, fmt):
    """Flags for payments the model rates above the business's chosen percentile."""
    if not settings.get("ml_enabled", True) or len(transactions) < MIN_PAYMENTS:
        return []
    _, card = load_model()
    threshold = settings["ml_percentile"]
    flags = []
    for t, pct, row, c in score_transactions(clean_transactions(transactions)):
        if pct < threshold:
            continue
        flags.append({
            "key": f"ml-{t['id']}",
            "type": "ml",
            "method": "machine_learning",
            "txn_ids": [t["id"]],
            "occurred_at": t["timestamp"].strftime("%Y-%m-%d %H:%M"),
            "amount": t["amount"],
            "counterparty": t["counterparty"],
            "merchant": t["merchant"],
            "category": t["category"],
            "reason": (f"The AI model rates this payment as more unusual than {int(min(pct, 99))}% of your "
                       f"payments, because {_explain(t, row, c, card, fmt)}."),
            "details": {"percentile": round(pct, 2), "threshold": threshold,
                        "features": {n: round(v, 3) for n, v in zip(FEATURES, row)}},
        })
    return flags


if __name__ == "__main__":
    _, trained = train_model(save=True)
    print(f"Trained on {trained['training_data']['rows']} Kaggle transactions -> {MODEL_PATH}")
    print(json.dumps(trained["evaluation"], indent=2))
