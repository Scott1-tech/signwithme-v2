"""The fonts a final PDF can be set in (contract v6), and what each can print.

Two kinds of family, one API name each:

* the standard 14 -- `Helvetica`, `Times-Roman`, `Courier`. Not embedded: a
  viewer supplies them. WinAnsi only (Western European text).
* embedded TrueType -- `LiberationSans`, `LiberationSerif`, `LiberationMono`
  (SIL OFL 1.1, app/assets/fonts/liberation, provenance in
  app/assets/fonts/SOURCES.md). Metric-compatible with Helvetica/Arial,
  Times/Times New Roman and Courier/Courier New respectively, so a line set in
  either family is the same width. Written into the PDF as a SUBSET (only the
  glyphs used, `/FontFile2`, `AAAAAA+` name prefix), so the document prints
  the same everywhere and carries Cyrillic, Uzbek `oʻ`/`gʻ` (U+02BB), Turkish
  and Central European letters, Greek, Vietnamese.

Every family has four faces, picked by `bold`/`italic`:

    family          regular        bold                 italic              bold + italic
    Helvetica       Helvetica      Helvetica-Bold       Helvetica-Oblique   Helvetica-BoldOblique
    Times-Roman     Times-Roman    Times-Bold           Times-Italic        Times-BoldItalic
    Courier         Courier        Courier-Bold         Courier-Oblique     Courier-BoldOblique
    LiberationSans  LiberationSans LiberationSans-Bold  LiberationSans-Italic  LiberationSans-BoldItalic
    (LiberationSerif / LiberationMono: same pattern)

`bold = italic = False` resolves to the family name itself -- exactly the name
contract v5 passed to `setFont` -- which is what keeps a v5 body byte for byte.

Editor v2 B2 adds six embedded families to the same registry
(`FAMILY_REGISTRY`, what `GET /api/fonts` lists): Arimo (Arial/Helvetica
metrics), Carlito (Calibri), Caladea (Cambria), DejaVu Sans / Serif, Amiri
(Latin part only -- right-to-left letters stay refused by `embedded_refusal`).
Plus three script faces for typed signatures only (`SIGNATURE_FONTS`, never
document text). All OFL 1.1 / Bitstream Vera; provenance and SHA-256 in
app/assets/fonts/SOURCES.md. `SUBSTITUTIONS` maps an original font name to
the family replacement text is set in (Calibri -> Carlito, ...). A family is
registered with reportlab the first time one of its faces is used.

The TrueType files are parsed once per process, on first use, under a lock
(finalize runs in FastAPI's thread pool). reportlab keeps per-document subset
state in a WeakKeyDictionary keyed by the canvas' document, so one registered
font serves concurrent builds.
"""
from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

BASE14_FAMILIES: dict[str, tuple[str, str, str, str]] = {
    # (regular, bold, italic, bold italic)
    "Helvetica": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
    "Times-Roman": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "Courier": ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"),
}
EMBEDDED_FAMILY_NAMES = ("LiberationSans", "LiberationSerif", "LiberationMono")
_STYLE_FILES = ("Regular", "Bold", "Italic", "BoldItalic")
FONTS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_DIR = FONTS_ROOT / "liberation"


@dataclass(frozen=True)
class FamilyInfo:
    """One family of the registry (editor v2 B2, `GET /api/fonts`).

    `files` = the four TTF files (regular, bold, italic, bold italic) relative
    to `FONTS_ROOT`; empty for a standard-14 family (not embedded)."""

    family: str
    label: str
    license: str
    files: tuple[str, str, str, str] | tuple = ()
    substitutes_for: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ("latin",)
    category: str = "text"  # text | signature

    @property
    def embedded(self) -> bool:
        return bool(self.files)


_OFL = "OFL-1.1"
_LATIN_CYR_GREEK = ("latin", "cyrillic", "greek")

