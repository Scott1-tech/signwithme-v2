"""2026-09-30 (`_staging\\zw`): defects found on the REAL Docusign template.

1. Zero-width format characters (U+200B on 979 of 1 915 lines) made most of
   the template's text impossible to edit in place (`undrawable_character`).
2. A value typed over a `____` fill line kept its old glyphs after the
   redaction (the free band lay outside MuPDF's hit box) -- the old text stayed
   visible. Now: removal against the real hit box + a check after the
   redaction (retry, else 422 `old_text_not_removed`).
3. MuPDF's filter moved the lines after `TL` + `Td` + `'` off the page
   (`Doc.fix_quote_ops`).
4. `element_points` checked a turned textbox by its upright box.
5. A tail spliced into a scaled Form XObject was ~1e-4 pt off (6-decimal
   inverse CTM) and the render check refused the splice.
"""
from __future__ import annotations

import io
import math
import unicodedata

import pymupdf
import pytest

from app.services import pdf_engine as E
from app.services.pdf_engine import adapter, content_splice, text_edit
from app.services.pdf_engine.markup import element_points, rotated_corners
from app.services.pdf_engine.model import IGNORABLE, match_key, strip_ignorable
from app.services.pdf_engine.ops import Run, TextOp
from tests.editor_v2_helpers import PAGE_UID, pixel_diff, render_gray, reportlab_pdf

REFS = [E.PageRef(PAGE_UID, 1)]
ZW = "​"


# -- helpers -------------------------------------------------------------------------------


def _pdf(content: bytes, *, xobject: bytes | None = None) -> bytes:
    """One page; /F1 Helvetica whose byte 0xA0 (a space-wide glyph) reads as
    U+200B -- exactly how the Docusign template carries them -- and /F2
    Times-Roman; optionally a Form XObject /X1 using /F1."""
    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /ZZZW def "
            b"1 begincodespacerange <00> <FF> endcodespacerange "
            b"1 beginbfrange <20> <7E> <0020> endbfrange 1 beginbfchar <A0> <200B> endbfchar "
            b"endcmap CMapName currentdict /CMap defineresource pop end end")
    resources = b"<< /Font << /F1 5 0 R /F2 7 0 R >>" + (b" /XObject << /X1 8 0 R >>" if xobject else b"") + b" >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources " + resources + b" /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding /ToUnicode 6 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(cmap) + cmap + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman /Encoding /WinAnsiEncoding >>",
    ]
    if xobject:
        objects.append(
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 1224 1584] /Resources << /Font << /F1 5 0 R >> >>"
            b" /Length %d >>\nstream\n" % len(xobject) + xobject + b"\nendstream"
        )
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    start = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start))
    return out.getvalue()


# "\240" = the ZW glyph. "Name:" ends at 72 + 38.676: the next text object's
# leading ZW sits on the previous one's trailing ZW (as Docusign writes them),
# so "Name:" and "Value" are one space-width apart.
DOCUSIGN = (
    b"BT /F1 12 Tf 72 700 Td (\240Carrier: ABC LLC\240) Tj ET "
    b"BT /F1 12 Tf 72 670 Td (\240Name:\240) Tj ET BT /F1 12 Tf 110.676 670 Td (\240Value here\240) Tj ET "
    b"BT /F1 12 Tf 72 640 Td (\240Other line stays\240) Tj ET"
)
# A 9 pt value 3.37 pt above a 12 pt Times fill line (the real template, p.2).
FILL_LINE = (
    b"BT /F1 9 Tf 200 600 Td (Phone: 123-4567) Tj ET "
    b"BT /F2 12 Tf 190 596.63 Td (______________________________) Tj ET "
    b"BT /F1 12 Tf 72 560 Td (Below the field) Tj ET"
)


def _line(pdf: bytes, shown: str):
    for block in E.page_text_model(pdf, 0).blocks:
        for line in block.lines:
            if line.display_text == shown:
                return block, line
    raise AssertionError(shown)


