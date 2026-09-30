"""Editor v2 B3: interactive form fields in the engine (`pdf_engine/forms.py`).

Six types created and read back (PyMuPDF via `field_dicts`, pypdf, pdf.js when
available), radio `/V` + `/AS` as PDF names, name collisions, tab order,
values and their validation, update/remove, pages deleted under fields,
rotated/cropped pages, flattening (0 widgets, the value in 3 extractors, the
`0 0 Td` guard) and the final pipeline.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from pypdf import PdfReader

from app.schemas.document import ContentOp
from app.services import pdf_engine
from app.services.pdf_engine import EngineError, adapter, forms
from app.services.pdf_engine.model import PageRef
from tests.editor_v2_helpers import (
    all_extractors,
    chrome_style_pdf,
    compact,
    pixel_diff,
    render_gray,
    reportlab_pdf,
)

U1 = "pg_000000000001"
U2 = "pg_000000000002"
_OP = TypeAdapter(ContentOp)


def R(x, y, w, h):
    return {"x_pt": x, "y_pt": y, "w_pt": w, "h_pt": h}


def add(name, ftype, rect, tab, page=U1, op_id=None, **extra):
    return {"id": op_id or f"add_{name}_{tab}", "kind": "field.add", "page_uid": page,
            "field": {"name": name, "type": ftype, "rect": rect, "tab_index": tab, **extra}}


def value(name, val, op_id=None):
    return {"id": op_id or f"val_{name}", "kind": "field.value", "name": name, "value": val}


def run(src: bytes, ops: list[dict], pages: int = 1):
    refs = [PageRef(f"pg_{i + 1:012d}", i + 1) for i in range(pages)]
    return pdf_engine.apply_ops(src, refs, [pdf_engine.from_model(_OP.validate_python(op)) for op in ops])


def fields(pdf: bytes, pages: int = 1) -> list[dict]:
    return pdf_engine.field_dicts(pdf, {i: f"pg_{i + 1:012d}" for i in range(pages)})


def blank(pages: int = 1) -> bytes:
    def draw(pdf, index):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 750, f"Form page {index + 1}")

    return reportlab_pdf(draw, pages=pages)


SIX = [
    add("full_name", "text", R(72, 100, 200, 20), 3, required=True, tooltip="Your name", max_len=40,
        font="Carlito", size_pt=11),
    add("notes", "multiline", R(72, 130, 200, 60), 4),
    add("state", "dropdown", R(72, 200, 100, 20), 2, options=["TX", "CA", "NY"], default="CA"),
    add("agree", "checkbox", R(72, 230, 14, 14), 1, export_value="Agreed"),
    add("pay", "radio", R(72, 260, 14, 14), 5, export_value="Weekly"),
    add("pay", "radio", R(120, 260, 14, 14), 6, export_value="Monthly"),
    add("sig", "signature", R(72, 300, 200, 40), 7),
]
VALUES = [
    value("full_name", "John Doe"),
    value("notes", "line one\nline two"),
    value("agree", True),
    value("pay", "Monthly"),
    value("state", "NY"),
]


@pytest.fixture(scope="module")
def six_pdf() -> bytes:
    return run(blank(), SIX + VALUES).pdf_bytes


# -- create + read -------------------------------------------------------------------


def test_six_types_are_created_and_read_back(six_pdf):
    rows = fields(six_pdf)
    assert [(f["name"], f["type"], f["tab_index"]) for f in rows] == [
        ("agree", "checkbox", 1), ("state", "dropdown", 2), ("full_name", "text", 3),
        ("notes", "multiline", 4), ("pay", "radio", 5), ("pay", "radio", 6), ("sig", "signature", 7)]
    by = {(f["name"], f["export_value"]): f for f in rows}
    name = by[("full_name", None)]
    assert name["value"] == "John Doe" and name["required"] and name["tooltip"] == "Your name"
    assert name["max_len"] == 40 and name["font"] == "Carlito" and name["size_pt"] == 11.0
    assert name["rect"] == R(72.0, 100.0, 200.0, 20.0) and name["source"] == "editor" and not name["hidden"]
    assert by[("notes", None)]["value"] == "line one\nline two"
    assert by[("state", None)]["value"] == "NY" and by[("state", None)]["options"] == ["TX", "CA", "NY"]
    assert by[("state", None)]["default"] == "CA"
    assert by[("agree", "Agreed")]["value"] is True
    assert by[("pay", "Weekly")]["value"] == "Monthly" == by[("pay", "Monthly")]["value"]
    assert by[("sig", None)]["value"] is None


def test_pypdf_reads_every_field_with_the_right_value(six_pdf):
    reader = PdfReader(io.BytesIO(six_pdf))
    got = reader.get_fields()
    assert set(got) == {"full_name", "notes", "state", "agree", "pay", "sig"}
    assert got["full_name"]["/V"] == "John Doe" and got["full_name"]["/FT"] == "/Tx"
    assert got["state"]["/V"] == "NY" and got["state"]["/FT"] == "/Ch"
    assert got["agree"]["/V"] == "/Agreed"
    assert got["pay"]["/V"] == "/Monthly" and set(got["pay"]["/_States_"]) == {"/Weekly", "/Monthly"}
    assert got["sig"]["/FT"] == "/Sig"
    assert reader.trailer["/Root"]["/AcroForm"]["/SigFlags"] == 0  # an EMPTY signature field


def test_radio_group_is_one_field_with_name_v_and_as(six_pdf):
    """[S5]: one parent field (/T, /Ff radio, /V a NAME), one kid widget per
    button with /AS its state or /Off, appearance states = export values."""
    reader = PdfReader(io.BytesIO(six_pdf))
    acro = reader.trailer["/Root"]["/AcroForm"]
    parents = [f.get_object() for f in acro["/Fields"] if f.get_object().get("/T") == "pay"]
    assert len(parents) == 1
    parent = parents[0]
    assert parent["/FT"] == "/Btn" and int(parent["/Ff"]) & (1 << 15) and int(parent["/Ff"]) & (1 << 14)
    assert type(parent["/V"]).__name__ == "NameObject" and parent["/V"] == "/Monthly"
    kids = [k.get_object() for k in parent["/Kids"]]
    assert sorted((k["/AS"], sorted(k["/AP"]["/N"].keys())) for k in kids) == [
        ("/Monthly", ["/Monthly", "/Off"]), ("/Off", ["/Off", "/Weekly"])]
    assert all("/T" not in k and "/V" not in k for k in kids)


def _pdfjs_fields(pdf: bytes, tmp_path: Path):
    node = shutil.which("node")
    script = Path(__file__).parent / "_pdfjs_fields.mjs"
    pdfjs = Path(r"C:\PTG\signwithme\frontend\node_modules\pdfjs-dist\legacy\build\pdf.mjs")
    if node is None or not pdfjs.exists():
        pytest.skip("node / pdfjs-dist not available")
    path = tmp_path / "form.pdf"
    path.write_bytes(pdf)
    out = subprocess.run([node, str(script), str(path), str(pdfjs.parent)], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def test_pdfjs_reads_the_fields(six_pdf, tmp_path):
    data = _pdfjs_fields(six_pdf, tmp_path)
    objects = data["fields"]
    assert objects["full_name"][0]["value"] == "John Doe"
    assert objects["state"][0]["value"] == "NY"
    assert objects["agree"][0]["type"] == "checkbox" and objects["agree"][0]["value"] == "Agreed"
    radios = [o for o in objects["pay"] if o["type"] == "radiobutton"]
    assert sorted(o["exportValues"] for o in radios) == ["Monthly", "Weekly"]
    assert {o["value"] for o in radios} == {"Monthly"}
    assert objects["sig"][0]["type"] == "signature"


# -- names, radio groups ---------------------------------------------------------------


def _code(src, ops):
    with pytest.raises(EngineError) as info:
        run(src, ops)
    return info.value.code, info.value.status


def test_name_collisions():
    src = blank()
    assert _code(src, [add("a", "text", R(72, 100, 100, 20), 0),
                       add("a", "text", R(72, 130, 100, 20), 1)]) == ("field_name_taken", 409)
    # a radio button may join a radio group of that name ...
    run(src, [add("r", "radio", R(72, 100, 14, 14), 0, export_value="A"),
              add("r", "radio", R(92, 100, 14, 14), 1, export_value="B")])
    # ... with a NEW export value only, and never a non-radio field's name
    assert _code(src, [add("r", "radio", R(72, 100, 14, 14), 0, export_value="A"),
                       add("r", "radio", R(92, 100, 14, 14), 1, export_value="A")]) == ("radio_value_taken", 409)
    assert _code(src, [add("t", "text", R(72, 100, 100, 20), 0),
                       add("t", "radio", R(92, 130, 14, 14), 1, export_value="A")]) == ("field_name_taken", 409)
    assert _code(src, [add("r", "radio", R(72, 100, 14, 14), 0, export_value="A"),
                       add("r", "checkbox", R(92, 130, 14, 14), 1)]) == ("field_name_taken", 409)


def test_radio_default_and_clearing():
    pdf = run(blank(), [add("r", "radio", R(72, 100, 14, 14), 0, export_value="A"),
                        add("r", "radio", R(92, 100, 14, 14), 1, export_value="B", default=True)]).pdf_bytes
    got = PdfReader(io.BytesIO(pdf)).get_fields()["r"]
    assert got["/DV"] == "/B"
    cleared = run(pdf, [value("r", "B"), value("r", "", op_id="clear")]).pdf_bytes
    assert PdfReader(io.BytesIO(cleared)).get_fields()["r"]["/V"] == "/Off"
    assert {f["value"] for f in fields(cleared)} == {""}


# -- tab order ------------------------------------------------------------------------------


def test_tab_order_sorts_the_page_widgets_and_sets_tabs_s(six_pdf):
    reader = PdfReader(io.BytesIO(six_pdf))
    page = reader.pages[0]
    assert page["/Tabs"] == "/S"
    order = []
    for annot in page["/Annots"]:
        obj = annot.get_object()
        if obj.get("/Subtype") == "/Widget":
            order.append(int(obj["/SWMTab"]))
    assert order == sorted(order) == [1, 2, 3, 4, 5, 6, 7]


def test_other_annotations_stay_before_the_widgets():
    src = run(blank(), [{"id": "l", "kind": "link.add", "page_uid": U1, "rect": R(72, 60, 50, 12),
                         "target": {"type": "uri", "uri": "https://example.com"}}]).pdf_bytes
    pdf = run(src, [add("b", "text", R(72, 100, 100, 20), 2), add("a", "text", R(72, 130, 100, 20), 1)]).pdf_bytes
    annots = [a.get_object() for a in PdfReader(io.BytesIO(pdf)).pages[0]["/Annots"]]
    assert [a["/Subtype"] for a in annots] == ["/Link", "/Widget", "/Widget"]
    assert [a["/T"] for a in annots[1:]] == ["a", "b"]


# -- values -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op,code",
    [
        (value("sig", "x"), "field_not_fillable"),
        (value("ro", "x"), "field_readonly"),
        (value("state", "ZZ"), "invalid_field_value"),
        (value("state", True), "invalid_field_value"),
        (value("full_name", "x" * 41), "invalid_field_value"),
        (value("full_name", "one\ntwo"), "invalid_field_value"),
        (value("full_name", "Привет"), "undrawable_character"),
        (value("agree", "yes"), "invalid_field_value"),
        (value("pay", "Daily"), "invalid_field_value"),
        (value("nope", "x"), "unknown_field"),
    ],
)
def test_value_validation(op, code):
    src = run(blank(), SIX + [add("ro", "text", R(300, 100, 100, 20), 9, readonly=True, default="fixed")]).pdf_bytes
    with pytest.raises(EngineError) as info:
        run(src, [op])
    assert info.value.code == code
    assert "Привет" not in json.dumps(info.value.details, ensure_ascii=False)  # never echoed


def test_value_on_a_field_added_in_the_same_draft_and_winansi_text():
    pdf = run(blank(), [add("n", "text", R(72, 100, 200, 20), 0), value("n", "Café – “ok” €5")]).pdf_bytes
    assert fields(pdf)[0]["value"] == "Café – “ok” €5"


# -- update / remove ----------------------------------------------------------------------------


def test_update_moves_renames_and_keeps_a_valid_value(six_pdf):
    pdf = run(six_pdf, [{"id": "u", "kind": "field.update", "page_uid": U1, "name": "full_name",
                         "field": {"name": "driver_name", "type": "text", "rect": R(300, 400, 150, 18), "tab_index": 0}}]
              ).pdf_bytes
    rows = {f["name"]: f for f in fields(pdf)}
    assert "full_name" not in rows
    assert rows["driver_name"]["value"] == "John Doe" and rows["driver_name"]["rect"] == R(300.0, 400.0, 150.0, 18.0)
    assert fields(pdf)[0]["name"] == "driver_name"  # tab 0 first
    # a value that no longer fits is dropped (max_len)
    pdf2 = run(six_pdf, [{"id": "u", "kind": "field.update", "page_uid": U1, "name": "full_name",
                          "field": {"name": "full_name", "type": "text", "rect": R(72, 100, 200, 20), "tab_index": 3,
                                    "max_len": 3}}]).pdf_bytes
    assert {f["name"]: f for f in fields(pdf2)}["full_name"]["value"] == ""


def test_update_of_a_radio_button_needs_its_export_value_and_keeps_the_choice(six_pdf):
    assert _code(six_pdf, [{"id": "u", "kind": "field.update", "page_uid": U1, "name": "pay",
                            "field": {"name": "pay", "type": "radio", "rect": R(72, 500, 14, 14), "tab_index": 5,
                                      "export_value": "Weekly"}}]) == ("unknown_field", 409)
    pdf = run(six_pdf, [{"id": "u", "kind": "field.update", "page_uid": U1, "name": "pay", "export_value": "Monthly",
                         "field": {"name": "pay", "type": "radio", "rect": R(200, 500, 14, 14), "tab_index": 6,
                                   "export_value": "Month"}}]).pdf_bytes
    got = PdfReader(io.BytesIO(pdf)).get_fields()["pay"]
    assert got["/V"] == "/Month" and set(got["/_States_"]) == {"/Weekly", "/Month"}


def test_remove_a_radio_button_then_the_whole_field(six_pdf):
    pdf = run(six_pdf, [{"id": "r1", "kind": "field.remove", "name": "pay", "export_value": "Monthly"}]).pdf_bytes
    rows = [f for f in fields(pdf) if f["name"] == "pay"]
    assert [(f["export_value"], f["value"]) for f in rows] == [("Weekly", "")]  # the chosen one went
    pdf = run(pdf, [{"id": "r2", "kind": "field.remove", "name": "pay"},
                    {"id": "r3", "kind": "field.remove", "name": "sig"}]).pdf_bytes
    assert {f["name"] for f in fields(pdf)} == {"full_name", "notes", "state", "agree"}
    assert set(PdfReader(io.BytesIO(pdf)).get_fields()) == {"full_name", "notes", "state", "agree"}
    assert _code(pdf, [{"id": "r4", "kind": "field.remove", "name": "pay"}]) == ("unknown_field", 409)


# -- pages ----------------------------------------------------------------------------------------


def test_deleting_a_page_takes_its_fields_along():
    src = run(blank(2), [add("a", "text", R(72, 100, 100, 20), 0, page=U1),
                         add("b", "text", R(72, 100, 100, 20), 1, page=U2)], pages=2).pdf_bytes
    result = run(src, [{"id": "d", "kind": "page.delete", "page_uid": U2}, value("b", "x"),
                       add("c", "text", R(72, 150, 100, 20), 2, page=U2)], pages=2)
    assert {f["name"] for f in fields(result.pdf_bytes)} == {"a"}
    assert set(PdfReader(io.BytesIO(result.pdf_bytes)).get_fields()) == {"a"}
    assert sorted(w["op_id"] for w in result.warnings if w["code"] == "op_on_removed_page") == ["add_c_2", "val_b"]


def test_a_field_on_a_page_inserted_by_the_same_draft():
    new = "pg_0000000000aa"
    result = run(blank(), [{"id": "i", "kind": "page.insert", "new_page_uid": new, "after_page_uid": U1},
                           add("n", "text", R(72, 100, 100, 20), 0, page=new), value("n", "hello")])
    rows = pdf_engine.field_dicts(result.pdf_bytes, {0: U1, 1: new})
    assert [(f["page_uid"], f["value"]) for f in rows] == [(new, "hello")]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rect_on_a_rotated_page_with_a_cropbox(rotation):
    """Field geometry (unrotated, top-left of the CropBox) == /Rect minus the
    CropBox origin, at every /Rotate (the same space as the text model)."""
    import pymupdf  # test-only: build the odd page

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.set_cropbox(pymupdf.Rect(20, 30, 500, 700))
    page.set_rotation(rotation)
    src = doc.tobytes()
    pdf = run(src, [add("f", "text", R(100, 200, 80, 20), 0), value("f", "Hi")]).pdf_bytes
    assert fields(pdf)[0]["rect"] == R(100.0, 200.0, 80.0, 20.0)
    widget = PdfReader(io.BytesIO(pdf)).pages[0]["/Annots"][0].get_object()
    # CropBox [20 92 500 762] in PDF space: x0 = 20 + 100, top = 762 - 200
    assert [float(v) for v in widget["/Rect"]] == [120.0, 542.0, 200.0, 562.0]
    with adapter.Doc(pdf) as opened:
        assert opened.page_info(0).rotation == rotation


def test_op_outside_the_page_is_refused():
    assert _code(blank(), [add("f", "text", R(600, 100, 80, 20), 0)])[0] == "op_outside_page"


# -- validation at draft save ----------------------------------------------------------------------


def test_validate_ops_reports_every_refused_field_op():
    ops = [pdf_engine.from_model(_OP.validate_python(op)) for op in [
        add("a", "text", R(72, 100, 100, 20), 0), add("a", "text", R(72, 130, 100, 20), 1, op_id="dup"),
        value("a", "one\ntwo", op_id="bad"), value("zz", "x", op_id="unknown")]]
    problems, _warnings = pdf_engine.validate_ops(blank(), [PageRef(U1, 1)], ops)
    assert sorted((p["id"], p["reason"]) for p in problems) == [
        ("bad", "invalid_field_value"), ("dup", "field_name_taken"), ("unknown", "unknown_field")]


# -- original AcroForm fields ------------------------------------------------------------------------


def _acroform_pdf() -> bytes:
    buffer = io.BytesIO()
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 750, "Original form")
    pdf.acroForm.textfield(name="Driver Name", x=72, y=700, width=200, height=20, value="")
    pdf.acroForm.checkbox(name="ok", x=72, y=660, size=14, checked=False)
    pdf.save()
    return buffer.getvalue()


def test_original_acroform_fields_are_listed_and_fillable():
    src = _acroform_pdf()
    rows = {f["name"]: f for f in fields(src)}
    assert rows["Driver Name"]["source"] == "original" and rows["Driver Name"]["tab_index"] is None
    assert rows["Driver Name"]["type"] == "text" and rows["ok"]["type"] == "checkbox"
    pdf = run(src, [value("Driver Name", "Jane Roe", op_id="v1"), value("ok", True)]).pdf_bytes
    got = PdfReader(io.BytesIO(pdf)).get_fields()
    assert got["Driver Name"]["/V"] == "Jane Roe" and got["ok"]["/V"] not in (None, "/Off")


def test_ungrouped_same_named_radios_of_an_upload():
    """PyMuPDF (and some producers) write every radio button as its own field
    with the same /T: they can be filled, but not joined."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    for x in (72, 100):
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON
        widget.field_name = "loose"
        widget.rect = pymupdf.Rect(x, 100, x + 14, 114)
        widget.field_value = False
        page.add_widget(widget)
    src = doc.tobytes()
    assert _code(src, [add("loose", "radio", R(140, 100, 14, 14), 0, export_value="Z")]) == ("field_name_taken", 409)
    pdf = run(src, [value("loose", "Yes")]).pdf_bytes
    assert {f["value"] for f in fields(pdf)} == {"Yes"}


