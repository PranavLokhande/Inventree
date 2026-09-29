"""
Database layer — MySQL version. Auth, RBAC, Sales, Contacts, Stock Movements.

Tables:
- shops:         each registered shop
- users:         superadmin / admin / employee with shop_id
- products:      unique by (shop_id, brand, product_type, model_no)
- inventory_log: append-only IN history
- sales:         stock OUT history (who sold, to whom, payment mode)
- contacts:      customers & dealers per shop
- sale_bills:    generated bills for sales

DISTRIBUTOR MODEL:
- products:      purchase_price (cost) + sale_price (selling)
- inventory_log: + supplier_id, supplier_name, payment_mode, invoice_no
- sales:         + sale_type (retail / wholesale)
- contacts:      + gst_no, contact_type allows supplier/customer/retailer
- dashboard:     + profit calculation (revenue - purchase cost)

MIGRATION NOTES (SQLite -> MySQL):
- ? placeholders -> %s
- sqlite3.Row -> cursor(dictionary=True) so rows come back as dicts already
- AUTOINCREMENT -> AUTO_INCREMENT, tables use ENGINE=InnoDB CHARSET=utf8mb4
- Table creation order changed: MySQL requires a referenced table to already
  exist when a FOREIGN KEY is declared (SQLite doesn't check this at
  CREATE TABLE time), so shops -> users -> contacts -> products ->
  inventory_log -> sales -> sale_bills.
- TEXT columns that had DEFAULT '' became VARCHAR(...) since MySQL doesn't
  allow defaults on TEXT/BLOB in most versions.
- Timestamps are now real Python datetime objects bound into DATETIME
  columns (instead of isoformat() strings), so DATE()/DATE_FORMAT()/
  DATE_SUB() work natively in MySQL.
- strftime('%Y-%m', col)      -> DATE_FORMAT(col, '%Y-%m')
- DATE('now')                 -> CURDATE()
- DATE('now', '-N days')      -> DATE_SUB(NOW(), INTERVAL N DAY)
- sqlite3.IntegrityError       -> mysql.connector.errors.IntegrityError
"""

import os
import hashlib
import secrets
from datetime import datetime

import mysql.connector
from mysql.connector import errors as mysql_errors

# ============ Connection config ============
# Set these via environment variables in production.
DB_HOST = os.environ.get("DB_HOST", "187.127.138.241")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "Storing_DB")
DB_NAME = os.environ.get("DB_NAME", "inventree")


def get_connection():
    """Returns a live MySQL connection. Caller is responsible for closing it."""
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user="root",
        password=DB_PASSWORD,
        database=DB_NAME,
    )


def _now():
    """Single source of truth for 'current timestamp' used across inserts."""
    return datetime.utcnow()


