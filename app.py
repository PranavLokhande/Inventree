"""
Inventory AI — Full Server with Auth, RBAC, Sales, Contacts, AI Alerts.

Roles:
  superadmin  — developer, sees all shops & users
  admin       — shop owner/manager, full control of their shop
  employee    — stock add/view + sales for their shop

Flow:
  1. Admin registers shop + self -> gets admin role
  2. Admin registers employees -> they get employee role
  3. Everyone logs in -> sees role-appropriate dashboard

DISTRIBUTOR MODEL (NEW):
  - Buy stock FROM suppliers/dealers → inventory_log (stock IN)
  - Sell stock TO customers or retailers (small shopkeepers) → sales (stock OUT)
  - Track purchase_price vs sale_price → profit calculation
"""
from flask import Flask, request, jsonify, render_template, redirect
import os
import uuid
import base64
import functools
import requests
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_socketio import SocketIO

import db
import groq_ai

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "inventory-ai-secret-key-2024")
socketio = SocketIO(app, cors_allowed_origins="*")

SESSIONS_VOICE = {}  # voice intake sessions


# ============ Auth Middleware ============
# (NO CHANGE — same as before)

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Login required", "redirect": "/login.html"}), 401
            return redirect("/login.html")
        user = db.get_user_by_id(user_id)
        if not user or not user["is_active"]:
            session.clear()
            return jsonify({"error": "Account disabled"}), 403
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            user = getattr(request, 'current_user', None)
            if not user or user["role"] not in roles:
                return jsonify({"error": "Permission denied"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def get_shop_id():
    """Get shop_id from current user."""
    user = request.current_user
    if user["role"] == "superadmin":
        return request.args.get("shop_id", type=int) or request.form.get("shop_id", type=int)
    return user["shop_id"]


# ============ Page Routes ============
# (NO CHANGE — same as before)

@app.route("/")
def index():
    if session.get("user_id"):
        return render_template("/dashboard.html")
    return render_template("/login.html")


@app.route("/login.html")
def login_page():
    return render_template("login.html")


@app.route("/dashboard.html")
@login_required
def dashboard_page():
    return render_template("dashboard.html")


# ============ Auth API ============
# (NO CHANGE — same as before)

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")

    user = db.authenticate_user(username, password)
    if not user:
        return jsonify({"error": "Invalid username or password"}), 401

    session["user_id"] = user["id"]
    return jsonify({
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"],
            "shop_id": user["shop_id"],
        }
    })


@app.route("/api/auth/register-shop", methods=["POST"])
def api_register_shop():
    """Admin registers a new shop AND their own admin account."""
    data = request.get_json(force=True)
    shop_name = data.get("shop_name", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    full_name = data.get("full_name", "").strip()
    address = data.get("address", "")
    phone = data.get("phone", "")

    if not all([shop_name, username, password, full_name]):
        return jsonify({"error": "All fields are required"}), 400
    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters"}), 400

    shop_id = db.create_shop(shop_name, address, phone)
    user_id = db.register_user(username, password, full_name, "admin", shop_id)
    if not user_id:
        return jsonify({"error": "Username already taken"}), 409

    conn = db.get_connection()
    conn.execute("UPDATE shops SET created_by=? WHERE id=?", (user_id, shop_id))
    conn.commit()
    conn.close()

    session["user_id"] = user_id
    return jsonify({"success": True, "user_id": user_id, "shop_id": shop_id})


@app.route("/api/auth/register-employee", methods=["POST"])
@login_required
@role_required("admin", "superadmin")
def api_register_employee():
    """Admin registers an employee for their shop."""
    data = request.get_json(force=True)
    user = request.current_user
    shop_id = user["shop_id"] if user["role"] == "admin" else data.get("shop_id")

    username = data.get("username", "").strip()
    password = data.get("password", "")
    full_name = data.get("full_name", "").strip()
    role = data.get("role", "employee")

    if role not in ("employee", "admin"):
        return jsonify({"error": "Invalid role"}), 400
    if not all([username, password, full_name]):
        return jsonify({"error": "All fields required"}), 400

    user_id = db.register_user(username, password, full_name, role, shop_id)
    if not user_id:
        return jsonify({"error": "Username already taken"}), 409

    return jsonify({"success": True, "user_id": user_id})


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/auth/me", methods=["GET"])
@login_required
def api_me():
    user = request.current_user
    shop = db.get_shop_by_id(user["shop_id"]) if user["shop_id"] else None
    return jsonify({
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"],
            "shop_id": user["shop_id"],
        },
        "shop": shop,
    })


# ============ Users management ============
# (NO CHANGE — same as before)

@app.route("/api/users", methods=["GET"])
@login_required
def api_get_users():
    user = request.current_user
    if user["role"] == "superadmin":
        return jsonify(db.get_all_users())
    elif user["role"] == "admin":
        return jsonify(db.get_users_by_shop(user["shop_id"]))
    return jsonify([])


@app.route("/api/users/<int:uid>/toggle", methods=["POST"])
@login_required
@role_required("admin", "superadmin")
def api_toggle_user(uid):
    data = request.get_json(force=True)
    db.toggle_user_active(uid, data.get("active", True))
    return jsonify({"success": True})


# ============ Dashboard Stats ============
# (NO CHANGE in app.py — db.py returns new profit fields automatically)

@app.route("/api/dashboard/stats", methods=["GET"])
@login_required
def api_dashboard_stats():
    shop_id = get_shop_id()
    if not shop_id:
        return jsonify({"error": "No shop selected"}), 400
    return jsonify(db.get_dashboard_stats(shop_id))


@app.route("/api/dashboard/low-stock", methods=["GET"])
@login_required
def api_low_stock():
    shop_id = get_shop_id()
    if not shop_id:
        return jsonify([])
    return jsonify(db.get_low_stock_products(shop_id))


@app.route("/api/dashboard/ai-alerts", methods=["GET"])
@login_required
def api_ai_alerts():
    shop_id = get_shop_id()
    if not shop_id:
        return jsonify([])
    products = db.get_all_products(shop_id)
    sales = db.get_sales(shop_id, limit=50)
    sales_summary = [{"brand": s["brand"], "product_type": s["product_type"],
                       "qty": s["quantity_sold"], "date": s["created_at"]} for s in sales[:30]]
    alerts = groq_ai.get_ai_stock_alerts(products, sales_summary)
    return jsonify(alerts)


# ============ Products ============

@app.route("/api/products", methods=["GET"])
@login_required
def api_get_products():
    """Returns products with supplier info. ?detailed=1 for supplier+invoice data."""
    user = request.current_user
    detailed = request.args.get("detailed")
    if user["role"] == "superadmin":
        shop_id = request.args.get("shop_id", type=int)
    else:
        shop_id = user["shop_id"]
    if detailed and shop_id:
        return jsonify(db.get_products_with_details(shop_id))
    return jsonify(db.get_all_products(shop_id))


# ██████████████████████████████████████████████████████████████
# ██  UPDATED: manual-add now accepts distributor fields      ██
# ██  OLD: price_per_unit (single price)                      ██
# ██  NEW: purchase_price, sale_price, supplier_id,           ██
# ██       supplier_name, payment_mode, invoice_no            ██
# ██████████████████████████████████████████████████████████████
@app.route("/api/products/manual-add", methods=["POST"])
@login_required
def api_manual_add():
    """Manual stock add from dashboard form — now with supplier & pricing."""
    user = request.current_user
    shop_id = user["shop_id"]
    if not shop_id:
        return jsonify({"error": "No shop assigned"}), 400

    item = {
        "brand": request.form.get("brand", "").strip(),
        "product_type": request.form.get("product_type", "").strip(),
        "model_no": request.form.get("model_no", "").strip(),
        "quantity": int(request.form.get("quantity", 0)),
        # ── CHANGED: was single "price_per_unit", now split into two ──
        "purchase_price": float(request.form.get("purchase_price") or 0) or None,
        "sale_price": float(request.form.get("sale_price") or 0) or None,
        "category": request.form.get("category", "Uncategorized").strip(),
    }

    if not item["brand"] or not item["product_type"] or not item["quantity"]:
        return jsonify({"error": "Brand, Product Type & Quantity required"}), 400

    # ── NEW: supplier & payment tracking ──
    supplier_id = request.form.get("supplier_id") or None
    if supplier_id:
        supplier_id = int(supplier_id)
    supplier_name = request.form.get("supplier_name", "").strip()
    
    # ── NEW: auto-add supplier if new name given and no dropdown selected ──
    if not supplier_id and supplier_name:
        supplier_id, supplier_name = db.auto_add_supplier(shop_id, supplier_name)
    
    payment_mode = request.form.get("payment_mode", "cash")
    invoice_no = request.form.get("invoice_no", "").strip()
    # ── NEW: invoice detail fields ──
    invoice_date = request.form.get("invoice_date", "").strip()
    invoice_total = request.form.get("invoice_total") or None
    if invoice_total:
        invoice_total = float(invoice_total)
    invoice_notes = request.form.get("invoice_notes", "").strip()

    # ── CHANGED: pass new params to db function ──
    saved = db.upsert_product_and_log(
        item, shop_id, user["id"], "Manual entry",
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        payment_mode=payment_mode,
        invoice_no=invoice_no,
        invoice_date=invoice_date,
        invoice_total=invoice_total,
        invoice_notes=invoice_notes,
    )
    broadcast_inventory_update()
    return jsonify(saved)


@app.route("/api/products/<int:product_id>", methods=["PATCH"])
@login_required
@role_required("admin", "superadmin")
def api_edit_product(product_id):
    # (NO CHANGE here — db.update_product_field handles new allowed fields internally)
    data = request.get_json(force=True)
    field = data.get("field")
    value = data.get("value")
    print("hello-->",field, value)
    try:
        updated = db.update_product_field(product_id, field, value)
        broadcast_inventory_update()
        return jsonify(updated)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
@login_required
@role_required("admin", "superadmin")
def api_delete_product(product_id):
    # (NO CHANGE)
    db.delete_product(product_id)
    broadcast_inventory_update()
    return jsonify({"deleted": product_id})


# ============ Voice Intake ============
# (NO CHANGE in app.py — db.upsert_product_and_log has default
#  values for new params, so voice flow keeps working as-is)

@app.route("/api/voice", methods=["POST"])
@login_required
def api_handle_voice():
    user = request.current_user
    shop_id = user["shop_id"]
    if not shop_id and user["role"] != "superadmin":
        return jsonify({"error": "No shop assigned"}), 400

    if "audio" not in request.files:
        return jsonify({"error": "Audio file missing"}), 400

    session_id = request.form.get("session_id") or str(uuid.uuid4())
    audio_file = request.files["audio"]
    file_bytes = audio_file.read()
    filename = audio_file.filename or "recording.webm"

    try:
        transcript = groq_ai.transcribe_audio(file_bytes, filename)
        if not transcript:
            return jsonify({"error": "Awaaz clear nahi aayi, ek baar aur bol do"}), 400

        items = groq_ai.extract_items(transcript)
        if not items:
            reply_text = "Koi stock item nahi mila bhai. Ek baar aur try karo!"
            audio_b64 = _safe_tts(reply_text)
            return jsonify({"session_id": session_id, "transcript": transcript,
                            "reply_text": reply_text, "reply_audio": audio_b64, "done": True, "items_found": 0})

        SESSIONS_VOICE[session_id] = {
            "queue": items, "current_index": 0, "transcript": transcript,
            "shop_id": shop_id, "user_id": user["id"],
        }

        first_item = items[0]
        reply_text = groq_ai.build_confirmation_text(first_item, 1, len(items))
        audio_b64 = _safe_tts(reply_text)

        return jsonify({
            "session_id": session_id, "transcript": transcript, "items_found": len(items),
            "current_item": first_item, "current_index": 1, "total_items": len(items),
            "reply_text": reply_text, "reply_audio": audio_b64, "done": False,
        })
    except requests.HTTPError as e:
        return jsonify({"error": f"Groq API error: {e.response.text}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reply", methods=["POST"])
@login_required
def api_handle_reply():
    session_id = request.form.get("session_id")
    voice_session = SESSIONS_VOICE.get(session_id)
    if not voice_session:
        return jsonify({"error": "Session expired! Start a new recording."}), 400

    if "audio" in request.files:
        file_bytes = request.files["audio"].read()
        filename = request.files["audio"].filename or "reply.webm"
        try:
            user_text = groq_ai.transcribe_audio(file_bytes, filename)
        except requests.HTTPError as e:
            return jsonify({"error": f"Groq API error: {e.response.text}"}), 502
    else:
        user_text = request.form.get("text", "").strip()

    if not user_text:
        return jsonify({"error": "Kuch sun nahi paaya, ek baar aur bolo!"}), 400

    queue = voice_session["queue"]
    idx = voice_session["current_index"]
    current_item = queue[idx]

    try:
        updated_item, confirmed = groq_ai.apply_correction(current_item, user_text)
        queue[idx] = updated_item

        if confirmed:
            # ── NEW: match supplier from voice to DB contacts ──
            spoken_supplier = updated_item.get("supplier_name", "")
            supplier_id, matched_name = db.match_supplier(voice_session["shop_id"], spoken_supplier)
            
            saved = db.upsert_product_and_log(
                updated_item, voice_session["shop_id"],
                voice_session["user_id"], voice_session["transcript"],
                supplier_id=supplier_id,
                supplier_name=matched_name,
            )
            broadcast_inventory_update()

            next_idx = idx + 1
            if next_idx >= len(queue):
                reply_text = f"Sab {len(queue)} items save ho gaye! Aur kuch stock aaya hai?"
                audio_b64 = _safe_tts(reply_text)
                del SESSIONS_VOICE[session_id]
                return jsonify({"user_text": user_text, "confirmed": True, "saved_item": saved,
                                "reply_text": reply_text, "reply_audio": audio_b64, "done": True})
            else:
                voice_session["current_index"] = next_idx
                next_item = queue[next_idx]
                reply_text = groq_ai.build_confirmation_text(next_item, next_idx + 1, len(queue))
                audio_b64 = _safe_tts(reply_text)
                return jsonify({"user_text": user_text, "confirmed": True, "saved_item": saved,
                                "current_item": next_item, "current_index": next_idx + 1,
                                "total_items": len(queue), "reply_text": reply_text, "reply_audio": audio_b64, "done": False})
        else:
            reply_text = groq_ai.build_confirmation_text(updated_item, idx + 1, len(queue))
            audio_b64 = _safe_tts(reply_text)
            return jsonify({"user_text": user_text, "confirmed": False, "current_item": updated_item,
                            "current_index": idx + 1, "total_items": len(queue),
                            "reply_text": reply_text, "reply_audio": audio_b64, "done": False})
    except requests.HTTPError as e:
        return jsonify({"error": f"Groq API error: {e.response.text}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _safe_tts(text):
    try:
        audio_bytes = groq_ai.text_to_speech(text)
        return base64.b64encode(audio_bytes).decode("utf-8")
    except Exception:
        return None


# ============ Sales ============

@app.route("/api/sales", methods=["GET"])
@login_required
def api_get_sales():
    # (NO CHANGE)
    user = request.current_user
    shop_id = get_shop_id() or user.get("shop_id")
    return jsonify(db.get_sales(shop_id))


# ██████████████████████████████████████████████████████████████
# ██  UPDATED: record_sale now accepts sale_type              ██
# ██  NEW FIELD: sale_type = "retail" or "wholesale"          ██
# ██████████████████████████████████████████████████████████████
@app.route("/api/sales", methods=["POST"])
@login_required
def api_record_sale():
    user = request.current_user
    shop_id = user["shop_id"]
    data = request.get_json(force=True)
    try:
        sale = db.record_sale(
            shop_id=shop_id,
            product_id=data["product_id"],
            quantity_sold=int(data["quantity"]),
            sale_price=float(data.get("sale_price") or 0),
            # ── NEW: retail (walk-in customer) or wholesale (chhota shopkeeper) ──
            sale_type=data.get("sale_type", "retail"),
            contact_id=data.get("contact_id"),
            contact_name=data.get("contact_name", ""),
            payment_mode=data.get("payment_mode", "cash"),
            notes=data.get("notes", ""),
            sold_by=user["id"],
        )
        # ── NEW: auto-generate bill ──
        bill = db.generate_sale_bill(shop_id, sale["id"], user["id"])
        sale["bill"] = bill
        broadcast_inventory_update()
        return jsonify(sale)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/sales/trends", methods=["GET"])
@login_required
def api_sales_trends():
    # (NO CHANGE)
    shop_id = get_shop_id()
    product_id = request.args.get("product_id", type=int)
    days = request.args.get("days", 30, type=int)
    return jsonify(db.get_sales_trend(shop_id, product_id, days))


# ============ Stock Movements ============
# (NO CHANGE in app.py — db.py handles supplier/buyer names internally)

@app.route("/api/movements", methods=["GET"])
@login_required
def api_movements():
    user = request.current_user
    shop_id = get_shop_id() or user.get("shop_id")
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    return jsonify(db.get_stock_movements(shop_id, date_from, date_to))


# ============ Contacts ============

@app.route("/api/contacts", methods=["GET"])
@login_required
def api_get_contacts():
    # (NO CHANGE)
    user = request.current_user
    shop_id = get_shop_id() or user.get("shop_id")
    ctype = request.args.get("type")
    return jsonify(db.get_contacts(shop_id, ctype))


# ██████████████████████████████████████████████████████████████
# ██  UPDATED: add_contact now accepts gst_no field           ██
# ██  contact_type can be: supplier, customer, retailer       ██
# ██████████████████████████████████████████████████████████████
@app.route("/api/contacts", methods=["POST"])
@login_required
def api_add_contact():
    user = request.current_user
    shop_id = user["shop_id"]
    data = request.get_json(force=True)
    cid = db.add_contact(
        shop_id,
        data["name"],
        data.get("phone", ""),
        data.get("email", ""),
        # ── CHANGED: was only "customer"/"dealer", now also "supplier"/"retailer" ──
        data.get("contact_type", "customer"),
        data.get("address", ""),
        # ── NEW: GST number for suppliers/dealers ──
        data.get("gst_no", ""),
    )
    return jsonify({"id": cid})


@app.route("/api/contacts/<int:cid>", methods=["DELETE"])
@login_required
@role_required("admin", "superadmin")
def api_delete_contact(cid):
    db.delete_contact(cid)
    return jsonify({"deleted": cid})


# ── NEW: Edit contact ──
@app.route("/api/contacts/<int:cid>", methods=["PATCH"])
@login_required
def api_edit_contact(cid):
    data = request.get_json(force=True)
    updated = db.update_contact(cid, data)
    if not updated:
        return jsonify({"error": "Contact not found"}), 404
    return jsonify(updated)


# ============ History ============

@app.route("/api/history", methods=["GET"])
@login_required
def api_get_history():
    user = request.current_user
    shop_id = get_shop_id() or user.get("shop_id")
    return jsonify(db.get_log_history(shop_id))


# ============ Product detail history ============

@app.route("/api/products/<int:pid>/history", methods=["GET"])
@login_required
def api_product_history(pid):
    return jsonify(db.get_product_full_history(pid))


# ============ Monthly stock + sales ============

@app.route("/api/stock/monthly", methods=["GET"])
@login_required
def api_monthly_stock():
    shop_id = get_shop_id()
    if not shop_id: return jsonify([])
    return jsonify(db.get_monthly_stock(shop_id, request.args.get("month")))

@app.route("/api/sales/monthly", methods=["GET"])
@login_required
def api_monthly_sales():
    shop_id = get_shop_id()
    if not shop_id: return jsonify([])
    return jsonify(db.get_monthly_sales(shop_id, request.args.get("month")))

@app.route("/api/stock/monthly-summary", methods=["GET"])
@login_required
def api_monthly_summary():
    shop_id = get_shop_id()
    if not shop_id: return jsonify({})
    return jsonify(db.get_monthly_summary(shop_id, request.args.get("month")))

@app.route("/api/stock/months", methods=["GET"])
@login_required
def api_available_months():
    shop_id = get_shop_id()
    if not shop_id: return jsonify([])
    return jsonify(db.get_available_months(shop_id))


# ============ NEW: Sale bills ============

@app.route("/api/sales/<int:sale_id>/bill", methods=["POST"])
@login_required
def api_generate_bill(sale_id):
    user = request.current_user
    # Check if bill already exists
    existing = db.get_bill_by_sale(sale_id)
    if existing:
        return jsonify(existing)
    bill = db.generate_sale_bill(user["shop_id"], sale_id, user["id"])
    if not bill:
        return jsonify({"error": "Sale not found"}), 404
    return jsonify(bill)


@app.route("/api/bills", methods=["GET"])
@login_required
def api_get_bills():
    user = request.current_user
    shop_id = get_shop_id() or user.get("shop_id")
    return jsonify(db.get_sale_bills(shop_id))


@app.route("/api/bills/<int:bill_id>", methods=["GET"])
@login_required
def api_get_bill(bill_id):
    bill = db.get_sale_bill(bill_id)
    if not bill:
        return jsonify({"error": "Bill not found"}), 404
    return jsonify(bill)


# ============ NEW: CSV Export ============

@app.route("/api/export/stock", methods=["GET"])
@login_required
def api_export_stock():
    """Export current stock as CSV download."""
    import csv
    import io
    user = request.current_user
    shop_id = get_shop_id() or user.get("shop_id")
    products = db.get_products_with_details(shop_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Brand", "Product", "Model", "Qty", "Purchase Price", "Sale Price",
                     "Category", "Supplier", "Last Invoice", "Last Stock Date", "Alert Level"])
    for p in products:
        writer.writerow([
            p["brand"], p["product_type"], p["model_no"], p["quantity"],
            p.get("purchase_price", ""), p.get("sale_price", ""),
            p.get("category", ""), p.get("supplier_name", ""),
            p.get("last_invoice_no", ""), p.get("last_stock_date", ""),
            p.get("min_stock_alert", 5),
        ])

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=stock_export.csv"}
    )


@app.route("/api/export/monthly", methods=["GET"])
@login_required
def api_export_monthly():
    """Export monthly stock entries as CSV."""
    import csv
    import io
    user = request.current_user
    shop_id = get_shop_id() or user.get("shop_id")
    month = request.args.get("month")
    entries = db.get_monthly_stock(shop_id, month)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Brand", "Product", "Model", "Qty Added", "Purchase Price",
                     "Supplier", "Payment Mode", "Invoice No", "Added By"])
    for e in entries:
        writer.writerow([
            e["created_at"], e["brand"], e["product_type"], e["model_no"],
            e["quantity_added"], e.get("purchase_price", ""),
            e.get("supplier_display", ""), e.get("payment_mode", ""),
            e.get("invoice_no", ""), e.get("added_by_name", ""),
        ])

    from flask import Response
    filename = f"stock_{month or 'current'}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/export/monthly-sales", methods=["GET"])
