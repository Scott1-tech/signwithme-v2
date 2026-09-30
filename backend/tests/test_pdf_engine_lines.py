"""PDF editor v2, B1.1: logical lines and geometric block ids (`lines.assemble`).

Live E2E found that a partial edit (`Carrier: ABC ...` -> `Carrier: Premier
...`) left the text model with the kept prefix and the new tail as two lines in
two blocks, renumbering every later block. These tests lock the fix."""
from __future__ import annotations

import pytest
from reportlab.pdfbase.pdfmetrics import stringWidth

from app.services import pdf_engine as E
from app.services.pdf_engine.lines import RawBlock, assemble
from app.services.pdf_engine.model import Char, Line, Span
from app.services.pdf_engine.ops import Run, TextOp
from tests.conftest import auth
from tests.editor_v2_helpers import (
    PAGE_UID,
    all_extractors,
    compact,
    first_page_uid,
    pixel_diff,
    render_gray,
    reportlab_pdf,
)

REFS = [E.PageRef(PAGE_UID, 1)]
BLACK = (0.0, 0.0, 0.0)
CARRIER = "Carrier: ABC Transportation LLC"


def _model(pdf: bytes):
    return E.page_text_model(pdf, 0)


def _find(model, text: str):
    hits = [(block, line) for block in model.blocks for line in block.lines if line.text == text]
    assert len(hits) == 1, [line.text for block in model.blocks for line in block.lines]
    return hits[0]


def _texts(model) -> list[str]:
    return [line.text for block in model.blocks for line in block.lines]


def _replace(pdf: bytes, text: str, new: str, *, extra: float = 200.0, op_id: str = "op1") -> E.EngineResult:
    block, line = _find(_model(pdf), text)
    x0, y0, x1, y1 = line.bbox
    op = TextOp(
        id=op_id, kind="text.replace", page_uid=PAGE_UID, block_id=block.id, line_ids=(line.id,),
        expect_text=text, bbox=line.bbox, runs=(Run(new, "LiberationSans", 11.0, BLACK, False, False),),
        box=(x0, y0, x1 + extra, y1),
    )
    return E.apply_ops(pdf, REFS, [op])


def _form() -> bytes:
    def draw(pdf, _index):
        pdf.setFont("Helvetica", 11)
        pdf.drawString(72, 740, "CARRIER DETAILS")
        pdf.drawString(72, 700, CARRIER)
        pdf.drawString(72, 686, "MC: 123456")
        pdf.drawString(72, 640, "Footer note")

    return reportlab_pdf(draw)


def _ids(model) -> list[tuple[str, list[str], str]]:
    return [(block.id, [line.id for line in block.lines], block.text) for block in model.blocks]


# -- the live E2E case --------------------------------------------------------------------


def test_partial_edit_leaves_one_line_in_one_block_and_other_ids_alone():
    src = _form()
    before = _model(src)
    block, line = _find(before, CARRIER)
    result = _replace(src, CARRIER, "Carrier: Premier Trucking Group Inc.")
    after = _model(result.pdf_bytes)
    new_block, new_line = _find(after, "Carrier: Premier Trucking Group Inc.")
    assert (new_block.id, new_line.id) == (block.id, line.id)
    assert len(after.blocks) == len(before.blocks)
    assert "Carrier: " not in _texts(after) and "Premier Trucking Group Inc." not in _texts(after)
    assert [item for item in _ids(after) if item[0] != block.id] == [item for item in _ids(before) if item[0] != block.id]
    # the kept prefix is the original glyphs, the tail the substitute face
    fonts = [span.font for span in new_line.spans]
    assert len(fonts) == 2 and fonts[0] == "Helvetica" and "Liberation" in fonts[1]


def test_an_edited_line_is_edited_again_with_one_op():
    src = _form()
    _block, original = _find(_model(src), CARRIER)
    first = _replace(src, CARRIER, "Carrier: Premier Trucking Group Inc.")
    second = _replace(first.pdf_bytes, "Carrier: Premier Trucking Group Inc.", "Carrier: Premier Trucking Group LLC")
    after = _model(second.pdf_bytes)
    _block, line = _find(after, "Carrier: Premier Trucking Group LLC")
    assert line.id == original.id
    head = line.chars[0][1]
    assert abs(head.origin[0] - original.chars[0][1].origin[0]) <= 0.01  # Δ left 0
    assert abs(line.baseline - original.baseline) <= 0.01  # Δ baseline 0
    for text in all_extractors(second.pdf_bytes):
        assert "Inc." not in text
        assert "PremierTruckingGroupLLC" in compact(text), text  # every new glyph visible
    x0, y0, _x1, y1 = original.bbox
    diff = pixel_diff(render_gray(src), render_gray(second.pdf_bytes), exclude_pt=[(x0 - 1, y0 - 1, x0 + 300, y1 + 1)])
    assert diff == 0, diff  # nothing outside the line moved


