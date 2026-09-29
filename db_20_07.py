"""
Database layer — Extended with Auth, RBAC, Sales, Contacts, Stock Movements.

Tables:
- shops:         each registered shop
- users:         superadmin / admin / employee with shop_id
- products:      unique by (shop_id, brand, product_type, model_no)
- inventory_log: append-only IN history
- sales:         stock OUT history (who sold, to whom, payment mode)
- contacts:      customers & dealers per shop

DISTRIBUTOR MODEL UPDATES:
- products:      last_price → purchase_price + sale_price (cost vs selling)
- inventory_log: + supplier_id, supplier_name, payment_mode, invoice_no
- sales:         + sale_type (retail / wholesale)
- contacts:      + gst_no, contact_type now allows supplier/customer/retailer
- dashboard:     + profit calculation (revenue - purchase cost)
"""

import sqlite3
import os
import hashlib
import secrets
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "inventory.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            created_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('superadmin','admin','employee')),
            shop_id INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(shop_id) REFERENCES shops(id)
        );

        /* ── CHANGED: last_price → purchase_price + sale_price ── */
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            brand TEXT NOT NULL,
            product_type TEXT NOT NULL,
            model_no TEXT NOT NULL DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 0,
            purchase_price REAL,
            sale_price REAL,
            category TEXT,
            min_stock_alert INTEGER DEFAULT 5,
            updated_at TEXT NOT NULL,
            UNIQUE(shop_id, brand, product_type, model_no),
            FOREIGN KEY(shop_id) REFERENCES shops(id)
        );

        /* ── CHANGED: price_per_unit → purchase_price, added supplier fields + invoice details ── */
        CREATE TABLE IF NOT EXISTS inventory_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            brand TEXT NOT NULL,
            product_type TEXT NOT NULL,
            model_no TEXT NOT NULL DEFAULT '',
            quantity_added INTEGER NOT NULL,
            purchase_price REAL,
            supplier_id INTEGER,
            supplier_name TEXT DEFAULT '',
            payment_mode TEXT DEFAULT 'cash',
            invoice_no TEXT DEFAULT '',
            invoice_date TEXT DEFAULT '',
            invoice_total REAL,
            invoice_notes TEXT DEFAULT '',
            raw_transcript TEXT,
            added_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(shop_id) REFERENCES shops(id),
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(supplier_id) REFERENCES contacts(id),
            FOREIGN KEY(added_by) REFERENCES users(id)
        );

        /* ── CHANGED: added sale_type column ── */
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity_sold INTEGER NOT NULL,
            sale_price REAL,
            total_amount REAL,
            sale_type TEXT DEFAULT 'retail',
            contact_id INTEGER,
            contact_name TEXT DEFAULT '',
            payment_mode TEXT DEFAULT 'cash',
            notes TEXT DEFAULT '',
            sold_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(shop_id) REFERENCES shops(id),
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(contact_id) REFERENCES contacts(id),
            FOREIGN KEY(sold_by) REFERENCES users(id)
        );

        /* ── CHANGED: removed CHECK constraint, added gst_no ── */
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            contact_type TEXT NOT NULL,
            address TEXT DEFAULT '',
            gst_no TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(shop_id) REFERENCES shops(id)
        );

        /* ── NEW: sale bills for download/print ── */
        CREATE TABLE IF NOT EXISTS sale_bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            bill_no TEXT NOT NULL,
            sale_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            brand TEXT, product_type TEXT, model_no TEXT,
            quantity INTEGER, sale_price REAL, total_amount REAL,
            buyer_name TEXT DEFAULT '', buyer_phone TEXT DEFAULT '',
            payment_mode TEXT DEFAULT 'cash',
            sale_type TEXT DEFAULT 'retail',
            notes TEXT DEFAULT '',
            created_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(shop_id) REFERENCES shops(id),
            FOREIGN KEY(sale_id) REFERENCES sales(id),
            FOREIGN KEY(created_by) REFERENCES users(id)
        );
    """)

    # Seed superadmin if not exists
    existing = conn.execute("SELECT id FROM users WHERE role='superadmin'").fetchone()
    if not existing:
        salt = secrets.token_hex(16)
        pw_hash = _hash_password("admin123", salt)
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO users (username, password_hash, salt, full_name, role, shop_id, created_at) "
            "VALUES (?, ?, ?, ?, 'superadmin', NULL, ?)",
            ("superadmin", pw_hash, salt, "Super Admin", now)
        )

    conn.commit()
    conn.close()


# ============ Auth helpers ============
# (NO CHANGE — same as before)

def _hash_password(password, salt):
    return hashlib.sha256((salt + password).encode()).hexdigest()


def create_shop(name, address="", phone="", created_by=None):
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO shops (name, address, phone, created_by, created_at) VALUES (?,?,?,?,?)",
        (name, address, phone, created_by, now)
    )
    shop_id = cur.lastrowid
    conn.commit()
    conn.close()
    return shop_id


def register_user(username, password, full_name, role, shop_id=None):
    conn = get_connection()
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    now = datetime.utcnow().isoformat()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, salt, full_name, role, shop_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, pw_hash, salt, full_name, role, shop_id, now)
        )
        user_id = cur.lastrowid
        conn.commit()
        conn.close()
        return user_id
    except sqlite3.IntegrityError:
        conn.close()
        return None  # username taken


def authenticate_user(username, password):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE username=? AND is_active=1", (username,)).fetchone()
    conn.close()
    if not user:
        return None
    expected = _hash_password(password, user["salt"])
    if expected == user["password_hash"]:
        return dict(user)
    return None


def get_user_by_id(user_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_users_by_shop(shop_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, username, full_name, role, is_active, created_at FROM users WHERE shop_id=? ORDER BY created_at",
        (shop_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_shops():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM shops ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_shop_by_id(shop_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def toggle_user_active(user_id, active):
    conn = get_connection()
    conn.execute("UPDATE users SET is_active=? WHERE id=?", (1 if active else 0, user_id))
    conn.commit()
    conn.close()


def get_all_users():
    conn = get_connection()
    rows = conn.execute(
        "SELECT u.id, u.username, u.full_name, u.role, u.is_active, u.created_at, u.shop_id, s.name as shop_name "
        "FROM users u LEFT JOIN shops s ON u.shop_id=s.id ORDER BY u.created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============ Products ============

def _normalize(value):
    return (value or "").strip().lower()


def find_product(conn, shop_id, brand, product_type, model_no):
    return conn.execute(
        "SELECT * FROM products WHERE shop_id=? AND LOWER(brand)=? AND LOWER(product_type)=? AND LOWER(model_no)=?",
        (shop_id, _normalize(brand), _normalize(product_type), _normalize(model_no)),
    ).fetchone()


# ██████████████████████████████████████████████████████████████████████
# ██  UPDATED: upsert_product_and_log                                ██
# ██  OLD: (item, shop_id, user_id, raw_transcript)                  ██
# ██  NEW: + supplier_id, supplier_name, payment_mode, invoice_no    ██
# ██  OLD: item had "price_per_unit"                                 ██
# ██  NEW: item has "purchase_price" and "sale_price"                ██
# ██████████████████████████████████████████████████████████████████████
def upsert_product_and_log(item, shop_id, user_id=None, raw_transcript="",
                           supplier_id=None, supplier_name="", payment_mode="cash", invoice_no="",
                           invoice_date="", invoice_total=None, invoice_notes=""):
    conn = get_connection()
    now = datetime.utcnow().isoformat()

    brand = item.get("brand", "Unknown")
    product_type = item.get("product_type", "Unknown")
    model_no = item.get("model_no", "") or ""
    quantity = int(item.get("quantity", 0))
    # ── CHANGED: was single "price_per_unit", now two prices ──
    # supports both old key (voice flow sends price_per_unit) and new keys
    purchase_price = item.get("purchase_price") or item.get("price_per_unit")
    sale_price = item.get("sale_price")
    category = item.get("category", "Uncategorized")

    existing = find_product(conn, shop_id, brand, product_type, model_no)

    if existing:
        new_qty = existing["quantity"] + quantity
        # ── CHANGED: update both prices if provided ──
        conn.execute(
            "UPDATE products SET quantity=?, updated_at=? WHERE id=?",
            (new_qty, now, existing["id"]),
        )
        # Only update prices if new values provided (don't overwrite with None)
        if purchase_price is not None:
            conn.execute("UPDATE products SET purchase_price=? WHERE id=?", (purchase_price, existing["id"]))
        if sale_price is not None:
            conn.execute("UPDATE products SET sale_price=? WHERE id=?", (sale_price, existing["id"]))
        if category:
            conn.execute("UPDATE products SET category=? WHERE id=?", (category, existing["id"]))
        product_id = existing["id"]
    else:
        # ── CHANGED: insert purchase_price + sale_price instead of last_price ──
        cur = conn.execute(
            "INSERT INTO products (shop_id, brand, product_type, model_no, quantity, purchase_price, sale_price, category, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (shop_id, brand, product_type, model_no, quantity, purchase_price, sale_price, category, now),
        )
        product_id = cur.lastrowid

    # ── CHANGED: inventory_log now stores supplier + payment + invoice info ──
    conn.execute(
        "INSERT INTO inventory_log (shop_id, product_id, brand, product_type, model_no, quantity_added, "
        "purchase_price, supplier_id, supplier_name, payment_mode, invoice_no, "
        "invoice_date, invoice_total, invoice_notes, "
        "raw_transcript, added_by, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (shop_id, product_id, brand, product_type, model_no, quantity,
         purchase_price, supplier_id, supplier_name, payment_mode, invoice_no,
         invoice_date, invoice_total, invoice_notes,
         raw_transcript, user_id, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    conn.close()
    return dict(row)


def get_all_products(shop_id=None):
    # (NO CHANGE)
    conn = get_connection()
    if shop_id:
        rows = conn.execute("SELECT * FROM products WHERE shop_id=? ORDER BY updated_at DESC", (shop_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM products ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ██████████████████████████████████████████████████████████████
# ██  UPDATED: allowed fields now include purchase_price,     ██
# ██  sale_price instead of just last_price                   ██
# ██████████████████████████████████████████████████████████████
def update_product_field(product_id, field, value):
    allowed = {"brand", "product_type", "model_no", "quantity",
               "purchase_price", "sale_price", "category", "min_stock_alert"}

    # supplier products table me nahi hota — inventory_log ki latest row me hota hai
    if field == "supplier":
        return update_product_supplier(product_id, value)

    if field not in allowed:
        raise ValueError(f"Field '{field}' editable nahi hai")
    conn = get_connection()
    conn.execute(
        f"UPDATE products SET {field}=?, updated_at=? WHERE id=?",
        (value, datetime.utcnow().isoformat(), product_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_product_supplier(product_id, supplier_name):
    """Product ki latest inventory_log row ka supplier update karta hai.
    Agar contacts me wahi supplier hai to link karta hai, warna naya bana deta hai."""
    conn = get_connection()

    # product ka shop_id chahiye (supplier shop ke andar hota hai)
    prod = conn.execute("SELECT shop_id FROM products WHERE id=?", (product_id,)).fetchone()
    if not prod:
        conn.close()
        raise ValueError("Product not found")
    shop_id = prod["shop_id"]

    # is product ki sabse nayi stock-IN row dhoondo
    latest = conn.execute(
        "SELECT id FROM inventory_log WHERE product_id=? ORDER BY id DESC LIMIT 1",
        (product_id,)
    ).fetchone()
    conn.close()  # auto_add_supplier apna connection khud kholta hai

    supplier_name = (supplier_name or "").strip()
    supplier_id = None
    if supplier_name:
        # contacts me match karo ya naya supplier bana do
        supplier_id, supplier_name = auto_add_supplier(shop_id, supplier_name)

    conn = get_connection()
    if latest:
        # latest IN row ka supplier update — yahi row last_supplier me dikhti hai
        conn.execute(
            "UPDATE inventory_log SET supplier_id=?, supplier_name=? WHERE id=?",
            (supplier_id, supplier_name, latest["id"])
        )
    else:
        # is product ka koi inventory_log nahi (rare) — ek placeholder IN row bana do
        p = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        conn.execute(
            "INSERT INTO inventory_log (shop_id, product_id, brand, product_type, model_no, "
            "quantity_added, supplier_id, supplier_name, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (shop_id, product_id, p["brand"], p["product_type"], p["model_no"],
             0, supplier_id, supplier_name, datetime.utcnow().isoformat())
        )
    conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    conn.close()
    return dict(row)



def delete_product(product_id):
    conn = get_connection()
    # Pehle related records delete karo (foreign key constraint)
    conn.execute("DELETE FROM sale_bills WHERE product_id=?", (product_id,))
    conn.execute("DELETE FROM sales WHERE product_id=?", (product_id,))
    conn.execute("DELETE FROM inventory_log WHERE product_id=?", (product_id,))
    conn.execute("DELETE FROM products WHERE id=?", (product_id,))
    conn.commit()
    conn.close()


# ██████████████████████████████████████████████████████████████
# ██  UPDATED: get_log_history now shows supplier name        ██
# ██████████████████████████████████████████████████████████████
def get_log_history(shop_id=None, limit=100):
    conn = get_connection()
    # ── CHANGED: added JOIN on contacts for supplier display name ──
    if shop_id:
        rows = conn.execute(
            "SELECT l.*, u.full_name as added_by_name, c.name as sup_display "
            "FROM inventory_log l "
            "LEFT JOIN users u ON l.added_by=u.id "
            "LEFT JOIN contacts c ON l.supplier_id=c.id "
            "WHERE l.shop_id=? ORDER BY l.id DESC LIMIT ?",
            (shop_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT l.*, u.full_name as added_by_name, c.name as sup_display "
            "FROM inventory_log l "
            "LEFT JOIN users u ON l.added_by=u.id "
            "LEFT JOIN contacts c ON l.supplier_id=c.id "
            "ORDER BY l.id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============ Sales ============

# ██████████████████████████████████████████████████████████████
# ██  UPDATED: record_sale now accepts sale_type parameter    ██
# ██  "retail" = walk-in customer                             ██
# ██  "wholesale" = selling to chhota shopkeeper in bulk      ██
# ██████████████████████████████████████████████████████████████
def record_sale(shop_id, product_id, quantity_sold, sale_price,
                sale_type="retail",  # ── NEW PARAM ──
                contact_id=None, contact_name="", payment_mode="cash", notes="", sold_by=None):
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    total = (sale_price or 0) * quantity_sold

    # Reduce product quantity
    product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        conn.close()
        raise ValueError("Product not found")
    new_qty = product["quantity"] - quantity_sold
    if new_qty < 0:
        conn.close()
        raise ValueError(f"Stock kam hai! Sirf {product['quantity']} available")

    conn.execute("UPDATE products SET quantity=?, updated_at=? WHERE id=?", (new_qty, now, product_id))

    # ── CHANGED: added sale_type in INSERT ──
    cur = conn.execute(
        "INSERT INTO sales (shop_id, product_id, quantity_sold, sale_price, total_amount, "
        "sale_type, contact_id, contact_name, payment_mode, notes, sold_by, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (shop_id, product_id, quantity_sold, sale_price, total,
         sale_type, contact_id, contact_name, payment_mode, notes, sold_by, now)
    )
    sale_id = cur.lastrowid
    conn.commit()

    row = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    conn.close()
    return dict(row)


# ██████████████████████████████████████████████████████████████
# ██  UPDATED: get_sales now also returns cost_price          ██
# ██  (purchase_price from products) for profit calculation   ██
# ██████████████████████████████████████████████████████████████
def get_sales(shop_id=None, limit=200):
    conn = get_connection()
    # ── CHANGED: added p.purchase_price as cost_price to SELECT ──
    if shop_id:
        rows = conn.execute(
            "SELECT s.*, p.brand, p.product_type, p.model_no, p.category, "
            "p.purchase_price as cost_price, "
            "u.full_name as sold_by_name, c.name as contact_display_name "
            "FROM sales s "
            "JOIN products p ON s.product_id=p.id "
            "LEFT JOIN users u ON s.sold_by=u.id "
            "LEFT JOIN contacts c ON s.contact_id=c.id "
            "WHERE s.shop_id=? ORDER BY s.created_at DESC LIMIT ?",
            (shop_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT s.*, p.brand, p.product_type, p.model_no, p.category, "
            "p.purchase_price as cost_price, "
            "u.full_name as sold_by_name, c.name as contact_display_name "
            "FROM sales s "
            "JOIN products p ON s.product_id=p.id "
            "LEFT JOIN users u ON s.sold_by=u.id "
            "LEFT JOIN contacts c ON s.contact_id=c.id "
            "ORDER BY s.created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sales_trend(shop_id, product_id=None, days=30):
    """Daily sales quantities for trend chart."""
    # (NO CHANGE)
    conn = get_connection()
    query = """
        SELECT DATE(created_at) as sale_date, SUM(quantity_sold) as total_qty,
               SUM(total_amount) as total_revenue
        FROM sales WHERE shop_id=?
    """
    params = [shop_id]
    if product_id:
        query += " AND product_id=?"
        params.append(product_id)
    query += f" AND created_at >= DATE('now', '-{int(days)} days') GROUP BY DATE(created_at) ORDER BY sale_date"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ██████████████████████████████████████████████████████████████
# ██  UPDATED: Stock IN now shows supplier name               ██
# ██  Stock IN reads purchase_price instead of price_per_unit  ██
# ██████████████████████████████████████████████████████████████
def get_stock_movements(shop_id, date_from=None, date_to=None):
    """Combined IN (inventory_log) and OUT (sales) movements."""
    conn = get_connection()

    # ── CHANGED: Stock IN now JOINs contacts for supplier name ──
    q_in = """
        SELECT l.created_at, 'IN' as direction, l.brand, l.product_type, l.model_no,
               l.quantity_added as quantity, l.purchase_price as price,
               u.full_name as done_by,
               COALESCE(c.name, l.supplier_name) as contact_name,
               l.payment_mode
        FROM inventory_log l
        LEFT JOIN users u ON l.added_by=u.id
        LEFT JOIN contacts c ON l.supplier_id=c.id
        WHERE l.shop_id=?
    """
    params_in = [shop_id]
    if date_from:
        q_in += " AND DATE(l.created_at) >= ?"
        params_in.append(date_from)
    if date_to:
        q_in += " AND DATE(l.created_at) <= ?"
        params_in.append(date_to)

    # Stock OUT (minor change: category removed from select since it might cause issues)
    q_out = """
        SELECT s.created_at, 'OUT' as direction, p.brand, p.product_type, p.model_no,
               s.quantity_sold as quantity, s.sale_price as price,
               u.full_name as done_by, COALESCE(c.name, s.contact_name) as contact_name,
               s.payment_mode
        FROM sales s
        JOIN products p ON s.product_id=p.id
        LEFT JOIN users u ON s.sold_by=u.id
        LEFT JOIN contacts c ON s.contact_id=c.id
        WHERE s.shop_id=?
    """
    params_out = [shop_id]
    if date_from:
        q_out += " AND DATE(s.created_at) >= ?"
        params_out.append(date_from)
    if date_to:
        q_out += " AND DATE(s.created_at) <= ?"
        params_out.append(date_to)

    rows_in = conn.execute(q_in, params_in).fetchall()
    rows_out = conn.execute(q_out, params_out).fetchall()
    conn.close()

    movements = [dict(r) for r in rows_in] + [dict(r) for r in rows_out]
    movements.sort(key=lambda x: x["created_at"], reverse=True)
    return movements


# ============ Contacts ============

# ██████████████████████████████████████████████████████████████
# ██  UPDATED: add_contact now accepts gst_no (7th param)     ██
# ██  contact_type can now be: supplier, customer, retailer   ██
# ██████████████████████████████████████████████████████████████
def add_contact(shop_id, name, phone="", email="", contact_type="customer", address="",
                gst_no=""):  # ── NEW PARAM ──
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    # ── CHANGED: added gst_no column ──
    cur = conn.execute(
        "INSERT INTO contacts (shop_id, name, phone, email, contact_type, address, gst_no, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (shop_id, name, phone, email, contact_type, address, gst_no, now)
    )
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    return cid


def get_contacts(shop_id, contact_type=None):
    # (NO CHANGE)
    conn = get_connection()
    if contact_type:
        rows = conn.execute(
            "SELECT * FROM contacts WHERE shop_id=? AND contact_type=? ORDER BY name", (shop_id, contact_type)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM contacts WHERE shop_id=? ORDER BY contact_type, name", (shop_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_contact(contact_id):
    # (NO CHANGE)
    conn = get_connection()
    conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    conn.commit()
    conn.close()


# ============ Dashboard stats ============

# ██████████████████████████████████████████████████████████████
# ██  UPDATED: dashboard now shows profit calculation          ██
# ██  stock_cost_value, total_purchased, total_sold_revenue,  ██
# ██  total_profit = revenue - purchase cost                   ██
# ██████████████████████████████████████████████████████████████
def get_dashboard_stats(shop_id):
    conn = get_connection()
    total_products = conn.execute("SELECT COUNT(*) as c FROM products WHERE shop_id=?", (shop_id,)).fetchone()["c"]
    total_qty = conn.execute("SELECT COALESCE(SUM(quantity),0) as c FROM products WHERE shop_id=?", (shop_id,)).fetchone()["c"]
    low_stock = conn.execute(
        "SELECT COUNT(*) as c FROM products WHERE shop_id=? AND quantity < min_stock_alert", (shop_id,)
    ).fetchone()["c"]
    today_sales = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) as revenue, COALESCE(SUM(quantity_sold),0) as units "
        "FROM sales WHERE shop_id=? AND DATE(created_at)=DATE('now')", (shop_id,)
    ).fetchone()

    # ── CHANGED: was last_price, now purchase_price for cost value ──
    stock_cost_value = conn.execute(
        "SELECT COALESCE(SUM(quantity * COALESCE(purchase_price,0)),0) as val FROM products WHERE shop_id=?", (shop_id,)
    ).fetchone()["val"]

    # ── NEW: profit calculation ──
    total_purchased = conn.execute(
        "SELECT COALESCE(SUM(quantity_added * COALESCE(purchase_price,0)),0) as c "
        "FROM inventory_log WHERE shop_id=?", (shop_id,)
    ).fetchone()["c"]

    total_sold_revenue = conn.execute(
        "SELECT COALESCE(SUM(total_amount),0) as c FROM sales WHERE shop_id=?", (shop_id,)
    ).fetchone()["c"]

    conn.close()
    return {
        "total_products": total_products,
        "total_quantity": total_qty,
        "low_stock_alerts": low_stock,
        "today_revenue": today_sales["revenue"],
        "today_units_sold": today_sales["units"],
        # ── CHANGED: was "total_stock_value", now more specific ──
        "stock_cost_value": round(stock_cost_value),
        # ── NEW: profit tracking fields ──
        "total_purchased": round(total_purchased),
        "total_sold_revenue": round(total_sold_revenue),
        "total_profit": round(total_sold_revenue - total_purchased),
    }


def get_low_stock_products(shop_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM products WHERE shop_id=? AND quantity < min_stock_alert ORDER BY quantity ASC",
        (shop_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============ NEW: Supplier matching from voice ============

def match_supplier(shop_id, spoken_name):
    """Try to match a spoken supplier name to existing contacts.
    Returns (supplier_id, matched_name) or (None, spoken_name)."""
    if not spoken_name:
        return None, ""
    conn = get_connection()
    spoken_lower = spoken_name.strip().lower()
    suppliers = conn.execute(
        "SELECT id, name FROM contacts WHERE shop_id=? AND contact_type='supplier'", (shop_id,)
    ).fetchall()
    conn.close()

    # Exact match
    for s in suppliers:
        if s["name"].lower() == spoken_lower:
            return s["id"], s["name"]
    # Partial match (spoken name is contained in DB name or vice versa)
    for s in suppliers:
        db_lower = s["name"].lower()
        if spoken_lower in db_lower or db_lower in spoken_lower:
            return s["id"], s["name"]
    # Word overlap match (at least one significant word matches)
    spoken_words = set(w for w in spoken_lower.split() if len(w) > 2)
    for s in suppliers:
        db_words = set(w for w in s["name"].lower().split() if len(w) > 2)
        if spoken_words & db_words:
            return s["id"], s["name"]

    return None, spoken_name


# ============ NEW: Products with supplier & invoice details ============

def get_products_with_details(shop_id):
    """Products with latest supplier name and invoice info from inventory_log."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT p.*,
            (SELECT COALESCE(c.name, l2.supplier_name)
             FROM inventory_log l2
             LEFT JOIN contacts c ON l2.supplier_id=c.id
             WHERE l2.product_id=p.id ORDER BY l2.id DESC LIMIT 1
            ) as last_supplier,
            (SELECT l3.invoice_no FROM inventory_log l3
             WHERE l3.product_id=p.id AND l3.invoice_no != '' ORDER BY l3.id DESC LIMIT 1
            ) as last_invoice_no,
            (SELECT l4.created_at FROM inventory_log l4
             WHERE l4.product_id=p.id ORDER BY l4.id DESC LIMIT 1
            ) as last_stock_date
        FROM products p WHERE p.shop_id=? ORDER BY p.updated_at DESC
    """, (shop_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_product_full_history(product_id):
    """Full IN + OUT history for a single product."""
    conn = get_connection()
    ins = conn.execute("""
        SELECT l.created_at, 'IN' as direction, l.quantity_added as quantity,
               l.purchase_price as price, COALESCE(c.name, l.supplier_name) as contact,
               l.payment_mode, l.invoice_no, l.invoice_date, l.invoice_total,
               u.full_name as done_by
        FROM inventory_log l
        LEFT JOIN users u ON l.added_by=u.id
        LEFT JOIN contacts c ON l.supplier_id=c.id
        WHERE l.product_id=?
    """, (product_id,)).fetchall()
    outs = conn.execute("""
        SELECT s.created_at, 'OUT' as direction, s.quantity_sold as quantity,
               s.sale_price as price, COALESCE(c.name, s.contact_name) as contact,
               s.payment_mode, '' as invoice_no, '' as invoice_date, NULL as invoice_total,
               u.full_name as done_by
        FROM sales s
        LEFT JOIN users u ON s.sold_by=u.id
        LEFT JOIN contacts c ON s.contact_id=c.id
        WHERE s.product_id=?
    """, (product_id,)).fetchall()
    conn.close()
    history = [dict(r) for r in ins] + [dict(r) for r in outs]
    history.sort(key=lambda x: x["created_at"], reverse=True)
    return history


# ============ NEW: Monthly stock view ============

def get_monthly_stock(shop_id, year_month=None):
    """Get stock IN entries for a given month (YYYY-MM). Defaults to current month."""
    conn = get_connection()
    if not year_month:
        year_month = datetime.utcnow().strftime("%Y-%m")
    rows = conn.execute(
        "SELECT l.*, u.full_name as added_by_name, "
        "COALESCE(c.name, l.supplier_name) as supplier_display "
        "FROM inventory_log l "
        "LEFT JOIN users u ON l.added_by=u.id "
        "LEFT JOIN contacts c ON l.supplier_id=c.id "
        "WHERE l.shop_id=? AND strftime('%Y-%m', l.created_at)=? "
        "ORDER BY l.created_at DESC",
        (shop_id, year_month)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_available_months(shop_id):
    """Get list of months that have stock entries."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT strftime('%Y-%m', created_at) as month "
        "FROM inventory_log WHERE shop_id=? ORDER BY month DESC",
        (shop_id,)
    ).fetchall()
    conn.close()
    return [r["month"] for r in rows]


