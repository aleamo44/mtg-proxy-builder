"""Elaborazione delle immagini delle carte per la stampa a 600 DPI.

Esegue esclusivamente il ricampionamento diretto del PNG di Scryfall alle
dimensioni di stampa (63x88mm a 600 DPI, ovvero 2835x3960 px) seguito da un
leggero Unsharp Masking per definire i contorni di testo e simboli.

Nessun margine di abbondanza (bleed) o bordo extra viene aggiunto:
l'immagine mantiene le proporzioni e la struttura originali.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageFilter

# 63 x 88 mm a 600 DPI.
TARGET_DPI = 600
TARGET_WIDTH = 2835
TARGET_HEIGHT = 3960

# Parametri per un unsharp masking leggero.
UNSHARP_RADIUS = 1.2
UNSHARP_PERCENT = 60
UNSHARP_THRESHOLD = 2


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


def process_image_bytes(png_bytes: bytes) -> bytes:
    """Elabora i byte di un PNG Scryfall e restituisce il PNG a 600 DPI.

    L'output incorpora i metadati DPI (600x600) così che le dimensioni
    fisiche di stampa risultino 63x88mm.
    """
    with Image.open(BytesIO(png_bytes)) as image:
        processed = upscale_image(image)

    buffer = BytesIO()
    processed.save(buffer, format="PNG", dpi=(TARGET_DPI, TARGET_DPI))
    return buffer.getvalue()
