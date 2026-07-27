"""Upscaling AI con Replicate (Real-ESRGAN) e cache cloud su Cloudflare R2.

Flusso per ogni carta con "Miglioramento Avanzato" attivo:

1. Genera una chiave univoca (set, numero, lingua, nome, fattore di scala).
2. CACHE HIT — se l'immagine ad alta risoluzione è già nel bucket R2
   ("magic-proxy-cache"), viene scaricata direttamente, senza chiamare
   Replicate (risparmio di costi e tempo).
3. CACHE MISS — l'immagine viene upscalata con Real-ESRGAN via Replicate e
   il risultato viene salvato su R2 per i riutilizzi futuri.

Le credenziali sono lette esclusivamente da ``st.secrets["replicate"]`` e
``st.secrets["r2"]``; in assenza di configurazione o in caso di errori di
rete le funzioni restituiscono ``None`` e il chiamante ripiega sul
miglioramento locale (FSRCNN).
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Callable

import boto3
import replicate
import requests
import streamlit as st
from botocore.config import Config

DEFAULT_BUCKET = "magic-proxy-cache"
REAL_ESRGAN_MODEL = "nightmareai/real-esrgan"
UPSCALE_FACTOR = 2
DOWNLOAD_TIMEOUT_SECONDS = 180


def _get_secret_section(name: str) -> dict | None:
    """Legge una sezione da ``st.secrets`` senza far trapelare eccezioni."""
    try:
        return dict(st.secrets[name])
    except Exception:
        return None


def get_replicate_token() -> str | None:
    """Token API di Replicate da ``st.secrets["replicate"]``."""
    section = _get_secret_section("replicate")
    if not section:
        return None
    token = str(section.get("api_token", "")).strip()
    return token or None


def get_r2_config() -> dict | None:
    """Configurazione R2 da ``st.secrets["r2"]`` (None se incompleta)."""
    section = _get_secret_section("r2")
    if not section:
        return None
    account_id = str(section.get("account_id", "")).strip()
    access_key_id = str(section.get("access_key_id", "")).strip()
    secret_access_key = str(section.get("secret_access_key", "")).strip()
    if not (account_id and access_key_id and secret_access_key):
        return None
    return {
        "endpoint_url": f"https://{account_id}.r2.cloudflarestorage.com",
        "access_key_id": access_key_id,
        "secret_access_key": secret_access_key,
        "bucket": str(section.get("bucket", DEFAULT_BUCKET)).strip()
        or DEFAULT_BUCKET,
    }


def get_r2_client(config: dict):
    """Client S3 (boto3) configurato per Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=config["endpoint_url"],
        aws_access_key_id=config["access_key_id"],
        aws_secret_access_key=config["secret_access_key"],
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def make_cache_key(item: dict) -> str:
    """Chiave univoca della carta nel bucket (set, numero, lingua, nome)."""
    slug = re.sub(r"[^a-z0-9]+", "-", item.get("name", "").lower()).strip("-")
    return (
        f"esrgan-x{UPSCALE_FACTOR}/{item.get('set_code', 'unk')}/"
        f"{item.get('collector_number', '0')}_{item.get('lang', 'xx')}_{slug}.png"
    )


def get_cached_image(client, bucket: str, key: str) -> bytes | None:
    """Scarica l'immagine dalla cache R2; ``None`` se assente o su errore."""
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except Exception:
        return None


def put_cached_image(client, bucket: str, key: str, data: bytes) -> bool:
    """Salva l'immagine nella cache R2 (best effort)."""
    try:
        client.put_object(
            Bucket=bucket, Key=key, Body=data, ContentType="image/png"
        )
        return True
    except Exception:
        return False


def upscale_with_replicate(png_bytes: bytes, api_token: str) -> bytes | None:
    """Upscaling Real-ESRGAN via Replicate; ``None`` in caso di errore."""
    try:
        client = replicate.Client(api_token=api_token)
        source = BytesIO(png_bytes)
        source.name = "card.png"
        output = client.run(
            REAL_ESRGAN_MODEL,
            input={
                "image": source,
                "scale": UPSCALE_FACTOR,
                "face_enhance": False,
            },
        )
        if hasattr(output, "read"):
            return output.read()
        response = requests.get(str(output), timeout=DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.content
    except Exception:
        return None


def enhance_card_image(
    item: dict,
    png_bytes: bytes,
    notify: Callable[[str], None] = lambda message: None,
) -> tuple[bytes | None, str]:
    """Pipeline cloud completa: cache R2 → Replicate → salvataggio in cache.

    Restituisce ``(immagine, sorgente)`` dove sorgente è ``"cache"`` o
    ``"replicate"``; ``(None, motivo)`` se la pipeline cloud non è
    disponibile o fallisce (il chiamante può ripiegare sul locale).
    """
    r2_config = get_r2_config()
    cache_key = make_cache_key(item)

    r2_client = None
    if r2_config is not None:
        try:
            r2_client = get_r2_client(r2_config)
        except Exception:
            r2_client = None

    if r2_client is not None:
        notify("⚡ Recupero immagine da Cloudflare R2...")
        cached = get_cached_image(r2_client, r2_config["bucket"], cache_key)
        if cached:
            return cached, "cache"

    api_token = get_replicate_token()
    if api_token is None:
        return None, "replicate non configurato"

    notify("✨ Upscaling AI in corso con Replicate...")
    upscaled = upscale_with_replicate(png_bytes, api_token)
    if upscaled is None:
        return None, "errore Replicate"

    if r2_client is not None:
        put_cached_image(r2_client, r2_config["bucket"], cache_key, upscaled)

    return upscaled, "replicate"
