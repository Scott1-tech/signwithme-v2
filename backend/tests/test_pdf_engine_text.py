"""PDF editor v2 engine, B1: `text.replace` / `text.delete` (EDITOR-V2-PLAN §4,
spike §10.2.1)."""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter

from app.services import pdf_engine as E
from app.services.pdf_engine import adapter
from app.services.pdf_engine.ops import Run, TextOp
from tests.editor_v2_helpers import (
    PAGE_UID,
    all_extractors,
    chrome_style_pdf,
    compact,
    pixel_diff,
    plumber_text,
    render_gray,
    reportlab_pdf,
)

REFS = [E.PageRef(PAGE_UID, 1)]
BLACK = (0.0, 0.0, 0.0)


def _form_pdf() -> bytes:
    def draw(pdf, _index):
        pdf.setFont("Helvetica", 11)
        pdf.drawString(72, 720, "INDEPENDENT CONTRACTOR AGREEMENT")
        pdf.drawString(72, 680, "Full Name:")
        pdf.drawString(200, 680, "_" * 30)
        pdf.drawString(205, 683, "CHARLES FRYE")
        pdf.drawString(72, 600, "Some other line of text here")
        pdf.drawString(72, 560, "Left words")
        pdf.drawString(160, 560, "Right words")

    return reportlab_pdf(draw)


def _line(pdf: bytes, text: str, index: int = 0):
    model = E.page_text_model(pdf, index)
    for block in model.blocks:
        for line in block.lines:
            if line.text == text:
                return block, line
    raise AssertionError(text)


def _op(pdf, text, new=None, *, kind="text.replace", fit="wrap", box=None, size=11.0, op_id="op1",
        color=BLACK, bold=False, family="LiberationSans", index=0, uid=PAGE_UID, expect=None, block_id=None):
    block, line = _line(pdf, text, index)
    common = dict(
        id=op_id, kind=kind, page_uid=uid, block_id=block_id or block.id, line_ids=(line.id,),
        expect_text=text if expect is None else expect, bbox=line.bbox,
    )
    if kind == "text.delete":
        return TextOp(**common)
    return TextOp(
        **common,
        runs=(Run(new, family, size, color, bold, False),),
        box=box,
        fit=fit,
    )


def _wide(pdf, text, extra=200.0):
    _block, line = _line(pdf, text)
    x0, y0, x1, y1 = line.bbox
    return (x0, y0, x1 + extra, y1)


def _plumber_chars(pdf: bytes, index: int = 0) -> list[dict]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return list(document.pages[index].chars)


# -- replace / delete ------------------------------------------------------------------


def test_replace_removes_old_text_in_every_extractor_and_keeps_baseline_and_left_edge():
    src = _form_pdf()
    _block, line = _line(src, "Some other line of text here")
    result = E.apply_ops(src, REFS, [_op(src, "Some other line of text here", "Brand new words",
                                         box=_wide(src, "Some other line of text here"))])
    for text in all_extractors(result.pdf_bytes):
        assert "Some other line" not in text
        assert "Brandnewwords" in compact(text)
    new_chars = [c for c in _plumber_chars(result.pdf_bytes) if c["text"] == "B" and abs(c["x0"] - 72) < 2]
    assert new_chars, "the new text starts where the old one did"
    old_first = line.spans[0].chars[0]
    # pdfplumber: `bottom` - descent... compare via the engine's own model instead.
    after = E.page_text_model(result.pdf_bytes, 0)
    written = [ln for b in after.blocks for ln in b.lines if ln.text.startswith("Brand")]
    assert written
    first = written[0].spans[0].chars[0]
    assert abs(first.origin[0] - old_first.origin[0]) <= 0.01  # Δ left 0
    assert abs(first.origin[1] - old_first.origin[1]) <= 0.01  # Δ baseline 0
    assert result.substituted_from and result.substituted_from[0]["original_font"] == "Helvetica"


def test_replace_leaves_the_rest_of_the_page_untouched():
    src = _form_pdf()
    _block, line = _line(src, "Some other line of text here")
    result = E.apply_ops(src, REFS, [_op(src, "Some other line of text here", "Short")])
    x0, y0, x1, y1 = line.bbox
    diff = pixel_diff(render_gray(src), render_gray(result.pdf_bytes), exclude_pt=[(x0 - 1, y0 - 1, x1 + 1, y1 + 1)])
    assert diff <= 16, diff


def test_delete_keeps_the_fill_line_under_a_typed_value():
    """MuPDF removes every glyph whose FULL font box touches a redaction rect;
    the value sits 3 pt above the `____` line and shares its box height. The
    band is moved into the value's own free space: the underscores stay."""
    src = _form_pdf()
    result = E.apply_ops(src, REFS, [_op(src, "CHARLES FRYE", kind="text.delete")])
    for text in all_extractors(result.pdf_bytes):
        assert "CHARLES" not in text and "FRYE" not in text
    assert plumber_text(result.pdf_bytes).count("_") == 30
    assert not any(w.get("code") == "neighbor_glyphs_redrawn" for w in result.warnings)