def _op(pdf, shown, new, *, expect=None, op_id="op1", kind="text.replace", grow=150.0, size=None):
    block, line = _line(pdf, shown)
    x0, y0, x1, y1 = line.bbox
    common = dict(id=op_id, kind=kind, page_uid=PAGE_UID, block_id=block.id, line_ids=(line.id,),
                  expect_text=shown if expect is None else expect, bbox=line.bbox)
    if kind == "text.delete":
        return TextOp(**common)
    span = [s for s in line.spans if not s.synthetic][0]
    return TextOp(**common, runs=(Run(new, "LiberationSans", size or span.size, (0.0, 0.0, 0.0), False, False),),
                  box=(x0, y0, x1 + grow, y1))


def _norm(text: str) -> str:
    return " ".join(strip_ignorable(text).split())


def _readers(pdf: bytes, index: int = 0) -> dict[str, str]:
    import pdfplumber
    from pypdf import PdfReader

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        plumber = document.pages[index].extract_text() or ""
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        mupdf = document[index].get_text()
    return {
        "pypdf": _norm(PdfReader(io.BytesIO(pdf)).pages[index].extract_text() or ""),
        "pdfplumber": _norm(plumber),
        "pymupdf": _norm(mupdf),
    }


def _glyph(pdf: bytes, needle: str):
    for block in E.page_text_model(pdf, 0).blocks:
        for line in block.lines:
            text = line.display_text
            if needle in text:
                view = line.view
                return line.chars[view[text.index(needle)].own][1]
    return None


