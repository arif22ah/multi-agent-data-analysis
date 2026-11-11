import sqlite3
import numpy as np
import pandas as pd
from src.config import SQLITE_DB_PATH, POSTGRES_URI

try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except Exception:
    PSYCOPG2_AVAILABLE = False


class EnterpriseDatabaseManager:
    """Manages read-only analytical execution against PostgreSQL with automatic SQLite fallback."""

    def __init__(self, db_uri: str = POSTGRES_URI):
        self.db_uri = db_uri
        self.sqlite_path = SQLITE_DB_PATH
        self.use_postgres = db_uri.startswith("postgresql://") and PSYCOPG2_AVAILABLE
        self.ensure_seeded()

    def ensure_seeded(self) -> None:
        if self.sqlite_path.exists() and self.sqlite_path.stat().st_size > 10240:
            return
        self._seed_sqlite_database()

    def _seed_sqlite_database(self) -> None:
        rng = np.random.default_rng(42)

        with sqlite3.connect(self.sqlite_path) as conn:
            cur = conn.cursor()
            cur.executescript("""
                DROP TABLE IF EXISTS support_tickets;
                DROP TABLE IF EXISTS order_items;
                DROP TABLE IF EXISTS orders;
                DROP TABLE IF EXISTS products;
                DROP TABLE IF EXISTS customers;

                CREATE TABLE customers (
                    customer_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    region TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    signup_date TEXT NOT NULL,
                    annual_spend REAL NOT NULL
                );

                CREATE TABLE products (
                    product_id INTEGER PRIMARY KEY,
                    product_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    unit_price REAL NOT NULL,
                    cost_price REAL NOT NULL,
                    stock_quantity INTEGER NOT NULL
                );

                CREATE TABLE orders (
                    order_id INTEGER PRIMARY KEY,
                    customer_id INTEGER NOT NULL,
                    order_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_amount REAL NOT NULL,
                    discount_pct REAL NOT NULL,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
                );

                CREATE TABLE order_items (
                    item_id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    line_total REAL NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders(order_id),
                    FOREIGN KEY (product_id) REFERENCES products(product_id)
                );

                CREATE TABLE support_tickets (
                    ticket_id INTEGER PRIMARY KEY,
                    customer_id INTEGER NOT NULL,
                    priority TEXT NOT NULL,
                    resolution_hours REAL NOT NULL,
                    csat_score INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
                );
            """)

            regions = ["North America", "Europe", "Asia-Pacific", "Latin America"]
            segments = ["Enterprise", "Mid-Market", "SMB"]
            customers = []
            for cid in range(1, 121):
                reg = str(rng.choice(regions, p=[0.4, 0.3, 0.2, 0.1]))
                seg = str(rng.choice(segments, p=[0.35, 0.40, 0.25]))
                spend = round(float(rng.lognormal(9.3, 0.65)), 2)
                month = int(rng.integers(1, 13))
                day = int(rng.integers(1, 28))
                customers.append((cid, f"Client_{cid:03d}", reg, seg, f"2025-{month:02d}-{day:02d}", spend))
            cur.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)", customers)

            categories = ["Cloud Infrastructure", "AI Platform", "Security Suite", "Data Warehouse", "Observability"]
            products = []
            for pid in range(1, 26):
                cat = categories[(pid - 1) % len(categories)]
                u_price = round(float(rng.uniform(120.0, 2400.0)), 2)
                c_price = round(u_price * float(rng.uniform(0.35, 0.65)), 2)
                stock = int(rng.integers(15, 350))
                products.append((pid, f"{cat.split()[0]}_Pro_{pid:02d}", cat, u_price, c_price, stock))
            cur.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)", products)

            statuses = ["Completed", "Processing", "Refunded", "Cancelled"]
            orders = []
            order_items = []
            item_id = 1
            for oid in range(1, 451):
                cid = int(rng.integers(1, 121))
                month = int(rng.integers(1, 13))
                day = int(rng.integers(1, 28))
                st = str(rng.choice(statuses, p=[0.78, 0.12, 0.05, 0.05]))
                disc = round(float(rng.choice([0.0, 5.0, 10.0, 15.0], p=[0.5, 0.25, 0.15, 0.10])), 1)

                n_items = int(rng.integers(1, 4))
                order_total = 0.0
                for _ in range(n_items):
                    pid = int(rng.integers(1, 26))
                    u_price = products[pid - 1][3]
                    qty = int(rng.integers(1, 8))
                    l_total = round(u_price * qty * (1.0 - disc / 100.0), 2)
                    order_total += l_total
                    order_items.append((item_id, oid, pid, qty, l_total))
                    item_id += 1

                orders.append((oid, cid, f"2026-{month:02d}-{day:02d}", st, round(order_total, 2), disc))

            cur.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", orders)
            cur.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", order_items)

            priorities = ["Critical", "High", "Medium", "Low"]
            t_statuses = ["Resolved", "Closed", "Open"]
            tickets = []
            for tid in range(1, 201):
                cid = int(rng.integers(1, 121))
                prio = str(rng.choice(priorities, p=[0.15, 0.30, 0.35, 0.20]))
                res_hrs = round(float(rng.gamma(2.2, 3.5)), 2)
                csat = int(rng.choice([1, 2, 3, 4, 5], p=[0.05, 0.08, 0.17, 0.35, 0.35]))
                tst = str(rng.choice(t_statuses, p=[0.6, 0.3, 0.1]))
                tickets.append((tid, cid, prio, res_hrs, csat, tst))
            cur.executemany("INSERT INTO support_tickets VALUES (?, ?, ?, ?, ?, ?)", tickets)
            conn.commit()

    def get_schema_metadata(self) -> dict[str, list[str]]:
        schema = {}
        with sqlite3.connect(self.sqlite_path) as conn:
            cur = conn.cursor()
            tables = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for (t_name,) in tables:
                cols = cur.execute(f"PRAGMA table_info({t_name})").fetchall()
                schema[t_name] = [f"{col[1]} ({col[2]})" for col in cols]
        return schema

    def execute_readonly_query(self, sql: str) -> pd.DataFrame:
        """Executes SQL in strict read-only mode (`mode=ro`) so the DB engine also blocks writes."""
        ro_uri = f"file:{self.sqlite_path.as_posix()}?mode=ro"
        with sqlite3.connect(ro_uri, uri=True) as conn:
            return pd.read_sql_query(sql, conn)
