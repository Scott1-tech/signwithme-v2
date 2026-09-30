"""PDF editor v2 -- the API contract (EDITOR-V2-PLAN Â§3, Â§6). FROZEN at B1-0.

Mirror: `frontend/src/api/document.ts`. A change here is a change there.

Conventions:
* geometry: PDF points, top-left origin, UNROTATED page space, relative to
  the CropBox's top-left corner (same as `signatures.bbox`, annotations v5);
* every request model is strict (`extra="forbid"`): an unknown key is a 422,
  never silently dropped;
* ops are a discriminated union on `kind`. B1 accepts `text.replace` and
  `text.delete` ONLY; every other kind of Â§3 is added by B2/B3 -- until then it
  is an unknown tag (422);
* `layer` is never taken from the client: the server derives it from `kind`;
* `revision` travels in the BODY (the client helper has no header parameter);
* nothing the user typed is ever echoed in an error (`app.main` scrubs 422s).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, WrapValidator, field_validator, model_validator

from app.schemas.contract import ANNOTATION_MODELS, AnnotationElement

# -- limits (Â§3) ----------------------------------------------------------------

MAX_OPS = 2000
MAX_BODY_BYTES = 2 * 1024 * 1024  # 413 above this
MAX_TEXT_CHARS = 5000  # per text op, all runs together
MAX_MARKUP = 500
MAX_RUNS = 200
MAX_TARGET_LINES = 200
TEXT_SIZE_MIN_PT = 4.0
TEXT_SIZE_MAX_PT = 144.0
LINE_HEIGHT_MIN = 0.8
LINE_HEIGHT_MAX = 3.0
LEASE_SECONDS = 120
LEASE_RENEW_SECONDS = 45

OP_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
PAGE_UID_PATTERN = r"^pg_[0-9a-f]{12}$"
BLOCK_ID_PATTERN = r"^b[0-9]{1,6}$"
LINE_ID_PATTERN = r"^b[0-9]{1,6}l[0-9]{1,6}$"
SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
REQUEST_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"

# Families new text can be set in: the embedded families of `GET /api/fonts`
# (B1: Liberation; B2 adds Arimo, Carlito, Caladea, DejaVu Sans/Serif, Amiri-Latin).
FontFamilyId = Literal[
    "LiberationSans", "LiberationSerif", "LiberationMono",
    "Arimo", "Carlito", "Caladea", "DejaVuSans", "DejaVuSerif", "Amiri",
]
FitMode = Literal["wrap", "shrink", "overflow"]
Align = Literal["left", "center", "right", "justify"]

_finite = {"allow_inf_nan": False}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rect(_Strict):
    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    w_pt: float = Field(gt=0, **_finite)
    h_pt: float = Field(gt=0, **_finite)


class TextRunStyle(_Strict):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    font: FontFamilyId
    size_pt: float = Field(ge=TEXT_SIZE_MIN_PT, le=TEXT_SIZE_MAX_PT, **_finite)
    color: str = Field(pattern=HEX_COLOR_PATTERN)
    bold: StrictBool = False
    italic: StrictBool = False


class TextTarget(_Strict):
    """What the op edits, as the client saw it. If the block/lines no longer
    hold `expect_text` (NFC) the op is refused with 409 `stale_target`; `bbox`
    is the fallback locator."""

    block_id: str = Field(pattern=BLOCK_ID_PATTERN)
    line_ids: list[Annotated[str, Field(pattern=LINE_ID_PATTERN)]] = Field(
        min_length=1, max_length=MAX_TARGET_LINES
    )
    expect_text: str = Field(max_length=MAX_TEXT_CHARS * 2)
    bbox: Rect

    @field_validator("line_ids")
    @classmethod
    def _unique_lines(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("line_ids must be unique")
        return value


class TextReplaceOp(_Strict):
    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["text.replace"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    target: TextTarget
    runs: list[TextRunStyle] = Field(min_length=1, max_length=MAX_RUNS)
    box: Rect | None = None
    align: Align = "left"
    line_height: float | None = Field(default=None, ge=LINE_HEIGHT_MIN, le=LINE_HEIGHT_MAX, **_finite)
    fit: FitMode = "wrap"

    @field_validator("runs")
    @classmethod
    def _text_budget(cls, value: list[TextRunStyle]) -> list[TextRunStyle]:
        total = sum(len(run.text) for run in value)
        if total > MAX_TEXT_CHARS:
            raise ValueError(f"at most {MAX_TEXT_CHARS} characters per text op (got {total})")
        return value


class TextDeleteOp(_Strict):
    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["text.delete"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    target: TextTarget


# -- B2: redaction, images, pages, links ------------------------------------------

MAX_REDACT_RECTS = 200
PAGE_SIZE_MIN_PT = 36.0
PAGE_SIZE_MAX_PT = 14400.0
MAX_URI_CHARS = 2048
LINK_ID_PATTERN = r"^lk[0-9]{1,9}$"
_URI_SCHEMES = ("http", "https", "mailto")


class RedactOp(_Strict):
    """REAL redaction: the text, image pixels and fully covered line art under
    the rects are removed from the file (not painted over). `fill` is what is
    left in their place: a black or white box, or nothing."""

    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["redact"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    rects: list[Rect] = Field(min_length=1, max_length=MAX_REDACT_RECTS)
    fill: Literal["black", "white", "none"] = "black"


class ImageRemoveOp(_Strict):
    """Remove an original image: `xref` as listed by `.../images`, `bbox` where
    it was drawn (a mismatch is 409 `stale_target`)."""

    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["image.remove"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    xref: int = Field(ge=1, le=10**8)
    bbox: Rect


class PageSize(_Strict):
    w_pt: float = Field(ge=PAGE_SIZE_MIN_PT, le=PAGE_SIZE_MAX_PT, **_finite)
    h_pt: float = Field(ge=PAGE_SIZE_MIN_PT, le=PAGE_SIZE_MAX_PT, **_finite)


class PageInsertOp(_Strict):
    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["page.insert"]
    new_page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    # None = insert before the first page.
    after_page_uid: str | None = Field(default=None, pattern=PAGE_UID_PATTERN)
    size: Literal["same_as_previous"] | PageSize = "same_as_previous"


class PageDeleteOp(_Strict):
    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["page.delete"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)


class PageMoveOp(_Strict):
    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["page.move"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    after_page_uid: str | None = Field(default=None, pattern=PAGE_UID_PATTERN)


class PageRotateOp(_Strict):
    """ABSOLUTE rotation (the page's /Rotate after the commit)."""

    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["page.rotate"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    rotation: Literal[0, 90, 180, 270]


def _validate_uri(value: str) -> str:
    """http(s) with a host, or mailto: with an address. Nothing else
    (javascript:, file:, data:, relative ...), no whitespace or controls."""
    from urllib.parse import urlsplit

    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError("the link must not contain spaces or control characters")
    parts = urlsplit(value)
    scheme = (parts.scheme or "").lower()
    if scheme not in _URI_SCHEMES:
        raise ValueError("only http, https and mailto links are allowed")
    if scheme in ("http", "https"):
        if not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError("an http(s) link needs a host (and no user:password)")
    else:
        address = parts.path
        if not re.fullmatch(r"[^@\s/]{1,64}@[^@\s/]{1,255}", address or ""):
            raise ValueError("a mailto link needs one e-mail address")
    return value


class LinkTargetUri(_Strict):
    type: Literal["uri"]
    uri: str = Field(min_length=1, max_length=MAX_URI_CHARS)

    @field_validator("uri")
    @classmethod
    def _uri(cls, value: str) -> str:
        return _validate_uri(value)


class LinkTargetPage(_Strict):
    """A page of THIS document, by its stable uid (a removed target page
    removes the link, with a warning)."""

    type: Literal["page"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    y_pt: float | None = Field(default=None, ge=0, **_finite)


LinkTarget = Annotated[Union[LinkTargetUri, LinkTargetPage], Field(discriminator="type")]


class LinkAddOp(_Strict):
    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["link.add"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    rect: Rect
    target: LinkTarget


class LinkUpdateOp(_Strict):
    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["link.update"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    link_id: str = Field(pattern=LINK_ID_PATTERN)
    rect: Rect
    target: LinkTarget


class LinkRemoveOp(_Strict):
    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["link.remove"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    link_id: str = Field(pattern=LINK_ID_PATTERN)


# -- B3: interactive form fields -------------------------------------------------

FIELD_NAME_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"
# A radio button's / checkbox's "on" state is a PDF name: the same safe set.
EXPORT_VALUE_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"
FieldType = Literal["text", "multiline", "dropdown", "radio", "checkbox", "signature"]
MAX_FIELD_OPTIONS = 200
MAX_OPTION_CHARS = 128
MAX_TOOLTIP_CHARS = 256
MAX_FIELD_VALUE_CHARS = 5000
FIELD_SIZE_MIN_PT = 4.0
FIELD_SIZE_MAX_PT = 72.0
MAX_TAB_INDEX = 100_000
TEXT_FIELD_TYPES = ("text", "multiline")
# How an op names an EXISTING field: an original AcroForm field's (qualified)
# name may hold spaces and punctuation, so any printable text up to 256.
FIELD_REF_PATTERN = r"^[^\x00-\x1f\x7f]{1,256}$"


class FieldDef(_Strict):
    """One form field (one widget; a radio GROUP is several `FieldDef`s with
    the same `name`, each with its own `export_value`).

    * `rect`: pt, top-left, unrotated page space (like every other op);
    * `default`: text/multiline/dropdown -> a string (dropdown: one of
      `options`); checkbox/radio -> a bool (radio: THIS button is the
      default choice); signature -> none;
    * `options`: dropdown only (required there);
    * `export_value`: radio (required, unique in its group) and checkbox
      (optional, default "Yes"); the value a filled form carries;
    * `font` / `size_pt`: text, multiline and dropdown; ignored on the button
      and signature types. Widgets are drawn with the metric-compatible
      standard face (Sans -> Helvetica, Serif -> Times, Mono -> Courier);
      `size_pt` None = auto size;
    * `max_len`: text / multiline only;
    * `tab_index`: tab order (lower first) -- the page's widgets are ordered
      by it and the page gets `/Tabs /S`.
    """

    name: str = Field(pattern=FIELD_NAME_PATTERN)
    type: FieldType
    rect: Rect
    required: StrictBool = False
    readonly: StrictBool = False
    tooltip: str | None = Field(default=None, max_length=MAX_TOOLTIP_CHARS)
    default: StrictBool | str | None = Field(default=None)
    options: list[Annotated[str, Field(min_length=1, max_length=MAX_OPTION_CHARS)]] | None = Field(
        default=None, min_length=1, max_length=MAX_FIELD_OPTIONS
    )
    export_value: str | None = Field(default=None, pattern=EXPORT_VALUE_PATTERN)
    font: FontFamilyId | None = None
    size_pt: float | None = Field(default=None, ge=FIELD_SIZE_MIN_PT, le=FIELD_SIZE_MAX_PT, **_finite)
    max_len: int | None = Field(default=None, ge=1, le=MAX_FIELD_VALUE_CHARS)
    tab_index: int = Field(ge=0, le=MAX_TAB_INDEX)

    @field_validator("default")
    @classmethod
    def _default_len(cls, value):
        if isinstance(value, str) and len(value) > MAX_FIELD_VALUE_CHARS:
            raise ValueError(f"at most {MAX_FIELD_VALUE_CHARS} characters")
        return value

    @field_validator("options")
    @classmethod
    def _unique_options(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("options must be unique")
        return value

    @model_validator(mode="after")
    def _by_type(self) -> "FieldDef":
        kind = self.type
        if kind == "dropdown":
            if not self.options:
                raise ValueError("a dropdown needs options")
        elif self.options is not None:
            raise ValueError("options are for a dropdown only")
        if kind == "radio" and self.export_value is None:
            raise ValueError("a radio button needs an export_value")
        if kind not in ("radio", "checkbox") and self.export_value is not None:
            raise ValueError("export_value is for a radio button or a checkbox only")
        if kind not in TEXT_FIELD_TYPES and self.max_len is not None:
            raise ValueError("max_len is for a text field only")
        if self.default is not None:
            if kind in ("radio", "checkbox"):
                if not isinstance(self.default, bool):
                    raise ValueError("the default of a radio button or checkbox is true/false")
            elif kind == "signature":
                raise ValueError("a signature field has no default")
            else:
                if not isinstance(self.default, str):
                    raise ValueError("the default of this field is a string")
                if kind == "dropdown" and self.default not in (self.options or []) and self.default != "":
                    raise ValueError("the default must be one of the options")
                if self.max_len is not None and len(self.default) > self.max_len:
                    raise ValueError("the default is longer than max_len")
        return self


class FieldAddOp(_Strict):
    """Create a field on `page_uid`. A name already in use is 409
    `field_name_taken` -- except a radio button joining an existing radio
    group of that name (its `export_value` must be new in the group:
    409 `radio_value_taken`). LAYER structure: review stays."""

    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["field.add"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    field: FieldDef


class FieldUpdateOp(_Strict):
    """Replace a field's definition (and position: `page_uid` + `field.rect`).
    `name` = the field's CURRENT name (default: `field.name`, i.e. no rename);
    `export_value` picks the button of a radio group. The current value is
    kept when it is still valid for the new definition. LAYER structure."""

    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["field.update"]
    page_uid: str = Field(pattern=PAGE_UID_PATTERN)
    name: str | None = Field(default=None, pattern=FIELD_REF_PATTERN)
    export_value: str | None = Field(default=None, pattern=EXPORT_VALUE_PATTERN)
    field: FieldDef


class FieldRemoveOp(_Strict):
    """Remove a field (all its widgets); with `export_value`, only that
    button of a radio group. LAYER structure."""

    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["field.remove"]
    name: str = Field(pattern=FIELD_REF_PATTERN)
    export_value: str | None = Field(default=None, pattern=EXPORT_VALUE_PATTERN)


class FieldValueOp(_Strict):
    """Fill a field. text/multiline: a string (multiline may hold newlines);
    dropdown: one of its options or "" (clear); checkbox: true/false; radio:
    the chosen button's export value or "" (none). A signature field is not
    filled here (422 `field_not_fillable`), nor a read-only one (422
    `field_readonly`). LAYER content: re-opens a past review."""

    id: str = Field(pattern=OP_ID_PATTERN)
    kind: Literal["field.value"]
    name: str = Field(pattern=r"^[^\x00-\x1f\x7f]{1,256}$")
    value: StrictBool | Annotated[str, Field(max_length=MAX_FIELD_VALUE_CHARS)] | list[
        Annotated[str, Field(max_length=MAX_OPTION_CHARS)]
    ]

    @field_validator("value")
    @classmethod
    def _list_len(cls, value):
        if isinstance(value, list) and len(value) > MAX_FIELD_OPTIONS:
            raise ValueError(f"at most {MAX_FIELD_OPTIONS} values")
        return value


FIELD_KINDS = ("field.add", "field.update", "field.remove", "field.value")

ContentOp = Annotated[
    Union[
        TextReplaceOp, TextDeleteOp, RedactOp, ImageRemoveOp,
        PageInsertOp, PageDeleteOp, PageMoveOp, PageRotateOp,
        LinkAddOp, LinkUpdateOp, LinkRemoveOp,
        FieldAddOp, FieldUpdateOp, FieldRemoveOp, FieldValueOp,
    ],
    Field(discriminator="kind"),
]

# Layer of every kind the server knows (Â§3). Never accepted from the client.
LAYER_CONTENT = "content"
LAYER_STRUCTURE = "structure"
LAYER_MARKUP_CONTENT = "markup-content"
LAYER_MARKUP = "markup"
OP_LAYERS: dict[str, str] = {
    "text.replace": LAYER_CONTENT,
    "text.delete": LAYER_CONTENT,
    "redact": LAYER_CONTENT,
    "image.remove": LAYER_CONTENT,
    "page.insert": LAYER_CONTENT,
    "page.delete": LAYER_CONTENT,
    "page.move": LAYER_CONTENT,
    "page.rotate": LAYER_STRUCTURE,
    "link.add": LAYER_STRUCTURE,
    "link.update": LAYER_STRUCTURE,
    "link.remove": LAYER_STRUCTURE,
    # B3 (§3 layer table): filling a field changes what is signed; adding,
    # moving or removing an (empty) field does not.
    "field.add": LAYER_STRUCTURE,
    "field.update": LAYER_STRUCTURE,
    "field.remove": LAYER_STRUCTURE,
    "field.value": LAYER_CONTENT,
}
# Markup kinds whose change re-opens review (Â§11 Q1). Legacy (v4-v6) names
# now; the v7 names (`textbox`, `stamp`, `image2`) join them in B2.
MARKUP_CONTENT_KINDS = frozenset(
    {"textbox", "whiteout", "stamp", "image", "image2", "strikeout", "text", "date"}
)


def op_layer(kind: str) -> str:
    return OP_LAYERS[kind]


def _unique_ops(value: list) -> list:
    seen: set[str] = set()
    for op in value:
        if op.id in seen:
            raise ValueError(f"op id {op.id!r} was given more than once")
        seen.add(op.id)
    return value


# -- markup (server-side annotations) -----------------------------------------


def _validate_markup_element(value: Any, handler):
    """A legacy annotation element (v4-v6 wire, `schemas.contract`) with
    `page_uid` in place of `page`. Validated by the SAME per-kind model the
    finalize body uses, so markup stored here is always finalize-able; stored
    back exactly as sent (keys the client set, plus `page_uid`)."""
    if isinstance(value, dict):
        element = dict(value)
        if "page" in element:
            raise ValueError("markup elements carry page_uid, not page")
        page_uid = element.pop("page_uid", None)
        if not isinstance(page_uid, str):
            raise ValueError("page_uid is required")
        if not re.fullmatch(PAGE_UID_PATTERN, page_uid):
            raise ValueError("page_uid is malformed")
        kind = element.get("kind")
        model = ANNOTATION_MODELS.get(kind) if isinstance(kind, str) else None
        if model is None:
            AnnotationElement.model_validate(element)  # raises missing / literal_error on kind
            raise ValueError("unknown kind")  # pragma: no cover
        parsed = model.model_validate({**element, "page": 1})
        dumped = parsed.model_dump(mode="json", exclude_unset=True)
        dumped.pop("page", None)
        dumped["page_uid"] = page_uid
        return dumped
    AnnotationElement.model_validate(value)
    raise ValueError("a markup element must be an object")  # pragma: no cover


MarkupElement = Annotated[dict, WrapValidator(_validate_markup_element)]


def _unique_markup(value: list[dict]) -> list[dict]:
    seen: set[str] = set()
    for element in value:
        if element["id"] in seen:
            raise ValueError(f"markup id {element['id']!r} was given more than once")
        seen.add(element["id"])
    return value


class MarkupPutIn(_Strict):
    revision: int = Field(ge=0)
    elements: list[MarkupElement] = Field(max_length=MAX_MARKUP)

    @field_validator("elements")
    @classmethod
    def _element_ids(cls, value):
        return _unique_markup(value)


class MarkupOut(BaseModel):
    revision: int
    elements: list[dict]


class MarkupSavedOut(BaseModel):
    revision: int
    review_reset: bool = False


# -- drafts ---------------------------------------------------------------------


class DraftStartIn(_Strict):
    base_version_no: int = Field(ge=1)
    client_session_id: str = Field(pattern=SESSION_ID_PATTERN)
    take_over: StrictBool = False


class DraftPutIn(_Strict):
    revision: int = Field(ge=0)
    client_session_id: str = Field(pattern=SESSION_ID_PATTERN)
    ops: list[ContentOp] = Field(max_length=MAX_OPS)
    # None = "no markup in this draft" (markup stays as saved on the document).
    markup: list[MarkupElement] | None = Field(default=None, max_length=MAX_MARKUP)

    @field_validator("ops")
    @classmethod
    def _op_ids(cls, value):
        return _unique_ops(value)

    @field_validator("markup")
    @classmethod
    def _markup_ids(cls, value):
        return None if value is None else _unique_markup(value)


class DraftLeaseIn(_Strict):
    revision: int = Field(ge=0)
    client_session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)


class CommitIn(_Strict):
    revision: int = Field(ge=0)
    commit_request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    discard_signatures: StrictBool = False


class DraftOut(BaseModel):
    revision: int
    base_version_no: int
    ops: list[dict]
    markup: list[dict] | None
    lease_expires_at: datetime
    is_mine: bool
    owner_name: str | None


class DraftSavedOut(BaseModel):
    revision: int
    saved_at: datetime
    lease_expires_at: datetime
    warnings: list[dict] = []


class LeaseOut(BaseModel):
    lease_expires_at: datetime


# -- document / versions / pages ------------------------------------------------


class DraftSummary(BaseModel):
    owner_name: str | None
    is_mine: bool
    lease_expires_at: datetime
    revision: int


class Capabilities(BaseModel):
    can_edit: bool
    can_edit_markup: bool
    reason: str | None = None
    # B3 (§11 Q2): may start a draft that only FILLS form fields
    # (`field.value`); true for every editor and for the contractor.
    can_fill: bool = False


class VersionOut(BaseModel):
    version_no: int
    kind: str
    file_hash: str
    page_count: int
    content_changed: bool
    review_reset: bool
    created_by_name: str | None
    created_at: datetime
    op_summary: dict


class DocumentOut(BaseModel):
    document_id: str
    current_version: VersionOut
    versions_count: int
    markup_revision: int
    draft: DraftSummary | None
    capabilities: Capabilities
    warnings: list[dict] = []


class DocumentSummaryOut(BaseModel):
    """What `GET /api/contracts/{id}` carries about the editor document."""

    document_id: str
    current_version_no: int
    versions_count: int
    draft_active: bool


class PageOut(BaseModel):
    page_uid: str
    position: int
    origin_page: int | None
    rotation: int
    width_pt: float
    height_pt: float
    has_text: bool
    scanned: bool


class TextSpanOut(BaseModel):
    font: str
    # B1 answer, Liberation only (kept so a B1 client never meets a family it
    # cannot map); metric-compatible with the original.
    family: Literal["LiberationSans", "LiberationSerif", "LiberationMono"]
    # B2: the substitution map's family (Calibri -> Carlito, Arial -> Arimo, ...).
    substitute_family: FontFamilyId | None = None
    size_pt: float
    color: str
    bold: bool
    italic: bool
    baseline: float
    # True: a space the server put between two joined pieces of one logical
    # line (B1.1); no glyph of the PDF behind it.
    synthetic: bool = False
    # [[c, x0, y0, x1, y1], ...] -- compact: a page carries thousands.
    chars: list[list]


class TextLineOut(BaseModel):
    id: str
    bbox: Rect
    # What the user sees: zero-width format characters (U+200B ..., see
    # `pdf_engine.model.IGNORABLE`) left out -- in `text` and in the spans'
    # `chars` alike. `bbox` is the glyphs' own (unchanged).
    text: str
    spans: list[TextSpanOut]


class TextBlockOut(BaseModel):
    id: str
    bbox: Rect
    editable: bool
    reason: str | None
    lines: list[TextLineOut]


class TextModelOut(BaseModel):
    page_uid: str
    version_no: int
    file_hash: str
    width_pt: float
    height_pt: float
    rotation: int
    has_text: bool
    scanned: bool
    blocks: list[TextBlockOut]


class FieldOut(BaseModel):
    """`GET .../versions/{no}/fields`: one entry per WIDGET (a radio group is
    one entry per button, same `name`), in tab order. `FieldDef` + where it is
    + its current value.

    * `source`: "editor" (made with `field.add`) or "original" (an AcroForm
      field the uploaded PDF already had -- e.g. Docusign's hidden envelope
      signature field: `hidden` true, zero-size rect);
    * `value`: text/multiline/dropdown a string ("" = empty), checkbox a bool,
      radio the group's chosen export value ("" = none), signature null;
    * `name` of an original field may fall outside the `FieldDef` name
      pattern; `type` "other" = a push button / list box (read only here);
    * `tab_index` null for an original field the editor never ordered.
    """

    name: str
    type: Literal["text", "multiline", "dropdown", "radio", "checkbox", "signature", "other"]
    page_uid: str
    rect: Rect
    required: bool
    readonly: bool
    tooltip: str | None
    default: bool | str | None
    options: list[str] | None
    export_value: str | None
    font: str | None
    size_pt: float | None
    max_len: int | None
    tab_index: int | None
    value: bool | str | list[str] | None
    source: Literal["editor", "original"]
    hidden: bool


MAX_DETECT_PAGES = 100


class DetectFieldsIn(_Strict):
    """`POST .../detect-fields`: pages of the CURRENT version to scan
    (default: all of them)."""

    page_uids: list[Annotated[str, Field(pattern=PAGE_UID_PATTERN)]] | None = Field(
        default=None, min_length=1, max_length=MAX_DETECT_PAGES
    )


class FieldSuggestionOut(BaseModel):
    """A place the detector thinks a field belongs. Nothing is stored: the
    client turns an accepted one into a `field.add` (`name` is a free,
    valid suggestion; `rect` already in field geometry)."""

    suggestion_id: str
    page_uid: str
    type: Literal["text", "multiline", "dropdown", "radio", "checkbox", "signature"]
    rect: Rect
    label: str
    name: str
    confidence: float


class DetectFieldsOut(BaseModel):
    version_no: int
    suggestions: list[FieldSuggestionOut]


class RemappedOut(BaseModel):
    signatures_kept: int
    signatures_removed: int


class CommitOut(BaseModel):
    version: VersionOut
    content_changed: bool
    review_reset: bool
    reparse_started: bool
    remapped: RemappedOut
    warnings: list[dict] = []
