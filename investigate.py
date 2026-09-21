"""Run the investigation queries. Usage: python investigate.py [--fix]"""
import sqlite3, sys, os
DB = os.path.join(os.path.dirname(__file__), "retail_support.db")
con = sqlite3.connect(DB); cur = con.cursor()

def show(title, sql):
    cur.execute(sql); rows = cur.fetchall(); cols = [d[0] for d in cur.description]
    print(f"\n--- {title} ({len(rows)} rows)")
    if rows:
        w = [max(len(str(x)) for x in [c] + [r[i] for r in rows[:12]]) for i, c in enumerate(cols)]
        print("  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
        for r in rows[:12]: print("  ".join(str(v).ljust(w[i]) for i, v in enumerate(r)))
        if len(rows) > 12: print(f"... +{len(rows)-12} more")

Q = {}
Q["1001"] = {
 "EVIDENCE: SUCCESS payment but order Pending": """
   SELECT o.order_id, o.status, p.payment_id, p.status pay_status, p.amount, p.paid_at
   FROM orders o JOIN payments p ON p.order_id=o.order_id
   WHERE o.status='Pending' AND p.status='SUCCESS' ORDER BY o.order_id""",
 "ROOT CAUSE: webhook errors for those orders": """
   SELECT a.endpoint, a.status_code, a.error, COUNT(*) hits, MIN(a.ts) first_seen, MAX(a.ts) last_seen
   FROM api_log a WHERE a.status_code>=500 AND a.endpoint='/webhook/payment-confirm'
   GROUP BY 1,2,3""",
 "VALIDATE: rule-out (abandoned Pending orders have NO success payment)": """
   SELECT COUNT(*) legit_pending_no_payment FROM orders o
   WHERE o.status='Pending' AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.order_id=o.order_id AND p.status='SUCCESS')""",
}
Q["1002"] = {
 "EVIDENCE: orders with >1 SUCCESS payment": """
   SELECT order_id, txn_ref, COUNT(*) times_charged, SUM(amount) total_charged, MIN(paid_at) first_paid, MAX(paid_at) last_paid
   FROM payments WHERE status='SUCCESS' GROUP BY order_id, txn_ref HAVING COUNT(*)>1""",
 "ROOT CAUSE: gateway timeout then client retry": """
   SELECT order_id, ts, status_code, error FROM api_log
   WHERE endpoint='/api/payments' AND (status_code>=500 OR error='client retry') ORDER BY order_id, ts LIMIT 8""",
}
Q["1003"] = {
 "EVIDENCE: NULL customer_id": "SELECT order_id, order_date, status FROM orders WHERE customer_id IS NULL",
 "EVIDENCE: orphan customer_id (LEFT JOIN ... IS NULL)": """
   SELECT o.order_id, o.customer_id, o.order_date FROM orders o
   LEFT JOIN customers c ON c.customer_id=o.customer_id
   WHERE o.customer_id IS NOT NULL AND c.customer_id IS NULL""",
}
Q["1004"] = {
 "EVIDENCE: total_amount != items x store tax (JOIN + aggregate)": """
   SELECT o.order_id, s.name store, o.total_amount billed,
          ROUND(SUM(i.qty*i.unit_price)*(1+s.tax_rate),2) expected,
          ROUND(o.total_amount - SUM(i.qty*i.unit_price)*(1+s.tax_rate),2) diff
   FROM orders o JOIN order_items i ON i.order_id=o.order_id JOIN stores s ON s.store_id=o.store_id
   GROUP BY o.order_id HAVING ABS(diff)>0.01""",
 "ROOT CAUSE: implied tax rate per store": """
   SELECT s.name, ROUND(o.total_amount/SUM(i.qty*i.unit_price)-1,2) implied_tax, s.tax_rate configured_tax, COUNT(DISTINCT o.order_id) orders
   FROM orders o JOIN order_items i USING(order_id) JOIN stores s USING(store_id)
   GROUP BY o.order_id HAVING ABS(implied_tax-configured_tax)>0.001 LIMIT 3""",
}
Q["1005"] = {
 "EVIDENCE: Delivered with no SUCCESS payment": """
   SELECT o.order_id, o.status, o.total_amount FROM orders o
   WHERE o.status='Delivered' AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.order_id=o.order_id AND p.status='SUCCESS')""",
 "ROOT CAUSE: manual admin override in API log": """
   SELECT order_id, ts, endpoint, error FROM api_log WHERE endpoint='/admin/orders/status' ORDER BY ts""",
}

FIX = {
 "1001": ("UPDATE orders SET status='Confirmed' WHERE status='Pending' AND EXISTS (SELECT 1 FROM payments p WHERE p.order_id=orders.order_id AND p.status='SUCCESS')",
          "SELECT COUNT(*) FROM orders o JOIN payments p USING(order_id) WHERE o.status='Pending' AND p.status='SUCCESS'"),
 "1002": ("UPDATE payments SET status='REFUND_INITIATED' WHERE payment_id IN (SELECT MAX(payment_id) FROM payments WHERE status='SUCCESS' GROUP BY order_id, txn_ref HAVING COUNT(*)>1)",
          "SELECT COUNT(*) FROM (SELECT 1 FROM payments WHERE status='SUCCESS' GROUP BY order_id, txn_ref HAVING COUNT(*)>1)"),
 "1004": ("UPDATE orders SET total_amount=(SELECT ROUND(SUM(i.qty*i.unit_price)*(1+s.tax_rate),2) FROM order_items i, stores s WHERE i.order_id=orders.order_id AND s.store_id=orders.store_id) WHERE order_id IN (SELECT o.order_id FROM orders o JOIN order_items i USING(order_id) JOIN stores s USING(store_id) GROUP BY o.order_id HAVING ABS(o.total_amount-SUM(i.qty*i.unit_price)*(1+s.tax_rate))>0.01)",
          "SELECT COUNT(*) FROM (SELECT o.order_id FROM orders o JOIN order_items i USING(order_id) JOIN stores s USING(store_id) GROUP BY o.order_id HAVING ABS(o.total_amount-SUM(i.qty*i.unit_price)*(1+s.tax_rate))>0.01)"),
}
CLOSE = {
 "1001": ("Payment webhook to order-service timed out (504) during 02:10-02:40; order status never updated.", "Backfilled status to Confirmed; ask dev to add webhook retry + reconciliation job."),
 "1002": ("Gateway timed out after charging; client retried. payments.txn_ref has no UNIQUE constraint / idempotency key.", "Duplicate charge marked REFUND_INITIATED; add UNIQUE(txn_ref) + idempotency key."),
 "1004": ("Store 3 checkout still using old 12% tax rate; stores.tax_rate is 18%.", "Recomputed totals; fix hardcoded tax config in app release."),
}

if "--fix" not in sys.argv:
    for inc, qs in Q.items():
        print(f"\n{'='*70}\nINC-{inc}")
        for t, sql in qs.items(): show(t, sql)
    print("\nRun with --fix to apply fixes and validate.")
else:
    for inc, (fix, check) in FIX.items():
        before = cur.execute(check).fetchone()[0]
        cur.execute(fix); changed = cur.rowcount
        after = cur.execute(check).fetchone()[0]
        cur.execute("UPDATE incidents SET status='Closed', closed_at=datetime('now'), root_cause=?, resolution=? WHERE incident_id=?",
                    (*CLOSE[inc], f"INC-{inc}"))
        print(f"INC-{inc}: bad rows before={before}, rows fixed={changed}, bad rows after={after} -> {'PASS' if after==0 else 'FAIL'}")
    for inc, why in (("1003","Escalated: guest checkout writes NULL/invalid customer_id; needs app fix + FK constraint"),
                     ("1005","Escalated to process owner: manual overrides without payment need approval workflow")):
        cur.execute("UPDATE incidents SET status='Escalated', root_cause=? WHERE incident_id=?", (why, f"INC-{inc}"))
        print(f"INC-{inc}: no safe data fix -> escalated")
    con.commit()
    show("TICKET BOARD", "SELECT incident_id, category, severity, status FROM incidents")