# ============ Schema ============
# NOTE: order matters here because of FOREIGN KEY dependencies.
_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS shops (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        address VARCHAR(255) DEFAULT '',
        phone VARCHAR(50) DEFAULT '',
        created_by INT,
        created_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(100) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        salt VARCHAR(255) NOT NULL,
        full_name VARCHAR(255) NOT NULL,
        role VARCHAR(20) NOT NULL CHECK (role IN ('superadmin','admin','employee')),
        shop_id INT,
        is_active TINYINT(1) DEFAULT 1,
        created_at DATETIME NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS contacts (
        id INT AUTO_INCREMENT PRIMARY KEY,
        shop_id INT NOT NULL,
        name VARCHAR(255) NOT NULL,
        phone VARCHAR(50) DEFAULT '',
        email VARCHAR(255) DEFAULT '',
        contact_type VARCHAR(50) NOT NULL,
        address VARCHAR(255) DEFAULT '',
        gst_no VARCHAR(50) DEFAULT '',
        created_at DATETIME NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        id INT AUTO_INCREMENT PRIMARY KEY,
        shop_id INT NOT NULL,
        brand VARCHAR(150) NOT NULL,
        product_type VARCHAR(150) NOT NULL,
        model_no VARCHAR(150) NOT NULL DEFAULT '',
        quantity INT NOT NULL DEFAULT 0,
        purchase_price DECIMAL(12,2),
        sale_price DECIMAL(12,2),
        category VARCHAR(100),
        min_stock_alert INT DEFAULT 5,
        updated_at DATETIME NOT NULL,
        UNIQUE KEY uq_product (shop_id, brand, product_type, model_no),
        FOREIGN KEY (shop_id) REFERENCES shops(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory_log (
        id INT AUTO_INCREMENT PRIMARY KEY,
        shop_id INT NOT NULL,
        product_id INT NOT NULL,
        brand VARCHAR(150) NOT NULL,
        product_type VARCHAR(150) NOT NULL,
        model_no VARCHAR(150) NOT NULL DEFAULT '',
        quantity_added INT NOT NULL,
        purchase_price DECIMAL(12,2),
        supplier_id INT,
        supplier_name VARCHAR(255) DEFAULT '',
        payment_mode VARCHAR(50) DEFAULT 'cash',
        invoice_no VARCHAR(100) DEFAULT '',
        invoice_date VARCHAR(50) DEFAULT '',
        invoice_total DECIMAL(12,2),
        invoice_notes VARCHAR(500) DEFAULT '',
        raw_transcript TEXT,
        added_by INT,
        created_at DATETIME NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (supplier_id) REFERENCES contacts(id),
        FOREIGN KEY (added_by) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS sales (
        id INT AUTO_INCREMENT PRIMARY KEY,
        shop_id INT NOT NULL,
        product_id INT NOT NULL,
        quantity_sold INT NOT NULL,
        sale_price DECIMAL(12,2),
        total_amount DECIMAL(12,2),
        sale_type VARCHAR(20) DEFAULT 'retail',
        contact_id INT,
        contact_name VARCHAR(255) DEFAULT '',
        payment_mode VARCHAR(50) DEFAULT 'cash',
        notes VARCHAR(500) DEFAULT '',
        sold_by INT,
        created_at DATETIME NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (contact_id) REFERENCES contacts(id),
        FOREIGN KEY (sold_by) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS sale_bills (
        id INT AUTO_INCREMENT PRIMARY KEY,
        shop_id INT NOT NULL,
        bill_no VARCHAR(100) NOT NULL,
        sale_id INT NOT NULL,
        product_id INT NOT NULL,
        brand VARCHAR(150), product_type VARCHAR(150), model_no VARCHAR(150),
        quantity INT, sale_price DECIMAL(12,2), total_amount DECIMAL(12,2),
        buyer_name VARCHAR(255) DEFAULT '', buyer_phone VARCHAR(50) DEFAULT '',
        payment_mode VARCHAR(50) DEFAULT 'cash',
        sale_type VARCHAR(20) DEFAULT 'retail',
        notes VARCHAR(500) DEFAULT '',
        created_by INT,
        created_at DATETIME NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops(id),
        FOREIGN KEY (sale_id) REFERENCES sales(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (created_by) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
]


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    for stmt in _SCHEMA_STATEMENTS:
        cur.execute(stmt)

    # Seed superadmin if not exists
    cur.execute("SELECT id FROM users WHERE role='superadmin'")
    existing = cur.fetchone()
    if not existing:
        salt = secrets.token_hex(16)
        pw_hash = _hash_password("admin123", salt)
        cur.execute(
            "INSERT INTO users (username, password_hash, salt, full_name, role, shop_id, created_at) "
            "VALUES (%s, %s, %s, %s, 'superadmin', NULL, %s)",
            ("superadmin", pw_hash, salt, "Super Admin", _now())
        )

    conn.commit()
    cur.close()
    conn.close()


# ============ Auth helpers ============

def _hash_password(password, salt):
    return hashlib.sha256((salt + password).encode()).hexdigest()


def create_shop(name, address="", phone="", created_by=None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO shops (name, address, phone, created_by, created_at) VALUES (%s,%s,%s,%s,%s)",
        (name, address, phone, created_by, _now())
    )
    shop_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return shop_id


def register_user(username, password, full_name, role, shop_id=None):
    conn = get_connection()
    cur = conn.cursor()
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, salt, full_name, role, shop_id, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (username, pw_hash, salt, full_name, role, shop_id, _now())
        )
        user_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()
        return user_id
    except mysql_errors.IntegrityError:
        cur.close()
        conn.close()
        return None  # username taken


def authenticate_user(username, password):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE username=%s AND is_active=1", (username,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    if not user:
        return None
    expected = _hash_password(password, user["salt"])
    if expected == user["password_hash"]:
        return user
    return None


def get_user_by_id(user_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_users_by_shop(shop_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, username, full_name, role, is_active, created_at FROM users WHERE shop_id=%s ORDER BY created_at",
        (shop_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_all_shops():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM shops ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_shop_by_id(shop_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM shops WHERE id=%s", (shop_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def toggle_user_active(user_id, active):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_active=%s WHERE id=%s", (1 if active else 0, user_id))
    conn.commit()
    cur.close()
    conn.close()


def get_all_users():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT u.id, u.username, u.full_name, u.role, u.is_active, u.created_at, u.shop_id, s.name as shop_name "
        "FROM users u LEFT JOIN shops s ON u.shop_id=s.id ORDER BY u.created_at DESC"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


# ============ Products ============

def _normalize(value):
    return (value or "").strip().lower()


def find_product(cur, shop_id, brand, product_type, model_no):
    """NOTE: takes an open cursor (dictionary=True), unlike the SQLite version
    which took a connection — MySQL queries need an explicit cursor."""
    cur.execute(
        "SELECT * FROM products WHERE shop_id=%s AND LOWER(brand)=%s AND LOWER(product_type)=%s AND LOWER(model_no)=%s",
        (shop_id, _normalize(brand), _normalize(product_type), _normalize(model_no)),
    )
    return cur.fetchone()


def upsert_product_and_log(item, shop_id, user_id=None, raw_transcript="",
                           supplier_id=None, supplier_name="", payment_mode="cash", invoice_no="",
                           invoice_date="", invoice_total=None, invoice_notes=""):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    now = _now()

    brand = item.get("brand", "Unknown")
    product_type = item.get("product_type", "Unknown")
    model_no = item.get("model_no", "") or ""
    quantity = int(item.get("quantity", 0))
    # supports both old key (voice flow sends price_per_unit) and new keys
    purchase_price = item.get("purchase_price") or item.get("price_per_unit")
    sale_price = item.get("sale_price")
    category = item.get("category", "Uncategorized")

    existing = find_product(cur, shop_id, brand, product_type, model_no)

    if existing:
        new_qty = existing["quantity"] + quantity
        cur.execute(
            "UPDATE products SET quantity=%s, updated_at=%s WHERE id=%s",
            (new_qty, now, existing["id"]),
        )
        # Only update prices if new values provided (don't overwrite with None)
        if purchase_price is not None:
            cur.execute("UPDATE products SET purchase_price=%s WHERE id=%s", (purchase_price, existing["id"]))
        if sale_price is not None:
            cur.execute("UPDATE products SET sale_price=%s WHERE id=%s", (sale_price, existing["id"]))
        if category:
            cur.execute("UPDATE products SET category=%s WHERE id=%s", (category, existing["id"]))
        product_id = existing["id"]
    else:
        cur.execute(
            "INSERT INTO products (shop_id, brand, product_type, model_no, quantity, purchase_price, sale_price, category, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (shop_id, brand, product_type, model_no, quantity, purchase_price, sale_price, category, now),
        )
        product_id = cur.lastrowid

    cur.execute(
        "INSERT INTO inventory_log (shop_id, product_id, brand, product_type, model_no, quantity_added, "
        "purchase_price, supplier_id, supplier_name, payment_mode, invoice_no, "
        "invoice_date, invoice_total, invoice_notes, "
        "raw_transcript, added_by, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (shop_id, product_id, brand, product_type, model_no, quantity,
         purchase_price, supplier_id, supplier_name, payment_mode, invoice_no,
         invoice_date, invoice_total, invoice_notes,
         raw_transcript, user_id, now),
    )
    conn.commit()
    cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_all_products(shop_id=None):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    if shop_id:
        cur.execute("SELECT * FROM products WHERE shop_id=%s ORDER BY updated_at DESC", (shop_id,))
    else:
        cur.execute("SELECT * FROM products ORDER BY updated_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def update_product_field(product_id, field, value):
    allowed = {"brand", "product_type", "model_no", "quantity",
               "purchase_price", "sale_price", "category", "min_stock_alert"}

    # supplier products table me nahi hota — inventory_log ki latest row me hota hai
    if field == "supplier":
        return update_product_supplier(product_id, value)

    if field not in allowed:
        raise ValueError(f"Field '{field}' editable nahi hai")
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        f"UPDATE products SET {field}=%s, updated_at=%s WHERE id=%s",
        (value, _now(), product_id),
    )
    conn.commit()
    cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def update_product_supplier(product_id, supplier_name):
    """Product ki latest inventory_log row ka supplier update karta hai.
    Agar contacts me wahi supplier hai to link karta hai, warna naya bana deta hai."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    # product ka shop_id chahiye (supplier shop ke andar hota hai)
    cur.execute("SELECT shop_id FROM products WHERE id=%s", (product_id,))
    prod = cur.fetchone()
    if not prod:
        cur.close()
        conn.close()
        raise ValueError("Product not found")
    shop_id = prod["shop_id"]

    # is product ki sabse nayi stock-IN row dhoondo
    cur.execute(
        "SELECT id FROM inventory_log WHERE product_id=%s ORDER BY id DESC LIMIT 1",
        (product_id,)
    )
    latest = cur.fetchone()
    cur.close()
    conn.close()  # auto_add_supplier apna connection khud kholta hai

    supplier_name = (supplier_name or "").strip()
    supplier_id = None
    if supplier_name:
        # contacts me match karo ya naya supplier bana do
        supplier_id, supplier_name = auto_add_supplier(shop_id, supplier_name)

    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    if latest:
        # latest IN row ka supplier update — yahi row last_supplier me dikhti hai
        cur.execute(
            "UPDATE inventory_log SET supplier_id=%s, supplier_name=%s WHERE id=%s",
            (supplier_id, supplier_name, latest["id"])
        )
    else:
        # is product ka koi inventory_log nahi (rare) — ek placeholder IN row bana do
        cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
        p = cur.fetchone()
        cur.execute(
            "INSERT INTO inventory_log (shop_id, product_id, brand, product_type, model_no, "
            "quantity_added, supplier_id, supplier_name, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (shop_id, product_id, p["brand"], p["product_type"], p["model_no"],
             0, supplier_id, supplier_name, _now())
        )
    conn.commit()
    cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def delete_product(product_id):
    conn = get_connection()
    cur = conn.cursor()
    # Pehle related records delete karo (foreign key constraint)
    cur.execute("DELETE FROM sale_bills WHERE product_id=%s", (product_id,))
    cur.execute("DELETE FROM sales WHERE product_id=%s", (product_id,))
    cur.execute("DELETE FROM inventory_log WHERE product_id=%s", (product_id,))
    cur.execute("DELETE FROM products WHERE id=%s", (product_id,))
    conn.commit()
    cur.close()
    conn.close()


def get_log_history(shop_id=None, limit=100):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    if shop_id:
        cur.execute(
            "SELECT l.*, u.full_name as added_by_name, c.name as sup_display "
            "FROM inventory_log l "
            "LEFT JOIN users u ON l.added_by=u.id "
            "LEFT JOIN contacts c ON l.supplier_id=c.id "
            "WHERE l.shop_id=%s ORDER BY l.id DESC LIMIT %s",
            (shop_id, limit)
        )
    else:
        cur.execute(
            "SELECT l.*, u.full_name as added_by_name, c.name as sup_display "
            "FROM inventory_log l "
            "LEFT JOIN users u ON l.added_by=u.id "
            "LEFT JOIN contacts c ON l.supplier_id=c.id "
            "ORDER BY l.id DESC LIMIT %s",
            (limit,)
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


# ============ Sales ============

def record_sale(shop_id, product_id, quantity_sold, sale_price,
                sale_type="retail",
                contact_id=None, contact_name="", payment_mode="cash", notes="", sold_by=None):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    now = _now()
    total = (sale_price or 0) * quantity_sold

    # Reduce product quantity
    cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
    product = cur.fetchone()
    if not product:
        cur.close()
        conn.close()
        raise ValueError("Product not found")
    new_qty = product["quantity"] - quantity_sold
    if new_qty < 0:
        cur.close()
        conn.close()
        raise ValueError(f"Stock kam hai! Sirf {product['quantity']} available")

    cur.execute("UPDATE products SET quantity=%s, updated_at=%s WHERE id=%s", (new_qty, now, product_id))

    cur.execute(
        "INSERT INTO sales (shop_id, product_id, quantity_sold, sale_price, total_amount, "
        "sale_type, contact_id, contact_name, payment_mode, notes, sold_by, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (shop_id, product_id, quantity_sold, sale_price, total,
         sale_type, contact_id, contact_name, payment_mode, notes, sold_by, now)
    )
    sale_id = cur.lastrowid
    conn.commit()

    cur.execute("SELECT * FROM sales WHERE id=%s", (sale_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_sales(shop_id=None, limit=200):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    if shop_id:
        cur.execute(
            "SELECT s.*, p.brand, p.product_type, p.model_no, p.category, "
            "p.purchase_price as cost_price, "
            "u.full_name as sold_by_name, c.name as contact_display_name "
            "FROM sales s "
            "JOIN products p ON s.product_id=p.id "
            "LEFT JOIN users u ON s.sold_by=u.id "
            "LEFT JOIN contacts c ON s.contact_id=c.id "
            "WHERE s.shop_id=%s ORDER BY s.created_at DESC LIMIT %s",
            (shop_id, limit)
        )
    else:
        cur.execute(
            "SELECT s.*, p.brand, p.product_type, p.model_no, p.category, "
            "p.purchase_price as cost_price, "
            "u.full_name as sold_by_name, c.name as contact_display_name "
            "FROM sales s "
            "JOIN products p ON s.product_id=p.id "
            "LEFT JOIN users u ON s.sold_by=u.id "
            "LEFT JOIN contacts c ON s.contact_id=c.id "
            "ORDER BY s.created_at DESC LIMIT %s",
            (limit,)
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_sales_trend(shop_id, product_id=None, days=30):
    """Daily sales quantities for trend chart."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    query = """
        SELECT DATE(created_at) as sale_date, SUM(quantity_sold) as total_qty,
               SUM(total_amount) as total_revenue
        FROM sales WHERE shop_id=%s
    """
    params = [shop_id]
    if product_id:
        query += " AND product_id=%s"
        params.append(product_id)
    query += f" AND created_at >= DATE_SUB(NOW(), INTERVAL {int(days)} DAY) GROUP BY DATE(created_at) ORDER BY sale_date"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_stock_movements(shop_id, date_from=None, date_to=None):
    """Combined IN (inventory_log) and OUT (sales) movements."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    q_in = """
        SELECT l.created_at, 'IN' as direction, l.brand, l.product_type, l.model_no,
               l.quantity_added as quantity, l.purchase_price as price,
               u.full_name as done_by,
               COALESCE(c.name, l.supplier_name) as contact_name,
               l.payment_mode
        FROM inventory_log l
        LEFT JOIN users u ON l.added_by=u.id
        LEFT JOIN contacts c ON l.supplier_id=c.id
        WHERE l.shop_id=%s
    """
    params_in = [shop_id]
    if date_from:
        q_in += " AND DATE(l.created_at) >= %s"
        params_in.append(date_from)
    if date_to:
        q_in += " AND DATE(l.created_at) <= %s"
        params_in.append(date_to)

    q_out = """
        SELECT s.created_at, 'OUT' as direction, p.brand, p.product_type, p.model_no,
               s.quantity_sold as quantity, s.sale_price as price,
               u.full_name as done_by, COALESCE(c.name, s.contact_name) as contact_name,
               s.payment_mode
        FROM sales s
        JOIN products p ON s.product_id=p.id
        LEFT JOIN users u ON s.sold_by=u.id
        LEFT JOIN contacts c ON s.contact_id=c.id
        WHERE s.shop_id=%s
    """
    params_out = [shop_id]
    if date_from:
        q_out += " AND DATE(s.created_at) >= %s"
        params_out.append(date_from)
    if date_to:
        q_out += " AND DATE(s.created_at) <= %s"
        params_out.append(date_to)

    cur.execute(q_in, params_in)
    rows_in = cur.fetchall()
    cur.execute(q_out, params_out)
    rows_out = cur.fetchall()
    cur.close()
    conn.close()

    movements = rows_in + rows_out
    movements.sort(key=lambda x: x["created_at"], reverse=True)
    return movements


# ============ Contacts ============

def add_contact(shop_id, name, phone="", email="", contact_type="customer", address="",
                gst_no=""):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO contacts (shop_id, name, phone, email, contact_type, address, gst_no, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (shop_id, name, phone, email, contact_type, address, gst_no, _now())
    )
    cid = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return cid


def get_contacts(shop_id, contact_type=None):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    if contact_type:
        cur.execute(
            "SELECT * FROM contacts WHERE shop_id=%s AND contact_type=%s ORDER BY name", (shop_id, contact_type)
        )
    else:
        cur.execute("SELECT * FROM contacts WHERE shop_id=%s ORDER BY contact_type, name", (shop_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def delete_contact(contact_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM contacts WHERE id=%s", (contact_id,))
    conn.commit()
    cur.close()
    conn.close()


# ============ Dashboard stats ============

def get_dashboard_stats(shop_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) as c FROM products WHERE shop_id=%s", (shop_id,))
    total_products = cur.fetchone()["c"]

    cur.execute("SELECT COALESCE(SUM(quantity),0) as c FROM products WHERE shop_id=%s", (shop_id,))
    total_qty = cur.fetchone()["c"]

    cur.execute(
        "SELECT COUNT(*) as c FROM products WHERE shop_id=%s AND quantity < min_stock_alert", (shop_id,)
    )
    low_stock = cur.fetchone()["c"]

    cur.execute(
        "SELECT COALESCE(SUM(total_amount),0) as revenue, COALESCE(SUM(quantity_sold),0) as units "
        "FROM sales WHERE shop_id=%s AND DATE(created_at)=CURDATE()", (shop_id,)
    )
    today_sales = cur.fetchone()

    cur.execute(
        "SELECT COALESCE(SUM(quantity * COALESCE(purchase_price,0)),0) as val FROM products WHERE shop_id=%s", (shop_id,)
    )
    stock_cost_value = cur.fetchone()["val"]

    cur.execute(
        "SELECT COALESCE(SUM(quantity_added * COALESCE(purchase_price,0)),0) as c "
        "FROM inventory_log WHERE shop_id=%s", (shop_id,)
    )
    total_purchased = cur.fetchone()["c"]

    cur.execute(
        "SELECT COALESCE(SUM(total_amount),0) as c FROM sales WHERE shop_id=%s", (shop_id,)
    )
    total_sold_revenue = cur.fetchone()["c"]

    cur.close()
    conn.close()
    return {
        "total_products": total_products,
        "total_quantity": total_qty,
        "low_stock_alerts": low_stock,
        "today_revenue": today_sales["revenue"],
        "today_units_sold": today_sales["units"],
        "stock_cost_value": round(stock_cost_value),
        "total_purchased": round(total_purchased),
        "total_sold_revenue": round(total_sold_revenue),
        "total_profit": round(total_sold_revenue - total_purchased),
    }


def get_low_stock_products(shop_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM products WHERE shop_id=%s AND quantity < min_stock_alert ORDER BY quantity ASC",
        (shop_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


# ============ Supplier matching from voice ============

def match_supplier(shop_id, spoken_name):
    """Try to match a spoken supplier name to existing contacts.
    Returns (supplier_id, matched_name) or (None, spoken_name)."""
    if not spoken_name:
        return None, ""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    spoken_lower = spoken_name.strip().lower()
    cur.execute(
        "SELECT id, name FROM contacts WHERE shop_id=%s AND contact_type='supplier'", (shop_id,)
    )
    suppliers = cur.fetchall()
    cur.close()
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


# ============ Products with supplier & invoice details ============

def get_products_with_details(shop_id):
    """Products with latest supplier name and invoice info from inventory_log."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
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
        FROM products p WHERE p.shop_id=%s ORDER BY p.updated_at DESC
    """, (shop_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_product_full_history(product_id):
    """Full IN + OUT history for a single product."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT l.created_at, 'IN' as direction, l.quantity_added as quantity,
               l.purchase_price as price, COALESCE(c.name, l.supplier_name) as contact,
               l.payment_mode, l.invoice_no, l.invoice_date, l.invoice_total,
               u.full_name as done_by
        FROM inventory_log l
        LEFT JOIN users u ON l.added_by=u.id
        LEFT JOIN contacts c ON l.supplier_id=c.id
        WHERE l.product_id=%s
    """, (product_id,))
    ins = cur.fetchall()
    cur.execute("""
        SELECT s.created_at, 'OUT' as direction, s.quantity_sold as quantity,
               s.sale_price as price, COALESCE(c.name, s.contact_name) as contact,
               s.payment_mode, '' as invoice_no, '' as invoice_date, NULL as invoice_total,
               u.full_name as done_by
        FROM sales s
        LEFT JOIN users u ON s.sold_by=u.id
        LEFT JOIN contacts c ON s.contact_id=c.id
        WHERE s.product_id=%s
    """, (product_id,))
    outs = cur.fetchall()
    cur.close()
    conn.close()
    history = ins + outs
    history.sort(key=lambda x: x["created_at"], reverse=True)
    return history


# ============ Monthly stock view ============

def get_monthly_stock(shop_id, year_month=None):
    """Get stock IN entries for a given month (YYYY-MM). Defaults to current month."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    if not year_month:
        year_month = datetime.utcnow().strftime("%Y-%m")
    cur.execute(
        "SELECT l.*, u.full_name as added_by_name, "
        "COALESCE(c.name, l.supplier_name) as supplier_display "
        "FROM inventory_log l "
        "LEFT JOIN users u ON l.added_by=u.id "
        "LEFT JOIN contacts c ON l.supplier_id=c.id "
        "WHERE l.shop_id=%s AND DATE_FORMAT(l.created_at, '%%Y-%%m')=%s "
        "ORDER BY l.created_at DESC",
        (shop_id, year_month)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_available_months(shop_id):
    """Get list of months that have stock entries."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT DISTINCT DATE_FORMAT(created_at, '%%Y-%%m') as month "
        "FROM inventory_log WHERE shop_id=%s ORDER BY month DESC",
        (shop_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r["month"] for r in rows]


# ============ Sale bill generation ============

def generate_sale_bill(shop_id, sale_id, user_id=None):
    """Auto-generate a bill for a sale. Returns bill dict."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT s.*, p.brand, p.product_type, p.model_no, p.category,
               COALESCE(c.name, s.contact_name) as buyer_name,
               COALESCE(c.phone, '') as buyer_phone
        FROM sales s
        JOIN products p ON s.product_id=p.id
        LEFT JOIN contacts c ON s.contact_id=c.id
        WHERE s.id=%s
    """, (sale_id,))
    sale = cur.fetchone()
    if not sale:
        cur.close()
        conn.close()
        return None

    # Generate bill number: BILL-SHOPID-SALEID
    bill_no = f"BILL-{shop_id}-{sale_id}"
    now = _now()

    cur.execute("""
        INSERT INTO sale_bills (shop_id, bill_no, sale_id, product_id, brand, product_type,
            model_no, quantity, sale_price, total_amount, buyer_name, buyer_phone,
            payment_mode, sale_type, notes, created_by, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (shop_id, bill_no, sale_id, sale["product_id"], sale["brand"],
          sale["product_type"], sale["model_no"], sale["quantity_sold"],
          sale["sale_price"], sale["total_amount"], sale["buyer_name"],
          sale["buyer_phone"], sale["payment_mode"], sale.get("sale_type", "retail"),
          sale["notes"], user_id, now))
    bill_id = cur.lastrowid
    conn.commit()
    cur.execute("SELECT * FROM sale_bills WHERE id=%s", (bill_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_sale_bill(bill_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM sale_bills WHERE id=%s", (bill_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_sale_bills(shop_id, limit=100):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM sale_bills WHERE shop_id=%s ORDER BY created_at DESC LIMIT %s",
        (shop_id, limit)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_bill_by_sale(sale_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM sale_bills WHERE sale_id=%s", (sale_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


# ============ Auto-add supplier from stock form ============

def auto_add_supplier(shop_id, supplier_name):
    """If supplier_name doesn't exist in contacts, create it. Returns (supplier_id, name)."""
    if not supplier_name or not supplier_name.strip():
        return None, ""
    name = supplier_name.strip()
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    # Check if already exists (case-insensitive)
    cur.execute(
        "SELECT id, name FROM contacts WHERE shop_id=%s AND contact_type='supplier' AND LOWER(name)=%s",
        (shop_id, name.lower())
    )
    existing = cur.fetchone()
    if existing:
        cur.close()
        conn.close()
        return existing["id"], existing["name"]
    # Create new supplier
    cur.execute(
        "INSERT INTO contacts (shop_id, name, phone, email, contact_type, address, gst_no, created_at) "
        "VALUES (%s,%s,'','','supplier','','',%s)", (shop_id, name, _now()))
    sid = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return sid, name


# ============ Update contact (edit) ============

def update_contact(contact_id, data):
    """Update contact fields. data is dict of field:value."""
    allowed = {"name", "phone", "email", "contact_type", "address", "gst_no"}
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    for field, value in data.items():
        if field in allowed:
            cur.execute(f"UPDATE contacts SET {field}=%s WHERE id=%s", (value, contact_id))
    conn.commit()
    cur.execute("SELECT * FROM contacts WHERE id=%s", (contact_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


# ============ Monthly sales (OUT) ============

def get_monthly_sales(shop_id, year_month=None):
    """Sales for a given month."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    if not year_month:
        year_month = datetime.utcnow().strftime("%Y-%m")
    cur.execute(
        "SELECT s.*, p.brand, p.product_type, p.model_no, p.category, "
        "u.full_name as sold_by_name, COALESCE(c.name, s.contact_name) as buyer_display "
        "FROM sales s JOIN products p ON s.product_id=p.id "
        "LEFT JOIN users u ON s.sold_by=u.id "
        "LEFT JOIN contacts c ON s.contact_id=c.id "
        "WHERE s.shop_id=%s AND DATE_FORMAT(s.created_at, '%%Y-%%m')=%s "
        "ORDER BY s.created_at DESC",
        (shop_id, year_month)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


# ============ Monthly summary stats ============

def get_monthly_summary(shop_id, year_month=None):
    """Summary stats for a month: total IN, total OUT, revenue, cost."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    if not year_month:
        year_month = datetime.utcnow().strftime("%Y-%m")
    cur.execute(
        "SELECT COALESCE(SUM(quantity_added),0) as qty, COALESCE(SUM(quantity_added*COALESCE(purchase_price,0)),0) as cost "
        "FROM inventory_log WHERE shop_id=%s AND DATE_FORMAT(created_at, '%%Y-%%m')=%s",
        (shop_id, year_month)
    )
    stock_in = cur.fetchone()
    cur.execute(
        "SELECT COALESCE(SUM(quantity_sold),0) as qty, COALESCE(SUM(total_amount),0) as revenue "
        "FROM sales WHERE shop_id=%s AND DATE_FORMAT(created_at, '%%Y-%%m')=%s",
        (shop_id, year_month)
    )
    stock_out = cur.fetchone()
    cur.close()
    conn.close()
    return {
        "in_qty": stock_in["qty"], "in_cost": round(stock_in["cost"]),
        "out_qty": stock_out["qty"], "out_revenue": round(stock_out["revenue"]),
        "profit": round(stock_out["revenue"] - stock_in["cost"]),
    }