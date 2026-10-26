"""Names shown to business owners. Change wording here, not in the detection code."""

FLAG_TYPE_NAMES = {
    "duplicate": "Possible double charge",
    "large": "Unusually large payment",
    "small": "Unusually small payment",
    "spike": "Category spending jump",
    "zscore": "Far from the usual amount",
    "iqr": "Outside the normal range",
    "ml": "Unusual pattern (AI model)",
    "fraud": "Possible fraud (AI model)",
}

FLAG_TYPE_DESCRIPTIONS = {
    "duplicate": "The same account was charged almost the same amount twice within a short time.",
    "large": "The payment is at or above the large-payment limit you set.",
    "small": "The payment is at or below the small-payment limit you set. Tiny charges can be card tests.",
    "spike": "A day's spending in one category is much higher than that category's daily average for the month.",
    "zscore": "The amount is much higher or lower than your usual payments in this category (Z-score method).",
    "iqr": "The amount falls outside the normal range of your payments in this category (IQR method).",
    "ml": ("An Isolation Forest model finds this payment's combination of amount, payee history and timing "
           "unusual compared with your other payments."),
    "fraud": ("A fraud model trained on over a million labelled card transactions estimates a high chance that "
              "this payment is fraud, based on its amount, timing and payee history."),
}

METHOD_NAMES = {"rule": "Rule check", "statistical": "Statistical check", "machine_learning": "AI model"}

STATUS_NAMES = {"new": "Needs review", "confirmed": "Confirmed issue", "dismissed": "Marked OK"}

SEVERITY_NAMES = {"high": "High", "medium": "Medium", "low": "Low"}

SENSITIVITY_NAMES = {
    "low": "Low (fewer flags, only the most unusual)",
    "medium": "Balanced",
    "high": "High (more flags, catches smaller changes)",
    "custom": "Custom",
}

# Navigation placeholders for features planned in later increments.
LATER_MODULES = {
    "hybrid": "Combined risk score",
    "alerts": "Alerts",
    "reports": "Reports",
    "audit": "Activity log",
    "users": "Team & roles",
}

CURRENCY_SYMBOLS = {"PHP": "₱", "USD": "$"}


def money_formatter(currency):
    symbol = CURRENCY_SYMBOLS.get(currency, "")

    def fmt(amount):
        return f"{symbol}{amount:,.2f}"

    return fmt
