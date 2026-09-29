# ⚡ Inventory AI — Voice-Powered Stock Intake (Groq, 100% Free)

Electronics shop ke liye enterprise-style inventory assistant. Employee bolkar
naya stock describe karta hai, AI samajhkar confirm karta hai (voice mein),
aur dashboard table live update hoti hai.

## Yeh kya karta hai

1. **Bolna**: "40 quantity Boult speaker aaye, 500 rupaye ke, fir 100 Noise
   speaker bhi aaye 800 ke" — ek hi recording mein multiple items.
2. **Whisper** (Groq, free) — poora audio text mein convert karta hai
   (Hindi/English mix samajhta hai).
3. **Llama 3.3** (Groq, free) — text se **har item alag-alag nikal ke**
   structured data banata hai: brand, product type, model no, quantity, price,
   category (category bhi AI khud decide karta hai — koi fixed list nahi).
4. **Human-in-the-loop confirmation** — AI ek-ek item bolkar confirm karta hai
   ("Item 1 of 2: Boult Speaker, 40 quantity, ₹500 — yeh sahi hai?"). Employee
   "haan" bole to save ho jata hai, ya correction bole ("nahi price 550 hai")
   to AI sirf wahi field update karke dobara confirm karta hai.
5. **Smart merge** — agar same brand+product+model already DB mein hai, to
   naya entry banane ke bajaye **quantity add ho jaati hai** existing stock
   mein. Naya combination ho to naya row banta hai.
6. **Live dashboard** — Socket.IO se table turant update hoti hai, bina
   refresh kiye. Har cell pe click karke manually edit bhi kar sakte hain
   (galti theek karne ke liye).
7. **TTS reply** — AI apna confirmation Groq PlayAI voice se bolkar bhi sunata
   hai (agar TTS quota khatam ho jaye to browser ki built-in awaaz fallback
   ke roop mein chal jaati hai, app kabhi nahi rukta).

## Architecture

```
Browser (mic + live table)
   |
   |--POST /api/voice  (audio) -----> Whisper -> Llama (extract N items) -> 1st confirmation
   |--POST /api/reply  (audio/text) -> Llama (confirm/correct) -> save to DB -> next item
   |
   v
Flask + Socket.IO  <-----------------  SQLite (products + inventory_log)
   |
   '--> broadcasts "inventory_update" to ALL connected dashboards instantly
```

### Database design
- **`products`** table: current stock snapshot, unique per (brand, product_type,
  model_no). Quantity yahan hamesha "abhi kitna stock hai" reflect karta hai.
- **`inventory_log`** table: append-only audit trail — har transaction (kab,
  kitna aaya, kis price pe, original bola gaya transcript) permanently store
  hota hai, chahe products table mein quantity merge ho jaye.

## Setup (5 minute)

### 1. Free Groq API key
- https://console.groq.com/keys → sign up (no card) → "Create API Key"

### 2. Install
```bash
cd inventory-ai
pip install -r requirements.txt
```

### 3. Set key
```bash
export GROQ_API_KEY="gsk_xxxxxxxxxxxxxxxxxxxx"
```

### 4. Run
```bash
python app.py
```
Browser mein kholo: **http://localhost:5000**

## Used Groq models (sab free tier)
| Purpose | Model |
|---|---|
| Speech-to-text | `whisper-large-v3-turbo` |
| Item extraction + conversation | `llama-3.3-70b-versatile` |
| Text-to-speech reply | `playai-tts` (voice: Aaliyah-PlayAI) |

Free tier har model ka apna RPM/RPD/TPM limit hai — exact number apne account
ke **console.groq.com → Limits** page par dikhega (model ke size ke hisaab se
alag hota hai). Agar "rate limit" error aaye to `groq_ai.py` mein `LLM_MODEL`
ko chhote model jaise `llama-3.1-8b-instant` mein badal dena kaam chala dega.

## Multi-item conversation flow (example)

```
Employee: "40 quantity Boult speaker aaye, 500 rupaye ke. Fir 60 Noise watch
           bhi aayi hain, 1200 ki."

AI:       "Item 1 of 2: Boult Speaker, 40 quantity, ₹500 prati piece. Yeh sahi hai?"
Employee: "haan"
AI:       [DB mein save] "Item 2 of 2: Noise Watch, 60 quantity, ₹1200 prati piece. Yeh sahi hai?"
Employee: "nahi, price 1100 hai"
AI:       "Item 2 of 2: Noise Watch, 60 quantity, ₹1100 prati piece. Yeh sahi hai?"
Employee: "haan sahi hai"
AI:       [DB mein save] "Sab 2 item save ho gaye hain. Aur kuch stock aaya hai?"
```

Dono items save hote hi dashboard table **turant** update hoti hai (Socket.IO
broadcast), bina kisi refresh ke.

## Manual correction (dashboard se)

Kisi bhi cell par click karein (Brand, Product Type, Model No, Qty, Price) —
edit karke Enter ya click-away karein, turant save ho jata hai aur sab connected
dashboards par live reflect hota hai.

## Customize

- **Categories ki logic**: `groq_ai.py` → `EXTRACTION_SYSTEM_PROMPT`
- **Confirmation ka tone/style**: `groq_ai.py` → `build_confirmation_text()`
- **TTS voice badalna**: `groq_ai.py` → `TTS_VOICE` (Groq console mein available
  voices dekhein: https://console.groq.com/docs/text-to-speech)
- **Multi-employee/production**: abhi sessions in-memory hain (single process).
  Production ke liye Redis ya DB-backed session store use karein, aur SQLite
  ki jagah Postgres.

## Files
```
inventory-ai/
├── app.py              # Flask + Socket.IO server, conversation state machine
├── db.py                # SQLite layer - products + inventory_log, smart merge
├── groq_ai.py           # Whisper STT + Llama extraction/correction + TTS
├── static/
│   └── index.html        # Voice panel + live dashboard table
├── requirements.txt
└── inventory.db          # Auto-creates on first run
```
