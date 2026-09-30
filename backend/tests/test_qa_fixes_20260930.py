"""Regression tests for the three defects found by the QA acceptance run
20260930-021733 (tests/acceptance/runs/20260930-021733/report.md).

1. A partial text edit appended the new tail at the END of the content
   stream: pypdf and PyMuPDF read "Carrier:" in place and "Premier Trucking
   Group Inc." at the end of the page (`content_splice`).
2. ✥ Move of an unchanged text block committed (201) but the text stayed
   where it was (`text_edit.plan_replace` / `moved_by`).
3. DejaVu Sans/Serif and Amiri: PyMuPDF's ToUnicode CMap carried 5-digit hex
   destinations (`<14fd> <14ff> <10300>`) -> pypdf "Odd-length string"
   (`tounicode.repair`, run by `Doc.save`).
"""
from __future__ import annotations

import io

import pymupdf
import pytest

from app.services import fonts as app_fonts
from app.services import pdf_engine as E
from app.services.pdf_engine import adapter, content_splice, text_edit, tounicode
from app.services.pdf_engine.ops import Run, TextOp
from tests.editor_v2_helpers import PAGE_UID, chrome_style_pdf, pixel_diff, render_gray, reportlab_pdf

REFS = [E.PageRef(PAGE_UID, 1)]
REFS3 = [E.PageRef(PAGE_UID, 1), E.PageRef("pg_000000000002", 2), E.PageRef("pg_000000000003", 3)]
OLD_CARRIER = "Carrier: ABC Transportation LLC"
NEW_CARRIER = "Carrier: Premier Trucking Group Inc."
CELL_RGB = (217 / 255, 237 / 255, 1.0)


# -- helpers -------------------------------------------------------------------------------


def _contract() -> bytes:
    """The acceptance contract's page 1 in miniature: a line in a light-blue
    cell, a fill line below, more lines after it; pages 2 and 3 plain."""

    def draw(pdf, index):
        if index == 0:
            pdf.setFillColorRGB(*CELL_RGB)
            pdf.rect(66, 792 - 86 - 20, 300, 20, stroke=0, fill=1)
            pdf.setFillColorRGB(0, 0, 0)
            pdf.setFont("Helvetica-Bold", 14)
            pdf.drawString(72, 792 - 60, "ZZSYNTH ACCEPTANCE AGREEMENT (synthetic)")
            pdf.setFont("Helvetica", 12)
            pdf.drawString(72, 792 - 100, OLD_CARRIER)
            pdf.drawString(72, 792 - 124, "Obsolete clause ZZOBSOLETE-7788 removed.")
            pdf.drawString(72, 792 - 148, "Style target line ZZSTYLE")
            pdf.drawString(72, 792 - 292, "Name: ____________________")
            pdf.drawString(72, 792 - 316, "Movable line ZZMOVE")
        else:
            pdf.setFont("Helvetica", 12)
            pdf.drawString(72, 792 - 60, f"ZZSYNTH page {index + 1} body text.")

    return reportlab_pdf(draw, pages=3)


