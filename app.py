"""Interfaccia web principale del MTG Proxy Builder.

Permette di cercare una carta tramite l'autocompletamento di Scryfall,
scegliere edizione, lingua e quantità, vedere l'anteprima in tempo reale
e costruire una coda di stampa. Per le carte a doppio lato (DFC) viene
scaricata ed elaborata solo la faccia frontale.

Il pulsante "Elabora Carte per la Stampa" scarica i PNG da Scryfall,
li ricampiona a 600 DPI e li impagina in un PDF A4 (griglia 3x3 centrata,
spaziatura configurabile) pronto per il download e la stampa.

Avvio: ``streamlit run app.py``
"""

import hmac
import json
import time
import uuid

import streamlit as st

from cloud_enhancer import enhance_card_image
from image_processor import process_image_bytes
from pdf_generator import generate_a4_pdf
from scryfall_api import autocomplete, download_image, get_card_printings

DFC_WARNING = (
    "⚠️ Carta a doppio lato: verrà scaricata e stampata solo la faccia frontale."
)

st.set_page_config(
    page_title="MTG Proxy Builder",
    page_icon="🃏",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Autenticazione basata sui Secrets di Streamlit (st.secrets).
# Tutta la logica dell'app (ricerca, download, Super-Resolution, PDF) viene
# eseguita SOLO dopo un login riuscito: in caso contrario st.stop()
# interrompe lo script prima di qualsiasi elaborazione.
# ---------------------------------------------------------------------------
def _load_auth_config() -> dict | None:
    """Legge la configurazione di autenticazione da ``st.secrets``.

    Restituisce ``None`` se i secrets non sono configurati o incompleti,
    senza mai far trapelare eccezioni o valori nel front-end.
    """
    try:
        auth = st.secrets["auth"]
        password = str(auth.get("app_password", "")).strip()
        allowed_emails = [
            str(email).strip().lower()
            for email in auth.get("allowed_emails", [])
            if str(email).strip()
        ]
    except Exception:
        return None
    if not password:
        return None
    return {"password": password, "allowed_emails": allowed_emails}


def require_authentication() -> None:
    """Blocca l'app finché l'utente non completa il login.

    - Se i secrets non sono configurati, mostra un errore pulito e si ferma.
    - Verifica email (se è configurata una lista di autorizzate) e password
      (confronto in tempo costante con ``hmac.compare_digest``).
    """
    if st.session_state.get("authenticated"):
        with st.sidebar:
            st.caption(f"Connesso come {st.session_state.get('auth_user', '')}")
            if st.button("Esci"):
                st.session_state.pop("authenticated", None)
                st.session_state.pop("auth_user", None)
                st.rerun()
        return

    st.title("🔒 MTG Proxy Builder")

    auth_config = _load_auth_config()
    if auth_config is None:
        st.error(
            "Autenticazione non configurata: imposta la sezione `[auth]` con "
            "`app_password` nei Secrets di Streamlit (in locale crea "
            "`.streamlit/secrets.toml` partendo da "
            "`.streamlit/secrets.toml.example`; su Streamlit Cloud usa la "
            "sezione *Secrets* delle impostazioni dell'app)."
        )
        st.stop()

    with st.form("login_form"):
        email = st.text_input("Email").strip().lower()
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Accedi", type="primary")

    if submitted:
        email_ok = (
            not auth_config["allowed_emails"]
            or email in auth_config["allowed_emails"]
        )
        password_ok = hmac.compare_digest(password, auth_config["password"])
        if email_ok and password_ok:
            st.session_state.authenticated = True
            st.session_state.auth_user = email
            st.rerun()
        else:
            st.error("Credenziali non valide.")

    st.stop()


require_authentication()


# ---------------------------------------------------------------------------
# Chiamate API con cache, per non interrogare Scryfall a ogni rerun.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def cached_autocomplete(query: str) -> list[str]:
    return autocomplete(query)


@st.cache_data(ttl=3600, show_spinner="Recupero delle stampe da Scryfall...")
def cached_printings(card_name: str) -> list[dict]:
    return get_card_printings(card_name)


# ---------------------------------------------------------------------------
# Stato della sessione.
# ---------------------------------------------------------------------------
if "queue" not in st.session_state:
    st.session_state.queue = []


def add_to_queue(entry: dict, merge: bool = True) -> None:
    """Aggiunge una carta alla coda di stampa.

    Con ``merge=True`` le carte identiche vengono accorpate sommando le
    quantità; con ``merge=False`` ogni aggiunta crea una voce separata
    (utile per confrontare parametri diversi, es. con e senza upscale).
    """
    if merge:
        for item in st.session_state.queue:
            if (
                item["name"] == entry["name"]
                and item["set_code"] == entry["set_code"]
                and item["collector_number"] == entry["collector_number"]
                and item["lang"] == entry["lang"]
            ):
                item["quantity"] = min(4, item["quantity"] + entry["quantity"])
                return
    entry["uid"] = uuid.uuid4().hex[:8]
    st.session_state.queue.append(entry)


def default_lang_index(languages: list[str]) -> int:
    """Indice della lingua di default: 'en', poi 'it', altrimenti la prima."""
    for preferred in ("en", "it"):
        if preferred in languages:
            return languages.index(preferred)
    return 0


def process_queue_to_pdf(queue: list[dict], spacing_mm: float) -> bytes | None:
    """Scarica, ricampiona a 600 DPI e impagina in PDF A4 le carte in coda.

    Ogni copia richiesta (quantità) occupa una cella della griglia 3x3;
    oltre le 9 carte il PDF diventa multipagina. Ogni step della pipeline
    (cache R2, Replicate, fallback locale) viene tracciato nel log di
    diagnostica visibile nell'interfaccia. Restituisce i byte del PDF,
    oppure ``None`` se nessuna carta è stata elaborata con successo.
    """
    processed_images: list[bytes] = []
    progress = st.progress(0.0, text="Elaborazione in corso...")

    log_lines: list[str] = []
    log_container = st.expander(
        "🛠️ Log e Diagnostica Processo", expanded=False
    ).container()

    def log(message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} — {message}"
        log_lines.append(line)
        log_container.write(line)

    log(
        f"Avvio elaborazione: {len(queue)} voci in coda, "
        f"spaziatura {spacing_mm}mm."
    )

    for index, item in enumerate(queue):
        label = f"{item['name']} [{item['set_code']} #{item['collector_number']}]"
        fraction = index / len(queue)
        progress.progress(fraction, text=f"Elaborazione: {label}")
        log(
            f"▶️ {label} x{item['quantity']} — Miglioramento Avanzato: "
            f"{'ON' if item.get('enhance', False) else 'OFF'}."
        )

        png_bytes = download_image(item["image_png"])
        if not png_bytes:
            log(f"❌ Download da Scryfall fallito per {label}: carta saltata.")
            st.error(f"Download fallito per {label}: carta saltata.")
            continue
        log(f"⬇️ Sorgente Scryfall scaricata ({len(png_bytes) // 1024} KB).")

        try:
            if item.get("enhance", False):
                # Pipeline cloud: cache R2 -> Replicate Real-ESRGAN -> R2.
                cloud_png, source = enhance_card_image(
                    item,
                    png_bytes,
                    notify=lambda text: progress.progress(
                        fraction, text=f"{text} ({label})"
                    ),
                    log=log,
                )
                if cloud_png is not None:
                    if source == "cache":
                        st.info(f"⚡ {label}: recuperata dalla cache R2.")
                    else:
                        st.info(f"✨ {label}: upscalata con Replicate.")
                    # Solo ricampionamento finale a 600 DPI: l'upscale AI
                    # è già stato applicato dalla pipeline cloud.
                    processed_png = process_image_bytes(cloud_png, enhance=False)
                else:
                    # Fallback locale (FSRCNN) se cloud non configurato o KO.
                    log(f"↩️ Fallback locale FSRCNN attivato — causa: {source}.")
                    progress.progress(
                        fraction,
                        text=f"Miglioramento locale (FSRCNN): {label}",
                    )
                    processed_png = process_image_bytes(png_bytes, enhance=True)
            else:
                processed_png = process_image_bytes(png_bytes, enhance=False)
        except Exception as exc:
            log(f"❌ Elaborazione fallita per {label}: {exc!r} — carta saltata.")
            st.error(f"Elaborazione fallita per {label}: carta saltata.")
            continue

        log(f"✅ {label}: pronta a 600 DPI (2835x3960 px).")
        processed_images.extend([processed_png] * item["quantity"])

    progress.progress(0.95, text="Impaginazione del PDF A4...")
    log(f"📄 Impaginazione PDF A4: {len(processed_images)} carte totali.")
    pdf_bytes = generate_a4_pdf(processed_images, spacing_mm=spacing_mm)
    progress.progress(1.0, text="Elaborazione completata.")
    log("🏁 Elaborazione completata." if pdf_bytes else "🏁 Nessuna carta elaborata.")
    st.session_state.process_log = log_lines
    return pdf_bytes


st.title("MTG Proxy Builder - Card Selector")

col_left, col_right = st.columns([1, 1], gap="large")

selected_printing: dict | None = None
selected_name: str | None = None
quantity = 1

# ---------------------------------------------------------------------------
# Colonna sinistra: form di selezione.
# ---------------------------------------------------------------------------
with col_left:
    st.subheader("Selezione carta")

    search_query = st.text_input(
        "Cerca il nome della carta",
        placeholder="Es. Lightning Bolt, Delver of Secrets...",
        help="Digita almeno 2 caratteri per vedere i suggerimenti.",
    )

    suggestions: list[str] = []
    if len(search_query.strip()) >= 2:
        suggestions = cached_autocomplete(search_query.strip())

    if suggestions:
        selected_name = st.selectbox(
            "Nomi suggeriti",
            options=suggestions,
            key="card_name_select",
        )
    elif search_query.strip():
        st.info("Nessuna carta trovata con questo nome.")

    if selected_name:
        printings = cached_printings(selected_name)

        if not printings:
            st.warning("Nessuna stampa disponibile per questa carta.")
        else:
            # Raggruppa le stampe per (set, collector number): a ogni gruppo
            # corrispondono le varianti linguistiche della stessa stampa.
            editions: dict[tuple[str, str], dict] = {}
            for printing in printings:
                key = (printing["set_code"], printing["collector_number"])
                editions.setdefault(key, {"label": "", "by_lang": {}})
                editions[key]["label"] = (
                    f"{printing['set_name']} (#{printing['collector_number']})"
                )
                editions[key]["by_lang"][printing["lang"]] = printing

            edition_keys = list(editions.keys())
            selected_key = st.selectbox(
                "Edizione / Set",
                options=edition_keys,
                format_func=lambda k: editions[k]["label"],
                key=f"edition_select_{selected_name}",
            )

            available_langs = sorted(editions[selected_key]["by_lang"].keys())
            lang_widget_key = f"lang_select_{selected_name}_{selected_key}"
            # Applica un eventuale cambio lingua richiesto dal pulsante
            # "Passa alla versione EN" PRIMA di istanziare il selectbox
            # (Streamlit vieta di modificarne lo stato dopo).
            if st.session_state.pop("pending_lang_switch", None) == lang_widget_key:
                st.session_state[lang_widget_key] = "en"
            selected_lang = st.selectbox(
                "Lingua",
                options=available_langs,
                index=default_lang_index(available_langs),
                format_func=str.upper,
                key=lang_widget_key,
            )

            selected_printing = editions[selected_key]["by_lang"][selected_lang]

            if selected_printing["is_low_res"]:
                st.warning(
                    "⚠️ La scansione per questa versione è a bassa risoluzione. "
                    "La stampa potrebbe risultare sgranata."
                )
                en_version = editions[selected_key]["by_lang"].get("en")
                if selected_lang != "en" and en_version is not None:
                    if st.button("🇬🇧 Passa alla versione EN ad Alta Definizione"):
                        st.session_state.pending_lang_switch = lang_widget_key
                        st.rerun()

            # Se l'utente cambia la selezione, l'anteprima torna a mostrare
            # la carta selezionata invece di quella scelta dalla coda.
            selection_signature = (
                selected_name,
                selected_printing["set_code"],
                selected_printing["collector_number"],
                selected_printing["lang"],
            )
            if st.session_state.get("last_selection") != selection_signature:
                st.session_state.last_selection = selection_signature
                st.session_state.pop("queue_preview", None)

            quantity = st.number_input(
                "Quantità",
                min_value=1,
                max_value=4,
                value=1,
                step=1,
            )

            merge_duplicates = st.session_state.get("merge_duplicates", True)
            if st.button("Aggiungi alla Coda di Stampa", type="primary"):
                # Default del miglioramento avanzato: ON solo per le scansioni
                # a bassa risoluzione mantenute in lingua non inglese; OFF per
                # le carte ad alta risoluzione o se si è passati alla
                # versione EN. Resta modificabile dal checkbox in coda.
                default_enhance = (
                    selected_printing["is_low_res"]
                    and selected_printing["lang"] != "en"
                )
                add_to_queue(
                    {
                        "name": selected_name,
                        "set_code": selected_printing["set_code"],
                        "set_name": selected_printing["set_name"],
                        "collector_number": selected_printing["collector_number"],
                        "lang": selected_printing["lang"],
                        "quantity": int(quantity),
                        "image_png": selected_printing["image_png"],
                        "image_normal": selected_printing["image_normal"],
                        "is_dfc": selected_printing["is_dfc"],
                        "is_low_res": selected_printing["is_low_res"],
                        "enhance": default_enhance,
                    },
                    merge=merge_duplicates,
                )
                st.success(f"Aggiunto: {selected_name} x{int(quantity)}")

# ---------------------------------------------------------------------------
# Colonna destra: anteprima visiva.
# ---------------------------------------------------------------------------
with col_right:
    st.subheader("Anteprima")

    # L'anteprima richiesta dalla coda ha priorità sulla selezione corrente.
    preview = st.session_state.get("queue_preview") or selected_printing

    if preview is None:
        st.caption("Cerca e seleziona una carta per vedere l'anteprima.")
    elif preview["image_normal"]:
        if preview["is_dfc"]:
            st.warning(DFC_WARNING)
        if preview.get("is_low_res"):
            st.warning(
                "⚠️ La scansione per questa versione è a bassa risoluzione. "
                "La stampa potrebbe risultare sgranata."
            )
        caption = (
            f"{preview['set_name']} (#{preview['collector_number']}) "
            f"[{preview['lang'].upper()}]"
            + (" — solo fronte" if preview["is_dfc"] else "")
        )
        if preview.get("name"):
            caption = f"{preview['name']} — {caption}"
        st.image(preview["image_normal"], caption=caption, width=340)
    else:
        st.warning("Immagine non disponibile per questa stampa.")

# ---------------------------------------------------------------------------
# Coda di stampa.
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Coda di Stampa")

if not st.session_state.queue:
    st.caption("La coda è vuota. Aggiungi delle carte per iniziare.")
else:
    for index, item in enumerate(st.session_state.queue):
        marker = "⚠️ " if item["is_dfc"] else ""
        with st.expander(f"{marker}{item['name']} (x{item['quantity']})"):
            st.markdown(
                f"- **Nome:** {item['name']}\n"
                f"- **Set:** {item['set_name']}\n"
                f"- **Numero:** #{item['collector_number']}\n"
                f"- **Lingua:** {item['lang'].upper()}\n"
                f"- **Qtà:** {item['quantity']}"
            )
            if item["is_dfc"]:
                st.warning(DFC_WARNING)

            # Chiave legata all'uid univoco della voce: non scivola su altre
            # voci dopo una rimozione e resta unica anche con carte duplicate
            # (accorpamento disattivato).
            item["enhance"] = st.checkbox(
                "✨ Miglioramento Avanzato (Denoise & Sharpening)",
                value=item.get("enhance", False),
                key=f"enhance_{item.get('uid', index)}",
            )

            btn_cols = st.columns([1, 1, 4])
            if btn_cols[0].button(
                "👁️ Anteprima",
                key=f"preview_{index}",
                help="Mostra questa carta nel riquadro dell'anteprima",
            ):
                st.session_state.queue_preview = item
                st.rerun()
            if btn_cols[1].button(
                "🗑️", key=f"remove_{index}", help="Rimuovi dalla coda"
            ):
                removed = st.session_state.queue.pop(index)
                if st.session_state.get("queue_preview") is removed:
                    st.session_state.pop("queue_preview", None)
                st.rerun()

    total_cards = sum(item["quantity"] for item in st.session_state.queue)
    st.caption(
        f"{len(st.session_state.queue)} voci in coda, {total_cards} carte totali."
    )

    if any(item["is_dfc"] for item in st.session_state.queue):
        st.warning(DFC_WARNING)

    queue_json = json.dumps(st.session_state.queue, indent=2, ensure_ascii=False)

    action_cols = st.columns([1, 1, 4])
    action_cols[0].download_button(
        "Scarica coda (JSON)",
        data=queue_json,
        file_name="print_queue.json",
        mime="application/json",
    )
    if action_cols[1].button("Svuota coda"):
        st.session_state.queue = []
        st.session_state.pop("processed_pdf", None)
        st.rerun()

    # -----------------------------------------------------------------------
    # Impostazioni di stampa e generazione del PDF.
    # -----------------------------------------------------------------------
    st.subheader("Impostazioni di Stampa")

    st.checkbox(
        "Accorpa carte identiche nella coda",
        value=True,
        key="merge_duplicates",
        help=(
            "Se attivo, aggiungere una carta già in coda ne incrementa la "
            "quantità. Se disattivo, ogni aggiunta crea una voce separata "
            "(utile per confrontare parametri diversi, es. con e senza "
            "Miglioramento Avanzato)."
        ),
    )

    spacing_mm = st.slider(
        "Spaziatura tra le carte (mm)",
        min_value=0.0,
        max_value=10.0,
        value=0.0,
        step=0.5,
        help="Imposta lo spazio uniforme (X e Y) tra le carte sul foglio A4.",
    )

    generate_clicked = st.button("🚀 Genera PDF A4 per la Stampa", type="primary")
    if generate_clicked:
        pdf_bytes = process_queue_to_pdf(st.session_state.queue, spacing_mm)
        if pdf_bytes is None:
            st.error("Nessuna carta elaborata: controlla gli errori qui sopra.")
            st.session_state.pop("processed_pdf", None)
        else:
            st.session_state.processed_pdf = pdf_bytes
            st.success(
                "Elaborazione completata: PDF A4 a 600 DPI pronto "
                f"(spaziatura {spacing_mm}mm)."
            )
    elif st.session_state.get("process_log"):
        # Log dell'ultima elaborazione, persistente tra i rerun.
        with st.expander("🛠️ Log e Diagnostica Processo"):
            for line in st.session_state.process_log:
                st.write(line)

    if st.session_state.get("processed_pdf"):
        st.download_button(
            "⬇️ Scarica PDF di stampa (A4, 600 DPI)",
            data=st.session_state.processed_pdf,
            file_name="proxy_sheet_a4_600dpi.pdf",
            mime="application/pdf",
            type="primary",
        )

    with st.expander("Anteprima JSON della coda"):
        st.code(queue_json, language="json")
