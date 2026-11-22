# 📦 droid-wms (v1.0)

Gestionale leggero e self-hosted per il controllo rapido di giacenze ed esposizione in corsia, progettato per girare interamente in locale su uno smartphone Android dedicato e accessibile via browser da qualsiasi dispositivo mobile.

---

## 🛠️ Dev Stack & Architettura

L'intera infrastruttura gira senza root su un **Samsung Galaxy S22** trasformato in micro-server always-on:

* **Host & Runtime:** Android + **Termux** (con `termux-wake-lock`, `Termux:Boot` per l'avvio automatico e `proot` per la risoluzione DNS in ambiente chroot).
* **Backend:** **Python 3** + **Flask** servito in produzione tramite **Waitress** (WSGI server multi-thread leggero).
* **Database:** **SQLite3** (`inventario.db`) in locale con indicizzazione su chiavi primarie di reparto (Codice PIM/MW a 6 cifre ed EAN a 13 cifre).
* **Ingestion & Scraping:** `requests` + `BeautifulSoup4` per l'estrazione automatica di titoli, codici e schede tecniche dai link dei cartellini digitali (`qrc.mw`), affiancato da parser CSV per l'importazione massiva.
* **Networking & Tunneling:** **ngrok** su dominio statico HTTPS con gestione persistente dei processi tramite **tmux**.
* **Sicurezza:** Autenticazione protetta da hash **PBKDF2-SHA256** (`security.json`), rate-limiting anti-bruteforce in memoria e cookie di sessione `HttpOnly` / `Secure` a persistenza estesa (10 anni con rinnovo automatico ad ogni richiesta).

---

## ✨ Funzionalità (v1.0)

* **Ricerca rapida:** lookup immediato per Codice PIM (6 cifre), EAN-13 (con normalizzazione automatica degli zeri iniziali) o nome modello.
* **Aggiornamento in tempo reale:** modifica giacenze (`+1` / `-1`) e stato esposto (`SÌ` / `NO`) tramite chiamate asincrone senza ricaricamento completo della pagina.
* **Importazione da QR / Link:** estrazione automatica della scheda tecnica da URL e arricchimento dei prodotti già presenti a database.
* **Importazione massiva CSV:** caricamento e aggiornamento in blocco dell'inventario da export tabellari con gestione intelligente dei duplicati.

---

## 📁 Struttura del Progetto

```text
droid-wms/
├── app.py              # Applicazione Flask, API JSON, scraper e UI HTML
├── requirements.txt    # Dipendenze Python (flask, waitress, requests, beautifulsoup4)
├── .gitignore          # Esclusione file sensibili, database locale e venv
├── README.md           # Documentazione del progetto
├── security.json       # [NON TRACCIATO] Secret key e hash PBKDF2-SHA256
└── inventario.db       # [NON TRACCIATO] Database SQLite dell'inventario
```

---

## 🚀 Setup & Avvio Rapido

### 1. Clona la repository e prepara l'ambiente virtuale

```bash
git clone https://github.com/iannellilorenzo/droid-wms.git
cd droid-wms
python -m venv venv
source venv/bin/activate
```

### 2. Installa le dipendenze

```bash
pip install flask waitress requests beautifulsoup4
```

### 3. Primo avvio (configurazione password)

Lancia l'applicazione una prima volta per generare `security.json` e impostare la master password (minimo 32 caratteri):

```bash
python app.py
```

### 4. Avvio in produzione (Waitress + ngrok)

```bash
waitress-serve --host=127.0.0.1 --port=8000 app:app
```

In una seconda sessione `tmux`, avvia il tunnel statico:

```bash
termux-chroot ngrok http --url=<IL-TUO-DOMINIO>.ngrok-free.dev 8000
```

---

## 🔒 Accesso, Credenziali & Contatti

> **DOCUMENTO DESECRETATO — CLASSIFICAZIONE: LIVELLO OMEGA / EYES ONLY**  
> Rilasciato ai sensi del *Freedom of Information Act (Divisione Elettronica & Corsia PC)*.  
> Alcune sezioni restano oscurate per ragioni di sicurezza operativa e tutela dell'inventario.

* **Endpoint di produzione:** `https://████████████████████.ngrok-free.dev`
* **Master Password:** `████████████████████████████████████` *(custodita nel caveau di ████████)*
* **Chiave crittografica (`security.json`):** `[REDACTED — NON INCLUSA NEL REPO]`
* **Soggetto responsabile dell'infrastruttura:** `Ian█████ Lor████`

### 📞 Procedura per richiedere l'accesso

Per ottenere le credenziali temporanee o l'abilitazione del dispositivo di reparto:

1. Contattare **████████ ████████** tramite canale criptato su **████████** oppure inviare un pizzino cifrato al **Reparto ████████, Corsia ██, Scaffale ██**.
2. In alternativa, presentarsi fisicamente al **███████████ di ████ ██ █████** chiedendo di *"quello che sistema i cartellini col telefono"*.
3. Ogni richiesta sprovvista di nulla osta verrà automaticamente inoltrata a **[REDACTED]**, all'ufficio **████████** e al supervisore di turno **██████████**.