def test_second_edit_on_a_page_does_not_reuse_the_subset_font():
    """Regression: the first edit's font is subset on save; `insert_font` under
    the same resource name reused that subset and wrote every glyph it lacked as
    GID 0 (invisible, U+0000)."""
    src = _form()
    first = _replace(src, "MC: 123456", "MC: 777")
    second = _replace(first.pdf_bytes, "Footer note", "Zebra quiz jumps")
    for text in all_extractors(second.pdf_bytes):
        assert "Zebraquizjumps" in compact(text), text
        assert "\x00" not in text
    _find(_model(second.pdf_bytes), "Zebra quiz jumps")


def test_partial_edit_inside_a_paragraph_keeps_the_paragraph_one_block():
    lines = ["First line of the paragraph", "Second line: old value", "Third line of the paragraph"]

    def draw(pdf, _index):
        pdf.setFont("Helvetica", 11)
        text = pdf.beginText(72, 700)
        text.setLeading(14)
        for line in lines:
            text.textLine(line)
        pdf.drawText(text)
        pdf.drawString(72, 600, "Another block")

    src = reportlab_pdf(draw)
    before = _model(src)
    block, line = _find(before, lines[1])
    result = _replace(src, lines[1], "Second line: brand new value", extra=100.0)
    after = _model(result.pdf_bytes)
    new_block, new_line = _find(after, "Second line: brand new value")
    assert (new_block.id, new_line.id) == (block.id, line.id)
    assert [ln.text for ln in new_block.lines] == [lines[0], "Second line: brand new value", lines[2]]
    assert len(after.blocks) == len(before.blocks)


def test_known_limit_full_replacement_of_one_paragraph_line_splits_the_block():
    """Documented B2 limit: the redaction of a WHOLE line splits MuPDF's text
    object, and MuPDF's grouping is kept. The text and its order are right;
    the paragraph is no longer one block."""
    lines = ["First line of the paragraph", "Second line of the paragraph", "Third line of the paragraph"]

    def draw(pdf, _index):
        pdf.setFont("Helvetica", 11)
        text = pdf.beginText(72, 700)
        text.setLeading(14)
        for line in lines:
            text.textLine(line)
        pdf.drawText(text)

    src = reportlab_pdf(draw)
    result = _replace(src, lines[1], "Replaced middle", extra=0.0)
    after = _model(result.pdf_bytes)
    assert _texts(after) == [lines[0], "Replaced middle", lines[2]]
    assert len(after.blocks) > 1


# -- what must stay apart -----------------------------------------------------------------


def test_two_columns_stay_separate():
    def draw(pdf, _index):
        pdf.setFont("Helvetica", 11)
        for row in range(6):
            pdf.drawString(72, 700 - row * 14, f"Left column row {row}")
            pdf.drawString(320, 700 - row * 14, f"Right column row {row}")

    model = _model(reportlab_pdf(draw))
    texts = _texts(model)
    for row in range(6):
        assert f"Left column row {row}" in texts
        assert f"Right column row {row}" in texts
    assert not any("Left" in text and "Right" in text for text in texts)


def _cells(order, gap_em: float, ruled: bool, size: int = 11) -> bytes:
    """One table row; cells written in `order` (stream order != reading order,
    with other text between, so MuPDF itself cannot join them)."""
    cells = ("CLASS", "TYPE", "MILES")
    xs, x = [], 72.0
    for cell in cells:
        xs.append(x)
        x += stringWidth(cell, "Helvetica", size) + gap_em * size

    def draw(pdf, _index):
        pdf.setFont("Helvetica", size)
        for n, i in enumerate(order):
            pdf.drawString(xs[i], 700, cells[i])
            pdf.drawString(72, 600 - 20 * n, f"filler {n}")
            if ruled and i:
                border = xs[i] - gap_em * size / 2
                pdf.line(border, 690, border, 715)

    return reportlab_pdf(draw)


