"""One of the TWO modules of the application that import PyMuPDF.

PyMuPDF is dual-licensed AGPL-3.0 / Artifex commercial (EDITOR-V2-PLAN §10.3).
Keeping every call behind two named files keeps the dependency replaceable and
its surface auditable: `grep -r "import pymupdf" app/` must find this file (the
document editor's engine) and `toolkit.py` (the standalone PDF tools) only
(a test enforces it).

Coordinates: PyMuPDF's `rawdict`, `add_redact_annot` and `TextWriter` all work
in the UNROTATED page space with the origin at the CropBox's top-left corner --
the application's canonical geometry. They are passed through unchanged; a
`derotation_matrix` must NOT be applied (spike §10.2.1 [S3]: it gives wrong
positions; `/Rotate` 90/180/270 and CropBox != MediaBox measured at Δ 0).

Two MuPDF behaviours this module works around (spike §10.2.1, MANDATORY):

* MuPDF's content filter (run by `apply_redactions`, `clean_contents`) drops
  `0 0 Td` / `0 0 TD` after a `Tj` as a "no-op". It is not one: it resets the
  text matrix to the line matrix. Chrome/Skia (Docusign) PDFs rely on it, and
  dropping it shifts every following glyph by ~3 pt -- even an EMPTY redaction
  damaged 36 of 44 pages of a real package. `fix_zero_td` rewrites the operand
  to `1e-4 0 Td` (1e-5 is again rounded to zero and dropped) in the page's
  content streams and its Form XObjects BEFORE any redaction.
* The same filter writes `'` / `"` after a `TL` + `Td` as a bare `T*` with a
  wrong TL: the following lines leave the page. `fix_quote_ops` spells them
  out as `T*` + `Tj` first (2026-09-30). Both guards: `_guard_filter`.
* Redaction is limited to TEXT: images and line art are never touched
  (`PDF_REDACT_IMAGE_NONE`, `PDF_REDACT_LINE_ART_NONE`), and redaction
  annotations are created with `fill=False`, so no white box is ever painted.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import unicodedata

import pymupdf

from app.services.pdf_engine import errors
from app.services.pdf_engine.lines import RawBlock, assemble
from app.services.pdf_engine.model import (
    Char,
    Line,
    PageInfo,
    PageText,
    Rect,
    Span,
)

logger = logging.getLogger(__name__)

ENGINE_NAME = "pymupdf"
ENGINE_REF = f"pymupdf {pymupdf.VersionBind}"

# MuPDF keeps global state (font cache, warnings); FastAPI runs sync routes in a
# thread pool. One lock around every document-level operation.
LOCK = threading.RLock()

# Operand written instead of a zero `Td` x. 1e-5 is rounded to 0 by MuPDF's
# filter and dropped again (spike §10.2.1). A distinctive value >= 1e-4, so
# that after the filter ran it can be recognised and set back to an exact 0
# (`restore_zero_td`): MuPDF writes it back as `.00012207031 -0 TD` (float32).
# Left in place, even 1e-4 moved a glyph across a pixel boundary in pdfium
# (36 px at 144 dpi on 3 pages of the real package); restored, 0 px.
TD_EPSILON = "0.000123"
_TD_EPSILON_VALUE = 0.000123
_TD_EPSILON_TOLERANCE = 2e-6

# A page with no text whose images cover at least this share of it is a scan.
SCANNED_IMAGE_COVERAGE = 0.5

_RAWDICT_FLAGS = (pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)

_FONT_CACHE: dict[str, "pymupdf.Font"] = {}

# Content splice (`write_text`, QA 2026-09-30 defect 1).
ANCHOR_TOLERANCE_PT = 0.01
SPLICE_CHECK_ZOOM = 2.0
SPLICE_CHECK_THRESHOLD = 48  # the QA pixel threshold (0..255 per channel)
RESET_GSTATE = "<</Type/ExtGState/CA 1/ca 1/BM/Normal/SMask/None/OP false/op false/OPM 0>>"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


BASE14_PREFIX = "base14:"


def _font(path: str) -> "pymupdf.Font":
    """A font by file path, or `base14:<helv|hebo|...>` for a standard-14 face."""
    font = _FONT_CACHE.get(path)
    if font is None:
        if path.startswith(BASE14_PREFIX):
            font = pymupdf.Font(fontname=path[len(BASE14_PREFIX):])
        else:
            font = pymupdf.Font(fontfile=path)
        _FONT_CACHE[path] = font
    return font


def text_length(font_path: str, text: str, size: float) -> float:
    with LOCK:
        return float(_font(font_path).text_length(text, fontsize=size))


def font_metrics(font_path: str) -> tuple[float, float]:
    """(ascender, descender) of a font file, per unit of font size."""
    with LOCK:
        font = _font(font_path)
        return float(font.ascender), float(font.descender)


class Doc:
    """An open PDF. Use as a context manager; every method takes the lock."""

    def __init__(self, data: bytes):
        self.warnings: list[dict] = []
        with LOCK:
            try:
                self._doc = pymupdf.open(stream=data, filetype="pdf")
            except Exception as exc:  # FileDataError, RuntimeError, ...
                raise errors.EngineError(
                    errors.PDF_CORRUPT, "The PDF could not be opened"
                ) from exc
            if self._doc.needs_pass or self._doc.is_encrypted:
                self._doc.close()
                raise errors.EngineError(
                    errors.PDF_ENCRYPTED, "The PDF is password-protected and cannot be edited"
                )
            if self._doc.page_count < 1:
                self._doc.close()
                raise errors.EngineError(errors.PDF_CORRUPT, "The PDF has no pages")
            if self._doc.is_repaired:
                # A truncated file is still readable (MuPDF rebuilt the xref):
                # a warning, not a refusal (spike §10.2.1 "Fayl holati").
                self.warnings.append({"code": "pdf_repaired"})

    def __enter__(self) -> "Doc":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with LOCK:
            if self._doc is not None:
                self._doc.close()
                self._doc = None

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    @property
    def is_repaired(self) -> bool:
        return bool(self._doc.is_repaired)

    # -- inspection -------------------------------------------------------

    def _image_coverage(self, page) -> float:
        area = abs(page.rect)
        if area <= 0:
            return 0.0
        covered = 0.0
        for info in page.get_image_info():
            rect = pymupdf.Rect(info["bbox"]) & page.rect
            if not rect.is_empty:
                covered += abs(rect)
        return min(covered / area, 1.0)

    def page_info(self, index: int) -> PageInfo:
        with LOCK:
            page = self._doc[index]
            text = page.get_text("text") or ""
            has_text = bool(text.strip())
            scanned = (not has_text) and self._image_coverage(page) >= SCANNED_IMAGE_COVERAGE
            crop = page.cropbox
            return PageInfo(
                position=index + 1,
                rotation=int(page.rotation) % 360,
                width_pt=round(float(crop.width), 3),
                height_pt=round(float(crop.height), 3),
                has_text=has_text,
                scanned=scanned,
            )

    def page_text(self, index: int) -> PageText:
        """The server text model of one page (blocks -> lines -> spans -> chars)."""
        with LOCK:
            page = self._doc[index]
            raw = page.get_text("rawdict", flags=_RAWDICT_FLAGS)
            font_types: dict[str, str] = {}
            for entry in page.get_fonts(full=True):
                # (xref, ext, type, basefont, name, encoding, referencer)
                font_types[_strip_subset(entry[3])] = str(entry[2])
            crop = page.cropbox
            info_scanned = False
            raw_blocks: list[RawBlock] = []
            has_text = False
            text_blocks = [b for b in raw.get("blocks", []) if b.get("type") == 0]
            for raw_block in text_blocks:
                lines: list[Line] = []
                reason = None
                for raw_line in raw_block.get("lines", []):
                    spans: list[Span] = []
                    direction = tuple(float(v) for v in raw_line.get("dir", (1.0, 0.0)))
                    for raw_span in raw_line.get("spans", []):
                        chars = [
                            Char(
                                c=ch["c"],
                                bbox=tuple(float(v) for v in ch["bbox"]),
                                origin=(float(ch["origin"][0]), float(ch["origin"][1])),
                            )
                            for ch in raw_span.get("chars", [])
                        ]
                        if not chars:
                            continue
                        font_name = _strip_subset(str(raw_span.get("font", "")))
                        span = Span(
                            font=font_name,
                            size=float(raw_span.get("size", 0.0)),
                            flags=int(raw_span.get("flags", 0)),
                            color=int(raw_span.get("color", 0)),
                            ascender=float(raw_span.get("ascender", 0.8)),
                            descender=float(raw_span.get("descender", -0.2)),
                            chars=chars,
                            bbox=tuple(float(v) for v in raw_span["bbox"]),
                        )
                        spans.append(span)
                        if any(ch.c.strip() for ch in chars):
                            has_text = True
                        if reason is None:
                            reason = _span_refusal(span, font_types)
                    if not spans:
                        continue
                    if reason is None and (abs(direction[0] - 1.0) > 1e-3 or abs(direction[1]) > 1e-3):
                        reason = "vertical_or_rotated_text"
                    lines.append(
                        Line(
                            id="",
                            bbox=tuple(float(v) for v in raw_line["bbox"]),
                            direction=direction,
                            spans=spans,
                        )
                    )
                if not lines:
                    continue
                raw_blocks.append(
                    RawBlock(lines=lines, bbox=tuple(float(v) for v in raw_block["bbox"]), reason=reason)
                )
            # Logical lines/blocks and geometric ids (B1.1, `lines.assemble`).
            blocks = assemble(raw_blocks, self._vertical_rules(page) if raw_blocks else [])
            if not has_text:
                info_scanned = self._image_coverage(page) >= SCANNED_IMAGE_COVERAGE
            if info_scanned:
                for block in blocks:
                    block.editable = False
                    block.reason = "scanned_page"
            return PageText(
                width=round(float(crop.width), 3),
                height=round(float(crop.height), 3),
                rotation=int(page.rotation) % 360,
                blocks=blocks,
                has_text=has_text,
                scanned=info_scanned,
                font_types=font_types,
            )

    def glyph_origins(self, index: int) -> dict[tuple[str, str], list[tuple[float, float]]]:
        """{(char, font name without subset prefix): [origin, ...]} of every
        glyph on the page (`rawdict`, same geometry as `page_text`) -- a cheap
        "is this glyph still there" index (`apply.execute_plan`)."""
        with LOCK:
            raw = self._doc[index].get_text("rawdict", flags=_RAWDICT_FLAGS)
        out: dict[tuple[str, str], list[tuple[float, float]]] = {}
        for block in raw.get("blocks", []):
            for line in block.get("lines", []) or ():
                for span in line.get("spans", []):
                    font = _strip_subset(str(span.get("font", "")))
                    for ch in span.get("chars", []):
                        out.setdefault((ch["c"], font), []).append((float(ch["origin"][0]), float(ch["origin"][1])))
        return out

    @staticmethod
    def _vertical_rules(page) -> list[tuple[float, float, float]]:
        """Vertical strokes and rectangle edges (table cell borders) as
        (x, y0, y1): text is never joined across one (`lines.assemble`)."""
        rules: list[tuple[float, float, float]] = []
        try:
            drawings = page.get_drawings()
        except Exception:  # noqa: BLE001 - no rules is a safe default
            return rules
        for drawing in drawings:
            for item in drawing.get("items", ()):
                kind = item[0]
                if kind == "l":
                    p, q = item[1], item[2]
                    if abs(p.x - q.x) <= 1.0 and abs(p.y - q.y) > 1.0:
                        rules.append(((p.x + q.x) / 2, min(p.y, q.y), max(p.y, q.y)))
                elif kind == "re":
                    rect = item[1]
                    if rect.height > 1.0:
                        if rect.width <= 2.0:
                            rules.append(((rect.x0 + rect.x1) / 2, rect.y0, rect.y1))
                        else:
                            rules.append((rect.x0, rect.y0, rect.y1))
                            rules.append((rect.x1, rect.y0, rect.y1))
        return rules

    # -- mutation ---------------------------------------------------------

    def _text_streams(self, index: int) -> list[int]:
        """xrefs of the page's content streams and its Form XObjects."""
        page = self._doc[index]
        xrefs: list[int] = list(page.get_contents())
        for entry in page.get_xobjects():
            xref = int(entry[0])
            if xref > 0 and self._doc.xref_get_key(xref, "Subtype")[1] == "/Form":
                xrefs.append(xref)
        return list(dict.fromkeys(xrefs))

    def _rewrite_td(self, index: int, match, replacement) -> int:
        """Tokenise (pypdf) every text stream of the page; set operand 0 of
        each `Td`/`TD` whose operands satisfy `match` to `replacement`. Only
        streams with a hit are written back (others stay byte for byte)."""
        from pypdf.generic import ContentStream, DecodedStreamObject

        rewritten = 0
        for xref in self._text_streams(index):
            try:
                data = self._doc.xref_stream(xref)
            except Exception:  # noqa: BLE001 - unreadable stream: nothing to do
                continue
            if not data or (b"Td" not in data and b"TD" not in data):
                continue
            stream = DecodedStreamObject()
            stream.set_data(data)
            try:
                content = ContentStream(stream, None)
                operations = content.operations
            except Exception:  # noqa: BLE001
                logger.warning("Content stream %s could not be tokenised; left as is", xref)
                continue
            changed = 0
            for operands, operator in operations:
                if operator in (b"Td", b"TD") and len(operands) == 2:
                    try:
                        hit = match(float(operands[0]), float(operands[1]))
                    except (TypeError, ValueError):
                        hit = False
                    if hit:
                        operands[0] = replacement()
                        changed += 1
            if changed:
                self._doc.update_stream(xref, content.get_data())
                rewritten += changed
        return rewritten

    def fix_zero_td(self, index: int) -> int:
        """`0 0 Td` / `0 0 TD` -> `0.000123 0 Td` in the page's content streams
        and Form XObjects, so MuPDF's filter keeps them. Returns the count.
        `redact` calls it; public for tests."""
        from pypdf.generic import FloatObject

        with LOCK:
            return self._rewrite_td(
                index, lambda x, y: x == 0.0 and y == 0.0, lambda: FloatObject(TD_EPSILON)
            )

    def fix_quote_ops(self, index: int) -> int:
        """`string '` -> `T* string Tj` and `aw ac string "` -> `aw Tw ac Tc
        T* string Tj` (their definitions, ISO 32000-1 §9.4.3) in the page's
        content streams and Form XObjects, BEFORE MuPDF's filter runs.

        MuPDF 1.28.2's filter keeps `TL` in its own state and writes the first
        move after it as `x y TD` (which sets TL := -y); for a `T*` it then
        writes an explicit `0 -TL TD`, but for `'` / `"` it writes a bare `T*`
        -- with the wrong TL, every following line leaves the page (canary
        `test_mupdf_filter_quote_operator_canary`). Spelled out as `T*` the
        same lines survive (measured: `scratch/tl.py`). Exact rewrite, not
        undone afterwards. Returns the count; streams without one stay byte
        for byte."""
        from pypdf.generic import ContentStream, DecodedStreamObject

        rewritten = 0
        with LOCK:
            for xref in self._text_streams(index):
                try:
                    data = self._doc.xref_stream(xref)
                except Exception:  # noqa: BLE001 - unreadable stream: nothing to do
                    continue
                if not data or (b"'" not in data and b'"' not in data):
                    continue
                stream = DecodedStreamObject()
                stream.set_data(data)
                try:
                    content = ContentStream(stream, None)
                    operations = content.operations
                except Exception:  # noqa: BLE001
                    logger.warning("Content stream %s could not be tokenised; left as is", xref)
                    continue
                if not any(operator in (b"'", b'"') for _operands, operator in operations):
                    continue
                out = []
                changed = 0
                for operands, operator in operations:
                    if operator == b"'" and len(operands) == 1:
                        out += [([], b"T*"), (operands, b"Tj")]
                        changed += 1
                    elif operator == b'"' and len(operands) == 3:
                        out += [([operands[0]], b"Tw"), ([operands[1]], b"Tc"), ([], b"T*"), ([operands[2]], b"Tj")]
                        changed += 1
                    else:
                        out.append((operands, operator))
                if changed:
                    content.operations = out
                    self._doc.update_stream(xref, content.get_data())
                    rewritten += changed
        return rewritten

    def _guard_filter(self, index: int) -> None:
        """Everything that must happen before MuPDF's content filter runs on
        a page (`redact`, `redact_area`, `remove_image`)."""
        self.fix_zero_td(index)
        self.fix_quote_ops(index)

    def restore_zero_td(self, index: int) -> int:
        """After the filter ran: our epsilon (however MuPDF re-wrote it) back
        to an exact 0, so every glyph is where the original put it."""
        from pypdf.generic import NumberObject

        with LOCK:
            return self._rewrite_td(
                index,
                lambda x, y: abs(x - _TD_EPSILON_VALUE) <= _TD_EPSILON_TOLERANCE and y == 0.0,
                lambda: NumberObject(0),
            )

    def redact(self, index: int, rects: list[Rect]) -> None:
        """Remove the glyphs under `rects` -- text only, no fill, no image or
        line-art changes. Always: `fix_zero_td` -> apply -> `restore_zero_td`
        (spike §10.2.1, MANDATORY; see the module docstring)."""
        if not rects:
            return
        with LOCK:
            self._guard_filter(index)
            page = self._doc[index]
            for rect in rects:
                page.add_redact_annot(pymupdf.Rect(*rect), fill=False, cross_out=False)
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
            self.restore_zero_td(index)

    def write_text(self, index: int, fragments: list[dict]) -> None:
        """Draw text fragments `{x, baseline, text, font_path, size, color}`
        (color = (r, g, b) floats) at their baselines.

        `page.insert_text`, not `TextWriter`: measured on a CropBox offset from
        the MediaBox, TextWriter lands 8 pt off while `insert_text` shares the
        `rawdict` coordinates exactly (Δ 0 at /Rotate 0/90/180/270). Each face
        is registered once per page under a stable resource alias."""
        if not fragments:
            return
        with LOCK:
            # QA 2026-09-30 (defect 1): a fragment with an `anchor` (the last
            # kept glyph before it) is moved from the end of the content into
            # the stream right after the operator that painted that glyph, so
            # every reader reads the line in order. Located BEFORE drawing.
            anchored = {i: f["anchor"] for i, f in enumerate(fragments) if f.get("anchor")}
            units, anchor_ops = self._anchor_ops(index, anchored) if anchored else ([], {})
            snippets: dict[int, int] = {}
            page = self._doc[index]
            aliases: dict[str, str] = {}
            # B1.1: a page edited before already carries `SWM<hash>` -- SUBSET
            # to the glyphs of that earlier edit. `insert_font` under the same
            # name silently reuses that subset, and every glyph it lacks is
            # written as GID 0 (invisible, extracted as U+0000). A fresh name
            # per write keeps the full face; `subset_fonts` trims it on save.
            taken = {str(entry[4]) for entry in page.get_fonts(full=True)}
            for number, fragment in enumerate(fragments):
                path = fragment["font_path"]
                alias = aliases.get(path)
                if alias is None:
                    base = "SWM" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
                    alias, n = base, 1
                    while alias in taken:
                        n += 1
                        alias = f"{base}n{n}"
                    page.insert_font(fontname=alias, fontfile=path)
                    aliases[path] = alias
                    taken.add(alias)
                before = page.get_contents()
                page.insert_text(
                    pymupdf.Point(fragment["x"], fragment["baseline"]),
                    fragment["text"],
                    fontname=alias,
                    fontsize=fragment["size"],
                    color=tuple(fragment["color"]),
                )
                if number in anchor_ops:
                    after = page.get_contents()
                    if after and after[-1] not in before:
                        snippets[number] = int(after[-1])
            if snippets:
                self._splice(index, fragments, units, anchor_ops, snippets)

    # -- QA 2026-09-30 defect 1: new text in content order -------------------------

    def _content_units(self, index: int) -> list[dict] | None:
        """The page's content streams and every Form XObject it draws
        (nested too), as `{xref, parent, name}`: `parent` None for a page
        stream, else the unit index of the content that invokes it by `name`.
        None when a stream appears twice (nothing is spliced then)."""
        page = self._doc[index]
        units = [{"xref": int(x), "parent": None, "name": None} for x in page.get_contents()]
        if not units or len({u["xref"] for u in units}) != len(units):
            return None
        forms = []
        for entry in page.get_xobjects():
            xref, name, invoker = int(entry[0]), str(entry[1]), int(entry[2])
            if xref > 0 and self._doc.xref_get_key(xref, "Subtype")[1] == "/Form":
                forms.append((xref, name, invoker))
        seen = {u["xref"] for u in units}
        by_xref: dict[int, int] = {}
        pending = list(forms)
        # parents first: a form invoked by the page (invoker 0), then its children
        for _round in range(8):
            rest = []
            for xref, name, invoker in pending:
                if invoker == 0:
                    parent = None
                elif invoker in by_xref:
                    parent = by_xref[invoker]
                else:
                    rest.append((xref, name, invoker))
                    continue
                if xref in seen:
                    units.append({"xref": xref, "parent": parent, "name": name, "shared": True})
                    continue
                seen.add(xref)
                by_xref[xref] = len(units)
                units.append({"xref": xref, "parent": parent, "name": name})
            pending = rest
            if not pending:
                break
        return units

    def _anchor_ops(self, index: int, anchors: dict[int, dict]) -> tuple[list[dict], dict[int, tuple[int, int]]]:
        """The content units (`_content_units`) and {fragment number: (unit
        index, op index)} of the operator
        that painted each anchor glyph (`content_splice.mark`). The page's
        streams and forms are marked in place for one `rawdict` per pass and
        restored at once; two passes (colours shifted) must agree."""
        from app.services.pdf_engine import content_splice

        units = self._content_units(index)
        if not units:
            return [], {}
        xrefs = [u["xref"] for u in units if not u.get("shared")]
        originals = [self._doc.xref_stream(x) or b"" for x in xrefs]
        found: list[dict[int, int]] = []
        for shift in (0, content_splice.VERIFY_SHIFT):
            try:
                marked, where = content_splice.mark(originals, shift)
            except content_splice.LexError:
                return [], {}
            if not where:
                return [], {}
            try:
                for xref, data in zip(xrefs, marked):
                    self._doc.update_stream(xref, data)
                raw = self._doc[index].get_text("rawdict", flags=_RAWDICT_FLAGS)
            finally:
                for xref, data in zip(xrefs, originals):
                    self._doc.update_stream(xref, data)
            hits: dict[int, int] = {}
            for block in raw.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        ordinal = content_splice.marker_ordinal(span.get("color", 0), shift)
                        if not 1 <= ordinal <= len(where):
                            continue
                        for ch in span.get("chars", []):
                            for number, anchor in anchors.items():
                                ax, ay = anchor["origin"]
                                if ch["c"] == anchor["c"] and abs(ch["origin"][0] - ax) <= ANCHOR_TOLERANCE_PT \
                                        and abs(ch["origin"][1] - ay) <= ANCHOR_TOLERANCE_PT:
                                    hits[number] = max(hits.get(number, 0), ordinal)
            found.append(hits)
        unit_of = [i for i, u in enumerate(units) if not u.get("shared")]
        out: dict[int, tuple[int, int]] = {}
        for number, ordinal in found[0].items():
            if found[1].get(number) != ordinal:
                continue
            s_index, op_index = where[ordinal - 1]
            out[number] = (unit_of[s_index], op_index)
        return units, out

    def _reference_counts(self) -> dict[int, int]:
        """How many times each object is referenced (`N 0 R`) in the file."""
        import re

        cached = getattr(self, "_refs_cache", None)
        if cached is not None and cached[0] == self._doc.xref_length():
            return cached[1]
        counts: dict[int, int] = {}
        pattern = re.compile(r"(?<![0-9.])(\d+) 0 R\b")
        for xref in range(1, self._doc.xref_length()):
            try:
                source = self._doc.xref_object(xref, compressed=True)
            except Exception:  # noqa: BLE001
                continue
            for match in pattern.finditer(source):
                target = int(match.group(1))
                counts[target] = counts.get(target, 0) + 1
        for key in ("Root", "Info"):
            kind, value = self._doc.xref_get_key(-1, key)
            if kind == "xref":
                target = int(value.split()[0])
                counts[target] = counts.get(target, 0) + 1
        self._refs_cache = (self._doc.xref_length(), counts)
        return counts

    def _drawn_once(self, holder: int, name: str, units: list[dict], parent: int | None, index: int) -> bool:
        """Form `name` of the content `holder` is drawn exactly once in the
        whole file: one reference to it (and to any indirect Resources /
        XObject dictionary on the way), one `Do` in its parent content."""
        from app.services.pdf_engine import content_splice

        refs = self._reference_counts()
        kind, value = self._doc.xref_get_key(holder, "Resources")
        if kind == "xref":
            res = int(value.split()[0])
            if refs.get(res, 0) != 1:
                return False
            holder, prefix = res, ""
        elif kind == "dict":
            prefix = "Resources/"
        else:
            return False
        kind, value = self._doc.xref_get_key(holder, f"{prefix}XObject")
        if kind == "xref":
            if refs.get(int(value.split()[0]), 0) != 1:
                return False
            kind, value = self._doc.xref_get_key(int(value.split()[0]), name)
        else:
            kind, value = self._doc.xref_get_key(holder, f"{prefix}XObject/{name}")
        if kind != "xref" or refs.get(int(value.split()[0]), 0) != 1:
            return False
        parents = [u for u in units if u["parent"] is None] if parent is None else [units[parent]]
        uses = 0
        for unit in parents:
            uses += sum(1 for _o, n in content_splice.do_ops(self._doc.xref_stream(unit["xref"]) or b"") if n == name)
        return uses == 1

    def _form_start(self, units: list[dict], unit: int, index: int, page_streams: list[bytes],
                    page_xrefs: list[int]):
        """The state a form unit's content begins in: its /Matrix x the state
        at the `Do` that draws it (recursively for a nested form)."""
        from app.services.pdf_engine import content_splice

        info = units[unit]
        parent = info["parent"]
        if parent is None:
            holder = self._doc[index].xref
            streams, start = page_streams, content_splice.State()
            names = [(s, o) for s, data in enumerate(page_streams)
                     for o, n in content_splice.do_ops(data) if n == info["name"]]
        else:
            holder = units[parent]["xref"]
            outer = self._form_start(units, parent, index, page_streams, page_xrefs)
            if outer is None:
                return None
            streams, start = [self._doc.xref_stream(holder) or b""], outer
            names = [(0, o) for o, n in content_splice.do_ops(streams[0]) if n == info["name"]]
        if len(names) != 1 or not self._drawn_once(holder, info["name"], units, parent, index):
            return None
        state = content_splice.state_at(streams, names[0][0], names[0][1], start)
        if state is None:
            return None
        kind, value = self._doc.xref_get_key(info["xref"], "Matrix")
        matrix = content_splice.IDENTITY
        if kind == "array":
            try:
                numbers = tuple(float(v) for v in value.strip("[]").split())
            except ValueError:
                return None
            if len(numbers) != 6:
                return None
            matrix = numbers
        return content_splice.State(content_splice.multiply(matrix, state.ctm), state.tl, state.tr)

    def _form_resources(self, form_xref: int, snippet: bytes, index: int, gs_name: str | None) -> bool:
        """The snippet's font (and the gs reset) in the form's own Resources."""
        import re

        kind, _value = self._doc.xref_get_key(form_xref, "Resources")
        if kind == "null":
            return True  # the form uses the page's resources
        match = re.search(rb"/(SWM[0-9a-zA-Z]+)\s+[\d.]+\s+Tf", snippet)
        if match is None:
            return False
        alias = match.group(1).decode("ascii")
        font_xref = next((int(e[0]) for e in self._doc[index].get_fonts(full=True) if str(e[4]) == alias), 0)
        if font_xref <= 0:
            return False
        try:
            self.set_key(form_xref, f"Resources/Font/{alias}", f"{font_xref} 0 R")
            if gs_name:
                self.set_key(form_xref, f"Resources/ExtGState/{gs_name}", RESET_GSTATE)
        except Exception:  # noqa: BLE001
            return False
        return True

    def _reset_gstate(self, index: int) -> str | None:
        """`/SWMgs0` in the page's ExtGState resources: opaque, Normal blend,
        no soft mask, no overprint -- the state `insert_text` assumes."""
        name = "SWMgs0"
        page = self._doc[index]
        try:
            self.set_key(page.xref, f"Resources/ExtGState/{name}", RESET_GSTATE)
        except Exception:  # noqa: BLE001 - no reset: the render check still guards
            return None
        return name

    def _region_pixels(self, index: int, rect: Rect | None) -> tuple[bytes, int]:
        page = self._doc[index]
        clip = page.rect if rect is None else (pymupdf.Rect(*rect) * page.rotation_matrix) & page.rect
        if clip.is_empty:
            return b"", 0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(SPLICE_CHECK_ZOOM, SPLICE_CHECK_ZOOM), clip=clip,
                              alpha=False, annots=False)
        return bytes(pix.samples), pix.n

    @staticmethod
    def _pixels_differ(a: tuple[bytes, int], b: tuple[bytes, int]) -> int:
        if a == b:
            return 0
        if len(a[0]) != len(b[0]) or a[1] != b[1]:
            return -1
        n = a[1]
        count = 0
        sa, sb = a[0], b[0]
        for i in range(0, len(sa), n):
            if any(abs(sa[i + k] - sb[i + k]) > SPLICE_CHECK_THRESHOLD for k in range(n)):
                count += 1
        return count

    def _splice(self, index: int, fragments: list[dict], units: list[dict],
                anchor_ops: dict[int, tuple[int, int]], snippets: dict[int, int]) -> None:
        """Move each appended snippet to its splice point (in the page content
        or in the Form XObject that holds the anchor); keep the appended page
        when anything is uncertain or a render of the region changes."""
        from app.services.pdf_engine import content_splice

        if not units:
            return
        page = self._doc[index]
        contents = [int(x) for x in page.get_contents()]
        snippet_xrefs = set(snippets.values())
        page_xrefs = [x for x in contents if x not in snippet_xrefs]
        if len(set(page_xrefs)) != len(page_xrefs):
            return
        refs = self._reference_counts()
        try:
            page_streams = [self._doc.xref_stream(x) or b"" for x in page_xrefs]
        except Exception:  # noqa: BLE001
            return
        gs_name = self._reset_gstate(index)
        plans: dict[int, list[tuple[int, bytes]]] = {}  # stream xref -> insertions
        originals: dict[int, bytes] = {}
        done: list[int] = []
        split = False
        for number in sorted(snippets):
            unit, op_index = anchor_ops[number]
            info = units[unit]
            snippet = self._doc.xref_stream(snippets[number]) or b""
            try:
                if info["parent"] is None and info["name"] is None:
                    if info["xref"] not in page_xrefs or refs.get(info["xref"], 0) != 1:
                        continue
                    point = content_splice.splice_point(page_streams, page_xrefs.index(info["xref"]), op_index)
                    target = page_xrefs[point.stream] if point is not None else 0
                else:
                    if info.get("shared"):
                        continue
                    start = self._form_start(units, unit, index, page_streams, page_xrefs)
                    if start is None:
                        continue
                    data = self._doc.xref_stream(info["xref"]) or b""
                    point = content_splice.splice_point([data], 0, op_index, start)
                    target = info["xref"]
                    if point is not None and not self._form_resources(target, snippet, index, gs_name):
                        continue
            except content_splice.LexError:
                continue
            if point is None:
                continue
            chunk = content_splice.wrap(snippet, point, gs_name)
            if chunk is None:
                continue
            if target not in originals:
                originals[target] = self._doc.xref_stream(target) or b""
            plans.setdefault(target, []).append((point.offset, chunk))
            done.append(number)
            split = split or point.split
        if not done:
            return
        region: Rect | None = None
        if not split:  # a split re-opens a text object: check the whole page
            rects = []
            for number in done:
                f = fragments[number]
                size = float(f["size"])
                width = float(f.get("width") or text_length(f["font_path"], f["text"], size))
                rects.append((f["x"] - 2, f["baseline"] - 1.2 * size - 2, f["x"] + width + 2,
                              f["baseline"] + 0.5 * size + 2))
            region = (min(r[0] for r in rects), min(r[1] for r in rects),
                      max(r[2] for r in rects), max(r[3] for r in rects))
        before = self._region_pixels(index, region)
        keep = [x for x in contents if x not in {snippets[n] for n in done}]
        for xref, insertions in plans.items():
            self._doc.update_stream(xref, content_splice.insert(originals[xref], insertions))
        self._set_contents(index, keep)
        after = self._region_pixels(index, region)
        differ = self._pixels_differ(before, after)
        if differ != 0:
            logger.info("Content splice changed %s px on page %s; the new text stays appended", differ, index)
            for xref, data in originals.items():
                self._doc.update_stream(xref, data)
            self._set_contents(index, contents)

    def _set_contents(self, index: int, xrefs: list[int]) -> None:
        page = self._doc[index]
        value = f"{xrefs[0]} 0 R" if len(xrefs) == 1 else "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]"
        self._doc.xref_set_key(page.xref, "Contents", value)

    # -- B2: redaction, images, pages, links -----------------------------------

    def page_geometry(self, index: int) -> dict:
        """MediaBox size, the CropBox's top-left inside it (both unrotated,
        top-left origin) and /Rotate -- what it takes to map a DISPLAYED
        MediaBox point (pdfplumber's space) to the application geometry."""
        with LOCK:
            page = self._doc[index]
            media, crop = page.mediabox, page.cropbox
            return {
                "media_w": float(media.width),
                "media_h": float(media.height),
                "crop_x0": float(crop.x0),
                "crop_y0": float(crop.y0),
                "rotation": int(page.rotation) % 360,
            }

    def page_size(self, index: int) -> tuple[float, float]:
        """Unrotated CropBox (width, height)."""
        with LOCK:
            crop = self._doc[index].cropbox
            return float(crop.width), float(crop.height)

    def redact_area(self, index: int, rects: list[Rect], fill: str) -> None:
        """REAL redaction of areas: glyphs, image pixels and line art fully
        inside a rect are removed from the content itself; `fill` black/white
        paints the area, `none` leaves it empty. Same `0 0 Td` guard as `redact`."""
        if not rects:
            return
        colour = {"black": (0, 0, 0), "white": (1, 1, 1)}.get(fill)
        with LOCK:
            self._guard_filter(index)
            page = self._doc[index]
            for rect in rects:
                page.add_redact_annot(
                    pymupdf.Rect(*rect), fill=colour if colour is not None else False, cross_out=False
                )
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
            self.restore_zero_td(index)

    def page_images(self, index: int) -> list[dict]:
        """Every image drawn on the page: `{xref, bbox}` (bbox top-left,
        unrotated, like the text model). An inline image has xref 0 and
        cannot be targeted."""
        with LOCK:
            page = self._doc[index]
            out = []
            for info in page.get_image_info(xrefs=True):
                xref = int(info.get("xref") or 0)
                if xref <= 0:
                    continue
                rect = pymupdf.Rect(info["bbox"]) * page.derotation_matrix
                out.append({"xref": xref, "bbox": (rect.x0, rect.y0, rect.x1, rect.y1)})
            return out

    def remove_image(self, index: int, xref: int, bbox: Rect) -> int:
        """Remove the image `xref` drawn at `bbox` from THIS page only (the same
        image XObject may be shared by other pages -- they keep it). Returns how
        many OTHER images went with it (they overlapped the same area)."""
        with LOCK:
            page = self._doc[index]
            inset = pymupdf.Rect(bbox[0] + 0.5, bbox[1] + 0.5, bbox[2] - 0.5, bbox[3] - 0.5)
            if inset.is_empty:
                inset = pymupdf.Rect(*bbox)
            before = [i for i in self.page_images(index)]
            self._guard_filter(index)
            page.add_redact_annot(inset, fill=False, cross_out=False)
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_NONE,
            )
            self.restore_zero_td(index)
            after = self.page_images(index)
            return max(0, len(before) - len(after) - 1)

    def insert_blank_page(self, width: float, height: float) -> int:
        """A new empty page at the end; returns its index."""
        with LOCK:
            self._doc.new_page(-1, width=float(width), height=float(height))
            return self._doc.page_count - 1

    def delete_page(self, index: int) -> None:
        """Links on other pages that pointed at it are removed by MuPDF."""
        with LOCK:
            self._doc.delete_page(index)

    def move_page(self, index: int, before: int) -> None:
        with LOCK:
            self._doc.move_page(index, before)

    def set_rotation(self, index: int, rotation: int) -> None:
        with LOCK:
            self._doc[index].set_rotation(int(rotation) % 360)

    def links(self, index: int) -> list[dict]:
        """`{link_id, rect (unrotated, top-left), kind, uri, page}` per link."""
        with LOCK:
            page = self._doc[index]
            out = []
            for link in page.get_links():
                xref = int(link.get("xref") or 0)
                rect = pymupdf.Rect(link["from"]) * page.derotation_matrix
                kind = int(link.get("kind", 0))
                out.append(
                    {
                        "link_id": f"lk{xref}",
                        "xref": xref,
                        "rect": (rect.x0, rect.y0, rect.x1, rect.y1),
                        "kind": {pymupdf.LINK_URI: "uri", pymupdf.LINK_GOTO: "page"}.get(kind, "other"),
                        "uri": link.get("uri") if kind == pymupdf.LINK_URI else None,
                        "page_index": link.get("page") if kind == pymupdf.LINK_GOTO else None,
                        "_raw": link,
                    }
                )
            return out

    def add_link(self, index: int, rect: Rect, *, uri: str | None = None,
                 page_index: int | None = None, y: float | None = None) -> None:
        with LOCK:
            page = self._doc[index]
            if uri is not None:
                page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(*rect), "uri": uri})
            else:
                link = {"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(*rect), "page": int(page_index)}
                link["to"] = pymupdf.Point(0, float(y or 0.0))
                page.insert_link(link)

    def remove_link(self, index: int, link_id: str) -> bool:
        with LOCK:
            page = self._doc[index]
            for link in page.get_links():
                if f"lk{int(link.get('xref') or 0)}" == link_id:
                    page.delete_link(link)
                    return True
            return False

    def set_page_ids(self, uids: list[str]) -> None:
        """`/SWMPageId (pg_...)` in every page dictionary ([S4]): the stable id
        travels inside the file, readable by any PDF library."""
        with LOCK:
            for index, uid in enumerate(uids):
                self._doc.xref_set_key(self._doc[index].xref, "SWMPageId", f"({uid})")

    def page_ids(self) -> list[str | None]:
        with LOCK:
            out = []
            for index in range(self._doc.page_count):
                kind, value = self._doc.xref_get_key(self._doc[index].xref, "SWMPageId")
                out.append(value if kind == "string" else None)
            return out

    # -- B2: v7 markup primitives (geometry: unrotated, top-left) --------------

    def draw_text(self, index: int, x: float, baseline: float, text: str, font_ref: str,
                  size: float, color, opacity: float = 1.0, morph=None) -> None:
        """One run of text. `font_ref`: a TTF path (embedded as a subset on
        save) or `base14:<name>`. A fresh resource alias per call (B1.1)."""
        with LOCK:
            page = self._doc[index]
            kwargs = dict(fontsize=size, color=tuple(color), fill_opacity=float(opacity),
                          stroke_opacity=float(opacity))
            if morph is not None:
                kwargs["morph"] = morph
            if font_ref.startswith(BASE14_PREFIX):
                page.insert_text(pymupdf.Point(x, baseline), text,
                                 fontname=font_ref[len(BASE14_PREFIX):], **kwargs)
                return
            taken = {str(entry[4]) for entry in page.get_fonts(full=True)}
            base = "SWM" + hashlib.sha1(font_ref.encode("utf-8")).hexdigest()[:8]
            alias, n = base, 1
            while alias in taken:
                n += 1
                alias = f"{base}n{n}"
            page.insert_font(fontname=alias, fontfile=font_ref)
            page.insert_text(pymupdf.Point(x, baseline), text, fontname=alias, **kwargs)

    def draw_rect(self, index: int, rect: Rect, *, stroke=None, fill=None, width: float = 1.0,
                  opacity: float = 1.0, dashes: str | None = None, radius: float | None = None,
                  line_cap: int = 0) -> None:
        with LOCK:
            shape = self._doc[index].new_shape()
            shape.draw_rect(pymupdf.Rect(*rect), radius=radius)
            shape.finish(color=stroke, fill=fill, width=width if stroke else 0, dashes=dashes,
                         lineCap=line_cap, stroke_opacity=opacity, fill_opacity=opacity, closePath=True)
            shape.commit()

    def draw_oval(self, index: int, rect: Rect, *, stroke=None, fill=None, width: float = 1.0,
                  opacity: float = 1.0, dashes: str | None = None, line_cap: int = 0) -> None:
        with LOCK:
            shape = self._doc[index].new_shape()
            shape.draw_oval(pymupdf.Rect(*rect))
            shape.finish(color=stroke, fill=fill, width=width if stroke else 0, dashes=dashes,
                         lineCap=line_cap, stroke_opacity=opacity, fill_opacity=opacity)
            shape.commit()

    def draw_polyline(self, index: int, points, *, stroke=None, fill=None, width: float = 1.0,
                      opacity: float = 1.0, dashes: str | None = None, closed: bool = False,
                      line_cap: int = 1, line_join: int = 1) -> None:
        with LOCK:
            shape = self._doc[index].new_shape()
            shape.draw_polyline([pymupdf.Point(x, y) for x, y in points])
            shape.finish(color=stroke, fill=fill, width=width if stroke else 0, dashes=dashes,
                         lineCap=line_cap, lineJoin=line_join, closePath=closed,
                         stroke_opacity=opacity, fill_opacity=opacity)
            shape.commit()

    def draw_image(self, index: int, rect: Rect, png: bytes) -> None:
        """The image stretched to exactly `rect` (the box IS what was sized)."""
        with LOCK:
            self._doc[index].insert_image(pymupdf.Rect(*rect), stream=png, keep_proportion=False)

    def show_page_rotated(self, index: int, rect: Rect, source: "Doc", rotate: float) -> None:
        """Page 0 of `source`, rotated by `rotate` degrees, fitted into `rect`
        (the rotated source's bounding box -- so the scale is 1)."""
        with LOCK:
            self._doc[index].show_pdf_page(
                pymupdf.Rect(*rect), source._doc, 0, keep_proportion=True, rotate=float(rotate)
            )

    @staticmethod
    def blank(width: float, height: float) -> "Doc":
        """A new one-page document (a scratch canvas for a rotated element)."""
        with LOCK:
            doc = pymupdf.open()
            doc.new_page(width=float(width), height=float(height))
            data = doc.tobytes()
            doc.close()
        return Doc(data)

    # -- B3: form widgets --------------------------------------------------------
    #
    # PyMuPDF's widget `rect` IS the application geometry (unrotated, top-left
    # of the CropBox): measured at /Rotate 0/90/180/270 with CropBox != MediaBox,
    # `/Rect` = CropBox origin + rect exactly. PyMuPDF writes text, choice,
    # checkbox and signature widgets (and their appearance streams); a radio
    # GROUP is restructured here at the dictionary level (spike [S5]: PyMuPDF
    # makes every button its own field, `/V` a string, and `field_value=True`
    # fails with "bad xref") -- see `forms.py`.

    WIDGET_KIND = {
        pymupdf.PDF_WIDGET_TYPE_TEXT: "text",
        pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "combobox",
        pymupdf.PDF_WIDGET_TYPE_LISTBOX: "listbox",
        pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
        pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
        pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "signature",
        pymupdf.PDF_WIDGET_TYPE_BUTTON: "button",
    }
    _CREATE_KIND = {
        "text": pymupdf.PDF_WIDGET_TYPE_TEXT,
        "combobox": pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
        "checkbox": pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
        "radio": pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
        "signature": pymupdf.PDF_WIDGET_TYPE_SIGNATURE,
    }

    def widgets(self, index: int) -> list[dict]:
        """Every widget annotation of the page, in `/Annots` order."""
        with LOCK:
            page = self._doc[index]
            out = []
            for widget in page.widgets():
                kind = self.WIDGET_KIND.get(widget.field_type, "other")
                buttons = kind in ("checkbox", "radio")
                states = []
                if buttons:
                    states = [s for s in ((widget.button_states() or {}).get("normal") or []) if s != "Off"]
                options = None
                if kind in ("combobox", "listbox"):
                    options = [
                        str(item[0] if isinstance(item, (list, tuple)) else item)
                        for item in (widget.choice_values or [])
                    ]
                annot_flags = self._int_key(widget.xref, "F") or 0
                out.append(
                    {
                        "xref": int(widget.xref),
                        "name": str(widget.field_name or ""),
                        "kind": kind,
                        "rect": tuple(float(v) for v in widget.rect),
                        "field_flags": int(widget.field_flags or 0),
                        "annot_flags": int(annot_flags),
                        "value": widget.field_value,
                        "options": options,
                        "on_states": states,
                        "tooltip": widget.field_label or None,
                        "max_len": int(widget.text_maxlen or 0) if kind == "text" else 0,
                        "font": widget.text_font or None,
                        "size": float(widget.text_fontsize or 0.0),
                    }
                )
            return out

    def _int_key(self, xref: int, key: str) -> int | None:
        kind, value = self._doc.xref_get_key(xref, key)
        if kind in ("int", "float"):
            try:
                return int(float(value))
            except ValueError:
                return None
        return None

    def get_key(self, xref: int, key: str) -> tuple[str, str]:
        with LOCK:
            return self._doc.xref_get_key(xref, key)

    def set_key(self, xref: int, key: str, value: str) -> None:
        """`key` may be a path (`AcroForm/DR/Font/Helv`); PyMuPDF refuses a
        path through an INDIRECT object, so the write goes to the object that
        holds the last indirect step (Docusign's AcroForm is indirect)."""
        with LOCK:
            parts = key.split("/")
            holder, relative = xref, []
            for part in parts[:-1]:
                relative.append(part)
                kind, found = self._doc.xref_get_key(holder, "/".join(relative))
                if kind == "xref":
                    holder, relative = int(found.split()[0]), []
            self._doc.xref_set_key(holder, "/".join(relative + [parts[-1]]), value)

    def object_source(self, xref: int) -> str:
        with LOCK:
            return self._doc.xref_object(xref, compressed=True)

    def replace_object(self, xref: int, source: str) -> None:
        with LOCK:
            self._doc.update_object(xref, source)

    def new_object(self, source: str) -> int:
        with LOCK:
            xref = self._doc.get_new_xref()
            self._doc.update_object(xref, source)
            return xref

    def new_stream(self, source: str, data: bytes) -> int:
        """A new stream object: dictionary `source`, content `data`."""
        with LOCK:
            xref = self._doc.get_new_xref()
            self._doc.update_object(xref, source)
            self._doc.update_stream(xref, data, new=True)
            return xref

    def catalog_xref(self) -> int:
        with LOCK:
            return int(self._doc.pdf_catalog())

    def page_xref(self, index: int) -> int:
        with LOCK:
            return int(self._doc[index].xref)

    def reload_page(self, index: int) -> None:
        """After dictionary-level edits of `/Annots`: MuPDF caches a page's
        annotation list, so the page is re-read."""
        with LOCK:
            self._doc.reload_page(self._doc[index])

    @staticmethod
    def pdf_string(text: str) -> str:
        """A PDF string literal for `text` (hex/UTF-16 when not ASCII)."""
        return pymupdf.get_pdf_str(text)

    def add_widget(self, index: int, *, kind: str, name: str, rect, field_flags: int = 0,
                   value=None, options: list[str] | None = None, font: str = "Helv",
                   size: float = 0.0, max_len: int = 0, tooltip: str | None = None) -> int:
        """One PyMuPDF widget; returns its xref. `kind`: text | combobox |
        checkbox | radio | signature."""
        with LOCK:
            page = self._doc[index]
            widget = pymupdf.Widget()
            widget.field_type = self._CREATE_KIND[kind]
            widget.field_name = name
            widget.rect = pymupdf.Rect(*rect)
            widget.field_flags = int(field_flags)
            if tooltip:
                widget.field_label = tooltip
            if kind in ("text", "combobox"):
                widget.text_font = font
                widget.text_fontsize = float(size or 0.0)
            if kind == "text" and max_len:
                widget.text_maxlen = int(max_len)
            if kind == "combobox":
                widget.choice_values = list(options or [])
            if kind in ("checkbox", "radio"):
                widget.field_value = False
            elif value is not None:
                widget.field_value = value
            annot = page.add_widget(widget)
            return int(annot.xref)

    def set_widget_value(self, index: int, xref: int, value) -> None:
        """Text / choice value through PyMuPDF (it regenerates the appearance)."""
        with LOCK:
            page = self._doc[index]
            widget = page.load_widget(xref)
            if widget is None:  # pragma: no cover - callers pass a live xref
                raise errors.EngineError(errors.UNKNOWN_FIELD, "That form field is not on this page", {})
            widget.field_value = value
            widget.update()

    def has_visible_widgets(self) -> bool:
        """Any widget with a non-empty rect that is not hidden (annotation
        flags Hidden 2 / NoView 32)?"""
        with LOCK:
            for page in self._doc:
                for widget in page.widgets():
                    flags = self._int_key(widget.xref, "F") or 0
                    if flags & (2 | 32):
                        continue
                    rect = widget.rect
                    if rect.width > 0.01 and rect.height > 0.01:
                        return True
            return False

    def widget_count(self) -> int:
        with LOCK:
            return sum(1 for page in self._doc for _ in page.widgets())

    def bake_widgets(self) -> None:
        """Flatten: every widget's appearance becomes page content, the
        widgets and the AcroForm go (other annotations stay). The page content
        streams are appended to, not filtered (the `0 0 Td` guard is not
        needed; a test pins it)."""
        with LOCK:
            self._doc.bake(annots=False, widgets=True)

    def save(self, *, subset_fonts: bool) -> bytes:
        with LOCK:
            if subset_fonts:
                self._doc.subset_fonts()
            self.repair_tounicode()
            return self._doc.tobytes(garbage=3, deflate=True)

    def repair_tounicode(self) -> int:
        """Every font's ToUnicode CMap that is not valid UTF-16BE is written
        again (`tounicode.repair`; PyMuPDF 1.28.2 writes non-BMP destinations
        as bare 5-digit code points -> pypdf "Odd-length string"). Valid CMaps
        are not touched. Returns how many were repaired."""
        from app.services.pdf_engine import tounicode

        repaired = 0
        with LOCK:
            seen: set[int] = set()
            for xref in range(1, self._doc.xref_length()):
                try:
                    if self._doc.xref_get_key(xref, "Type") != ("name", "/Font"):
                        continue
                    kind, value = self._doc.xref_get_key(xref, "ToUnicode")
                except Exception:  # noqa: BLE001 - a free / broken object
                    continue
                if kind != "xref":
                    continue
                cmap_xref = int(value.split()[0])
                if cmap_xref in seen:
                    continue
                seen.add(cmap_xref)
                try:
                    data = self._doc.xref_stream(cmap_xref)
                except Exception:  # noqa: BLE001
                    continue
                if not data:
                    continue
                fixed = tounicode.repair(data)
                if fixed is not None:
                    self._doc.update_stream(cmap_xref, fixed)
                    repaired += 1
        return repaired

    def single_page(self, index: int) -> bytes:
        """A new one-page PDF holding page `index` as it is now."""
        with LOCK:
            out = pymupdf.open()
            try:
                out.insert_pdf(self._doc, from_page=index, to_page=index)
                return out.tobytes(garbage=3, deflate=True)
            finally:
                out.close()


def _strip_subset(name: str) -> str:
    """`ABCDEF+Arial-BoldMT` -> `Arial-BoldMT`."""
    if len(name) > 7 and name[6] == "+" and name[:6].isalpha() and name[:6].isupper():
        return name[7:]
    return name


def _span_refusal(span: Span, font_types: dict[str, str]) -> str | None:
    """Why a span cannot be edited in place ([S8]), or None."""
    if font_types.get(span.font, "").lower() == "type3":
        return "type3_font"
    for char in span.chars:
        if unicodedata.bidirectional(char.c) in ("R", "AL"):
            return "rtl_text"
    return None