def _new_glyph_chars(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        raw = document[0].get_text("rawdict")
    return "".join(ch["c"] for b in raw["blocks"] for ln in b.get("lines", []) for s in ln["spans"]
                   if "Liberation" in s["font"] for ch in s["chars"])


# -- 1. zero-width characters ---------------------------------------------------------------


def test_the_ignorable_set_is_invisible_format_characters_only():
    for char in IGNORABLE:
        assert unicodedata.category(char) == "Cf", hex(ord(char))
        assert unicodedata.bidirectional(char) == "BN", hex(ord(char))
    # direction controls and tag characters are NOT ignorable
    for char in ("‮", "‭", "‎", "‏", "⁦", "\U000e0041", "⁡"):
        assert char not in IGNORABLE
    assert strip_ignorable("a​b﻿c­d⁠e‌‍f") == "abcdef"
    assert match_key("Café​") == "Café"


def test_a_docusign_line_reads_without_its_zero_width_glyphs():
    src = _pdf(DOCUSIGN)
    _block, line = _line(src, "Carrier: ABC LLC")
    assert line.text == f"{ZW}Carrier: ABC LLC{ZW}"  # the glyphs are still the line's
    _block, joined = _line(src, "Name: Value here")  # a ZW pair between words = a space
    assert ZW in joined.text and " " not in joined.text.split("Value")[0].replace("Name:", "")
    wire = E.text_model_dict(E.page_text_model(src, 0))
    for block in wire["blocks"]:
        for wire_line in block["lines"]:
            chars = "".join(c[0] for span in wire_line["spans"] for c in span["chars"])
            assert chars == wire_line["text"]  # what the editor builds == what it compares
            assert not any(c in IGNORABLE for c in chars)
    carrier = next(l for b in wire["blocks"] for l in b["lines"] if l["text"] == "Carrier: ABC LLC")
    x0, y0, x1, y1 = line.bbox
    assert carrier["bbox"] == {"x_pt": round(x0, 3), "y_pt": round(y0, 3), "w_pt": round(x1 - x0, 3),
                               "h_pt": round(y1 - y0, 3)}  # the target geometry is unchanged
    name = next(l for b in wire["blocks"] for l in b["lines"] if l["text"] == "Name: Value here")
    space = [c for span in name["spans"] for c in span["chars"]][5]
    assert space[0] == " " and space[3] - space[1] > 3.0  # its box is the gap between the words


def test_a_line_with_zero_width_glyphs_is_edited_in_place():
    src = _pdf(DOCUSIGN)
    old_a = _glyph(src, "ABC LLC")
    result = E.apply_ops(src, REFS, [_op(src, "Carrier: ABC LLC", "Carrier: Premier Trucking Group")])
    out = result.pdf_bytes
    for name, text in _readers(out).items():
        assert "Carrier: Premier Trucking Group" in text, name
        assert "ABC" not in text, name
        assert text.index("Premier") < text.index("Name:"), name  # in reading order
    tail = _glyph(out, "Premier")
    assert abs(tail.origin[0] - old_a.origin[0]) <= 0.01 and abs(tail.origin[1] - old_a.origin[1]) <= 0.01
    assert not any(c in IGNORABLE for c in _new_glyph_chars(out))
    x0, y0, x1, y1 = _line(src, "Carrier: ABC LLC")[1].bbox
    assert pixel_diff(render_gray(src), render_gray(out), exclude_pt=[(x0 - 1, y0 - 1, x1 + 120, y1 + 1)]) == 0


def test_an_old_client_echoing_the_raw_text_still_edits():
    """A client that sent `expect_text` and runs WITH the U+200B (the wire
    before this fix): the target resolves, nothing is refused."""
    src = _pdf(DOCUSIGN)
    raw = f"{ZW}Carrier: ABC LLC{ZW}"
    op = _op(src, "Carrier: ABC LLC", f"{ZW}Carrier: XYZ Inc{ZW}", expect=raw)
    problems, _warnings = E.validate_ops(src, REFS, [op])
    assert problems == []
    out = E.apply_ops(src, REFS, [op]).pdf_bytes
    assert all("Carrier: XYZ Inc" in text for text in _readers(out).values())


def test_a_zero_width_pair_between_words_edits_after_the_space():
    src = _pdf(DOCUSIGN)
    old_v = _glyph(src, "Value here")
    out = E.apply_ops(src, REFS, [_op(src, "Name: Value here", "Name: Other value")]).pdf_bytes
    tail = _glyph(out, "Other value")
    # The tail is " Other value" set from the first zero-width glyph: a real
    # space (0.278 em, like the ZW glyph it replaces) puts "O" where "V" was,
    # up to how exactly the two ZW glyphs overlapped (0.012 pt here).
    assert abs(tail.origin[0] - old_v.origin[0]) <= 0.05 and abs(tail.origin[1] - old_v.origin[1]) <= 0.01
    for name, text in _readers(out).items():
        assert "Value here" not in text, name
        # the tail starts with a REAL space glyph: every reader sees the word break
        assert "Name: Other value" in text, name


def test_ignorable_characters_in_new_text_are_not_drawn_nor_refused():
    src = _pdf(DOCUSIGN)
    new = "Carrier:​ ABC﻿ LLC­ and⁠ co‌‍"
    op = _op(src, "Carrier: ABC LLC", new)
    problems, _warnings = E.validate_ops(src, REFS, [op])
    assert problems == []
    out = E.apply_ops(src, REFS, [op]).pdf_bytes
    drawn = _new_glyph_chars(out)
    assert drawn and not any(c in IGNORABLE for c in drawn)
    assert all("Carrier: ABC LLC and co" in text for text in _readers(out).values())


@pytest.mark.parametrize("char", ["‮", "‎", "⁦", "\U000e0041", "\u0007"])
def test_direction_controls_and_other_invisible_characters_are_still_refused(char):
    src = _pdf(DOCUSIGN)
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS, [_op(src, "Carrier: ABC LLC", f"Carrier: {char}ABC LLC")])
    assert caught.value.code == "undrawable_character" and caught.value.status == 422
    assert caught.value.details["codepoint"] == f"U+{ord(char):04X}"


