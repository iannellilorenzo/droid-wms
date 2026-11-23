# ⚡ droid-wms (v2.8.0)

Gestionale inventario self-hosted ad alte prestazioni progettato per girare in locale su uno smartphone Android dedicato e accessibile via browser come **Progressive Web App (PWA)** nativa.

Costruito sullo stack asincrono **Starlette + Uvicorn + `selectolax`**, la **v2.8.0** consolida l'intero ciclo operativo di corsia: **Esportatore Report Venduto (Target Italia)** con filtro dinamico per marchio o processore Intel e copia istantanea nella clipboard di sistema, **KPI interattivi con drill-down**, **modifica rapida dei prezzi**, **pulizia automatica notturna dei prodotti esauriti**, caching HTTP tramite **`ETag` (`304 Not Modified`)**, **scraping asincrono non bloccante**, **Event Delegation centralizzata** e badge azzurri ad alto contrasto (`#38bdf8`) per le variazioni negative.

## 🛠️ Dev Stack & Architettura (v2.8.0)

L'intera infrastruttura gira senza root su un **Samsung Galaxy S22** trasformato in micro-server always-on:

* **Host & Runtime:** Android + **Termux** (con `termux-wake-lock`, `Termux:Boot` per l'avvio automatico e `proot` chroot per la risoluzione DNS nativa).

* **Backend Asincrono & Caching HTTP:** **Python 3** + **Starlette** servito in produzione su **Uvicorn** (`--no-access-log` per ridurre le scritture su flash), con `GZipMiddleware` (compressione payload fino all'80%), **headers `ETag` (`304 Not Modified`)** su `GET /api/products` per azzerare il traffico dati quando l'inventario è invariato, e thread daemon in background con fuso orario `Europe/Rome` (`tzdata`).

* **Scraping Non Bloccante & Regex Pre-compilate:** Pipeline ibrida **Regex pre-compilate + `selectolax`** (parser HTML nativo in C) eseguita su thread dedicato tramite `asyncio.to_thread()` con rimozione preventiva delle immagini Base64, mantenendo l'event loop sempre libero durante la lettura dei link `qrc.mw`.

* **Frontend, PWA Offline-First & Event Delegation:** **Single-Page App (SPA)** installabile come **PWA** (`/manifest.json`, `/sw.js`, `/icon.svg`) con strategia **Network-First + CacheStorage fallback** (`droid-wms-v280`). Gestione centralizzata delle azioni tramite **Event Delegation (`data-action` + `switch/case`)**, comparatori pre-risolti fuori dal loop di ordinamento, wrapper di rete resiliente (`apiFetch`), **aggiornamento DOM chirurgico della singola card (`updateSingleCard`)** e risposte **Optimistic UI a 0 ms** con rollback automatico su errore di rete.

* **Motore di Ricerca Fuzzy Ibrido & Debounce:** Ricerca multi-token agnostica rispetto al brand con debounce a **70 ms**, tolleranza agli errori di battitura (distanza di **Levenshtein** + match per prefisso sulle parole) combinata ad **ancoraggio numerico rigoroso** (per evitare falsi positivi tra serie diverse, es. *Slim 3* vs *Slim 5*) e ordinamento dinamico per pertinenza.

* **Database & Backup:** **SQLite3** (`inventario.db`) configurato in modalità **`WAL` (`Write-Ahead Logging`)** con chiusura deterministica delle connessioni (`contextlib.closing`), `synchronous=NORMAL`, `temp_store=MEMORY` e `mmap_size=64MB` per mantenere indici e tabelle temporanee in RAM, affiancato da export automatici giornalieri in **CSV** (`backups/`).

* **Networking & Tunneling:** **ngrok** su dominio statico HTTPS con processi persistenti gestiti via **tmux**.

* **Sicurezza:** Autenticazione protetta da hash **PBKDF2-SHA256** (`security.json`), rate-limiting anti-bruteforce in memoria e cookie firmati (`itsdangerous`) `HttpOnly` / `Secure` a persistenza estesa (10 anni con auto-rinnovo ad ogni richiesta).

## ✨ Funzionalità (v2.8.0)

* **Esportatore Report Venduto (Target Italia) con Copia Automatica:**

  * Pulsante dedicato **📋 Report** nell'header per generare il riepilogo giornaliero dei prodotti venduti (`delta < 0`) nel formato standard richiesto per il report promoter.

  * **Filtro Dinamico Marchio & CPU:** Permette di selezionare con un tap **Intel** (tutte le vendite di dispositivi con processore Intel di qualsiasi brand) oppure una specifica marca rilevata nel catalogo (**Lenovo**, **HP**, **ASUS**, ecc.).

  * **Clipboard & Persistenza:** Copia istantaneamente il testo formattato negli appunti di sistema (con fallback universale per PWA/WebView) e memorizza in `localStorage` anagrafica promoter, punto vendita e orario turno, compilando in automatico la data odierna (`GG/MM/AA`).

* **Bottoni KPI Interattivi & Drill-Down Dinamico:**

  * **Esposti & Pezzi totali:** Toccando i rispettivi pulsanti si apre un pannello di dettaglio suddiviso dinamicamente per **Marca** (es. *Lenovo*, *HP*, *ASUS*, ecc.) e, per i brand multi-categoria, ulteriormente ripartito tra **Computer (Notebook / Fisso / AIO)** e **Tablet**. Il tap su una singola voce filtra all'istante le card sottostanti.

  * **Variazioni oggi:** Consente di filtrare al volo i movimenti giornalieri per **CPU Intel** (trasversale su tutte le marche) oppure per singolo **Brand** (includendo tutte le architetture: Intel, AMD, Snapdragon, ecc.) e sottocategoria.

  * **Allerta Riassortimento (`Da riassortire: n`):** Pulsante toggle rapido (disponibile anche nel menu *Stato rapido*) che isola i prodotti attualmente **esposti** con giacenza $\le 1$ (zero scorte residue in magazzino).

* **Modifica Rapida Prezzo in Corsia:** Icona matita dedicata accanto al prezzo (o pulsante `+ €` se non ancora impostato) su ogni scheda prodotto per aggiornare al volo il listino (`POST /api/price/{id}`) con aggiornamento istantaneo del database e delle specifiche.

* **Chiusura Giornata, Backup & Pulizia Esauriti (`00:00`):** Ogni notte a mezzanotte (`Europe/Rome`) salva un report CSV completo in `backups/giacenze_YYYY-MM-DD.csv` (scaricabile anche istantaneamente dall'header), **elimina definitivamente dal database tutti i prodotti rimasti a giacenza `0`** e consolida le giacenze attive come nuova base per il giorno successivo. Include anche un pulsante per la chiusura manuale anticipata.

* **Installazione Web App Nativa (PWA):** Installabile direttamente dalla schermata Home di Android via Chrome (*"Aggiungi a schermata Home"* / *"Installa app"*), con icona vettoriale dedicata, funzionamento offline-resilient e interfaccia full-screen priva di barra degli indirizzi.

* **Ricerca Fuzzy Ibrida & Pertinenza:** Supporta ricerche parziali, errori di battitura e codici senza spazi (es. `idepad slim3`, `lenvo`), distinguendo in modo esatto i numeri di serie/modello, il Codice PIM (6 cifre) e l'EAN-13.

* **Interazioni Istantanee (Optimistic UI):** I tasti `+`, `-`, `ESPOSTO` e modifica prezzo aggiornano immediatamente la singola card senza attendere la latenza della rete mobile e senza perdere la posizione di scorrimento, sincronizzando il database in background.

* **Delta Giacenze Giornaliero ad Alto Contrasto (`+n` / `-n`):** Confronto automatico tra la giacenza attuale e quella di inizio giornata (`giacenza_base`). Mostra un badge verde (`+n`) per i carichi e un badge azzurro elettrico (`-n`, `#38bdf8`) per gli scarichi/vendite, distinguendosi chiaramente da `ESPOSTO: NO` e scomparendo automaticamente se il conteggio torna in pari.

* **Note di Reparto:** Campo testuale dedicato su ogni scheda prodotto per segnare informazioni rapide (es. *"ultimo esposto"*, *"manca scatola"*), completamente indicizzato nella ricerca.

* **Ordinamento & Filtri Dinamici Hardware / Prezzo:** Ordinamento istantaneo (esposti, prezzo crescente/decrescente, giacenza, alfabetico) e filtri auto-popolati per **Marca**, **Diagonale Schermo**, **RAM**, **SSD**, **Scheda Video (Dedicata vs Integrata)**, **Prezzo Max** e stato variazioni.

* **UI Mobile-First & Long-Press PIM:** Pillola prezzo arrotondata (`€ PREZZO`) nella barra azioni e isolamento CSS (`user-select`) sulle 6 cifre del Codice PIM (`MW: xxxxxx`) per selezionare esattamente il numero al long-press su smartphone senza catturare spazi.

* **Scraping QR & Import CSV Massivo:** Lettura automatica delle schede tecniche da link `qrc.mw` e aggiornamento massivo dell'inventario da file CSV.

## 📁 Struttura del Progetto

```text
droid-wms/
├── app.py              # Backend Starlette, ETag 304, scraping async, PWA SW, SQLite WAL/RAM, scheduler 00:00 e SPA
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

