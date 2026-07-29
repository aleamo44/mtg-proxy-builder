# MTG Proxy Builder

Applicazione web Streamlit per cercare carte Magic: The Gathering, costruire una coda di stampa e generare fogli A4 a **600 DPI** pronti per la stampa di proxy professionali.

Le immagini vengono recuperate da [Scryfall](https://scryfall.com/), ricampionate alla dimensione fisica ufficiale della carta (63×88 mm) e impaginate in una griglia 3×3 centrata sul foglio A4. Opzionalmente, le scansioni a bassa risoluzione possono essere migliorate con upscaling AI (Replicate Real-ESRGAN, con fallback locale FSRCNN) e cache su Cloudflare R2.

---

## Funzionalità

- **Ricerca multilingua** — autocompletamento Scryfall con fallback `lang:any` per nomi italiani e altre lingue
- **Selezione edizione e lingua** — tutte le stampe (printings) con lingue filtrate per edizione; codici lingua in maiuscolo (`EN`, `IT`, …)
- **Anteprima in tempo reale** — inclusa anteprima di carte già in coda
- **Coda di stampa compatta** — expander per voce con dettagli, anteprima, rimozione e toggle di miglioramento
- **Accorpamento opzionale** — toggle per unire carte identiche o tenerle separate (utile per confrontare con/senza upscale)
- **Controllo qualità sorgente** — avviso se la scansione è low-res + scorciatoia «Passa alla versione EN»
- **Upscaling AI** — Real-ESRGAN via Replicate, con cache Cloudflare R2 e fallback locale FSRCNN
- **PDF A4 a 600 DPI** — griglia 3×3 centrata, spaziatura configurabile 0–10 mm, multipagina
- **Log di diagnostica** — expander con ogni step della pipeline (cache HIT/MISS, Replicate, fallback)
- **Autenticazione** — login obbligatorio basato su `st.secrets` prima di qualsiasi elaborazione

---

## Avvio rapido

### Requisiti

- Python 3.10+
- Dipendenze elencate in [`requirements.txt`](requirements.txt)

```bash
pip install -r requirements.txt
```

### Secrets

Copia il template e compila i valori:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Il file reale `.streamlit/secrets.toml` è escluso da git. Su Streamlit Cloud, incolla lo stesso contenuto nella sezione **Secrets** dell'app.

| Sezione | Chiavi | Obbligatorio |
|---|---|---|
| `[auth]` | `app_password`, `allowed_emails` (opzionale) | **Sì** — senza password l'app resta bloccata |
| `[replicate]` | `api_token` | No — senza token si usa il fallback FSRCNN |
| `[r2]` | `account_id` (URL endpoint o account ID), `access_key_id`, `secret_access_key`, `bucket` | No — senza R2 non c'è cache cloud |

### Avvio

```bash
streamlit run app.py
```

Apri il browser su `http://localhost:8501`, accedi con le credenziali configurate e inizia a costruire la coda.

---

## Flusso di lavoro

1. **Cerca** una carta (nome inglese o localizzato, es. *Fulmine* / *Lightning Bolt*).
2. **Scegli** edizione, lingua e quantità (1–4).
3. **Aggiungi** alla coda di stampa. Per le DFC viene usata solo la faccia frontale.
4. **Imposta** la spaziatura tra le carte e, se serve, disattiva l'accorpamento delle voci duplicate.
5. **Attiva** il «Miglioramento Avanzato» sulle voci che lo richiedono (default ON solo per le low-res non inglesi).
6. **Genera** il PDF A4 e scaricalo.

---

## Specifiche di stampa

| Parametro | Valore |
|---|---|
| Formato foglio | A4 (210×297 mm) |
| Risoluzione | 600 DPI (4961×7016 px) |
| Dimensione carta | 63×88 mm (1488×2079 px a 600 DPI) |
| Layout | Griglia 3×3 centrata (9 carte/pagina) |
| Spaziatura | 0–10 mm, uniforme X/Y |
| Multipagina | Automatica oltre le 9 carte |

Nessun bleed margin o bordo extra: l'immagine mantiene proporzioni e struttura originali.

---

## Pipeline di miglioramento

Quando una voce in coda ha **Miglioramento Avanzato** attivo:

```
Scryfall PNG
    │
    ▼
Cache Cloudflare R2  ──HIT──►  immagine già upscalata
    │ MISS
    ▼
Replicate Real-ESRGAN (x2)  ──►  salvataggio su R2
    │ (errore / non configurato)
    ▼
Fallback locale FSRCNN (x2) + CLAHE leggero
    │
    ▼
Ricampionamento Lanczos a 2835×3960 px (600 DPI)
    │
    ▼
Impaginazione PDF A4
```

Le carte senza miglioramento saltano direttamente al ricampionamento a 600 DPI. Ogni step è tracciato nell'expander **Log e Diagnostica Processo**.

---

## Struttura del progetto

```
mtg-proxy-builder/
├── app.py                  # Interfaccia Streamlit (auth, selezione, coda, PDF)
├── scryfall_api.py         # Client Scryfall (autocomplete, printings, download)
├── image_processor.py      # Upscale 600 DPI + enhance locale (FSRCNN)
├── pdf_generator.py        # Impaginazione A4 3×3
├── cloud_enhancer.py       # Replicate Real-ESRGAN + cache Cloudflare R2
├── test_r2_connection.py   # Diagnostica connessione R2
├── FSRCNN_x2.pb            # Modello OpenCV Super-Resolution (~38 KB)
├── requirements.txt
├── .streamlit/
│   ├── config.toml         # Config server (download remoti, message size)
│   └── secrets.toml.example
└── .gitignore
```

---

## Moduli principali

### `scryfall_api.py`

Client HTTP con User-Agent `MTGProxyBuilder/1.0` e ritardo ~100 ms tra le richieste.

- `autocomplete(query)` — suggerimenti nomi; fallback multilingua se l'endpoint standard è vuoto
- `get_card_printings(card_name)` — tutte le stampe via `oracle_id` (funziona anche da nomi localizzati)
- Flag `is_dfc` (solo faccia A) e `is_low_res` (scansione sotto i 600 px / solo JPEG `normal`)
- URL immagine in cascata: `png` → `large` → `normal`

### `image_processor.py`

- `upscale_image` — Lanczos a 2835×3960 px + Unsharp Mask leggero
- `enhance_image` — Super-Resolution FSRCNN x2 (OpenCV `dnn_superres`) + CLAHE; fallback bicubico se il modello non carica
- `process_image_bytes` — pipeline completa con metadati DPI 600

### `pdf_generator.py`

- `generate_a4_pdf(images, spacing_mm)` — griglia 3×3 centrata matematicamente, multipagina

### `cloud_enhancer.py`

- Credenziali solo da `st.secrets["replicate"]` e `st.secrets["r2"]`
- Client boto3 con `region_name="auto"` e `addressing_style="path"`
- Chiavi cache: `esrgan-x2/<set>/<numero>_<lang>_<slug>.png` nel bucket `magic-proxy-cache`

---

## Diagnostica R2

Per verificare la connessione a Cloudflare R2 senza avviare l'intera app:

```bash
python3 test_r2_connection.py
```

Lo script legge `.streamlit/secrets.toml` oppure le variabili d'ambiente `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` e `R2_BUCKET`. In caso di successo elenca gli oggetti nel bucket.

> L'Access Key ID di R2 deve essere lungo **32 caratteri**. Se è più corto, probabilmente hai copiato un API Token Cloudflare invece della chiave S3 (dashboard R2 → Manage R2 API Tokens).

---

## Sicurezza

- Login obbligatorio: nessuna ricerca, download, Super-Resolution o generazione PDF senza autenticazione
- Credenziali esclusivamente in `st.secrets` (mai hardcoded)
- Confronto password a tempo costante (`hmac.compare_digest`)
- Whitelist email opzionale (`allowed_emails`)
- `.streamlit/secrets.toml` e `.env` esclusi da git
- Template sicuro in `.streamlit/secrets.toml.example` (senza valori reali)

---

## Note

- Le carte a doppio lato (DFC / Transform) stampano **solo la faccia frontale**.
- L'API Scryfall è pubblica e non richiede chiavi; rispettare i rate limit (il client lo fa già).
- Replicate e R2 sono opzionali: senza di essi l'app resta pienamente utilizzabile con il miglioramento locale.
- Su host remoti (tunnel, Streamlit Cloud) `.streamlit/config.toml` disabilita XSRF/CORS per consentire i download dei PDF.

---

## Licenza

Progetto personale per la creazione di proxy a uso privato. Magic: The Gathering è un marchio di Wizards of the Coast. Le immagini delle carte appartengono ai rispettivi titolari dei diritti.
