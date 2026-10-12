# Treasury Guard

Transaction anomaly detection for MSMEs.

## Increment 2 — Rule-based anomaly detection (Sprint 2)

The system now flags suspicious transactions using rules:

| Rule | Default setting |
|---|---|
| Duplicate charge | Same customer, amounts within 5% of each other, at most 72 hours apart |
| Large outflow | Amount at or above 97.00 |
| Small outflow | Amount at or below 12.00 |
| Category spending spike | A day's spend in a category is at least 2× that category's average daily spend for the month |

Every flag is saved in a SQLite database (`instance/treasury_guard.db`) with its rule, the transactions involved, a plain-language reason and a review status (needs review, confirmed, dismissed). Re-running detection keeps the review status of flags that still trigger and removes flags that no longer do.

Statistical detection, the hybrid ML score, alerts, reports, audit trail and users & roles are placeholders for later increments.

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. Detection runs automatically the first time.

## Tests

```bash
python -m unittest
```

## Project layout

```
app.py                       Flask routes
treasury_guard/
  data_loader.py             loads and joins the dataset CSVs
  rules.py                   the three detection rules
  storage.py                 SQLite storage for flags, settings and runs
templates/                   HTML pages
static/style.css             minimal styles
data/                        dataset CSVs
tests/test_rules.py          unit tests
```

## Dataset

[Fraud Detection Dataset](https://www.kaggle.com/datasets/goyaladi/fraud-detection-dataset) by goyaladi on Kaggle. This MVP uses three of its files, joined on `TransactionID`:

- `transaction_records.csv` — TransactionID, Amount, CustomerID
- `transaction_metadata.csv` — TransactionID, Timestamp, MerchantID
- `transaction_category_labels.csv` — TransactionID, Category

The dataset is synthetic: 1,000 transactions, one per hour from 2022-01-01 to 2022-02-11, with amounts spread evenly between 10 and 100. No exact duplicate charges exist in it, which is why the duplicate rule uses an amount tolerance. Amounts have no currency.
