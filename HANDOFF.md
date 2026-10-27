# TreasuryGuard — Handoff

**Project:** TreasuryGuard: Automated Transaction Anomaly Detection and Reporting System (Group G.O.A.T, BCS33)
**Repo:** https://github.com/MarkoAcacio/Treasure-Guard, branch `mvp` (not pushed yet: run `git push origin mvp`)
**Local folder:** `C:\Users\Marko\Desktop\Websites\TreasureGuard`
**Last updated:** October 27, 2026

---

## 1. Where things stand

| Sprint / objective | Status | Notes |
|---|---|---|
| **1. Registration, login, business profiles, CSV upload with validation and column mapping, transaction list** | Done | Sign-up/login, profile page, categories, two-step upload with automatic column matching, transactions page |
| **2. Rule-based detection** (double charges, large/small payments, category spikes, flag storage) | Done | Per business, flags keep review status across re-runs |
| **3. Statistical detection** (Z-score, IQR), per-business thresholds and sensitivity, severity scoring | Done | Low / Balanced / High / Custom presets, severity 0–100 |
| **4. Machine learning + hybrid score** | Done | Fraud model + Isolation Forest in use; Combined risk page gives one 0–100 score per payment from rules + statistics + AI |
| **Increment 4: dashboard, alerts, plain-language explanations, review workflow** | Done | Overview with spending trend and categories; flag pages explain each flag in plain words with what to check; review queue with notes and bulk actions; in-app alerts and email digests (saved to `instance/outbox` until SMTP is set) |
| **6. Reports (PDF/CSV), multiple record types, role-based access, audit trail** | Mostly done | Reports page (print to PDF) + CSV exports, owner/reviewer/viewer roles, activity log. Multiple record types in uploads not built |
| **7. Testing with MSME owners** | Not started | Model test results on the Kaggle test split are ready for the paper (section 6) |
| **Frontend styling** | Done | One design system in `static/style.css`; every page works on phones and in dark mode |

---

## 2. Change history (`mvp` branch)

| Commit | What changed |
|---|---|
| `c2a534c` Increment 2 MVP | First Flask app: 3 rules on the goyaladi Kaggle sample data, SQLite flag storage |
| `0996c66` (Marko) | Dashboard tweak |
| `80697cd` Increments 1–3 | Accounts, business profiles, categories, CSV preview/validation/mapping/import, per-business rules, Z-score/IQR, sensitivity presets, severity scoring, JSON API, sign-up/login pages, owner-friendly labels |
| `3325807` (Marko) | `run_on_wifi.bat` to open the site from a phone on the same Wi-Fi |
| `362eace` | First Isolation Forest, trained on the goyaladi data (replaced next) |
| `040386a` | **Models retrained on the labelled credit card fraud dataset**: fraud classifier + Isolation Forest, with-time and date-only versions, model card with test results |
| `e8d1747` | Upload page (choose file → match columns → import) |
| `cd163e6` | Pages for every feature: dashboard with all checks and severity, transactions, detection settings, AI models, business profile, sample-data button |

**Uncommitted in the folder:** `models/fraud_models.joblib` and `models/model_card.json` from Marko's own retraining (results almost identical). Commit them if you want that version.

---

## 3. How to run

```bat
cd C:\Users\Marko\Desktop\Websites\TreasureGuard
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

- Open http://127.0.0.1:5000 → **Sign up** (tick "Load sample transactions" for demo data).
- **From a phone on the same Wi-Fi:** double-click `run_on_wifi.bat` and open the address it prints. It runs with debug mode off (debug mode must never be reachable from other devices). Restart it after code changes.
- **Tests:** `python -m unittest` (51 tests).
- **Retrain the AI models (optional):** `pip install -r requirements-train.txt` then `python -m treasury_guard.training`. Downloads the dataset with kagglehub (~200 MB), takes a few minutes, overwrites `models/`.
- **Requirements:** Python 3.14 venv on Marko's PC, Flask 3, **scikit-learn 1.9.1 exactly** (the saved model only loads with the same version; otherwise AI checks are skipped and the reason is logged).

---

## 4. Code map

```
app.py                       Flask app: pages, login, upload flow; registers the API
run_on_wifi.bat              start the server for phones on the same Wi-Fi
treasury_guard/
  database.py                SQLite schema (users, businesses, categories, uploads, transactions,
                             detection_settings, flags, detection_runs); auto-upgrades the old MVP DB
  accounts.py                sign-up, login (hashed passwords), business profile, categories
  csv_import.py              CSV preview, suggested column mapping, row validation, import
  transactions.py            transaction list/filters/paging, loading payments for detection
  rules.py                   Sprint 2 rule checks
  statistical.py             Sprint 3 Z-score and IQR checks
  severity.py                severity score 0-100 and High/Medium/Low
  settings.py                per-business thresholds, sensitivity presets, limits suggested from data
  preprocessing.py           keeps only needed columns, cleans rows, builds the AI features
  training.py                trains and evaluates the AI models (training only; needs pandas + kagglehub)
  anomaly_model.py           loads the AI models, scores a business's payments, writes reasons
  detection.py               runs every check for a business and saves the flags
  flags.py                   flag storage, review status, counts
  labels.py                  every name shown to owners (change wording here)
  demo_data.py               loads the goyaladi sample data into a business
  api.py                     JSON API under /api
