# Retail Application Support — SQL Incident Investigation

Small SQLite retail DB (customers, stores, products, orders, order_items, payments, api_log, incidents) with 5 planted production incidents.

## Run
```
python build_db.py          # creates retail_support.db (Python 3, stdlib only)
python investigate.py       # evidence + root-cause queries for all 5 incidents
python investigate.py --fix # apply fixes, validate before/after, close tickets
```

## Incidents
| ID | Symptom | Class | Root cause |
|---|---|---|---|
| 1001 | Paid, order Pending | Application | Webhook 504 timeout |
| 1002 | Charged twice | Data / Payment | Retry after timeout, no UNIQUE txn_ref |
| 1003 | NULL / orphan customer_id | Data quality | Guest checkout, no FK |
| 1004 | Total ≠ items × tax | Configuration | Old 12% tax hardcoded (config = 18%) |
| 1005 | Delivered, no payment | Process / user | Manual admin override |

Flow: Incident → Investigation → SQL evidence → Root cause → Resolution → Validation → Closure.
Full write-up example: `rca/INC-1001.md`. Try writing 1002–1005 yourself.
