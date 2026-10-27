"""Builds docs/test_transactions.csv: two months of a small Filipino café's transactions in the
upload template's format, with known problems planted so every check has something to find.

Run: python docs/make_test_csv.py
"""
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)
START = datetime(2026, 9, 1)
DAYS = 56
rows = []
ref = 10000


def add(when, amount, payee, merchant, category, description, kind="Payment"):
    global ref
    ref += 1
    rows.append([when.strftime("%Y-%m-%d %H:%M"), f"{amount:.2f}", kind, payee, merchant, category, f"OR-{ref}",
                 description])


def at(day, hour, minute=None):
    return START + timedelta(days=day, hours=hour, minutes=random.randint(0, 59) if minute is None else minute)


suppliers = [("Golden Grains Trading", "Inventory & supplies", 2500, 7000),
             ("Bayan Fresh Produce", "Inventory & supplies", 1200, 3500),
             ("Kape Barako Supply Co.", "Inventory & supplies", 3000, 6000)]

# ---------- Normal activity ----------
for day in range(DAYS):
    d = START + timedelta(days=day)
    add(at(day, 20), random.uniform(8000, 15000), "Daily sales", "POS", "Sales", "Store sales", kind="Income")
    if day % 2 == 0:
        name, cat, lo, hi = random.choice(suppliers)
        add(at(day, random.randint(8, 11)), random.uniform(lo, hi), name, name, cat, "Stock delivery")
    place = random.choice(["Jollibee", "Mang Inasal", "Chowking", "7-Eleven", "Greenwich", "Ministop"])
    add(at(day, random.randint(11, 14)), random.uniform(150, 900), place, place, "Food", "Staff lunch")
    if random.random() < 0.35:
        add(at(day, random.randint(9, 17)), random.uniform(90, 650), "Grab", "Grab", "Transport", "Delivery run")
    if random.random() < 0.3:
        add(at(day, random.randint(10, 21)), random.uniform(300, 2400), "Shopee", "Shopee", "Online", "Packaging and cups")
    if d.day == 5:
        add(at(day, 9), 25000, "Mrs. Santos (landlord)", "Bank transfer", "Rent", "Monthly rent")
    if d.day == 10:
        add(at(day, 10), random.uniform(4200, 4900), "Meralco", "Meralco", "Utilities", "Electricity")
        add(at(day, 10), random.uniform(800, 1000), "Maynilad", "Maynilad", "Utilities", "Water")
        add(at(day, 10), 1999, "PLDT", "PLDT", "Utilities", "Internet")
    if d.day in (15, 30):
        for staff in ("Juan Dela Cruz", "Maria Reyes", "Pedro Bautista"):
            add(at(day, 16), random.uniform(8500, 9500), staff, "GCash", "Payroll", "Half-month salary")

# ---------- Planted problems (what each check should catch) ----------
# 1. Double charge: same supplier, same amount, two hours apart
add(START + timedelta(days=37, hours=9, minutes=5), 6420, "Kape Barako Supply Co.", "Kape Barako Supply Co.",
    "Inventory & supplies", "Stock delivery")
add(START + timedelta(days=37, hours=11, minutes=10), 6420, "Kape Barako Supply Co.", "Kape Barako Supply Co.",
    "Inventory & supplies", "Stock delivery")
# 2. Very large payment to an unknown payee at 2 AM
add(START + timedelta(days=44, hours=2, minutes=14), 148500, "JM Equipment Sales", "Bank transfer",
    "Inventory & supplies", "Espresso machine")
# 3. Tiny card-test charges in the middle of the night
add(START + timedelta(days=48, hours=3, minutes=2), 0.50, "XYZ Online Services", "XYZ Online", "Online", "")
add(START + timedelta(days=48, hours=3, minutes=4), 1.00, "XYZ Online Services", "XYZ Online", "Online", "")
# 4. Burst: five near-identical payments to one new payee within an hour, late at night
for i in range(5):
    add(START + timedelta(days=49, hours=23, minutes=5 + i * 9), 4999 - i * 3, "QuickPay Merchant 88", "QuickPay",
        "Online", "")
# 5. Category spending jump: a day of heavy Food spending
for i in range(7):
    add(START + timedelta(days=25, hours=12, minutes=i * 7), random.uniform(1800, 2600), "Max's Restaurant",
        "Max's Restaurant", "Food", "Team lunch")
# 6. Typing mistake: electricity bill with an extra zero
add(START + timedelta(days=39, hours=10, minutes=30), 45320, "Meralco", "Meralco", "Utilities", "Electricity")

rows.sort(key=lambda r: r[0])
out = Path(__file__).with_name("test_transactions.csv")
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Date", "Amount", "Type", "Payee", "Merchant", "Category", "Reference", "Description"])
    w.writerows(rows)
print(f"Wrote {len(rows)} rows to {out}")