# ============ NEW: Sale bill generation ============

def generate_sale_bill(shop_id, sale_id, user_id=None):
    """Auto-generate a bill for a sale. Returns bill dict."""
    conn = get_connection()
    sale = conn.execute("""
        SELECT s.*, p.brand, p.product_type, p.model_no, p.category,
               COALESCE(c.name, s.contact_name) as buyer_name,
               COALESCE(c.phone, '') as buyer_phone
        FROM sales s
        JOIN products p ON s.product_id=p.id
        LEFT JOIN contacts c ON s.contact_id=c.id
        WHERE s.id=?
    """, (sale_id,)).fetchone()
    if not sale:
        conn.close()
        return None
    sale = dict(sale)

    # Generate bill number: BILL-SHOPID-SALEID
    bill_no = f"BILL-{shop_id}-{sale_id}"
    now = datetime.utcnow().isoformat()

    cur = conn.execute("""
        INSERT INTO sale_bills (shop_id, bill_no, sale_id, product_id, brand, product_type,
            model_no, quantity, sale_price, total_amount, buyer_name, buyer_phone,
            payment_mode, sale_type, notes, created_by, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (shop_id, bill_no, sale_id, sale["product_id"], sale["brand"],
          sale["product_type"], sale["model_no"], sale["quantity_sold"],
          sale["sale_price"], sale["total_amount"], sale["buyer_name"],
          sale["buyer_phone"], sale["payment_mode"], sale.get("sale_type", "retail"),
          sale["notes"], user_id, now))
    bill_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM sale_bills WHERE id=?", (bill_id,)).fetchone()
    conn.close()
    return dict(row)


def get_sale_bill(bill_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM sale_bills WHERE id=?", (bill_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_sale_bills(shop_id, limit=100):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM sale_bills WHERE shop_id=? ORDER BY created_at DESC LIMIT ?",
        (shop_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bill_by_sale(sale_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM sale_bills WHERE sale_id=?", (sale_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ============ NEW: Auto-add supplier from stock form ============

def auto_add_supplier(shop_id, supplier_name):
    """If supplier_name doesn't exist in contacts, create it. Returns (supplier_id, name)."""
    if not supplier_name or not supplier_name.strip():
        return None, ""
    name = supplier_name.strip()
    conn = get_connection()
    # Check if already exists (case-insensitive)
    existing = conn.execute(
        "SELECT id, name FROM contacts WHERE shop_id=? AND contact_type='supplier' AND LOWER(name)=?",
        (shop_id, name.lower())
    ).fetchone()
    if existing:
        conn.close()
        return existing["id"], existing["name"]
    # Create new supplier
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO contacts (shop_id, name, phone, email, contact_type, address, gst_no, created_at) "
        "VALUES (?,?,'','','supplier','','',?)", (shop_id, name, now))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid, name