def test_replace_on_a_fill_line_is_not_a_collision():
    src = _form_pdf()
    result = E.apply_ops(src, REFS, [_op(src, "CHARLES FRYE", "JOHN SMITH")])
    text = plumber_text(result.pdf_bytes)
    assert text.count("_") == 30
    assert "CHARLES" not in text


def test_partial_replacement_rewrites_only_the_tail():
    src = _form_pdf()
    result = E.apply_ops(src, REFS, [_op(src, "CHARLES FRYE", "CHARLES FRYER", box=_wide(src, "CHARLES FRYE", 20))])
    chars = [c for c in _plumber_chars(result.pdf_bytes) if c["text"] not in "_"]
    fonts = {c["text"]: c["fontname"] for c in chars if c["top"] < 110 and c["x0"] > 200}
    assert "Helvetica" in fonts["C"]  # the kept prefix is the original glyph
    tail = [c for c in chars if c["text"] == "R" and c["x0"] > 280]
    assert tail and "Liberation" in tail[0]["fontname"]  # only the tail is new
    assert compact(plumber_text(result.pdf_bytes).replace("_", "")).count("CHARLESFRYER") == 1


def test_no_white_box_on_a_coloured_background():
    def draw(pdf, _index):
        pdf.setFillColorRGB(0.2, 0.5, 0.9)
        pdf.rect(60, 590, 300, 30, fill=1, stroke=0)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Helvetica", 11)
        pdf.drawString(72, 600, "Cell text inside")

    src = reportlab_pdf(draw)
    _block, line = _line(src, "Cell text inside")
    result = E.apply_ops(src, REFS, [_op(src, "Cell text inside", "Hi")])
    image = render_gray(result.pdf_bytes)
    x0, y0, x1, y1 = line.bbox
    background = render_gray(src).getpixel((int(350 * 2), int((y0 + y1))))
    # Where the removed tail of the old text was (right of "Hi"), the cell colour shows.
    for x_pt in (x1 - 10, x1 - 5):
        pixel = image.getpixel((int(x_pt * 2), int((y0 + y1))))
        assert abs(pixel - background) <= 8, (pixel, background)
        assert pixel < 240  # not white


# -- fit ---------------------------------------------------------------------------------


LONG = "This replacement is far too long to fit on one line of the original box at all"


def test_wrap_that_overflows_is_refused_with_the_size_of_the_problem():
    src = _form_pdf()
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS, [_op(src, "Some other line of text here", LONG)])
    assert caught.value.code == "text_overflow"
    assert caught.value.details["lines_over"] >= 1
    assert caught.value.details["needed_h_pt"] > 0
    assert caught.value.status == 422


def test_shrink_fits_by_reducing_the_size():
    src = _form_pdf()
    result = E.apply_ops(src, REFS, [_op(src, "Some other line of text here", "Some other line of text here, extended",
                                         fit="shrink")])
    assert any(w.get("code") == "text_shrunk" for w in result.warnings)
    sizes = {round(c["size"], 1) for c in _plumber_chars(result.pdf_bytes) if "Liberation" in c["fontname"]}
    assert sizes and max(sizes) < 11.0 and min(sizes) >= 6.0


def test_overflow_writes_everything_and_warns():
    src = _form_pdf()
    result = E.apply_ops(src, REFS, [_op(src, "Some other line of text here", LONG, fit="overflow")])
    assert any(w.get("code") == "text_overflow" for w in result.warnings)
    assert "farto" in compact(plumber_text(result.pdf_bytes)).replace("_", "")


def test_multi_line_box_wraps_inside_the_box():
    src = _form_pdf()
    _block, line = _line(src, "Some other line of text here")
    x0, y0, x1, y1 = line.bbox
    box = (x0, y0, x0 + 150, y0 + 60)
    result = E.apply_ops(src, REFS, [_op(src, "Some other line of text here", LONG[:60], box=box)])
    new = [c for c in _plumber_chars(result.pdf_bytes) if "Liberation" in c["fontname"]]
    assert max(c["x1"] for c in new) <= x0 + 150 + 0.5
    assert len({round(c["top"]) for c in new}) >= 2  # wrapped


def test_collision_with_neighbouring_text_is_refused_or_warned():
    src = _form_pdf()
    box = _wide(src, "Left words", 300)
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS, [_op(src, "Left words", "Left words got much longer", box=box)])
    assert caught.value.code == "text_overflow"
    assert caught.value.details.get("reason") == "collision"
    result = E.apply_ops(src, REFS, [_op(src, "Left words", "Left words got much longer", box=box, fit="overflow")])
    assert any(w.get("code") == "text_overflow" for w in result.warnings)
    # The neighbour never moves: its original glyphs are still where they were.
    right = [c for c in _plumber_chars(result.pdf_bytes) if c["text"] == "R" and "Helvetica" in c["fontname"]]
    assert any(abs(char["x0"] - 160) < 0.01 for char in right)


