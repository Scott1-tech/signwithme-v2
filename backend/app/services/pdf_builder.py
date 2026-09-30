"""Overlay signatures and dates onto the uploaded contract to produce the final
legally binding document.

Details that a naive overlay gets wrong, and are handled here:

* Page size is read from each page's own mediabox. Hardcoding `letter` shifts
  every overlay on a legal or A4 page.
* Canonical bounding boxes use a top-left origin (pdfplumber); reportlab draws
  from the bottom left. The conversion happens once, in `_draw_page`.
* Pages carrying /Rotate need their overlay rotated to match, or the signature
  lands sideways.
* A field box does not end at the rule's visible ink, and a captured signature
  may carry padding around its strokes. The rule is measured from the rendered
  page (services.rule_locator) and the strokes from the image (`_ink_metrics`).
* A placement on a page the document does not have is refused
  (`PageOutOfRangeError`), never silently dropped.

Placement math -- shared VERBATIM with the frontend preview (contract v2):

    line = rule_y ?? (bbox.y + bbox.height)
    dx   = slot_offsets_x_pt[field_id] (slot placements only; free = 0)
    scale = slot_scales[field_id] if present else signature_scale   (contract v3)
    signature: image contain-fitted into bbox.width*scale x bbox.height*scale,
               left = bbox.x + dx,
               ink bottom = line - SIGNATURE_INK_GAP - (signature_offset_pt + slot_offset)
    date:      baseline = line - DATE_INK_GAP - (date_offset_pt + slot_offset),
               x = bbox.x + dx, font/size from the layout

All y values are canonical (top-left origin, points); vertical offsets are
POSITIVE = UP, the horizontal slot offset is POSITIVE = RIGHT.

`slot_scales` REPLACES the global scale for its slot rather than multiplying
it, so a corner drag lands at the size the handle was dragged to. Only the box
the image is fitted into changes: the left edge stays `bbox.x + dx` and the ink
bottom stays on its target, so a resized signature grows UP and to the RIGHT
from a fixed bottom-left anchor. Nothing clamps the result to the page -- a
large enough scale simply draws past the mediabox, where viewers clip it.

Free annotations (contract v4)
------------------------------

`AnnotationPlacement` is a mark the user put somewhere by hand rather than in a
template slot: a line of text, a date, a tick or a cross. It carries its own
`(x_pt, y_pt)` in the SAME canonical space as `bbox`/`rule_y` -- points, origin
TOP-LEFT -- and no slot maths applies to it at all: no rule snapping, no layout
offset, no scale. `text`/`date` anchor the text BASELINE at the point;
`check`/`cross` centre the mark on it.

Annotations are drawn onto the same overlay canvas as the signatures and dates
but AFTER them, so the painter's algorithm puts them on top (z-order).

Two decisions worth stating, because both protect a legally binding document
from silently printing something other than what was typed:

* A tick/cross is STROKED GEOMETRY, not a glyph. ZapfDingbats has the obvious
  characters (a20 = 0x34 = heavy check, a24 = 0x38 = heavy ballot X), but it is
  one of the base-14 fonts a viewer is expected to *supply*, and viewers that
  have no such font draw .notdef -- a solid black box where the tick should be
  (pypdfium2, i.e. the Chrome/Edge engine, does exactly that here). Strokes need
  no font, render identically everywhere, and give exact geometry: the ink box
  of a mark is exactly `size_pt` square, centred on `(x_pt, y_pt)`.
* A character the text font cannot encode is REFUSED, not drawn. reportlab
  never raises on one: `pdfmetrics.unicode2T1` silently swaps that stretch to
  ZapfDingbats byte `n` -- glyph `a73`, a filled black square -- so a Cyrillic
  name would print on a signed contract as a row of black blocks, and nobody
  would be told. `undrawable_character` asks reportlab's own fallback what it
  would do, so the check cannot drift from what the drawing code emits. The
  standard 14 fonts are WinAnsi: Western European text, the curly quotes, the
  dashes and the euro sign are fine; Cyrillic, CJK, emoji and every control
  character (including a newline and a tab) are not.

Embedded fonts and bold/italic (contract v6)
--------------------------------------------

`text`/`date` annotations and the slot dates may also be set in an EMBEDDED
TrueType family -- `LiberationSans`, `LiberationSerif`, `LiberationMono`
(app.services.fonts) -- and any family in bold and/or italic. An embedded font
is written into the PDF as a subset (`/FontFile2`), so what prints does not
depend on the viewer's fonts. `undrawable_character` then checks the face's
own TrueType cmap instead of WinAnsi: a character with no glyph (emoji, CJK,
...) is refused exactly as before, as are characters reportlab cannot set
faithfully without shaping (controls/format characters, combining marks,
right-to-left letters -- see `fonts.embedded_refusal`). With `bold`/`italic`
absent or false the face is the family itself and the output is the v5 bytes.

Sejda-style tools (contract v5)
-------------------------------

Same canonical space, same mapping, same all-or-nothing refusal. How each kind
is drawn (colours: `ANNOTATION_PALETTE_HEX`, a closed named palette with NO
white):

* `highlight` -- each rect filled with the colour at alpha 0.35 (normal blend),
  so the text beneath stays legible. `underline` / `strikeout` -- a filled bar
  of thickness max(0.75, h * 0.08) pt at the bottom / the vertical middle of
  each rect, full rect width.
* `ink` -- one polyline per path, `stroke_pt` wide, round caps and joins.
* `rect` / `ellipse` -- outline `stroke_pt` wide, centred on the box edge (as
  SVG strokes); `fill: true` adds a same-colour fill at alpha 0.2. Never opaque.
* `line` / `arrow` -- butt caps, so the ink stops at the endpoints. The arrow
  head is a filled triangle, tip exactly at (x2, y2), length 6 * stroke and
  half-width 3 * stroke (scaled down together on a line shorter than that).
* `image` -- a stored, normalised RGBA PNG (POST .../annotation-assets),
  stretched to exactly the requested box, alpha kept.
* `whiteout` -- an opaque white box.
* `note` -- NOT ink: a real `/Text` annotation (icon + popup), added with pypdf
  after the overlays are merged (`_add_sticky_note`).

Z-order: original page -> whiteouts -> signatures and slot dates -> every other
annotation in request order. A whiteout can therefore cover only the ORIGINAL
content -- never a placed signature -- and the replacement text of an "edit
existing text" pair (whiteout + text sharing a `group_id`) lands on top of it.

WHITEOUT IS NOT REDACTION. It paints over the page; it does not remove
anything. The covered words remain in the page's content stream and are
returned by copy/paste, by search and by any text extractor (pinned by a test).
The audit trail records where every whiteout is, so what was covered can be
recovered from the original upload. Real redaction is out of scope.
"""
from __future__ import annotations

