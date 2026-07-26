"""Utility functions to interact with the Scryfall API."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any
from urllib.error import HTTPError, URLError

_USER_AGENT = "MTGProxyBuilder/1.0"
_REQUEST_DELAY_SECONDS = 0.1
_REQUEST_TIMEOUT_SECONDS = 15

_AUTOCOMPLETE_URL = "https://api.scryfall.com/cards/autocomplete"
_SEARCH_URL = "https://api.scryfall.com/cards/search"


def _safe_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Perform a GET request and decode JSON safely."""
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        # Keep a small delay between requests to respect rate limits.
        time.sleep(_REQUEST_DELAY_SECONDS)
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return None
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError):
        return None

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    return data if isinstance(data, dict) else None


def _search_printings(query: str) -> list[dict[str, Any]]:
    """Search card printings and follow pagination when available."""
    data = _safe_get_json(
        _SEARCH_URL,
        {
            "q": query,
            "include_multilingual": "true",
            "unique": "prints",
        },
    )
    if not data or data.get("object") != "list":
        return []

    cards: list[dict[str, Any]] = []
    page_cards = data.get("data", [])
    if isinstance(page_cards, list):
        cards.extend(card for card in page_cards if isinstance(card, dict))

    while data.get("has_more") and data.get("next_page"):
        next_page = data.get("next_page")
        if not isinstance(next_page, str):
            break

        data = _safe_get_json(next_page)
        if not data or data.get("object") != "list":
            break

        page_cards = data.get("data", [])
        if isinstance(page_cards, list):
            cards.extend(card for card in page_cards if isinstance(card, dict))

    return cards


def _to_printing_dict(card: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Scryfall card object into the expected printing shape."""
    card_faces = card.get("card_faces")
    faces_data = card_faces if isinstance(card_faces, list) else []
    is_dfc = len(faces_data) >= 2

    image_uris = card.get("image_uris")
    top_level_uris = image_uris if isinstance(image_uris, dict) else {}
    face_uris = [
        face.get("image_uris", {})
        for face in faces_data
        if isinstance(face, dict) and isinstance(face.get("image_uris"), dict)
    ]

    image_png = top_level_uris.get("png")
    image_normal = top_level_uris.get("normal")

    if is_dfc and face_uris:
        image_png = image_png or face_uris[0].get("png")
        image_normal = image_normal or face_uris[0].get("normal")

    faces: list[str] = []
    if is_dfc:
        for uris in face_uris[:2]:
            face_url = uris.get("png") or uris.get("normal")
            if isinstance(face_url, str):
                faces.append(face_url)

    return {
        "set_code": card.get("set"),
        "set_name": card.get("set_name"),
        "collector_number": card.get("collector_number"),
        "lang": card.get("lang"),
        "image_png": image_png if isinstance(image_png, str) else None,
        "image_normal": image_normal if isinstance(image_normal, str) else None,
        "is_dfc": is_dfc,
        "faces": faces,
    }


def autocomplete(query: str) -> list[str]:
    """Return a list of card names matching the query."""
    normalized_query = query.strip()
    if not normalized_query:
        return []

    data = _safe_get_json(_AUTOCOMPLETE_URL, {"q": normalized_query})
    if not data:
        return []

    names = data.get("data")
    if not isinstance(names, list):
        return []

    return [name for name in names if isinstance(name, str)]


def get_card_printings(card_name: str) -> list[dict]:
    """
    Return all multilingual printings for a card.

    The function first searches by exact name. If an oracle_id is available,
    it performs a second search by oracleid to ensure all printings are included.
    """
    normalized_name = card_name.strip()
    if not normalized_name:
        return []

    escaped_name = normalized_name.replace("\\", "\\\\").replace('"', '\\"')
    exact_query = f'exact:"{escaped_name}"'

    exact_cards = _search_printings(exact_query)
    if not exact_cards:
        return []

    oracle_id = next(
        (
            card.get("oracle_id")
            for card in exact_cards
            if isinstance(card.get("oracle_id"), str) and card.get("oracle_id")
        ),
        None,
    )

    if isinstance(oracle_id, str):
        cards = _search_printings(f"oracleid:{oracle_id}") or exact_cards
    else:
        cards = exact_cards

    return [_to_printing_dict(card) for card in cards]
