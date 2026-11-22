# ⚡ droid-wms (v2.1)

Gestionale inventario self-hosted ad alte prestazioni progettato per girare in locale su uno smartphone Android dedicato e accessibile via browser da qualsiasi dispositivo mobile.

Costruito sullo stack asincrono **Starlette + Uvicorn + `selectolax`** della v2.0, la **v2.1** introduce gli strumenti operativi completi per la gestione quotidiana della corsia: tracciamento automatico delle variazioni di giacenza (`+n`/`-n`), chiusura giornata programmata a mezzanotte con export CSV, note di reparto, filtri hardware avanzati e ottimizzazioni UI per l'uso rapido da smartphone.

---

## 🛠️ Dev Stack & Architettura (v2.1)

L'intera infrastruttura gira senza root su un **Samsung Galaxy S22** trasformato in micro-server always-on:

* **Host & Runtime:** Android + **Termux** (con `termux-wake-lock`, `Termux:Boot` per l'avvio automatico e `proot` chroot per la risoluzione DNS nativa).
* **Backend:** **Python 3** + **Starlette** servito in produzione su **Uvicorn** (motore ASGI puro, ultra-leggero e asincrono) + thread daemon in background con fuso orario `Europe/Rome` (`tzdata`).
* **Frontend:** **Single-Page App (SPA)** in HTML/CSS/Vanilla JS puro: caricamento unico del catalogo in memoria, ricerca e filtri istantanei client-side a **0 ms di latenza** e interazioni ottimizzate per schermo touch.
* **Scraping Engine:** Pipeline ibrida **Regex + `selectolax`** (parser HTML nativo in C) con rimozione preventiva dei payload immagini Base64 per estrazione fulminea dai link `qrc.mw`.
* **Database & Backup:** **SQLite3** (`inventario.db`) in locale indicizzato su Codice PIM/MW (6 cifre) ed EAN-13, affiancato da export automatici giornalieri in **CSV** (`backups/`).
* **Networking & Tunneling:** **ngrok** su dominio statico HTTPS con processi persistenti gestiti via **tmux**.
* **Sicurezza:** Autenticazione protetta da hash **PBKDF2-SHA256** (`security.json`), rate-limiting anti-bruteforce in memoria e cookie firmati (`itsdangerous`) `HttpOnly` / `Secure` a persistenza estesa (10 anni con auto-rinnovo ad ogni richiesta).

---

## ✨ Funzionalità (v2.1)

* **Delta Giacenze Giornaliero (`+n` / `-n`):** confronto automatico tra la giacenza attuale e quella di inizio giornata (`giacenza_base`). Mostra un badge verde `+n` o rosso `-n` su ogni scheda modificata e scompare automaticamente se il conteggio torna in pari.
* **Chiusura Giornata & Backup Automatico (`00:00`):** ogni notte a mezzanotte (`Europe/Rome`) salva un report CSV completo in `backups/giacenze_YYYY-MM-DD.csv` (scaricabile anche istantaneamente dall'header) e consolida le giacenze finali come nuova base per il giorno successivo, azzerando i contatori. Include anche un pulsante per la chiusura manuale anticipata.
* **Note di Reparto:** campo testuale dedicato su ogni scheda prodotto per segnare informazioni rapide (es. *"ultimo esposto"*, *"manca scatola"*), completamente indicizzato nella barra di ricerca.
* **Ordinamento & Filtri Hardware / Prezzo:** ordinamento istantaneo (esposti, prezzo crescente/decrescente, giacenza, alfabetico) e filtri combinabili per **Marca**, **Diagonale Schermo**, **RAM**, **SSD**, **Scheda Video (Dedicata vs Integrata)**, **Prezzo Max** e stato variazioni.
* **UI Mobile-First & Long-Press PIM:** badge prezzo arrotondato (`€ PREZZO`) nella barra azioni e isolamento CSS (`user-select`) sulle 6 cifre del Codice PIM (`MW: xxxxxx`) per selezionare esattamente il numero al long-press su smartphone senza catturare spazi.
* **Scraping QR & Import CSV Massivo:** lettura automatica delle schede tecniche da link `qrc.mw` e aggiornamento massivo dell'inventario da file CSV.

---

## 📁 Struttura del Progetto

```text
droid-wms/
├── app.py              # Backend Starlette, scheduler 00:00, API JSON, scraper e UI SPA
├── requirements.txt    # Dipendenze Python (starlette, uvicorn, selectolax, tzdata, ecc.)
├── .gitignore          # Esclusione file sensibili, database, backup CSV e venv
├── README.md           # Documentazione del progetto
├── security.json       # [NON TRACCIATO] Secret key e hash PBKDF2-SHA256
├── inventario.db       # [NON TRACCIATO] Database SQLite dell'inventario
└── backups/            # [NON TRACCIATO] Storico CSV delle chiusure giornaliere
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
pip install -r requirements.txt
```

### 3. Primo avvio (configurazione password strong)

Lancia l'applicazione una prima volta per generare `security.json` e impostare la master password (almeno 32 caratteri):

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

### 4. Avvio in produzione (Uvicorn + ngrok)

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
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
* **Master Password:** `████████████████████████████████████` *(32+ char alfanumerici + simboli, generata via Bitwarden e custodita nel caveau di ████████)*
* **Chiave crittografica (`security.json`):** `[REDACTED — ESCLUSA DAL REPO]`
* **Soggetto responsabile dell'infrastruttura:** `Ian█████ Lor████` *(nome in codice: "Il Tecnico del Turno Chiusura")*

### 📞 Procedura per richiedere l'accesso

Per ottenere le credenziali temporanee o l'abilitazione del dispositivo di reparto:

1. Contattare **████████ ████████** tramite canale criptato su **████████** oppure inviare un pizzino cifrato al **Reparto ████████, Corsia ██, Scaffale ██**.
2. In alternativa, presentarsi fisicamente al **███████████ di ████ ██ █████** chiedendo di *"quello che sistema i cartellini col telefono"*.
3. Ogni richiesta sprovvista di nulla osta verrà automaticamente inoltrata a **[REDACTED]**, all'ufficio **████████** e al supervisore di turno **██████████**.

