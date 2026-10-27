"""Names and explanations shown to business owners. Change wording here, not in the detection code."""
from datetime import datetime

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

# What the check means, in plain words.
FLAG_TYPE_DESCRIPTIONS = {
    "duplicate": "The same payee was charged almost the same amount twice, close together. "
                 "Sometimes that's a mistake or a charge that went through twice.",
    "large": "This payment is bigger than your large-payment limit. Big payments are worth a second look "
             "because they cost the most if something is wrong.",
    "small": "This payment is smaller than your small-payment limit. Fraudsters sometimes send tiny charges "
             "to test whether a card or account works.",
    "spike": "You spent a lot more in this category on one day than you usually do in a day that month.",
    "zscore": "The amount is a long way from what you normally pay in this category. "
              "(This uses the Z-score method: how many 'standard steps' away from the average it is.)",
    "iqr": "The amount sits outside the range that most of your payments in this category fall into. "
           "(This uses the IQR method, which ignores the extreme values when working out what's normal.)",
    "ml": "An AI model (Isolation Forest) compared this payment with all your others, looking at the amount, "
          "the payee's history and the timing together. This combination stood out as rare.",
    "fraud": "An AI model trained on over a million labelled card payments looked at the amount, timing and "
             "payee history. Payments like this were often fraud in that data.",
}

# What the owner can check before deciding.
FLAG_NEXT_STEPS = {
    "duplicate": ["Look for two receipts or invoices for this amount.",
                  "Check your bank or card statement to see if both charges actually went out.",
                  "If it was charged twice by mistake, ask the payee for a refund."],
    "large": ["Match the payment to an invoice or purchase order.",
              "Make sure someone who's allowed to approve it actually did.",
              "If big payments like this are normal for you, raise the limit in Detection settings."],
    "small": ["Check whether you recognise the payee.",
              "If you don't, call your bank. Small test charges often come before a bigger fraud."],
    "spike": ["Open the payments from that day and see what drove the jump.",
              "A bulk order or a once-a-month bill is fine. Spending you can't explain isn't."],
    "zscore": ["Compare the payment with the invoice or receipt.",
               "Check that the amount wasn't typed wrong (an extra zero, for example)."],
    "iqr": ["Compare the payment with the invoice or receipt.",
            "Check that the amount wasn't typed wrong (an extra zero, for example)."],
    "ml": ["Read the reasons listed and see if they make sense for this payment.",
           "If you know why it happened (a new supplier, a rush order), mark it OK."],
    "fraud": ["Check that you recognise the payee and the payment.",
              "If you don't, contact your bank right away and ask them to block the card or account.",
              "If you do, mark it OK. That helps measure how accurate the model is."],
}

METHOD_NAMES = {"rule": "Rule check", "statistical": "Statistical check", "machine_learning": "AI model"}

STATUS_NAMES = {"new": "Needs review", "confirmed": "Confirmed issue", "dismissed": "Marked OK"}
STATUS_HELP = {
    "new": "Nobody has looked at this yet.",
    "confirmed": "Something was wrong. Keep it on record and follow up.",
    "dismissed": "It checked out. The payment was fine.",
}

SEVERITY_NAMES = {"high": "High", "medium": "Medium", "low": "Low"}
SEVERITY_HELP = {
    "high": "Look at this first. It's well past the limit, or several checks flagged it.",
    "medium": "Worth checking soon.",
    "low": "Slightly unusual. Check it when you have time.",
}

SENSITIVITY_NAMES = {
    "low": "Low (fewer flags, only the most unusual)",
    "medium": "Balanced",
    "high": "High (more flags, catches smaller changes)",
    "custom": "Custom",
}

ROLE_NAMES = {"owner": "Owner", "reviewer": "Reviewer", "viewer": "Viewer"}
ROLE_HELP = {
    "owner": "Everything, including uploads, settings and the team.",
    "reviewer": "Can see everything and review flags, but can't upload or change settings.",
    "viewer": "Can look at everything but can't change anything.",
}

CURRENCY_SYMBOLS = {"PHP": "₱", "USD": "$"}