# -- refusals ----------------------------------------------------------------------------


def test_stale_target_is_a_409():
    src = _form_pdf()
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS, [_op(src, "Some other line of text here", "x", expect="Something else")])
    assert caught.value.code == "stale_target" and caught.value.status == 409
    assert caught.value.details == {"op_id": "op1"}


def test_bbox_is_the_fallback_locator():
    src = _form_pdf()
    result = E.apply_ops(src, REFS, [_op(src, "Some other line of text here", "Moved id", block_id="b99")])
    assert "Movedid" in compact(plumber_text(result.pdf_bytes))


def test_two_ops_on_one_line_are_refused():
    src = _form_pdf()
    ops = [_op(src, "Left words", "A", op_id="a"), _op(src, "Left words", "B", op_id="b")]
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS, ops)
    assert caught.value.code == "duplicate_target"


def test_undrawable_character_is_refused_without_echoing_text():
    src = _form_pdf()
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS, [_op(src, "Left words", "tick ✔")])
    assert caught.value.code == "undrawable_character"
    assert caught.value.details["codepoint"] == "U+2714"
    assert "tick" not in caught.value.message and "tick" not in str(caught.value.details)


def test_scanned_page_is_not_editable():
    from PIL import Image

    image = Image.new("RGB", (850, 1100), (240, 240, 240))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    def draw(pdf, _index):
        from reportlab.lib.utils import ImageReader

        pdf.drawImage(ImageReader(buffer), 0, 0, width=letter[0], height=letter[1])

    src = reportlab_pdf(draw)
    pages, _warnings = E.inspect_pages(src)
    assert pages[0].scanned and not pages[0].has_text
    op = TextOp(id="s1", kind="text.delete", page_uid=PAGE_UID, block_id="b0", line_ids=("b0l0",),
                expect_text="x", bbox=(0, 0, 10, 10))
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(src, REFS, [op])
    assert caught.value.code == "scanned_page_not_editable"


def test_password_protected_pdf_is_refused():
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(_form_pdf())).pages:
        writer.add_page(page)
    writer.encrypt(user_password="secret", owner_password="owner")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(E.EngineError) as caught:
        E.inspect_pages(buffer.getvalue())
    assert caught.value.code == "pdf_encrypted"


def test_garbage_is_pdf_corrupt():
    with pytest.raises(E.EngineError) as caught:
        E.inspect_pages(b"%PDF-1.4\n this is not a pdf at all")
    assert caught.value.code == "pdf_corrupt"


def test_truncated_file_opens_repaired_with_a_warning():
    src = reportlab_pdf(lambda pdf, i: (pdf.setFont("Helvetica", 11), pdf.drawString(72, 700, f"page {i}")), pages=6)
    truncated = src[: int(len(src) * 0.6)]
    pages, warnings = E.inspect_pages(truncated)
    assert pages
    assert {"code": "pdf_repaired"} in warnings


# -- MuPDF `0 0 Td` regression (spike §10.2.1, MANDATORY fix) ----------------------------


def test_td_fix_keeps_chrome_style_pages_intact_after_a_redaction():
    src = chrome_style_pdf()
    _block, line = _line(src, "Header line to delete")
    result = E.apply_ops(src, REFS, [_op(src, "Header line to delete", kind="text.delete")])
    x0, y0, x1, y1 = line.bbox
    diff = pixel_diff(render_gray(src), render_gray(result.pdf_bytes), exclude_pt=[(x0, y0, x1, y1)])
    assert diff <= 16, diff


def test_td_canary_mupdf_still_drops_zero_td(monkeypatch):
    """Canary: without the fix, MuPDF shifts the glyphs after `0 0 Td`. If this
    starts failing after a PyMuPDF upgrade, MuPDF fixed the bug -- re-check the
    spike and consider dropping `fix_zero_td` (EDITOR-V2-PLAN §10.2.1)."""
    monkeypatch.setattr(adapter.Doc, "fix_zero_td", lambda self, index: 0)
    src = chrome_style_pdf()
    _block, line = _line(src, "Header line to delete")
    result = E.apply_ops(src, REFS, [_op(src, "Header line to delete", kind="text.delete")])
    x0, y0, x1, y1 = line.bbox
    diff = pixel_diff(render_gray(src), render_gray(result.pdf_bytes), exclude_pt=[(x0, y0, x1, y1)])
    assert diff > 1000, diff


