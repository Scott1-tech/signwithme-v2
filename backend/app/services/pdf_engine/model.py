"""Engine-side data model: pages and the server text model.

Geometry everywhere: PDF points, TOP-LEFT origin, UNROTATED page space,
relative to the CropBox's top-left corner -- the same system as
`signatures.bbox`, annotations v5 and `lib/layout.ts` (EDITOR-V2-PLAN §0.2).
PyMuPDF's `rawdict` already reports exactly that (spike §10.2.1 [S3]: no
derotation), so the adapter hands coordinates through unchanged.

Ids are positional and deterministic for a given file (B1.1, `lines.assemble`):
MuPDF's raw lines on one baseline are joined into logical lines, blocks are
numbered in reading order -- block `b<i>` is the i-th block by its first
line's baseline, then left edge; line `b<i>l<j>` its j-th line top to bottom.
The same version bytes always give the same ids; a new version gets a new text
model (the client re-reads it by version hash), and an edit that leaves the
other blocks' geometry alone leaves their ids alone.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


# Zero-width / invisible FORMAT characters (2026-09-30, `_staging\zw`): no ink,
# no advance a reader shows, and no direction of their own. Every one is
# Unicode category Cf with bidi class BN ("boundary neutral") and in
# Default_Ignorable_Code_Point:
#   U+00AD soft hyphen, U+200B zero width space, U+200C ZWNJ, U+200D ZWJ,
#   U+2060 word joiner, U+FEFF zero width no-break space (BOM).
# Docusign/Chrome packages put U+200B around most text objects (the real
# template: 4 975 of them on 979 of 1 915 lines, 51 %), so a line's text as
# the user sees it is its text WITHOUT them. They are NOT drawn in new text
# and never counted when an edit's `expect_text` is compared. Every other
# Cf character keeps being refused (`app.services.fonts.embedded_refusal`):
# the bidi controls (U+200E/F LRM/RLM, U+202A-E embeddings/overrides incl.
# U+202E RLO, U+2066-9 isolates) change how text READS, tag characters
# (U+E0000..) can smuggle hidden text -- dropping those silently is not
# "ignoring nothing".
IGNORABLE: frozenset[str] = frozenset("­​‌‍⁠﻿")
# A run of ignorable glyphs between two visible glyphs reads as a word space
# when the pen moved at least this x size across it (= `lines.SPACE_GAP`).
VIEW_SPACE_GAP = 0.15


def strip_ignorable(text: str) -> str:
    return "".join(char for char in (text or "") if char not in IGNORABLE)


def match_key(text: str) -> str:
    """How two texts are compared (`expect_text`, B1.1 pieces): NFC, without
    the ignorable characters."""
    return nfc(strip_ignorable(text))


@dataclass(frozen=True)
class ViewChar:
    """One character of a line as the user sees it (`Line.view`).

    `raw` indexes `Line.chars`: the glyph itself, plus the ignorable glyphs
    that travel with it (leading ones of the line go with its first visible
    character, any other run with the character before it). `own` is the
    index of the glyph that IS this character, or None for a space that
    stands for a run of ignorable glyphs between two words."""

    c: str
    raw: tuple[int, ...]
    own: int | None


@dataclass
class Char:
    c: str
    bbox: Rect
    origin: tuple[float, float]


@dataclass
class Span:
    font: str
    size: float
    flags: int
    color: int  # 0xRRGGBB
    ascender: float
    descender: float
    chars: list[Char]
    bbox: Rect
    # True for the one-char space span `lines.assemble` puts between two raw
    # lines it joined (no glyph of the PDF behind it).
    synthetic: bool = False

    @property
    def bold(self) -> bool:
        return bool(self.flags & 16) or "bold" in self.font.lower()

    @property
    def italic(self) -> bool:
        name = self.font.lower()
        return bool(self.flags & 2) or "italic" in name or "oblique" in name


@dataclass
class Line:
    id: str
    bbox: Rect
    direction: tuple[float, float]
    spans: list[Span]

    @property
    def chars(self) -> list[tuple[Span, Char]]:
        return [(span, char) for span in self.spans for char in span.chars]

    @property
    def text(self) -> str:
        return "".join(char.c for span in self.spans for char in span.chars)

    @property
    def baseline(self) -> float:
        for span in self.spans:
            for char in span.chars:
                return float(char.origin[1])
        return float(self.bbox[3])

    @property
    def view(self) -> list[ViewChar]:
        return line_view(self.chars)

    @property
    def display_text(self) -> str:
        """The text the user sees and edits (`IGNORABLE` glyphs left out; a
        run of them that stands between two words reads as one space)."""
        return "".join(char.c for char in self.view)


def line_view(pairs: list[tuple[Span, Char]]) -> list[ViewChar]:
    """`Line.view` of a line's (span, char) pairs. Pure; the pairs (and so
    every glyph's geometry) are left as they are -- the view only says which
    glyphs the user's characters stand for."""
    out: list[ViewChar] = []
    pending: list[int] = []  # ignorable glyphs before the first visible one
    count = len(pairs)
    index = 0
    while index < count:
        if pairs[index][1].c not in IGNORABLE:
            out.append(ViewChar(pairs[index][1].c, tuple(pending) + (index,), index))
            pending = []
            index += 1
            continue
        end = index
        while end < count and pairs[end][1].c in IGNORABLE:
            end += 1
        run = tuple(range(index, end))
        index = end
        if not out:
            pending.extend(run)
            continue
        before = out[-1]
        if end < count and before.own is not None and not before.c.isspace() and not pairs[end][1].c.isspace():
            left_span, left = pairs[before.own]
            right_span, right = pairs[end]
            size = min(left_span.size, right_span.size)
            if size > 0 and right.bbox[0] - left.bbox[2] > VIEW_SPACE_GAP * size:
                out.append(ViewChar(" ", run, None))
                continue
        out[-1] = ViewChar(before.c, before.raw + run, before.own)
    # A line of ignorable glyphs only has an empty view (its glyphs are still
    # the line's: a whole-line edit removes them).
    return out


