"""
Groq AI layer: Speech-to-Text, structured extraction, TTS, AI Stock Alerts.
UPDATED: extraction now captures supplier_name from voice
"""

import os, json, base64, requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

WHISPER_MODEL = "whisper-large-v3-turbo"
LLM_MODEL = "llama-3.3-70b-versatile"
TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICE = "hannah"
TTS_MAX_CHARS = 200

def _require_key():
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY environment variable set nahi hai.")

def transcribe_audio(file_bytes, filename="audio.webm"):
    _require_key()
    url = f"{GROQ_BASE_URL}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files = {"file": (filename, file_bytes)}
    data = {"model": WHISPER_MODEL, "response_format": "json"}
    resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


# ██ UPDATED: Now extracts supplier_name + purchase_price + sale_price ██
EXTRACTION_SYSTEM_PROMPT = """You are a cheerful inventory intake assistant for an electronics distributor in India.
You receive a transcript (Hindi/English mixed, "Hinglish") of an employee describing NEW STOCK that arrived.

The employee is a DISTRIBUTOR — they buy from suppliers/dealers and sell to customers/retailers.
They may mention: which supplier/dealer the stock came from, purchase price (cost), selling price.

Extract EVERY distinct inventory item mentioned. For each item, identify:
- brand: company/brand name (exactly as spoken)
- product_type: what kind of product (e.g. "Speaker", "TV", "Earbuds", "Charger")
- model_no: model number/name if mentioned, else ""
- quantity: integer count of units received
- purchase_price: cost price per unit in INR (what they PAID to the supplier). If a total was given, divide by quantity. If not mentioned, null.
- sale_price: selling price per unit in INR (what they will SELL at). If not mentioned, null.
- supplier_name: name of the supplier/dealer the stock came from (e.g. "Mehta wholesaler", "Delhi wala dealer", "Sharma traders"). If not mentioned, ""
- category: short inventory category (e.g. "Audio", "Electronics", "Wearables", "Mobile Accessories")

IMPORTANT RULES:
- "price" or "rate" usually means purchase_price (cost) unless they say "selling price" or "bechne ka price"
- If they say "500 ka aaya" or "500 mein liya" = purchase_price is 500
- If they say "800 mein bechenge" = sale_price is 800
- Supplier can be a person name, company name, or informal reference ("Sharma ji se aaya", "Delhi dealer se")
- Numbers in Hindi (chaalis=40, paanch sau=500) convert to digits

Respond ONLY with valid JSON (no markdown):
{"items": [
  {"brand":"...","product_type":"...","model_no":"...","quantity":0,"purchase_price":null,"sale_price":null,"supplier_name":"","category":"..."}
]}

If no inventory items found, return {"items": []}"""


def extract_items(transcript):
    _require_key()
    url = f"{GROQ_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(raw)
        items = parsed.get("items", [])
    except json.JSONDecodeError:
        items = []

    clean_items = []
    for it in items:
        clean_items.append({
            "brand": (it.get("brand") or "Unknown").strip(),
            "product_type": (it.get("product_type") or "Unknown").strip(),
            "model_no": (it.get("model_no") or "").strip(),
            "quantity": int(it.get("quantity") or 0),
            "purchase_price": it.get("purchase_price"),
            "sale_price": it.get("sale_price"),
            "supplier_name": (it.get("supplier_name") or "").strip(),
            "category": (it.get("category") or "Uncategorized").strip(),
        })
    return clean_items


CORRECTION_SYSTEM_PROMPT = """You are a cheerful inventory intake assistant. You previously extracted this item:
{current_item}

The employee just said a correction or confirmation in Hindi/English mixed speech.
If confirmed ("haan", "sahi hai", "yes", "correct", "theek hai"), return SAME item with "confirmed": true.
If correction, apply ONLY corrected field(s), set "confirmed": false.

Fields that can be corrected: brand, product_type, model_no, quantity, purchase_price, sale_price, supplier_name, category.

Respond ONLY with valid JSON:
{{"item": {{...}}, "confirmed": true/false}}"""


