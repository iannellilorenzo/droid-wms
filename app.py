import os
import re
import csv
import io
import json
import time
import getpass
import secrets
import sqlite3
from datetime import timedelta
from functools import wraps
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, session, redirect, url_for, render_template_string, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventario.db")
SEC_PATH = os.path.join(BASE_DIR, "security.json")

app = Flask(__name__)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=3650),
    SESSION_REFRESH_EACH_REQUEST=True,
)

LOGIN_ATTEMPTS = {}
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


def load_or_init_security():
    if os.path.exists(SEC_PATH):
        with open(SEC_PATH, "r") as f:
            data = json.load(f)
            app.secret_key = data["secret_key"]
            return data["pwd_hash"]
    print("\n--- CONFIGURAZIONE NUOVA PASSWORD STRONG ---")
    while True:
        pwd = getpass.getpass("Incolla la nuova password (almeno 32 caratteri): ").strip()
        if len(pwd) >= 32:
            break
        print(f"Troppo corta ({len(pwd)} caratteri). Inserisci almeno 32 caratteri.")
    data = {
        "secret_key": secrets.token_hex(32),
        "pwd_hash": generate_password_hash(pwd, method="pbkdf2:sha256", salt_length=16),
    }
    with open(SEC_PATH, "w") as f:
        json.dump(data, f)
    os.chmod(SEC_PATH, 0o600)
    app.secret_key = data["secret_key"]
    print("Password salvata come hash PBKDF2-SHA256 in security.json.\n")
    return data["pwd_hash"]


