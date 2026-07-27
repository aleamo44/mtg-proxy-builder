"""Modulo per gestire le interazioni con l'API di Scryfall.

Fornisce funzioni per l'autocompletamento dei nomi delle carte e per
recuperare tutte le stampe (printings) di una carta, incluse le versioni
multilingua e le carte a doppia faccia (DFC).

Rispetta le linee guida di Scryfall:
- User-Agent personalizzato in tutte le richieste.
- Ritardo di ~100ms tra una richiesta e l'altra per rispettare i rate limit.
"""

from __future__ import annotations

import time

import requests

API_BASE_URL = "https://api.scryfall.com"
USER_AGENT = "MTGProxyBuilder/1.0"
REQUEST_DELAY_SECONDS = 0.1
REQUEST_TIMEOUT_SECONDS = 15

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
)

_last_request_time = 0.0


def _rate_limited_get(url: str, params: dict | None = None) -> dict | None:
    """Esegue una GET verso Scryfall applicando il ritardo per il rate limit.

    Restituisce il JSON decodificato oppure ``None`` in caso di errore
    HTTP, di rete o di parsing.
    """
    global _last_request_time

    elapsed = time.monotonic() - _last_request_time
    if elapsed < REQUEST_DELAY_SECONDS:
        time.sleep(REQUEST_DELAY_SECONDS - elapsed)

    try:
        response = _session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        _last_request_time = time.monotonic()
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        _last_request_time = time.monotonic()
        return None


def download_image(url: str) -> bytes | None:
    """Scarica un'immagine (es. PNG di Scryfall) rispettando il rate limit.

    Restituisce i byte dell'immagine oppure ``None`` in caso di errore.
    """
    global _last_request_time

    if not url:
        return None

    elapsed = time.monotonic() - _last_request_time
    if elapsed < REQUEST_DELAY_SECONDS:
        time.sleep(REQUEST_DELAY_SECONDS - elapsed)

    try:
        response = _session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        _last_request_time = time.monotonic()
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        _last_request_time = time.monotonic()
        return None


def _printed_name(card: dict) -> str | None:
    """Restituisce il nome stampato (localizzato) di una carta, se presente.

    Per le carte multi-faccia il nome stampato è dentro ``card_faces``.
    """
    printed = card.get("printed_name")
    if printed:
        return printed

    faces = card.get("card_faces") or []
    face_names = [face.get("printed_name") for face in faces]
    if face_names and all(face_names):
        return " // ".join(face_names)
    return None


def _multilingual_name_search(query: str, max_results: int = 20) -> list[str]:
    """Cerca nomi di carte in qualsiasi lingua tramite la ricerca globale.

    Interroga ``/cards/search`` con ``q=lang:any "<query>"`` ed estrae sia i
    nomi inglesi (``name``) sia i nomi stampati nelle altre lingue
    (``printed_name``), senza duplicati.
    """
    data = _rate_limited_get(
        f"{API_BASE_URL}/cards/search",
        params={
            "q": f'lang:any "{query}"',
            "include_multilingual": "true",
            "unique": "prints",
        },
    )
    if not data or data.get("object") == "error":
        return []

    names: list[str] = []
    seen: set[str] = set()
    query_lower = query.lower()

    for card in data.get("data", []):
        if not isinstance(card, dict):
            continue
        candidates = []
        printed = _printed_name(card)
        # Il nome stampato che corrisponde alla query digitata viene proposto
        # per primo, seguito dal nome inglese di riferimento.
        if printed and query_lower in printed.lower():
            candidates.append(printed)
        if card.get("name"):
            candidates.append(card["name"])
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                names.append(candidate)
        if len(names) >= max_results:
            break

    return names[:max_results]


def autocomplete(query: str) -> list[str]:
    """Restituisce i nomi di carte che corrispondono alla query.

    Interroga prima l'endpoint ``/cards/autocomplete`` di Scryfall (solo nomi
    inglesi). Se non produce risultati e la query ha almeno 3 caratteri,
    effettua un fallback sulla ricerca globale ``lang:any`` per trovare le
    carte anche tramite i nomi stampati in altre lingue (es. italiano).

    In caso di errore o di query vuota restituisce una lista vuota.
    """
    query = (query or "").strip()
    if not query:
        return []

    data = _rate_limited_get(f"{API_BASE_URL}/cards/autocomplete", params={"q": query})
    if data and data.get("object") != "error":
        names = [name for name in data.get("data", []) if isinstance(name, str)]
        if names:
            return names

    if len(query) >= 3:
        return _multilingual_name_search(query)

    return []