def test_whole_line_delete_takes_the_zero_width_glyphs_too():
    src = _pdf(DOCUSIGN)
    out = E.apply_ops(src, REFS, [_op(src, "Carrier: ABC LLC", None, kind="text.delete")]).pdf_bytes
    for block in E.page_text_model(out, 0).blocks:
        for line in block.lines:
            assert "Carrier" not in line.text
    # no leftover line of zero-width glyphs only
    assert not any(line.text and not line.display_text
                   for block in E.page_text_model(out, 0).blocks for line in block.lines)


# -- 2. old text must be gone ---------------------------------------------------------------


def test_hit_box_is_the_font_box_shrunk_to_80_percent():
    char = text_edit.Char("A", (10.0, 100.0, 20.0, 110.0), (10.0, 108.0))
    assert text_edit.hit_box(char) == pytest.approx((11.0, 101.0, 19.0, 109.0))  # 10 % off each side


def test_a_value_over_a_fill_line_is_removed_and_the_line_kept():
    """The real template, p.2: the free band chosen in the rawdict box lay
    above MuPDF's hit box -- every old digit stayed under the new text."""
    src = _pdf(FILL_LINE)
    result = E.apply_ops(src, REFS, [_op(src, "Phone: 123-4567", "Phone: 999", grow=40)])
    out = result.pdf_bytes
    for name, text in _readers(out).items():
        assert "4567" not in text and "123" not in text, name
        # pdfplumber interleaves the value with the fill line under it (it does so in the source too)
        assert "Phone:999" in "".join(text.replace("_", "").split()), name
    with adapter.Doc(out) as doc:
        glyphs = doc.glyph_origins(0)
    assert len(glyphs.get(("_", "Times-Roman"), [])) == 30  # the fill line is untouched
    assert not any(w.get("code") == "neighbor_glyphs_redrawn" for w in result.warnings)
    excluded = [(r["rect"]["x_pt"] - 1, r["rect"]["y_pt"] - 1, r["rect"]["x_pt"] + r["rect"]["w_pt"] + 1,
                 r["rect"]["y_pt"] + r["rect"]["h_pt"] + 1) for r in result.changed_regions]
    assert pixel_diff(render_gray(src), render_gray(out), exclude_pt=excluded) == 0


def test_a_glyph_left_by_the_first_redaction_gets_a_second_one(monkeypatch):
    """With the old model (the whole rawdict box) the first pass leaves all 8
    glyphs; the check after it removes them with their core band."""
    monkeypatch.setattr(text_edit, "HIT_SHRINK", 1.0)
    src = _pdf(FILL_LINE)
    calls = []
    real = adapter.Doc.redact
    monkeypatch.setattr(adapter.Doc, "redact", lambda self, index, rects: (calls.append(len(rects)), real(self, index, rects)))
    out = E.apply_ops(src, REFS, [_op(src, "Phone: 123-4567", "Phone: 999", grow=40)]).pdf_bytes
    assert calls == [8, 8]  # first pass, then the retry for the 8 glyphs still there
    for name, text in _readers(out).items():
        assert "4567" not in text, name


def test_old_text_that_cannot_be_removed_is_refused_not_committed(monkeypatch):
    monkeypatch.setattr(adapter.Doc, "redact", lambda self, index, rects: None)
    src = _pdf(FILL_LINE)
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS, [_op(src, "Phone: 123-4567", "Phone: 999", grow=40, op_id="keep1")])
    assert caught.value.code == "old_text_not_removed" and caught.value.status == 422
    assert caught.value.details == {"op_id": "keep1", "glyphs": 8}
    assert "123" not in caught.value.message  # no document text in the answer


