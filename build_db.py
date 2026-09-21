"""Builds retail_support.db with realistic data + 5 planted production incidents."""
import sqlite3, random, os
from datetime import datetime, timedelta

random.seed(42)
DB = os.path.join(os.path.dirname(__file__), "retail_support.db")
if os.path.exists(DB):
    os.remove(DB)
con = sqlite3.connect(DB)
cur = con.cursor()

# NOTE: no FOREIGN KEY on orders.customer_id and no UNIQUE on payments.txn_ref -- deliberate.
# Those gaps are part of the root causes you'll find.
cur.executescript("""
CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, created_at TEXT);
CREATE TABLE stores    (store_id INTEGER PRIMARY KEY, name TEXT, city TEXT, tax_rate REAL);
CREATE TABLE products  (product_id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
CREATE TABLE orders    (order_id INTEGER PRIMARY KEY, customer_id INTEGER, store_id INTEGER,
                        order_date TEXT, status TEXT, total_amount REAL);
CREATE TABLE order_items (item_id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER,
                        qty INTEGER, unit_price REAL);
CREATE TABLE payments  (payment_id INTEGER PRIMARY KEY, order_id INTEGER, txn_ref TEXT,
                        amount REAL, method TEXT, status TEXT, paid_at TEXT);
CREATE TABLE api_log   (log_id INTEGER PRIMARY KEY, ts TEXT, endpoint TEXT, method TEXT,
                        status_code INTEGER, latency_ms INTEGER, order_id INTEGER, error TEXT);
CREATE TABLE incidents (incident_id TEXT PRIMARY KEY, title TEXT, category TEXT, severity TEXT,
                        status TEXT, opened_at TEXT, closed_at TEXT, root_cause TEXT, resolution TEXT);
""")

fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")
start = datetime(2026, 9, 1, 9, 0, 0)

stores = [(1, "Kothrud Store", "Pune", .18), (2, "Bandra Store", "Mumbai", .18),
          (3, "Indiranagar Store", "Bengaluru", .18), (4, "Salt Lake Store", "Kolkata", .18)]
cur.executemany("INSERT INTO stores VALUES (?,?,?,?)", stores)
TAX = {s[0]: s[3] for s in stores}

prods = [("USB-C Cable","Accessories",399),("Wireless Mouse","Accessories",799),("Keyboard","Accessories",1499),
         ("Headphones","Audio",2499),("Bluetooth Speaker","Audio",3299),("Webcam","Video",2199),
         ("Monitor 24in","Displays",10999),("Laptop Stand","Accessories",1299),("Power Bank","Accessories",1899),
         ("Smart Watch","Wearables",4999)]
cur.executemany("INSERT INTO products (name,category,price) VALUES (?,?,?)", prods)
PRICE = {i+1: p[2] for i, p in enumerate(prods)}

first = ["Aarav","Vivaan","Diya","Isha","Rohan","Meera","Kabir","Anaya","Arjun","Sneha","Neha","Rahul"]
last = ["Sharma","Patil","Iyer","Gupta","Nair","Deshmukh","Reddy","Das","Joshi","Kulkarni"]
for i in range(1, 121):
    n = f"{random.choice(first)} {random.choice(last)}"
    cur.execute("INSERT INTO customers VALUES (?,?,?,?,?)",
                (i, n, f"{n.lower().replace(' ','.')}{i}@mail.com", f"98{random.randint(10000000,99999999)}",
                 fmt(start - timedelta(days=random.randint(10, 400)))))

oid = [0]; pid = [0]; lid = [0]
def add_order(cust, store, when, status, tax=None, items=None):
    oid[0] += 1
    items = items or [(random.randint(1, 10), random.randint(1, 3)) for _ in range(random.randint(1, 3))]
    sub = sum(PRICE[p] * q for p, q in items)
    total = round(sub * (1 + (TAX[store] if tax is None else tax)), 2)
    cur.execute("INSERT INTO orders VALUES (?,?,?,?,?,?)", (oid[0], cust, store, fmt(when), status, total))
    cur.executemany("INSERT INTO order_items (order_id,product_id,qty,unit_price) VALUES (?,?,?,?)",
                    [(oid[0], p, q, PRICE[p]) for p, q in items])
    return oid[0], total
def add_pay(o, amt, status, when, ref=None):
    pid[0] += 1
    cur.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?)",
                (pid[0], o, ref or f"TXN{100000+pid[0]}", amt, random.choice(["UPI","CARD","NETBANKING"]), status, fmt(when)))
def log(when, ep, meth, code, lat, o, err=None):
    lid[0] += 1
    cur.execute("INSERT INTO api_log VALUES (?,?,?,?,?,?,?,?)", (lid[0], fmt(when), ep, meth, code, lat, o, err))
def normal_flow(o, when, ok=True):
    log(when, "/api/orders", "POST", 201, random.randint(80, 250), o)
    log(when + timedelta(seconds=20), "/api/payments", "POST", 200, random.randint(200, 900), o)
    if ok:
        log(when + timedelta(seconds=25), "/webhook/payment-confirm", "POST", 200, random.randint(50, 200), o)