def _raw_pdf(content: bytes, *, xobject: bytes | None = None) -> bytes:
    """A one-page PDF with exactly `content` as its content stream (Helvetica
    as /F1; optionally a Form XObject /X1)."""
    resources = b"<< /Font << /F1 5 0 R >>" + (b" /XObject << /X1 6 0 R >>" if xobject else b"") + b" >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources " + resources + b" /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    if xobject:
        objects.append(
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >>"
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


def _find(pdf: bytes, text: str, index: int = 0):
    for block in E.page_text_model(pdf, index).blocks:
        for line in block.lines:
            if line.text == text:
                return block, line
    raise AssertionError(text)


def _replace(pdf, old, new, *, box=None, grow=200.0, size=None, family="LiberationSans", color=(0.0, 0.0, 0.0),
             op_id="op1", index=0, uid=PAGE_UID):
    block, line = _find(pdf, old, index)
    x0, y0, x1, y1 = line.bbox
    return TextOp(
        id=op_id, kind="text.replace", page_uid=uid, block_id=block.id, line_ids=(line.id,),
        expect_text=old, bbox=line.bbox,
        runs=(Run(new, family, size or line.spans[0].size, color, False, False),),
        box=box if box is not None else (x0, y0, x1 + grow, y1),
    )


def _norm(text: str) -> str:
    return " ".join(text.split())


def _readers(pdf: bytes, index: int = 0) -> dict[str, str]:
    """pypdf, pdfplumber and PyMuPDF text of one page -- the three readers of
    the acceptance run (it compares whitespace-normalised text)."""
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


def _in_order(pdf: bytes, needle: str, index: int = 0) -> dict[str, bool]:
    return {name: _norm(needle) in text for name, text in _readers(pdf, index).items()}


def _page_content(pdf: bytes, index: int = 0) -> bytes:
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        return b"\n".join(document.xref_stream(x) for x in document[index].get_contents())


def _appended(monkeypatch, pdf, ops, refs=REFS) -> bytes:
    """The same edit with the splice switched off (the pre-fix output)."""
    with monkeypatch.context() as patch:
        patch.setattr(text_edit, "_anchor", lambda kept: None)
        return E.apply_ops(pdf, refs, ops).pdf_bytes


def _first_char(pdf: bytes, needle: str, index: int = 0):
    for block in E.page_text_model(pdf, index).blocks:
        for line in block.lines:
            if needle in line.text:
                start = line.text.index(needle)
                return line.chars[start][1]
    return None


# -- 1. partial edit: the tail is spliced into the content in reading order -------------------


def test_partial_edit_reads_in_order_in_every_reader(monkeypatch):
    src = _contract()
    _block, line = _find(src, OLD_CARRIER)
    result = E.apply_ops(src, REFS3, [_replace(src, OLD_CARRIER, NEW_CARRIER)])
    out = result.pdf_bytes
    assert _in_order(out, NEW_CARRIER) == {"pypdf": True, "pdfplumber": True, "pymupdf": True}
    for text in _readers(out).values():
        assert "ABC Transportation" not in text
        # the line stays between its neighbours (not at the end of the page)
        assert text.index("ZZSYNTH ACCEPTANCE") < text.index(NEW_CARRIER) < text.index("Obsolete clause")
    # no extra content stream: the snippet went INTO the stream after "Carrier: "
    content = _page_content(out)
    assert content.index(b"(Carrier: )Tj") < content.index(b"/SWMgs0 gs") < content.index(b"(Obsolete clause")
    # Δ 0: the tail starts where the replaced text did, on the same baseline
    tail = _first_char(out, "Premier")
    old = line.chars[len("Carrier: ")][1]
    assert abs(tail.origin[0] - old.origin[0]) <= 0.01 and abs(tail.origin[1] - old.origin[1]) <= 0.01
    # the look did not change: same pixels as the appended (pre-fix) output, no white box
    appended = _appended(monkeypatch, src, [_replace(src, OLD_CARRIER, NEW_CARRIER)], REFS3)
    assert pixel_diff(render_gray(appended, scale=4.0), render_gray(out, scale=4.0), scale=4.0) == 0
    x0, y0, x1, y1 = line.bbox
    assert pixel_diff(render_gray(src), render_gray(out), exclude_pt=[(x0, y0, x1 + 150, y1)]) == 0
    # the other pages are byte for byte the same content
    for index in (1, 2):
        assert _page_content(out, index) == _page_content(src, index)


def test_partial_edit_again_and_again_stays_in_order():
    """B1.1 re-edit: the second/third edit's anchor is the spliced text itself."""
    pdf = _contract()
    steps = [(OLD_CARRIER, NEW_CARRIER), (NEW_CARRIER, "Carrier: Premier Trucking Group LLC"),
             ("Carrier: Premier Trucking Group LLC", "Carrier: Premier Trucking Group LLC (US)")]
    style = "Style target line ZZSTYLE"
    for number, (old, new) in enumerate(steps):
        new_style = f"Style target line ZZSTYLE v{number}"
        pdf = E.apply_ops(pdf, REFS3, [_replace(pdf, old, new), _replace(pdf, style, new_style, op_id="op2")]).pdf_bytes
        style = new_style
        assert _in_order(pdf, new) == {"pypdf": True, "pdfplumber": True, "pymupdf": True}, number
        assert all(_in_order(pdf, new_style).values()), number
        for text in _readers(pdf).values():
            assert text.index(new) < text.index("Obsolete clause") < text.index(new_style)


@pytest.mark.parametrize(
    "content",
    [
        # one text object, several lines by Td
        b"BT /F1 12 Tf 72 700 Td (Carrier: ABC LLC) Tj 0 -14 Td (Next line here) Tj 0 -14 Td (Third line) Tj ET",
        # Tm + T* with TL
        b"BT /F1 12 Tf 14 TL 1 0 0 1 72 700 Tm (Carrier: ABC LLC) Tj T* (Next line here) Tj T* (Third line) Tj ET",
        # a scaled CTM
        b"q 0.5 0 0 0.5 10 20 cm BT /F1 24 Tf 124 1360 Td (Carrier: ABC LLC) Tj 0 -28 Td (Next line here) Tj ET Q",
        # character / word spacing and a stroke render mode in force
        b"BT /F1 12 Tf 1.5 Tc 3 Tw 2 Tr 72 700 Td (Carrier: ABC LLC) Tj 0 -14 Td (Next line here) Tj ET",
        # tagged content inside the text object
        b"BT /F1 12 Tf /P <</MCID 0>> BDC 72 700 Td (Carrier: ABC LLC) Tj EMC"
        b" /P <</MCID 1>> BDC 0 -14 Td (Next line here) Tj EMC ET",
    ],
    ids=["td", "tstar", "ctm", "spacing", "bdc"],
)
def test_splice_inside_one_text_object(monkeypatch, content):
    src = _raw_pdf(content)
    new = "Carrier: Premier Trucking Group Inc."
    out = E.apply_ops(src, REFS, [_replace(src, "Carrier: ABC LLC", new, size=None)]).pdf_bytes
    assert b"/SWMgs0 gs" in _page_content(out)  # spliced, not appended
    readers = _readers(out)
    tail = "Premier Trucking Group Inc."
    for name, text in readers.items():
        assert tail in text, name
        assert text.index(tail) < text.index("Next line here"), name
    appended = _appended(monkeypatch, src, [_replace(src, "Carrier: ABC LLC", new, size=None)])
    assert pixel_diff(render_gray(appended, scale=4.0), render_gray(out, scale=4.0), scale=4.0) == 0


def test_mupdf_filter_quote_operator_canary():
    """Was a strict xfail: MuPDF 1.28.2's filter writes `x y Td` after `TL`
    as `x y TD` (TL := -y) and a following `'` as a bare `T*` -- the next
    lines left the page. Guarded since 2026-09-30 by `Doc.fix_quote_ops`
    (the unguarded canary: `test_zw_fixes_20260930.py`)."""
    src = _raw_pdf(b"BT /F1 12 Tf 14 TL 72 700 Td (Carrier: ABC LLC) Tj (Next line here) ' (Third line) ' ET")
    out = E.apply_ops(src, REFS, [_replace(src, "Carrier: ABC LLC", "Carrier: Premier Trucking Group Inc.")]).pdf_bytes
    assert "Next line here" in _readers(out)["pymupdf"]
    assert "Third line" in _readers(out)["pymupdf"]


def test_text_painted_over_later_stays_on_top():
    """Content drawn AFTER the prefix over the tail's place: splicing would put
    the tail under it -- the render check keeps the appended text."""
    src = _raw_pdf(b"BT /F1 12 Tf 72 700 Td (Carrier: ABC LLC) Tj ET 1 1 0 rg 150 690 120 20 re f")
    out = E.apply_ops(src, REFS, [_replace(src, "Carrier: ABC LLC", "Carrier: Premier Trucking Group Inc.")]).pdf_bytes
    assert b"/SWMgs0 gs" not in _page_content(out)
    assert "Premier Trucking Group Inc." in _readers(out)["pdfplumber"]


def test_prefix_inside_a_form_xobject_is_spliced_into_the_form(monkeypatch):
    """Docusign packages draw many lines inside Form XObjects (the real
    template: every partial-editable line of pages 1/2/6/13/21)."""
    content = b"BT /F1 12 Tf 72 740 Td (Before the form) Tj ET q 1 0 0 1 10 -20 cm /X1 Do Q" \
              b" BT /F1 12 Tf 72 600 Td (After the form) Tj ET"
    src = _raw_pdf(content, xobject=b"BT /F1 12 Tf 62 720 Td (Carrier: ABC LLC) Tj 0 -14 Td (Form line two) Tj ET")
    new = "Carrier: Premier Trucking Group Inc."
    out = E.apply_ops(src, REFS, [_replace(src, "Carrier: ABC LLC", new)]).pdf_bytes
    assert b"/SWMgs0 gs" not in _page_content(out)  # not in the page content ...
    for name, text in _readers(out).items():  # ... but in the form, in order
        assert new in text, name
        assert text.index("Before the form") < text.index(new) < text.index("Form line two") \
            < text.index("After the form"), name
    appended = _appended(monkeypatch, src, [_replace(src, "Carrier: ABC LLC", new)])
    assert pixel_diff(render_gray(appended, scale=4.0), render_gray(out, scale=4.0), scale=4.0) == 0


def test_a_form_drawn_twice_is_never_spliced_into():
    """Splicing into a form drawn twice would print the tail twice."""
    content = b"q /X1 Do Q q 1 0 0 1 0 -300 cm /X1 Do Q"
    src = _raw_pdf(content, xobject=b"BT /F1 12 Tf 72 700 Td (Carrier: ABC LLC) Tj ET")
    out = E.apply_ops(src, REFS, [_replace(src, "Carrier: ABC LLC", "Carrier: Premier Trucking Group Inc.")]).pdf_bytes
    text = _readers(out)["pdfplumber"]
    assert text.count("Premier Trucking Group Inc.") == 1


def test_chrome_style_zero_td_guard_still_holds_with_a_splice():
    src = chrome_style_pdf()
    _block, line = _find(src, "BBBBCC")
    op = _replace(src, "BBBBCC", "BBBBCD", grow=50)
    out = E.apply_ops(src, REFS, [op]).pdf_bytes
    assert b"/SWMgs0 gs" in _page_content(out)
    x0, y0, x1, y1 = line.bbox
    assert pixel_diff(render_gray(src), render_gray(out), exclude_pt=[(x0 - 1, y0 - 1, x1 + 10, y1 + 1)]) == 0
    with adapter.Doc(out) as doc:
        assert doc.restore_zero_td(0) == 0  # no epsilon left behind


def test_preview_uses_the_same_splice():
    src = _contract()
    ops = [_replace(src, OLD_CARRIER, NEW_CARRIER)]
    pdf = E.render_page_preview(src, adapter.sha256_hex(src), REFS3, PAGE_UID, ops, "qa-digest-1")
    assert all(_in_order(pdf, NEW_CARRIER).values())


# -- content_splice units ---------------------------------------------------------------------


def test_lexer_keeps_strings_inline_images_and_offsets():
    data = (b"BT /F1 12 Tf 72 700 Td (a \\) ET (b) Tj) Tj ET % comment ET\n"
            b"BI /W 2 /H 1 /BPC 8 /CS /G ID \x00ET\x01 EI q 1 0 0 1 5 5 cm Q")
    ops = content_splice.tokenize(data)
    names = [op.name for op in ops]
    assert names == [b"BT", b"Tf", b"Td", b"Tj", b"ET", b"BI", b"q", b"cm", b"Q"]
    tj = ops[3]
    assert data[tj.start:tj.end] == b"(a \\) ET (b) Tj) Tj"
    assert content_splice.insert(data, [(ops[4].end, b" X"), (ops[4].end, b" Y")]).count(b"ET X Y") == 1


def test_split_restores_the_line_matrix():
    streams = [b"BT /F1 12 Tf 10 TL 2 0 0 2 50 60 Tm (A) Tj 0 -7 Td (B) Tj ET"]
    point = content_splice.splice_point(streams, 0, 4)  # BT Tf TL Tm [Tj] Td Tj ET
    assert point.split and point.tlm == (2.0, 0.0, 0.0, 2.0, 50.0, 60.0)
    assert streams[0][point.offset:].startswith(b"0 -7 Td")
    wrapped = content_splice.wrap(b"q BT ET Q", point, "G")
    assert wrapped.startswith(b"\nET\nq\n") and wrapped.endswith(b"BT\n2 0 0 2 50 60 Tm\n")


def test_no_split_inside_a_clipping_text_object():
    streams = [b"BT 7 Tr /F1 12 Tf 72 700 Td (A) Tj 0 -14 Td (B) Tj ET"]
    point = content_splice.splice_point(streams, 0, 4)
    assert point is not None and not point.split  # after ET, never in the middle


# -- 2. ✥ Move ---------------------------------------------------------------------------------


@pytest.mark.parametrize("dx, dy, new", [(200.0, 0.0, None), (150.218, 0.0, None), (40.0, 30.0, None),
                                         (200.0, 0.0, "Movable line ZZMOVE (moved)")])
def test_move_places_the_whole_block_at_its_new_place(dx, dy, new):
    src = _contract()
    old = "Movable line ZZMOVE"
    _block, line = _find(src, old)
    x0, y0, x1, y1 = line.bbox
    first = line.chars[0][1]
    op = _replace(src, old, new or old, box=(x0 + dx, y0 + dy, x1 + dx + 60, y1 + dy))
    result = E.apply_ops(src, REFS3, [op])
    out = result.pdf_bytes
    moved = _first_char(out, new or old)
    assert moved is not None
    assert abs(moved.origin[0] - (first.origin[0] + dx)) <= 0.01  # left edge + dx exactly
    assert abs(moved.origin[1] - (first.origin[1] + dy)) <= 0.01  # baseline + dy exactly
    assert all(_in_order(out, new or old).values())
    # the old place is empty (the new place does not overlap it in these cases)
    assert x0 + dx > x1 or dy >= y1 - y0
    for block in E.page_text_model(out, 0).blocks:
        for other in block.lines:
            assert not any(x0 - 0.5 <= char.origin[0] <= x1 and abs(char.origin[1] - first.origin[1]) < 0.5
                           for _span, char in other.chars), other.text
    after = render_gray(out)
    ink = sum(1 for x in range(int(x0 * 2), int(x1 * 2)) for y in range(int(y0 * 2), int(y1 * 2))
              if after.getpixel((x, y)) < 128)
    assert ink == 0
    assert result.changed_regions  # the region covers the old and the new place


def test_unmoved_unchanged_block_is_still_a_no_op():
    src = _contract()
    old = "Movable line ZZMOVE"
    _block, line = _find(src, old)
    plan = text_edit.plan_page(E.page_text_model(src, 0), [_replace(src, old, old, box=line.bbox)])
    assert plan.removal == [] and plan.fragments == []


def test_resizing_the_box_is_not_a_move():
    src = _contract()
    old = "Movable line ZZMOVE"
    _block, line = _find(src, old)
    x0, y0, x1, y1 = line.bbox
    op = _replace(src, old, old, box=(x0 + 0.0004, y0 - 0.0004, x1 + 120, y1 + 30))
    assert text_edit.moved_by(op, line.bbox) is None


def test_move_outside_the_page_is_refused_not_dropped():
    src = _contract()
    old = "Movable line ZZMOVE"
    _block, line = _find(src, old)
    x0, y0, x1, y1 = line.bbox
    op = _replace(src, old, old, box=(560.0, y0, 700.0, y1))
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS3, [op])
    assert caught.value.code == "op_outside_page" and caught.value.status == 422
    problems, _warnings = E.validate_ops(src, REFS3, [op])
    assert problems == [{"id": "op1", "reason": "op_outside_page"}]


