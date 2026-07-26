"""Interfaccia web principale del MTG Professional Proxy Builder.

Permette di cercare una carta tramite l'autocompletamento di Scryfall,
scegliere edizione, lingua e quantità, vedere l'anteprima in tempo reale
(entrambe le facce per le carte DFC) e costruire una coda di stampa
esportabile in JSON per i passaggi successivi (download PNG e upscaling).

Avvio: ``streamlit run app.py``
"""

import json

import streamlit as st

from scryfall_api import autocomplete, get_card_printings

st.set_page_config(
    page_title="MTG Professional Proxy Builder",
    page_icon="🃏",
    layout="wide",
)


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


def add_to_queue(entry: dict) -> None:
    """Aggiunge una carta alla coda, sommando le quantità se già presente."""
    for item in st.session_state.queue:
        if (
            item["name"] == entry["name"]
            and item["set_code"] == entry["set_code"]
            and item["collector_number"] == entry["collector_number"]
            and item["lang"] == entry["lang"]
        ):
            item["quantity"] = min(4, item["quantity"] + entry["quantity"])
            return
    st.session_state.queue.append(entry)


def default_lang_index(languages: list[str]) -> int:
    """Indice della lingua di default: 'en', poi 'it', altrimenti la prima."""
    for preferred in ("en", "it"):
        if preferred in languages:
            return languages.index(preferred)
    return 0


st.title("MTG Professional Proxy Builder - Card Selector")

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
            selected_lang = st.selectbox(
                "Lingua",
                options=available_langs,
                index=default_lang_index(available_langs),
                key=f"lang_select_{selected_name}_{selected_key}",
            )

            selected_printing = editions[selected_key]["by_lang"][selected_lang]

            quantity = st.number_input(
                "Quantità",
                min_value=1,
                max_value=4,
                value=1,
                step=1,
            )

            if st.button("Aggiungi alla Coda di Stampa", type="primary"):
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
                        "faces": selected_printing["faces"],
                    }
                )
                st.success(f"Aggiunto: {selected_name} x{int(quantity)}")

# ---------------------------------------------------------------------------
# Colonna destra: anteprima visiva.
# ---------------------------------------------------------------------------
with col_right:
    st.subheader("Anteprima")

    if selected_printing is None:
        st.caption("Cerca e seleziona una carta per vedere l'anteprima.")
    elif selected_printing["is_dfc"] and selected_printing["faces"]:
        face_cols = st.columns(len(selected_printing["faces"]))
        captions = ["Fronte", "Retro"]
        for i, (face_col, face_url) in enumerate(
            zip(face_cols, selected_printing["faces"])
        ):
            with face_col:
                st.image(
                    face_url,
                    caption=captions[i] if i < len(captions) else f"Faccia {i + 1}",
                    width="stretch",
                )
    elif selected_printing["image_normal"]:
        st.image(
            selected_printing["image_normal"],
            caption=(
                f"{selected_printing['set_name']} "
                f"(#{selected_printing['collector_number']}) "
                f"[{selected_printing['lang']}]"
            ),
            width=340,
        )
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
    header = st.columns([3, 3, 1, 1, 1, 1])
    for col, label in zip(header, ["Nome", "Set", "Numero", "Lingua", "Qtà", ""]):
        col.markdown(f"**{label}**")

    for index, item in enumerate(st.session_state.queue):
        row = st.columns([3, 3, 1, 1, 1, 1])
        row[0].write(item["name"])
        row[1].write(item["set_name"])
        row[2].write(f"#{item['collector_number']}")
        row[3].write(item["lang"])
        row[4].write(str(item["quantity"]))
        if row[5].button("🗑️", key=f"remove_{index}", help="Rimuovi dalla coda"):
            st.session_state.queue.pop(index)
            st.rerun()

    total_cards = sum(item["quantity"] for item in st.session_state.queue)
    st.caption(
        f"{len(st.session_state.queue)} voci in coda, {total_cards} carte totali."
    )

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
        st.rerun()

    with st.expander("Anteprima JSON della coda"):
        st.code(queue_json, language="json")
