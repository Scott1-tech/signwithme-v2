"""Pydantic ops (`app.schemas.document`) -> plain engine dataclasses.

The engine never sees pydantic models, and ops stored in a draft (JSON) are
re-validated through the same schema before they reach it.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import TypeAdapter

from app.schemas.document import ContentOp, Rect as RectModel

Rect = tuple[float, float, float, float]

_CONTENT_OP = TypeAdapter(ContentOp)

TEXT_KINDS = ("text.replace", "text.delete")
PAGE_KINDS = ("page.insert", "page.delete", "page.move", "page.rotate")
LINK_KINDS = ("link.add", "link.update", "link.remove")
FIELD_KINDS = ("field.add", "field.update", "field.remove", "field.value")


@dataclass(frozen=True)
class Run:
    text: str
    family: str
    size: float
    color: tuple[float, float, float]
    bold: bool
    italic: bool


@dataclass(frozen=True)
class TextOp:
    id: str
    kind: str  # "text.replace" | "text.delete"
    page_uid: str
    block_id: str
    line_ids: tuple[str, ...]
    expect_text: str
    bbox: Rect
    runs: tuple[Run, ...] = ()
    box: Rect | None = None
    align: str = "left"
    line_height: float | None = None
    fit: str = "wrap"


@dataclass(frozen=True)
class RedactOp:
    id: str
    kind: str  # "redact"
    page_uid: str
    rects: tuple[Rect, ...]
    fill: str  # black | white | none


@dataclass(frozen=True)
class ImageRemoveOp:
    id: str
    kind: str  # "image.remove"
    page_uid: str
    xref: int
    bbox: Rect


@dataclass(frozen=True)
class PageOp:
    id: str
    kind: str  # page.insert | page.delete | page.move | page.rotate
    page_uid: str | None = None  # the page acted on (None for insert)
    new_page_uid: str | None = None
    after_page_uid: str | None = None
    size: object = None  # "same_as_previous" | (w, h) | None
    rotation: int | None = None


@dataclass(frozen=True)
class LinkOp:
    id: str
    kind: str  # link.add | link.update | link.remove
    page_uid: str
    link_id: str | None = None
    rect: Rect | None = None
    target_type: str | None = None  # uri | page
    uri: str | None = None
    target_page_uid: str | None = None
    target_y: float | None = None


def _rect(model: RectModel | None) -> Rect | None:
    if model is None:
        return None
    return (model.x_pt, model.y_pt, model.x_pt + model.w_pt, model.y_pt + model.h_pt)


def _rgb(hex_color: str) -> tuple[float, float, float]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _text_op(op) -> TextOp:
    target = op.target
    common = dict(
        id=op.id,
        kind=op.kind,
        page_uid=op.page_uid,
        block_id=target.block_id,
        line_ids=tuple(target.line_ids),
        expect_text=target.expect_text,
        bbox=_rect(target.bbox),
    )
    if op.kind == "text.delete":
        return TextOp(**common)
    return TextOp(
        **common,
        runs=tuple(
            Run(
                text=run.text,
                family=run.font,
                size=float(run.size_pt),
                color=_rgb(run.color),
                bold=bool(run.bold),
                italic=bool(run.italic),
            )
            for run in op.runs
        ),
        box=_rect(op.box),
        align=op.align,
        line_height=op.line_height,
        fit=op.fit,
    )


def from_model(op):
    kind = op.kind
    if kind in TEXT_KINDS:
        return _text_op(op)
    if kind == "redact":
        return RedactOp(op.id, kind, op.page_uid, tuple(_rect(r) for r in op.rects), op.fill)
    if kind == "image.remove":
        return ImageRemoveOp(op.id, kind, op.page_uid, int(op.xref), _rect(op.bbox))
    if kind == "page.insert":
        size = op.size if isinstance(op.size, str) else (float(op.size.w_pt), float(op.size.h_pt))
        return PageOp(op.id, kind, new_page_uid=op.new_page_uid, after_page_uid=op.after_page_uid, size=size)
    if kind == "page.delete":
        return PageOp(op.id, kind, page_uid=op.page_uid)
    if kind == "page.move":
        return PageOp(op.id, kind, page_uid=op.page_uid, after_page_uid=op.after_page_uid)
    if kind == "page.rotate":
        return PageOp(op.id, kind, page_uid=op.page_uid, rotation=int(op.rotation))
    if kind in LINK_KINDS:
        target = getattr(op, "target", None)
        return LinkOp(
            op.id,
            kind,
            op.page_uid,
            link_id=getattr(op, "link_id", None),
            rect=_rect(getattr(op, "rect", None)),
            target_type=target.type if target is not None else None,
            uri=getattr(target, "uri", None),
            target_page_uid=getattr(target, "page_uid", None),
            target_y=getattr(target, "y_pt", None),
        )
    if kind in FIELD_KINDS:
        from app.services.pdf_engine.forms import FieldOp

        return FieldOp(
            id=op.id,
            kind=kind,
            page_uid=getattr(op, "page_uid", None),
            name=getattr(op, "name", None) if kind != "field.add" else None,
            export_value=getattr(op, "export_value", None),
            spec=_field_spec(op.field) if kind in ("field.add", "field.update") else None,
            value=getattr(op, "value", None),
        )
    raise ValueError(f"unknown op kind {kind!r}")  # pragma: no cover - schema is closed


def _field_spec(model):
    from app.services.pdf_engine.forms import FieldSpec

    return FieldSpec(
        name=model.name,
        type=model.type,
        rect=_rect(model.rect),
        required=bool(model.required),
        readonly=bool(model.readonly),
        tooltip=model.tooltip,
        default=model.default,
        options=tuple(model.options) if model.options is not None else None,
        export_value=model.export_value,
        font=model.font,
        size_pt=model.size_pt,
        max_len=model.max_len,
        tab_index=int(model.tab_index),
    )


def parse_stored(ops: list[dict]) -> list:
    """Stored (JSON) ops -> validated pydantic models, in order."""
    return [_CONTENT_OP.validate_python(op) for op in ops or []]


def from_stored(ops: list[dict]) -> list:
    return [from_model(op) for op in parse_stored(ops)]


def text_ops(ops: list) -> list[TextOp]:
    return [op for op in ops if isinstance(op, TextOp)]