def test_move_onto_other_text_is_refused_with_a_code():
    src = _contract()
    old = "Movable line ZZMOVE"
    _block, line = _find(src, old)
    target, _l = _find(src, "Name: ____________________")
    x0, y0, x1, y1 = line.bbox
    dy = -24.0  # onto the "Name:" line
    op = _replace(src, old, old, box=(x0, y0 + dy, x1 + 40, y1 + dy))
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS3, [op])
    assert caught.value.code == "text_overflow" and caught.value.details.get("reason") == "collision"


def test_move_through_the_api_moves_the_text(api, contractor_token, uploaded_contract):
    from tests.conftest import auth
    from tests.editor_v2_helpers import commit, doc_url, find_line, first_page_uid, put_draft, session_id, \
        start_draft, text_model

    uid = first_page_uid(api, contractor_token, uploaded_contract)
    model = text_model(api, contractor_token, uploaded_contract, uid)
    title = "INDEPENDENT CONTRACTOR AGREEMENT"
    block, line = find_line(model, title)
    bbox = line["bbox"]
    span = line["spans"][0]
    op = {
        "id": "mv1", "kind": "text.replace", "page_uid": uid,
        "target": {"block_id": block["id"], "line_ids": [line["id"]], "expect_text": title, "bbox": bbox},
        "runs": [{"text": title, "font": span["family"], "size_pt": span["size_pt"], "color": span["color"],
                  "bold": span["bold"]}],
        "box": {**bbox, "x_pt": bbox["x_pt"] + 60.0, "w_pt": bbox["w_pt"] + 10},
    }
    session = session_id()
    started = start_draft(api, contractor_token, uploaded_contract, session)
    assert started.status_code in (200, 201), started.text
    saved = put_draft(api, contractor_token, uploaded_contract, session, started.json()["revision"], [op])
    assert saved.status_code == 200, saved.text
    response = commit(api, contractor_token, uploaded_contract, saved.json()["revision"])
    assert response.status_code == 201, response.text
    new_pdf = api.get(doc_url(uploaded_contract, "/versions/2/file"), headers=auth(contractor_token)).content
    after = text_model(api, contractor_token, uploaded_contract, uid, version_no=2)
    _b, moved = find_line(after, title)
    assert abs(moved["bbox"]["x_pt"] - (bbox["x_pt"] + 60.0)) <= 0.05
    assert abs(moved["spans"][0]["baseline"] - span["baseline"]) <= 0.05
    assert all(_in_order(new_pdf, title).values())