def test_td_fix_rewrites_only_zero_operands():
    src = chrome_style_pdf()
    with adapter.Doc(src) as doc:
        assert doc.fix_zero_td(0) == 40
        assert doc.fix_zero_td(0) == 0  # idempotent: nothing left to fix


# -- geometry: /Rotate and CropBox != MediaBox ([S3]) ---------------------------------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_page_and_offset_cropbox_keep_coordinates(rotation):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject

    reader = PdfReader(io.BytesIO(_form_pdf()))
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    page.cropbox = RectangleObject([20, 30, 590, 770])
    if rotation:
        page.rotate(rotation)
    buffer = io.BytesIO()
    writer.write(buffer)
    src = buffer.getvalue()
    _block, line = _line(src, "Some other line of text here")
    old = line.spans[0].chars[0]
    result = E.apply_ops(src, REFS, [_op(src, "Some other line of text here", "Rotated fine",
                                         box=_wide(src, "Some other line of text here"))])
    after = E.page_text_model(result.pdf_bytes, 0)
    written = [ln for b in after.blocks for ln in b.lines if ln.text.startswith("Rotated")]
    assert written, [ln.text for b in after.blocks for ln in b.lines]
    first = written[0].spans[0].chars[0]
    assert abs(first.origin[0] - old.origin[0]) <= 0.1
    assert abs(first.origin[1] - old.origin[1]) <= 0.1
    assert result.pages[0].rotation == rotation


# -- collateral glyphs -------------------------------------------------------------------------


def test_unavoidable_neighbour_is_drawn_again():
    def draw(pdf, _index):
        pdf.setFont("Helvetica", 60)
        pdf.drawString(100, 600, "W")
        pdf.setFont("Helvetica", 6)
        pdf.drawString(112, 615, "tiny")

    src = reportlab_pdf(draw)
    result = E.apply_ops(src, REFS, [_op(src, "tiny", kind="text.delete")])
    text = compact(plumber_text(result.pdf_bytes))
    assert "tiny" not in text
    assert "W" in text
    assert any(w.get("code") == "neighbor_glyphs_redrawn" for w in result.warnings)


# -- text model -------------------------------------------------------------------------------


def test_text_model_ids_are_stable_and_compact():
    src = _form_pdf()
    first = E.text_model_dict(E.page_text_model(src, 0))
    E.inspect.clear_cache()
    second = E.text_model_dict(E.page_text_model(src, 0))
    assert first == second
    block = first["blocks"][0]
    assert re.fullmatch(r"b\d+", block["id"])
    assert re.fullmatch(r"b\d+l\d+", block["lines"][0]["id"])
    span = block["lines"][0]["spans"][0]
    assert span["family"] == "LiberationSans"
    assert len(span["chars"][0]) == 5


def test_pymupdf_is_imported_only_by_the_adapter():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"^\s*(import\s+(pymupdf|fitz)\b|from\s+(pymupdf|fitz)\b)", re.MULTILINE)
    offenders = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [str(Path("services/pdf_engine/adapter.py"))]


def test_preview_hides_the_block_and_applies_other_ops():
    src = _form_pdf()
    block, _line_obj = _line(src, "Some other line of text here")
    ops = [_op(src, "Left words", "Changed", op_id="x1")]
    pdf = E.render_page_preview(src, adapter.sha256_hex(src), REFS, PAGE_UID, ops, "digest-1", hide_block=block.id)
    text = compact(plumber_text(pdf))
    assert "Someotherline" not in text
    assert "Changed" in text
    from pypdf import PdfReader

    assert len(PdfReader(io.BytesIO(pdf)).pages) == 1


def test_non_latin_text_is_written_and_extractable():
    src = _form_pdf()
    new = "Ism: Oʻtkir Ғайратов"
    result = E.apply_ops(src, REFS, [_op(src, "Some other line of text here", new,
                                         box=_wide(src, "Some other line of text here"))])
    for text in all_extractors(result.pdf_bytes):
        assert compact(new) in compact(text), text

def test_td_epsilon_is_restored_to_an_exact_zero_after_redaction():
    """The epsilon only has to survive MuPDF's filter; left in, even 1e-4
    moved glyphs across a pixel boundary on the real package. After the
    redaction every zero Td is an exact zero again."""
    src = chrome_style_pdf()
    result = E.apply_ops(src, REFS, [_op(src, "Header line to delete", kind="text.delete")])
    with adapter.Doc(result.pdf_bytes) as doc:
        assert doc.restore_zero_td(0) == 0  # no epsilon left behind
        # 20 of the 40 zero Tds come back as exact zeros (MuPDF writes `0 -0 TD`).
        # The other 20 were each followed by `30 0 Td`, which MuPDF merges into
        # one `30.000122 -0 TD`: 1.2e-4 pt from the original, measured 0 px.
        assert doc.fix_zero_td(0) == 20