@login_required
def api_export_monthly_sales():
    """Export monthly sales as CSV."""
    import csv, io
    user = request.current_user
    shop_id = get_shop_id() or user.get("shop_id")
    month = request.args.get("month")
    entries = db.get_monthly_sales(shop_id, month)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Brand", "Product", "Model", "Qty Sold", "Sale Price",
                     "Total", "Buyer", "Payment Mode", "Sale Type", "Sold By"])
    for e in entries:
        writer.writerow([
            e["created_at"], e.get("brand", ""), e.get("product_type", ""),
            e.get("model_no", ""), e["quantity_sold"], e.get("sale_price", ""),
            e.get("total_amount", ""), e.get("buyer_display", ""),
            e.get("payment_mode", ""), e.get("sale_type", ""),
            e.get("sold_by_name", ""),
        ])

    from flask import Response
    filename = f"sales_{month or 'current'}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============ Superadmin routes ============

@app.route("/api/admin/shops", methods=["GET"])
@login_required
@role_required("superadmin")
def api_all_shops():
    return jsonify(db.get_all_shops())


@app.route("/api/admin/users", methods=["GET"])
@login_required
@role_required("superadmin")
def api_all_users():
    return jsonify(db.get_all_users())


# ============ Socket.IO ============
# (NO CHANGE)

def broadcast_inventory_update():
    socketio.emit("inventory_update", {"ts": True})


@socketio.on("connect")
def on_connect():
    pass


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", 6000))
    print(f"\n{'='*50}")
    print(f"  Inventory AI running at http://localhost:{port}")
    print(f"  SuperAdmin login: superadmin / admin123")
    print(f"{'='*50}\n")
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)