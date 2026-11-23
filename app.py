import os
import re
import csv
import io
import json
import time
import getpass
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from itsdangerous import URLSafeTimedSerializer, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, Response

try:
    from selectolax.parser import HTMLParser
    HAS_SELECTOLAX = True
except ImportError:
    from bs4 import BeautifulSoup
    HAS_SELECTOLAX = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventario.db")
SEC_PATH = os.path.join(BASE_DIR, "security.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

TZ_ROME = ZoneInfo("Europe/Rome")
COOKIE_NAME = "droid_wms_session"
SESSION_MAX_AGE = 86400 * 3650  # 10 anni, rinnovato ad ogni richiesta

LOGIN_ATTEMPTS = {}
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


def load_or_init_security():
    if os.path.exists(SEC_PATH):
        with open(SEC_PATH, "r") as f:
            data = json.load(f)
            pwd_hash = data.get("pwd_hash") or data.get("pin_hash")
            return data["secret_key"], pwd_hash
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
    return data["secret_key"], data["pwd_hash"]


SECRET_KEY, PWD_HASH = load_or_init_security()
SERIALIZER = URLSafeTimedSerializer(SECRET_KEY, salt="droid-wms-auth")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
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
                giacenza_base INTEGER NOT NULL DEFAULT 0,
                esposto INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                source_url TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(prodotti)").fetchall()}
        if "giacenza_base" not in cols:
            conn.execute("ALTER TABLE prodotti ADD COLUMN giacenza_base INTEGER NOT NULL DEFAULT 0")
            conn.execute("DELETE FROM prodotti WHERE giacenza <= 0")
        conn.execute("UPDATE prodotti SET giacenza_base = giacenza")
        if "note" not in cols:
            conn.execute("ALTER TABLE prodotti ADD COLUMN note TEXT NOT NULL DEFAULT ''")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_mw ON prodotti(codice_mw)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ean ON prodotti(ean)")
        conn.execute("PRAGMA journal_mode=WAL")


init_db()


def run_nightly_backup_and_reset(date_str=None):
    if not date_str:
        date_str = datetime.now(TZ_ROME).strftime("%Y-%m-%d")

    daily_file = os.path.join(BACKUP_DIR, f"giacenze_{date_str}.csv")
    latest_file = os.path.join(BACKUP_DIR, "giacenze_latest.csv")

    with get_db() as conn:
        rows = conn.execute(
            "SELECT codice_mw, ean, titolo, giacenza_base, giacenza, esposto, note, specifiche FROM prodotti ORDER BY esposto DESC, titolo ASC"
        ).fetchall()

        fieldnames = ["Codice PIM", "EAN", "Titolo", "Giacenza Precedente", "Variazione", "Giacenza Finale", "Esposto", "Note", "Prezzo EUR"]
        for path in (daily_file, latest_file):
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in rows:
                    try:
                        specs = json.loads(r["specifiche"])
                    except Exception:
                        specs = {}
                    delta = r["giacenza"] - r["giacenza_base"]
                    writer.writerow({
                        "Codice PIM": r["codice_mw"] or "",
                        "EAN": r["ean"] or "",
                        "Titolo": r["titolo"],
                        "Giacenza Precedente": r["giacenza_base"],
                        "Variazione": f"{delta:+d}" if delta != 0 else "0",
                        "Giacenza Finale": r["giacenza"],
                        "Esposto": "SI" if r["esposto"] else "NO",
                        "Note": r["note"] or "",
                        "Prezzo EUR": specs.get("Prezzo EUR", ""),
                    })

        conn.execute("UPDATE prodotti SET giacenza_base = giacenza")


def midnight_scheduler():
    last_run_date = None
    while True:
        now = datetime.now(TZ_ROME)
        today_str = now.strftime("%Y-%m-%d")
        if now.hour == 0 and now.minute == 0 and last_run_date != today_str:
            yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                run_nightly_backup_and_reset(yesterday_str)
                last_run_date = today_str
            except Exception as e:
                print(f"[BACKUP ERROR] {e}")
        time.sleep(20)


threading.Thread(target=midnight_scheduler, daemon=True).start()


def get_session_data(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = SERIALIZER.loads(token, max_age=SESSION_MAX_AGE)
        return data if data.get("auth") else None
    except BadSignature:
        return None


def set_session_cookie(response, remember: bool):
    token = SERIALIZER.dumps({"auth": True, "remember": remember})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE if remember else None,
        httponly=True,
        secure=True,
        samesite="lax",
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/login", "/logout", "/manifest.json", "/sw.js", "/icon.svg"):
            return await call_next(request)

        sess = get_session_data(request)
        if not sess:
            if path.startswith("/api/"):
                return JSONResponse({"error": "Non autorizzato"}, status_code=401)
            return RedirectResponse("/login", status_code=303)

        response = await call_next(request)
        if sess.get("remember"):
            set_session_cookie(response, remember=True)
        return response


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


def extract_structured_meta(titolo, specs):
    marchio = (specs.get("Marchio") or specs.get("Marca") or "").strip().upper()
    if not marchio:
        known_brands = ["LENOVO", "SAMSUNG", "HP", "ASUS", "ACER", "MSI", "APPLE", "DELL", "LG", "SONY", "HUAWEI", "MICROSOFT"]
        up_title = titolo.upper()
        for b in known_brands:
            if b in up_title or up_title.startswith(b[:3] + " "):
                marchio = "SAMSUNG" if up_title.startswith("SAM ") else ("LENOVO" if up_title.startswith("LEN ") else b)
                break

    prezzo = None
    raw_price = specs.get("Prezzo EUR") or specs.get("Prezzo") or ""
    m_price = re.search(r"(\d+(?:[.,]\d+)?)", str(raw_price))
    if m_price:
        prezzo = float(m_price.group(1).replace(",", "."))

    schermo = str(specs.get("Schermo pollici") or "").strip()
    if not schermo:
        for k, v in specs.items():
            if any(w in k.lower() for w in ["schermo", "display", "pollici", "diagonale"]):
                m_s = re.search(r"(\d{2}(?:[.,]\d)?)", str(v))
                if m_s:
                    schermo = m_s.group(1).replace(",", ".")
                    break

    ram = str(specs.get("RAM GB") or "").strip()
    if not ram:
        for k, v in specs.items():
            if "ram" in k.lower() or "memoria" in k.lower():
                m_r = re.search(r"\b(4|6|8|12|16|24|32|64)\b", str(v))
                if m_r:
                    ram = m_r.group(1)
                    break

    ssd = str(specs.get("SSD GB") or "").strip()
    if not ssd:
        for k, v in specs.items():
            if any(w in k.lower() for w in ["ssd", "archiviazione", "capacità", "storage", "disco"]):
                m_d = re.search(r"\b(64|128|256|512|1000|1024|2000|2048)\b", str(v))
                if m_d:
                    ssd = m_d.group(1)
                    break
                if re.search(r"\b1\s*TB\b", str(v), re.I):
                    ssd = "1024"
                    break

    gpu_raw = str(specs.get("GPU") or specs.get("Scheda grafica") or specs.get("Scheda video") or "").strip()
    gpu_check = f"{gpu_raw} {titolo}".upper()
    dedicated_keywords = ["RTX", "GTX", "NVIDIA", "GEFORCE", "RADEON RX", "ARC A", "ARC B", "QUADRO"]
    is_dedicated = any(kw in gpu_check for kw in dedicated_keywords)
    gpu_tipo = "Dedicata" if is_dedicated else "Integrata"

    modello = (specs.get("Modello") or "").strip()
    cat_raw = f"{locals().get('titolo', locals().get('title', ''))} {specs.get('Tipologia', '')} {specs.get('Categoria', '')} {specs.get('Tipo', '')}".lower()
    is_tablet = bool(re.search(r"\b(tablet|ipad|tab)\b", cat_raw))
    categoria = "Tablet" if is_tablet else "Computer"

    cpu_raw = f"{locals().get('titolo', locals().get('title', ''))} {specs.get('Processore', '')} {specs.get('CPU', '')} {specs.get('Marca processore', '')} {specs.get('Modello Processore', '')}".lower()
    if re.search(r"\b(intel|core\s*i[3579]|core\s*ultra|celeron|pentium|n100|n200)\b", cpu_raw):
        cpu_brand = "Intel"
    elif re.search(r"\b(amd|ryzen|athlon)\b", cpu_raw):
        cpu_brand = "AMD"
    elif re.search(r"\b(snapdragon|qualcomm)\b", cpu_raw):
        cpu_brand = "Qualcomm"
    elif re.search(r"\b(apple|m1|m2|m3|m4)\b", cpu_raw):
        cpu_brand = "Apple"
    else:
        cpu_brand = ""

    return {
        "marchio": marchio or "Altro",
        "modello": modello,
        "categoria": categoria,
        "cpu_brand": cpu_brand,
        "prezzo": prezzo,
        "schermo": schermo,
        "ram": ram,
        "ssd": ssd,
        "gpu_raw": gpu_raw,
        "gpu_tipo": gpu_tipo,
    }


def serialize_product(row):
    d = dict(row)
    try:
        specs = json.loads(d["specifiche"])
    except Exception:
        specs = {}
    d["specs"] = specs
    d["delta"] = d["giacenza"] - d["giacenza_base"]
    d["meta"] = extract_structured_meta(d["titolo"], specs)
    del d["specifiche"]
    return d


def scrape_mw(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    clean_html = re.sub(r"<img\b[^>]*>", "", resp.text, flags=re.I)
    specs = {}

    if HAS_SELECTOLAX:
        tree = HTMLParser(clean_html)
        tree.strip_tags(["script", "style", "svg", "noscript"])
        h1 = tree.css_first("h1")
        titolo = h1.text(separator=" ", strip=True) if h1 else "Prodotto MediaWorld"

        for tr in tree.css("tr"):
            cells = [c.text(separator=" ", strip=True) for c in tr.css("th, td")]
            if len(cells) >= 2 and cells[0] and cells[1]:
                specs[cells[0]] = cells[1]

        if not specs:
            for node in tree.css("div, dl, li"):
                children = [c for c in node.iter() if c.text(strip=True)]
                if len(children) == 2:
                    k = children[0].text(separator=" ", strip=True)
                    v = children[1].text(separator=" ", strip=True)
                    if 1 < len(k) < 60 and 0 < len(v) < 250 and k != v:
                        if not children[0].css_first("div, table") and not children[1].css_first("div, table"):
                            specs[k] = v

        full_text = tree.body.text(separator="\n", strip=True) if tree.body else ""
    else:
        soup = BeautifulSoup(clean_html, "html.parser")
        for tag in soup(["script", "style", "svg", "noscript"]):
            tag.decompose()
        h1 = soup.find("h1")
        titolo = h1.get_text(" ", strip=True) if h1 else "Prodotto MediaWorld"
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) >= 2 and cells[0] and cells[1]:
                specs[cells[0]] = cells[1]
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


def render_login(error: str = ""):
    err_html = f'<div class="err">{error}</div>' if error else ""
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Accesso Gestionale</title>
<style>
  body {{ background:#0b0b0c; color:#f4f4f5; font-family:system-ui,sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
  .box {{ background:#161618; padding:28px; border-radius:16px; width:90%; max-width:360px; border:1px solid #27272a; }}
  h2 {{ margin:0 0 16px; text-align:center; }}
  input[type=password] {{ width:100%; box-sizing:border-box; padding:14px; font-size:16px; border-radius:10px; border:1px solid #3f3f46; background:#09090b; color:#fff; margin-bottom:14px; }}
  .remember {{ display:flex; align-items:center; gap:8px; font-size:14px; color:#a1a1aa; margin-bottom:16px; cursor:pointer; user-select:none; }}
  .remember input {{ width:18px; height:18px; accent-color:#e11d48; cursor:pointer; }}
  button {{ width:100%; padding:14px; font-size:16px; font-weight:600; border:none; border-radius:10px; background:#e11d48; color:#fff; cursor:pointer; }}
  .err {{ color:#fb7185; font-size:14px; margin-top:12px; text-align:center; }}
</style>
</head>
<body>
<div class="box">
  <h2>Gestionale Reparto</h2>
  <form method="post" action="/login">
    <input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
    <label class="remember">
      <input type="checkbox" name="remember" value="1" checked>
      <span>Ricorda questo dispositivo per sempre</span>
    </label>
    <button type="submit">Accedi</button>
  </form>
  {err_html}
</div>
</body>
</html>"""


HTML_SPA = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>droid-wms v2.4.0</title>
<meta name="theme-color" content="#09090b">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon.svg">
<style>
  :root { --bg:#09090b; --card:#141417; --border:#27272a; --accent:#e11d48; --green:#10b981; --text:#f4f4f5; --muted:#a1a1aa; }
  * { box-sizing: border-box; }
  body { background:var(--bg); color:var(--text); font-family:system-ui,-apple-system,sans-serif; margin:0; padding:12px; max-width:740px; margin-inline:auto; }
  h1 { font-size:19px; margin:0 0 12px; display:flex; justify-content:space-between; align-items:center; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:13px; margin-bottom:12px; }
  input[type=text], input[type=url], input[type=number], input[type=file], select {
    width:100%; padding:10px 12px; border-radius:10px; border:1px solid #3f3f46;
    background:#09090b; color:#fff; font-size:14px; font-family:inherit;
  }
  .row { display:flex; gap:8px; align-items:center; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
  .grid3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; }
  button { padding:10px 14px; border-radius:10px; border:none; font-weight:600; font-size:14px; cursor:pointer; background:var(--accent); color:#fff; }
  button.sec { background:#27272a; color:#e4e4e7; }
  button.qty { width:42px; height:42px; font-size:20px; padding:0; }
  .badge { display:inline-block; padding:3px 8px; border-radius:6px; font-size:12px; font-weight:600; background:#27272a; color:var(--muted); margin-right:5px; margin-bottom:4px; }
  .badge.price { color:#fbbf24; }
  .badge.delta-pos { background:rgba(16,185,129,0.2); color:#34d399; border:1px solid #10b981; font-weight:800; }
  .badge.delta-neg { background:rgba(244,63,94,0.2); color:#fb7185; border:1px solid #e11d48; font-weight:800; }
  .specs { width:100%; border-collapse:collapse; margin-top:10px; font-size:13px; }
  .specs td { padding:5px 4px; border-bottom:1px solid #222226; vertical-align:top; }
  .specs td:first-child { color:var(--muted); width:42%; }
  .toggle { padding:8px 13px; border-radius:20px; font-size:13px; font-weight:700; }
  .toggle.on { background:rgba(16,185,129,0.2); color:#34d399; border:1px solid #10b981; }
  .toggle.off { background:rgba(244,63,94,0.15); color:#fb7185; border:1px solid #e11d48; }
  .price-pill { padding:8px 13px; border-radius:20px; font-size:13px; font-weight:700; background:rgba(251,191,36,0.16); color:#fbbf24; border:1px solid #f59e0b; white-space:nowrap; }
  .toast { position:fixed; bottom:16px; left:50%; transform:translateX(-50%); background:#1e293b; color:#38bdf8; border:1px solid #38bdf8; padding:10px 18px; border-radius:99px; font-size:13px; font-weight:600; z-index:99; display:none; box-shadow:0 6px 20px rgba(0,0,0,0.6); }
  label.lbl { display:block; font-size:11px; color:var(--muted); margin-bottom:3px; }
  .kpi-bar { display:grid; grid-template-columns:repeat(2, 1fr); gap:6px; margin-bottom:8px; }
  .kpi-btn { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:8px 10px; font-size:12px; color:var(--muted); display:flex; align-items:center; justify-content:space-between; cursor:pointer; text-align:left; font-weight:500; }
  .kpi-btn strong { color:var(--text); font-size:14px; }
  .kpi-btn.active { border-color:#3b82f6; background:rgba(59,130,246,0.14); color:#93c5fd; }
  .kpi-btn.alert { border-color:rgba(245,158,11,0.45); background:rgba(245,158,11,0.08); color:#fbbf24; }
  .kpi-btn.alert strong { color:#fbbf24; }
  .kpi-btn.alert.active { border-color:#f59e0b; background:rgba(245,158,11,0.24); }
  .kpi-drawer { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:10px; margin-bottom:12px; }
  .kpi-drawer-hdr { display:flex; justify-content:space-between; align-items:center; font-size:11px; color:var(--muted); margin-bottom:8px; text-transform:uppercase; letter-spacing:0.4px; }
  .kpi-chips { display:flex; flex-wrap:wrap; gap:6px; }
  .kpi-chip { background:#0e0e11; border:1px solid var(--border); border-radius:8px; padding:6px 10px; font-size:12px; color:var(--text); cursor:pointer; display:inline-flex; align-items:center; gap:6px; }
  .kpi-chip.sub { border-style:dashed; font-size:11px; padding:5px 9px; }
  .kpi-chip.selected { border-color:#3b82f6; background:rgba(59,130,246,0.18); color:#93c5fd; }
  .kpi-bar { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px; }
  .kpi-pill { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:6px 10px; font-size:12px; color:var(--muted); display:flex; align-items:center; gap:5px; }
  .kpi-pill strong { color:var(--text); font-size:13px; }
  .kpi-pill.alert { border-color:rgba(245,158,11,0.5); background:rgba(245,158,11,0.1); color:#fbbf24; cursor:pointer; }
  .kpi-pill.alert strong { color:#fbbf24; }
</style>
</head>
<body>

<h1>
  <span>Inventario Reparto <small id="count-lbl" style="color:var(--muted);font-weight:400"></small></span>
  <div class="row">
    <a href="/api/backup/download" style="color:#38bdf8;font-size:12px;text-decoration:none;margin-right:8px;">Backup CSV</a>
    <a href="/logout" style="color:var(--muted);font-size:12px;text-decoration:none;">Esci</a>
  </div>
</h1>

<div id="kpiBar" class="kpi-bar"></div>
<div id="kpiDrawer" class="kpi-drawer" style="display:none;"></div>

<div id="kpiBar" class="kpi-bar"></div>

<!-- RICERCA ISTANTANEA + SORT + FILTRI -->
<div class="card">
  <div class="row" style="margin-bottom:8px;">
    <input type="text" id="searchInput" placeholder="Cerca Codice MW (6 cifre), EAN, modello o note..." oninput="renderList()" autofocus>
    <button type="button" class="sec" onclick="clearSearch()" style="padding:10px 12px;">✕</button>
  </div>

  <div class="grid2" style="margin-bottom:8px;">
    <div>
      <label class="lbl">Ordina per</label>
      <select id="sortSelect" onchange="renderList()">
        <option value="esposto">Esposti prima</option>
        <option value="price_asc">Prezzo: crescente</option>
        <option value="price_desc">Prezzo: decrescente</option>
        <option value="qty_desc">Giacenza: alta → bassa</option>
        <option value="qty_asc">Giacenza: bassa → alta</option>
        <option value="name_asc">Nome: A → Z</option>
      </select>
    </div>
    <div>
      <label class="lbl">Stato rapido</label>
      <select id="quickFilter" onchange="renderList()">
        <option value="all">Tutti i prodotti</option>
        <option value="esposti">Solo Esposti</option>
        <option value="non_esposti">Solo Non Esposti</option>
        <option value="changed">Con variazioni oggi (+n / -n)</option>
        <option value="restock">Allerta riassortimento (Esposti ≤ 1 pz)</option>
      </select>
    </div>
  </div>

  <details>
    <summary style="cursor:pointer; color:var(--muted); font-size:13px; font-weight:600;">Filtri hardware & prezzo</summary>
    <div class="grid3" style="margin-top:10px;">
      <div>
        <label class="lbl">Marca</label>
        <select id="fBrand" onchange="renderList()"><option value="">Tutte</option></select>
      </div>
      <div>
        <label class="lbl">Schermo</label>
        <select id="fScreen" onchange="renderList()"><option value="">Tutti</option></select>
      </div>
      <div>
        <label class="lbl">Scheda Video</label>
        <select id="fGpu" onchange="renderList()">
          <option value="">Tutte</option>
          <option value="Dedicata">Dedicata (RTX/GTX/RX)</option>
          <option value="Integrata">Integrata</option>
        </select>
      </div>
      <div>
        <label class="lbl">RAM (GB)</label>
        <select id="fRam" onchange="renderList()"><option value="">Tutte</option></select>
      </div>
      <div>
        <label class="lbl">SSD (GB)</label>
        <select id="fSsd" onchange="renderList()"><option value="">Tutti</option></select>
      </div>
      <div>
        <label class="lbl">Prezzo Max (€)</label>
        <input type="number" id="fMaxPrice" placeholder="es. 800" oninput="renderList()">
      </div>
    </div>
    <div style="text-align:right; margin-top:8px;">
      <button type="button" class="sec" onclick="resetFilters()" style="font-size:12px; padding:6px 10px;">Azzera filtri</button>
    </div>
  </details>
</div>

<!-- AGGIUNGI PRODOTTO / CSV / CHIUSURA GIORNATA -->
<div class="card">
  <details id="addDetails">
    <summary style="cursor:pointer;font-weight:600;font-size:14px;">+ Aggiungi prodotto / Importa CSV / Chiusura giornata</summary>

    <div style="margin-top:12px;">
      <label class="lbl">Leggi da cartellino digitale qrc.mw</label>
      <div class="row">
        <input type="url" id="fetchUrl" placeholder="Incolla link https://qrc.mw/...">
        <button type="button" class="sec" id="fetchBtn" onclick="fetchFromUrl()">Leggi</button>
      </div>
    </div>

    <form id="saveForm" onsubmit="saveProduct(event)" style="margin-top:12px; border-top:1px solid var(--border); padding-top:12px;">
      <input type="hidden" id="pSourceUrl">
      <input type="hidden" id="pSpecsJson" value="{}">
      <label class="lbl">Titolo / Modello</label>
      <input type="text" id="pTitle" placeholder="es. LENOVO IdeaPad Slim 3..." required style="margin-bottom:8px;">
      <div class="grid2" style="margin-bottom:8px;">
        <div>
          <label class="lbl">Codice MW / PIM (6 cifre)</label>
          <input type="text" id="pMw" pattern="[0-9]{6}" placeholder="es. 593522">
        </div>
        <div>
          <label class="lbl">EAN (13 cifre)</label>
          <input type="text" id="pEan" pattern="[0-9]{12,13}" placeholder="es. 0199276800732">
        </div>
      </div>
      <div class="grid2" style="margin-bottom:8px;">
        <div>
          <label class="lbl">Giacenza iniziale</label>
          <input type="number" id="pQty" value="1" min="0">
        </div>
        <div style="padding-top:18px;">
          <label style="font-size:13px;"><input type="checkbox" id="pEsposto"> Esposto in corsia</label>
        </div>
      </div>
      <button type="submit" style="width:100%;">Salva prodotto</button>
    </form>

    <form onsubmit="importCsv(event)" style="margin-top:12px; border-top:1px solid var(--border); padding-top:12px;">
      <label class="lbl">Importa / Aggiorna da CSV MediaWorld</label>
      <div class="row">
        <input type="file" id="csvFile" accept=".csv" required style="padding:7px;">
        <button type="submit" class="sec">Importa</button>
      </div>
    </form>

    <div style="margin-top:12px; border-top:1px solid var(--border); padding-top:12px; display:flex; justify-content:space-between; align-items:center;">
      <span style="font-size:12px; color:var(--muted);">Backup automatico alle 00:00 (azzera +n/-n)</span>
      <button type="button" class="sec" onclick="manualCloseDay()" style="font-size:12px; padding:7px 10px;">Chiudi giornata ora</button>
    </div>
  </details>
</div>

<div id="productList"></div>
<div id="toast" class="toast"></div>

<script>
let PRODUCTS = [];
let KPI_TAB = null;       // 'esposti' | 'pezzi' | 'variazioni' | null
let KPI_SUBFILTER = null; // { mode, brand, categoria, cpu } | null

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => { t.style.display = 'none'; }, 2800);
}

function prepareSearchIndex(p) {
  p._modelWords = tokenizeSmart(`${p.meta.marchio || ''} ${p.meta.modello || ''} ${p.titolo}`);
  p._noteWords = tokenizeSmart(p.note || '');
  p._allWords = [...p._modelWords, ...p._noteWords];
  p._modelSet = new Set(p._modelWords);
  p._noteSet = new Set(p._noteWords);
  return p;
}

async function loadProducts() {
  const res = await fetch('/api/products');
  if (res.status === 401) { location.href = '/login'; return; }
  const data = await res.json();
  PRODUCTS = data.map(prepareSearchIndex);
  populateFilterDropdowns();
  renderList();
}

function populateFilterDropdowns() {
  const brands = new Set(), screens = new Set(), rams = new Set(), ssds = new Set();
  PRODUCTS.forEach(p => {
    if (p.meta.marchio) brands.add(p.meta.marchio);
    if (p.meta.schermo) screens.add(p.meta.schermo);
    if (p.meta.ram) rams.add(p.meta.ram);
    if (p.meta.ssd) ssds.add(p.meta.ssd);
  });

  fillSelect('fBrand', [...brands].sort(), 'Tutte');
  fillSelect('fScreen', [...screens].sort((a,b) => parseFloat(a)-parseFloat(b)), 'Tutti', v => v + '"');
  fillSelect('fRam', [...rams].sort((a,b) => parseInt(a)-parseInt(b)), 'Tutte', v => v + ' GB');
  fillSelect('fSsd', [...ssds].sort((a,b) => parseInt(a)-parseInt(b)), 'Tutti', v => v + ' GB');
}

function fillSelect(id, items, defaultLabel, fmt = v => v) {
  const sel = document.getElementById(id);
  const curr = sel.value;
  sel.innerHTML = `<option value="">${defaultLabel}</option>` +
    items.map(v => `<option value="${v}">${fmt(v)}</option>`).join('');
  if (items.includes(curr)) sel.value = curr;
}

function clearSearch() {
  document.getElementById('searchInput').value = '';
  renderList();
}

function resetFilters() {
  ['fBrand','fScreen','fGpu','fRam','fSsd','fMaxPrice'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('quickFilter').value = 'all';
  renderList();
}

function esc(str) {
  return String(str ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

function levenshtein(a, b) {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  const v0 = Array.from({length: b.length + 1}, (_, i) => i);
  const v1 = new Array(b.length + 1);
  for (let i = 0; i < a.length; i++) {
    v1[0] = i + 1;
    for (let j = 0; j < b.length; j++) {
      const cost = a[i] === b[j] ? 0 : 1;
      v1[j + 1] = Math.min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost);
    }
    for (let j = 0; j <= b.length; j++) v0[j] = v1[j];
  }
  return v1[b.length];
}

function tokenizeSmart(str) {
  return String(str || '')
    .toLowerCase()
    .replace(/([a-zà-ÿ])(\\d)/gi, '$1 $2')
    .replace(/(\\d)([a-zà-ÿ])/gi, '$1 $2')
    .split(/[^a-z0-9à-ÿ]+/)
    .filter(Boolean);
}

function scoreProductSearch(p, rawQ) {
  const q = rawQ.trim().toLowerCase();
  if (!q) return 1;

  // 1. Ricerca puramente numerica -> Codice PIM / EAN / numero esatto
  if (/^[\\d\\s-]+$/.test(q)) {
    const digits = q.replace(/\\D/g, '');
    if (!digits) return 0;
    const ean13 = digits.length === 12 ? '0' + digits : digits;
    if (p.codice_mw && p.codice_mw.includes(digits)) return 100;
    if (p.ean && (p.ean.includes(digits) || p.ean.includes(ean13))) return 100;
    const titleWords = tokenizeSmart(`${p.titolo} ${p.note || ''}`);
    return titleWords.includes(digits) ? 60 : 0;
  }

  // 2. Ricerca testuale / mista -> Fuzzy Ibrido per token
  const qTokens = tokenizeSmart(q);
  if (!qTokens.length) return 1;

  const modelWords = p._modelWords || [];
  const noteWords = p._noteWords || [];
  const allWords = p._allWords || [];
  const modelSet = p._modelSet || new Set();
  const noteSet = p._noteSet || new Set();

  let totalScore = 0;

  for (const qt of qTokens) {
    // Token numerico (es. "3", "5", "16", "512"): match esatto obbligatorio, zero fuzzy
    if (/^\\d+$/.test(qt)) {
      if (modelSet.has(qt)) {
        totalScore += 15;
      } else if (noteSet.has(qt)) {
        totalScore += 6;
      } else {
        return 0;
      }
      continue;
    }

    // Token testuale: match esatto, prefisso o Levenshtein (typo tolerance)
    const maxDist = qt.length >= 7 ? 2 : (qt.length >= 4 ? 1 : 0);
    let bestTokenScore = 0;

    for (let idx = 0; idx < allWords.length; idx++) {
      const w = allWords[idx];
      if (/^\\d+$/.test(w)) continue;
      const inModel = idx < modelWords.length;
      const weight = inModel ? 1.0 : 0.5;

      if (w === qt) {
        bestTokenScore = Math.max(bestTokenScore, 12 * weight);
        break;
      }
      if (w.startsWith(qt) && qt.length >= 2) {
        bestTokenScore = Math.max(bestTokenScore, 9 * weight);
        continue;
      }
      if (maxDist > 0 && Math.abs(w.length - qt.length) <= maxDist) {
        const dist = levenshtein(qt, w);
        if (dist <= maxDist) {
          bestTokenScore = Math.max(bestTokenScore, (8 - dist * 2) * weight);
        }
      }
    }

    if (bestTokenScore === 0) return 0;
    totalScore += bestTokenScore;
  }

  return totalScore;
}

function renderList() {
  const q = document.getElementById('searchInput').value;
  const sortBy = document.getElementById('sortSelect').value;
  const quick = document.getElementById('quickFilter').value;
  const fBrand = document.getElementById('fBrand').value;
  const fScreen = document.getElementById('fScreen').value;
  const fGpu = document.getElementById('fGpu').value;
  const fRam = document.getElementById('fRam').value;
  const fSsd = document.getElementById('fSsd').value;
  const fMaxPrice = parseFloat(document.getElementById('fMaxPrice').value);

  const scored = [];
  for (const p of PRODUCTS) {
    if (quick === 'esposti' && !p.esposto) continue;
    if (quick === 'non_esposti' && p.esposto) continue;
    if (quick === 'changed' && p.delta === 0) continue;
    if (quick === 'restock' && !(p.esposto && p.giacenza <= 1)) continue;
    if (fBrand && p.meta.marchio !== fBrand) continue;
    if (fScreen && p.meta.schermo !== fScreen) continue;
    if (fGpu && p.meta.gpu_tipo !== fGpu) continue;
    if (fRam && p.meta.ram !== fRam) continue;
    if (fSsd && p.meta.ssd !== fSsd) continue;
    if (!isNaN(fMaxPrice) && (p.meta.prezzo === null || p.meta.prezzo > fMaxPrice)) continue;

    if (KPI_SUBFILTER) {
      if (KPI_SUBFILTER.mode === 'esposti' && !p.esposto) continue;
      if (KPI_SUBFILTER.mode === 'variazioni' && p.delta === 0) continue;
      if (KPI_SUBFILTER.brand && (p.meta.marchio || 'Altro') !== KPI_SUBFILTER.brand) continue;
      if (KPI_SUBFILTER.categoria && (p.meta.categoria || 'Computer') !== KPI_SUBFILTER.categoria) continue;
      if (KPI_SUBFILTER.cpu && (p.meta.cpu_brand || '') !== KPI_SUBFILTER.cpu) continue;
    }

    const score = scoreProductSearch(p, q);
    if (score > 0) scored.push({p, score});
  }

  scored.sort((a, b) => {
    if (q.trim() && b.score !== a.score) return b.score - a.score;
    const pa = a.p, pb = b.p;
    if (sortBy === 'esposto') return (pb.esposto - pa.esposto) || pa.titolo.localeCompare(pb.titolo);
    if (sortBy === 'price_asc') return (pa.meta.prezzo ?? 999999) - (pb.meta.prezzo ?? 999999);
    if (sortBy === 'price_desc') return (pb.meta.prezzo ?? -1) - (pa.meta.prezzo ?? -1);
    if (sortBy === 'qty_desc') return pb.giacenza - pa.giacenza;
    if (sortBy === 'qty_asc') return pa.giacenza - pb.giacenza;
    if (sortBy === 'name_asc') return pa.titolo.localeCompare(pb.titolo);
    return 0;
  });

  const filtered = scored.map(x => x.p);
  try { renderKpiUI(); } catch (err) { console.error('KPI error:', err); }
  document.getElementById('count-lbl').textContent = `(${filtered.length}/${PRODUCTS.length})`;

  const container = document.getElementById('productList');
  if (!filtered.length) {
    container.innerHTML = `<div style="text-align:center;color:var(--muted);padding:30px 0;">Nessun prodotto trovato.</div>`;
    return;
  }

  container.innerHTML = filtered.map(p => {
    let deltaBadge = '';
    if (p.delta > 0) deltaBadge = `<span class="badge delta-pos">+${p.delta}</span>`;
    else if (p.delta < 0) deltaBadge = `<span class="badge delta-neg">${p.delta}</span>`;

    const specsRows = Object.entries(p.specs || {})
      .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('');

    const pricePill = p.meta.prezzo !== null
      ? `<span class="price-pill">€ ${Number.isInteger(p.meta.prezzo) ? p.meta.prezzo : p.meta.prezzo.toFixed(2)}</span>`
      : '';

    return `
    <div class="card" id="card-${p.id}">
      <div style="font-weight:700; font-size:15px; margin-bottom:6px;">${esc(p.titolo)}</div>
      <div style="margin-bottom:10px;">
        ${deltaBadge}
        ${p.codice_mw ? `<span class="badge" style="user-select:none;-webkit-user-select:none;">MW: <span style="user-select:all;-webkit-user-select:all;display:inline-block;color:#f4f4f5;">${esc(p.codice_mw)}</span></span>` : ''}
        ${p.ean ? `<span class="badge">EAN: ${esc(p.ean)}</span>` : ''}
        ${p.meta.ram ? `<span class="badge">${esc(p.meta.ram)}GB RAM</span>` : ''}
        ${p.meta.ssd ? `<span class="badge">${esc(p.meta.ssd)}GB SSD</span>` : ''}
        ${p.meta.gpu_tipo === 'Dedicata' ? `<span class="badge" style="color:#c084fc;">GPU Dedicata</span>` : ''}
      </div>

      <div class="row" style="justify-content:space-between; background:#0e0e11; padding:9px; border-radius:10px; gap:6px;">
        <div class="row">
          <button type="button" class="sec qty" onclick="updateQty(${p.id}, -1)">-</button>
          <div style="min-width:54px; text-align:center;">
            <div style="font-size:10px;color:var(--muted)">GIACENZA</div>
            <strong style="font-size:20px;">${p.giacenza}</strong>
          </div>
          <button type="button" class="sec qty" onclick="updateQty(${p.id}, 1)">+</button>
        </div>

        <div class="row" style="gap:6px; flex-wrap:wrap; justify-content:flex-end;">
          ${pricePill}
          <button type="button" class="toggle ${p.esposto ? 'on' : 'off'}" onclick="toggleEsposto(${p.id})">
            ${p.esposto ? 'ESPOSTO: SÌ' : 'ESPOSTO: NO'}
          </button>
        </div>
      </div>

      <!-- SEZIONE NOTE -->
      <div style="margin-top:9px;">
        <div class="row">
          <input type="text" id="note-${p.id}" value="${esc(p.note || '')}" placeholder="Note reparto (es. ultimo esposto, manca scatola...)" onchange="saveNote(${p.id})" style="font-size:13px; padding:8px 10px;">
          <button type="button" class="sec" onclick="saveNote(${p.id})" style="padding:8px 11px; font-size:12px;">Salva</button>
        </div>
      </div>

      <details style="margin-top:8px;">
        <summary style="cursor:pointer; color:var(--muted); font-size:12px;">Scheda tecnica (${Object.keys(p.specs || {}).length} voci) & Opzioni</summary>
        ${specsRows ? `<table class="specs">${specsRows}</table>` : ''}
        <div class="row" style="margin-top:10px;">
          <input type="url" id="enrich-${p.id}" placeholder="Integra scheda da link https://qrc.mw/..." style="padding:8px; font-size:12px;">
          <button type="button" class="sec" onclick="enrichProduct(${p.id})" style="padding:8px 10px; font-size:12px;">Aggiorna</button>
        </div>
        <div style="text-align:right; margin-top:6px;">
          <button type="button" onclick="deleteProduct(${p.id})" style="background:transparent; color:#fb7185; font-size:12px; padding:4px;">Elimina scheda</button>
        </div>
      </details>
    </div>`;
  }).join('');
}

async function updateQty(id, delta) {
  const idx = PRODUCTS.findIndex(x => x.id === id);
  if (idx === -1) return;
  const p = PRODUCTS[idx];
  const prevQty = p.giacenza;
  const prevDelta = p.delta;

  p._pendingQty = (p._pendingQty || 0) + 1;
  p.giacenza = Math.max(0, p.giacenza + delta);
  p.delta = p.giacenza - p.giacenza_base;
  renderList();

  try {
    const res = await fetch(`/api/qty/${id}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({delta})
    });
    if (!res.ok) throw new Error();
    const updated = await res.json();
    p._pendingQty = Math.max(0, (p._pendingQty || 1) - 1);
    if (p._pendingQty === 0) {
      const currIdx = PRODUCTS.findIndex(x => x.id === id);
      if (currIdx !== -1) PRODUCTS[currIdx] = prepareSearchIndex(updated);
      renderList();
    }
  } catch (e) {
    p._pendingQty = Math.max(0, (p._pendingQty || 1) - 1);
    p.giacenza = prevQty;
    p.delta = prevDelta;
    renderList();
    showToast('Errore di rete: giacenza ripristinata');
  }
}

async function toggleEsposto(id) {
  const idx = PRODUCTS.findIndex(x => x.id === id);
  if (idx === -1) return;
  const p = PRODUCTS[idx];
  const prevEsposto = p.esposto;

  p._pendingEsp = (p._pendingEsp || 0) + 1;
  p.esposto = p.esposto ? 0 : 1;
  renderList();

  try {
    const res = await fetch(`/api/esposto/${id}`, { method: 'POST' });
    if (!res.ok) throw new Error();
    const updated = await res.json();
    p._pendingEsp = Math.max(0, (p._pendingEsp || 1) - 1);
    if (p._pendingEsp === 0) {
      const currIdx = PRODUCTS.findIndex(x => x.id === id);
      if (currIdx !== -1) PRODUCTS[currIdx] = prepareSearchIndex(updated);
      renderList();
    }
  } catch (e) {
    p._pendingEsp = Math.max(0, (p._pendingEsp || 1) - 1);
    p.esposto = prevEsposto;
    renderList();
    showToast('Errore di rete: stato esposto ripristinato');
  }
}

async function saveNote(id) {
  const note = document.getElementById(`note-${id}`).value;
  const res = await fetch(`/api/note/${id}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({note})
  });
  if (res.ok) {
    const updated = await res.json();
    const idx = PRODUCTS.findIndex(x => x.id === id);
    if (idx !== -1) PRODUCTS[idx] = prepareSearchIndex(updated);
    showToast('Nota salvata');
  }
}

async function fetchFromUrl() {
  const url = document.getElementById('fetchUrl').value.trim();
  if (!url) return;
  const btn = document.getElementById('fetchBtn');
  btn.textContent = '...';
  const res = await fetch('/api/fetch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url})
  });
  btn.textContent = 'Leggi';
  const data = await res.json();
  if (!res.ok) { showToast(data.error || 'Errore lettura link'); return; }
  document.getElementById('pTitle').value = data.titolo || '';
  document.getElementById('pMw').value = data.codice_mw || '';
  document.getElementById('pEan').value = data.ean || '';
  document.getElementById('pSourceUrl').value = data.source_url || '';
  document.getElementById('pSpecsJson').value = JSON.stringify(data.specifiche || {});
  showToast('Scheda estratta: verifica i codici e salva');
}

async function saveProduct(e) {
  e.preventDefault();
  const payload = {
    titolo: document.getElementById('pTitle').value,
    codice_mw: document.getElementById('pMw').value,
    ean: document.getElementById('pEan').value,
    giacenza: parseInt(document.getElementById('pQty').value || '0'),
    esposto: document.getElementById('pEsposto').checked,
    source_url: document.getElementById('pSourceUrl').value,
    specifiche: document.getElementById('pSpecsJson').value
  };
  const res = await fetch('/api/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) { showToast(data.error || 'Errore salvataggio'); return; }
  document.getElementById('saveForm').reset();
  document.getElementById('pSpecsJson').value = '{}';
  document.getElementById('addDetails').open = false;
  showToast('Prodotto salvato!');
  function toggleRestockFilter() {
  const sel = document.getElementById('quickFilter');
  sel.value = sel.value === 'restock' ? 'all' : 'restock';
  renderList();
}

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

function toggleKpiTab(tab) {
  if (KPI_TAB === tab) {
    KPI_TAB = null;
    KPI_SUBFILTER = null;
  } else {
    KPI_TAB = tab;
    KPI_SUBFILTER = null;
  }
  renderList();
}

function clearKpiFilter() {
  KPI_TAB = null;
  KPI_SUBFILTER = null;
  renderList();
}

function applyKpiSubfilter(mode, brand, categoria, cpu) {
  const same = KPI_SUBFILTER
    && KPI_SUBFILTER.mode === mode
    && KPI_SUBFILTER.brand === brand
    && KPI_SUBFILTER.categoria === categoria
    && KPI_SUBFILTER.cpu === cpu;
  KPI_SUBFILTER = same ? null : { mode, brand, categoria, cpu };
  renderList();
}

function toggleRestockFilter() {
  const sel = document.getElementById('quickFilter');
  sel.value = sel.value === 'restock' ? 'all' : 'restock';
  renderList();
}

function renderKpiUI() {
  let totEsposti = 0, totPezzi = 0, totDelta = 0, totRestock = 0;
  const byBrand = {};
  let intelDelta = 0, intelMoved = 0;

  for (const p of PRODUCTS) {
    const brand = p.meta.marchio || 'Altro';
    const cat = p.meta.categoria || 'Computer';
    if (!byBrand[brand]) {
      byBrand[brand] = {
        esposti: 0, pezzi: 0, delta: 0, moved: 0,
        cats: {
          Computer: { esposti: 0, pezzi: 0, delta: 0, count: 0 },
          Tablet: { esposti: 0, pezzi: 0, delta: 0, count: 0 }
        }
      };
    }
    const b = byBrand[brand];
    b.pezzi += p.giacenza;
    b.delta += p.delta;
    if (p.delta !== 0) b.moved++;
    if (p.esposto) {
      totEsposti++;
      b.esposti++;
      if (p.giacenza <= 1) totRestock++;
    }
    if (b.cats[cat]) {
      b.cats[cat].count++;
      b.cats[cat].pezzi += p.giacenza;
      b.cats[cat].delta += p.delta;
      if (p.esposto) b.cats[cat].esposti++;
    }
    if (p.meta.cpu_brand === 'Intel') {
      intelDelta += p.delta;
      if (p.delta !== 0) intelMoved++;
    }
    totPezzi += p.giacenza;
    totDelta += p.delta;
  }

  const deltaColor = totDelta > 0 ? '#34d399' : (totDelta < 0 ? '#fb7185' : 'var(--text)');
  const deltaStr = totDelta > 0 ? `+${totDelta}` : `${totDelta}`;
  const isRestockActive = document.getElementById('quickFilter').value === 'restock';

  document.getElementById('kpiBar').innerHTML = `
    <button type="button" class="kpi-btn ${KPI_TAB === 'esposti' ? 'active' : ''}" onclick="toggleKpiTab('esposti')">
      <span>Esposti ▾</span><strong>${totEsposti}</strong>
    </button>
    <button type="button" class="kpi-btn ${KPI_TAB === 'pezzi' ? 'active' : ''}" onclick="toggleKpiTab('pezzi')">
      <span>Pezzi totali ▾</span><strong>${totPezzi}</strong>
    </button>
    <button type="button" class="kpi-btn ${KPI_TAB === 'variazioni' ? 'active' : ''}" onclick="toggleKpiTab('variazioni')">
      <span>Variazioni oggi ▾</span><strong style="color:${deltaColor}">${deltaStr}</strong>
    </button>
    <button type="button" class="kpi-btn alert ${isRestockActive ? 'active' : ''}" onclick="toggleRestockFilter()">
      <span>Da riassortire</span><strong>${totRestock}</strong>
    </button>
  `;

  const drawer = document.getElementById('kpiDrawer');
  if (!KPI_TAB) {
    drawer.style.display = 'none';
    return;
  }

  drawer.style.display = 'block';
  const brands = Object.keys(byBrand).sort((a, b) => byBrand[b].pezzi - byBrand[a].pezzi);
  let chipsHtml = '';
  let titleText = '';

  if (KPI_TAB === 'esposti' || KPI_TAB === 'pezzi') {
    const field = KPI_TAB === 'esposti' ? 'esposti' : 'pezzi';
    titleText = KPI_TAB === 'esposti' ? 'Esposti per Marca & Tipologia' : 'Pezzi totali per Marca & Tipologia';

    for (const br of brands) {
      const info = byBrand[br];
      const val = info[field];
      const selBrand = KPI_SUBFILTER && KPI_SUBFILTER.mode === KPI_TAB && KPI_SUBFILTER.brand === br && !KPI_SUBFILTER.categoria;
      chipsHtml += `<button type="button" class="kpi-chip ${selBrand ? 'selected' : ''}" onclick="applyKpiSubfilter('${KPI_TAB}', '${esc(br)}', null, null)">${esc(br)}: <strong>${val}</strong></button>`;

      const hasComp = info.cats.Computer.count > 0;
      const hasTab = info.cats.Tablet.count > 0;
      if (hasComp && hasTab) {
        const selComp = KPI_SUBFILTER && KPI_SUBFILTER.mode === KPI_TAB && KPI_SUBFILTER.brand === br && KPI_SUBFILTER.categoria === 'Computer';
        const selTab = KPI_SUBFILTER && KPI_SUBFILTER.mode === KPI_TAB && KPI_SUBFILTER.brand === br && KPI_SUBFILTER.categoria === 'Tablet';
        chipsHtml += `<button type="button" class="kpi-chip sub ${selComp ? 'selected' : ''}" onclick="applyKpiSubfilter('${KPI_TAB}', '${esc(br)}', 'Computer', null)">↳ ${esc(br)} Computer: <strong>${info.cats.Computer[field]}</strong></button>`;
        chipsHtml += `<button type="button" class="kpi-chip sub ${selTab ? 'selected' : ''}" onclick="applyKpiSubfilter('${KPI_TAB}', '${esc(br)}', 'Tablet', null)">↳ ${esc(br)} Tablet: <strong>${info.cats.Tablet[field]}</strong></button>`;
      }
    }
  } else if (KPI_TAB === 'variazioni') {
    titleText = 'Variazioni oggi (Intel / Marche)';
    const fmtD = d => d > 0 ? `+${d}` : `${d}`;
    const colD = d => d > 0 ? '#34d399' : (d < 0 ? '#fb7185' : 'var(--muted)');

    const selIntel = KPI_SUBFILTER && KPI_SUBFILTER.mode === 'variazioni' && KPI_SUBFILTER.cpu === 'Intel';
    chipsHtml += `<button type="button" class="kpi-chip ${selIntel ? 'selected' : ''}" onclick="applyKpiSubfilter('variazioni', null, null, 'Intel')">Intel: <strong style="color:${colD(intelDelta)}">${fmtD(intelDelta)}</strong></button>`;

    for (const br of brands) {
      const info = byBrand[br];
      const selBrand = KPI_SUBFILTER && KPI_SUBFILTER.mode === 'variazioni' && KPI_SUBFILTER.brand === br && !KPI_SUBFILTER.categoria;
      chipsHtml += `<button type="button" class="kpi-chip ${selBrand ? 'selected' : ''}" onclick="applyKpiSubfilter('variazioni', '${esc(br)}', null, null)">${esc(br)}: <strong style="color:${colD(info.delta)}">${fmtD(info.delta)}</strong></button>`;

      const hasComp = info.cats.Computer.count > 0;
      const hasTab = info.cats.Tablet.count > 0;
      if (hasComp && hasTab) {
        const selComp = KPI_SUBFILTER && KPI_SUBFILTER.mode === 'variazioni' && KPI_SUBFILTER.brand === br && KPI_SUBFILTER.categoria === 'Computer';
        const selTab = KPI_SUBFILTER && KPI_SUBFILTER.mode === 'variazioni' && KPI_SUBFILTER.brand === br && KPI_SUBFILTER.categoria === 'Tablet';
        chipsHtml += `<button type="button" class="kpi-chip sub ${selComp ? 'selected' : ''}" onclick="applyKpiSubfilter('variazioni', '${esc(br)}', 'Computer', null)">↳ ${esc(br)} Computer: <strong style="color:${colD(info.cats.Computer.delta)}">${fmtD(info.cats.Computer.delta)}</strong></button>`;
        chipsHtml += `<button type="button" class="kpi-chip sub ${selTab ? 'selected' : ''}" onclick="applyKpiSubfilter('variazioni', '${esc(br)}', 'Tablet', null)">↳ ${esc(br)} Tablet: <strong style="color:${colD(info.cats.Tablet.delta)}">${fmtD(info.cats.Tablet.delta)}</strong></button>`;
      }
    }
  }

  drawer.innerHTML = `
    <div class="kpi-drawer-hdr">
      <span>${titleText}</span>
      <button type="button" onclick="clearKpiFilter()" style="background:transparent;color:#fb7185;padding:0;font-size:11px;">Chiudi ✕</button>
    </div>
    <div class="kpi-chips">${chipsHtml}</div>
  `;
}

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

loadProducts();
}

async function importCsv(e) {
  e.preventDefault();
  const fileInput = document.getElementById('csvFile');
  if (!fileInput.files.length) return;
  const fd = new FormData();
  fd.append('csv_file', fileInput.files[0]);
  const res = await fetch('/api/import-csv', { method: 'POST', body: fd });
  const data = await res.json();
  showToast(data.message || data.error);
  if (res.ok) {
    fileInput.value = '';
    loadProducts();
  }
}

async function enrichProduct(id) {
  const url = document.getElementById(`enrich-${id}`).value.trim();
  if (!url) return;
  const res = await fetch(`/api/enrich/${id}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url})
  });
  const data = await res.json();
  if (!res.ok) { showToast(data.error || 'Errore aggiornamento'); return; }
  showToast('Scheda tecnica integrata!');
  loadProducts();
}

async function deleteProduct(id) {
  if (!confirm('Eliminare definitivamente questo prodotto?')) return;
  const res = await fetch(`/api/delete/${id}`, { method: 'POST' });
  if (res.ok) {
    PRODUCTS = PRODUCTS.filter(x => x.id !== id);
    renderList();
    showToast('Prodotto eliminato');
  }
}

async function manualCloseDay() {
  if (!confirm('Salvare il backup CSV ora e azzerare tutti i contatori +n/-n?')) return;
  const res = await fetch('/api/close-day', { method: 'POST' });
  if (res.ok) {
    showToast('Backup salvato e contatori azzerati!');
    loadProducts();
  }
}

loadProducts();
</script>
</body>
</html>"""


async def login_page(request: Request):
    return HTMLResponse(render_login())


async def login_submit(request: Request):
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    now = time.time()
    rec = LOGIN_ATTEMPTS.get(ip, {"count": 0, "until": 0})

    if now < rec["until"]:
        wait = int(rec["until"] - now)
        return HTMLResponse(render_login(f"Troppi tentativi. Riprova tra {wait}s."), status_code=429)

    form = await request.form()
    password = str(form.get("password") or "").strip()
    remember = str(form.get("remember") or "")

    if check_password_hash(PWD_HASH, password):
        LOGIN_ATTEMPTS.pop(ip, None)
        resp = RedirectResponse("/", status_code=303)
        set_session_cookie(resp, remember=(remember == "1"))
        return resp

    rec["count"] += 1
    if rec["count"] >= MAX_ATTEMPTS:
        rec["until"] = now + LOCKOUT_SECONDS
        rec["count"] = 0
    LOGIN_ATTEMPTS[ip] = rec
    return HTMLResponse(render_login("Password errata."), status_code=401)


async def logout(request: Request):
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


async def index(request: Request):
    return HTMLResponse(HTML_SPA)


async def api_products(request: Request):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM prodotti ORDER BY esposto DESC, titolo ASC").fetchall()
    return JSONResponse([serialize_product(r) for r in rows])


async def api_fetch(request: Request):
    data = await request.json()
    url = (data.get("url") or "").strip()
    try:
        return JSONResponse(scrape_mw(url))
    except Exception as e:
        return JSONResponse({"error": f"Errore lettura link: {e}"}, status_code=400)


async def api_save(request: Request):
    data = await request.json()
    titolo = (data.get("titolo") or "").strip()
    codice_mw = normalize_mw(data.get("codice_mw"))
    ean = normalize_ean(data.get("ean"))
    giacenza = max(0, int(data.get("giacenza") or 0))
    esposto = 1 if data.get("esposto") else 0
    specifiche = data.get("specifiche") or "{}"
    if isinstance(specifiche, dict):
        specifiche = json.dumps(specifiche)
    source_url = data.get("source_url") or ""

    if not codice_mw and not ean:
        return JSONResponse({"error": "Inserisci almeno uno tra Codice MW (6 cifre) o EAN (13 cifre)."}, status_code=400)

    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO prodotti (codice_mw, ean, titolo, specifiche, giacenza, giacenza_base, esposto, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (codice_mw, ean, titolo, specifiche, giacenza, giacenza, esposto, source_url),
            )
        return JSONResponse({"ok": True})
    except sqlite3.IntegrityError:
        return JSONResponse({"error": "Codice MW o EAN già presente nel database."}, status_code=409)


async def api_import_csv(request: Request):
    try:
        form = await request.form()
        csv_file = form.get("csv_file")
        if not csv_file:
            return JSONResponse({"error": "Nessun file selezionato."}, status_code=400)

        raw = await csv_file.read()
        content = raw.decode("utf-8-sig", errors="replace")
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
                specs = {k: row[k].strip() for k in spec_cols if row.get(k) and row[k].strip()}

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
                           SET titolo = ?, specifiche = ?, giacenza = ?, giacenza_base = ?, esposto = ?,
                               codice_mw = COALESCE(?, codice_mw), ean = COALESCE(?, ean),
                               updated_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (titolo, json.dumps(old_specs), giacenza, giacenza, esposto, codice_mw, ean, existing["id"]),
                    )
                    aggiornati += 1
                else:
                    conn.execute(
                        """INSERT INTO prodotti (codice_mw, ean, titolo, specifiche, giacenza, giacenza_base, esposto)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (codice_mw, ean, titolo, json.dumps(specs), giacenza, giacenza, esposto),
                    )
                    inseriti += 1

        return JSONResponse({"message": f"CSV importato: {inseriti} nuovi, {aggiornati} aggiornati."})
    except Exception as e:
        return JSONResponse({"error": f"Errore CSV: {e}"}, status_code=400)


async def api_qty(request: Request):
    pid = int(request.path_params["pid"])
    data = await request.json()
    delta = int(data.get("delta", 0))
    with get_db() as conn:
        conn.execute(
            "UPDATE prodotti SET giacenza = MAX(0, giacenza + ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (delta, pid),
        )
        row = conn.execute("SELECT * FROM prodotti WHERE id = ?", (pid,)).fetchone()
    return JSONResponse(serialize_product(row))


async def api_esposto(request: Request):
    pid = int(request.path_params["pid"])
    with get_db() as conn:
        conn.execute(
            "UPDATE prodotti SET esposto = CASE WHEN esposto = 1 THEN 0 ELSE 1 END, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pid,),
        )
        row = conn.execute("SELECT * FROM prodotti WHERE id = ?", (pid,)).fetchone()
    return JSONResponse(serialize_product(row))


async def api_note(request: Request):
    pid = int(request.path_params["pid"])
    data = await request.json()
    note = str(data.get("note", ""))
    with get_db() as conn:
        conn.execute(
            "UPDATE prodotti SET note = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (note, pid),
        )
        row = conn.execute("SELECT * FROM prodotti WHERE id = ?", (pid,)).fetchone()
    return JSONResponse(serialize_product(row))


async def api_enrich(request: Request):
    pid = int(request.path_params["pid"])
    data = await request.json()
    url = (data.get("url") or "").strip()
    try:
        scraped = scrape_mw(url)
        with get_db() as conn:
            row = conn.execute("SELECT specifiche, codice_mw, ean FROM prodotti WHERE id = ?", (pid,)).fetchone()
            if not row:
                return JSONResponse({"error": "Prodotto non trovato"}, status_code=404)
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
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": f"Errore lettura link: {e}"}, status_code=400)


async def api_delete(request: Request):
    pid = int(request.path_params["pid"])
    with get_db() as conn:
        conn.execute("DELETE FROM prodotti WHERE id = ?", (pid,))
    return JSONResponse({"ok": True})


async def api_close_day(request: Request):
    run_nightly_backup_and_reset()
    return JSONResponse({"ok": True})


async def download_backup(request: Request):
    latest_file = os.path.join(BACKUP_DIR, "giacenze_latest.csv")
    if not os.path.exists(latest_file):
        run_nightly_backup_and_reset()
    return FileResponse(latest_file, media_type="text/csv", filename="giacenze_backup.csv")


async def pwa_manifest(request: Request):
    return JSONResponse({
        "name": "droid-wms — Gestionale Reparto",
        "short_name": "droid-wms",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#09090b",
        "theme_color": "#09090b",
        "orientation": "portrait",
        "icons": [
            {
                "src": "/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            }
        ]
    })


async def pwa_sw(request: Request):
    js = "self.addEventListener('install', () => self.skipWaiting()); self.addEventListener('fetch', () => {});"
    return Response(js, media_type="application/javascript")


async def pwa_icon(request: Request):
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="112" fill="#09090b"/>
  <rect x="28" y="28" width="456" height="456" rx="92" fill="none" stroke="#27272a" stroke-width="12"/>
  <path d="M128 176l128-64 128 64v160l-128 64-128-64z" fill="none" stroke="#e11d48" stroke-width="26" stroke-linejoin="round"/>
  <path d="M128 176l128 64 128-64M256 240v160" fill="none" stroke="#f4f4f5" stroke-width="22" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""
    return Response(svg, media_type="image/svg+xml")


routes = [
    Route("/manifest.json", pwa_manifest, methods=["GET"]),
    Route("/sw.js", pwa_sw, methods=["GET"]),
    Route("/icon.svg", pwa_icon, methods=["GET"]),
    Route("/login", login_page, methods=["GET"]),
    Route("/login", login_submit, methods=["POST"]),
    Route("/logout", logout, methods=["GET"]),
    Route("/", index, methods=["GET"]),
    Route("/api/products", api_products, methods=["GET"]),
    Route("/api/fetch", api_fetch, methods=["POST"]),
    Route("/api/save", api_save, methods=["POST"]),
    Route("/api/import-csv", api_import_csv, methods=["POST"]),
    Route("/api/qty/{pid:int}", api_qty, methods=["POST"]),
    Route("/api/esposto/{pid:int}", api_esposto, methods=["POST"]),
    Route("/api/note/{pid:int}", api_note, methods=["POST"]),
    Route("/api/enrich/{pid:int}", api_enrich, methods=["POST"]),
    Route("/api/delete/{pid:int}", api_delete, methods=["POST"]),
    Route("/api/close-day", api_close_day, methods=["POST"]),
    Route("/api/backup/download", download_backup, methods=["GET"]),
]

app = Starlette(routes=routes, middleware=[Middleware(GZipMiddleware, minimum_size=500), Middleware(AuthMiddleware)])
