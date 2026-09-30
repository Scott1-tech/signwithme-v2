"""Put new text where it belongs in the content stream (QA 2026-09-30, defect 1).

`page.insert_text` APPENDS a content stream. A partial edit keeps the prefix
of a line in its original glyphs and draws only the new tail -- so the tail
came last in the page's content, and readers that follow content order
(pypdf, PyMuPDF `get_text()`) read "Carrier:" in its place and "Premier
Trucking ..." at the end of the page. It looked right (Δ 0) but read wrong.

This module is pure (bytes in, bytes out; no PyMuPDF): a small content-stream
lexer that keeps byte offsets, so an insertion leaves every other byte of the
stream as it was (no re-serialisation: the `0 0 Td` guard, numbers, spacing,
inline images all stay byte for byte).

The adapter (`Doc.write_text`) uses it like this:

1. `mark` -- a scratch copy of the page's streams AND of the Form XObjects it
   draws, where every text-showing operator is preceded by a unique
   fill/stroke colour; MuPDF's `rawdict` then says which operator painted the
   glyph just before the new text (the "anchor": the last kept prefix glyph).
   A second pass with every colour shifted confirms it. Text inside a Form
   XObject is spliced into THAT form when the form is drawn exactly once.
2. `splice_point` -- where to put the new text after that operator: after the
   `ET` of its text object, or -- when the same text object goes on with more
   lines -- before the next line-positioning operator, closing the text object
   there and reopening it with the line matrix restored (`Tm`).
3. `wrap` -- the snippet PyMuPDF wrote for the new text, made independent of
   the graphics state at that point: `q`, the inverse of the current CTM, text
   state reset (Tc Tw Tz Ts Tr), an ExtGState reset, the snippet, `Q`.

Anything this module is not sure about returns None and the text stays
appended (the pre-fix behaviour: right look, reading order at the end). The
adapter also compares a render of the region before and after the splice and
keeps the appended version when a single pixel differs by more than the QA
threshold.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

WHITESPACE = b" \t\r\n\x0c\x00"
DELIMITERS = b"()<>[]{}/%"
_NUMBER = re.compile(rb"^[+-]?(\d+\.?\d*|\.\d+)$")
_EI = re.compile(rb"[\s\x00]EI(?=[\s\x00]|$)")

SHOW_OPS = frozenset({b"Tj", b"TJ", b"'", b'"'})
LINE_OPS = frozenset({b"Td", b"TD", b"Tm", b"T*", b"'", b'"'})
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
Matrix = tuple[float, float, float, float, float, float]


@dataclass
class Op:
    name: bytes
    operands: list[bytes] = field(default_factory=list)
    start: int = 0  # offset of the first operand (or of the operator)
    end: int = 0  # offset just after the operator


class LexError(ValueError):
    pass


def _skip_string(data: bytes, i: int) -> int:
    """`data[i] == b'('`: offset just after the matching `)`."""
    depth = 0
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x5C:  # backslash: skip the escaped byte
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise LexError("unterminated string")


def tokenize(data: bytes) -> list[Op]:
    """The operators of a content stream with their operands and byte spans.
    An inline image (`BI ... ID <binary> EI`) is one op named `BI`."""
    ops: list[Op] = []
    operands: list[bytes] = []
    op_start: int | None = None
    bi_start: int | None = None
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in WHITESPACE:
            i += 1
            continue
        if c == 0x25:  # % comment
            while i < n and data[i] not in b"\r\n":
                i += 1
            continue
        start = i
        if c == 0x28:
            i = _skip_string(data, i)
        elif c == 0x3C:
            if i + 1 < n and data[i + 1] == 0x3C:
                i += 2
            else:
                end = data.find(b">", i)
                if end < 0:
                    raise LexError("unterminated hex string")
                i = end + 1
        elif c == 0x3E:
            i += 2 if i + 1 < n and data[i + 1] == 0x3E else 1
        elif c in b"[]{}":
            i += 1
        elif c == 0x2F:
            i += 1
            while i < n and data[i] not in WHITESPACE and data[i] not in DELIMITERS:
                i += 1
        else:
            while i < n and data[i] not in WHITESPACE and data[i] not in DELIMITERS:
                i += 1
            token = data[start:i]
            if not (_NUMBER.match(token) or token in (b"true", b"false", b"null")):
                if token == b"BI":
                    bi_start = op_start if op_start is not None else start
                    operands, op_start = [], None
                    continue
                if token == b"ID" and bi_start is not None:
                    found = _EI.search(data, i + 1)
                    if found is None:
                        raise LexError("unterminated inline image")
                    i = found.end()
                    ops.append(Op(b"BI", [], bi_start, i))
                    bi_start, operands, op_start = None, [], None
                    continue
                if bi_start is not None:
                    raise LexError("operator inside an inline image dictionary")
                ops.append(Op(token, operands, op_start if op_start is not None else start, i))
                operands, op_start = [], None
                continue
        if op_start is None:
            op_start = start
        operands.append(data[start:i])
    if bi_start is not None:
        raise LexError("unterminated inline image")
    return ops


def _numbers(op: Op, count: int) -> list[float] | None:
    if len(op.operands) != count:
        return None
    try:
        return [float(value) for value in op.operands]
    except ValueError:
        return None


def multiply(m1: Matrix, m2: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def inverse(m: Matrix) -> Matrix | None:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-9:
        return None
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(e * ia + f * ic), -(e * ib + f * id_))


# -- 1. marking ----------------------------------------------------------------------

# The second marking pass shifts every colour by this much: a glyph whose
# colour does not move by exactly this between the passes was not painted by
# a marked operator (it has a colour of its own -- e.g. an unmarked Type3 glyph).
VERIFY_SHIFT = 0x05A5A5


def marker_ordinal(color: int, shift: int = 0) -> int:
    """The ordinal a `rawdict` span colour (0xRRGGBB) stands for (0 = none)."""
    return (int(color) - shift) & 0xFFFFFF


def mark(streams: list[bytes], shift: int = 0) -> tuple[list[bytes], list[tuple[int, int]]]:
    """Scratch copies of `streams` (page content streams and Form XObject
    streams alike): before every text-showing operator a unique
    `r g b rg r g b RG` whose colour is (ordinal + shift) -- ordinals 1, 2, ...
    across all the streams. Returns the copies and, per ordinal - 1,
    (stream index, op index)."""
    out: list[bytes] = []
    where: list[tuple[int, int]] = []
    for s_index, data in enumerate(streams):
        ops = tokenize(data)
        pieces: list[bytes] = []
        cursor = 0
        for o_index, op in enumerate(ops):
            if op.name in SHOW_OPS:
                where.append((s_index, o_index))
                value = (len(where) + shift) & 0xFFFFFF
                r, g, b = (value >> 16) & 255, (value >> 8) & 255, value & 255
                colour = f"{r / 255:.6f} {g / 255:.6f} {b / 255:.6f}"
                pieces.append(data[cursor : op.start])
                pieces.append(f"\n{colour} rg {colour} RG\n".encode("ascii"))
                cursor = op.start
        pieces.append(data[cursor:])
        out.append(b"".join(pieces))
    if len(where) + VERIFY_SHIFT >= 0xFFFFFF:
        raise LexError("too many text operators to mark")
    return out, where


# -- 2. where ------------------------------------------------------------------------


@dataclass
class State:
    """The part of the graphics state the splice needs, at some point."""

    ctm: Matrix = IDENTITY
    tl: float = 0.0
    tr: int = 0


@dataclass
class SplicePoint:
    stream: int  # index into the streams list
    offset: int  # byte offset of the insertion
    split: bool  # True: close the text object here and reopen it
    ctm: Matrix  # CTM at the insertion
    tlm: Matrix | None  # line matrix to restore after a split


class _Walker:
    """Runs the operators of `streams` (one content, in order) keeping CTM,
    TL, Tr (with the q/Q stack) and, inside a text object, the line matrix."""

    def __init__(self, start: State):
        self.ctm, self.tl, self.tr = start.ctm, start.tl, start.tr
        self.stack: list[tuple[Matrix, float, int]] = []
        self.in_text = False
        self.tlm: Matrix = IDENTITY
        self.bt_index = -1
        self.mc_depth = 0
        self.clip_mode = False

    def step(self, o_index: int, op: Op) -> bool:
        """Apply one operator; False when an operand cannot be read."""
        name = op.name
        if name == b"q":
            self.stack.append((self.ctm, self.tl, self.tr))
        elif name == b"Q":
            if self.stack:
                self.ctm, self.tl, self.tr = self.stack.pop()
        elif name == b"cm":
            values = _numbers(op, 6)
            if values is None:
                return False
            self.ctm = multiply(tuple(values), self.ctm)  # type: ignore[arg-type]
        elif name == b"BT":
            self.in_text, self.tlm, self.bt_index, self.mc_depth = True, IDENTITY, o_index, 0
            self.clip_mode = self.tr >= 4
        elif name == b"ET":
            self.in_text, self.bt_index = False, -1
        elif name in (b"BDC", b"BMC"):
            if self.in_text:
                self.mc_depth += 1
        elif name == b"EMC":
            if self.in_text:
                self.mc_depth -= 1
        elif name == b"TL":
            values = _numbers(op, 1)
            if values is None:
                return False
            self.tl = values[0]
        elif name == b"Tr":
            values = _numbers(op, 1)
            if values is None:
                return False
            self.tr = int(values[0])
            if self.in_text and self.tr >= 4:
                self.clip_mode = True
        elif name == b"Tm":
            values = _numbers(op, 6)
            if values is None:
                return False
            self.tlm = tuple(values)  # type: ignore[assignment]
        elif name in (b"Td", b"TD"):
            values = _numbers(op, 2)
            if values is None:
                return False
            self.tlm = multiply((1.0, 0.0, 0.0, 1.0, values[0], values[1]), self.tlm)
            if name == b"TD":
                self.tl = -values[1]
        elif name in (b"T*", b"'", b'"'):
            if name == b'"' and len(op.operands) != 3:
                return False
            self.tlm = multiply((1.0, 0.0, 0.0, 1.0, 0.0, -self.tl), self.tlm)
        return True


def state_at(streams: list[bytes], stream: int, op_index: int, start: State | None = None) -> State | None:
    """CTM / TL / Tr just before operator `op_index` of `streams[stream]`
    (for a Form XObject: the state it is invoked in)."""
    walker = _Walker(start or State())
    for s_index, data in enumerate(streams):
        for o_index, op in enumerate(tokenize(data)):
            if s_index == stream and o_index == op_index:
                return State(walker.ctm, walker.tl, walker.tr)
            if not walker.step(o_index, op):
                return None
    return None


def splice_point(streams: list[bytes], stream: int, op_index: int,
                 start: State | None = None) -> SplicePoint | None:
    """Where new text belongs right after show-operator `op_index` of
    `streams[stream]` (see the module docstring), or None. `start`: the state
    the content begins in (a Form XObject's: its /Matrix x the CTM at `Do`)."""
    walker = _Walker(start or State())
    for s_index, data in enumerate(streams):
        walker.in_text, walker.tlm, walker.bt_index, walker.mc_depth = False, IDENTITY, -1, 0
        for o_index, op in enumerate(tokenize(data)):
            name = op.name
            if s_index == stream and o_index > op_index and walker.in_text and walker.bt_index >= 0:
                if name == b"ET":
                    return SplicePoint(s_index, op.end, False, walker.ctm, None)
                if name in LINE_OPS and walker.mc_depth == 0 and not walker.clip_mode:
                    return SplicePoint(s_index, op.start, True, walker.ctm, walker.tlm)
            if not walker.step(o_index, op):
                return None
            if s_index == stream and o_index == op_index:
                if not walker.in_text or walker.bt_index < 0 or name not in SHOW_OPS:
                    return None
        if s_index == stream:
            return None  # the text object runs past the end of its stream
    return None


def do_ops(data: bytes) -> list[tuple[int, str]]:
    """(op index, XObject name) of every `Do` of a content stream."""
    out = []
    for o_index, op in enumerate(tokenize(data)):
        if op.name == b"Do" and len(op.operands) == 1 and op.operands[0].startswith(b"/"):
            out.append((o_index, op.operands[0][1:].decode("latin-1")))
    return out


# -- 3. what -------------------------------------------------------------------------


def _num(value: float) -> str:
    # 10 decimals (2026-09-30): with 6, the inverse of a form's CTM (1/0.75
    # ...) put a spliced tail ~1e-4 pt off -- enough for MuPDF's rasteriser to
    # pick another sub-pixel bucket and the render check to refuse the splice.
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def matrix_ops(m: Matrix) -> str:
    return " ".join(_num(v) for v in m)


def wrap(snippet: bytes, point: SplicePoint, gs_name: str | None) -> bytes | None:
    """The bytes to insert at `point` for a PyMuPDF text snippet written for
    the page's default state (identity CTM)."""
    inv = inverse(point.ctm)
    if inv is None:
        return None
    parts = [b"\n"]
    if point.split:
        parts.append(b"ET\n")
    parts.append(b"q\n")
    if any(abs(a - b) > 1e-9 for a, b in zip(inv, IDENTITY)):
        parts.append(f"{matrix_ops(inv)} cm\n".encode("ascii"))
    parts.append(b"0 Tc 0 Tw 100 Tz 0 Ts 0 Tr\n")
    if gs_name:
        parts.append(f"/{gs_name} gs\n".encode("ascii"))
    parts.append(snippet.strip(b"\r\n "))
    parts.append(b"\nQ\n")
    if point.split:
        parts.append(f"BT\n{matrix_ops(point.tlm or IDENTITY)} Tm\n".encode("ascii"))
    return b"".join(parts)


def insert(data: bytes, insertions: list[tuple[int, bytes]]) -> bytes:
    """`data` with every (offset, bytes) inserted; equal offsets keep their
    list order."""
    out: list[bytes] = []
    cursor = 0
    ordered = sorted(range(len(insertions)), key=lambda k: (insertions[k][0], k))
    for k in ordered:
        offset, chunk = insertions[k]
        out.append(data[cursor:offset])
        out.append(chunk)
        cursor = offset
    out.append(data[cursor:])
    return b"".join(out)