def test_table_cells_stay_separate():
    """Ruled cells whose texts nearly touch (0.4 x size, the word-gap range),
    and unruled ones at 0.8 x size (the real package's closest cell pair)."""
    for pdf in (_cells((2, 0, 1), 0.4, ruled=True), _cells((2, 0, 1), 0.8, ruled=False)):
        texts = _texts(_model(pdf))
        for cell in ("CLASS", "TYPE", "MILES"):
            assert cell in texts, texts
    # control: the same unruled row at a word gap IS one line
    assert "CLASS TYPE MILES" in _texts(_model(_cells((2, 0, 1), 0.3, ruled=False)))


def _raw_line(text: str, x: float, baseline: float, size: float = 11.0, width: float = 6.0) -> Line:
    chars = [Char(c, (x + i * width, baseline - 0.8 * size, x + (i + 1) * width, baseline + 0.2 * size),
                  (x + i * width, baseline)) for i, c in enumerate(text)]
    span = Span("Helvetica", size, 0, 0, 0.8, -0.2, chars, (x, baseline - 0.8 * size, x + len(text) * width,
                                                           baseline + 0.2 * size))
    return Line("", span.bbox, (1.0, 0.0), [span])


def test_a_whitespace_only_piece_is_not_joined():
    """A checkbox glyph mapped to a space next to its label (real package p.4)
    must not end up under an edit of the label."""
    label = _raw_line("Yes", 72, 700)
    box = _raw_line(" ", 72 + 18 + 2, 701.5, size=10.0)
    blocks = assemble([RawBlock([label], label.bbox), RawBlock([box], box.bbox)])
    assert [b.text for b in blocks] == ["Yes", " "] or [b.text for b in blocks] == [" ", "Yes"]


def test_a_gap_without_a_space_glyph_gets_a_synthetic_space():
    left = _raw_line("Name:", 72, 700)
    right = _raw_line("Premier", 72 + 30 + 4, 700)  # 4 pt = 0.36 x size, no space glyph
    blocks = assemble([RawBlock([right], right.bbox), RawBlock([left], left.bbox)])
    assert len(blocks) == 1 and blocks[0].text == "Name: Premier"
    line = blocks[0].lines[0]
    assert [span.synthetic for span in line.spans] == [False, True, False]
    assert line.id == "b0l0"


def test_chrome_style_words_are_one_line_in_one_block():
    """Chrome/Skia (Docusign) writes word by word: `(A) Tj 0 0 Td <dx> 0 Td (B) Tj`."""
    size = 11
    label = "Carrier:"
    dx = stringWidth(label, "Helvetica", size) + stringWidth(" ", "Helvetica", size)

    def draw(pdf, _index):
        pdf.setFont("Helvetica", size)
        pdf.drawString(72, 740, "Header")
        pdf._code.append(f"BT /F1 {size} Tf 72 700 Td ({label}) Tj 0 0 Td {dx:.3f} 0 Td (ABC) Tj ET")
        pdf._code.append(f"BT /F1 {size} Tf 72 660 Td (Other) Tj ET")
        # the same line's last word, written LATER in the stream
        tail_x = 72 + dx + stringWidth("ABC ", "Helvetica", size)
        pdf._code.append(f"BT /F1 {size} Tf {tail_x:.3f} 700 Td (LLC) Tj ET")

    model = _model(reportlab_pdf(draw))
    block, line = _find(model, "Carrier: ABC LLC")
    assert len(block.lines) == 1
    assert [b.id for b in model.blocks] == ["b0", "b1", "b2"]


def test_the_wire_model_marks_the_synthetic_space():
    from app.services.pdf_engine.model import PageText

    left = _raw_line("Name:", 72, 700)
    right = _raw_line("Premier", 72 + 30 + 4, 700)
    blocks = assemble([RawBlock([left], left.bbox), RawBlock([right], right.bbox)])
    wire = E.text_model_dict(PageText(612, 792, 0, blocks, True, False))
    line = wire["blocks"][0]["lines"][0]
    assert line["text"] == "Name: Premier"
    assert [span["synthetic"] for span in line["spans"]] == [False, True, False]


# -- ids -------------------------------------------------------------------------------------


def test_block_ids_follow_reading_order_not_stream_order():
    def draw(pdf, _index):
        pdf.setFont("Helvetica", 11)
        for y, text in ((500, "Bottom"), (700, "Top"), (600, "Middle")):
            pdf.drawString(72, y, text)

    model = _model(reportlab_pdf(draw))
    assert [(b.id, b.text) for b in model.blocks] == [("b0", "Top"), ("b1", "Middle"), ("b2", "Bottom")]
    assert [b.lines[0].id for b in model.blocks] == ["b0l0", "b1l0", "b2l0"]