def _best_image_url(image_uris: dict) -> str | None:
    """Restituisce l'URL dell'immagine alla massima risoluzione disponibile.

    Fallback dinamico in cascata: prova prima ``png``, se assente/None passa
    a ``large`` e infine ripiega su ``normal``.
    """
    for size in ("png", "large", "normal"):
        url = image_uris.get(size)
        if url:
            return url
    return None


def _extract_printing(card: dict) -> dict:
    """Converte un oggetto carta Scryfall nel dizionario di stampa richiesto."""
    card_faces = card.get("card_faces") or []
    # Una carta è DFC solo se ogni faccia ha immagini proprie (es. transform,
    # modal_dfc). Le carte split/adventure hanno card_faces ma una sola immagine.
    is_dfc = len(card_faces) >= 2 and all(
        isinstance(face.get("image_uris"), dict) for face in card_faces
    )

    # Per le carte DFC viene usata sempre e solo la Faccia A (fronte,
    # card_faces[0]): la Faccia B non viene mai richiesta né scaricata.
    if is_dfc:
        image_uris = card_faces[0].get("image_uris", {})
    else:
        image_uris = card.get("image_uris") or {}

    return {
        "set_code": card.get("set", ""),
        "set_name": card.get("set_name", ""),
        "collector_number": card.get("collector_number", ""),
        "lang": card.get("lang", ""),
        "image_png": _best_image_url(image_uris),
        "image_normal": image_uris.get("normal"),
        "is_dfc": is_dfc,
    }


def _resolve_oracle_id(card_name: str) -> str | None:
    """Risolve l'``oracle_id`` di una carta a partire dal suo nome.

    Prova prima con il nome esatto inglese (``/cards/named?exact=``); se non
    trovato (es. nome stampato in italiano o altra lingua), effettua una
    ricerca globale ``lang:any`` e cerca la carta il cui nome stampato o
    inglese corrisponde esattamente al nome richiesto.
    """
    named = _rate_limited_get(
        f"{API_BASE_URL}/cards/named", params={"exact": card_name}
    )
    if named and named.get("oracle_id"):
        return named["oracle_id"]

    data = _rate_limited_get(
        f"{API_BASE_URL}/cards/search",
        params={
            "q": f'lang:any "{card_name}"',
            "include_multilingual": "true",
            "unique": "prints",
        },
    )
    if not data or data.get("object") == "error":
        return None

    name_lower = card_name.lower()
    fallback_id: str | None = None
    for card in data.get("data", []):
        if not isinstance(card, dict) or not card.get("oracle_id"):
            continue
        printed = _printed_name(card)
        if (printed and printed.lower() == name_lower) or (
            card.get("name", "").lower() == name_lower
        ):
            return card["oracle_id"]
        if fallback_id is None:
            fallback_id = card["oracle_id"]

    # Nessuna corrispondenza esatta: usa il primo risultato della ricerca.
    return fallback_id


def get_card_printings(card_name: str) -> list[dict]:
    """Restituisce tutte le stampe di una carta, incluse quelle multilingua.

    Risolve prima l'``oracle_id`` della carta (funziona anche con i nomi
    stampati in lingue diverse dall'inglese, es. italiano), poi interroga
    ``/cards/search`` con ``q=oracleid:...``, ``unique=prints`` e
    ``include_multilingual=true``, seguendo la paginazione.

    In caso di carta non trovata o errore restituisce una lista vuota.
    """
    card_name = (card_name or "").strip()
    if not card_name:
        return []

    oracle_id = _resolve_oracle_id(card_name)
    if oracle_id:
        query = f"oracleid:{oracle_id}"
    else:
        # Fallback: ricerca diretta per nome esatto.
        query = f'!"{card_name}"'

    printings: list[dict] = []
    url: str | None = f"{API_BASE_URL}/cards/search"
    params: dict | None = {
        "q": query,
        "unique": "prints",
        "include_multilingual": "true",
        "order": "released",
    }

    while url:
        data = _rate_limited_get(url, params=params)
        if not data or data.get("object") == "error":
            break

        for card in data.get("data", []):
            if isinstance(card, dict):
                printings.append(_extract_printing(card))

        # Le pagine successive sono indicate da next_page (URL completo).
        url = data.get("next_page") if data.get("has_more") else None
        params = None

    return printings
