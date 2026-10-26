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

### AI models (trained on a labelled Kaggle fraud dataset)
- **Training data:** [Credit Card Transactions Fraud Detection Dataset](https://www.kaggle.com/datasets/kartik2112/fraud-detection) (Sparkov simulation), 1.30M training rows (Jan 2019 – Jun 2020) and 556k test rows (Jun – Dec 2020), about 0.5% fraud. Each card is treated as one business and its merchants as payees; amounts converted USD → PHP at 62.879. Personal columns (name, address, birth date, job, location) are dropped.
- **Fraud model:** scikit-learn `HistGradientBoostingClassifier` trained on the `is_fraud` labels. Flags "Possible fraud (AI model)" with a fraud-risk percentage and plain-language reasons.
- **Unusual-pattern model:** scikit-learn `IsolationForest` (no labels). Flags "Unusual pattern (AI model)" for payments more unusual than a chosen percentile of the business's own payments.
- **Two versions of each:** one using time of day (for uploads with times) and one for date-only uploads; the app picks automatically. Including the time of payment in uploads makes the fraud model noticeably better.
- **Features (scale-free):** amount vs. the business's usual payment, amount vs. its category, hours since the last payment to the same payee, change from that payee's last amount, payments to that payee in 24 hours, burst of payments vs. a usual day, and time of day. See [docs/CSV_FORMAT.md](docs/CSV_FORMAT.md).
- **Test results** (on the later, unseen test period; full numbers in `models/model_card.json`):

  | Model | ROC-AUC | PR-AUC | Precision | Recall |
  |---|---|---|---|---|
  | Fraud model, with time of day | 0.973 | 0.483 | 0.47 | 0.61 |
  | Fraud model, date only | 0.948 | 0.333 | 0.29 | 0.51 |
  | Isolation Forest (with time) | 0.853 | 0.031 | 0.04 | 0.32 (at 97th percentile) |
  | Baseline rule: top 1% amount per business | — | — | 0.11 | 0.26 |

- **Settings:** `fraud_min_probability` (default: the model's recommended level, chosen for best F1 on a validation split; Low sensitivity 0.5, High 0.15) and `ml_percentile` (Low 99.5, Balanced 99, High 97). Both models need at least 20 payments in a business.
- **Retrain:** `pip install -r requirements-train.txt` then `python -m treasury_guard.training` (downloads the dataset with kagglehub, ~200 MB, takes a few minutes). The app only loads `models/fraud_models.joblib`; it needs the scikit-learn version in `requirements.txt`, otherwise the AI checks are skipped.

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

Pages (plain, unstyled for now): Anomaly flags (dashboard), Transactions, Upload CSV (with column matching and a "Load sample data" button), Detection settings (sensitivity presets, rule, statistical and AI settings), AI models (training data and test results), Business profile (details and categories), Log out.

The JSON API below exposes the same features for a future frontend.

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
| `GET /api/model` | The AI models' card: training data, features, test results, and whether they loaded |
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
  training.py                trains both AI models (run once; needs requirements-train.txt)
  anomaly_model.py           loads the AI models, scores a business's payments, writes reasons
  settings.py                per-business thresholds and sensitivity presets
  detection.py               runs all checks for a business and saves the flags
  flags.py                   flag storage and review status
  labels.py                  every name shown to owners (edit wording here)
  demo_data.py               loads the Kaggle sample data
  api.py                     JSON API
templates/, static/          placeholder pages
models/                      trained model and model card
docs/                        CSV format guide and template
data/                        Kaggle sample data for the demo account
tests/                       unit and API tests
```

## Sample data

The demo account loads the [Fraud Detection Dataset](https://www.kaggle.com/datasets/goyaladi/fraud-detection-dataset) by goyaladi (three files joined on `TransactionID`). It is synthetic, with amounts spread evenly between 10 and 100 and random fraud labels, so it is only used to show the app working, not to train or evaluate the models.
