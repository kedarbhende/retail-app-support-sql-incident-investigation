# retail-app-support-sql-incident-investigation

A practice project for application support work. I built a synthetic retail
database with 5 planted incidents, then investigated each one with SQL:
find evidence, identify root cause, fix or escalate, validate, document.

- **Database (SQLite):** 8 tables (customers, stores, products, orders,
  order_items, payments, api_log, incidents), ~330 orders, 120 customers
- **`build_db.py`:** generates the data and plants the incidents
- **`investigate.py`:** evidence and root-cause queries; `--fix` applies
  fixes, prints before/after validation, and updates ticket status
- **`rca/`:** written RCA documents

## Incidents
| ID | Symptom | Root cause | Class | Outcome |
|----|---------|-----------|-------|---------|
| 1001 | Paid, order stuck Pending | Webhook 504 timeout | Application | Fixed + validated |
| 1002 | Customer charged twice | Gateway timeout + retry, no UNIQUE on txn_ref | Data/Payment | Fixed + validated |
| 1003 | NULL/orphan customer_id | Guest checkout path, no FK | Data quality | Escalated |
| 1004 | Total ≠ items × tax | Old 12% tax vs 18% config | Configuration | Fixed + validated |
| 1005 | Delivered, no payment | Manual admin override | Process/user | Escalated |

## SQL used
JOINs, `LEFT JOIN … IS NULL` (orphans), `GROUP BY / HAVING` (duplicates,
mismatches), `NOT EXISTS`, aggregates for recalculating totals.

## How to run
```
python build_db.py
python investigate.py
python investigate.py --fix
