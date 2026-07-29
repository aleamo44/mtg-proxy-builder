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
import time
from io import BytesIO
from typing import Callable

import boto3
import replicate
import requests
import streamlit as st
from botocore.config import Config
from botocore.exceptions import ClientError

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


def build_endpoint_url(raw_value: str) -> str:
    """Normalizza l'endpoint R2 a partire dal valore nei secrets.

    Accetta l'URL completo (con o senza schema) oppure il solo account ID
    (stringa senza punti): in quel caso costruisce
    ``https://<account_id>.r2.cloudflarestorage.com``.
    """
    value = raw_value.strip().rstrip("/")
    if value.startswith("http"):
        return value
    if "." not in value:
        return f"https://{value}.r2.cloudflarestorage.com"
    return f"https://{value}"


def get_r2_config() -> dict | None:
    """Configurazione R2 da ``st.secrets["r2"]`` (None se incompleta)."""
    section = _get_secret_section("r2")
    if not section:
        return None
    # La chiave account_id accetta sia l'URL completo dell'endpoint fornito
    # da Cloudflare (es. https://<account_id>.r2.cloudflarestorage.com) sia
    # l'account ID "secco": in quel caso il dominio viene aggiunto.
    raw_value = str(
        section.get("account_id") or section.get("endpoint") or ""
    ).strip().rstrip("/")
    access_key_id = str(section.get("access_key_id", "")).strip()
    secret_access_key = str(section.get("secret_access_key", "")).strip()
    if not (raw_value and access_key_id and secret_access_key):
        return None

    endpoint_url = build_endpoint_url(raw_value)

    return {
        "endpoint_url": endpoint_url,
        "access_key_id": access_key_id,
        "secret_access_key": secret_access_key,
        "bucket": str(section.get("bucket", DEFAULT_BUCKET)).strip()
        or DEFAULT_BUCKET,
    }


def get_r2_client(config: dict):
    """Client S3 (boto3) configurato per Cloudflare R2.

    ``region_name="auto"`` e ``addressing_style: path`` sono fondamentali
    per la compatibilità con gli endpoint R2 ed evitano l'errore
    "Invalid endpoint" durante l'inizializzazione.
    """
    return boto3.client(
        service_name="s3",
        endpoint_url=config["endpoint_url"],
        aws_access_key_id=config["access_key_id"],
        aws_secret_access_key=config["secret_access_key"],
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
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


def get_cached_image(
    client, bucket: str, key: str
) -> tuple[bytes | None, str | None]:
    """Scarica l'immagine dalla cache R2.

    Restituisce ``(dati, None)`` in caso di HIT, ``(None, None)`` in caso di
    MISS (chiave assente) e ``(None, errore)`` per errori di rete/credenziali.
    """
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read(), None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return None, None
        return None, f"{code or 'ClientError'}: {exc}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def put_cached_image(client, bucket: str, key: str, data: bytes) -> str | None:
    """Salva l'immagine nella cache R2; restituisce l'errore o ``None``."""
    try:
        client.put_object(
            Bucket=bucket, Key=key, Body=data, ContentType="image/png"
        )
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def upscale_with_replicate(
    png_bytes: bytes, api_token: str
) -> tuple[bytes | None, str | None]:
    """Upscaling Real-ESRGAN via Replicate.

    Restituisce ``(immagine, None)`` oppure ``(None, errore dettagliato)``.
    """
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
            return output.read(), None
        response = requests.get(str(output), timeout=DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.content, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def enhance_card_image(
    item: dict,
    png_bytes: bytes,
    notify: Callable[[str], None] = lambda message: None,
    log: Callable[[str], None] = lambda message: None,
) -> tuple[bytes | None, str]:
    """Pipeline cloud completa: cache R2 → Replicate → salvataggio in cache.

    Ogni passo (controllo cache HIT/MISS, chiamata Replicate con esito e
    tempo, salvataggio su R2) viene riportato tramite ``log``; gli errori
    non vengono mai nascosti.

    Restituisce ``(immagine, sorgente)`` dove sorgente è ``"cache"`` o
    ``"replicate"``; ``(None, causa)`` se la pipeline cloud non è
    disponibile o fallisce (il chiamante può ripiegare sul locale).
    """
    r2_config = get_r2_config()
    cache_key = make_cache_key(item)
    log(f"🔑 Chiave cache: {cache_key}")

    r2_client = None
    if r2_config is None:
        log("ℹ️ R2 non configurato nei secrets: cache cloud disattivata.")
    else:
        try:
            r2_client = get_r2_client(r2_config)
        except Exception as exc:
            log(f"❌ Inizializzazione client R2 fallita: {exc}")

    if r2_client is not None:
        notify("⚡ Recupero immagine da Cloudflare R2...")
        log("⚡ Controllo cache R2 in corso...")
        cached, cache_error = get_cached_image(
            r2_client, r2_config["bucket"], cache_key
        )
        if cached:
            log(f"✅ Cache R2: HIT ({len(cached) // 1024} KB scaricati).")
            return cached, "cache"
        if cache_error:
            log(f"❌ Errore lettura cache R2: {cache_error}")
        else:
            log("ℹ️ Cache R2: MISS, immagine non ancora presente.")

    api_token = get_replicate_token()
    if api_token is None:
        log("ℹ️ Token Replicate non configurato nei secrets.")
        return None, "Replicate non configurato"

    notify("✨ Upscaling AI in corso con Replicate...")
    log(f"✨ Chiamata Replicate ({REAL_ESRGAN_MODEL}, x{UPSCALE_FACTOR})...")
    started = time.monotonic()
    upscaled, replicate_error = upscale_with_replicate(png_bytes, api_token)
    elapsed = time.monotonic() - started
    if upscaled is None:
        log(f"❌ Replicate fallito dopo {elapsed:.1f}s: {replicate_error}")
        return None, f"errore Replicate: {replicate_error}"
    log(
        f"✅ Replicate completato in {elapsed:.1f}s "
        f"({len(upscaled) // 1024} KB ricevuti)."
    )

    if r2_client is not None:
        put_error = put_cached_image(
            r2_client, r2_config["bucket"], cache_key, upscaled
        )
        if put_error:
            log(f"❌ Salvataggio su R2 fallito: {put_error}")
        else:
            log(f"💾 Immagine salvata su R2 ({cache_key}).")

    return upscaled, "replicate"