@dataclass
class Block:
    id: str
    bbox: Rect
    lines: list[Line]
    editable: bool = True
    reason: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass
class PageText:
    width: float
    height: float
    rotation: int
    blocks: list[Block]
    has_text: bool
    scanned: bool
    font_types: dict[str, str] = field(default_factory=dict)

    def block(self, block_id: str) -> Block | None:
        for block in self.blocks:
            if block.id == block_id:
                return block
        return None

    def all_lines(self) -> list[tuple[Block, Line]]:
        return [(block, line) for block in self.blocks for line in block.lines]


@dataclass
class PageInfo:
    """One page as `document_pages` stores it (identity-free: no uid here)."""

    position: int  # 1-based
    rotation: int
    width_pt: float  # unrotated CropBox
    height_pt: float
    has_text: bool
    scanned: bool


@dataclass
class PageRef:
    page_uid: str
    position: int  # 1-based position in the version the ops apply to


@dataclass
class EngineResult:
    pdf_bytes: bytes
    sha256: str
    page_count: int
    pages: list[PageInfo]
    changed_regions: list[dict]  # [{page_uid, rect: {x_pt,y_pt,w_pt,h_pt}}]
    warnings: list[dict]
    substituted_from: list[dict]  # [{op_id, family, original_font}] (font NAME only)
    # B2: page uid of every page of the result, in order.
    page_uids: list[str] = field(default_factory=list)
    redactions: list[dict] = field(default_factory=list)  # [{page_uid, rect}]
    images_removed: int = 0
    pages_plan: object | None = None
    # B3: counts of the field ops carried out (no names, no values), or None.
    fields: dict | None = None


def rect_dict(rect: Rect) -> dict:
    x0, y0, x1, y1 = rect
    return {
        "x_pt": round(float(x0), 3),
        "y_pt": round(float(y0), 3),
        "w_pt": round(float(x1 - x0), 3),
        "h_pt": round(float(y1 - y0), 3),
    }


def union(rects: list[Rect]) -> Rect:
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def intersects(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
