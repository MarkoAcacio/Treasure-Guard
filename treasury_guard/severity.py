"""Sprint 3: severity scoring (0-100) for flags.

1. Each flag gets a base score from how far past its threshold it is.
2. When several different checks flag the same payment, each of those flags gets
   +10 per extra check that agrees (capped at 100), because agreement makes a real
   problem more likely.
3. Score -> level: 75+ high, 50-74 medium, below 50 low.
"""
from collections import defaultdict

AGREEMENT_BONUS = 10
HIGH_FROM = 75
MEDIUM_FROM = 50


def _clamp(x):
    return max(0, min(100, round(x)))


def base_score(flag):
    d = flag["details"]
    kind = flag["type"]
    if kind == "duplicate":
        closeness_time = 1 - d["hours_apart"] / d["window_hours"]
        closeness_amount = 1 - (d["amount_diff_pct"] / d["tolerance_pct"] if d["tolerance_pct"] else 0)
        return _clamp(65 + 25 * closeness_time + 10 * closeness_amount)
    if kind == "large":
        return _clamp(50 + 100 * (flag["amount"] - d["limit"]) / d["limit"])
    if kind == "small":
        return _clamp(40 + 60 * (d["limit"] - flag["amount"]) / d["limit"]) if d["limit"] else 40
    if kind == "spike":
        return _clamp(45 + 100 * (d["ratio"] - d["multiplier"]) / d["multiplier"])
    if kind == "zscore":
        return _clamp(50 + 25 * (abs(d["z_score"]) - d["threshold"]))
    if kind == "iqr":
        return _clamp(45 + 30 * d["distance_in_iqr"])
    if kind == "fraud":
        return _clamp(45 + 55 * d["probability"])
    if kind == "ml":
        span = max(100 - d["threshold"], 0.5)
        return _clamp(50 + 40 * (d["percentile"] - d["threshold"]) / span)
    return 50


def level_for(score):
    if score >= HIGH_FROM:
        return "high"
    if score >= MEDIUM_FROM:
        return "medium"
    return "low"


def score_flags(flags):
    """Add severity_score and severity to each flag (in place) and return the flags."""
    types_per_txn = defaultdict(set)
    for f in flags:
        for txn_id in f["txn_ids"]:
            types_per_txn[txn_id].add(f["type"])
    for f in flags:
        base = base_score(f)
        agreeing = max(len(types_per_txn[t]) for t in f["txn_ids"]) - 1
        bonus = AGREEMENT_BONUS * agreeing
        f["severity_score"] = _clamp(base + bonus)
        f["severity"] = level_for(f["severity_score"])
        f["details"] = {**f["details"], "base_score": base, "agreement_bonus": bonus}
    return flags
