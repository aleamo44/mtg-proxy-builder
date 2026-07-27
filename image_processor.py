"""Elaborazione delle immagini delle carte per la stampa a 600 DPI.

Esegue esclusivamente il ricampionamento diretto del PNG di Scryfall alle
dimensioni di stampa (63x88mm a 600 DPI, ovvero 2835x3960 px) seguito da un
leggero Unsharp Masking per definire i contorni di testo e simboli.

Nessun margine di abbondanza (bleed) o bordo extra viene aggiunto:
l'immagine mantiene le proporzioni e la struttura originali.
"""

from __future__ import annotations

from io import BytesIO

import cv2
import numpy as np
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


def enhance_image(pil_image: Image.Image) -> Image.Image:
    """Miglioramento avanzato: CLAHE (spazio LAB) + unsharp masking (OpenCV).

    Pipeline:
    1. Conversione da PIL a NumPy BGR per OpenCV.
    2. Passaggio allo spazio colore LAB per isolare il canale L (luminanza).
    3. CLAHE (clipLimit=2.0, tileGridSize=8x8) sul solo canale L, per
       aumentare il contrasto del testo e pulire il fondo senza alterare
       i colori originali.
    4. Riconversione in BGR e unsharp masking per affilare i contorni.
    5. Riconversione finale in PIL RGB.

    L'eventuale canale alfa (angoli trasparenti della carta) viene
    preservato riapplicandolo al risultato, così l'impaginazione su
    foglio bianco non mostra angoli neri.
    """
    alpha = pil_image.getchannel("A") if pil_image.mode == "RGBA" else None

    # 1. PIL -> NumPy BGR.
    bgr = cv2.cvtColor(np.asarray(pil_image.convert("RGB")), cv2.COLOR_RGB2BGR)

    # 2-3. Spazio LAB: CLAHE sul solo canale di luminanza.
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    lab = cv2.merge((l_channel, a_channel, b_channel))

    # 4. Ritorno in BGR e unsharp masking sui contorni.
    image = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    gaussian = cv2.GaussianBlur(image, (0, 0), sigmaX=2.0)
    sharpened = cv2.addWeighted(image, 1.5, gaussian, -0.5, 0)

    # 5. BGR -> PIL RGB.
    result = Image.fromarray(cv2.cvtColor(sharpened, cv2.COLOR_BGR2RGB))
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)
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