# The six v6 families first, in their v6 order, then the B2 additions (EDITOR-V2-PLAN
# §9 B2). Order is what `FONT_FAMILIES` and `GET /api/fonts` show.
FAMILY_REGISTRY: tuple[FamilyInfo, ...] = (
    FamilyInfo("Helvetica", "Helvetica", "base14", substitutes_for=("Helvetica",)),
    FamilyInfo("Times-Roman", "Times", "base14", substitutes_for=("Times", "Times-Roman")),
    FamilyInfo("Courier", "Courier", "base14", substitutes_for=("Courier",)),
    FamilyInfo(
        "LiberationSans", "Liberation Sans", _OFL,
        tuple(f"liberation/LiberationSans-{s}.ttf" for s in _STYLE_FILES),
        ("Helvetica", "Arial"), _LATIN_CYR_GREEK,
    ),
    FamilyInfo(
        "LiberationSerif", "Liberation Serif", _OFL,
        tuple(f"liberation/LiberationSerif-{s}.ttf" for s in _STYLE_FILES),
        ("Times New Roman", "Times"), _LATIN_CYR_GREEK,
    ),
    FamilyInfo(
        "LiberationMono", "Liberation Mono", _OFL,
        tuple(f"liberation/LiberationMono-{s}.ttf" for s in _STYLE_FILES),
        ("Courier New", "Courier"), _LATIN_CYR_GREEK,
    ),
    FamilyInfo(
        "Arimo", "Arimo", _OFL, tuple(f"arimo/Arimo-{s}.ttf" for s in _STYLE_FILES),
        ("Arial", "Helvetica"), _LATIN_CYR_GREEK,
    ),
    FamilyInfo(
        "Carlito", "Carlito", _OFL, tuple(f"carlito/Carlito-{s}.ttf" for s in _STYLE_FILES),
        ("Calibri",), _LATIN_CYR_GREEK,
    ),
    FamilyInfo(
        "Caladea", "Caladea", _OFL, tuple(f"caladea/Caladea-{s}.ttf" for s in _STYLE_FILES),
        ("Cambria",), ("latin",),
    ),
    FamilyInfo(
        "DejaVuSans", "DejaVu Sans", "Bitstream-Vera",
        ("dejavu/DejaVuSans.ttf", "dejavu/DejaVuSans-Bold.ttf", "dejavu/DejaVuSans-Oblique.ttf",
         "dejavu/DejaVuSans-BoldOblique.ttf"),
        ("Verdana", "DejaVu Sans", "Bitstream Vera Sans"), _LATIN_CYR_GREEK,
    ),
    FamilyInfo(
        "DejaVuSerif", "DejaVu Serif", "Bitstream-Vera",
        ("dejavu/DejaVuSerif.ttf", "dejavu/DejaVuSerif-Bold.ttf", "dejavu/DejaVuSerif-Italic.ttf",
         "dejavu/DejaVuSerif-BoldItalic.ttf"),
        ("DejaVu Serif", "Bitstream Vera Serif"), _LATIN_CYR_GREEK,
    ),
    # Latin part only: right-to-left letters stay refused (`embedded_refusal`).
    FamilyInfo(
        "Amiri", "Amiri (Latin)", _OFL, tuple(f"amiri/Amiri-{s}.ttf" for s in _STYLE_FILES),
        ("Amiri",), ("latin",),
    ),
)
FAMILIES_BY_NAME: dict[str, FamilyInfo] = {info.family: info for info in FAMILY_REGISTRY}

# Typed-signature scripts (never document text): one face each.
SIGNATURE_FONT_REGISTRY: tuple[FamilyInfo, ...] = (
    FamilyInfo("GreatVibes", "Great Vibes", _OFL, ("script/GreatVibes-Regular.ttf",) * 4,
               category="signature"),
    FamilyInfo("Allura", "Allura", _OFL, ("script/Allura-Regular.ttf",) * 4, category="signature"),
    FamilyInfo("Sacramento", "Sacramento", _OFL, ("script/Sacramento-Regular.ttf",) * 4,
               category="signature"),
)
SIGNATURE_FONTS: dict[str, Path] = {
    info.family: FONTS_ROOT / info.files[0] for info in SIGNATURE_FONT_REGISTRY
}

EMBEDDED_FAMILIES: dict[str, tuple[str, str, str, str]] = {
    info.family: (info.family, f"{info.family}-Bold", f"{info.family}-Italic", f"{info.family}-BoldItalic")
    for info in FAMILY_REGISTRY
    if info.embedded
}
FONT_FAMILIES: tuple[str, ...] = tuple(info.family for info in FAMILY_REGISTRY)
DEFAULT_FAMILY = "Helvetica"

# Registered face name -> TTF file.
EMBEDDED_FONT_FILES: dict[str, Path] = {
    face: FONTS_ROOT / path
    for info in FAMILY_REGISTRY
    if info.embedded
    for face, path in zip(EMBEDDED_FAMILIES[info.family], info.files)
}

# Original font NAME (lower-case, no spaces/dashes) -> the family new text is
# set in (editor v2). Checked by `substitute_family`.
SUBSTITUTIONS: dict[str, str] = {
    "arial": "Arimo",
    "arialmt": "Arimo",
    "helvetica": "LiberationSans",
    "calibri": "Carlito",
    "cambria": "Caladea",
    "timesnewroman": "LiberationSerif",
    "timesnewromanpsmt": "LiberationSerif",
    "times": "LiberationSerif",
    "couriernew": "LiberationMono",
    "courier": "LiberationMono",
    "verdana": "DejaVuSans",
    "dejavusans": "DejaVuSans",
    "dejavuserif": "DejaVuSerif",
    "arimo": "Arimo",
    "carlito": "Carlito",
    "caladea": "Caladea",
    "amiri": "Amiri",
}


