"""`text.replace` / `text.delete` planning (EDITOR-V2-PLAN §4, spike §10.2.1).

Planning is pure: it reads a `PageText` and produces what the adapter must do
-- redaction rectangles, text fragments to draw, the changed region, warnings.
Nothing is written until every op of the page has planned successfully, so a
refused op never leaves a half-edited page behind.

Removal. Only the TARGET glyphs are removed, each through a small "core"
rectangle (inner 60 % of its width; vertically baseline-0.55*size ..
baseline-0.15*size). MuPDF removes every glyph whose hit box (its font box
shrunk to 80 %, `hit_box`) touches a redaction rectangle, and a filled-in
value typed 3 pt above a `____` fill line shares that height with the
underscores -- the core band alone would take the underscores with it. So
the band is moved, per glyph, into a part of the glyph's own hit box that no
other glyph's hit box covers; only when no such part
exists is the neighbour unavoidably removed, and then it is drawn again
(`collateral`, reported as warning `neighbor_glyphs_redrawn`).

No white box is ever painted (redaction `fill=False`, images/line art NONE).

New text is set in a metric-compatible Liberation face (see `fonts`) from the
ORIGINAL first baseline and left edge. A single-line edit that keeps a common
prefix in the same style rewrites only the TAIL of the line from the first
changed character (spike: rewriting less would overprint). Wrap/shrink/overflow
are decided BEFORE anything is drawn (the spike's `insert_textbox` dry-run
rule, applied to our own layout: text is either placed whole or not at all).
Neighbouring text is never moved; a new glyph band crossing another glyph is a
collision -> 422 `text_overflow` (or a warning with `fit="overflow"`).

QA 2026-09-30: a tail fragment carries an `anchor` (its last kept glyph) so the
adapter splices it into the content right after that glyph (reading order,
`content_splice`). A box whose top-left moved (✥ Move) rewrites the WHOLE block
at the new place even when the text is unchanged; a move is carried out or
refused (422 `op_outside_page` / `text_overflow` / `text_move_not_applied`),
never dropped.

2026-09-30 (zero-width characters, removal check):
* Zero-width format characters (`model.IGNORABLE`, U+200B & co. -- on 51 % of
  the real template's lines) are what the user does not see: they are left
  out of new text (never drawn, never glyph-checked), `expect_text` is
  compared without them (`model.match_key`) and a line's editable text is its
  `Line.view` (the glyphs behind each visible character are still removed).
* Removal rectangles are chosen against MuPDF's REAL hit box of a glyph
  (`hit_box`): the rawdict box shrunk to 80 %. The old free band could sit in
  the part of the rawdict box MuPDF ignores, and the glyph silently stayed
  (real template p.2: a value over a `____` line). `apply.execute_plan`
  checks after the redaction that every target glyph is gone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace

from app.services.pdf_engine import adapter, errors
from app.services.pdf_engine import fonts as engine_fonts
from app.services.pdf_engine.model import (
    IGNORABLE,
    Char,
    Line,
    PageText,
    Rect,
    Span,
    ViewChar,
    intersects,
    match_key,
    nfc,
    strip_ignorable,
    union,
)
from app.services.pdf_engine.ops import Run, TextOp

CORE_X = 0.2
BAND_TOP = 0.55
BAND_BOTTOM = 0.15
MIN_BAND_PT = 0.3
FIT_TOLERANCE_PT = 1.0
SHRINK_STEP_PT = 0.5
SHRINK_MIN_PT = 6.0
DEFAULT_LINE_HEIGHT = 1.2
# The op's box top-left further than this from the target's -> a move (the
# client echoes the text model's bbox rounded to 3 decimals).
MOVE_TOLERANCE_PT = 0.05
# Fill-line characters are meant to be written over: never a collision.
WRITE_OVER = frozenset("_‗‾")
_TOKEN = re.compile(r"\n|[^\S\n]+|\S+")


@dataclass
class Resolved:
    op: TextOp
    block_id: str
    lines: list[Line]

    @property
    def bbox(self) -> Rect:
        return union([line.bbox for line in self.lines])

    @property
    def chars(self) -> list[tuple[Span, Char]]:
        return [pair for line in self.lines for pair in line.chars]


@dataclass
class PagePlan:
    removal: list[tuple[Span, Char]] = field(default_factory=list)
    fragments: list[dict] = field(default_factory=list)
    changed_regions: list[Rect] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    substituted_from: list[dict] = field(default_factory=list)
    # id(char) of a removal glyph -> the op that removes it (for the 422 of
    # `apply.execute_plan` when a glyph is still there after the redaction).
    owners: dict[int, str] = field(default_factory=dict)


# -- target resolution --------------------------------------------------------------


def _match_lines(lines: list[Line], expect: str) -> bool:
    """`expect_text` against the lines, ignorable characters left out on both
    sides: the text the user saw (`display_text`, where a U+200B run between
    two words reads as a space) or the raw glyph text (a client that echoed
    the old wire text)."""
    key = match_key(expect)
    return key == match_key("\n".join(line.display_text for line in lines)) or key == match_key(
        "\n".join(line.text for line in lines)
    )


def resolve_target(page: PageText, op: TextOp) -> Resolved:
    """The lines an op edits, verified against `expect_text` (NFC).

    First by `block_id` + `line_ids`; if those no longer hold the expected text
    the target `bbox` is the fallback locator (same number of consecutive lines
    of one block, overlapping the bbox, with the expected text). Otherwise the
    client edited a document that has changed: 409 `stale_target`.
    """
    block = page.block(op.block_id)
    if block is not None:
        by_id = {line.id: line for line in block.lines}
        lines = [by_id.get(line_id) for line_id in op.line_ids]
        if all(line is not None for line in lines) and _match_lines(lines, op.expect_text):
            return _checked(Resolved(op, block.id, lines), block.editable, block.reason)
    count = len(op.line_ids)
    for candidate in page.blocks:
        for start in range(0, len(candidate.lines) - count + 1):
            window = candidate.lines[start : start + count]
            if not all(intersects(line.bbox, op.bbox) for line in window):
                continue
            if _match_lines(window, op.expect_text):
                return _checked(Resolved(op, candidate.id, window), candidate.editable, candidate.reason)
    if count == 1:
        piece = _piece_of_line(page, op)
        if piece is not None:
            return piece
    raise errors.EngineError(
        errors.STALE_TARGET,
        "The text this edit was made on is no longer in the document",
        {"op_id": op.id},
    )


PIECE_EDGE_TOL_PT = 1.5


def _piece_of_line(page: PageText, op: TextOp) -> Resolved | None:
    """An op made on a PIECE of what is now one logical line (B1.1).

    Before B1.1 MuPDF's raw lines were the model's lines, so an op saved in a
    draft may target `'Carrier: '` alone while the model now has the joined
    `'Carrier: Premier ...'`. Such an op still resolves -- to exactly the
    characters it was made on -- when the expected text occurs in one line that
    overlaps the target bbox AND those characters span the bbox's left and right
    edges (±`PIECE_EDGE_TOL_PT`). Anything looser stays `stale_target`."""
    expect = match_key(op.expect_text)
    if not expect or "\n" in expect:
        return None
    hits: list[tuple[str, Line, bool, str | None]] = []
    for block in page.blocks:
        for line in block.lines:
            view = line.view
            text = "".join(char.c for char in view)
            if nfc(text) != text or not intersects(line.bbox, op.bbox) or expect not in text:
                continue
            pairs = line.chars
            start = text.find(expect)
            while start >= 0:
                picked = view[start : start + len(expect)]
                chosen = [pairs[i] for i in sorted({i for char in picked for i in char.raw})]
                # The piece's edges with the ignorable glyphs that travel with
                # it (a raw MuPDF line of a Docusign package starts and ends
                # with one) or without them.
                edges = [
                    (min(c.bbox[0] for _s, c in group), max(c.bbox[2] for _s, c in group))
                    for group in (chosen, [pairs[char.own] for char in picked if char.own is not None])
                    if group
                ]
                if any(abs(x0 - op.bbox[0]) <= PIECE_EDGE_TOL_PT and abs(x1 - op.bbox[2]) <= PIECE_EDGE_TOL_PT
                       for x0, x1 in edges):
                    hits.append((block.id, _sub_line(line, chosen), block.editable, block.reason))
                start = text.find(expect, start + 1)
    if len(hits) != 1:
        return None
    block_id, line, editable, reason = hits[0]
    return _checked(Resolved(op, block_id, [line]), editable, reason)


def _sub_line(line: Line, chosen: list[tuple[Span, Char]]) -> Line:
    """The chosen characters of `line` as a line of their own (same id, same
    Char objects -- the rest of the line stays "other" text)."""
    spans: list[Span] = []
    for span, char in chosen:
        if spans and spans[-1].font == span.font and spans[-1].size == span.size and spans[-1].color == span.color \
                and spans[-1].flags == span.flags and spans[-1].synthetic == span.synthetic:
            spans[-1].chars.append(char)
        else:
            spans.append(Span(span.font, span.size, span.flags, span.color, span.ascender, span.descender,
                              [char], span.bbox, span.synthetic))
    for span in spans:
        span.bbox = union([char.bbox for char in span.chars])
    return Line(id=line.id, bbox=union([char.bbox for _span, char in chosen]), direction=line.direction, spans=spans)


def _checked(resolved: Resolved, editable: bool, reason: str | None) -> Resolved:
    if not editable:
        raise errors.EngineError(
            errors.UNSUPPORTED_SPAN,
            "This text cannot be edited in place",
            {"op_id": resolved.op.id, "reason": reason},
        )
    return resolved


# -- removal -------------------------------------------------------------------------


def core_rect(span: Span, char: Char) -> Rect:
    x0, _y0, x1, _y1 = char.bbox
    inset = max(x1 - x0, 0.0) * CORE_X
    baseline = char.origin[1]
    left, right = x0 + inset, x1 - inset
    if right - left < 0.2:
        middle = (x0 + x1) / 2
        left, right = middle - 0.1, middle + 0.1
    return (left, baseline - BAND_TOP * span.size, right, baseline - BAND_BOTTOM * span.size)


# MuPDF's text redaction takes a glyph when a redaction rectangle meets the
# glyph's ascender/descender box SHRUNK to 80 % about its centre, on both axes
# -- not the whole box (`Char.bbox`, rawdict). Measured 2026-09-30 on the real
# template (ArialMT 8/9 pt, Times New Roman regular/bold 11/12/14 pt: ratio
# 0.788-0.809 at 0.1 pt steps). A band chosen in the outer 10 % of the rawdict
# box removes nothing (p.2: a 9 pt value 3 pt above a 12 pt `____` line).
HIT_SHRINK = 0.8


def hit_box(char: Char) -> Rect:
    x0, y0, x1, y1 = char.bbox
    dx = max(x1 - x0, 0.0) * (1.0 - HIT_SHRINK) / 2.0
    dy = max(y1 - y0, 0.0) * (1.0 - HIT_SHRINK) / 2.0
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def _free_band(box: Rect, x_range: tuple[float, float], blockers: list[Rect]) -> tuple[float, float] | None:
    """The longest vertical interval of `box` (the target glyph's hit box)
    that no blocker box covers (within `x_range`), or None."""
    top, bottom = box[1] + 0.05, box[3] - 0.05
    covered = sorted(
        (max(top, b[1]), min(bottom, b[3]))
        for b in blockers
        if b[0] < x_range[1] and x_range[0] < b[2] and b[1] < bottom and top < b[3]
    )
    best: tuple[float, float] | None = None
    cursor = top
    for start, end in covered + [(bottom, bottom)]:
        if start - cursor >= MIN_BAND_PT and (best is None or start - cursor > best[1] - best[0]):
            best = (cursor, start)
        cursor = max(cursor, end)
    if best is None:
        return None
    return (best[0] + 0.02, best[1] - 0.02)


class _Grid:
    """Other glyphs bucketed by 20 pt rows, for neighbour lookups."""

    ROW = 20.0

    def __init__(self, pairs: list[tuple[Span, Char]]):
        self.rows: dict[int, list[tuple[Span, Char]]] = {}
        for pair in pairs:
            _span, char = pair
            for row in range(int(char.bbox[1] // self.ROW), int(char.bbox[3] // self.ROW) + 1):
                self.rows.setdefault(row, []).append(pair)

    def near(self, rect: Rect) -> list[tuple[Span, Char]]:
        found: dict[int, tuple[Span, Char]] = {}
        for row in range(int(rect[1] // self.ROW), int(rect[3] // self.ROW) + 1):
            for pair in self.rows.get(row, ()):
                found[id(pair[1])] = pair
        return list(found.values())


def removal_rects(
    targets: list[tuple[Span, Char]], others: list[tuple[Span, Char]]
) -> tuple[list[Rect], list[tuple[Span, Char]]]:
    """Redaction rectangles for `targets`, and the other glyphs they will
    unavoidably take with them (to be drawn again). Both sides are judged by
    MuPDF's hit box (`hit_box`), the rectangle always lies inside the
    target's own hit box."""
    grid = _Grid(others)
    rects: list[Rect] = []
    collateral: dict[int, tuple[Span, Char]] = {}
    for span, char in targets:
        rect = core_rect(span, char)
        near = grid.near(char.bbox)
        neighbours = [pair for pair in near if intersects(hit_box(pair[1]), rect)]
        if neighbours:
            band = _free_band(hit_box(char), (rect[0], rect[2]), [hit_box(pair[1]) for pair in near])
            if band is not None:
                rect = (rect[0], band[0], rect[2], band[1])
                neighbours = [pair for pair in neighbours if intersects(hit_box(pair[1]), rect)]
        for pair in neighbours:
            collateral[id(pair[1])] = pair
        rects.append(rect)
    return rects, list(collateral.values())


# -- layout ------------------------------------------------------------------------------


@dataclass
class Piece:
    text: str
    run: Run
    face: str
    size: float
    width: float
    space: bool


def _face(run: Run) -> str:
    return engine_fonts.face_for(run.family, run.bold, run.italic)


def _measure(run: Run, text: str, size: float) -> float:
    return adapter.text_length(engine_fonts.font_path(_face(run)), text, size)


def _split_long(piece: Piece, width: float, scale: float) -> list[Piece]:
    parts: list[Piece] = []
    current = ""
    for ch in piece.text:
        trial = current + ch
        if current and _measure(piece.run, trial, piece.size) > width:
            parts.append(Piece(current, piece.run, piece.face, piece.size, _measure(piece.run, current, piece.size), False))
            current = ch
        else:
            current = trial
    if current:
        parts.append(Piece(current, piece.run, piece.face, piece.size, _measure(piece.run, current, piece.size), False))
    return parts


def layout_lines(runs: tuple[Run, ...], width: float, scale: float) -> list[list[Piece]]:
    """Greedy word wrap of styled runs into lines no wider than `width`."""
    lines: list[list[Piece]] = [[]]
    line_width = 0.0

    def trim(line: list[Piece]) -> None:
        while line and line[-1].space:
            line.pop()

    for run in runs:
        size = run.size * scale
        face = _face(run)
        for match in _TOKEN.finditer(run.text):
            token = match.group(0)
            if token == "\n":
                trim(lines[-1])
                lines.append([])
                line_width = 0.0
                continue
            space = token.isspace()
            piece = Piece(token, run, face, size, _measure(run, token, size), space)
            if space:
                if lines[-1]:
                    lines[-1].append(piece)
                    line_width += piece.width
                continue
            if lines[-1] and line_width + piece.width > width + 0.01:
                trim(lines[-1])
                lines.append([])
                line_width = 0.0
            if piece.width > width + 0.01:
                chunks = _split_long(piece, width, scale)
                for chunk in chunks[:-1]:
                    lines[-1].append(chunk)
                    lines.append([])
                piece = chunks[-1]
                line_width = 0.0
            lines[-1].append(piece)
            line_width += piece.width
    trim(lines[-1])
    return lines


def _line_width(line: list[Piece]) -> float:
    return sum(piece.width for piece in line)


def _line_size(line: list[Piece], fallback: float) -> float:
    return max((piece.size for piece in line), default=fallback)


def _descender(face: str) -> float:
    return abs(adapter.font_metrics(engine_fonts.font_path(face))[1])


@dataclass
class Layout:
    fragments: list[dict]
    lines_needed: int
    lines_over: int
    needed_h_pt: float
    bottom: float
    fits_height: bool


def place(
    runs: tuple[Run, ...],
    box: Rect,
    first_baseline: float,
    gap: float | None,
    align: str,
    scale: float,
) -> Layout:
    width = max(box[2] - box[0], 1.0)
    lines = layout_lines(runs, width, scale)
    fragments: list[dict] = []
    base_size = max(run.size for run in runs) * scale
    baseline = first_baseline
    last_bottom = first_baseline
    for number, line in enumerate(lines):
        size = _line_size(line, base_size)
        if number > 0:
            baseline += gap if gap is not None else DEFAULT_LINE_HEIGHT * size
        line_w = _line_width(line)
        x = box[0]
        extra_per_space = 0.0
        if align == "center":
            x = box[0] + (width - line_w) / 2
        elif align == "right":
            x = box[2] - line_w
        elif align == "justify" and number < len(lines) - 1:
            spaces = sum(1 for piece in line if piece.space)
            if spaces:
                extra_per_space = (width - line_w) / spaces
        for piece in line:
            if not piece.space:
                fragments.append(
                    {
                        "x": x,
                        "baseline": baseline,
                        "text": piece.text,
                        "font_path": engine_fonts.font_path(piece.face),
                        "size": piece.size,
                        "color": piece.run.color,
                        "width": piece.width,
                    }
                )
            x += piece.width + (extra_per_space if piece.space else 0.0)
        face = line[0].face if line else _face(runs[0])
        last_bottom = baseline + _descender(face) * size
    limit = box[3] + FIT_TOLERANCE_PT
    fits = last_bottom <= limit
    lines_over = 0
    if not fits:
        step = gap if gap is not None else DEFAULT_LINE_HEIGHT * base_size
        lines_over = max(1, int(-(-(last_bottom - limit) // max(step, 0.1))))
    return Layout(
        fragments=fragments,
        lines_needed=len(lines),
        lines_over=lines_over,
        needed_h_pt=round(last_bottom - box[1], 3),
        bottom=last_bottom,
        fits_height=fits,
    )


def fragment_band(fragment: dict) -> Rect:
    size = fragment["size"]
    inset = min(fragment["width"] * 0.05, size * 0.1)
    return (
        fragment["x"] + inset,
        fragment["baseline"] - BAND_TOP * size,
        fragment["x"] + fragment["width"] - inset,
        fragment["baseline"] - BAND_BOTTOM * size,
    )


def collisions(fragments: list[dict], others: list[tuple[Span, Char]]) -> int:
    """How many other glyphs the new text's core bands cross."""
    grid = _Grid(others)
    hit: set[int] = set()
    for fragment in fragments:
        band = fragment_band(fragment)
        for span, char in grid.near(band):
            if char.c.isspace() or char.c in WRITE_OVER or char.c in IGNORABLE:
                continue
            if intersects(band, core_rect(span, char)):
                hit.add(id(char))
    return len(hit)


# -- per-op planning -----------------------------------------------------------------


def visible_runs(op: TextOp) -> TextOp:
    """The op with the ignorable characters (`model.IGNORABLE`) taken out of
    its runs: they are not drawn (a run may end up empty -- it then draws
    nothing). Positions of a later refusal count in this text."""
    if not any(char in IGNORABLE for run in op.runs for char in run.text):
        return op
    return dc_replace(op, runs=tuple(dc_replace(run, text=strip_ignorable(run.text)) for run in op.runs))


def check_glyphs(op: TextOp) -> None:
    for run_index, run in enumerate(op.runs):
        face = _face(run)
        for position, char in enumerate(run.text):
            if char == "\n" or char in IGNORABLE:
                continue
            reason = engine_fonts.refusal(char, face)
            if reason is not None:
                raise errors.EngineError(
                    errors.UNDRAWABLE_CHARACTER,
                    f"{face} cannot print U+{ord(char):04X}: {reason}",
                    {
                        "op_id": op.id,
                        "run": run_index,
                        "position": position,
                        "codepoint": f"U+{ord(char):04X}",
                        "font": face,
                    },
                )


def _same_style(run: Run, span: Span) -> bool:
    color = tuple(round(c * 255) for c in run.color)
    original = ((span.color >> 16) & 255, (span.color >> 8) & 255, span.color & 255)
    return (
        abs(run.size - span.size) <= 0.05
        and color == original
        and run.bold == span.bold
        and run.italic == span.italic
    )


def _partial(resolved: Resolved, box: Rect) -> tuple[list[tuple[Span, Char]], list[dict]] | None:
    """Tail rewrite of a single line: (glyphs to remove, fragments) or None."""
    op = resolved.op
    if len(resolved.lines) != 1 or not op.runs:
        return None
    line = resolved.lines[0]
    pairs = line.chars
    # Compared as the user sees the line (`Line.view`); each visible character
    # stands for its own glyph plus the ignorable glyphs that travel with it.
    view = line.view
    old = "".join(char.c for char in view)
    new = "".join(run.text for run in op.runs)
    if not view or "\n" in new or nfc(old) != old or nfc(new) != new:
        return None
    first = op.runs[0]
    style = (first.family, first.size, first.color, first.bold, first.italic)
    if any((run.family, run.size, run.color, run.bold, run.italic) != style for run in op.runs):
        return None
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    # A kept prefix never ENDS in a space that only stands for zero-width
    # glyphs (Docusign's word gap): the new tail then starts with a REAL space
    # glyph (same 0.25 em advance) and the zero-width glyphs go with the old
    # tail -- otherwise readers see no word break before the new text.
    while 0 < prefix < len(new) and view[prefix - 1].own is None:
        prefix -= 1
    if prefix == 0:
        return None
    # The kept prefix stays in its own glyphs whatever its style; the new tail
    # must match the style of the text it replaces (B1.1: a logical line may
    # join a bold label and a regular value, or an original prefix and a tail
    # an earlier edit drew in the substitute face).
    anchor = _glyph_span(pairs, view[min(prefix, len(view) - 1)])
    if anchor.synthetic:
        anchor = _glyph_span(pairs, view[prefix - 1])
    if not _same_style(first, anchor):
        return None
    if prefix == len(old) == len(new):
        return [], []
    tail = new[prefix:]
    # Kept: the glyphs up to the last kept visible character's own glyph; the
    # ignorable glyphs after it go with the removed tail.
    last_kept = view[prefix - 1]
    cut = last_kept.own + 1 if last_kept.own is not None else last_kept.raw[-1] + 1
    if cut < len(pairs):
        x = pairs[cut][1].bbox[0]
    else:
        x = pairs[-1][1].bbox[2]
    removal = pairs[cut:]
    if not tail:
        return removal, []
    run = Run(tail, first.family, first.size, first.color, first.bold, first.italic)
    width = _measure(run, tail, run.size)
    if x + width > box[2] + FIT_TOLERANCE_PT:
        return None
    fragment = {
        "x": x,
        "baseline": line.baseline,
        "text": tail,
        "font_path": engine_fonts.font_path(_face(run)),
        "size": run.size,
        "color": run.color,
        "width": width,
    }
    anchor = _anchor(pairs[:cut])
    if anchor is not None:
        fragment["anchor"] = anchor
    return removal, [fragment]


def _glyph_span(pairs: list[tuple[Span, Char]], char: ViewChar) -> Span:
    """The span of the glyph a visible character stands for (its own glyph,
    or the first of the ignorable glyphs a view space stands for)."""
    return pairs[char.own if char.own is not None else char.raw[0]][0]


def _anchor(kept: list[tuple[Span, Char]]) -> dict | None:
    """The last kept glyph that the content stream really paints (not a
    synthetic join space, not whitespace, not an ignorable glyph): the new
    tail is spliced into the content right after it (QA 2026-09-30, defect 1)."""
    for span, char in reversed(kept):
        if span.synthetic or char.c.isspace() or char.c in IGNORABLE:
            continue
        return {"c": char.c, "origin": (char.origin[0], char.origin[1])}
    return None


def _gap(resolved: Resolved, op: TextOp) -> float | None:
    if op.line_height is not None:
        return None  # per line: line_height * size (handled by caller via scale)
    lines = resolved.lines
    if len(lines) >= 2:
        return max(lines[1].baseline - lines[0].baseline, 1.0)
    return None


def moved_by(op: TextOp, target_bbox: Rect) -> tuple[float, float] | None:
    """(dx, dy) when the op's box puts the block somewhere else (✥ Move), else
    None. Only the box's top-left counts: a box grown to the right or down is a
    reflow, not a move."""
    if op.box is None:
        return None
    dx = op.box[0] - target_bbox[0]
    dy = op.box[1] - target_bbox[1]
    if abs(dx) <= MOVE_TOLERANCE_PT and abs(dy) <= MOVE_TOLERANCE_PT:
        return None
    return dx, dy


def _check_move(op: TextOp, move: tuple[float, float], page_size: tuple[float, float] | None,
                target_bbox: Rect, fragments: list[dict]) -> None:
    """A move is carried out whole or refused (QA 2026-09-30, defect 2: it used
    to be dropped silently). The box must lie on the page, and the planned text
    must start exactly `dx, dy` from where the block was."""
    box = op.box
    if page_size is not None:
        width, height = page_size
        if box[0] < -0.5 or box[1] < -0.5 or box[2] > width + 0.5 or box[3] > height + 0.5:
            raise errors.EngineError(
                errors.OP_OUTSIDE_PAGE, "The moved text would reach outside the page", {"op_id": op.id}
            )
    if not fragments or op.align != "left":
        return
    dx, dy = move
    first = fragments[0]
    if abs(first["x"] - (target_bbox[0] + dx)) > 0.01:
        raise errors.EngineError(
            errors.TEXT_MOVE_NOT_APPLIED,
            "The text block could not be moved",
            {"op_id": op.id, "dx": round(dx, 3), "dy": round(dy, 3)},
        )


def plan_replace(resolved: Resolved, others: list[tuple[Span, Char]], plan: PagePlan,
                 page_size: tuple[float, float] | None = None) -> None:
    op = resolved.op = visible_runs(resolved.op)
    check_glyphs(op)
    target_bbox = resolved.bbox
    first_baseline = resolved.lines[0].baseline
    if op.box is not None:
        box = op.box
        first_baseline += box[1] - target_bbox[1]
    else:
        box = target_bbox
    move = moved_by(op, target_bbox)

    # A moved block is rewritten whole at its new place (all its glyphs go,
    # the whole text is set from the new left edge / first baseline) -- even
    # when its text did not change. Only an edit in place may keep a prefix.
    partial = None if move is not None else _partial(resolved, box)
    if partial is not None:
        removal, fragments = partial
        crossing = collisions(fragments, others)
        if crossing and op.fit != "overflow":
            raise errors.EngineError(
                errors.TEXT_OVERFLOW,
                "The new text would overlap other text on the page",
                {"op_id": op.id, "reason": "collision", "lines_over": 0},
            )
        if crossing:
            plan.warnings.append(
                {"op_id": op.id, "code": errors.TEXT_OVERFLOW, "lines_over": 0, "collisions": crossing}
            )
        plan.removal.extend(removal)
        plan.fragments.extend(fragments)
        _record(plan, op, resolved, target_bbox, fragments)
        return

    gap = _gap(resolved, op)
    scale = 1.0
    while True:
        line_gap = gap * scale if gap is not None else None
        if op.line_height is not None:
            line_gap = op.line_height * max(run.size for run in op.runs) * scale
        result = place(op.runs, box, first_baseline, line_gap, op.align, scale)
        crossing = collisions(result.fragments, others)
        if result.fits_height and not crossing:
            break
        if op.fit == "overflow":
            plan.warnings.append(
                {
                    "op_id": op.id,
                    "code": errors.TEXT_OVERFLOW,
                    "lines_over": result.lines_over,
                    "collisions": crossing,
                }
            )
            break
        smallest = min(run.size for run in op.runs) * scale
        if op.fit == "shrink" and smallest - SHRINK_STEP_PT >= SHRINK_MIN_PT:
            scale = (smallest - SHRINK_STEP_PT) / min(run.size for run in op.runs)
            continue
        details = {"op_id": op.id, "lines_over": result.lines_over, "needed_h_pt": result.needed_h_pt}
        if result.fits_height:
            details["reason"] = "collision"
        raise errors.EngineError(errors.TEXT_OVERFLOW, "The new text does not fit its box", details)
    if move is not None:
        _check_move(op, move, page_size, target_bbox, result.fragments)
    if scale != 1.0:
        plan.warnings.append({"op_id": op.id, "code": "text_shrunk", "size_scale": round(scale, 4)})
    plan.removal.extend(resolved.chars)
    plan.fragments.extend(result.fragments)
    _record(plan, op, resolved, target_bbox, result.fragments)


def _record(plan: PagePlan, op: TextOp, resolved: Resolved, target_bbox: Rect, fragments: list[dict]) -> None:
    rects = [target_bbox]
    for fragment in fragments:
        size = fragment["size"]
        rects.append(
            (
                fragment["x"],
                fragment["baseline"] - size,
                fragment["x"] + fragment["width"],
                fragment["baseline"] + 0.3 * size,
            )
        )
    plan.changed_regions.append(union(rects))
    spans = {span.font for span, _char in resolved.chars}
    for run in op.runs:
        for original in sorted(spans):
            plan.substituted_from.append({"op_id": op.id, "family": run.family, "original_font": original})
            break


def plan_page(page: PageText, ops: list[TextOp]) -> PagePlan:
    """Resolve and lay out every op of one page, or raise the first refusal."""
    if page.scanned:
        raise errors.EngineError(
            errors.SCANNED_PAGE_NOT_EDITABLE,
            "This page is a scanned image; its text cannot be edited (OCR is a separate feature)",
            {"op_id": ops[0].id if ops else None},
        )
    resolved: list[Resolved] = []
    claimed: dict[str, str] = {}
    for op in ops:
        target = resolve_target(page, op)
        for line in target.lines:
            if line.id in claimed:
                raise errors.EngineError(
                    errors.DUPLICATE_TARGET,
                    "Two edits target the same line",
                    {"op_id": op.id, "other_op_id": claimed[line.id]},
                )
            claimed[line.id] = op.id
        resolved.append(target)
    target_ids = {id(char) for item in resolved for _span, char in item.chars}
    others = [
        pair for _block, line in page.all_lines() for pair in line.chars if id(pair[1]) not in target_ids
    ]
    plan = PagePlan()
    for item in resolved:
        before = len(plan.removal)
        if item.op.kind == "text.delete":
            plan.removal.extend(item.chars)
            plan.changed_regions.append(item.bbox)
        else:
            plan_replace(item, others, plan, (page.width, page.height))
        for _span, char in plan.removal[before:]:
            plan.owners[id(char)] = item.op.id
    return plan
