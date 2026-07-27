"""Elaborazione delle immagini delle carte per la stampa a 600 DPI.

Esegue esclusivamente il ricampionamento diretto del PNG di Scryfall alle
dimensioni di stampa (63x88mm a 600 DPI, ovvero 2835x3960 px) seguito da un
leggero Unsharp Masking per definire i contorni di testo e simboli.

Nessun margine di abbondanza (bleed) o bordo extra viene aggiunto:
l'immagine mantiene le proporzioni e la struttura originali.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image, ImageFilter

# 63 x 88 mm a 600 DPI.
TARGET_DPI = 600
TARGET_WIDTH = 2835
TARGET_HEIGHT = 3960

# Parametri per un unsharp masking leggero.
UNSHARP_RADIUS = 1.2
UNSHARP_PERCENT = 60
UNSHARP_THRESHOLD = 2

# Modello FSRCNN x2 per la Super-Resolution nativa di OpenCV (~38 KB).
# NB: il repository EDSR_Tensorflow non ospita i modelli FSRCNN (404);
# il file ufficiale si trova nel repo gemello FSRCNN_Tensorflow di Saafke.
FSRCNN_MODEL_URL = (
    "https://raw.githubusercontent.com/Saafke/FSRCNN_Tensorflow/master/"
    "models/FSRCNN_x2.pb"
)
FSRCNN_MODEL_PATH = Path(__file__).parent / "FSRCNN_x2.pb"

_superres = None
_superres_failed = False


def ensure_superres_model(path: Path = FSRCNN_MODEL_PATH) -> bool:
    """Scarica il modello FSRCNN_x2.pb se non è già presente nel progetto.

    Restituisce ``True`` se il file è disponibile e plausibile.
    """
    if path.is_file() and path.stat().st_size > 10_000:
        return True
    try:
        response = requests.get(FSRCNN_MODEL_URL, timeout=30)
        response.raise_for_status()
        if len(response.content) < 10_000:
            return False
        path.write_bytes(response.content)
        return True
    except requests.RequestException:
        return False


def _get_superres():
    """Inizializza (una sola volta) il modulo SuperRes di OpenCV con FSRCNN.

    Restituisce l'istanza pronta all'uso oppure ``None`` se il modello non
    è disponibile o non può essere caricato: in quel caso ``enhance_image``
    ripiega sul ridimensionamento bicubico.
    """
    global _superres, _superres_failed
    if _superres is not None or _superres_failed:
        return _superres
    try:
        if not hasattr(cv2, "dnn_superres") or not ensure_superres_model():
            raise RuntimeError("modello FSRCNN non disponibile")
        sr = cv2.dnn_superres.DnnSuperResImpl_create()
        sr.readModel(str(FSRCNN_MODEL_PATH))
        sr.setModel("fsrcnn", 2)
        _superres = sr
    except Exception:
        _superres_failed = True
        _superres = None
    return _superres


def upscale_image(image: Image.Image) -> Image.Image:
    """Ricampiona l'immagine a 2835x3960 px (600 DPI) e la rifinisce.

    Esegue un resize diretto con filtro Lanczos, senza aggiungere bordi o
    margini di abbondanza, quindi applica un Unsharp Mask leggero per
    definire i contorni di testo e simboli.
    """
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")

    upscaled = image.resize(
        (TARGET_WIDTH, TARGET_HEIGHT),
        resample=Image.Resampling.LANCZOS,
    )

    return upscaled.filter(
        ImageFilter.UnsharpMask(
            radius=UNSHARP_RADIUS,
            percent=UNSHARP_PERCENT,
            threshold=UNSHARP_THRESHOLD,
        )
    )


def _clahe_luminance(bgr: np.ndarray, clip_limit: float) -> np.ndarray:
    """Applica CLAHE al solo canale L (LAB) di un'immagine BGR."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    return cv2.cvtColor(
        cv2.merge((l_channel, a_channel, b_channel)), cv2.COLOR_LAB2BGR
    )


def enhance_image(pil_image: Image.Image) -> Image.Image:
    """Miglioramento avanzato: Super-Resolution FSRCNN x2 + CLAHE leggero.

    Applica l'upscale neurale nativo di OpenCV (``cv2.dnn_superres`` con il
    modello FSRCNN_x2) sull'immagine BGR, poi rifinisce il contrasto del
    testo con un CLAHE leggerissimo (clipLimit=1.2) sul canale L (LAB).

    Fallback: se il modello non è disponibile o non si carica, ripiega su
    un ridimensionamento bicubico x2 con un filtro di affilatura molto
    leggero.

    L'eventuale canale alfa (angoli trasparenti della carta) viene
    ridimensionato e riapplicato al risultato, così l'impaginazione su
    foglio bianco non mostra angoli neri.
    """
    alpha = pil_image.getchannel("A") if pil_image.mode == "RGBA" else None
    bgr = cv2.cvtColor(np.asarray(pil_image.convert("RGB")), cv2.COLOR_RGB2BGR)

    sr = _get_superres()
    if sr is not None:
        upscaled = sr.upsample(bgr)
        refined = _clahe_luminance(upscaled, clip_limit=1.2)
        result = Image.fromarray(cv2.cvtColor(refined, cv2.COLOR_BGR2RGB))
    else:
        # Fallback: bicubico x2 + affilatura molto leggera.
        upscaled = cv2.resize(
            bgr, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC
        )
        result = Image.fromarray(cv2.cvtColor(upscaled, cv2.COLOR_BGR2RGB))
        result = result.filter(
            ImageFilter.UnsharpMask(radius=1.0, percent=40, threshold=3)
        )

    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha.resize(result.size, Image.Resampling.LANCZOS))
    return result


def process_image_bytes(png_bytes: bytes, enhance: bool = False) -> bytes:
    """Elabora i byte di un PNG Scryfall e restituisce il PNG a 600 DPI.

    Se ``enhance`` è ``True`` applica prima :func:`enhance_image`
    (denoise bilaterale + unsharp masking) sull'immagine sorgente.
    L'output incorpora i metadati DPI (600x600) così che le dimensioni
    fisiche di stampa risultino 63x88mm.
    """
    with Image.open(BytesIO(png_bytes)) as image:
        source = enhance_image(image) if enhance else image
        processed = upscale_image(source)

    buffer = BytesIO()
    processed.save(buffer, format="PNG", dpi=(TARGET_DPI, TARGET_DPI))
    return buffer.getvalue()