PWD_HASH = load_or_init_security()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prodotti (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codice_mw TEXT UNIQUE,
                ean TEXT UNIQUE,
                titolo TEXT NOT NULL,
                specifiche TEXT NOT NULL,
                giacenza INTEGER NOT NULL DEFAULT 0,
                esposto INTEGER NOT NULL DEFAULT 0,
                source_url TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mw ON prodotti(codice_mw)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ean ON prodotti(ean)")


init_db()


def normalize_ean(val):
    if not val:
        return None
    digits = re.sub(r"\D", "", str(val).strip())
    if not digits:
        return None
    if len(digits) == 12:
        digits = digits.zfill(13)
    return digits


def normalize_mw(val):
    if not val:
        return None
    digits = re.sub(r"\D", "", str(val).strip())
    return digits if len(digits) == 6 else None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("auth"):
            if request.is_json:
                return jsonify({"error": "Non autorizzato"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def scrape_mw(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["img", "script", "style", "svg", "noscript"]):
        tag.decompose()

    h1 = soup.find("h1")
    titolo = h1.get_text(" ", strip=True) if h1 else "Prodotto MediaWorld"

    specs = {}
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) >= 2 and cells[0] and cells[1]:
            specs[cells[0]] = cells[1]

    if not specs:
        for container in soup.find_all(["div", "dl", "li"]):
            children = [c for c in container.find_all(recursive=False) if c.get_text(strip=True)]
            if len(children) == 2:
                k = children[0].get_text(" ", strip=True)
                v = children[1].get_text(" ", strip=True)
                if 1 < len(k) < 60 and 0 < len(v) < 250 and k != v:
                    if not children[0].find(["div", "table"]) and not children[1].find(["div", "table"]):
                        specs[k] = v

    full_text = soup.get_text("\n", strip=True)

    ean = ""
    ean_match = re.search(r"(?:EAN|GTIN|Barcode|Codice\s*a\s*barre)\D{0,15}(\b\d{12,13}\b)", full_text, re.I)
    if ean_match:
        ean = normalize_ean(ean_match.group(1)) or ""
    else:
        all_13 = re.findall(r"\b\d{13}\b", full_text)
        if all_13:
            ean = all_13[0]

    codice_mw = ""
    mw_match = re.search(r"(?:Codice\s*(?:articolo|prodotto|MW|PIM)?|Art\.?|SKU)\D{0,15}(\b\d{6}\b)", full_text, re.I)
    if mw_match:
        codice_mw = mw_match.group(1)
    else:
        tail_lines = full_text.splitlines()[-15:]
        for line in reversed(tail_lines):
            m6 = re.search(r"\b(\d{6})\b", line)
            if m6:
                codice_mw = m6.group(1)
                break

    return {
        "titolo": titolo,
        "codice_mw": codice_mw,
        "ean": ean,
        "specifiche": specs,
        "source_url": url,
    }


HTML_LOGIN = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Accesso Gestionale</title>
<style>
  body { background:#0b0b0c; color:#f4f4f5; font-family:system-ui,sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }
  .box { background:#161618; padding:28px; border-radius:16px; width:90%; max-width:360px; border:1px solid #27272a; }
  h2 { margin:0 0 16px; text-align:center; }
  input[type=password] { width:100%; box-sizing:border-box; padding:14px; font-size:16px; border-radius:10px; border:1px solid #3f3f46; background:#09090b; color:#fff; margin-bottom:14px; }
  .remember { display:flex; align-items:center; gap:8px; font-size:14px; color:#a1a1aa; margin-bottom:16px; cursor:pointer; user-select:none; }
  .remember input { width:18px; height:18px; accent-color:#e11d48; cursor:pointer; }
  button { width:100%; padding:14px; font-size:16px; font-weight:600; border:none; border-radius:10px; background:#e11d48; color:#fff; cursor:pointer; }
  .err { color:#fb7185; font-size:14px; margin-top:12px; text-align:center; }
</style>
</head>
<body>
<div class="box">
  <h2>Gestionale Reparto</h2>
  <form method="post">
    <input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
    <label class="remember">
      <input type="checkbox" name="remember" value="1" checked>
      <span>Ricorda questo dispositivo per sempre</span>
    </label>
    <button type="submit">Accedi</button>
  </form>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
</div>
</body>
</html>
"""

HTML_MAIN = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gestionale MW</title>
<style>
  :root { --bg:#09090b; --card:#141417; --border:#27272a; --accent:#e11d48; --green:#10b981; --text:#f4f4f5; --muted:#a1a1aa; }
  body { background:var(--bg); color:var(--text); font-family:system-ui,-apple-system,sans-serif; margin:0; padding:14px; max-width:720px; margin-inline:auto; }
  h1 { font-size:20px; margin:0 0 14px; display:flex; justify-content:space-between; align-items:center; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:14px; margin-bottom:14px; }
  input[type=text], input[type=url], input[type=number], input[type=file] {
    width:100%; box-sizing:border-box; padding:12px; border-radius:10px; border:1px solid #3f3f46;
    background:#09090b; color:#fff; font-size:15px; margin-bottom:10px;
  }
  .row { display:flex; gap:8px; align-items:center; }
  button { padding:11px 16px; border-radius:10px; border:none; font-weight:600; font-size:15px; cursor:pointer; background:var(--accent); color:#fff; }
  button.sec { background:#27272a; color:#e4e4e7; }
  button.qty { width:44px; height:44px; font-size:20px; padding:0; }
  .badge { display:inline-block; padding:3px 8px; border-radius:6px; font-size:12px; font-weight:600; background:#27272a; color:var(--muted); margin-right:6px; margin-bottom:4px; }
  .specs { width:100%; border-collapse:collapse; margin-top:10px; font-size:13px; }
  .specs td { padding:6px 4px; border-bottom:1px solid #222226; vertical-align:top; }
  .specs td:first-child { color:var(--muted); width:42%; }
  .toggle { padding:8px 14px; border-radius:20px; font-size:14px; font-weight:700; }
  .toggle.on { background:rgba(16,185,129,0.2); color:#34d399; border:1px solid #10b981; }
  .toggle.off { background:rgba(244,63,94,0.15); color:#fb7185; border:1px solid #e11d48; }
  .msg { padding:10px; border-radius:8px; background:#1e293b; color:#38bdf8; margin-bottom:12px; font-size:14px; }
</style>
</head>
<body>

<h1>
  <span>Inventario Reparto <small style="color:var(--muted);font-weight:400">({{ totale }})</small></span>
  <a href="/logout" style="color:var(--muted);font-size:13px;text-decoration:none;">Esci</a>
</h1>

{% if msg %}<div class="msg">{{ msg }}</div>{% endif %}

<div class="card">
  <form method="get" action="/" class="row">
    <input type="text" name="q" value="{{ q }}" placeholder="Cerca Codice MW (6 cifre), EAN o modello..." style="margin:0" autofocus>
    <button type="submit">Cerca</button>
    {% if q %}<a href="/" style="color:var(--muted);padding:0 6px;text-decoration:none;">✕</a>{% endif %}
  </form>
</div>

<div class="card">
  <details {% if preview %}open{% endif %}>
    <summary style="cursor:pointer;font-weight:600;font-size:15px;">+ Aggiungi prodotto (Link qrc.mw / Manuale / CSV)</summary>

    <form method="post" action="/fetch" style="margin-top:12px;">
      <label style="font-size:12px;color:var(--muted)">Da cartellino digitale qrc.mw</label>
      <div class="row">
        <input type="url" name="url" placeholder="Incolla link https://qrc.mw/..." required style="margin:0">
        <button type="submit" class="sec">Leggi</button>
      </div>
    </form>

    <form method="post" action="/save" style="margin-top:14px; border-top:1px solid var(--border); padding-top:12px;">
      <input type="hidden" name="source_url" value="{{ preview.source_url if preview else '' }}">
      <input type="hidden" name="specifiche" value='{{ (preview.specifiche if preview else {}) | tojson }}'>
      <label style="font-size:12px;color:var(--muted)">Titolo / Modello</label>
      <input type="text" name="titolo" value="{{ preview.titolo if preview else '' }}" placeholder="es. LENOVO IdeaPad Slim 3..." required>
      <div class="row">
        <div style="flex:1">
          <label style="font-size:12px;color:var(--muted)">Codice MW / PIM (6 cifre)</label>
          <input type="text" name="codice_mw" value="{{ preview.codice_mw if preview else '' }}" pattern="[0-9]{6}" placeholder="es. 593522">
        </div>
        <div style="flex:1">
          <label style="font-size:12px;color:var(--muted)">EAN (13 cifre)</label>
          <input type="text" name="ean" value="{{ preview.ean if preview else '' }}" pattern="[0-9]{12,13}" placeholder="es. 0199276800732">
        </div>
      </div>
      <div class="row">
        <div style="flex:1">
          <label style="font-size:12px;color:var(--muted)">Giacenza iniziale</label>
          <input type="number" name="giacenza" value="1" min="0">
        </div>
        <div style="flex:1; padding-top:14px;">
          <label><input type="checkbox" name="esposto" value="1"> Esposto in corsia</label>
        </div>
      </div>
      <button type="submit" style="width:100%;margin-top:6px;">Salva prodotto</button>
    </form>

    <form method="post" action="/import-csv" enctype="multipart/form-data" style="margin-top:14px; border-top:1px solid var(--border); padding-top:12px;">
      <label style="font-size:12px;color:var(--muted)">Importa / Aggiorna da file CSV MediaWorld</label>
      <div class="row" style="margin-top:6px;">
        <input type="file" name="csv_file" accept=".csv" required style="margin:0; padding:8px;">
        <button type="submit" class="sec">Importa</button>
      </div>
    </form>
  </details>
</div>

{% for p in prodotti %}
<div class="card" id="prod-{{ p.id }}">
  <div style="font-weight:700; font-size:16px; margin-bottom:6px;">{{ p.titolo }}</div>
  <div style="margin-bottom:10px;">
    {% if p.codice_mw %}<span class="badge">MW: {{ p.codice_mw }}</span>{% endif %}
    {% if p.ean %}<span class="badge">EAN: {{ p.ean }}</span>{% endif %}
    {% if p.specs_dict.get('Prezzo EUR') %}<span class="badge" style="color:#fbbf24;">€ {{ p.specs_dict.get('Prezzo EUR') }}</span>{% endif %}
    {% if p.specs_dict.get('Stato') %}<span class="badge">{{ p.specs_dict.get('Stato') }}</span>{% endif %}
  </div>

  <div class="row" style="justify-content:space-between; background:#0e0e11; padding:10px; border-radius:10px;">
    <div class="row">
      <button type="button" class="sec qty" onclick="updateQty({{ p.id }}, -1)">-</button>
      <div style="min-width:54px; text-align:center;">
        <div style="font-size:11px;color:var(--muted)">GIACENZA</div>
        <strong id="qty-{{ p.id }}" style="font-size:20px;">{{ p.giacenza }}</strong>
      </div>
      <button type="button" class="sec qty" onclick="updateQty({{ p.id }}, 1)">+</button>
    </div>

    <button type="button" id="esp-{{ p.id }}" class="toggle {{ 'on' if p.esposto else 'off' }}" onclick="toggleEsposto({{ p.id }})">
      {{ 'ESPOSTO: SÌ' if p.esposto else 'ESPOSTO: NO' }}
    </button>
  </div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer; color:var(--muted); font-size:13px;">Scheda tecnica ({{ p.specs_dict|length }} voci) & Opzioni</summary>
    {% if p.specs_dict %}
    <table class="specs">
      {% for k, v in p.specs_dict.items() %}
      <tr><td>{{ k }}</td><td>{{ v }}</td></tr>
      {% endfor %}
    </table>
    {% endif %}

    <form method="post" action="/enrich/{{ p.id }}" class="row" style="margin-top:10px;">
      <input type="url" name="url" placeholder="Integra scheda da link https://qrc.mw/..." required style="margin:0; padding:8px; font-size:13px;">
      <button type="submit" class="sec" style="padding:8px 12px; font-size:13px;">Aggiorna</button>
    </form>

    <form method="post" action="/delete/{{ p.id }}" onsubmit="return confirm('Eliminare definitivamente questo prodotto?');" style="margin-top:8px; text-align:right;">
      <button type="submit" style="background:transparent; color:#fb7185; font-size:12px; padding:4px 8px;">Elimina scheda</button>
    </form>
  </details>
</div>
{% else %}
<div style="text-align:center; color:var(--muted); padding:30px 0;">Nessun prodotto trovato.</div>
{% endfor %}

<script>
async function updateQty(id, delta) {
  const res = await fetch(`/api/qty/${id}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({delta})
  });
  if (res.ok) {
    const data = await res.json();
    document.getElementById(`qty-${id}`).textContent = data.giacenza;
  }
}

async function toggleEsposto(id) {
  const res = await fetch(`/api/esposto/${id}`, { method: 'POST' });
  if (res.ok) {
    const data = await res.json();
    const btn = document.getElementById(`esp-${id}`);
    btn.className = 'toggle ' + (data.esposto ? 'on' : 'off');
    btn.textContent = data.esposto ? 'ESPOSTO: SÌ' : 'ESPOSTO: NO';
  }
}
</script>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    now = time.time()
    rec = LOGIN_ATTEMPTS.get(ip, {"count": 0, "until": 0})

    if now < rec["until"]:
        wait = int(rec["until"] - now)
        return render_template_string(HTML_LOGIN, error=f"Troppi tentativi. Riprova tra {wait}s."), 429

    if request.method == "POST":
        pwd = request.form.get("password", "").strip()
        if check_password_hash(PWD_HASH, pwd):
            LOGIN_ATTEMPTS.pop(ip, None)
            session.permanent = (request.form.get("remember") == "1")
            session["auth"] = True
            return redirect(url_for("index"))
        rec["count"] += 1
        if rec["count"] >= MAX_ATTEMPTS:
            rec["until"] = now + LOCKOUT_SECONDS
            rec["count"] = 0
        LOGIN_ATTEMPTS[ip] = rec
        return render_template_string(HTML_LOGIN, error="Password errata."), 401

    return render_template_string(HTML_LOGIN, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    msg = request.args.get("msg", "")
    with get_db() as conn:
        totale = conn.execute("SELECT COUNT(*) FROM prodotti").fetchone()[0]
        if q:
            q_ean = normalize_ean(q) or q
            rows = conn.execute(
                """SELECT * FROM prodotti
                   WHERE codice_mw = ? OR ean = ? OR ean = ? OR titolo LIKE ?
                   ORDER BY esposto DESC, titolo ASC""",
                (q, q, q_ean, f"%{q}%"),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM prodotti ORDER BY esposto DESC, updated_at DESC LIMIT 100").fetchall()

    prodotti = []
    for r in rows:
        d = dict(r)
        try:
            d["specs_dict"] = json.loads(d["specifiche"])
        except Exception:
            d["specs_dict"] = {}
        prodotti.append(d)

    return render_template_string(HTML_MAIN, prodotti=prodotti, q=q, preview=None, msg=msg, totale=totale)


@app.route("/fetch", methods=["POST"])
@login_required
def fetch_product():
    url = request.form.get("url", "").strip()
    try:
        preview = scrape_mw(url)
        with get_db() as conn:
            totale = conn.execute("SELECT COUNT(*) FROM prodotti").fetchone()[0]
        return render_template_string(HTML_MAIN, prodotti=[], q="", preview=preview, msg="Scheda estratta: verifica i codici e salva.", totale=totale)
    except Exception as e:
        return redirect(url_for("index", msg=f"Errore lettura link: {e}"))


@app.route("/save", methods=["POST"])
@login_required
def save_product():
    titolo = request.form.get("titolo", "").strip()
    codice_mw = normalize_mw(request.form.get("codice_mw"))
    ean = normalize_ean(request.form.get("ean"))
    giacenza = max(0, int(request.form.get("giacenza", 0)))
    esposto = 1 if request.form.get("esposto") == "1" else 0
    specifiche = request.form.get("specifiche", "{}")
    source_url = request.form.get("source_url", "")

    if not codice_mw and not ean:
        return redirect(url_for("index", msg="Inserisci almeno uno tra Codice MW (6 cifre) o EAN (13 cifre)."))

    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO prodotti (codice_mw, ean, titolo, specifiche, giacenza, esposto, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (codice_mw, ean, titolo, specifiche, giacenza, esposto, source_url),
            )
        return redirect(url_for("index", msg="Prodotto salvato con successo!"))
    except sqlite3.IntegrityError:
        return redirect(url_for("index", msg="Errore: Codice MW o EAN già presente nel database."))


@app.route("/import-csv", methods=["POST"])
@login_required
def import_csv():
    f = request.files.get("csv_file")
    if not f:
        return redirect(url_for("index", msg="Nessun file selezionato."))

    try:
        content = f.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(content))
        inseriti = 0
        aggiornati = 0

        spec_cols = [
            "Marchio", "Modello", "Stato", "Prezzo EUR", "B.O.",
            "Codifica (data)", "CPU", "RAM GB", "SSD GB", "Schermo pollici", "GPU"
        ]

        with get_db() as conn:
            for row in reader:
                codice_mw = normalize_mw(row.get("Codice PIM") or row.get("Codice MW"))
                ean = normalize_ean(row.get("EAN"))
                if not codice_mw and not ean:
                    continue

                sigla = (row.get("Sigla completa") or "").strip()
                modello = (row.get("Modello") or "").strip()
                marchio = (row.get("Marchio") or "").strip()
                if sigla and modello and modello.lower() not in sigla.lower():
                    titolo = f"{modello} ({sigla})"
                else:
                    titolo = sigla or f"{marchio} {modello}".strip() or "Prodotto"

                try:
                    giacenza = max(0, int(float(row.get("Giacenza") or 0)))
                except ValueError:
                    giacenza = 0

                esp_raw = (row.get("Esposti") or "").strip().upper()
                esposto = 1 if esp_raw in ("SI", "SÌ", "1", "TRUE", "YES") else 0

                specs = {k: row[k].strip() for k in spec_cols if row.get(k) and row.get(k).strip()}

                existing = None
                if codice_mw:
                    existing = conn.execute("SELECT id, specifiche FROM prodotti WHERE codice_mw = ?", (codice_mw,)).fetchone()
                if not existing and ean:
                    existing = conn.execute("SELECT id, specifiche FROM prodotti WHERE ean = ?", (ean,)).fetchone()

                if existing:
                    try:
                        old_specs = json.loads(existing["specifiche"])
                    except Exception:
                        old_specs = {}
                    old_specs.update(specs)
                    conn.execute(
                        """UPDATE prodotti
                           SET titolo = ?, specifiche = ?, giacenza = ?, esposto = ?,
                               codice_mw = COALESCE(?, codice_mw), ean = COALESCE(?, ean),
                               updated_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (titolo, json.dumps(old_specs), giacenza, esposto, codice_mw, ean, existing["id"]),
                    )
                    aggiornati += 1
                else:
                    conn.execute(
                        """INSERT INTO prodotti (codice_mw, ean, titolo, specifiche, giacenza, esposto)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (codice_mw, ean, titolo, json.dumps(specs), giacenza, esposto),
                    )
                    inseriti += 1

        return redirect(url_for("index", msg=f"CSV importato: {inseriti} nuovi prodotti, {aggiornati} aggiornati."))
    except Exception as e:
        return redirect(url_for("index", msg=f"Errore importazione CSV: {e}"))


@app.route("/enrich/<int:pid>", methods=["POST"])
@login_required
def enrich_product(pid):
    url = request.form.get("url", "").strip()
    try:
        scraped = scrape_mw(url)
        with get_db() as conn:
            row = conn.execute("SELECT specifiche, codice_mw, ean FROM prodotti WHERE id = ?", (pid,)).fetchone()
            if not row:
                return redirect(url_for("index", msg="Prodotto non trovato."))
            try:
                specs = json.loads(row["specifiche"])
            except Exception:
                specs = {}
            specs.update(scraped["specifiche"])
            new_mw = row["codice_mw"] or normalize_mw(scraped["codice_mw"])
            new_ean = row["ean"] or normalize_ean(scraped["ean"])
            conn.execute(
                """UPDATE prodotti
                   SET specifiche = ?, codice_mw = ?, ean = ?, source_url = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (json.dumps(specs), new_mw, new_ean, url, pid),
            )
        return redirect(url_for("index", msg="Scheda tecnica integrata dal link!"))
    except Exception as e:
        return redirect(url_for("index", msg=f"Errore lettura link: {e}"))


@app.route("/delete/<int:pid>", methods=["POST"])
@login_required
def delete_product(pid):
    with get_db() as conn:
        conn.execute("DELETE FROM prodotti WHERE id = ?", (pid,))
    return redirect(url_for("index", msg="Prodotto eliminato."))


@app.route("/api/qty/<int:pid>", methods=["POST"])
@login_required
def api_qty(pid):
    delta = int(request.json.get("delta", 0))
    with get_db() as conn:
        conn.execute(
            "UPDATE prodotti SET giacenza = MAX(0, giacenza + ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (delta, pid),
        )
        row = conn.execute("SELECT giacenza FROM prodotti WHERE id = ?", (pid,)).fetchone()
    return jsonify({"giacenza": row["giacenza"]})


@app.route("/api/esposto/<int:pid>", methods=["POST"])
@login_required
def api_esposto(pid):
    with get_db() as conn:
        conn.execute(
            "UPDATE prodotti SET esposto = CASE WHEN esposto = 1 THEN 0 ELSE 1 END, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pid,),
        )
        row = conn.execute("SELECT esposto FROM prodotti WHERE id = ?", (pid,)).fetchone()
    return jsonify({"esposto": bool(row["esposto"])})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
