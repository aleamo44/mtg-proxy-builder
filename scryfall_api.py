"""Small helpers for interacting with the Scryfall API."""

from __future__ import annotations

import time
from typing import Any

import requests


BASE_URL = "https://api.scryfall.com"
USER_AGENT = "MTGProxyBuilder/1.0"
REQUEST_DELAY_SECONDS = 0.1
REQUEST_TIMEOUT_SECONDS = 10

_HEADERS = {"User-Agent": USER_AGENT}


def _get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Return JSON from Scryfall, or None for any safe-to-ignore failure."""
    time.sleep(REQUEST_DELAY_SECONDS)

    try:
        response = requests.get(
            url,
            headers=_HEADERS,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None

    return data if isinstance(data, dict) else None


def _search_cards(query: str) -> list[dict[str, Any]]:
    """Run a Scryfall card search and follow pagination safely."""
    cards: list[dict[str, Any]] = []
    url: str | None = f"{BASE_URL}/cards/search"
    params: dict[str, str] | None = {
        "q": query,
        "include_multilingual": "true",
        "unique": "prints",
    }

    while url:
        data = _get_json(url, params=params)
        if data is None:
            return []

        page_cards = data.get("data", [])
        if not isinstance(page_cards, list):
            return []

        cards.extend(card for card in page_cards if isinstance(card, dict))

        next_page = data.get("next_page")
        url = next_page if data.get("has_more") and isinstance(next_page, str) else None
        params = None

    return cards


def _image_url(image_uris: dict[str, Any] | None, size: str) -> str | None:
    if not isinstance(image_uris, dict):
        return None

    url = image_uris.get(size)
    return url if isinstance(url, str) else None


def _card_faces(card: dict[str, Any]) -> list[dict[str, Any]]:
    faces = card.get("card_faces", [])
    if not isinstance(faces, list):
        return []

    return [face for face in faces if isinstance(face, dict)]


def _quoted_search_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _face_image_urls(card: dict[str, Any], size: str) -> list[str]:
    urls: list[str] = []

    for face in _card_faces(card):
        image_url = _image_url(face.get("image_uris"), size) or _image_url(
            face.get("image_uris"),
            "normal",
        )
        if image_url:
            urls.append(image_url)

    return urls


def _print_info(card: dict[str, Any]) -> dict[str, Any]:
    image_png = _image_url(card.get("image_uris"), "png")
    image_normal = _image_url(card.get("image_uris"), "normal")
    faces = _face_image_urls(card, "png")
    normal_faces = _face_image_urls(card, "normal")
    is_dfc = len(faces) >= 2

    if image_png is None and faces:
        image_png = faces[0]
    if image_normal is None and normal_faces:
        image_normal = normal_faces[0]

    result: dict[str, Any] = {
        "set_code": card.get("set", ""),
        "set_name": card.get("set_name", ""),
        "collector_number": card.get("collector_number", ""),
        "lang": card.get("lang", ""),
        "image_png": image_png,
        "image_normal": image_normal,
        "is_dfc": is_dfc,
        "faces": faces if is_dfc else [],
    }

    return result


def autocomplete(query: str) -> list[str]:
    """Return Scryfall card-name autocomplete matches for the provided query."""
    data = _get_json(f"{BASE_URL}/cards/autocomplete", params={"q": query})
    if data is None:
        return []

    matches = data.get("data", [])
    if not isinstance(matches, list):
        return []

    return [match for match in matches if isinstance(match, str)]


def get_card_printings(card_name: str) -> list[dict[str, Any]]:
    """Return print metadata for all known Scryfall printings of a card."""
    exact_matches = _search_cards(f'exact:"{_quoted_search_value(card_name)}"')
    if not exact_matches:
        return []

    oracle_id = exact_matches[0].get("oracle_id")
    if isinstance(oracle_id, str):
        cards = _search_cards(f"oracleid:{oracle_id}")
    else:
        cards = exact_matches

    return [_print_info(card) for card in cards]