def apply_correction(current_item, user_reply):
    _require_key()
    url = f"{GROQ_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    system = CORRECTION_SYSTEM_PROMPT.format(current_item=json.dumps(current_item, ensure_ascii=False))
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_reply},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"item": current_item, "confirmed": False}

    item = parsed.get("item", current_item)
    confirmed = bool(parsed.get("confirmed", False))
    return item, confirmed


def get_ai_stock_alerts(products, sales_summary=None):
    _require_key()
    url = f"{GROQ_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    stock_data = json.dumps(products[:50], ensure_ascii=False)
    sales_info = json.dumps(sales_summary or [], ensure_ascii=False)
    system = """You are a smart inventory advisor for an Indian electronics distributor.
Analyze current stock and recent sales. Give 3-5 actionable alerts in Hinglish.
Each alert: type (warning/info/success), title (short), message (1 line).
Focus on: low stock, fast-selling items, slow-moving, restock suggestions, profit margins.
Respond ONLY with JSON: {"alerts": [{"type":"warning","title":"...","message":"..."}]}"""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Stock:\n{stock_data}\n\nSales:\n{sales_info}"},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        return json.loads(raw).get("alerts", [])
    except Exception:
        return []


def build_confirmation_text(item, index, total):
    import random
    pp = f"₹{item['purchase_price']} cost" if item.get("purchase_price") else ""
    sp = f", ₹{item['sale_price']} selling" if item.get("sale_price") else ""
    price_part = f"{pp}{sp}" if (pp or sp) else "price nahi mila"
    model_part = f" {item['model_no']}" if item.get("model_no") else ""
    supplier_part = f", supplier: {item['supplier_name']}" if item.get("supplier_name") else ""

    first = ["Badhiya! Pehla item — ", "Chalo shuru! — ", "Nice! Dekhte hain — "]
    nxt = ["Next item! ", "Chalo aage — ", "Ek aur! "]
    solo = ["Accha! Toh ", "Okay! Toh ", "Badhiya! "]

    if total > 1:
        opener = random.choice(first) if index == 1 else random.choice(nxt)
    else:
        opener = random.choice(solo)

    return (f"{opener}{item['brand']} {item['product_type']}{model_part}, "
            f"{item['quantity']} units, {price_part}{supplier_part}. Sahi hai?")


# ── TTS functions (NO CHANGE) ──

def _split_into_chunks(text, max_chars=TTS_MAX_CHARS):
    if len(text) <= max_chars: return [text]
    chunks = []; remaining = text.strip()
    while remaining:
        if len(remaining) <= max_chars: chunks.append(remaining); break
        window = remaining[:max_chars]
        break_at = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
        if break_at == -1: break_at = window.rfind(" ")
        if break_at == -1: break_at = max_chars
        chunks.append(remaining[:break_at + 1].strip())
        remaining = remaining[break_at + 1:].strip()
    return chunks

def _call_orpheus(text_chunk):
    url = f"{GROQ_BASE_URL}/audio/speech"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": TTS_MODEL, "voice": TTS_VOICE, "input": text_chunk, "response_format": "wav"}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.content

def _stitch_wavs(wav_bytes_list):
    import wave, io
    if len(wav_bytes_list) == 1: return wav_bytes_list[0]
    output_buffer = io.BytesIO(); output_wav = None
    for wav_bytes in wav_bytes_list:
        with wave.open(io.BytesIO(wav_bytes), "rb") as in_wav:
            if output_wav is None:
                output_wav = wave.open(output_buffer, "wb")
                output_wav.setparams(in_wav.getparams())
            output_wav.writeframes(in_wav.readframes(in_wav.getnframes()))
    output_wav.close()
    return output_buffer.getvalue()

def text_to_speech(text):
    _require_key()
    chunks = _split_into_chunks(text)
    wav_pieces = [_call_orpheus(chunk) for chunk in chunks]
    return _stitch_wavs(wav_pieces)