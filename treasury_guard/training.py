"""Train the AI models on the Kaggle credit card fraud dataset.

    pip install -r requirements-train.txt
    python -m treasury_guard.training

Downloads kartik2112/fraud-detection with kagglehub (about 200 MB), pre-processes it with
the same code the app uses, trains two models and saves them to models/:

- Isolation Forest (unsupervised): "how unusual is this payment for this business?"
- Histogram gradient boosting classifier (supervised, learns from is_fraud):
  "how likely is this payment to be fraud?"

Each is trained twice: with time of day (for uploads that have times) and date-only
(for uploads with dates only). The dataset's own split is used for an honest test:
fraudTrain.csv (Jan 2019 - Jun 2020) for training, fraudTest.csv (Jun - Dec 2020) for testing.
"""
import json
from datetime import datetime
from pathlib import Path
from statistics import median

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

from .preprocessing import (TRAINING_COLUMNS_DROPPED, TRAINING_COLUMNS_USED, TRAINING_DATASET, USD_TO_PHP,
                            USD_TO_PHP_DATE, build_features, clean_training_row, clean_transactions,
                            feature_names)

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
BUNDLE_PATH = MODEL_DIR / "fraud_models.joblib"
CARD_PATH = MODEL_DIR / "model_card.json"
MODEL_VERSION = 4
VARIANTS = {"with_time": True, "date_only": False}
IFOREST_PARAMS = {"n_estimators": 200, "max_samples": 512, "random_state": 42}
CLASSIFIER_PARAMS = {"max_iter": 400, "learning_rate": 0.08, "max_leaf_nodes": 31, "l2_regularization": 1.0,
                     "early_stopping": True, "validation_fraction": 0.1, "random_state": 42}
IFOREST_FIT_ROWS = 300_000
BUSINESS_PERCENTILE = 97.0


def download():
    import kagglehub  # training-only dependency
    return Path(kagglehub.dataset_download(TRAINING_DATASET))


def load_split(csv_path, date_only):
    """Yield (card_transactions, labels) per card, pre-processed."""
    import pandas as pd  # training-only dependency
    cols = ["trans_date_trans_time", "cc_num", "merchant", "category", "amt", "is_fraud"]
    df = pd.read_csv(csv_path, usecols=cols)
    df["row_id"] = np.arange(len(df))
    for _, card in df.groupby("cc_num", sort=False):
        txns, labels = [], {}
        for r in card.itertuples(index=False):
            t = clean_training_row(int(r.row_id), r.trans_date_trans_time, r.amt, r.merchant, r.category)
            if date_only:
                t["timestamp"] = t["timestamp"].replace(hour=0, minute=0, second=0)
            txns.append(t)
            labels[int(r.row_id)] = int(r.is_fraud)
        yield clean_transactions(txns), labels


def featurize(csv_path, use_time, fill_change):
    X, y, card_ids = [], [], []
    for card_no, (txns, labels) in enumerate(load_split(csv_path, date_only=not use_time)):
        rows, _ = build_features(txns, use_time, fill_change)
        X.extend(rows)
        y.extend(labels[t["id"]] for t in txns)
        card_ids.extend([card_no] * len(rows))
    return X, np.array(y), np.array(card_ids)


def _fill(X, value):
    for row in X:
        if row[3] is None:
            row[3] = value
    return np.array(X, dtype=float)


def _per_business_percentiles(scores, card_ids):
    """Percentile of each score within its own card (business), like the app does."""
    pct = np.empty(len(scores))
    for card in np.unique(card_ids):
        idx = np.where(card_ids == card)[0]
        order = np.argsort(np.argsort(scores[idx], kind="stable"), kind="stable")
        pct[idx] = 100.0 * order / len(idx)
    return pct


def _prf(y, flagged):
    tp = int(((flagged == 1) & (y == 1)).sum())
    fp = int(((flagged == 1) & (y == 0)).sum())
    fn = int(((flagged == 0) & (y == 1)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3),
            "flagged": int(flagged.sum()), "true_positives": tp, "false_positives": fp, "missed_fraud": fn}