def test_hidden_zero_size_widget_is_marked_hidden_and_not_visible():
    """Every Docusign package carries a hidden, zero-size envelope signature
    field: listed as `hidden`, and it does not make a document "have a form"."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    widget.field_name = "ENVELOPEID_X"
    widget.rect = pymupdf.Rect(0, 0, 1, 1)
    page.add_widget(widget)
    xref = next(page.widgets()).xref
    doc.xref_set_key(xref, "F", "134")
    doc.xref_set_key(xref, "Rect", "[0 0 0 0]")
    src = doc.tobytes()
    assert fields(src)[0]["hidden"] is True
    assert pdf_engine.has_visible_widgets(src) is False


# -- flatten --------------------------------------------------------------------------------------------


def test_flatten_leaves_no_widget_and_the_values_are_page_text(six_pdf):
    flat = pdf_engine.flatten_forms(six_pdf)
    assert fields(flat) == []
    reader = PdfReader(io.BytesIO(flat))
    assert "/AcroForm" not in reader.trailer["/Root"] and not reader.get_fields()
    for text in all_extractors(flat):
        packed = compact(text)
        assert "JohnDoe" in packed and "lineone" in packed and "linetwo" in packed and "NY" in packed
        assert "SIGN" not in packed  # an empty signature box is a plain frame
    plain = blank()
    assert pdf_engine.flatten_forms(plain) is plain  # nothing to flatten: same bytes


def test_flatten_does_not_move_chrome_style_text():
    """`bake` appends to the content; the `0 0 Td` guard (spike §10.2.1) is not
    needed: 0 px changed outside the fields."""
    src = chrome_style_pdf()
    pdf = run(src, [add("f", "text", R(400, 100, 150, 20), 0), value("f", "Hello"),
                    add("s", "signature", R(400, 150, 150, 40), 1)]).pdf_bytes
    flat = pdf_engine.flatten_forms(pdf)
    exclude = [(400, 100, 550, 120), (400, 150, 550, 190)]
    assert pixel_diff(render_gray(src), render_gray(flat), exclude_pt=exclude) == 0
    assert pixel_diff(render_gray(src), render_gray(flat)) > 0  # the fields ARE drawn


def test_final_pipeline_flattens_and_keeps_the_legacy_path(monkeypatch):
    from app.services.final_pipeline import FinalInputs, build_final
    from tests.test_final_pipeline import _freeze

    _freeze(monkeypatch)
    pdf = run(blank(), [add("f", "text", R(72, 100, 200, 20), 0), value("f", "Filled In")]).pdf_bytes
    final = build_final(FinalInputs(source=pdf, metadata={"title": "t"})).pdf_bytes
    assert fields(final) == []
    assert all("FilledIn" in compact(t) for t in all_extractors(final))
    # a source without a visible widget: exactly the builder's own output
    from app.services.pdf_builder import PdfBuilder

    legacy = blank()
    assert build_final(FinalInputs(source=legacy, metadata={"title": "t"})).pdf_bytes == \
        PdfBuilder().build_final_contract(legacy, [], [], title="t").pdf_bytes


def test_audit_stats_are_counts_only():
    stats = run(blank(), SIX + VALUES).fields
    assert stats == {"total": 12, "by_kind": {"field.add": 7, "field.value": 5},
                     "by_type": {"checkbox": 1, "dropdown": 1, "multiline": 1, "radio": 2, "signature": 1, "text": 1}}
    assert "John" not in json.dumps(stats)


def test_forms_module_imports_no_pymupdf():
    source = Path(forms.__file__).read_text(encoding="utf-8")
    assert "import pymupdf" not in source and "import fitz" not in source
