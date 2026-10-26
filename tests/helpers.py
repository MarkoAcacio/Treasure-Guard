from datetime import datetime, timedelta

from treasury_guard.labels import money_formatter
from treasury_guard.settings import DEFAULT_SETTINGS

T0 = datetime(2022, 1, 1, 9, 0)
FMT = money_formatter("PHP")


def txn(id, hours, amount, account="A1", category="Food", merchant="M1"):
    return {"id": id, "reference": str(id), "timestamp": T0 + timedelta(hours=hours), "amount": amount,
            "counterparty": account, "merchant": merchant, "category": category}


def settings(**overrides):
    return {**DEFAULT_SETTINGS, "large_limit": 97.0, "small_limit": 12.0, **overrides}
