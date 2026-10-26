# Treasury Guard

Transaction anomaly detection for Philippine MSMEs.

## What's built

### Increment 1 — Core system and data ingestion
- Owner sign-up and login (passwords are hashed), one business profile per owner (name, type, currency, address)
- Spending categories per business (defaults on sign-up, plus any new categories found in uploads)
- CSV upload in two steps: **preview** (columns, sample rows, suggested column mapping) then **import** with the owner's mapping
- Validation: required date and amount, many date formats, amounts like `₱1,200.00`, `PHP 99`, `(350.50)`; bad rows are reported by line number; the whole file is rejected if more than half the rows are bad (usually a wrong mapping); rows whose reference number was already imported are skipped
- Transaction list with filters and paging

### Increment 2 — Rule-based detection
| Check (name shown to owners) | How it works |
|---|---|
| Possible double charge | Same account (or merchant), amounts within X%, at most N hours apart |
| Unusually large payment | Amount at or above the large-payment limit |
| Unusually small payment | Amount at or below the small-payment limit |
| Category spending jump | A day's spend in a category ≥ N× that category's average daily spend for the month |

Flags are stored per business with a plain-language reason and a review status (Needs review, Confirmed issue, Marked OK). Re-running keeps the status of flags that still trigger.

### Increment 3 — Statistical detection, thresholds and severity
| Check | How it works |
|---|---|
| Far from the usual amount (Z-score) | Flags a payment whose amount is ≥ threshold standard deviations from the average for its category |
| Outside the normal range (IQR) | Flags a payment beyond Q1 − k·IQR or Q3 + k·IQR for its category |

- Categories with fewer than `min_category_samples` payments are compared with all of the business's payments.
- **Per-business settings** with **sensitivity presets**:

  | Sensitivity | Z-score | IQR k | Category jump | Double-charge tolerance |
  |---|---|---|---|---|
  | Low | 3.5 | 3.0 | 2.5× | 2% |
  | Medium (default) | 3.0 | 1.5 | 2.0× | 5% |
  | High | 2.5 | 1.0 | 1.5× | 8% |

  Changing any of these by hand switches to "custom". Large/small payment limits are set from the business's own data (top and bottom 1% of payments) until the owner sets them.
- **Severity score (0–100)** for every flag: a base score from how far past its threshold it is, plus 10 for each other check that flagged the same payment. 75+ is High, 50–74 Medium, below 50 Low.

### AI model (Isolation Forest, trained on the Kaggle dataset)
- **Model:** scikit-learn `IsolationForest` (200 trees), trained on the Kaggle data with `python -m treasury_guard.anomaly_model`. Saved to `models/isolation_forest.joblib`, with `models/model_card.json` describing the training data, features and evaluation. If the file is missing or made with another scikit-learn version, it retrains itself (about a second).
- **Why unsupervised:** the Kaggle fraud labels are random (a supervised Random Forest scored ROC-AUC 0.43, worse than guessing), and owners' uploads have no labels. Isolation Forest learns what normal payments look like instead.
- **Pre-processing:** only the needed columns are used (see [docs/CSV_FORMAT.md](docs/CSV_FORMAT.md)); each payment becomes four features compared with the business's own history, so the model works for any currency or business size.
- **Flags:** "Unusual pattern (AI model)" for payments more unusual than the business's chosen percentile of its own payments (Low 99th, Balanced 97th, High 95th), with a plain-language reason. Needs at least 20 payments.
- **Evaluation (in the model card):** on the Kaggle fraud labels, ROC-AUC ≈ 0.50 (expected, since the labels are random); on 60 injected unusual payments (very large, tiny test charges, quick repeats with a different amount), ROC-AUC ≈ 1.00 and 90% caught at the 97th percentile.

## Upload format

See [docs/CSV_FORMAT.md](docs/CSV_FORMAT.md) for the columns, accepted values and pre-processing, and [docs/transactions_template.csv](docs/transactions_template.csv) for a template.

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000, create an account at `/signup` (tick "Load sample transactions" for demo data), or log out at `/logout`.

The HTML pages are placeholders. The real frontend should use the JSON API below.

