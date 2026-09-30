"""Which font a replacement text is set in (spike §10.2.1 [S2]).

Reusing the original (subset) font is not viable in general: in the measured
package the target font carried 54 glyphs and new text needed 17 it did not
have (16 of 56 fonts covered). So new text is set in a METRIC-COMPATIBLE
embedded family by default and the substitution is reported openly
(`substituted_from`, font NAME only -- never text):

    Helvetica / generic sans           -> LiberationSans
    Arial / ArialMT                    -> Arimo            (B2)
    Calibri                            -> Carlito          (B2)
    Cambria                            -> Caladea          (B2)
    Verdana / DejaVu Sans              -> DejaVuSans       (B2)
    Times / Times New Roman / serif    -> LiberationSerif
    Courier / Courier New / mono       -> LiberationMono

The explicit map is `app.services.fonts.SUBSTITUTIONS`; a name it does not
know falls back to the B1 hint rules below.

The glyph check reuses `app.services.fonts.embedded_refusal`, the rule the
final-PDF annotation text already follows, so the editor and finalize agree on
what is printable. Liberation has no ✔ ✓ ☐.
"""
from __future__ import annotations

from app.services import fonts as app_fonts

# Every embedded family of the registry (B1: Liberation; B2: + Arimo, Carlito,
# Caladea, DejaVuSans, DejaVuSerif, Amiri).
ENGINE_FAMILIES: tuple[str, ...] = tuple(app_fonts.EMBEDDED_FAMILIES)
DEFAULT_ENGINE_FAMILY = "LiberationSans"

_SERIF_HINTS = ("times", "serif", "georgia", "garamond", "book", "roman", "minion", "palatino")
_MONO_HINTS = ("courier", "mono", "consol", "menlo", "typewriter")


def suggest_family(original_font: str) -> str:
    """The embedded family closest to an original font name."""
    mapped = app_fonts.substitute_family(original_font)
    if mapped is not None:
        return mapped
    name = (original_font or "").lower()
    if any(hint in name for hint in _MONO_HINTS):
        return "LiberationMono"
    if "sans" in name:
        return "LiberationSans"
    if any(hint in name for hint in _SERIF_HINTS):
        return "LiberationSerif"
    return "LiberationSans"


LEGACY_FAMILIES = ("LiberationSans", "LiberationSerif", "LiberationMono")


def legacy_family(original_font: str) -> str:
    """The B1 answer (Liberation only) -- what `TextSpanOut.family` keeps
    reporting, so a client built for B1 never sees a family it cannot map.
    The B2 substitute is `suggest_family` (`TextSpanOut.substitute_family`)."""
    name = (original_font or "").lower()
    if any(hint in name for hint in _MONO_HINTS):
        return "LiberationMono"
    if "sans" in name:
        return "LiberationSans"
    if any(hint in name for hint in (*_SERIF_HINTS, "cambria")):
        return "LiberationSerif"
    return "LiberationSans"


def face_for(family: str, bold: bool = False, italic: bool = False) -> str:
    if family not in ENGINE_FAMILIES:
        family = DEFAULT_ENGINE_FAMILY
    return app_fonts.resolve_font(family, bold, italic)


def font_path(face: str) -> str:
    return str(app_fonts.EMBEDDED_FONT_FILES[face])


def refusal(char: str, face: str) -> str | None:
    """Why `char` cannot be set in `face`, or None (see `embedded_refusal`)."""
    return app_fonts.embedded_refusal(char, app_fonts.glyph_map(face))