def money_formatter(currency):
    symbol = CURRENCY_SYMBOLS.get(currency, "")

    def fmt(amount):
        return f"{symbol}{amount:,.2f}"

    return fmt


def short_date(stamp):
    """'2022-01-05 14:30' or '2022-01-05' -> 'Jan 5, 2022'."""
    try:
        d = datetime.fromisoformat(stamp[:10])
    except (TypeError, ValueError):
        return stamp or ""
    return f"{d:%b} {d.day}, {d.year}"


def flag_facts(flag, fmt):
    """The numbers behind a flag as (label, value) pairs for the flag page."""
    d = flag["details"]
    kind = flag["type"]
    facts = []
    if kind == "duplicate":
        facts = [("First charge", fmt(d["first_amount"])), ("Second charge", fmt(flag["amount"])),
                 ("Time between them", f"{d['hours_apart']:g} hours (limit {d['window_hours']:g} hours)"),
                 ("Difference in amount", f"{d['amount_diff_pct']:g}% (limit {d['tolerance_pct']:g}%)")]
    elif kind in ("large", "small"):
        facts = [("This payment", fmt(flag["amount"])),
                 ("Your limit", fmt(d["limit"]))]
    elif kind == "spike":
        facts = [("Spent that day", fmt(d["day_total"])), ("Usual daily spend that month", fmt(d["daily_average"])),
                 ("Times the usual", f"{d['ratio']:g}x (flags at {d['multiplier']:g}x)"),
                 ("Payments that day", d["payments"])]
    elif kind == "zscore":
        facts = [("This payment", fmt(flag["amount"])), ("Average", fmt(d["mean"])),
                 ("Compared with", f"{d['compared_with']} ({d['group_size']} payments)"),
                 ("Distance from average", f"{abs(d['z_score']):g} standard deviations (flags at {d['threshold']:g})")]
    elif kind == "iqr":
        facts = [("This payment", fmt(flag["amount"])),
                 ("Normal range", f"{fmt(max(d['lower_fence'], 0))} to {fmt(d['upper_fence'])}"),
                 ("Compared with", f"{d['compared_with']} ({d['group_size']} payments)")]
    elif kind == "fraud":
        facts = [("Fraud risk", f"{d['probability'] * 100:.0f}%"),
                 ("Flags at", f"{d['threshold'] * 100:.0f}% or more"),
                 ("Model used", "With time of day" if d.get("model_variant") == "with_time" else "Dates only")]
    elif kind == "ml":
        facts = [("More unusual than", f"{min(d['percentile'], 99):.0f}% of your payments"),
                 ("Your setting", f"Flag when more unusual than {d['threshold']:g}%"),
                 ("Model used", "With time of day" if d.get("model_variant") == "with_time" else "Dates only")]
    if "base_score" in d:
        bonus = d.get("agreement_bonus", 0)
        facts.append(("Severity score", f"{flag['severity_score']} of 100"
                      + (f" (includes +{bonus} because other checks flagged it too)" if bonus else "")))
    return facts


def headline(flag, fmt):
    """One plain sentence saying what was found, e.g. for alerts and the top of a flag page."""
    amount = fmt(flag["amount"])
    payee = flag.get("counterparty") or flag.get("merchant")
    if payee and payee.isdigit():
        payee = f"account {payee}"  # files often name payees by account number only
    who = f"to {payee}" if payee else f"in {flag.get('category') or 'your payments'}"
    return {
        "duplicate": f"{amount} {who} may have been paid twice",
        "large": f"{amount} {who} is bigger than your usual payments",
        "small": f"A very small payment of {amount} {who}",
        "spike": f"{flag.get('category')} spending jumped to {amount} on {short_date(flag['occurred_at'])}",
        "zscore": f"{amount} {who} is far from your usual {flag.get('category')} amount",
        "iqr": f"{amount} {who} is outside your normal {flag.get('category')} range",
        "ml": f"{amount} {who} doesn't fit your usual pattern",
        "fraud": f"{amount} {who} looks like possible fraud",
    }.get(flag["type"], f"{amount} {who} was flagged")