def train_variant(data_dir, use_time):
    names = feature_names(use_time)
    # First pass: compute fill value for change_from_last_payment from the training data.
    X_tr, y_tr, cards_tr = featurize(data_dir / "fraudTrain.csv", use_time, fill_change=None)
    fill_change = float(median(row[3] for row in X_tr if row[3] is not None))
    X_tr = _fill(X_tr, fill_change)
    X_te, y_te, cards_te = featurize(data_dir / "fraudTest.csv", use_time, fill_change)
    X_te = np.array(X_te, dtype=float)

    # Isolation Forest (no labels used).
    rng = np.random.default_rng(42)
    fit_idx = rng.choice(len(X_tr), size=min(IFOREST_FIT_ROWS, len(X_tr)), replace=False)
    iforest = IsolationForest(**IFOREST_PARAMS).fit(X_tr[fit_idx])
    if_scores_te = -iforest.score_samples(X_te)
    if_pct_te = _per_business_percentiles(if_scores_te, cards_te)

    # Classifier: fit on the first 85% of the training period, pick the threshold on the last 15%.
    cards = np.unique(cards_tr)  # split by card so no business is in both parts
    rng.shuffle(cards)
    val_cards = set(cards[: int(len(cards) * 0.15)].tolist())
    is_val = np.array([c in val_cards for c in cards_tr])
    clf = HistGradientBoostingClassifier(**CLASSIFIER_PARAMS).fit(X_tr[~is_val], y_tr[~is_val])
    p_val = clf.predict_proba(X_tr[is_val])[:, 1]
    prec, rec, thr = precision_recall_curve(y_tr[is_val], p_val)
    f1 = 2 * prec * rec / np.maximum(prec + rec, 1e-9)
    best = int(np.argmax(f1[:-1]))
    threshold = float(thr[best])
    p_te = clf.predict_proba(X_te)[:, 1]

    # Baseline: a simple rule, "amount at or above the business's own 99th percentile".
    amount_rank = _per_business_percentiles(X_te[:, 0], cards_te)

    stats = {}
    for i, name in enumerate(names):
        col = X_tr[:, i]
        q1, q2, q3 = np.percentile(col, [25, 50, 75])
        stats[name] = {"median": float(q2), "iqr": float(q3 - q1) or 1.0,
                       "min": float(col.min()), "max": float(col.max())}

    evaluation = {
        "test_rows": int(len(y_te)), "test_fraud": int(y_te.sum()),
        "fraud_classifier": {
            "roc_auc": round(roc_auc_score(y_te, p_te), 4),
            "pr_auc": round(average_precision_score(y_te, p_te), 4),
            "threshold": round(threshold, 4),
            "at_threshold": _prf(y_te, (p_te >= threshold).astype(int)),
        },
        "isolation_forest": {
            "roc_auc": round(roc_auc_score(y_te, if_scores_te), 4),
            "pr_auc": round(average_precision_score(y_te, if_scores_te), 4),
            f"at_{BUSINESS_PERCENTILE:g}th_percentile_per_business":
                _prf(y_te, (if_pct_te >= BUSINESS_PERCENTILE).astype(int)),
        },
        "baseline_rule_amount_top_1pct_per_business": _prf(y_te, (amount_rank >= 99).astype(int)),
        "combined_classifier_or_isolation_forest": _prf(
            y_te, ((p_te >= threshold) | (if_pct_te >= BUSINESS_PERCENTILE)).astype(int)),
    }
    model = {"features": list(names), "fill_change": fill_change, "feature_stats": stats,
             "iforest": iforest, "classifier": clf, "threshold": threshold}
    return model, evaluation, int(len(y_tr)), int(y_tr.sum())


def train_all(data_dir=None):
    data_dir = Path(data_dir) if data_dir else download()
    models, evaluations, rows = {}, {}, {}
    for variant, use_time in VARIANTS.items():
        print(f"Training {variant} ...", flush=True)
        models[variant], evaluations[variant], n, fraud = train_variant(data_dir, use_time)
        rows[variant] = {"train_rows": n, "train_fraud": fraud}
        print(json.dumps(evaluations[variant], indent=2), flush=True)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model_version": MODEL_VERSION, "sklearn_version": sklearn.__version__, "variants": models},
                BUNDLE_PATH, compress=3)
    card = {
        "model_version": MODEL_VERSION,
        "sklearn_version": sklearn.__version__,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "models": {
            "isolation_forest": {"type": "IsolationForest", "parameters": IFOREST_PARAMS,
                                 "uses_labels": False, "fit_rows": IFOREST_FIT_ROWS},
            "fraud_classifier": {"type": "HistGradientBoostingClassifier", "parameters": CLASSIFIER_PARAMS,
                                 "uses_labels": True},
        },
        "training_data": {
            "dataset": f"https://www.kaggle.com/datasets/{TRAINING_DATASET}",
            "description": "Simulated US credit card transactions (Sparkov). Each card is treated as one business.",
            "train_file": "fraudTrain.csv (2019-01-01 to 2020-06-21)",
            "test_file": "fraudTest.csv (2020-06-21 to 2020-12-31)",
            "columns_used": TRAINING_COLUMNS_USED,
            "columns_dropped": TRAINING_COLUMNS_DROPPED,
            "currency": f"USD converted to PHP at {USD_TO_PHP} ({USD_TO_PHP_DATE}); features are scale-free",
            "rows": rows,
        },
        "variants": {v: {"features": models[v]["features"], "threshold": models[v]["threshold"],
                         "feature_stats": models[v]["feature_stats"]} for v in models},
        "evaluation": evaluations,
    }
    CARD_PATH.write_text(json.dumps(card, indent=2))
    print(f"Saved {BUNDLE_PATH} and {CARD_PATH}")
    return card


if __name__ == "__main__":
    train_all()
