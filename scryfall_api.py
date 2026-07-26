"""Interazioni con l'API di Scryfall per MTGProxyBuilder.

Questo modulo espone due funzioni principali:
- ``autocomplete``: suggerisce nomi di carte a partire da un testo parziale.
- ``get_card_printings``: recupera tutte le stampe di una carta (tutte le
  lingue e tutti i set) con gli URL delle immagini utili per la stampa dei proxy.

Tutte le richieste usano uno User-Agent personalizzato e rispettano i rate
limit di Scryfall introducendo un piccolo ritardo tra una chiamata e l'altra.
In caso di errori HTTP o di carte non trovate, le funzioni restituiscono liste
vuote invece di sollevare eccezioni.
"""

from __future__ import annotations

import time

import requests

API_BASE = "https://api.scryfall.com"

USER_AGENT = "MTGProxyBuilder/1.0"

REQUEST_DELAY = 0.1

REQUEST_TIMEOUT = 20

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json;q=0.9,*/*;q=0.8",
    }
)


def _request(path: str, params: dict | None = None) -> dict | None:
    """Esegue una GET verso Scryfall rispettando i rate limit.

    Restituisce il corpo JSON come dizionario, oppure ``None`` in caso di
    qualsiasi errore (rete, HTTP, JSON non valido).
    """

    url = path if path.startswith("http") else f"{API_BASE}{path}"

    time.sleep(REQUEST_DELAY)

    try:
        response = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    try:
        return response.json()
    except ValueError:
        return None


def autocomplete(query: str) -> list[str]:
    """Restituisce i nomi di carte corrispondenti al testo ``query``.

    Interroga l'endpoint ``/cards/autocomplete`` di Scryfall. In caso di errore
    o di nessun risultato restituisce una lista vuota.
    """

    query = (query or "").strip()
    if not query:
        return []

    data = _request("/cards/autocomplete", params={"q": query})
    if not data:
        return []

    catalog = data.get("data")
    if not isinstance(catalog, list):
        return []

    return [name for name in catalog if isinstance(name, str)]


def _iter_search_results(query: str) -> list[dict]:
    """Recupera tutte le pagine dei risultati di ricerca per ``query``."""

    results: list[dict] = []
    params: dict | None = {
        "q": query,
        "include_multilingual": "true",
        "unique": "prints",
    }
    next_url: str | None = "/cards/search"

    while next_url:
        data = _request(next_url, params=params)
        if not data:
            break

        page = data.get("data")
        if isinstance(page, list):
            results.extend(card for card in page if isinstance(card, dict))

        if data.get("has_more") and data.get("next_page"):
            next_url = data["next_page"]
            params = None
        else:
            next_url = None

    return results


def _extract_printing(card: dict) -> dict:
    """Estrae dal dizionario di una carta i campi utili per una stampa."""

    image_uris = card.get("image_uris")
    faces: list[str] = []
    is_dfc = False
    image_png = None
    image_normal = None

    if isinstance(image_uris, dict):
        image_png = image_uris.get("png")
        image_normal = image_uris.get("normal")
    else:
        card_faces = card.get("card_faces")
        if isinstance(card_faces, list):
            face_images = [
                face["image_uris"]
                for face in card_faces
                if isinstance(face, dict) and isinstance(face.get("image_uris"), dict)
            ]
            if face_images:
                is_dfc = True
                faces = [
                    fi.get("png") for fi in face_images if fi.get("png")
                ]
                image_png = face_images[0].get("png")
                image_normal = face_images[0].get("normal")

    return {
        "set_code": card.get("set"),
        "set_name": card.get("set_name"),
        "collector_number": card.get("collector_number"),
        "lang": card.get("lang"),
        "image_png": image_png,
        "image_normal": image_normal,
        "is_dfc": is_dfc,
        "faces": faces,
    }


def get_card_printings(card_name: str) -> list[dict]:
    """Restituisce tutte le stampe di una carta in tutte le lingue.

    Prima recupera la carta tramite il nome esatto per ottenerne l'``oracle_id``
    e cercare quindi tutte le stampe con ``oracleid:...``. Se la ricerca per
    oracle id non è possibile, ripiega su una ricerca ``exact:"..."``.

    In caso di errore o di carta non trovata restituisce una lista vuota.
    """

    card_name = (card_name or "").strip()
    if not card_name:
        return []

    named = _request("/cards/named", params={"exact": card_name})
    oracle_id = named.get("oracle_id") if isinstance(named, dict) else None

    if oracle_id:
        query = f"oracleid:{oracle_id}"
    else:
        escaped = card_name.replace('"', '\\"')
        query = f'exact:"{escaped}"'

    cards = _iter_search_results(query)
    if not cards:
        return []

    return [_extract_printing(card) for card in cards]