def substitute_family(original_font: str) -> str | None:
    """The embedded family the substitution map names for an original font
    name (`ABCDEF+Calibri-Bold` -> `Carlito`), or None if it names none."""
    name = (original_font or "").split("+", 1)[-1]
    base = name.split("-", 1)[0].split(",", 1)[0]
    key = "".join(ch for ch in base.lower() if ch.isalnum())
    if key in SUBSTITUTIONS:
        return SUBSTITUTIONS[key]
    for prefix in sorted(SUBSTITUTIONS, key=len, reverse=True):
        if key.startswith(prefix):
            return SUBSTITUTIONS[prefix]
    return None


def font_catalog(category: str = "text") -> list[dict]:
    """What `GET /api/fonts` lists."""
    registry = SIGNATURE_FONT_REGISTRY if category == "signature" else FAMILY_REGISTRY
    out = []
    for info in registry:
        styles = ["regular"] if info.category == "signature" else ["regular", "bold", "italic", "bold_italic"]
        out.append(
            {
                "family": info.family,
                "label": info.label,
                "styles": styles,
                "license": info.license,
                "substitutes_for": list(info.substitutes_for),
                "scripts": list(info.scripts),
                "embedded": info.embedded,
                "category": info.category,
            }
        )
    return out

_lock = threading.Lock()
_registered = False


def resolve_font(family: str | None, bold: bool = False, italic: bool = False) -> str:
    """The face name to hand to reportlab. An unknown family falls back to
    Helvetica, as contract v4/v5 did (the schema has already refused it)."""
    faces = BASE14_FAMILIES.get(family or "") or EMBEDDED_FAMILIES.get(family or "")
    if faces is None:
        faces = BASE14_FAMILIES[DEFAULT_FAMILY]
    return faces[(2 if italic else 0) + (1 if bold else 0)]


def is_embedded(face_or_family: str) -> bool:
    return face_or_family in EMBEDDED_FONT_FILES


_registered_families: set[str] = set()
_FAMILY_OF_FACE: dict[str, str] = {
    face: family for family, faces in EMBEDDED_FAMILIES.items() for face in faces
}


def _register_family(family: str) -> None:
    """Register one embedded family's four faces with reportlab (lazily: a
    family is parsed the first time one of its faces is used). Caller holds
    `_lock`."""
    if family in _registered_families:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    faces = EMBEDDED_FAMILIES[family]
    for face in faces:
        # subfontIndex 0; reportlab subsets on write (only the used glyphs).
        pdfmetrics.registerFont(TTFont(face, str(EMBEDDED_FONT_FILES[face])))
    regular, bold, italic, bold_italic = faces
    pdfmetrics.registerFontFamily(family, normal=regular, bold=bold, italic=italic, boldItalic=bold_italic)
    _registered_families.add(family)


def register_embedded_fonts() -> None:
    """Register every embedded face and its family, once per process."""
    global _registered
    if _registered:
        return
    with _lock:
        if _registered:
            return
        for family in EMBEDDED_FAMILIES:
            _register_family(family)
        _registered = True


def ensure_font(face: str) -> str:
    """`face`, registered with reportlab if it is an embedded one (only its
    own family is parsed)."""
    if is_embedded(face):
        family = _FAMILY_OF_FACE[face]
        if family not in _registered_families:
            with _lock:
                _register_family(family)
    return face


def glyph_map(face: str) -> dict[int, int]:
    """codepoint -> glyph id of an embedded face (its TrueType cmap)."""
    from reportlab.pdfbase import pdfmetrics

    return pdfmetrics.getFont(ensure_font(face)).face.charToGlyph


# Why a character an embedded font DOES map is still refused. reportlab draws
# glyph by glyph in logical order, with no shaping and no bidi, so each of these
# would print something other than what the user saw in the editor:
#   * control / format / private-use (Cc, Cf, Co, Cs): invisible or undefined --
#     including the bidi overrides (U+202E ...) that make a line read backwards
#     and the zero-width characters;
#   * combining marks (Mn, Me): no mark positioning, so "e" + U+0301 does not
#     compose into "é" -- the precomposed (NFC) character works;
#   * right-to-left letters (bidi R / AL): drawn left to right, i.e. reversed.
REASON_NO_GLYPH = "the font has no glyph for it"
REASON_INVISIBLE = "control, format and private-use characters are not printed"
REASON_COMBINING = "combining marks are not supported; use the precomposed (NFC) character"
REASON_RTL = "right-to-left text is not supported"


def embedded_refusal(char: str, cmap: dict[int, int]) -> str | None:
    """Why `char` cannot be printed in the face whose cmap this is, or None."""
    category = unicodedata.category(char)
    if category in ("Cc", "Cf", "Co", "Cs"):
        return REASON_INVISIBLE
    if not cmap.get(ord(char)):  # absent, or mapped to glyph 0 (.notdef)
        return REASON_NO_GLYPH
    if category in ("Mn", "Me"):
        return REASON_COMBINING
    if unicodedata.bidirectional(char) in ("R", "AL"):
        return REASON_RTL
    return None