import hashlib
import io
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.services.fonts import (
    FONT_FAMILIES,
    embedded_refusal,
    ensure_font,
    glyph_map,
    is_embedded,
    resolve_font,
)
from app.services.geometry import BBox, rotate_for_page
from app.services.rule_locator import RuleLocator
from app.services.validators import format_date

logger = logging.getLogger(__name__)

DATE_FONT = "Helvetica"
DATE_FONT_SIZE = 11.0
# Font FAMILIES (contract v6: the base-14 three + three embedded Liberation).
# The face actually drawn is `resolve_font(family, bold, italic)`.
DATE_FONTS = FONT_FAMILIES

# Clearance between a mark and the top of the line's ink, "as a pen leaves it".
SIGNATURE_INK_GAP = 1.0
DATE_INK_GAP = 1.0
# Below this alpha a pixel is padding, not ink.
INK_ALPHA_THRESHOLD = 8
# For an opaque (scanned) signature there is no alpha to go by, so anything
# darker than this counts as ink.
INK_LUMA_THRESHOLD = 250
# Projection noise filter for `_ink_metrics`: a row (column) is ink only when
# at least this fraction of the image width (height) is ink on it, and a thin
# run of such rows must be this many rows deep unless one row is strong.
INK_MIN_LINE_FRACTION = 0.005
INK_MIN_RUN = 2
INK_STRONG_LINE_FRACTION = 0.05

_UNSET = object()
# Public name for callers: "no stored rule_y -- measure it live".
RULE_UNSET = _UNSET

# -- free annotations (contract v4) -------------------------------------------

ANNOTATION_FONTS = DATE_FONTS
ANNOTATION_FONT = DATE_FONT
ANNOTATION_TEXT_KINDS = ("text", "date")
ANNOTATION_MARK_KINDS = ("check", "cross")
# -- contract v5 --
ANNOTATION_MARKUP_KINDS = ("highlight", "underline", "strikeout")
ANNOTATION_SHAPE_KINDS = ("rect", "ellipse")
ANNOTATION_LINE_KINDS = ("line", "arrow")
ANNOTATION_BOX_KINDS = ANNOTATION_SHAPE_KINDS + ("image", "whiteout")
ANNOTATION_KINDS = (
    ANNOTATION_TEXT_KINDS
    + ANNOTATION_MARK_KINDS
    + ANNOTATION_MARKUP_KINDS
    + ("ink",)
    + ANNOTATION_SHAPE_KINDS
    + ANNOTATION_LINE_KINDS
    + ("note", "image", "whiteout")
)

# The named palette (contract v5), as RGB 0..1. The frontend preview must use
# the same hex values:
#   black  #000000   blue   #1D4ED8   red    #DC2626   green  #16A34A
#   yellow #FACC15   orange #EA580C   purple #7E22CE
# `black` is exactly (0, 0, 0) so that a v4 body (no `color`) emits exactly
# the v4 operators ("0 0 0 rg" / "0 0 0 RG") -- the byte-for-byte guarantee.
# No white on purpose: white paint exists only as the `whiteout` kind.
ANNOTATION_PALETTE_HEX = {
    "black": "#000000",
    "blue": "#1D4ED8",
    "red": "#DC2626",
    "green": "#16A34A",
    "yellow": "#FACC15",
    "orange": "#EA580C",
    "purple": "#7E22CE",
}


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


ANNOTATION_PALETTE = {name: _hex_to_rgb(code) for name, code in ANNOTATION_PALETTE_HEX.items()}
ANNOTATION_DEFAULT_COLOR = {
    "highlight": "yellow",
    "underline": "red",
    "strikeout": "red",
    "note": "yellow",
}
# Highlight: translucent so the text under it stays legible (normal blend).
HIGHLIGHT_ALPHA = 0.35
# rect/ellipse `fill: true`: translucent only -- an opaque fill would be an
# unlabelled whiteout.
SHAPE_FILL_ALPHA = 0.2
# Underline / strikeout bar thickness = max(MIN, rect height * RATIO). A 14 pt
# line box gives a ~1.1 pt bar; tiny text never goes below 0.75 pt.
MARKUP_LINE_RATIO = 0.08
MARKUP_LINE_MIN_PT = 0.75
# Arrow head (filled triangle, tip exactly on (x2, y2)): length = RATIO * stroke,
# half-width = RATIO/2 * stroke, both capped at the line's own length.
ARROW_HEAD_LENGTH_RATIO = 6.0
ARROW_HEAD_HALF_WIDTH_RATIO = 3.0
# Sticky note icon: (x_pt, y_pt) is its top-left; the /Rect is this square.
NOTE_ICON_PT = 20.0
# /F on the /Text annotation: Print (4) | NoZoom (8) | NoRotate (16), what
# Acrobat itself writes for a sticky note. Print is what makes it appear on paper.
NOTE_ANNOTATION_FLAGS = 4 | 8 | 16

# Stroke weight of a tick/cross, as a fraction of its size. Round caps add half
# of it back at each extreme, so the INK box is exactly `size_pt` square (see
# `_mark_reach`): the geometry is what the test asserts, not a font's opinion.
MARK_LINE_WIDTH_RATIO = 0.16
# The tick, in units of the reach from the centre (reportlab axes, y UP): left
# arm, down to the elbow, up to the long arm. The path's own box is exactly
# [-1, 1] on both axes, which is what keeps the mark centred.
MARK_CHECK_PATH = ((-1.0, 0.0), (-0.25, -1.0), (1.0, 1.0))


class PageOutOfRangeError(ValueError):
    """A placement targets a page the source document does not have."""

    def __init__(self, pages: list[int], page_count: int):
        super().__init__(f"Placement on page(s) {pages} but the document has {page_count} page(s)")
        self.pages = pages
        self.page_count = page_count


class AnnotationPlacementError(ValueError):
    """An annotation's page, or its anchor point, is not on the document.

    Separate from `PageOutOfRangeError` because the two mean different things to
    a caller: a signature on a missing page is a stored row that has to be
    removed (409), while an annotation is supplied in the finalize request
    itself, so it is a bad request (422).
    """

    # Closed set, carried verbatim in the API `details` so a client can branch.
    PAGE_OUT_OF_RANGE = "page_out_of_range"
    OUTSIDE_PAGE = "annotation_outside_page"

    def __init__(self, reason: str, entries: list[dict], page_count: int):
        super().__init__(
            f"{len(entries)} annotation(s) cannot be placed on this "
            f"{page_count}-page document ({reason})"
        )
        self.reason = reason
        self.entries = entries
        self.page_count = page_count


