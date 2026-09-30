"""Page inspection and the server text model, with a small cache.

`rawdict` of a whole 44-page package costs 0.5-0.8 s (spike [S7]) -> the text
model is cached per (file sha256, page index). Entries are immutable by
convention: nothing mutates a `PageText` after `adapter.Doc.page_text`.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from app.services.pdf_engine import adapter
from app.services.pdf_engine import fonts as engine_fonts
from app.services.pdf_engine.model import PageInfo, PageText, rect_dict

_TEXT_CACHE: "OrderedDict[tuple[str, int], PageText]" = OrderedDict()
_TEXT_CACHE_MAX = 64
_LOCK = threading.Lock()


def inspect_pages(pdf_bytes: bytes) -> tuple[list[PageInfo], list[dict]]:
    """Every page's size/rotation/text flags, plus open warnings (repaired)."""
    with adapter.Doc(pdf_bytes) as doc:
        return [doc.page_info(i) for i in range(doc.page_count)], list(doc.warnings)


def page_text_model(pdf_bytes: bytes, index: int, sha256: str | None = None) -> PageText:
    key = (sha256 or adapter.sha256_hex(pdf_bytes), index)
    with _LOCK:
        cached = _TEXT_CACHE.get(key)
        if cached is not None:
            _TEXT_CACHE.move_to_end(key)
            return cached
    with adapter.Doc(pdf_bytes) as doc:
        if not 0 <= index < doc.page_count:
            raise IndexError(index)
        model = doc.page_text(index)
    with _LOCK:
        _TEXT_CACHE[key] = model
        while len(_TEXT_CACHE) > _TEXT_CACHE_MAX:
            _TEXT_CACHE.popitem(last=False)
    return model


_OPEN_WARNINGS: "OrderedDict[str, list[dict]]" = OrderedDict()


def open_warnings(pdf_bytes: bytes, sha256: str) -> list[dict]:
    """Warnings from merely opening the file (`pdf_repaired`), cached by hash."""
    with _LOCK:
        if sha256 in _OPEN_WARNINGS:
            return list(_OPEN_WARNINGS[sha256])
    with adapter.Doc(pdf_bytes) as doc:
        warnings = list(doc.warnings)
    with _LOCK:
        _OPEN_WARNINGS[sha256] = warnings
        while len(_OPEN_WARNINGS) > _TEXT_CACHE_MAX:
            _OPEN_WARNINGS.popitem(last=False)
    return list(warnings)


def page_images(pdf_bytes: bytes, index: int) -> list[dict]:
    """`[{xref, bbox: Rect}]` of the page's images (targets of `image.remove`)."""
    with adapter.Doc(pdf_bytes) as doc:
        return [{"xref": i["xref"], "bbox": rect_dict(i["bbox"])} for i in doc.page_images(index)]


def page_links(pdf_bytes: bytes, index: int, uid_by_index: dict[int, str] | None = None) -> list[dict]:
    """`[{link_id, rect, target}]` (targets of `link.update` / `link.remove`)."""
    with adapter.Doc(pdf_bytes) as doc:
        out = []
        for link in doc.links(index):
            if link["kind"] == "uri":
                target = {"type": "uri", "uri": link["uri"]}
            elif link["kind"] == "page":
                page_index = link["page_index"]
                target = {"type": "page", "page_uid": (uid_by_index or {}).get(page_index), "position": (
                    page_index + 1 if page_index is not None and page_index >= 0 else None)}
            else:
                target = {"type": "other"}
            out.append({"link_id": link["link_id"], "rect": rect_dict(link["rect"]), "target": target})
        return out


def clear_cache() -> None:
    with _LOCK:
        _TEXT_CACHE.clear()
        _OPEN_WARNINGS.clear()


def _hex(color: int) -> str:
    return f"#{(color >> 16) & 255:02x}{(color >> 8) & 255:02x}{color & 255:02x}"


def _span_dict(span, chars: list[list]) -> dict:
    return {
        "font": span.font,
        "family": engine_fonts.legacy_family(span.font),
        "substitute_family": engine_fonts.suggest_family(span.font),
        "size_pt": round(span.size, 3),
        "color": _hex(span.color),
        "bold": span.bold,
        "italic": span.italic,
        "baseline": round(span.chars[0].origin[1], 3),
        "synthetic": span.synthetic,
        "chars": chars,
    }


def _r(value: float) -> float:
    return round(value, 3)


def _wire_spans(line) -> list[dict]:
    """The line's spans as the USER sees them (`Line.view`): ignorable
    glyphs (U+200B ...) are left out, a run of them between two words is one
    space whose box is the gap. Span and line geometry are the glyphs' own, so
    `text` == the concatenated `chars` and every target bbox is unchanged."""
    pairs = line.chars
    spans: list[dict] = []
    current = None
    for view_char in line.view:
        if view_char.own is not None:
            span, char = pairs[view_char.own]
            entry = [char.c, _r(char.bbox[0]), _r(char.bbox[1]), _r(char.bbox[2]), _r(char.bbox[3])]
        else:
            span, first = pairs[view_char.raw[0]]
            left = pairs[view_char.raw[0] - 1][1].bbox[2]
            right = pairs[view_char.raw[-1] + 1][1].bbox[0]
            last = pairs[view_char.raw[-1]][1]
            entry = [" ", _r(left), _r(min(first.bbox[1], last.bbox[1])), _r(max(left, right)),
                     _r(max(first.bbox[3], last.bbox[3]))]
        if current is not span:
            spans.append(_span_dict(span, []))
            current = span
        spans[-1]["chars"].append(entry)
    return spans


def text_model_dict(page: PageText) -> dict:
    """The compact wire form of `TextModelOut` (without the page/version keys)."""
    blocks = []
    for block in page.blocks:
        lines = []
        for line in block.lines:
            spans = _wire_spans(line)
            lines.append({"id": line.id, "bbox": rect_dict(line.bbox), "text": line.display_text, "spans": spans})
        blocks.append(
            {
                "id": block.id,
                "bbox": rect_dict(block.bbox),
                "editable": block.editable,
                "reason": block.reason,
                "lines": lines,
            }
        )
    return {
        "width_pt": page.width,
        "height_pt": page.height,
        "rotation": page.rotation,
        "has_text": page.has_text,
        "scanned": page.scanned,
        "blocks": blocks,
    }