# ============ NEW: Update contact (edit) ============

def update_contact(contact_id, data):
    """Update contact fields. data is dict of field:value."""
    allowed = {"name", "phone", "email", "contact_type", "address", "gst_no"}
    conn = get_connection()
    for field, value in data.items():
        if field in allowed:
            conn.execute(f"UPDATE contacts SET {field}=? WHERE id=?", (value, contact_id))
    conn.commit()
    row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ============ NEW: Monthly sales (OUT) ============

def get_monthly_sales(shop_id, year_month=None):
    """Sales for a given month."""
    conn = get_connection()
    if not year_month:
        year_month = datetime.utcnow().strftime("%Y-%m")
    rows = conn.execute(
        "SELECT s.*, p.brand, p.product_type, p.model_no, p.category, "
        "u.full_name as sold_by_name, COALESCE(c.name, s.contact_name) as buyer_display "
        "FROM sales s JOIN products p ON s.product_id=p.id "
        "LEFT JOIN users u ON s.sold_by=u.id "
        "LEFT JOIN contacts c ON s.contact_id=c.id "
        "WHERE s.shop_id=? AND strftime('%Y-%m', s.created_at)=? "
        "ORDER BY s.created_at DESC",
        (shop_id, year_month)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============ NEW: Monthly summary stats ============

def get_monthly_summary(shop_id, year_month=None):
    """Summary stats for a month: total IN, total OUT, revenue, cost."""
    conn = get_connection()
    if not year_month:
        year_month = datetime.utcnow().strftime("%Y-%m")
    stock_in = conn.execute(
        "SELECT COALESCE(SUM(quantity_added),0) as qty, COALESCE(SUM(quantity_added*COALESCE(purchase_price,0)),0) as cost "
        "FROM inventory_log WHERE shop_id=? AND strftime('%Y-%m', created_at)=?",
        (shop_id, year_month)
    ).fetchone()
    stock_out = conn.execute(
        "SELECT COALESCE(SUM(quantity_sold),0) as qty, COALESCE(SUM(total_amount),0) as revenue "
        "FROM sales WHERE shop_id=? AND strftime('%Y-%m', created_at)=?",
        (shop_id, year_month)
    ).fetchone()
    conn.close()
    return {
        "in_qty": stock_in["qty"], "in_cost": round(stock_in["cost"]),
        "out_qty": stock_out["qty"], "out_revenue": round(stock_out["revenue"]),
        "profit": round(stock_out["revenue"] - stock_in["cost"]),
    }