@dataclass(frozen=True)
class Layout:
    signature_offset_pt: float = 0.0
    date_offset_pt: float = 0.0
    slot_offsets_pt: dict = field(default_factory=dict)
    slot_offsets_x_pt: dict = field(default_factory=dict)
    signature_scale: float = 1.0
    slot_scales: dict = field(default_factory=dict)
    date_font: str = DATE_FONT
    date_font_size: float = DATE_FONT_SIZE
    # contract v6: slot dates in bold / italic (any family).
    date_bold: bool = False
    date_italic: bool = False

    @property
    def date_face(self) -> str:
        return resolve_font(self.date_font, self.date_bold, self.date_italic)

    @classmethod
    def from_mapping(cls, data: dict | None) -> "Layout":
        if not data:
            return cls()
        return cls(**data)

    def slot_offset(self, field_id: str | None) -> float:
        """Vertical per-slot offset, POSITIVE = UP. A free placement gets 0."""
        if not field_id:
            return 0.0
        return float(self.slot_offsets_pt.get(field_id, 0.0))

    def slot_offset_x(self, field_id: str | None) -> float:
        """Horizontal per-slot offset, POSITIVE = RIGHT. A free placement gets 0."""
        if not field_id:
            return 0.0
        return float(self.slot_offsets_x_pt.get(field_id, 0.0))

    def signature_scale_for(self, field_id: str | None) -> float:
        """The scale this signature is drawn at (contract v3).

        A `slot_scales` entry REPLACES the global `signature_scale`; it is
        deliberately not multiplied by it, so the size a user drags a corner
        handle to is the size that comes out, whatever the global slider says.
        A slot with no entry -- and every free placement -- keeps the global.
        """
        if field_id:
            slot = self.slot_scales.get(field_id)
            if slot is not None:
                return float(slot)
        return float(self.signature_scale)


@dataclass
class SignaturePlacement:
    page: int  # 1-indexed
    bbox: BBox  # canonical: points, top-left origin
    image_bytes: bytes
    field_id: str | None = None
    # The template's stored `rule_y` (float, or None = "no line"). Left unset,
    # the rule is measured live from the source document.
    rule_y: object = _UNSET


@dataclass
class DatePlacement:
    page: int
    bbox: BBox
    value: str  # rendered as MM/DD/YYYY regardless of the input format
    field_id: str | None = None
    rule_y: object = _UNSET


@dataclass
class AnnotationPlacement:
    """A mark the user placed by hand, outside any template slot (contract v4).

    `x_pt`/`y_pt` are canonical (points, top-left origin), the same space as
    `bbox` and `rule_y`. For `text`/`date` the point is the LEFT END OF THE TEXT
    BASELINE; for `check`/`cross` it is the CENTRE of the mark. `id` is the
    client's own handle for the element and is used only to name it in an error.
    """

    id: str
    kind: str
    page: int  # 1-indexed
    x_pt: float = 0.0
    y_pt: float = 0.0
    text: str | None = None
    font: str = ANNOTATION_FONT
    size_pt: float = DATE_FONT_SIZE
    # -- contract v5 --------------------------------------------------------
    # Palette name; None = the kind's default (black, or see
    # ANNOTATION_DEFAULT_COLOR).
    color: str | None = None
    # rect/ellipse/image/whiteout: the box is (x_pt, y_pt, w_pt, h_pt).
    w_pt: float = 0.0
    h_pt: float = 0.0
    # line/arrow: from (x_pt, y_pt) to (x2_pt, y2_pt).
    x2_pt: float = 0.0
    y2_pt: float = 0.0
    stroke_pt: float = 2.0
    fill: bool = False
    # highlight/underline/strikeout: ((x, y, w, h), ...)
    rects: tuple = ()
    # ink: (((x, y), (x, y), ...), ...)
    paths: tuple = ()
    # image: the stored, normalised RGBA PNG (see POST .../annotation-assets).
    asset_id: str | None = None
    image_bytes: bytes | None = None
    group_id: str | None = None
    # -- contract v6: text/date style (any family, see app.services.fonts) --
    bold: bool = False
    italic: bool = False

    @property
    def face(self) -> str:
        return resolve_font(self.font, self.bold, self.italic)

    def rgb(self) -> tuple[float, float, float]:
        name = self.color or ANNOTATION_DEFAULT_COLOR.get(self.kind, "black")
        return ANNOTATION_PALETTE.get(name, ANNOTATION_PALETTE["black"])


@dataclass
class BuildResult:
    pdf_bytes: bytes
    sha256: str
    page_count: int


# Characters a font is known to draw. Bounded by the font's encoding (WinAnsi:
# at most ~224 entries per font), so it cannot grow with traffic.
_DRAWABLE_CHARS: dict[str, set[str]] = {}


def undrawable_character(
    text: str, font: str, bold: bool = False, italic: bool = False
) -> tuple[int, str] | None:
    """`(index, character)` of the first character `font` cannot draw, or None.

    `font` is a family; `bold`/`italic` pick the face (contract v6).

    Standard-14 faces: reportlab does NOT raise on a character outside the
    font's encoding: its `unicode2T1` quietly re-runs that stretch in
    ZapfDingbats with byte `n` (glyph `a73`, a filled black square), so a
    Cyrillic name comes out of a signed contract as a row of blocks. Asking
    `unicode2T1` itself -- and treating "came back in a font other than the one
    asked for" as the failure -- means this can never disagree with what
    `_draw_annotation_text` emits, whatever reportlab changes its stand-in to.

    Embedded faces: reportlab maps a character missing from the TrueType cmap
    to glyph 0 (.notdef, an empty box or nothing) without a word, so the cmap
    itself is the test, plus the characters it cannot set faithfully
    (`fonts.embedded_refusal`; `undrawable_reason` says which rule).
    """
    name = resolve_font(font, bold, italic)
    if is_embedded(name):
        cmap = glyph_map(name)
        for index, char in enumerate(text):
            if embedded_refusal(char, cmap) is not None:
                return index, char
        return None

    from reportlab.pdfbase import pdfmetrics

    known = _DRAWABLE_CHARS.setdefault(name, set())
    face = None
    for index, char in enumerate(text):
        if char in known:
            continue
        if face is None:
            face = pdfmetrics.getFont(name)
        if any(used is not face for used, _ in pdfmetrics.unicode2T1(char, [face])):
            return index, char
        known.add(char)
    return None


def undrawable_reason(char: str, font: str, bold: bool = False, italic: bool = False) -> str:
    """Why `char` was refused by `undrawable_character` -- the tail of the 422
    message. The standard-14 wording is unchanged from contract v4."""
    name = resolve_font(font, bold, italic)
    if is_embedded(name):
        return embedded_refusal(char, glyph_map(name)) or "it cannot be printed"
    return "the standard PDF fonts cover Western European text only"


def signature_ink_bottom(line: float, layout: Layout, field_id: str | None) -> float:
    return line - SIGNATURE_INK_GAP - (layout.signature_offset_pt + layout.slot_offset(field_id))


def date_baseline(line: float, layout: Layout, field_id: str | None) -> float:
    return line - DATE_INK_GAP - (layout.date_offset_pt + layout.slot_offset(field_id))


