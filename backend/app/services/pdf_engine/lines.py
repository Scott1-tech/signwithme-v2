"""Logical lines and blocks from MuPDF's raw lines (B1.1).

MuPDF groups text in CONTENT-STREAM order: a line ends whenever the next glyph
does not continue the pen position of the previous one in the stream. Two
things therefore split one visual line into several raw lines / blocks:

* our own partial edit (`text_edit._partial`) keeps the prefix glyphs in the
  original text object and draws the new tail as a NEW text object at the end
  of the stream, on the same baseline -- MuPDF reports `'Carrier: '` and
  `'Premier Trucking Group Inc.'` as two lines in two blocks, and every block
  after the edited one used to be renumbered;
* producers that position words one by one (Chrome/Skia `(A) Tj 0 0 Td ...`).

This module is pure (no PyMuPDF): the adapter hands it the raw lines grouped by
raw block plus the page's vertical rules, and it returns the server text model's
blocks:

1. Raw lines on one baseline are joined into ONE logical line when they are
   horizontal, the baseline differs by at most `BASELINE_TOL` x size, the sizes
   are alike (`SIZE_RATIO_MIN`), they do not overlap, the gap between them is at
   most `JOIN_GAP` x size and no vertical rule (a table cell border) lies in the
   gap. Neither piece may be whitespace only (a checkbox glyph mapped to a
   space must not end up under an edit of its label). Measured on the real
   44-page package (B1.1 report): same-baseline neighbours at < 0.6 x size are
   label/space and label/checkbox pairs; the first pair from DIFFERENT table
   cells sits at 0.80 x size; column/field gaps are >= 1.07 x size, most
   >= 3 x size. `JOIN_GAP` = 0.6.
   A gap wider than `SPACE_GAP` x size with no space glyph on either side gets a
   synthetic space (a one-char span with `synthetic=True`), so the line text
   reads as it looks.
2. Raw blocks joined through a logical line become one block.
3. Blocks are numbered in reading order (first line's baseline, then left
   edge); lines within a horizontal block by baseline, then left edge. Ids stay
   `b<i>` / `b<i>l<j>`, now derived from GEOMETRY, not from stream order: an edit
   that leaves the geometry of the other blocks alone leaves their ids alone.

Known limit (B2): MuPDF's own grouping is kept otherwise. A FULL replacement of
one line inside a multi-line block makes MuPDF split that block (the redaction
splits its text object); partial edits and whole-block edits do not.
MuPDF also joins, itself, stream-consecutive cells across a table rule; this
module never splits what MuPDF joined.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from app.services.pdf_engine.model import Block, Char, Line, Rect, Span, union

BASELINE_TOL = 0.25
SIZE_RATIO_MIN = 0.85
JOIN_GAP = 0.6
OVERLAP_TOL = 0.15
SPACE_GAP = 0.15

Separator = tuple[float, float, float]  # x, y0, y1 (a vertical rule)


@dataclass
class RawBlock:
    lines: list[Line]  # ids are ignored and re-assigned
    bbox: Rect
    reason: str | None = None


# -- geometry helpers ---------------------------------------------------------------------


def _horizontal(line: Line) -> bool:
    dx, dy = line.direction
    return abs(dx - 1.0) <= 1e-3 and abs(dy) <= 1e-3


def _first(line: Line) -> tuple[Span, Char]:
    span = line.spans[0]
    return span, span.chars[0]


def _last(line: Line) -> tuple[Span, Char]:
    span = line.spans[-1]
    return span, span.chars[-1]


def _x0(line: Line) -> float:
    return _first(line)[1].bbox[0]


def _x1(line: Line) -> float:
    return _last(line)[1].bbox[2]


def _size(line: Line) -> float:
    return max(span.size for span in line.spans)


def _ruled(separators: list[Separator], x0: float, x1: float, top: float, bottom: float) -> bool:
    """A vertical rule strictly inside the gap (x0, x1) crossing the band."""
    for x, y0, y1 in separators:
        if x0 + 0.1 < x < x1 - 0.1 and y0 < bottom and top < y1:
            return True
    return False


def _joinable(a: Line, b: Line, separators: list[Separator]) -> float | None:
    """The gap (pt) if `b` continues `a` to the right on the same line, else None."""
    if not a.text.strip() or not b.text.strip():
        # A whitespace-only piece is often a checkbox/box glyph mapped to a
        # space (real package p.4): joining it would put it under the edit.
        return None
    span_a, char_a = _last(a)
    span_b, char_b = _first(b)
    size = min(span_a.size, span_b.size)
    if size <= 0 or min(span_a.size, span_b.size) / max(span_a.size, span_b.size) < SIZE_RATIO_MIN:
        return None
    baseline_a, baseline_b = char_a.origin[1], char_b.origin[1]
    if abs(baseline_a - baseline_b) > BASELINE_TOL * size:
        return None
    left_end, right_start = char_a.bbox[2], char_b.bbox[0]
    if right_start <= _x0(a) or right_start < left_end - OVERLAP_TOL * size:
        return None
    gap = right_start - left_end
    if gap > JOIN_GAP * size:
        return None
    baseline = min(baseline_a, baseline_b)
    if gap > 0.2 and _ruled(separators, left_end, right_start, baseline - 0.7 * size, baseline):
        return None
    return max(gap, 0.0)


def _joined(chain: list[Line]) -> Line:
    spans: list[Span] = list(chain[0].spans)
    for previous, line in zip(chain, chain[1:]):
        span_a, char_a = _last(previous)
        _span_b, char_b = _first(line)
        gap = char_b.bbox[0] - char_a.bbox[2]
        size = min(span_a.size, _span_b.size)
        if gap > SPACE_GAP * size and not char_a.c.isspace() and not char_b.c.isspace():
            top = min(char_a.bbox[1], char_b.bbox[1])
            bottom = max(char_a.bbox[3], char_b.bbox[3])
            space = Char(" ", (char_a.bbox[2], top, char_b.bbox[0], bottom), (char_a.bbox[2], char_a.origin[1]))
            spans.append(replace(span_a, chars=[space], bbox=space.bbox, synthetic=True))
        spans.extend(line.spans)
    return Line(id="", bbox=union([line.bbox for line in chain]), direction=chain[0].direction, spans=spans)


# -- step 1: logical lines ------------------------------------------------------------------


def _chains(lines: list[Line], separators: list[Separator]) -> list[list[int]]:
    """Indices of `lines`, grouped into left-to-right chains (singletons too)."""
    horizontal = [i for i, line in enumerate(lines) if _horizontal(line)]
    # Bucket by baseline so the pair search stays near-linear on dense pages.
    buckets: dict[int, list[int]] = {}
    for i in horizontal:
        buckets.setdefault(int(lines[i].baseline // 4), []).append(i)
    best_right: dict[int, tuple[float, int]] = {}
    best_left: dict[int, tuple[float, int]] = {}
    for i in horizontal:
        key = int(lines[i].baseline // 4)
        for j in buckets.get(key - 1, []) + buckets.get(key, []) + buckets.get(key + 1, []):
            if j == i:
                continue
            gap = _joinable(lines[i], lines[j], separators)
            if gap is None:
                continue
            if i not in best_right or (gap, j) < best_right[i]:
                best_right[i] = (gap, j)
            if j not in best_left or (gap, i) < best_left[j]:
                best_left[j] = (gap, i)
    nxt = {i: j for i, (_g, j) in best_right.items() if best_left.get(j, (0, None))[1] == i}
    has_prev = set(nxt.values())
    chains: list[list[int]] = []
    seen: set[int] = set()
    for i in range(len(lines)):
        if i in has_prev or i in seen:
            continue
        chain = [i]
        seen.add(i)
        while chain[-1] in nxt and nxt[chain[-1]] not in seen:
            chain.append(nxt[chain[-1]])
            seen.add(chain[-1])
        chains.append(chain)
    for i in range(len(lines)):  # a (theoretical) cycle: keep the rest as singletons
        if i not in seen:
            chains.append([i])
    return chains


@dataclass
class _Group:
    raw: list[int]
    lines: list[Line]

    @property
    def horizontal(self) -> bool:
        return all(_horizontal(line) for line in self.lines)


def _sorted_lines(lines: list[Line]) -> list[Line]:
    return sorted(lines, key=lambda line: (round(line.baseline, 2), round(_x0(line), 2)))


# -- entry point ----------------------------------------------------------------------------


def assemble(raw_blocks: list[RawBlock], separators: list[Separator] | None = None) -> list[Block]:
    separators = separators or []
    flat: list[Line] = []
    owner: list[int] = []
    for bi, raw in enumerate(raw_blocks):
        for line in raw.lines:
            flat.append(line)
            owner.append(bi)

    # Union-find over raw blocks: a logical line spanning two raw blocks joins them.
    parent = list(range(len(raw_blocks)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    logical: list[tuple[int, Line]] = []  # (raw block of its first piece, line)
    for chain in _chains(flat, separators):
        for i in chain[1:]:
            parent[find(owner[i])] = find(owner[chain[0]])
        line = flat[chain[0]] if len(chain) == 1 else _joined([flat[i] for i in chain])
        logical.append((owner[chain[0]], line))

    by_root: dict[int, _Group] = {}
    for bi in range(len(raw_blocks)):
        by_root.setdefault(find(bi), _Group(raw=[], lines=[])).raw.append(bi)
    order_in_stream = {id(line): n for n, (_bi, line) in enumerate(logical)}
    for bi, line in logical:
        by_root[find(bi)].lines.append(line)
    groups = [g for g in by_root.values() if g.lines]

    finished: list[tuple[tuple[float, float], Block]] = []
    for group in groups:
        raws = sorted(group.raw)
        reason = next((raw_blocks[bi].reason for bi in raws if raw_blocks[bi].reason), None)
        if group.horizontal:
            # One block per LINE (the Sejda way): a click edits the line under
            # the pointer, never the whole paragraph around it. Each block is
            # exactly as large as its own line.
            for line in _sorted_lines(group.lines):
                finished.append(
                    ((round(line.baseline, 2), round(_x0(line), 2)),
                     Block(id="", bbox=line.bbox, lines=[line], editable=reason is None, reason=reason))
                )
            continue
        # Rotated / vertical text: keep MuPDF's grouping and order.
        lines = sorted(group.lines, key=lambda line: order_in_stream.get(id(line), 0))
        bbox = union([raw_blocks[bi].bbox for bi in raws] + [line.bbox for line in lines])
        first = lines[0]
        finished.append(
            ((round(first.baseline, 2), round(_x0(first), 2)), Block(id="", bbox=bbox, lines=lines,
                                                                    editable=reason is None, reason=reason))
        )
    finished.sort(key=lambda item: item[0])
    blocks: list[Block] = []
    for bi, (_key, block) in enumerate(finished):
        block.id = f"b{bi}"
        block.lines = [replace(line, id=f"b{bi}l{li}") for li, line in enumerate(block.lines)]
        blocks.append(block)
    return blocks