def test_a_redrawn_neighbour_is_inside_the_changed_regions():
    def draw(pdf, _index):
        pdf.setFont("Helvetica", 60)
        pdf.drawString(100, 600, "W")
        pdf.setFont("Helvetica", 6)
        pdf.drawString(112, 615, "tiny")

    src = reportlab_pdf(draw)
    block = next(b for b in E.page_text_model(src, 0).blocks for line in b.lines if line.text == "tiny")
    line = next(line for line in block.lines if line.text == "tiny")
    op = TextOp(id="d1", kind="text.delete", page_uid=PAGE_UID, block_id=block.id, line_ids=(line.id,),
                expect_text="tiny", bbox=line.bbox)
    result = E.apply_ops(src, REFS, [op])
    assert any(w.get("code") == "neighbor_glyphs_redrawn" for w in result.warnings)
    w_glyph = next(c for b in E.page_text_model(src, 0).blocks for ln in b.lines for _s, c in ln.chars if c.c == "W")
    regions = [(r["rect"]["x_pt"], r["rect"]["y_pt"], r["rect"]["x_pt"] + r["rect"]["w_pt"],
                r["rect"]["y_pt"] + r["rect"]["h_pt"]) for r in result.changed_regions]
    x0, y0, x1, y1 = w_glyph.bbox
    assert any(r[0] <= x0 + 0.01 and r[1] <= y0 + 0.01 and r[2] >= x1 - 0.01 and r[3] >= y1 - 0.01 for r in regions)


# -- 3. `'` / `"` after TL ------------------------------------------------------------------

QUOTE = b"BT /F1 12 Tf 14 TL 72 700 Td (Carrier: ABC LLC) Tj (Next line here) ' (Third line) ' ET"
DQUOTE = b"BT /F1 12 Tf 14 TL 72 700 Td (Carrier: ABC LLC) Tj 1 0.5 (Next line here) \" (Third line) ' ET"


@pytest.mark.parametrize("content", [QUOTE, DQUOTE], ids=["quote", "dquote"])
def test_quote_operators_keep_their_lines_on_the_page(content):
    src = _pdf(content)
    before = {line.display_text: line.baseline for b in E.page_text_model(src, 0).blocks for line in b.lines}
    out = E.apply_ops(src, REFS, [_op(src, "Carrier: ABC LLC", "Carrier: Premier Trucking Group Inc.")]).pdf_bytes
    after = {line.display_text: line.baseline for b in E.page_text_model(out, 0).blocks for line in b.lines}
    for needle in ("Next line here", "Third line"):
        key = next(k for k in after if needle in k)
        assert abs(after[key] - before[next(k for k in before if needle in k)]) <= 0.01
        assert all(needle in text for text in _readers(out).values())
    if content is DQUOTE:  # `"` also set word and character spacing: kept
        next_line = next(ln for b in E.page_text_model(out, 0).blocks for ln in b.lines if "Next" in ln.display_text)
        old = next(ln for b in E.page_text_model(src, 0).blocks for ln in b.lines if "Next" in ln.display_text)
        assert abs(next_line.bbox[2] - old.bbox[2]) <= 0.01


def test_quote_guard_rewrites_exactly_and_only_what_it_must():
    src = _pdf(QUOTE)
    with adapter.Doc(src) as doc:
        assert doc.fix_quote_ops(0) == 2
        assert doc.fix_quote_ops(0) == 0  # idempotent
        data = doc._doc.xref_stream(doc._doc[0].get_contents()[0])
    assert b"'" not in data.replace(b"\\'", b"") and data.count(b"T*") == 2
    plain = _pdf(b"BT /F1 12 Tf 72 700 Td (It's fine) Tj ET")
    with adapter.Doc(plain) as doc:
        before = doc._doc.xref_stream(doc._doc[0].get_contents()[0])
        assert doc.fix_quote_ops(0) == 0  # a quote inside a string is not an operator
        assert doc._doc.xref_stream(doc._doc[0].get_contents()[0]) == before


def test_quote_canary_mupdf_still_breaks_without_the_guard(monkeypatch):
    """Canary: if this starts failing after a PyMuPDF upgrade, MuPDF fixed its
    filter -- `fix_quote_ops` may then be dropped."""
    monkeypatch.setattr(adapter.Doc, "fix_quote_ops", lambda self, index: 0)
    src = _pdf(QUOTE)
    out = E.apply_ops(src, REFS, [_op(src, "Carrier: ABC LLC", "Carrier: Premier Trucking Group Inc.")]).pdf_bytes
    assert "Next line here" not in _readers(out)["pymupdf"]


