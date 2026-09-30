"""ToUnicode CMap repair (QA 2026-09-30, defect 3).

PyMuPDF 1.28.2 `insert_font` writes a ToUnicode CMap whose destinations for
code points outside the BMP are the bare code point in hex -- `<14fd> <14ff>
<10300>`, five hex digits. A ToUnicode destination is UTF-16BE (PDF 32000
9.10.3): an odd number of digits is a syntax error and pypdf's
`extract_text` dies on it ("Odd-length string"); other readers guess. It hit
DejaVu Sans/Serif and Amiri (they carry Old Italic, math alphanumerics, Arabic
math glyphs), not Liberation/Arimo/Carlito/Caladea (BMP only).

`repair` is general, not tied to a font: it parses every `bfchar`/`bfrange`
entry, reads each destination the way the writer meant it, and -- only when
the CMap is not valid -- writes the mapping again from scratch:

* a destination is UTF-16BE when its digit count is a multiple of 4; any other
  count (`<10300>`, `<41>`) is read as ONE code point;
* output: `bfrange` only for runs of single-BMP-code-point destinations whose
  source and destination stay inside one last-byte block (no carry -- some
  readers increment only the last byte); everything else is `bfchar`, with
  UTF-16 surrogate pairs above U+FFFF; at most 100 entries per block.

The header (CIDSystemInfo, CMapName, codespacerange) and the trailer are kept
byte for byte. A valid CMap is returned unchanged (`None`).
"""
from __future__ import annotations

import re

_HEX = re.compile(rb"<([0-9A-Fa-f\s]*)>")
_BLOCK = re.compile(rb"\d+\s+begin(bfchar|bfrange)(.*?)end\1", re.S)
_TOKEN = re.compile(rb"<[0-9A-Fa-f\s]*>|\[|\]|/[^\s<>\[\]()/%{}]+")
MAX_ENTRIES = 100


def _digits(token: bytes) -> str:
    return re.sub(rb"\s+", b"", token[1:-1]).decode("ascii")


def _dst_text(digits: str) -> str | None:
    """A destination's text. UTF-16BE when the digit count is a multiple of 4,
    else one code point (the PyMuPDF form). None when unreadable."""
    if not digits:
        return None
    if len(digits) % 4 == 0:
        raw = bytes.fromhex(digits)
        try:
            return raw.decode("utf-16-be")
        except UnicodeDecodeError:
            # a lone surrogate: keep the code units as they are
            return "".join(chr(int(digits[i:i + 4], 16)) for i in range(0, len(digits), 4))
    value = int(digits, 16)
    if value > 0x10FFFF:
        return None
    return chr(value)


def _valid_dst(digits: str) -> bool:
    return bool(digits) and len(digits) % 4 == 0


def _increment(text: str, step: int) -> str | None:
    if not text:
        return None
    value = ord(text[-1]) + step
    if value > 0x10FFFF:
        return None
    return text[:-1] + chr(value)