templates/, static/          plain pages
models/                      fraud_models.joblib + model_card.json
docs/CSV_FORMAT.md           upload columns, accepted values, pre-processing, training columns
docs/transactions_template.csv
data/                        goyaladi sample data (demo account only)
tests/                       unit, model and page tests
```

**Detection flow:** upload → `csv_import` validates and stores → `detection.run_detection` loads the business's payments (money out only) → fills payment limits from the data if the owner hasn't set them → rule checks + statistical checks + AI checks → `severity.score_flags` → `flags.save_detection_results` (keeps review status of flags that still apply, removes the rest).

---

## 5. Detection checks

| Check (name owners see) | Method | Key setting (default) |
|---|---|---|
| Possible double charge | Rule | Same payee, amounts within 5%, at most 72 h apart |
| Unusually large payment | Rule | At or above the business's top 1% (auto) or an owner-set amount |
| Unusually small payment | Rule | At or below the bottom 1% (auto) or owner-set |
| Category spending jump | Rule | A day's category spend ≥ 2× its daily average for the month |
| Far from the usual amount | Statistical (Z-score) | ≥ 3 standard deviations from the category average |
| Outside the normal range | Statistical (IQR) | Beyond Q1 − 1.5·IQR / Q3 + 1.5·IQR of the category |
| Possible fraud (AI model) | Gradient boosting (supervised) | Fraud risk ≥ model's recommended level (~24–31%) |
| Unusual pattern (AI model) | Isolation Forest (unsupervised) | More unusual than 99% of the business's own payments |

- **Sensitivity presets:** Low / Balanced / High set Z-score, IQR, spike multiplier, double-charge tolerance and both AI levels together; editing any of them switches to Custom.
- **Small categories** (< 10 payments) are compared with all of the business's payments.
- **Severity:** base score from how far past the threshold a flag is, +10 for each other check that flagged the same payment; 75+ High, 50–74 Medium, < 50 Low.
- **Review status:** Needs review / Confirmed issue / Marked OK.

---

## 6. AI models (for the paper)

**Training data:** Kaggle [Credit Card Transactions Fraud Detection Dataset](https://www.kaggle.com/datasets/kartik2112/fraud-detection) (Sparkov simulation). Train: 1,296,675 rows (Jan 2019 – Jun 2020, 7,506 fraud). Test: 555,719 rows (Jun – Dec 2020, 2,145 fraud). Each card is treated as one business and merchants as payees; USD converted to PHP at 62.879 (ECB, 2026-10-14).

**Pre-processing:** only time, amount, card, merchant, category and `is_fraud` are used; names, gender, birth date, job, address, coordinates and IDs are dropped. The same code prepares uploads and training data.

**Features (scale-free, compared with the business's own history):** amount vs. usual payment, amount vs. category, hours since last payment to the same payee, change from that payee's last amount, payments to that payee in 24 h, 24-hour payment burst vs. a usual day, and time of day (sin/cos) when the file has times. Two versions of each model: **with time** and **date only**; chosen automatically per upload.

**Test results (unseen later period):**

| Model | ROC-AUC | PR-AUC | Precision | Recall |
|---|---|---|---|---|
| Fraud model, with time | 0.973 | 0.48 | 0.47–0.49 | 0.60–0.61 |
| Fraud model, date only | 0.947–0.948 | 0.33 | 0.29 | 0.50–0.51 |
| Isolation Forest (97th percentile per business) | 0.85–0.87 | 0.03 | 0.04 | 0.30–0.32 |
| Baseline rule: top 1% amount per business | — | — | 0.11 | 0.26 |

(Ranges cover the committed model and Marko's retrained one.)

**What we learned along the way:**
- The first dataset (goyaladi) has **random fraud labels**: a supervised Random Forest scored ROC-AUC 0.43 and Isolation Forest 0.50, so it can't train or evaluate fraud detection. It's now used only as demo data.
- Isolation Forest **can't extrapolate** beyond its training range, so a feature far outside it is treated as highly unusual (range check).
- A model scored against training data flagged > 50% of realistic MSME payments; ranking scores **within each business** fixed it (≈ 1% flagged on simulated normal businesses; injected fraud-like bursts caught).
- Including the **time of payment** in uploads clearly improves fraud detection (0.973 vs 0.948).

**Limitation:** the training data is consumer card spending, not MSME payments. Sprint 7 testing on real owners' uploads is what will show real-world accuracy.

---

## 7. Uploads, pages and API

**CSV columns** (full guide in `docs/CSV_FORMAT.md`, template in `docs/transactions_template.csv`): Date and Amount required; Type (payment/income), Payee, Category, Merchant, Reference, Description optional. Unmapped columns are ignored and never stored. Bad rows are reported by line; > 50% bad rows rejects the file; references already uploaded are skipped; 5 MB / 50,000 rows max.

**Pages:** Anomaly flags (dashboard), Transactions, Upload CSV, Detection settings, AI models, Business profile, Sign up, Log in, Log out, placeholders for later modules.

**JSON API** (`/api/...`, session cookie, write requests need header `X-Requested-With: TreasuryGuard`): auth (register/login/logout/me), business, categories, uploads (preview, import, history, template), demo/load, transactions, detection settings/run, flags (list/get/update status), summary, model card. Details in `README.md`.

---

## 8. Decisions and why

| Decision | Reason |
|---|---|
| Flask + SQLite, server-rendered pages | Simple to run on a laptop for an MVP; JSON API ready for a richer frontend later |
| One business per owner account | Enough for Sprints 1–3; roles/team members are Sprint 6 |
| Only payments (money out) are checked | Anomalies in the objectives are about outflows; income rows are stored but skipped |
| Payment limits taken from each business's data until the owner sets them | Fixed peso limits don't fit every business size |
| Sensitivity presets + Custom | Objective 3: owners can tune detection without understanding the math |
| Supervised fraud model **and** Isolation Forest | The labelled dataset makes a real fraud model possible; Isolation Forest (named in the paper) catches odd payments that don't look like known fraud |
| Scale-free features + per-business ranking | Lets a model trained on US card data work for PHP businesses of any size |
| With-time and date-only model versions | Many MSME records have dates only; using the midnight time would mislead the model |
| All owner-facing wording in `labels.py` | Renamed checks/statuses to plain language in one place |
| `X-Requested-With` header on API writes | Blocks cross-site requests (CSRF) without extra libraries |
| Old MVP database tables dropped on upgrade | They only held regenerated demo flags |

---

## 9. Known issues and gaps

- `mvp` branch is **not pushed** yet; retrained model files are uncommitted.
- Email alerts need SMTP settings (see README) to really send; until then they're saved in `instance/outbox`.
- Team members get a password from the owner; there's no invite email or password reset yet.
- No login rate limiting or password reset; `/logout` also accepts GET.
- The demo (goyaladi) data is synthetic: Z-score/IQR find nothing in it and AI flags are mostly quick repeat payments.
- Date formats like `03/04/2026` are read as month/day first.
- AI checks need at least 20 payments in a business and scikit-learn 1.9.1.
- For production: set `TREASURY_GUARD_SECRET`, use a real WSGI server (e.g. waitress on Windows), never run with debug on a network.

---

## 10. Suggested next steps

1. Push `mvp`; decide whether to commit the retrained model files.
2. **Sprint 4 wrap-up:** one combined risk score per transaction (rules + statistics + AI) shown on the transactions page and used to sort.
3. **Sprint 5:** spending-trend charts on the dashboard; in-app alerts for new High flags; email alerts (needs an SMTP account).
4. **Sprint 6:** PDF/CSV reports; multiple record types (e.g. sales, payables) in uploads; team members with roles; an activity log (audit trail) of uploads, setting changes and reviews.
5. **Sprint 7:** recruit MSME owners, collect their (anonymised) CSVs, compare flags with owners' own review (Confirmed issue / Marked OK) to measure precision; usability survey; test the hypothesis against manual review.
6. Frontend styling pass (a design exists on the Treasury Guard Design canvas: login/register and dashboard).

---

## 11. Related materials made in this project

- **Treasury Guard design canvas** (Claude Design artifact): Login/Register and Dashboard screen designs.
- **Treasury Guard Increment 2** (Claude artifact): an earlier in-browser prototype of the Sprint 2 rules with saved flags.
- Research notes on datasets: goyaladi (random labels, demo only) vs kartik2112 (used for training); PaySim, IBM and ULB datasets were considered and not used.
