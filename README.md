# ⚡ droid-wms (v3.0.0)

Gestionale inventario self-hosted ad alte prestazioni progettato per girare in locale su uno smartphone Android dedicato e accessibile via browser come **Progressive Web App (PWA)** nativa.

Costruito sullo stack asincrono **Starlette + Uvicorn + `selectolax`**, la **v3.0.0** unisce un restyling completo **True Black OLED mobile-first** (header su una riga con menu `⋮`, ricerca sticky, pulsante `+` flottante ad altezza pollice, card compatte e copia rapida del codice MW) a un'architettura ottimizzata su ogni livello: **caching `ETag` (`304 Not Modified`) sia sull'HTML che sulle API**, **connection pooling HTTP**, **thread-safety su SQLite WAL**, **scheduler notturno a risveglio mirato** e **ricerca fuzzy zero-allocation**.

## 🛠️ Dev Stack & Architettura (v3.0.0)

L'intera infrastruttura gira senza root su un **Samsung Galaxy S22** trasformato in micro-server always-on:

* **Host & Runtime:** Android + **Termux** (con `termux-wake-lock`, `Termux:Boot` per l'avvio automatico e `proot` chroot per la risoluzione DNS nativa).

* **Backend Asincrono & Caching HTTP (`0 Byte` su reload):** **Python 3** + **Starlette** servito in produzione su **Uvicorn** (`--no-access-log` per ridurre le scritture su flash), con `GZipMiddleware` (compressione payload fino all'80%), **headers `ETag` (`304 Not Modified`) sia su `GET /` (hash SHA-256 pre-calcolato all'avvio) sia su `GET /api/products`** per azzerare il traffico dati sul tunnel quando non ci sono modifiche, e scheduler notturno `Europe/Rome` (`tzdata`) con calcolo preciso dello sleep fino a mezzanotte per minimizzare i wakeup della CPU.

* **Scraping Non Bloccante & Connection Pooling:** Pipeline ibrida **Regex pre-compilate + `selectolax`** (parser HTML nativo in C) eseguita su thread separato tramite `asyncio.to_thread()` con sessione persistente `requests.Session()` (riuso connessioni TLS/keep-alive), validazione dello schema URL e rimozione preventiva delle immagini Base64.

* **Frontend OLED, PWA One-Tap & Event Delegation:** **Single-Page App (SPA)** in tema **True Black OLED (`#000000`)** installabile come **PWA** (`/manifest.json`, `/sw.js`, `/icon.svg`) con pulsante diretto **📲 Installa app** (`beforeinstallprompt`) che si nasconde automaticamente in modalità `standalone`. Gestione centralizzata degli eventi tramite **Event Delegation (`data-action` + `switch/case`)**, comparatori pre-risolti fuori dal loop di ordinamento, wrapper di rete resiliente (`apiFetch` con supporto nativo HTTP `304`), **aggiornamento DOM chirurgico della singola card (`updateSingleCard`)** con preservazione dello stato aperto (schede tecniche e bozze note) e risposte **Optimistic UI a 0 ms** con rollback automatico su errore di rete.

* **Motore di Ricerca Fuzzy Zero-Allocation:** Ricerca multi-token agnostica rispetto al brand con debounce a **65 ms**, distanza di **Levenshtein su buffer `Int32Array` riusabili** (zero allocazioni e zero micro-lag da Garbage Collector durante la digitazione) combinata ad **ancoraggio numerico rigoroso** (per distinguere con esattezza serie diverse come *Slim 3* vs *Slim 5*) e ordinamento dinamico per pertinenza.

* **Database & Backup:** **SQLite3** (`inventario.db`) configurato in modalità **`WAL` (`Write-Ahead Logging`)** con sincronizzazione thread-safe (`DB_LOCK`), chiusura deterministica delle connessioni (`contextlib.closing`), query su colonne mirate, fast-path nell'estrazione metadati, `synchronous=NORMAL`, `temp_store=MEMORY`, `mmap_size=64MB` e checkpoint WAL passivo durante l'export giornaliero in **CSV** (`backups/`).

* **Networking & Tunneling:** **ngrok** su dominio statico HTTPS con processi persistenti gestiti via **tmux**.

* **Sicurezza:** Autenticazione protetta da hash **PBKDF2-SHA256** (`security.json`), rate-limiting anti-bruteforce in memoria con pulizia automatica dei record scaduti e cookie firmati (`itsdangerous`) `HttpOnly` / `Secure` a persistenza estesa (10 anni con auto-rinnovo ad ogni richiesta).

## ✨ Funzionalità (v3.0.0)

* **Interfaccia Mobile-First ad Altezza Pollice:**

  * **Header pulito su una riga:** mostra solo `droid-wms (n/tot)`, il pulsante **📋 Report** e il menu **⋮** a comparsa (che racchiude *Installa app*, *Scarica Backup CSV*, *Importa CSV*, *Chiudi giornata* ed *Esci*, tenendo le azioni delicate lontane dai tocchi accidentali).

  * **Barra di ricerca sticky & Filtri a comparsa:** resta fissata in alto durante lo scorrimento con tasto **Filtri** dotato di contatore attivo `(n)`.

  * **Tasto `+` flottante (FAB) in basso a destra:** raggiungibile con una mano sola per aprire subito il pannello di inserimento (con focus automatico sul campo link `qrc.mw`) e chiusura rapida dei fogli anche tramite tasto `Esc`.

  * **Card prodotto compatte (-30% altezza) & Copia rapida MW:** il campo di modifica delle note resta compatto finché non tocchi **📝** (o la pillola della nota se già presente) e un tap sul badge **MW: xxxxxx** copia istantaneamente il codice PIM negli appunti.

* **Installazione Diretta PWA (`📲 Installa app`):** Pulsante dedicato nel menu **⋮** per avviare l'installazione nativa sulla schermata Home di Android con un solo tocco (con fallback guidato e auto-hide una volta installata).

* **Esportatore Report Venduto (Target Italia) con Copia Automatica:**

  * Pulsante **📋 Report** nell'header per generare il riepilogo giornaliero dei prodotti venduti (`delta < 0`) nel formato standard richiesto per il report promoter.

  * **Filtro Dinamico Marchio & CPU:** Seleziona con un tap **Intel** (tutte le vendite di dispositivi con processore Intel di qualsiasi brand) oppure una specifica marca rilevata nel catalogo (**Lenovo**, **HP**, **ASUS**, ecc.).

  * **Clipboard & Persistenza:** Copia istantaneamente il testo formattato negli appunti di sistema e memorizza in `localStorage` anagrafica promoter, punto vendita e orario turno, compilando in automatico la data odierna (`GG/MM/AA`).

* **Bottoni KPI Interattivi & Drill-Down Dinamico:**

  * **Esposti & Pezzi totali:** Apri il pannello di dettaglio suddiviso dinamicamente per **Marca** (es. *Lenovo*, *HP*, *ASUS*, ecc.) e, per i brand multi-categoria, ulteriormente ripartito tra **Computer (Notebook / Fisso / AIO)** e **Tablet**, con filtro istantaneo al tap.

  * **Variazioni oggi:** Filtra al volo i movimenti giornalieri per **CPU Intel** (trasversale su tutte le marche) oppure per singolo **Brand** e sottocategoria.

  * **Allerta Riassortimento (`Da riassortire: n`):** Toggle rapido per isolare i prodotti attualmente **esposti** con giacenza $\le 1$.

* **Modifica Rapida Prezzo in Corsia:** Icona matita accanto al prezzo (o pulsante `+ €`) su ogni scheda prodotto per aggiornare al volo il listino (`POST /api/price/{id}`) preservando lo stato aperto della card.

* **Chiusura Giornata, Backup & Pulizia Esauriti (`00:00`):** Ogni notte a mezzanotte (`Europe/Rome`) salva un report CSV completo in `backups/giacenze_YYYY-MM-DD.csv` (scaricabile anche manualmente dal menu **⋮**), **elimina definitivamente dal database tutti i prodotti rimasti a giacenza `0`** e consolida le giacenze attive come nuova base per il giorno successivo.

* **Delta Giacenze Giornaliero ad Alto Contrasto (`+n` / `-n`):** Confronto automatico tra giacenza attuale e inizio giornata (`giacenza_base`), con badge verde (`+n`) per i carichi e badge azzurro elettrico (`-n`, `#38bdf8`) per gli scarichi/vendite.

* **Scraping QR & Import CSV Massivo:** Lettura automatica delle schede tecniche da link `qrc.mw` e aggiornamento massivo dell'inventario da file CSV MediaWorld.

## 📁 Struttura del Progetto

```text
droid-wms/
├── app.py              # Backend Starlette, ETag 304 (HTML+API), scraping async pooled, PWA SW, SQLite WAL e SPA OLED
├── requirements.txt    # Dipendenze Python (starlette, uvicorn, selectolax, tzdata, ecc.)
├── .gitignore          # Esclusione file sensibili, database, backup CSV e venv
├── README.md           # Documentazione del progetto
├── security.json       # [NON TRACCIATO] Secret key e hash PBKDF2-SHA256
├── inventario.db       # [NON TRACCIATO] Database SQLite (modalità WAL + mmap RAM)
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

