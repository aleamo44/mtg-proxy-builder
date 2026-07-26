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


def autocomplete(query: str) -> list[str]:
    """Restituisce i nomi di carte che corrispondono alla query.

    Interroga l'endpoint ``/cards/autocomplete`` di Scryfall.
    In caso di errore o di query vuota restituisce una lista vuota.
    """
    query = (query or "").strip()
    if not query:
        return []

    data = _rate_limited_get(f"{API_BASE_URL}/cards/autocomplete", params={"q": query})
    if not data or data.get("object") == "error":
        return []

    names = data.get("data", [])
    return [name for name in names if isinstance(name, str)]


def _extract_printing(card: dict) -> dict:
    """Converte un oggetto carta Scryfall nel dizionario di stampa richiesto."""
    card_faces = card.get("card_faces") or []
    # Una carta è DFC solo se ogni faccia ha immagini proprie (es. transform,
    # modal_dfc). Le carte split/adventure hanno card_faces ma una sola immagine.
    is_dfc = len(card_faces) >= 2 and all(
        isinstance(face.get("image_uris"), dict) for face in card_faces
    )

    if is_dfc:
        faces = [
            face["image_uris"].get("png") or face["image_uris"].get("normal")
            for face in card_faces
        ]
        faces = [url for url in faces if url]
        image_uris = card_faces[0].get("image_uris", {})
    else:
        faces = []
        image_uris = card.get("image_uris") or {}

    return {
        "set_code": card.get("set", ""),
        "set_name": card.get("set_name", ""),
        "collector_number": card.get("collector_number", ""),
        "lang": card.get("lang", ""),
        "image_png": image_uris.get("png"),
        "image_normal": image_uris.get("normal"),
        "is_dfc": is_dfc,
        "faces": faces,
    }


def get_card_printings(card_name: str) -> list[dict]:
    """Restituisce tutte le stampe di una carta, incluse quelle multilingua.

    Cerca prima la carta per nome esatto per ricavarne l'``oracle_id``, poi
    interroga ``/cards/search`` con ``q=oracleid:...``, ``unique=prints`` e
    ``include_multilingual=true``, seguendo la paginazione.

    In caso di carta non trovata o errore restituisce una lista vuota.
    """
    card_name = (card_name or "").strip()
    if not card_name:
        return []

    # Ricava l'oracle_id dal nome esatto: la ricerca per oracleid include
    # anche le stampe con nomi localizzati, a differenza di q=exact:"...".
    named = _rate_limited_get(
        f"{API_BASE_URL}/cards/named", params={"exact": card_name}
    )
    if named and named.get("oracle_id"):
        query = f'oracleid:{named["oracle_id"]}'
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
