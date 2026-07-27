"""Impaginazione delle carte su fogli A4 per la stampa a 600 DPI.

Dispone le carte elaborate in una griglia 3x3 perfettamente centrata su un
foglio A4 (210x297mm) a 600 DPI, con spaziatura configurabile tra le carte,
e genera un PDF multipagina (9 carte per pagina).
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

MM_PER_INCH = 25.4
DPI = 600

# Dimensioni in millimetri.
A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297
CARD_WIDTH_MM = 63
CARD_HEIGHT_MM = 88

GRID_COLS = 3
GRID_ROWS = 3
CARDS_PER_PAGE = GRID_COLS * GRID_ROWS

MAX_SPACING_MM = 10


def mm_to_px(mm: float) -> int:
    """Converte i millimetri in pixel a 600 DPI (1mm = 600/25.4 px)."""
    return round(mm * DPI / MM_PER_INCH)


def _as_pil_image(image: Image.Image | bytes) -> Image.Image:
    """Accetta un'immagine PIL o i suoi byte e restituisce un'immagine PIL."""
    if isinstance(image, Image.Image):
        return image
    return Image.open(BytesIO(image))


def generate_a4_pdf(
    images: list[Image.Image | bytes], spacing_mm: float = 0.0
) -> bytes | None:
    """Impagina le immagini delle carte in un PDF A4 a 600 DPI.

    Le carte vengono disposte in una griglia 3x3 centrata matematicamente
    sul foglio: i margini sono calcolati come metà dello spazio residuo
    dopo aver sottratto l'ingombro totale della griglia (carte + spaziatura).
    Se le immagini sono più di 9 il PDF diventa multipagina.

    ``spacing_mm`` è la distanza tra le carte (0-10mm), convertita in pixel
    in base ai 600 DPI.

    Restituisce i byte del PDF, oppure ``None`` se la lista è vuota.
    """
    if not images:
        return None

    spacing_mm = max(0.0, min(float(spacing_mm), MAX_SPACING_MM))

    page_width = mm_to_px(A4_WIDTH_MM)
    page_height = mm_to_px(A4_HEIGHT_MM)
    card_width = mm_to_px(CARD_WIDTH_MM)
    card_height = mm_to_px(CARD_HEIGHT_MM)
    spacing_px = mm_to_px(spacing_mm)

    # Ingombro totale della griglia 3x3 (carte + spaziature interne).
    grid_width = GRID_COLS * card_width + (GRID_COLS - 1) * spacing_px
    grid_height = GRID_ROWS * card_height + (GRID_ROWS - 1) * spacing_px

    if grid_width > page_width or grid_height > page_height:
        raise ValueError(
            f"La griglia 3x3 con spaziatura {spacing_mm}mm "
            "non entra in un foglio A4."
        )

    # Margini per la centratura perfetta della griglia sul foglio.
    margin_x = (page_width - grid_width) // 2
    margin_y = (page_height - grid_height) // 2

    pages: list[Image.Image] = []
    for start in range(0, len(images), CARDS_PER_PAGE):
        batch = images[start : start + CARDS_PER_PAGE]
        page = Image.new("RGB", (page_width, page_height), "white")

        for slot, source in enumerate(batch):
            card = _as_pil_image(source)
            if card.size != (card_width, card_height):
                card = card.resize(
                    (card_width, card_height), resample=Image.Resampling.LANCZOS
                )

            col = slot % GRID_COLS
            row = slot // GRID_COLS
            x = margin_x + col * (card_width + spacing_px)
            y = margin_y + row * (card_height + spacing_px)

            if card.mode == "RGBA":
                page.paste(card, (x, y), mask=card.split()[3])
            else:
                page.paste(card.convert("RGB"), (x, y))

        pages.append(page)

    buffer = BytesIO()
    pages[0].save(
        buffer,
        format="PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=float(DPI),
    )
    return buffer.getvalue()
