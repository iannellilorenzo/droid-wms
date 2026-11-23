# ⚡ droid-wms (v2.2.1)

Gestionale inventario self-hosted ad alte prestazioni progettato per girare in locale su uno smartphone Android dedicato e accessibile via browser da qualsiasi dispositivo mobile.

Costruito sullo stack asincrono **Starlette + Uvicorn + `selectolax`**, la **v2.2.1** porta la reattività dell'interfaccia a livello istantaneo grazie all'**Optimistic UI**, al motore di **ricerca Fuzzy Ibrido** con ancoraggio numerico, alla compressione **GZip** e all'ottimizzazione del database **SQLite in modalità WAL**.

## 🛠️ Dev Stack & Architettura (v2.2.1)

L'intera infrastruttura gira senza root su un **Samsung Galaxy S22** trasformato in micro-server always-on:

* **Host & Runtime:** Android + **Termux** (con `termux-wake-lock`, `Termux:Boot` per l'avvio automatico e `proot` chroot per la risoluzione DNS nativa).

* **Backend:** **Python 3** + **Starlette** servito in produzione su **Uvicorn** (`--no-access-log` per ridurre le scritture su flash), con `GZipMiddleware` per comprimere i payload JSON/HTML fino all'80% e thread daemon in background con fuso orario `Europe/Rome` (`tzdata`).

* **Frontend & Optimistic UI:** **Single-Page App (SPA)** in HTML/CSS/Vanilla JS puro: caricamento unico del catalogo con indice pre-tokenizzato in RAM, aggiornamenti **Optimistic UI a 0 ms** sui contatori e sugli stati (con sincronizzazione asincrona e rollback automatico in caso di assenza di rete).

* **Motore di Ricerca Fuzzy Ibrido:** ricerca multi-token intelligente agnostica rispetto al brand, con tolleranza agli errori di battitura (distanza di **Levenshtein** + match per prefisso sulle parole) combinata ad **ancoraggio numerico rigoroso** (per evitare falsi positivi tra serie diverse, es. *Slim 3* vs *Slim 5*) e ordinamento dinamico per pertinenza.

* **Scraping Engine:** Pipeline ibrida **Regex + `selectolax`** (parser HTML nativo in C) con rimozione preventiva dei payload immagini Base64 per estrazione fulminea dai link `qrc.mw`.

* **Database & Backup:** **SQLite3** (`inventario.db`) configurato in modalità **`WAL` (`Write-Ahead Logging`)** con `synchronous=NORMAL` e indici B-Tree su Codice PIM/MW (6 cifre) ed EAN-13, affiancato da export automatici giornalieri in **CSV** (`backups/`).

* **Networking & Tunneling:** **ngrok** su dominio statico HTTPS con processi persistenti gestiti via **tmux**.

* **Sicurezza:** Autenticazione protetta da hash **PBKDF2-SHA256** (`security.json`), rate-limiting anti-bruteforce in memoria e cookie firmati (`itsdangerous`) `HttpOnly` / `Secure` a persistenza estesa (10 anni con auto-rinnovo ad ogni richiesta).

## ✨ Funzionalità (v2.2.1)

* **Ricerca Fuzzy Ibrida & Pertinenza:** supporta ricerche parziali, errori di battitura e codici senza spazi (es. `idepad slim3`, `lenvo`), distinguendo in modo esatto i numeri di serie/modello, il Codice PIM (6 cifre) e l'EAN-13.

* **Interazioni Istantanee (Optimistic UI):** i tasti `+`, `-` ed `ESPOSTO` aggiornano immediatamente l'interfaccia senza attendere la latenza della rete mobile, sincronizzando il database in background.

* **Delta Giacenze Giornaliero (`+n` / `-n`):** confronto automatico tra la giacenza attuale e quella di inizio giornata (`giacenza_base`). Mostra un badge verde `+n` o rosso `-n` su ogni scheda modificata e scompare automaticamente se il conteggio torna in pari.

* **Chiusura Giornata & Backup Automatico (`00:00`):** ogni notte a mezzanotte (`Europe/Rome`) salva un report CSV completo in `backups/giacenze_YYYY-MM-DD.csv` (scaricabile anche istantaneamente dall'header) e consolida le giacenze finali come nuova base per il giorno successivo, azzerando i contatori. Include anche un pulsante per la chiusura manuale anticipata.

* **Note di Reparto:** campo testuale dedicato su ogni scheda prodotto per segnare informazioni rapide (es. *"ultimo esposto"*, *"manca scatola"*), completamente indicizzato nella ricerca.

* **Ordinamento & Filtri Dinamici Hardware / Prezzo:** ordinamento istantaneo (esposti, prezzo crescente/decrescente, giacenza, alfabetico) e filtri auto-popolati per **Marca**, **Diagonale Schermo**, **RAM**, **SSD**, **Scheda Video (Dedicata vs Integrata)**, **Prezzo Max** e stato variazioni.

* **UI Mobile-First & Long-Press PIM:** pillola prezzo arrotondata (`€ PREZZO`) nella barra azioni e isolamento CSS (`user-select`) sulle 6 cifre del Codice PIM (`MW: xxxxxx`) per selezionare esattamente il numero al long-press su smartphone senza catturare spazi.

* **Scraping QR & Import CSV Massivo:** lettura automatica delle schede tecniche da link `qrc.mw` e aggiornamento massivo dell'inventario da file CSV.

## 📁 Struttura del Progetto

```text
droid-wms/
├── app.py              # Backend Starlette, GZip, SQLite WAL, scheduler 00:00, fuzzy search e SPA
├── requirements.txt    # Dipendenze Python (starlette, uvicorn, selectolax, tzdata, ecc.)
├── .gitignore          # Esclusione file sensibili, database, backup CSV e venv
├── README.md           # Documentazione del progetto
├── security.json       # [NON TRACCIATO] Secret key e hash PBKDF2-SHA256
├── inventario.db       # [NON TRACCIATO] Database SQLite (modalità WAL)
└── backups/            # [NON TRACCIATO] Storico CSV delle chiusure giornaliere
```

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
uvicorn app:app --host 127.0.0.1 --port 8000 --no-access-log
```

In una seconda sessione `tmux`, avvia il tunnel statico:

```bash
termux-chroot ngrok http --url=<IL-TUO-DOMINIO>.ngrok-free.dev 8000
```

## 🔒 Accesso, Credenziali & Contatti

> **DOCUMENTO DESECRETATO — CLASSIFICAZIONE: LIVELLO OMEGA / EYES ONLY**
>
> Rilasciato ai sensi del *Freedom of Information Act (Divisione Elettronica & Corsia PC)*.
>
> Alcune sezioni restano oscurate per ragioni di sicurezza operativa e tutela dell'inventario.

* **Endpoint di produzione:** `https://████████████████████.ngrok-free.dev`

* **Master Password:** `████████████████████████████████████` *(32+ char alfanumerici + simboli, generata via Bitwarden e custodita nel caveau di ████████)*

* **Chiave crittografica (`security.json`):** `[REDACTED — ESCLUSA DAL REPO]`

* **Soggetto responsabile dell'infrastruttura:** `Ian█████ Lor████` *(nome in codice: "Il Tecnico del Turno Chiusura")*

### 📞 Procedura per richiedere l'accesso

Per ottenere le credenziali temporanee o l'abilitazione del dispositivo di reparto:

1. Contattare **████████ ████████** tramite canale criptato su **████████** oppure inviare un pizzino cifrato al **Reparto ████████, Corsia ██, Scaffale ██**.

2. In alternativa, presentarsi fisicamente al **███████████ di ████ ██ █████** chiedendo di *"quello che sistema i cartellini col telefono"*.

3. Ogni richiesta sprovvista di nulla osta verrà automaticamente inoltrata a **\[REDACTED\]**, all'ufficio **████████** e al supervisore di turno **██████████**.