# ---- baseline healthy traffic (300 orders) ----
for _ in range(300):
    when = start + timedelta(days=random.randint(0, 12), minutes=random.randint(0, 600))
    s = random.choice([1, 2, 3, 4]); c = random.randint(1, 120)
    r = random.random()
    if r < .80:
        st = random.choice(["Delivered"]*5 + ["Shipped"]*2 + ["Confirmed"])
        o, t = add_order(c, s, when, st); add_pay(o, t, "SUCCESS", when + timedelta(seconds=20)); normal_flow(o, when)
    elif r < .90:
        o, t = add_order(c, s, when, "Cancelled"); add_pay(o, t, "FAILED", when + timedelta(seconds=20)); normal_flow(o, when, ok=False)
    else:  # legit abandoned checkout: Pending with NO successful payment (noise for your queries)
        o, t = add_order(c, s, when, "Pending"); normal_flow(o, when, ok=False)

# ---- INC-1001: payment SUCCESS but order stuck Pending (webhook timeouts on 14 Sep 02:10-02:40) ----
base = datetime(2026, 9, 14, 2, 10)
for k in range(7):
    when = base + timedelta(minutes=k * 4)
    o, t = add_order(random.randint(1, 120), random.choice([1, 2, 3, 4]), when, "Pending")
    add_pay(o, t, "SUCCESS", when + timedelta(seconds=20))
    log(when, "/api/orders", "POST", 201, 140, o)
    log(when + timedelta(seconds=20), "/api/payments", "POST", 200, 610, o)
    log(when + timedelta(seconds=25), "/webhook/payment-confirm", "POST", 504, 30000, o, "upstream timeout: order-service")

# ---- INC-1002: duplicate charge (client retry after gateway timeout; no UNIQUE on txn_ref) ----
for k in range(4):
    when = datetime(2026, 9, 15, 11, 0) + timedelta(hours=k * 2)
    o, t = add_order(random.randint(1, 120), random.choice([1, 2, 3, 4]), when, "Confirmed")
    ref = f"TXN9{k}{random.randint(1000,9999)}"
    add_pay(o, t, "SUCCESS", when + timedelta(seconds=20), ref)
    add_pay(o, t, "SUCCESS", when + timedelta(seconds=52), ref)
    log(when, "/api/orders", "POST", 201, 150, o)
    log(when + timedelta(seconds=20), "/api/payments", "POST", 504, 30000, o, "gateway timeout (charge went through)")
    log(when + timedelta(seconds=52), "/api/payments", "POST", 200, 700, o, "client retry")
    log(when + timedelta(seconds=55), "/webhook/payment-confirm", "POST", 200, 90, o)

# ---- INC-1003: orders with NULL / non-existent customer_id (guest checkout path) ----
for k in range(5):
    when = datetime(2026, 9, 16, 10, 0) + timedelta(minutes=k * 17)
    o, t = add_order(None, random.choice([1, 2]), when, "Confirmed"); add_pay(o, t, "SUCCESS", when + timedelta(seconds=20)); normal_flow(o, when)
for k in range(3):
    when = datetime(2026, 9, 16, 15, 0) + timedelta(minutes=k * 23)
    o, t = add_order(9000 + k, random.choice([3, 4]), when, "Confirmed"); add_pay(o, t, "SUCCESS", when + timedelta(seconds=20)); normal_flow(o, when)

# ---- INC-1004: total_amount mismatch at Store 3 (app still using old 12% tax rate; config says 18%) ----
for k in range(6):
    when = datetime(2026, 9, 17, 12, 0) + timedelta(hours=k)
    o, t = add_order(random.randint(1, 120), 3, when, "Confirmed", tax=0.12)
    add_pay(o, t, "SUCCESS", when + timedelta(seconds=20)); normal_flow(o, when)

# ---- INC-1005: 'Delivered' orders with no payment record at all (manual status override) ----
for k in range(3):
    when = datetime(2026, 9, 18, 9, 30) + timedelta(hours=k * 3)
    o, t = add_order(random.randint(1, 120), 4, when, "Delivered")
    log(when, "/api/orders", "POST", 201, 130, o)
    log(when + timedelta(hours=20), "/admin/orders/status", "PUT", 200, 60, o, "manual override by support-user")

tickets = [
 ("INC-1001","Customer paid but order still Pending","Application","High"),
 ("INC-1002","Customer charged twice for one order","Data / Payment","High"),
 ("INC-1003","Orders with no valid customer record","Data Quality","Medium"),
 ("INC-1004","Invoice total differs from item total at Bengaluru store","Configuration","Medium"),
 ("INC-1005","Orders marked Delivered with no payment on file","Process / User","High"),
]
for i, (n, t, c, s) in enumerate(tickets):
    cur.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?)",
                (n, t, c, s, "Open", fmt(datetime(2026, 9, 14 + i, 14, 0)), None, None, None))
con.commit(); con.close()
print("Built", DB)