def parse(data: bytes) -> tuple[dict[tuple[int, int], str], bool] | None:
    """(mapping {(code, code_bytes): text}, every destination already valid?)
    or None when the CMap cannot be read (then it is left alone)."""
    mapping: dict[tuple[int, int], str] = {}
    valid = True
    for match in _BLOCK.finditer(data):
        kind, body = match.group(1), match.group(2)
        tokens = _TOKEN.findall(body)
        i = 0
        if kind == b"bfchar":
            while i + 1 < len(tokens):
                src, dst = tokens[i], tokens[i + 1]
                i += 2
                if not src.startswith(b"<") or not dst.startswith(b"<"):
                    return None  # a name destination: not ours to rewrite
                src_digits, dst_digits = _digits(src), _digits(dst)
                if not src_digits or len(src_digits) % 2:
                    return None
                text = _dst_text(dst_digits)
                if text is None:
                    return None
                valid = valid and _valid_dst(dst_digits)
                mapping[(int(src_digits, 16), len(src_digits) // 2)] = text
            continue
        while i + 2 < len(tokens):
            lo, hi = tokens[i], tokens[i + 1]
            if not lo.startswith(b"<") or not hi.startswith(b"<"):
                return None
            lo_digits, hi_digits = _digits(lo), _digits(hi)
            if not lo_digits or len(lo_digits) % 2 or len(lo_digits) != len(hi_digits):
                return None
            width = len(lo_digits) // 2
            first, last = int(lo_digits, 16), int(hi_digits, 16)
            if last < first or last - first > 0xFFFF:
                return None
            if tokens[i + 2] == b"[":
                j = i + 3
                items: list[bytes] = []
                while j < len(tokens) and tokens[j] != b"]":
                    items.append(tokens[j])
                    j += 1
                if j >= len(tokens):
                    return None
                for offset, item in enumerate(items[: last - first + 1]):
                    digits = _digits(item) if item.startswith(b"<") else ""
                    text = _dst_text(digits)
                    if text is None:
                        return None
                    valid = valid and _valid_dst(digits)
                    mapping[(first + offset, width)] = text
                i = j + 1
                continue
            dst = tokens[i + 2]
            if not dst.startswith(b"<"):
                return None
            digits = _digits(dst)
            base = _dst_text(digits)
            if base is None:
                return None
            valid = valid and _valid_dst(digits)
            for offset in range(last - first + 1):
                text = _increment(base, offset)
                if text is None:
                    return None
                mapping[(first + offset, width)] = text
            i += 3
    return mapping, valid


def _utf16(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


def _code(code: int, width: int) -> str:
    return f"{code:0{width * 2}X}"


def build_entries(mapping: dict[tuple[int, int], str]) -> bytes:
    """`bfrange` / `bfchar` blocks for a mapping (see the module docstring)."""
    ranges: list[tuple[int, int, int, int]] = []  # (first, last, width, first code point)
    chars: list[tuple[int, int, str]] = []
    items = sorted(mapping.items())
    index = 0
    while index < len(items):
        (code, width), text = items[index]
        cp = ord(text) if len(text) == 1 else -1
        if cp < 0 or cp > 0xFFFF or 0xD800 <= cp <= 0xDFFF:
            chars.append((code, width, text))
            index += 1
            continue
        end = index
        while end + 1 < len(items):
            (next_code, next_width), next_text = items[end + 1]
            step = end + 1 - index
            if (
                next_width != width
                or next_code != code + step
                or len(next_text) != 1
                or ord(next_text) != cp + step
                or (code & 0xFF) + step > 0xFF
                or (cp & 0xFF) + step > 0xFF
                or 0xD800 <= ord(next_text) <= 0xDFFF
            ):
                break
            end += 1
        if end == index:
            chars.append((code, width, text))
        else:
            ranges.append((code, items[end][0][0], width, cp))
        index = end + 1
    out: list[str] = []
    for start in range(0, len(ranges), MAX_ENTRIES):
        chunk = ranges[start : start + MAX_ENTRIES]
        out.append(f"{len(chunk)} beginbfrange")
        out.extend(f"<{_code(a, w)}> <{_code(b, w)}> <{cp:04X}>" for a, b, w, cp in chunk)
        out.append("endbfrange")
    for start in range(0, len(chars), MAX_ENTRIES):
        chunk = chars[start : start + MAX_ENTRIES]
        out.append(f"{len(chunk)} beginbfchar")
        out.extend(f"<{_code(c, w)}> <{_utf16(t)}>" for c, w, t in chunk)
        out.append("endbfchar")
    return ("\n".join(out) + "\n").encode("ascii")


def repair(data: bytes) -> bytes | None:
    """The CMap with every mapping written validly, or None when it already
    is valid (or cannot be read -- then it is not touched)."""
    blocks = list(_BLOCK.finditer(data))
    if not blocks:
        return None
    parsed = parse(data)
    if parsed is None:
        return None
    mapping, valid = parsed
    if valid:
        return None
    head, tail = data[: blocks[0].start()], data[blocks[-1].end():]
    if not head.endswith(b"\n"):
        head += b"\n"
    return head + build_entries(mapping) + tail.lstrip(b"\r\n")


def is_valid(data: bytes) -> bool:
    """Every bfchar/bfrange destination is UTF-16BE (a multiple of 4 hex digits)
    and every source code an even number of digits."""
    for match in _BLOCK.finditer(data):
        tokens = _TOKEN.findall(match.group(2))
        kind = match.group(1)
        i = 0
        if kind == b"bfchar":
            while i + 1 < len(tokens):
                src, dst = _digits(tokens[i]), _digits(tokens[i + 1])
                if len(src) % 2 or not _valid_dst(dst):
                    return False
                i += 2
            continue
        while i + 2 < len(tokens):
            lo, hi = _digits(tokens[i]), _digits(tokens[i + 1])
            if len(lo) % 2 or len(lo) != len(hi):
                return False
            if tokens[i + 2] == b"[":
                j = i + 3
                while j < len(tokens) and tokens[j] != b"]":
                    if not _valid_dst(_digits(tokens[j])):
                        return False
                    j += 1
                i = j + 1
                continue
            if not _valid_dst(_digits(tokens[i + 2])):
                return False
            i += 3
    return True