# -- 4. turned markup elements --------------------------------------------------------------


def test_turned_corners_are_the_drawn_footprint():
    corners = rotated_corners(100, 200, 200, 20, 90)
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    assert min(xs) == pytest.approx(190) and max(xs) == pytest.approx(210)
    assert min(ys) == pytest.approx(110) and max(ys) == pytest.approx(310)
    # clockwise as displayed (y down): the top-left corner goes to the top-right
    assert corners[0] == pytest.approx((210, 110))
    diag = rotated_corners(0, 0, 10, 10, 45)
    assert max(p[0] for p in diag) - min(p[0] for p in diag) == pytest.approx(10 * math.sqrt(2))


@pytest.mark.parametrize("kind", ["textbox", "stamp", "image2"])
def test_a_turned_box_near_the_edge_is_checked_by_what_is_drawn(kind):
    # A 200 x 20 box turned 90°: drawn 20 wide, 200 tall around its centre.
    element = {"kind": kind, "x_pt": 480.0, "y_pt": 300.0, "w_pt": 200.0, "h_pt": 20.0, "rotation_deg": 90}
    points = element_points(element)
    assert all(-0.01 <= x <= 612.01 and -0.01 <= y <= 792.01 for x, y in points)  # was refused: x + w = 680
    outside = {**element, "x_pt": 520.0}  # centre x 620: the turned box does leave the page
    assert not all(-0.01 <= x <= 612.01 for x, _y in element_points(outside))
    upright = {**element, "rotation_deg": 0}
    assert element_points(upright) == [(480.0, 300.0), (680.0, 320.0)]


def test_shapes_are_not_turned_by_rotation_deg():
    element = {"kind": "shape2", "shape": "rect", "x_pt": 10.0, "y_pt": 10.0, "w_pt": 50.0, "h_pt": 5.0,
               "rotation_deg": 90}
    assert element_points(element) == [(10.0, 10.0), (60.0, 15.0)]  # drawn upright (`draw_element`)


# -- 5. splice into a scaled form -----------------------------------------------------------


def test_the_inverse_matrix_keeps_ten_decimals():
    inv = content_splice.inverse((0.75, 0.0, 0.0, 0.75, 20.0, 30.0))
    text = content_splice.matrix_ops(inv)
    assert text.split()[0] == "1.3333333333"
    assert content_splice.matrix_ops((2.0, 0.0, 0.0, 2.0, 50.0, 60.0)) == "2 0 0 2 50 60"


def test_a_tail_in_a_scaled_form_is_spliced_exactly():
    content = b"BT /F1 12 Tf 72 740 Td (Before the form) Tj ET q 0.75 0 0 0.75 20 30 cm /X1 Do Q" \
              b" BT /F1 12 Tf 72 400 Td (After the form) Tj ET"
    form = b"BT /F1 16 Tf 62 700 Td (\240Carrier: ABC LLC\240) Tj 0 -24 Td (Form line two) Tj ET"
    src = _pdf(content, xobject=form)
    new = "Carrier: Premier Trucking Group Inc."
    out = E.apply_ops(src, REFS, [_op(src, "Carrier: ABC LLC", new)]).pdf_bytes
    with pymupdf.open(stream=out, filetype="pdf") as document:
        page_content = b"".join(document.xref_stream(x) for x in document[0].get_contents())
        form_xref = next(int(x[0]) for x in document[0].get_xobjects())
        form_content = document.xref_stream(form_xref)
    assert b"/SWMgs0 gs" not in page_content and b"/SWMgs0 gs" in form_content  # spliced, not appended
    for name, text in _readers(out).items():
        assert text.index("Before the form") < text.index(new) < text.index("Form line two") \
            < text.index("After the form"), name