def test_ids_are_stable_across_repeated_edits():
    src = _form()
    before = _ids(_model(src))
    first = _replace(src, "MC: 123456", "MC: 654321")
    second = _replace(first.pdf_bytes, "Footer note", "Footer changed")
    after = _model(second.pdf_bytes)
    assert [(bid, lids) for bid, lids, _t in _ids(after)] == [(bid, lids) for bid, lids, _t in before]


# -- expect_text / stale_target with joined lines ----------------------------------------------


def _edited() -> bytes:
    return _replace(_form(), CARRIER, "Carrier: Premier Trucking Group Inc.").pdf_bytes


def test_expect_text_of_a_piece_with_a_foreign_bbox_is_stale():
    pdf = _edited()
    block, line = _find(_model(pdf), "Carrier: Premier Trucking Group Inc.")
    op = TextOp(id="s", kind="text.delete", page_uid=PAGE_UID, block_id=block.id, line_ids=(line.id,),
                expect_text="Carrier: ", bbox=line.bbox)  # the whole line's bbox, not the piece's
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(pdf, REFS, [op])
    assert caught.value.code == "stale_target"


def test_an_op_saved_against_a_piece_still_resolves_to_exactly_that_piece():
    """A draft saved before B1.1 targets the tail as its own line (old ids)."""
    pdf = _edited()
    _block, line = _find(_model(pdf), "Carrier: Premier Trucking Group Inc.")
    tail = [char for _span, char in line.chars][len("Carrier: "):]
    bbox = (tail[0].bbox[0], min(c.bbox[1] for c in tail), tail[-1].bbox[2], max(c.bbox[3] for c in tail))
    op = TextOp(id="old", kind="text.replace", page_uid=PAGE_UID, block_id="b9", line_ids=("b9l0",),
                expect_text="Premier Trucking Group Inc.", bbox=bbox,
                runs=(Run("Premier Trucking Group LLC", "LiberationSans", 11.0, BLACK, False, False),),
                box=(bbox[0], bbox[1], bbox[2] + 100, bbox[3]))
    result = E.apply_ops(pdf, REFS, [op])
    _find(_model(result.pdf_bytes), "Carrier: Premier Trucking Group LLC")


def test_whole_joined_line_with_a_wrong_expect_text_is_stale():
    pdf = _edited()
    block, line = _find(_model(pdf), "Carrier: Premier Trucking Group Inc.")
    op = TextOp(id="s", kind="text.delete", page_uid=PAGE_UID, block_id=block.id, line_ids=(line.id,),
                expect_text=CARRIER, bbox=line.bbox)
    with pytest.raises(E.EngineError) as caught:
        E.apply_ops(pdf, REFS, [op])
    assert caught.value.code == "stale_target" and caught.value.details == {"op_id": "s"}


# -- compare slots carry the page uid (plan §5.4) ------------------------------------------


def test_compare_slots_carry_the_page_uid(api, contractor_token, uploaded_contract):
    url = f"/api/contracts/{uploaded_contract}/compare"
    legacy = api.get(url, headers=auth(contractor_token)).json()
    assert legacy["signature_slots"] and all(slot["page_uid"] is None for slot in legacy["signature_slots"])
    uid = first_page_uid(api, contractor_token, uploaded_contract)  # creates the document
    body = api.get(url, headers=auth(contractor_token)).json()
    on_page_1 = [slot for slot in body["signature_slots"] if slot["page"] == 1]
    assert on_page_1 and all(slot["page_uid"] == uid for slot in on_page_1)


def test_preview_hides_an_op_saved_with_an_old_block_id():
    """`?hide=<block>` drops the op that edits that block even when the op was
    saved against the pre-B1.1 ids (it resolves by bbox)."""
    from app.services.pdf_engine import adapter
    from tests.editor_v2_helpers import plumber_text

    src = _form()
    block, line = _find(_model(src), "MC: 123456")
    op = TextOp(id="old", kind="text.replace", page_uid=PAGE_UID, block_id="b99", line_ids=("b99l0",),
                expect_text="MC: 123456", bbox=line.bbox,
                runs=(Run("MC: 999999", "LiberationSans", 11.0, BLACK, False, False),))
    pdf = E.render_page_preview(src, adapter.sha256_hex(src), REFS, PAGE_UID, [op], "digest-old", hide_block=block.id)
    text = compact(plumber_text(pdf))
    assert "999999" not in text and "123456" not in text
