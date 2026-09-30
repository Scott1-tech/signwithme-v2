"""Typed signatures (EDITOR-V2-PLAN §6, §7 "Sign ▸ Type"; B2).

`POST /api/signatures {typed: {text, font}}` renders the text in one of three
SIL-OFL script fonts (`app/assets/fonts/script`, provenance in SOURCES.md) to a
PNG, and that PNG goes through the SAME normalisation as a drawn signature
(`deps.normalise_signature_png`: RGBA re-encode, ink crop, bottom on the ink).
From there it is an ordinary library signature -- the row carries
`source='typed'`, and the typed TEXT is never stored anywhere (not in the row,
not in the audit trail, not in a log line).

Rendering: Pillow + FreeType, black ink on a transparent background, at a
fixed 160 px em so every typed signature has the same resolution as a pad
drawing of a similar size.
"""
from __future__ import annotations

import io
import threading
import unicodedata

from PIL import Image, ImageDraw, ImageFont

from app.services.fonts import SIGNATURE_FONTS, embedded_refusal

RENDER_SIZE_PX = 160
PADDING_PX = 24
INK_RGBA = (0, 0, 0, 255)

_lock = threading.Lock()
_pil_fonts: dict[str, ImageFont.FreeTypeFont] = {}
_cmaps: dict[str, dict[int, int]] = {}


def _cmap(font: str) -> dict[int, int]:
    with _lock:
        cmap = _cmaps.get(font)
        if cmap is None:
            from reportlab.pdfbase.ttfonts import TTFontFile

            cmap = dict(TTFontFile(str(SIGNATURE_FONTS[font])).charToGlyph)
            _cmaps[font] = cmap
        return cmap


def _pil_font(font: str) -> ImageFont.FreeTypeFont:
    with _lock:
        loaded = _pil_fonts.get(font)
        if loaded is None:
            loaded = ImageFont.truetype(str(SIGNATURE_FONTS[font]), RENDER_SIZE_PX)
            _pil_fonts[font] = loaded
        return loaded


def unprintable(text: str, font: str) -> tuple[int, str, str] | None:
    """(index, character, reason) of the first character `font` cannot draw
    faithfully, or None. Same rules as document text (`embedded_refusal`):
    no glyph, control/format, combining marks, right-to-left. A space is
    always fine (script fonts map it, but the check must not depend on it)."""
    if font not in SIGNATURE_FONTS:
        return 0, text[:1] or " ", "unknown font"
    cmap = _cmap(font)
    for index, char in enumerate(unicodedata.normalize("NFC", text)):
        if char == " ":
            continue
        reason = embedded_refusal(char, cmap)
        if reason is not None:
            return index, char, reason
    return None


MAX_WIDTH_PX = 3600  # under deps.MAX_SIGNATURE_DIMENSION (4000) with the padding


def render_png(text: str, font: str) -> bytes:
    """The typed text as an RGBA PNG (not yet normalised). A long text is set
    smaller so the image stays inside the signature pixel limits."""
    text = unicodedata.normalize("NFC", text).strip()
    face = _pil_font(font)
    left, top, right, bottom = face.getbbox(text)
    if right - left > MAX_WIDTH_PX:
        size = max(24, int(RENDER_SIZE_PX * MAX_WIDTH_PX / (right - left)))
        face = ImageFont.truetype(str(SIGNATURE_FONTS[font]), size)
        left, top, right, bottom = face.getbbox(text)
    width = max(1, right - left) + 2 * PADDING_PX
    height = max(1, bottom - top) + 2 * PADDING_PX
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((PADDING_PX - left, PADDING_PX - top), text, font=face, fill=INK_RGBA)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def typed_signature_png(text: str, font: str) -> bytes:
    """Rendered AND normalised through the drawn-signature chain."""
    from app.api.deps import normalise_signature_png

    return normalise_signature_png(render_png(text, font))