# -- 3. ToUnicode CMaps ------------------------------------------------------------------------


def _cmaps(pdf: bytes) -> list[bytes]:
    out = []
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        for xref in range(1, document.xref_length()):
            if document.xref_get_key(xref, "Type") == ("name", "/Font"):
                kind, value = document.xref_get_key(xref, "ToUnicode")
                if kind == "xref":
                    out.append(document.xref_stream(int(value.split()[0])))
    return out


def test_the_qa_entry_is_repaired_to_surrogate_pairs():
    cmap = (b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
            b"2 beginbfrange\n<0003> <0005> <0020>\n<14fd> <14ff> <10300>\nendbfrange\n"
            b"1 beginbfchar\n<0100> <1d400>\nendbfchar\n"
            b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    assert not tounicode.is_valid(cmap)
    fixed = tounicode.repair(cmap)
    assert fixed is not None and tounicode.is_valid(fixed)
    mapping, valid = tounicode.parse(fixed)
    assert valid
    assert mapping[(0x14FD, 2)] == "\U00010300" and mapping[(0x14FF, 2)] == "\U00010302"
    assert mapping[(0x0100, 2)] == "\U0001D400" and mapping[(0x0004, 2)] == "!"
    assert b"<14FD> <D800DF00>" in fixed
    assert fixed.startswith(cmap[: cmap.index(b"2 beginbfrange")])  # header kept
    assert fixed.endswith(b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    assert tounicode.repair(fixed) is None  # valid -> untouched


@pytest.mark.parametrize("face", sorted(app_fonts.EMBEDDED_FONT_FILES))
def test_every_embedded_face_saves_a_valid_tounicode(face):
    """All 40 faces; the QA run found 10 broken (DejaVu Sans/Serif x 8, Amiri
    and Amiri-Italic)."""
    from pypdf import PdfReader

    text = "Hello ZZTOUNI 123"
    with adapter.Doc.blank(400, 200) as doc:
        doc.draw_text(0, 20, 50, text, str(app_fonts.EMBEDDED_FONT_FILES[face]), 12, (0, 0, 0))
        raw = doc._doc.tobytes()
        pdf = doc.save(subset_fonts=True)
    before = [tounicode.parse(c)[0] for c in _cmaps(raw)]
    cmaps = _cmaps(pdf)
    assert cmaps and all(tounicode.is_valid(c) for c in cmaps)
    assert [tounicode.parse(c)[0] for c in cmaps] == before  # same mapping, valid syntax
    assert text in PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert all(text in t for t in _readers(pdf).values())


def test_non_bmp_text_extracts_after_the_repair():
    from pypdf import PdfReader

    path = str(app_fonts.EMBEDDED_FONT_FILES["DejaVuSerif-Bold"])
    text = "\U0001D400\U0001D401 math"
    with adapter.Doc.blank(400, 200) as doc:
        doc.draw_text(0, 20, 50, text, path, 12, (0, 0, 0))
        pdf = doc.save(subset_fonts=True)
    assert text in PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert all(text in t for t in _readers(pdf).values())


@pytest.mark.parametrize("family", ["DejaVuSans", "DejaVuSerif", "Amiri"])
def test_text_edit_and_final_in_the_affected_families_read_everywhere(family):
    from app.services.final_pipeline import FinalInputs, build_final
    from app.services.pdf_engine.markup import Placement
    from pypdf import PdfReader

    src = _contract()
    _block, line = _find(src, "Style target line ZZSTYLE")
    x0, y0, x1, y1 = line.bbox
    edited = E.apply_ops(src, REFS3, [_replace(src, "Style target line ZZSTYLE", "ZZFONTS edited line",
                                               family=family, size=12.0, box=(x0, y0, x1 + 100, y1 + 6))]).pdf_bytes
    assert all(tounicode.is_valid(c) for c in _cmaps(edited))
    assert "ZZFONTS edited line" in PdfReader(io.BytesIO(edited)).pages[0].extract_text()
    element = {"id": "e1", "kind": "textbox", "page": 1, "x_pt": 72, "y_pt": 400, "w_pt": 300, "h_pt": 40,
               "runs": [{"text": "ZZTEXTBOX final", "font": family, "size_pt": 12, "color": "black"}]}
    from app.schemas.contract import ANNOTATION_MODELS

    element = ANNOTATION_MODELS["textbox"].model_validate(element).model_dump(mode="json")
    final = build_final(FinalInputs(source=edited, v7=[Placement(element, 0)])).pdf_bytes
    assert all(tounicode.is_valid(c) for c in _cmaps(final))
    for needle in ("ZZFONTS edited line", "ZZTEXTBOX final"):
        assert all(needle in t for t in _readers(final).values()), needle