class PdfBuilder:
    def build_final_contract(
        self,
        source_pdf: bytes,
        signatures: list[SignaturePlacement],
        dates: list[DatePlacement],
        *,
        title: str = "Signed Independent Contractor Agreement",
        author: str = "Cargo Freight Trucking Inc",
        owner_password: str | None = None,
        subject: str | None = None,
        keywords: str | None = None,
        layout: Layout | None = None,
        annotations: list[AnnotationPlacement] | None = None,
        finish: bool = True,
    ) -> BuildResult:
        """`finish=False` (editor v2 `final_pipeline`): the overlays are merged
        but no metadata/encryption is written -- the caller adds the v7 markup
        on top and then calls `finish_pdf` itself. `finish=True` (default) is
        the unchanged, byte-for-byte path."""
        layout = layout or Layout()
        annotations = list(annotations or [])
        # Cloning into the writer up front keeps every page attached to it, which
        # is what pypdf requires for merge_page (and is mandatory from pypdf 7).
        writer = PdfWriter(clone_from=io.BytesIO(source_pdf))
        page_count = len(writer.pages)

        out_of_range = sorted(
            {p.page for p in [*signatures, *dates] if not 1 <= int(p.page) <= page_count}
        )
        if out_of_range:
            raise PageOutOfRangeError(out_of_range, page_count)
        # Every annotation is checked before any of them is drawn: a request is
        # all or nothing, never "most of your marks landed".
        _assert_annotations_on_page(annotations, [_displayed_size(p) for p in writer.pages])

        sigs_by_page: dict[int, list[SignaturePlacement]] = {}
        dates_by_page: dict[int, list[DatePlacement]] = {}
        notes_by_page: dict[int, list[AnnotationPlacement]] = {}
        for placement in signatures:
            sigs_by_page.setdefault(placement.page - 1, []).append(placement)
        for placement in dates:
            dates_by_page.setdefault(placement.page - 1, []).append(placement)
        # Sticky notes are not ink: they become real /Text annotations after the
        # overlays are merged (see `_add_sticky_note`).
        sticky_notes = [note for note in annotations if note.kind == "note"]
        for note in annotations:
            if note.kind != "note":
                notes_by_page.setdefault(int(note.page) - 1, []).append(note)

        needs_live = any(p.rule_y is _UNSET for p in [*signatures, *dates])
        with RuleLocator(source_pdf if needs_live else None) as rules:
            for index, page in enumerate(writer.pages):
                page_sigs = sigs_by_page.get(index, [])
                page_dates = dates_by_page.get(index, [])
                page_notes = notes_by_page.get(index, [])
                if page_sigs or page_dates or page_notes:
                    self._merge_overlay(
                        page,
                        self._draw_page(
                            page, index + 1, page_sigs, page_dates, page_notes, rules, layout
                        ),
                    )

        for note in sticky_notes:
            _add_sticky_note(writer, note)

        if finish:
            _write_metadata(writer, title=title, author=author, subject=subject,
                            keywords=keywords, owner_password=owner_password)

        buffer = io.BytesIO()
        writer.write(buffer)
        pdf_bytes = buffer.getvalue()
        return BuildResult(
            pdf_bytes=pdf_bytes,
            sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            page_count=page_count,
        )

    # -- overlay -------------------------------------------------------------

    def _merge_overlay(self, page, overlay_page) -> None:
        """Merge an overlay drawn in DISPLAYED coordinates (see `_draw_page`).

        pypdf's merge ignores the overlay's own /Rotate, so the content has to
        be put into the base page's UNROTATED user space by an explicit
        matrix. Rotation 0 keeps the plain merge (the v1-v6 bytes)."""
        rotation = int(page.get("/Rotate") or 0) % 360
        if rotation == 0:
            page.merge_page(overlay_page)
            return
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        left, bottom = float(page.mediabox.left), float(page.mediabox.bottom)
        # Displayed (bottom-left origin, y up) -> unrotated user space.
        a, b, c, d, e, f = {
            90: (0, 1, -1, 0, width, 0),
            180: (-1, 0, 0, -1, width, height),
            270: (0, -1, 1, 0, 0, height),
        }[rotation]
        page.merge_transformed_page(overlay_page, (a, b, c, d, e + left, f + bottom))

    def _draw_page(
        self,
        page,
        page_number: int,
        signatures: list[SignaturePlacement],
        dates: list[DatePlacement],
        annotations: list[AnnotationPlacement],
        rules: RuleLocator,
        layout: Layout,
    ):
        # A rotated page is displayed with its dimensions swapped; the overlay
        # canvas must match the *displayed* geometry. Everything is drawn
        # upright in DISPLAYED coordinates (canonical space, no per-mark
        # rotation) and `_merge_overlay` maps the whole canvas into the page's
        # unrotated user space with one matrix (editor v2 B2: the old per-mark
        # mapping misplaced every mark on /Rotate 90/270 and drew images upside
        # down on 180). Rotation 0 is exactly the v1-v6 drawing.
        canvas_size = _displayed_size(page)
        unrotated = canvas_size
        rotation = 0

        packet = io.BytesIO()
        pdf_canvas = canvas.Canvas(packet, pagesize=canvas_size)

        # Z-order (contract v5), one canvas, painter's algorithm:
        #   original page -> whiteouts -> signatures / slot dates -> every other
        #   annotation, in request order.
        # So a whiteout can only ever cover the ORIGINAL content: it cannot hide
        # a placed signature, and the replacement text of an "edit existing
        # text" pair lands on top of its whiteout.
        whiteouts = [note for note in annotations if note.kind == "whiteout"]
        annotations = [note for note in annotations if note.kind != "whiteout"]
        for note in whiteouts:
            self._draw_annotation(pdf_canvas, note, unrotated, rotation, canvas_size[1])

        for placement in signatures:
            line = _line(placement, page_number, rules)
            self._draw_signature(
                pdf_canvas, placement, line, layout, unrotated, rotation, canvas_size[1]
            )

        for placement in dates:
            line = _line(placement, page_number, rules)
            self._draw_date(pdf_canvas, placement, line, layout, unrotated, rotation, canvas_size[1])

        # Last, so a hand-placed mark is ON TOP of the slot marks it may overlap
        # (one canvas, painter's algorithm). Annotations take no layout offset,
        # no rule snapping and no scale: the point the user chose is the point.
        for note in annotations:
            self._draw_annotation(pdf_canvas, note, unrotated, rotation, canvas_size[1])

        pdf_canvas.save()
        packet.seek(0)
        return PdfReader(packet).pages[0]

    def _draw_signature(
        self,
        pdf_canvas,
        placement: SignaturePlacement,
        line: float,
        layout: Layout,
        unrotated: tuple[float, float],
        rotation: int,
        page_height: float,
    ) -> None:
        bbox = placement.bbox
        left = bbox.x + layout.slot_offset_x(placement.field_id)
        # Per-slot scale wins over the global one for this slot (contract v3).
        # Only the fit box grows: `left` and `ink_bottom` below are untouched,
        # which is what keeps the bottom-left anchor still while resizing.
        scale_factor = layout.signature_scale_for(placement.field_id)
        box_width = bbox.width * scale_factor
        box_height = bbox.height * scale_factor
        ink_bottom = signature_ink_bottom(line, layout, placement.field_id)
        image = ImageReader(io.BytesIO(placement.image_bytes))

        metrics = _ink_metrics(placement.image_bytes)
        if metrics is None:
            # Unreadable by Pillow: fit the whole image, its bottom on the target.
            rect = BBox(left, ink_bottom - box_height, box_width, box_height)
            x, y, w, h = rotate_for_page(rect, *unrotated, rotation).to_reportlab(page_height)
            pdf_canvas.drawImage(
                image, x, y, width=w, height=h, preserveAspectRatio=True, anchor="sw", mask="auto"
            )
            return

        pixel_width, pixel_height, _, _, _, ink_bottom_px = metrics
        # Contain: both axes scaled by the SAME factor, the smaller fit, so the
        # drawing never exceeds the box in either direction and never stretches.
        scale = min(box_width / pixel_width, box_height / pixel_height)
        draw_width = pixel_width * scale
        draw_height = pixel_height * scale
        # Transparent padding below the strokes (none for a B2-cropped image)
        # hangs below the target so the INK bottom lands on it.
        below_ink = (pixel_height - ink_bottom_px) * scale
        rect = BBox(left, ink_bottom + below_ink - draw_height, draw_width, draw_height)
        x, y, w, h = rotate_for_page(rect, *unrotated, rotation).to_reportlab(page_height)
        pdf_canvas.drawImage(
            image, x, y, width=w, height=h, preserveAspectRatio=True, anchor="sw", mask="auto"
        )

    def _draw_date(
        self,
        pdf_canvas,
        placement: DatePlacement,
        line: float,
        layout: Layout,
        unrotated: tuple[float, float],
        rotation: int,
        page_height: float,
    ) -> None:
        try:
            text = format_date(placement.value)
        except ValueError:
            text = str(placement.value)
        baseline = date_baseline(line, layout, placement.field_id)
        size = float(layout.date_font_size)
        # A zero-height anchor box at the baseline, so the rotation helper maps
        # the point exactly as it maps a signature rectangle.
        left = placement.bbox.x + layout.slot_offset_x(placement.field_id)
        anchor = BBox(left, baseline - size, placement.bbox.width, size)
        x, y, _, _ = rotate_for_page(anchor, *unrotated, rotation).to_reportlab(page_height)
        # v6: family + bold/italic -> face; with neither it is the family itself,
        # i.e. exactly the v5 `setFont` argument.
        font = ensure_font(layout.date_face)
        pdf_canvas.setFont(font, size)
        pdf_canvas.setFillColorRGB(0, 0, 0)
        pdf_canvas.drawString(x, y, text)

    # -- free annotations (contract v4) --------------------------------------

    def _draw_annotation(
        self,
        pdf_canvas,
        note: AnnotationPlacement,
        unrotated: tuple[float, float],
        rotation: int,
        page_height: float,
    ) -> None:
        if note.kind in ANNOTATION_TEXT_KINDS:
            self._draw_annotation_text(pdf_canvas, note, unrotated, rotation, page_height)
            return
        if note.kind in ANNOTATION_MARK_KINDS:
            self._draw_annotation_mark(pdf_canvas, note, unrotated, rotation, page_height)
            return
        drawer = {
            "highlight": self._draw_markup,
            "underline": self._draw_markup,
            "strikeout": self._draw_markup,
            "ink": self._draw_ink,
            "rect": self._draw_shape,
            "ellipse": self._draw_shape,
            "line": self._draw_line,
            "arrow": self._draw_line,
            "image": self._draw_image,
            "whiteout": self._draw_whiteout,
        }.get(note.kind)
        if drawer is None:  # pragma: no cover - the schema is a closed enum
            raise ValueError(f"Unsupported annotation kind: {note.kind!r}")
        mapper = _Mapper(unrotated, rotation, page_height)
        # Every v5 drawer changes colour/alpha/line state; the overlay canvas is
        # shared with every other mark on the page, so each one is bracketed.
        pdf_canvas.saveState()
        try:
            drawer(pdf_canvas, note, mapper)
        finally:
            pdf_canvas.restoreState()

    # -- contract v5 drawers. Geometry is canonical (points, top-left origin);
    # `mapper` turns it into overlay-canvas coordinates exactly as the v1-v4
    # marks are mapped (rotate_for_page + to_reportlab). A stroke is CENTRED on
    # its geometry, as in SVG / PDF: a rect's ink reaches stroke/2 beyond its
    # box, a line's ink is stroke wide around the segment.

    def _draw_markup(self, pdf_canvas, note: AnnotationPlacement, mapper: "_Mapper") -> None:
        """highlight: each rect filled, alpha HIGHLIGHT_ALPHA.
        underline: a bar of thickness t along the BOTTOM of each rect (its
        lower edge = the rect's lower edge). strikeout: the same bar centred on
        the rect's vertical middle. t = max(0.75, h * 0.08) pt."""
        red, green, blue = note.rgb()
        pdf_canvas.setFillColorRGB(red, green, blue)
        if note.kind == "highlight":
            pdf_canvas.setFillAlpha(HIGHLIGHT_ALPHA)
        for x, y, w, h in note.rects:
            if note.kind == "highlight":
                box = (x, y, w, h)
            else:
                thickness = markup_line_thickness(h)
                top = y + h - thickness if note.kind == "underline" else y + (h - thickness) / 2.0
                box = (x, top, w, thickness)
            rx, ry, rw, rh = mapper.box(*box)
            pdf_canvas.rect(rx, ry, rw, rh, stroke=0, fill=1)

    def _draw_ink(self, pdf_canvas, note: AnnotationPlacement, mapper: "_Mapper") -> None:
        pdf_canvas.setStrokeColorRGB(*note.rgb())
        pdf_canvas.setLineWidth(float(note.stroke_pt))
        pdf_canvas.setLineCap(1)
        pdf_canvas.setLineJoin(1)
        path = pdf_canvas.beginPath()
        for points in note.paths:
            (first_x, first_y), *rest = points
            path.moveTo(*mapper.point(first_x, first_y))
            for x, y in rest:
                path.lineTo(*mapper.point(x, y))
        pdf_canvas.drawPath(path, stroke=1, fill=0)

    def _draw_shape(self, pdf_canvas, note: AnnotationPlacement, mapper: "_Mapper") -> None:
        red, green, blue = note.rgb()
        pdf_canvas.setStrokeColorRGB(red, green, blue)
        pdf_canvas.setLineWidth(float(note.stroke_pt))
        pdf_canvas.setLineJoin(0)  # miter: square corners, as SVG draws a rect
        fill = 1 if note.fill else 0
        if fill:
            pdf_canvas.setFillColorRGB(red, green, blue)
            pdf_canvas.setFillAlpha(SHAPE_FILL_ALPHA)
        x, y, w, h = mapper.box(note.x_pt, note.y_pt, note.w_pt, note.h_pt)
        if note.kind == "rect":
            pdf_canvas.rect(x, y, w, h, stroke=1, fill=fill)
        else:
            pdf_canvas.ellipse(x, y, x + w, y + h, stroke=1, fill=fill)

    def _draw_line(self, pdf_canvas, note: AnnotationPlacement, mapper: "_Mapper") -> None:
        """Butt caps (SVG's default), so the ink ends exactly at the endpoints.
        An arrow's shaft stops at the base of its head; the head is a filled
        triangle whose tip is exactly (x2, y2)."""
        red, green, blue = note.rgb()
        stroke = float(note.stroke_pt)
        pdf_canvas.setStrokeColorRGB(red, green, blue)
        pdf_canvas.setFillColorRGB(red, green, blue)
        pdf_canvas.setLineWidth(stroke)
        pdf_canvas.setLineCap(0)
        x1, y1, x2, y2 = note.x_pt, note.y_pt, note.x2_pt, note.y2_pt
        if note.kind == "line":
            pdf_canvas.line(*mapper.point(x1, y1), *mapper.point(x2, y2))
            return
        (base_x, base_y), left, right = arrow_head(x1, y1, x2, y2, stroke)
        if (base_x, base_y) != (x1, y1):
            pdf_canvas.line(*mapper.point(x1, y1), *mapper.point(base_x, base_y))
        head = pdf_canvas.beginPath()
        head.moveTo(*mapper.point(x2, y2))
        head.lineTo(*mapper.point(*left))
        head.lineTo(*mapper.point(*right))
        head.close()
        pdf_canvas.drawPath(head, stroke=0, fill=1)

    def _draw_image(self, pdf_canvas, note: AnnotationPlacement, mapper: "_Mapper") -> None:
        """The stored asset fills the box exactly (no aspect correction: the box
        IS what the user sized). Alpha is kept, so a stamp on a transparent
        background shows the document through it."""
        if not note.image_bytes:  # pragma: no cover - finalize resolves every asset first
            raise ValueError(f"Image annotation {note.id!r} has no image")
        x, y, w, h = mapper.box(note.x_pt, note.y_pt, note.w_pt, note.h_pt)
        pdf_canvas.drawImage(
            ImageReader(io.BytesIO(note.image_bytes)), x, y, width=w, height=h,
            preserveAspectRatio=False, mask="auto",
        )

    def _draw_whiteout(self, pdf_canvas, note: AnnotationPlacement, mapper: "_Mapper") -> None:
        """An opaque white box. VISUAL ONLY (see the module docstring): the
        original text stays in the page's content stream and is still found by
        copy/paste and search. This is NOT a redaction."""
        pdf_canvas.setFillColorRGB(1, 1, 1)
        x, y, w, h = mapper.box(note.x_pt, note.y_pt, note.w_pt, note.h_pt)
        pdf_canvas.rect(x, y, w, h, stroke=0, fill=1)

    def _draw_annotation_text(
        self,
        pdf_canvas,
        note: AnnotationPlacement,
        unrotated: tuple[float, float],
        rotation: int,
        page_height: float,
    ) -> None:
        """`(x_pt, y_pt)` is the left end of the BASELINE, exactly as for a date."""
        text = note.text or ""
        size = float(note.size_pt)
        # v6: family + bold/italic -> face (the family itself when neither).
        font = ensure_font(note.face)
        # A box whose lower edge is the baseline, so the rotation helper maps the
        # anchor the same way it maps a signature rectangle. Its width only
        # matters on a rotated page, where it becomes the mapped height.
        anchor = BBox(note.x_pt, note.y_pt - size, pdf_canvas.stringWidth(text, font, size), size)
        x, y, _, _ = rotate_for_page(anchor, *unrotated, rotation).to_reportlab(page_height)
        pdf_canvas.setFont(font, size)
        # `black` is exactly (0, 0, 0): a v4 body (no `color`) emits the v4 bytes.
        pdf_canvas.setFillColorRGB(*note.rgb())
        pdf_canvas.drawString(x, y, text)

    def _draw_annotation_mark(
        self,
        pdf_canvas,
        note: AnnotationPlacement,
        unrotated: tuple[float, float],
        rotation: int,
        page_height: float,
    ) -> None:
        """A tick or a cross, STROKED, centred on `(x_pt, y_pt)`.

        Not a font glyph: see the module docstring. The ink box comes out exactly
        `size_pt` square because the path reaches `(size - line_width) / 2` from
        the centre and the round caps add `line_width / 2` back at each end.
        """
        size = float(note.size_pt)
        half = size / 2.0
        square = BBox(note.x_pt - half, note.y_pt - half, size, size)
        x, y, width, height = rotate_for_page(square, *unrotated, rotation).to_reportlab(page_height)
        centre_x, centre_y = x + width / 2.0, y + height / 2.0
        line_width = size * MARK_LINE_WIDTH_RATIO
        reach = _mark_reach(size)

        pdf_canvas.saveState()
        try:
            pdf_canvas.setStrokeColorRGB(*note.rgb())
            pdf_canvas.setLineWidth(line_width)
            pdf_canvas.setLineCap(1)  # round: the cap is what completes the box
            pdf_canvas.setLineJoin(1)
            path = pdf_canvas.beginPath()
            if note.kind == "check":
                (first_x, first_y), *rest = MARK_CHECK_PATH
                path.moveTo(centre_x + first_x * reach, centre_y + first_y * reach)
                for point_x, point_y in rest:
                    path.lineTo(centre_x + point_x * reach, centre_y + point_y * reach)
            else:
                path.moveTo(centre_x - reach, centre_y - reach)
                path.lineTo(centre_x + reach, centre_y + reach)
                path.moveTo(centre_x - reach, centre_y + reach)
                path.lineTo(centre_x + reach, centre_y - reach)
            pdf_canvas.drawPath(path, stroke=1, fill=0)
        finally:
            # The overlay canvas is shared with every other mark on this page.
            pdf_canvas.restoreState()


