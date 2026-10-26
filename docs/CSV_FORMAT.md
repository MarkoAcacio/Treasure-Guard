# Transaction CSV format

Owners upload their transactions as a CSV file (Excel and Google Sheets can both "Save as / Download as CSV").
A ready-to-fill template is in [`transactions_template.csv`](transactions_template.csv), also downloadable from `GET /api/uploads/template`.

Column names don't have to match exactly. On upload, Treasury Guard suggests which column is which, and the owner can change it before importing (column mapping).

## Columns

| Column (recommended name) | Required | What to put in it | Accepted values / examples | Used by |
|---|---|---|---|---|
| **Date** | Yes | When the payment happened | `2026-10-01`, `2026-10-01 08:15`, `10/01/2026`, `Oct 1, 2026`. Add the time if you have it. | All checks |
| **Amount** | Yes | How much was paid | `2500`, `2,500.00`, `₱2,500.00`, `PHP 2500`, `(2500)`. Signs are ignored; use **Type** for income. | All checks |
| **Type** | No | Money out or money in | Money out: `Payment`, `Expense`, `Debit`, `Withdrawal`, `Out`. Money in: `Income`, `Sales`, `Deposit`, `Credit`, `In`. Without this column every row counts as a payment. | Decides which rows are checked (only payments are) |
| **Payee** | No, but strongly recommended | Who was paid, or the account that was charged | `JB Rice Trading`, `Electric Co. Account 1234` | Double charges, AI model |
| **Merchant** | No | Store or supplier name, if different from Payee | `JB Rice Trading` | Double charges (when Payee is empty) |
| **Category** | No, but strongly recommended | Spending category | Any text. Suggested: `Inventory & supplies`, `Food`, `Utilities`, `Rent`, `Payroll`, `Transport`, `Online`, `Travel`, `Retail`, `Other`. Empty → `Uncategorized`. | Category jump, Z-score, IQR, AI model |
| **Reference** | No | Receipt, invoice or transaction number | `OR-10001`, `INV-2231` | Skips rows already uploaded |
| **Description** | No | Notes | `3 sacks of rice` | Shown to the owner only |

Any other columns (for example names, addresses, balances) are **ignored and never stored**.

## Header names recognised automatically

| Field | Headers that are matched automatically (case and spaces don't matter) |
|---|---|
| Date | Date, Timestamp, DateTime, Transaction Date, Time, Posted Date, Payment Date |
| Amount | Amount, Amt, Value, Total, Transaction Amount, Debit, Price |
| Type | Type, Direction, Transaction Type, Debit/Credit, DR/CR, In/Out |
| Payee | Payee, Counterparty, Customer ID, Customer, Account, Account ID, Vendor, Paid To |
| Merchant | Merchant, Merchant ID, Merchant Name, Store, Supplier, Shop |
| Category | Category, Expense Type, Expense Category, Classification |
| Reference | Reference, Ref, Transaction ID, Txn ID, ID, Receipt No, OR No, Invoice No |
| Description | Description, Details, Memo, Note, Notes, Particulars |

## Validation

- Each row needs a valid date and a non-zero amount, and a Type value from the list above if a Type column is mapped. Bad rows are skipped and reported by line number.
- If more than half the rows are bad, nothing is imported (this usually means a column was mapped wrong).
- Rows whose Reference was already imported for the business are skipped.
- Limits: 5 MB and 50,000 rows per file.

## Pre-processing before detection

1. Only the mapped columns are kept; everything else is dropped.
2. Rows are cleaned: amounts made positive, category and payee trimmed, empty categories set to `Uncategorized`.
3. Only payments (money out) are checked for anomalies.
4. For the AI model, each payment is turned into four features that compare it with the same business's own history, so peso amounts and business size don't matter:

| Feature | Meaning |
|---|---|
| `amount_vs_category` | How far the amount is from the business's typical payment in that category (robust z-score on a log scale) |
| `hours_since_same_account` | Hours since the last payment to the same payee (up to 3 days) |
| `change_from_last_payment` | How different the amount is from the last payment to the same payee |
| `payments_same_account_24h` | How many payments went to the same payee in the previous 24 hours |

## Training data (Kaggle) — what is used

The model is trained on the [Fraud Detection Dataset](https://www.kaggle.com/datasets/goyaladi/fraud-detection-dataset) using the same pre-processing:

| File | Columns used | Mapped to |
|---|---|---|
| transaction_records.csv | TransactionID, Amount, CustomerID | Reference, Amount, Payee |
| transaction_metadata.csv | Timestamp, MerchantID | Date, Merchant |
| transaction_category_labels.csv | Category (Food, Retail, Travel, Online, Other) | Category |
| fraud_indicators.csv | FraudIndicator | **Evaluation only**, never a training input |

Dropped: customer_data.csv (personal details), account_activity.csv (balances and logins aren't in MSME records), suspicious_activity.csv (customer-level label), amount_data.csv (a second, unrelated amount), anomaly_scores.csv (a precomputed score).
