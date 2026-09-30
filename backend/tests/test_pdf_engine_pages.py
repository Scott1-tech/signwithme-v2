"""Editor v2 B2: page.insert / delete / move / rotate in the engine."""
from __future__ import annotations

import io

import pytest

from app.services import pdf_engine
from app.services.pdf_engine import PageRef, plan_pages
from tests.editor_v2_helpers import plumber_text, reportlab_pdf
from tests.test_pdf_engine_ops import UIDS, _op

NEW = "pg_0000000000ff"


def _pages(n: int = 3, sizes=None) -> bytes:
    def draw(pdf, index):
        if sizes:
            pdf.setPageSize(sizes[index])
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 300, f"Page {index + 1}")

    return reportlab_pdf(draw, pages=n)


def _refs(n=3):
    return [PageRef(UIDS[i], i + 1) for i in range(n)]


def _texts(pdf_bytes: bytes) -> list[str]:
    from pypdf import PdfReader

    return [plumber_text(pdf_bytes, i).strip() for i in range(len(PdfReader(io.BytesIO(pdf_bytes)).pages))]


def test_insert_delete_move_in_one_draft():
    ops = [
        _op({"id": "i", "kind": "page.insert", "new_page_uid": NEW, "after_page_uid": UIDS[0]}),
        _op({"id": "d", "kind": "page.delete", "page_uid": UIDS[1]}),
        _op({"id": "m", "kind": "page.move", "page_uid": UIDS[2], "after_page_uid": None}),
    ]
    result = pdf_engine.apply_ops(_pages(), _refs(), ops)
    assert result.page_uids == [UIDS[2], UIDS[0], NEW]
    assert _texts(result.pdf_bytes) == ["Page 3", "Page 1", ""]
    assert result.page_count == 3
    assert result.pages[2].has_text is False


def test_insert_same_as_previous_takes_the_previous_page_size_and_explicit_sizes_work():
    source = _pages(2, sizes=[(612, 792), (842, 595)])
    ops = [
        _op({"id": "a", "kind": "page.insert", "new_page_uid": NEW, "after_page_uid": UIDS[1]}),
        _op({"id": "b", "kind": "page.insert", "new_page_uid": "pg_0000000000fe", "after_page_uid": None,
             "size": {"w_pt": 300, "h_pt": 400}}),
    ]
    result = pdf_engine.apply_ops(source, _refs(2), ops)
    sizes = [(p.width_pt, p.height_pt) for p in result.pages]
    assert sizes == [(300.0, 400.0), (612.0, 792.0), (842.0, 595.0), (842.0, 595.0)]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotate_is_absolute(rotation):
    base = pdf_engine.apply_ops(_pages(), _refs(), [_op({"id": "r0", "kind": "page.rotate", "page_uid": UIDS[0],
                                                          "rotation": 90})])
    result = pdf_engine.apply_ops(base.pdf_bytes, _refs(), [
        _op({"id": "r1", "kind": "page.rotate", "page_uid": UIDS[0], "rotation": rotation})
    ])
    assert result.pages[0].rotation == rotation
    assert (result.pages[0].width_pt, result.pages[0].height_pt) == (612.0, 792.0)  # unrotated size


def test_ops_on_a_page_the_draft_deletes_are_skipped_with_a_warning():
    ops = [
        _op({"id": "r", "kind": "redact", "page_uid": UIDS[1], "rects": [{"x_pt": 1, "y_pt": 1, "w_pt": 5, "h_pt": 5}]}),
        _op({"id": "d", "kind": "page.delete", "page_uid": UIDS[1]}),
    ]
    result = pdf_engine.apply_ops(_pages(), _refs(), ops)
    assert {"code": "op_on_removed_page", "op_id": "r", "page_uid": UIDS[1]} in result.warnings
    assert result.page_uids == [UIDS[0], UIDS[2]]


@pytest.mark.parametrize(
    "ops, code",
    [
        ([{"id": "d1", "kind": "page.delete", "page_uid": UIDS[0]},
          {"id": "d2", "kind": "page.delete", "page_uid": UIDS[1]},
          {"id": "d3", "kind": "page.delete", "page_uid": UIDS[2]}], "cannot_delete_last_page"),
        ([{"id": "i", "kind": "page.insert", "new_page_uid": UIDS[1]}], "duplicate_page_uid"),
        ([{"id": "m", "kind": "page.move", "page_uid": "pg_0000000000aa"}], "unknown_page"),
        ([{"id": "i", "kind": "page.insert", "new_page_uid": NEW, "after_page_uid": "pg_0000000000aa"}], "unknown_page"),
        ([{"id": "d", "kind": "page.delete", "page_uid": UIDS[0]},
          {"id": "m", "kind": "page.move", "page_uid": UIDS[0]}], "unknown_page"),
    ],
)
def test_impossible_page_plans_are_refused(ops, code):
    engine_ops = [_op(op) for op in ops]
    with pytest.raises(pdf_engine.EngineError) as caught:
        plan_pages(UIDS, engine_ops)
    assert caught.value.code == code
    problems, _warnings = pdf_engine.validate_ops(_pages(), _refs(), engine_ops)
    assert problems and problems[0]["reason"] == code


def test_a_page_inserted_and_deleted_in_the_same_draft_leaves_no_trace():
    plan = plan_pages(UIDS, [
        _op({"id": "i", "kind": "page.insert", "new_page_uid": NEW, "after_page_uid": UIDS[0]}),
        _op({"id": "d", "kind": "page.delete", "page_uid": NEW}),
    ])
    assert plan.final_uids == UIDS and not plan.inserted and not plan.deleted


def test_move_after_itself_is_refused_and_move_to_the_end_works():
    with pytest.raises(pdf_engine.EngineError):
        plan_pages(UIDS, [_op({"id": "m", "kind": "page.move", "page_uid": UIDS[0], "after_page_uid": UIDS[0]})])
    plan = plan_pages(UIDS, [_op({"id": "m", "kind": "page.move", "page_uid": UIDS[0], "after_page_uid": UIDS[2]})])
    assert plan.final_uids == [UIDS[1], UIDS[2], UIDS[0]]


def test_link_to_a_moved_page_follows_it():
    base = pdf_engine.apply_ops(_pages(), _refs(), [
        _op({"id": "l", "kind": "link.add", "page_uid": UIDS[0],
             "rect": {"x_pt": 72, "y_pt": 80, "w_pt": 50, "h_pt": 20},
             "target": {"type": "page", "page_uid": UIDS[2]}}),
    ])
    moved = pdf_engine.apply_ops(base.pdf_bytes, _refs(), [
        _op({"id": "m", "kind": "page.move", "page_uid": UIDS[2], "after_page_uid": None}),
    ])
    assert moved.page_uids == [UIDS[2], UIDS[0], UIDS[1]]
    links = pdf_engine.page_links(moved.pdf_bytes, 1, {i: u for i, u in enumerate(moved.page_uids)})
    assert [link["target"]["page_uid"] for link in links] == [UIDS[2]]


def test_rotation_schema_accepts_quarter_turns_only():
    from pydantic import TypeAdapter, ValidationError

    from app.schemas.document import ContentOp

    for bad in (45, 360, -90, "90"):
        with pytest.raises(ValidationError):
            TypeAdapter(ContentOp).validate_python(
                {"id": "r", "kind": "page.rotate", "page_uid": UIDS[0], "rotation": bad}
            )