def _mark_reach(size: float) -> float:
    """How far a tick/cross path runs from its centre, before the round cap."""
    return (size - size * MARK_LINE_WIDTH_RATIO) / 2.0


class _Mapper:
    """Canonical (points, top-left origin, displayed page) -> overlay canvas.

    The same two steps every v1-v4 mark takes (`rotate_for_page`, then
    `to_reportlab`), packaged for geometry that is a list of points."""

    def __init__(self, unrotated: tuple[float, float], rotation: int, page_height: float):
        self.unrotated = unrotated
        self.rotation = rotation
        self.page_height = page_height

    def box(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        mapped = rotate_for_page(BBox(x, y, w, h), *self.unrotated, self.rotation)
        return mapped.to_reportlab(self.page_height)

    def point(self, x: float, y: float) -> tuple[float, float]:
        mapped_x, mapped_y, _, _ = self.box(x, y, 0.0, 0.0)
        return mapped_x, mapped_y


def markup_line_thickness(rect_height: float) -> float:
    """Underline / strikeout bar thickness for a text rect this tall."""
    return max(MARKUP_LINE_MIN_PT, float(rect_height) * MARKUP_LINE_RATIO)


def arrow_head(
    x1: float, y1: float, x2: float, y2: float, stroke: float
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """(base, left corner, right corner) of the head of an arrow ending at
    (x2, y2), in canonical coordinates. The tip is (x2, y2) itself. Head length
    ARROW_HEAD_LENGTH_RATIO * stroke and half-width ARROW_HEAD_HALF_WIDTH_RATIO
    * stroke, both scaled down together when the line is shorter than the head
    (then the head starts at (x1, y1) and there is no shaft)."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    head_length = ARROW_HEAD_LENGTH_RATIO * stroke
    half_width = ARROW_HEAD_HALF_WIDTH_RATIO * stroke
    if head_length > length:
        half_width *= length / head_length
        head_length = length
    base = (x2 - ux * head_length, y2 - uy * head_length)
    if head_length == length:
        base = (x1, y1)
    left = (base[0] - uy * half_width, base[1] + ux * half_width)
    right = (base[0] + uy * half_width, base[1] - ux * half_width)
    return base, left, right


def _add_sticky_note(writer: PdfWriter, note: AnnotationPlacement) -> None:
    """A real PDF `/Text` annotation (ISO 32000-1 12.5.6.4): an icon on the page
    and the text in a popup, NOT drawn into the content stream.

    Built as a plain dictionary rather than through `pypdf.annotations.Text`:
    pypdf 5.1.0 writes the flags of that class under a `/Flags` key, which is
    not a PDF annotation key (the flags key is `/F`), so the Print flag would
    be lost. No `/AP` (appearance stream) is written: every viewer draws its
    own sticky-note icon for a `/Text` annotation -- pdfium (Chrome/Edge)
    generates one from `/Name` and `/C`, Acrobat and pdf.js use their built-in
    icons. `/F` = Print | NoZoom | NoRotate, as Acrobat writes it; Print is
    what puts the icon on paper.

    The note text is in `/Contents` only. It is not in the audit trail (see
    `_annotation_stats` in app/api/signatures.py).
    """
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    page = writer.pages[int(note.page) - 1]
    # NoRotate (which viewers apply to /Text annotations whether or not the
    # flag is set -- pdf.js hard-codes it) pins the icon by the UPPER-LEFT
    # corner of /Rect in default user space and draws it upright from there.
    # So that corner is the mapped (x_pt, y_pt); on an unrotated page this is
    # simply the 20 pt square below-right of the point.
    anchor_x, anchor_y = displayed_point_to_user_space(page, note.x_pt, note.y_pt)
    x, y, w, h = anchor_x, anchor_y - NOTE_ICON_PT, NOTE_ICON_PT, NOTE_ICON_PT
    red, green, blue = note.rgb()
    annotation = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Text"),
            NameObject("/Rect"): ArrayObject(
                [FloatObject(round(v, 4)) for v in (x, y, x + w, y + h)]
            ),
            NameObject("/Contents"): TextStringObject(note.text or ""),
            NameObject("/Name"): NameObject("/Comment"),
            NameObject("/Open"): BooleanObject(False),
            NameObject("/F"): NumberObject(NOTE_ANNOTATION_FLAGS),
            NameObject("/C"): ArrayObject(
                [FloatObject(round(v, 4)) for v in (red, green, blue)]
            ),
        }
    )
    writer.add_annotation(int(note.page) - 1, annotation)


def displayed_point_to_user_space(page, x_pt: float, y_pt: float) -> tuple[float, float]:
    """A canonical point (displayed page, top-left origin) -> the page's
    default user space (unrotated, bottom-left origin, mediabox offset)."""
    rotation = int(page.get("/Rotate") or 0) % 360
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    left, bottom = float(page.mediabox.left), float(page.mediabox.bottom)
    displayed_height = width if rotation in (90, 270) else height
    xd, yd = float(x_pt), displayed_height - float(y_pt)  # displayed, y up
    if rotation == 90:
        ux, uy = width - yd, xd
    elif rotation == 180:
        ux, uy = width - xd, height - yd
    elif rotation == 270:
        ux, uy = yd, height - xd
    else:
        return xd, yd  # the v1-v6 arithmetic, unchanged
    return ux + left, uy + bottom


def _write_metadata(writer, *, title, author, subject=None, keywords=None, owner_password=None) -> None:
    """The final PDF's Info dictionary (+ optional owner password). Shared by
    `build_final_contract(finish=True)` and `finish_pdf`."""
    metadata = {
        "/Title": title,
        "/Author": author,
        "/Creator": "Contract Signing App",
        "/Producer": "Contract Signing App v1.0",
        "/CreationDate": datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ"),
    }
    # Optional notes (e.g. a reviewer override) travel inside the signed
    # bytes, so they are covered by the file hash like everything else.
    if subject:
        metadata["/Subject"] = subject
    if keywords:
        metadata["/Keywords"] = keywords
    writer.add_metadata(metadata)

    if owner_password:
        # Readable by anyone holding the document, but not editable without
        # the owner password -- the signed artefact should not be alterable.
        writer.encrypt(user_password="", owner_password=owner_password)


def finish_pdf(
    pdf_bytes: bytes,
    *,
    title: str = "Signed Independent Contractor Agreement",
    author: str = "Cargo Freight Trucking Inc",
    subject: str | None = None,
    keywords: str | None = None,
    owner_password: str | None = None,
) -> BuildResult:
    """Metadata (+ encryption) on an unfinished build (`finish=False` + v7 markup)."""
    writer = PdfWriter(clone_from=io.BytesIO(pdf_bytes))
    _write_metadata(writer, title=title, author=author, subject=subject, keywords=keywords,
                    owner_password=owner_password)
    buffer = io.BytesIO()
    writer.write(buffer)
    data = buffer.getvalue()
    return BuildResult(pdf_bytes=data, sha256=hashlib.sha256(data).hexdigest(), page_count=len(writer.pages))


def annotation_geometry(note: AnnotationPlacement) -> list[tuple[float, float]]:
    """Every canonical point that has to lie on the page for `note`.

    text/date/check/cross/note: the anchor point only (a long text line may run
    off the edge, as an oversized signature may). Boxes (rect, ellipse, image,
    whiteout, each markup rect): all four corners, i.e. the whole box. line /
    arrow: both endpoints. ink: every point of every path."""
    if note.kind in ANNOTATION_BOX_KINDS:
        boxes = [(note.x_pt, note.y_pt, note.w_pt, note.h_pt)]
    elif note.kind in ANNOTATION_MARKUP_KINDS:
        boxes = list(note.rects)
    elif note.kind in ANNOTATION_LINE_KINDS:
        return [(note.x_pt, note.y_pt), (note.x2_pt, note.y2_pt)]
    elif note.kind == "ink":
        return [point for path in note.paths for point in path]
    else:
        return [(note.x_pt, note.y_pt)]
    return [
        corner
        for x, y, w, h in boxes
        for corner in ((x, y), (x + w, y + h))
    ]


def _displayed_size(page) -> tuple[float, float]:
    """(width, height) of a pypdf page AS DISPLAYED -- swapped when /Rotate is
    90 or 270. This is the space canonical coordinates live in, because the
    parser reads them from pdfplumber, which reports the rotated geometry."""
    box = page.mediabox
    width, height = float(box.width), float(box.height)
    rotation = int(page.get("/Rotate") or 0) % 360
    return (height, width) if rotation in (90, 270) else (width, height)


def _assert_annotations_on_page(
    annotations: list[AnnotationPlacement], page_sizes: list[tuple[float, float]]
) -> None:
    """Refuse an annotation the document cannot carry.

    Both cases are a bad request rather than a silent drop: the user put the
    element there deliberately, and a mark that vanishes from a signed contract
    without anyone being told is the failure mode this whole module exists to
    avoid. What has to be on the page is `annotation_geometry`: the ANCHOR POINT
    of a text (a long line may run off the right edge exactly as an oversized
    signature already may), and the whole geometry of every v5 shape. A
    stroke's own width is not counted: a 12 pt border on a box that touches the
    edge is clipped by the viewer, like any other ink past the mediabox.
    """
    page_count = len(page_sizes)
    stray = [
        {"id": note.id, "kind": note.kind, "page": int(note.page)}
        for note in annotations
        if not 1 <= int(note.page) <= page_count
    ]
    if stray:
        raise AnnotationPlacementError(
            AnnotationPlacementError.PAGE_OUT_OF_RANGE, stray, page_count
        )
    outside = []
    for note in annotations:
        width, height = page_sizes[int(note.page) - 1]
        if not all(
            0.0 <= float(x) <= width and 0.0 <= float(y) <= height
            for x, y in annotation_geometry(note)
        ):
            outside.append(
                {
                    "id": note.id,
                    "kind": note.kind,
                    "page": int(note.page),
                    "page_width_pt": round(width, 3),
                    "page_height_pt": round(height, 3),
                }
            )
    if outside:
        raise AnnotationPlacementError(
            AnnotationPlacementError.OUTSIDE_PAGE, outside, page_count
        )


def _line(placement, page_number: int, rules: RuleLocator) -> float:
    """`rule_y`, else the box's own lower edge (contract v1 `line`)."""
    rule = placement.rule_y
    if rule is _UNSET:
        rule = rules.locate(page_number, placement.bbox)
    return float(rule) if rule is not None else placement.bbox.y1


def _ink_rows(counts: list[int], min_count: int, strong_count: int) -> list[bool]:
    """Rows (or columns) that are ink: dense enough, and either part of a run of
    at least INK_MIN_RUN such rows or strong on their own. Isolated specks --
    the thing that stretched `getbbox()` -- fail both tests."""
    dense = [c >= min_count for c in counts]
    keep = [False] * len(counts)
    index = 0
    while index < len(counts):
        if not dense[index]:
            index += 1
            continue
        start = index
        while index < len(counts) and dense[index]:
            index += 1
        run = range(start, index)
        if index - start >= INK_MIN_RUN or any(counts[i] >= strong_count for i in run):
            for i in run:
                keep[i] = True
    return keep


def _projected_box(mask: Image.Image) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the ink in a 0/255 mask, noise-filtered."""
    width, height = mask.size
    data = mask.tobytes()
    rows = [data[r * width:(r + 1) * width].count(255) for r in range(height)]
    if not any(rows):
        return None
    # Column counts via a transpose keeps it in C instead of a Python pixel loop.
    tdata = mask.transpose(Image.Transpose.TRANSPOSE).tobytes()
    cols = [tdata[c * height:(c + 1) * height].count(255) for c in range(width)]

    row_keep = _ink_rows(
        rows,
        max(1, int(width * INK_MIN_LINE_FRACTION)),
        max(1, int(width * INK_STRONG_LINE_FRACTION)),
    )
    col_keep = _ink_rows(
        cols,
        max(1, int(height * INK_MIN_LINE_FRACTION)),
        max(1, int(height * INK_STRONG_LINE_FRACTION)),
    )
    if not any(row_keep) or not any(col_keep):
        return None
    top = row_keep.index(True)
    bottom = len(row_keep) - row_keep[::-1].index(True)
    left = col_keep.index(True)
    right = len(col_keep) - col_keep[::-1].index(True)
    return (left, top, right, bottom)


def _ink_metrics(image_bytes: bytes) -> tuple[int, int, int, int, int, int] | None:
    """(width, height, ink_left, ink_top, ink_right, ink_bottom) in pixels.

    Alpha decides what is ink (or luma, for a flattened capture on white). The
    box comes from row/column projections with a minimum pixel count per row
    and a minimum run depth, so a lone speck of background noise can no longer
    pull the ink bottom away from the strokes. Falls back to the whole image
    when nothing qualifies, so the result is never empty.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            width, height = image.size
            if width <= 0 or height <= 0:
                return None
            whole = (0, 0, width, height)
            transparent = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
            rgba = image.convert("RGBA")
            box = None
            if transparent:
                alpha = rgba.getchannel("A")
                if alpha.getextrema()[0] < INK_ALPHA_THRESHOLD:
                    box = _projected_box(
                        alpha.point(lambda v: 255 if v >= INK_ALPHA_THRESHOLD else 0)
                    )
            if box is None:
                # No usable alpha: a flattened capture puts strokes on white.
                # Transparent pixels are flattened to white so they are not ink.
                flat = Image.alpha_composite(Image.new("RGBA", image.size, (255, 255, 255, 255)), rgba)
                box = _projected_box(
                    flat.convert("L").point(lambda v: 255 if v < INK_LUMA_THRESHOLD else 0)
                )
            if box is None:
                box = whole
            return (width, height, box[0], box[1], box[2], box[3])
    except Exception:
        logger.exception("Could not measure the signature image")
        return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