## Tests

```bash
python -m unittest
```

## JSON API

All routes are under `/api` and use a session cookie (log in or register first).
**Every POST, PUT and PATCH must send the header `X-Requested-With: TreasuryGuard`** (blocks cross-site requests).
Errors return `{"error": "message"}` with a 4xx status.

| Method & route | What it does |
|---|---|
| `POST /api/auth/register` | `{email, password, full_name, business_name, business_type}` → creates owner + business, logs in |
| `POST /api/auth/login` | `{email, password}` |
| `POST /api/auth/logout` | |
| `GET /api/auth/me` | Current user and business |
| `GET /api/business` / `PUT /api/business` | Business profile (`name`, `business_type`, `currency` PHP/USD, `address`) |
| `GET /api/categories` / `POST /api/categories` | List / add a category `{name}` |
| `POST /api/uploads/preview` | Form field `file` (CSV) → columns, sample rows, `suggested_mapping`, field descriptions |
| `POST /api/uploads` | Form fields `file` and `mapping` (JSON, e.g. `{"date":"Date","amount":"Amount"}`) → import summary + detection results |
| `GET /api/uploads` | Upload history |
| `GET /api/uploads/template` | Download the CSV template |
| `GET /api/model` | The AI model's card: training data, features, evaluation |
| `POST /api/demo/load` | Import the Kaggle sample data into this business |
| `GET /api/transactions` | Query: `category`, `from`, `to` (YYYY-MM-DD), `direction` (out/in), `flagged=1`, `page`, `per_page` (max 200). Each row includes its highest flag severity |
| `GET /api/detection/settings` | Current settings, presets, suggested payment limits |
| `PUT /api/detection/settings` | Change settings, e.g. `{"sensitivity": "high"}` or `{"zscore_threshold": 2.8}` or `{"reset": true}`; re-runs detection |
| `POST /api/detection/run` | Re-run detection |
| `GET /api/flags` | Query: `type`, `status`, `severity`, `min_score`, `sort` (severity/date). Most severe first by default |
| `GET /api/flags/<flag_key>` | One flag |
| `PATCH /api/flags/<flag_key>` | `{status: new|confirmed|dismissed}` |
| `GET /api/summary` | Counts by check, severity, status and method; last run |

CSV fields for mapping: `date` and `amount` (required); `reference`, `counterparty` (who was paid / account charged), `merchant`, `category`, `description`, `direction` (payment/expense vs income/deposit) are optional. Without a `direction` column every row is treated as a payment.

## Project layout

```
app.py                       Flask app: pages + registers the API
treasury_guard/
  database.py                SQLite schema and connection
  accounts.py                sign-up, login, business profile, categories
  csv_import.py              CSV preview, column mapping, validation, import
  transactions.py            transaction list and loading for detection
  rules.py                   Increment 2 rule checks
  statistical.py             Increment 3 Z-score and IQR checks
  severity.py                Increment 3 severity scoring
  preprocessing.py           keeps only needed columns, cleans rows, builds model features
  anomaly_model.py           Isolation Forest: training, model card, scoring and reasons
  settings.py                per-business thresholds and sensitivity presets
  detection.py               runs all checks for a business and saves the flags
  flags.py                   flag storage and review status
  labels.py                  every name shown to owners (edit wording here)
  demo_data.py               loads the Kaggle sample data
  api.py                     JSON API
templates/, static/          placeholder pages
models/                      trained model and model card
docs/                        CSV format guide and template
data/                        Kaggle data (sample data and model training)
tests/                       unit and API tests
```

## Sample data

[Fraud Detection Dataset](https://www.kaggle.com/datasets/goyaladi/fraud-detection-dataset) by goyaladi on Kaggle (three files joined on `TransactionID`). It is synthetic: 1,000 transactions, amounts spread evenly between 10 and 100. Because the amounts are so evenly spread, the Z-score and IQR checks find nothing in it (they are tested with other data in `tests/test_statistical.py`), and the AI model flags about 3% of it, mostly quick repeat payments to the same account. Its `fraud_indicators.csv` labels are used only to evaluate the model.
