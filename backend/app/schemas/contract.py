from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictBool,
    WrapValidator,
    field_validator,
    model_validator,
)

from app.schemas.common import BBoxModel


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID
    contractor_name: str | None
    contractor_email: str | None
    status: str
    review_status: str
    error_message: str | None
    reviewed_at: datetime | None
    review_notes: str | None
    created_at: datetime
    updated_at: datetime


class ContractListOut(ContractOut):
    """A contract as the dashboard list needs it: name of its template, and how
    much is wrong with it, without a second request per row.

    Everything below is derived from what the list query already has -- the
    contract's own columns plus one joined template name -- so the endpoint
    stays a single query no matter how many contracts come back. Deliberately
    counts and flags only: the stored comparison carries labels, values and
    field ids, and none of it is echoed here (see `ContractDetailOut` /
    `GET /{id}/compare` for the real thing).

    `None` on a derived field means "the contract has no stored comparison
    yet" (uploaded, parsing, or a failed parse) -- not "zero problems".
    """

    template_name: str | None = None
    # Critical entries per category, by the same severity rule the review and
    # signing gates use (app.services.review_override.critical_counts).
    critical_missing_count: int | None = None
    critical_inconsistency_count: int | None = None
    critical_fmcsa_count: int | None = None
    # The engine's verdict, exactly as stored. Signing additionally honours a
    # recorded reviewer override, so `can_sign=false` with `review_override=true`
    # does not on its own mean the contract cannot be signed.
    can_sign: bool | None = None
    review_override: bool = False
    # Signature/date slots in the STORED comparison. NOT named
    # `signature_slot_count`, because that number is not what the signing
    # screen shows: `GET /{id}/compare` drops slots on pages the uploaded PDF
    # does not have, and deciding that needs the contract's own PDF -- one
    # storage read and one PDF parse per row, which is exactly what a list
    # endpoint must not do. So this is the unfiltered count and may be higher
    # than the number of slots a signer will see.
    stored_signature_slot_count: int | None = None

    @field_validator("review_override", mode="before")
    @classmethod
    def _list_override_flag(cls, value):
        # Rows written before the column existed read as NULL, not False.
        return bool(value)


class ContractDetailOut(ContractOut):
    extracted_data: dict
    comparison_result: dict
    final_available: bool = False
    # Reviewer override of critical missing-required fields (additive; rows
    # written before the columns existed read as "no override").
    review_override: bool = False
    review_override_reason: str | None = None
    review_override_fields: list[str] = []
    # PDF editor v2 (`schemas.document.DocumentSummaryOut`); None = legacy.
    document: dict | None = None

    @field_validator("document", mode="before")
    @classmethod
    def _document_summary_only(cls, value):
        # `from_attributes` would hand over the ORM relationship; the route
        # sets the summary itself (api/contracts.py `_document_summary`).
        return value if isinstance(value, dict) else None

    @field_validator("review_override", mode="before")
    @classmethod
    def _override_flag(cls, value):
        return bool(value)

    @field_validator("review_override_fields", mode="before")
    @classmethod
    def _override_fields(cls, value):
        return [str(v) for v in (value or [])]


# Exactly a hex SHA-256 digest; `contracts.file_hash` stores it lowercase.
SHA256_HEX_PATTERN = r"^[0-9a-fA-F]{64}$"
MAX_DUPLICATE_RESULTS = 20


class ContractDuplicateOut(BaseModel):
    """An earlier contract uploaded from the same bytes. Deliberately no
    contractor name/email and no extracted values."""

    id: UUID
    status: str
    review_status: str
    template_id: UUID
    template_name: str | None
    created_at: datetime


class ContractDuplicatesOut(BaseModel):
    duplicates: list[ContractDuplicateOut]


class ContractCreateResponse(BaseModel):
    contract_id: UUID
    task_id: str | None
    status: str


class ContractFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    template_field_id: str
    field_type: str
    status: str
    raw_value: str | None
    normalized_value: str | None
    confidence_score: float | None
    is_required: bool
    is_valid: bool | None
    validation_error: str | None
    bbox: BBoxModel | None
    page_number: int | None
    extra: dict = {}


class FieldCorrection(BaseModel):
    """A reviewer overriding what the parser read.

    Strict for the same reason as `ContractFinalization`: every key is optional
    and the handler applies `model_dump(exclude_unset=True)`, so a misspelt
    `value` would leave the parser's own reading in place -- and still mark the
    field `verified` with confidence 1.0 and the validation error cleared. The
    reviewer would be told the correction was saved while the wrong value went
    on to the signing gate. An unknown key is refused instead.
    """

    model_config = ConfigDict(extra="forbid")

    value: str | None = None
    selected: list[str] | None = None
    checked: bool | None = None
    mark_verified: bool = True


class ReviewSubmission(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    notes: str | None = None
    # Approve although the engine reports critical *missing required* fields.
    # Requires `notes` (the reason) and is refused outright when an FMCSA
    # violation or a critical inconsistency is outstanding. Ignored when the
    # contract can already be signed.
    override_missing: bool = False


class SignaturePlacementIn(BaseModel):
    """Where a captured signature goes.

    With `field_id` the server positions the image from the template's own
    coordinates and ignores `page`/`bbox` (a client must not be able to steer
    where a slot signature lands). Without it -- free placement -- both are
    required.
    """

    page: int | None = Field(default=None, ge=1)
    bbox: BBoxModel | None = None
    field_id: str | None = None
    signer_name: str | None = None
    signer_email: EmailStr | None = None
    signer_role: str = Field(default="contractor", pattern="^(contractor|company_rep)$")
    # PNG data URL or bare base64 produced by the canvas signature pad.
    image_data: str

    @model_validator(mode="after")
    def _free_placement_needs_coordinates(self) -> "SignaturePlacementIn":
        if self.field_id is None and (self.page is None or self.bbox is None):
            raise ValueError("page and bbox are required when no field_id is given")
        return self


class SignatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    signer_name: str | None
    signer_email: str | None
    signer_role: str
    page_number: int
    bbox: BBoxModel
    field_id: str | None
    signed_at: datetime


# One "apply to all" click may cover every signature slot in a package. The cap
# is an abuse bound, not a product limit: the largest real contract package is
# well under it, while an unbounded list would turn one request into an
# arbitrary number of storage writes and audit rows.
MAX_BULK_SIGNATURE_SLOTS = 200


class BulkSignaturePlacementIn(BaseModel):
    """One captured image applied to many template signature slots.

    Deliberately narrower than `SignaturePlacementIn`: there is no `page` and no
    `bbox`, so every placement is positioned from the template's own
    coordinates. Free placement stays a single-slot operation.
    """

    field_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_SIGNATURE_SLOTS)
    signer_name: str | None = None
    signer_email: EmailStr | None = None
    signer_role: str = Field(default="contractor", pattern="^(contractor|company_rep)$")
    # PNG data URL or bare base64 produced by the canvas signature pad.
    image_data: str

    @field_validator("field_ids")
    @classmethod
    def _no_duplicates(cls, value: list[str]) -> list[str]:
        # Refused rather than de-duplicated, as `_resolve_dates` refuses a
        # repeated date field: a caller that names a slot twice has a bug, and
        # silently collapsing it would report a placement count it never asked for.
        seen: set[str] = set()
        for field_id in value:
            if field_id in seen:
                raise ValueError(f"Signature slot {field_id!r} was given more than once")
            seen.add(field_id)
        return value


class BulkSignaturePlaced(BaseModel):
    """A slot that now holds a signature. `bbox`/`page_number` come from the
    template, so the caller can render the result without a re-fetch."""

    field_id: str
    signature_id: UUID
    page_number: int
    bbox: BBoxModel


class BulkSignatureSkipped(BaseModel):
    """A slot left untouched. `already_signed`: an existing signature is never
    overwritten -- remove it first."""

    field_id: str
    reason: str


class BulkSignatureFailed(BaseModel):
    """A slot that could not be used: `unknown_field` (not in this contract's
    template), `not_a_signature_field` (wrong type, or no usable coordinates)
    or `page_out_of_range` (the slot's page is past the uploaded PDF's end)."""

    field_id: str
    reason: str


class BulkSignatureResult(BaseModel):
    """Partial success is the normal outcome, so this is always a 200: one bad
    slot must not discard the placements that did succeed. Every requested
    field id appears in exactly one of the three lists."""

    contract_id: UUID
    placed: list[BulkSignaturePlaced] = []
    skipped: list[BulkSignatureSkipped] = []
    failed: list[BulkSignatureFailed] = []


TYPED_SIGNATURE_MAX_CHARS = 64
TypedSignatureFont = Literal["GreatVibes", "Allura", "Sacramento"]


class TypedSignatureIn(BaseModel):
    """A signature typed as text (editor v2 B2): the server renders it in one
    of the three OFL script fonts to a PNG; the TEXT itself is never stored."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=TYPED_SIGNATURE_MAX_CHARS)
    font: TypedSignatureFont = "GreatVibes"

    @model_validator(mode="after")
    def _printable(self) -> "TypedSignatureIn":
        if not self.text.strip():
            raise ValueError("A typed signature needs a non-blank text")
        from app.services.typed_signature import unprintable

        found = unprintable(self.text, self.font)
        if found is not None:
            index, char, reason = found
            raise ValueError(
                f"{self.font} cannot print {char!r} (U+{ord(char):04X}) at position {index}; {reason}"
            )
        return self


class UserSignatureCreate(BaseModel):
    """Saving a reusable signature to the current user's private library.

    Exactly one of `image_data` (drawn / uploaded) or `typed` (B2)."""

    label: str | None = Field(default=None, max_length=255)
    is_default: bool = False
    # PNG data URL or bare base64 produced by the canvas signature pad.
    image_data: str | None = None
    typed: TypedSignatureIn | None = None
    # How `image_data` was made (editor v2); informational only.
    source: Literal["drawn", "uploaded"] | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "UserSignatureCreate":
        if (self.image_data is None) == (self.typed is None):
            raise ValueError("give exactly one of 'image_data' or 'typed'")
        if self.typed is not None and self.source is not None:
            raise ValueError("'source' applies to image_data only")
        return self


class UserSignatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None
    is_default: bool
    signature_hash: str
    created_at: datetime
    # drawn | uploaded | typed; null for rows saved before editor v2.
    source: str | None = None


class DatePlacementIn(BaseModel):
    """One date value for one template date field. No bbox on purpose: the
    server takes the coordinates from the template, so the client cannot steer
    where a date lands. `page` is informational only."""

    field_id: str
    value: str
    page: int | None = Field(default=None, ge=1)


LAYOUT_OFFSET_LIMIT_PT = 20.0
# Per-slot offsets are dragged with the mouse and may move a mark several lines
# (or across the page), so they get a wider range than the global nudges.
SLOT_OFFSET_LIMIT_PT = 200.0
MAX_LAYOUT_SLOT_OFFSETS = 500
# Per-slot scales are dragged from a corner handle, so they get a wider range
# than the global `signature_scale` slider: a drag must not hit the end of the
# range after a few pixels. The frontend clamps to what fits its own preview;
# the server only enforces these bounds (contract v3).
SLOT_SCALE_MIN = 0.3
SLOT_SCALE_MAX = 3.0
# Font FAMILIES (contract v6): the standard-14 three, not embedded, WinAnsi
# only; and three embedded TrueType families (Liberation, metric-compatible with
# the first three, subset into the PDF). `bold`/`italic` pick the face -- see
# app.services.fonts. Kept in step with app.services.fonts.FONT_FAMILIES.
# Editor v2 (B2) adds six embedded families (Arimo, Carlito, Caladea, DejaVu
# Sans/Serif, Amiri-Latin); the v6 six are unchanged and first.
FontFamily = Literal[
    "Helvetica", "Times-Roman", "Courier", "LiberationSans", "LiberationSerif", "LiberationMono",
    "Arimo", "Carlito", "Caladea", "DejaVuSans", "DejaVuSerif", "Amiri",
]
DATE_FONTS = (
    "Helvetica", "Times-Roman", "Courier", "LiberationSans", "LiberationSerif", "LiberationMono",
    "Arimo", "Carlito", "Caladea", "DejaVuSans", "DejaVuSerif", "Amiri",
)


class FinalizeLayout(BaseModel):
    """How marks sit on their fill lines. Shared contract with the frontend:
    both apply the same math (see app.services.pdf_builder). Offsets are PDF
    points. Vertical offsets are POSITIVE = UP and a slot offset adds to its
    kind's global offset; `slot_offsets_x_pt` is horizontal, POSITIVE = RIGHT,
    and applies to slot placements only (contract v2).

    `slot_scales` (contract v3) sizes one signature slot on its own. Unlike the
    offsets it does NOT add to the global value: an entry REPLACES
    `signature_scale` for that slot, so a corner drag lands where the handle
    was dragged to instead of at global x slot. Signature slots only -- date
    text is sized by `date_font_size` -- and slot placements only.
    """

    model_config = ConfigDict(extra="forbid")

    signature_offset_pt: float = Field(
        default=0.0, ge=-LAYOUT_OFFSET_LIMIT_PT, le=LAYOUT_OFFSET_LIMIT_PT, allow_inf_nan=False
    )
    date_offset_pt: float = Field(
        default=0.0, ge=-LAYOUT_OFFSET_LIMIT_PT, le=LAYOUT_OFFSET_LIMIT_PT, allow_inf_nan=False
    )
    slot_offsets_pt: dict[str, float] = Field(default_factory=dict)
    slot_offsets_x_pt: dict[str, float] = Field(default_factory=dict)
    signature_scale: float = Field(default=1.0, ge=0.6, le=1.5, allow_inf_nan=False)
    slot_scales: dict[str, float] = Field(default_factory=dict)
    date_font: FontFamily = "Helvetica"
    date_font_size: float = Field(default=11.0, ge=8.0, le=16.0, allow_inf_nan=False)
    # contract v6: slot dates in bold / italic. Strict booleans: `"true"` or `1`
    # is a client bug, not a yes. Absent/false = the v5 output, byte for byte.
    date_bold: StrictBool = False
    date_italic: StrictBool = False

    def audit_record(self) -> dict:
        """What the finalize audit entry records. `date_bold`/`date_italic` are
        listed only when set, so a v5 body is recorded exactly as before."""
        record = self.model_dump()
        for key in ("date_bold", "date_italic"):
            if not record[key]:
                del record[key]
        return record

    @field_validator("slot_offsets_pt", "slot_offsets_x_pt")
    @classmethod
    def _slot_offsets_in_range(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > MAX_LAYOUT_SLOT_OFFSETS:
            raise ValueError(f"At most {MAX_LAYOUT_SLOT_OFFSETS} slot offsets are allowed")
        for field_id, offset in value.items():
            if not math.isfinite(offset) or not (
                -SLOT_OFFSET_LIMIT_PT <= offset <= SLOT_OFFSET_LIMIT_PT
            ):
                raise ValueError(
                    f"Offset for {field_id!r} must be within "
                    f"[-{SLOT_OFFSET_LIMIT_PT:g}, {SLOT_OFFSET_LIMIT_PT:g}] pt"
                )
        return value

    @field_validator("slot_scales")
    @classmethod
    def _slot_scales_in_range(cls, value: dict[str, float]) -> dict[str, float]:
        # Same cap as the offset maps: one entry per slot at most, and a
        # package never has anywhere near this many.
        if len(value) > MAX_LAYOUT_SLOT_OFFSETS:
            raise ValueError(f"At most {MAX_LAYOUT_SLOT_OFFSETS} slot scales are allowed")
        for field_id, scale in value.items():
            if not math.isfinite(scale) or not (SLOT_SCALE_MIN <= scale <= SLOT_SCALE_MAX):
                raise ValueError(
                    f"Scale for {field_id!r} must be within "
                    f"[{SLOT_SCALE_MIN:g}, {SLOT_SCALE_MAX:g}]"
                )
        return value


# -- free annotations (contract v4, extended in v5) ---------------------------

# An abuse bound, not a product limit: a fully annotated 34-page package is well
# under it, while an unbounded list would turn one request into an arbitrary
# amount of drawing work. v4 had 200; v5 raised it to 500 because a single
# freehand stroke or highlight run is one element each.
MAX_ANNOTATIONS = 500
MAX_ANNOTATION_TEXT_CHARS = 200
ANNOTATION_SIZE_MIN_PT = 6.0
# Text is capped lower than a mark: a tick may legitimately be drawn large
# enough to cover a printed checkbox, while 48pt is already a headline.
ANNOTATION_TEXT_SIZE_MAX_PT = 48.0
ANNOTATION_MARK_SIZE_MAX_PT = 72.0
DEFAULT_ANNOTATION_SIZE_PT = 11.0
# Client-supplied handle. Deliberately not a UUID -- the client owns it -- but
# restricted to a charset that is safe to echo back in an error `details` and
# that cannot carry text the user typed (so it never becomes a PII channel).
ANNOTATION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# -- v5 bounds ------------------------------------------------------------------
# Named palette only (hex values: app.services.pdf_builder.ANNOTATION_PALETTE).
# There is deliberately NO `white`: the only way to paint white is the
# `whiteout` kind, so every covering-up shows in the audit trail as a whiteout.
AnnotationColor = Literal["black", "blue", "red", "green", "yellow", "orange", "purple"]
MAX_MARKUP_RECTS = 50
MAX_INK_PATHS = 20
MIN_INK_PATH_POINTS = 2
MAX_INK_PATH_POINTS = 2000
MAX_INK_POINTS_PER_ELEMENT = 4000
# Across the whole finalize request, every ink element together.
MAX_INK_POINTS_TOTAL = 20_000
STROKE_MIN_PT = 0.5
STROKE_MAX_PT = 12.0
DEFAULT_STROKE_PT = 2.0
# Every box (a rect, a shape, an image, a whiteout, a markup rect) is at least
# this big on both axes: a zero-width box is either a client bug or invisible.
MIN_BOX_PT = 1.0
# A line/arrow shorter than this is a click, not a line.
MIN_LINE_LENGTH_PT = 1.0
MAX_NOTE_TEXT_CHARS = 1000
# Server-generated (uuid4().hex) -- see POST .../annotation-assets.
ANNOTATION_ASSET_ID_PATTERN = r"^[0-9a-f]{32}$"

_finite = {"allow_inf_nan": False}


class _AnnotationBase(BaseModel):
    """What every element has, whatever its kind (contract v5)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=ANNOTATION_ID_PATTERN)
    page: int = Field(ge=1)
    # Validation only, no semantics on the server: the editor uses it to bind
    # the whiteout + text pair of an "edit existing text" operation.
    group_id: str | None = Field(default=None, pattern=ANNOTATION_ID_PATTERN)


class AnnotationRectIn(BaseModel):
    """A box in canonical space (points, top-left origin)."""

    model_config = ConfigDict(extra="forbid")

    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    w_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    h_pt: float = Field(ge=MIN_BOX_PT, **_finite)


class AnnotationPointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)


class AnnotationIn(BaseModel):
    """One element the user placed on the document by hand (contract v4).

    Coordinates are canonical: PDF points, origin TOP-LEFT, the same system as
    `bbox` and `rule_y`. For `text`/`date` the point is the left end of the text
    BASELINE; for `check`/`cross` it is the CENTRE of the mark.

    Nothing here is silently ignored or silently corrected. An element the
    server cannot draw exactly as asked is refused (422), because the result is
    a signed contract: a mark that quietly moved, shrank or turned into a row of
    dots is worse than a failed request.

    v5 adds only optional keys: `color` (absent = black, and then the PDF is
    byte-for-byte the v4 one) and `group_id`.

    v6 adds the embedded families to `font` and optional `bold`/`italic` (any
    family). Absent or false = the v5 PDF, byte for byte. In an embedded
    family the text may be anything the font has a glyph for (Cyrillic,
    Uzbek `oʻ`, ...); a standard-14 family stays WinAnsi.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=ANNOTATION_ID_PATTERN)
    kind: Literal["text", "date", "check", "cross"]
    page: int = Field(ge=1)
    group_id: str | None = Field(default=None, pattern=ANNOTATION_ID_PATTERN)
    x_pt: float = Field(allow_inf_nan=False)
    y_pt: float = Field(allow_inf_nan=False)
    text: str | None = None
    font: FontFamily = "Helvetica"
    size_pt: float = Field(default=DEFAULT_ANNOTATION_SIZE_PT, allow_inf_nan=False)
    color: AnnotationColor | None = None
    bold: StrictBool = False
    italic: StrictBool = False

    @model_validator(mode="after")
    def _kind_rules(self) -> "AnnotationIn":
        is_text = self.kind in ("text", "date")
        limit = ANNOTATION_TEXT_SIZE_MAX_PT if is_text else ANNOTATION_MARK_SIZE_MAX_PT
        if not ANNOTATION_SIZE_MIN_PT <= self.size_pt <= limit:
            raise ValueError(
                f"size_pt for a {self.kind!r} annotation must be within "
                f"[{ANNOTATION_SIZE_MIN_PT:g}, {limit:g}] pt"
            )
        if not is_text:
            # A tick carries no text and is not set in a font; accepting either
            # would mean accepting input that is then thrown away.
            if "text" in self.model_fields_set and self.text is not None:
                raise ValueError(f"A {self.kind!r} annotation takes no text")
            if self.bold or self.italic:
                raise ValueError(f"A {self.kind!r} annotation is not set in a font (no bold/italic)")
            return self

        text = self.text
        if text is None or not text.strip():
            raise ValueError(f"A {self.kind!r} annotation needs a non-empty text")
        if len(text) > MAX_ANNOTATION_TEXT_CHARS:
            raise ValueError(
                f"text must be at most {MAX_ANNOTATION_TEXT_CHARS} characters "
                f"(got {len(text)})"
            )
        # The standard 14 PDF fonts are WinAnsi. reportlab does not refuse a
        # character outside it -- it prints a ZapfDingbats block in its place --
        # so the refusal has to happen here, naming the character. An embedded
        # font is checked against its own cmap (a missing glyph would be .notdef).
        from app.services.fonts import is_embedded, resolve_font
        from app.services.pdf_builder import undrawable_character, undrawable_reason

        found = undrawable_character(text, self.font, self.bold, self.italic)
        if found is not None:
            index, char = found
            face = resolve_font(self.font, self.bold, self.italic)
            # Standard 14: named by family, exactly the v4/v5 message.
            name = face if is_embedded(face) else self.font
            raise ValueError(
                f"{name} cannot print {char!r} (U+{ord(char):04X}) at position {index}; "
                f"{undrawable_reason(char, self.font, self.bold, self.italic)}"
            )
        return self


class MarkupAnnotationIn(_AnnotationBase):
    """`highlight` / `underline` / `strikeout` over one or more text runs.

    One rect per line of selected text. Default colour: highlight `yellow`,
    underline/strikeout `red`.
    """

    kind: Literal["highlight", "underline", "strikeout"]
    rects: list[AnnotationRectIn] = Field(min_length=1, max_length=MAX_MARKUP_RECTS)
    color: AnnotationColor | None = None


class InkAnnotationIn(_AnnotationBase):
    """Freehand strokes: each path is a polyline, drawn with round caps/joins."""

    kind: Literal["ink"]
    paths: list[
        list[AnnotationPointIn]
    ] = Field(min_length=1, max_length=MAX_INK_PATHS)
    stroke_pt: float = Field(default=DEFAULT_STROKE_PT, ge=STROKE_MIN_PT, le=STROKE_MAX_PT, **_finite)
    color: AnnotationColor = "black"

    @field_validator("paths")
    @classmethod
    def _path_sizes(cls, value):
        total = 0
        for index, path in enumerate(value):
            if not MIN_INK_PATH_POINTS <= len(path) <= MAX_INK_PATH_POINTS:
                raise ValueError(
                    f"path {index} must have {MIN_INK_PATH_POINTS}..{MAX_INK_PATH_POINTS} "
                    f"points (got {len(path)})"
                )
            total += len(path)
        if total > MAX_INK_POINTS_PER_ELEMENT:
            raise ValueError(
                f"an ink element may have at most {MAX_INK_POINTS_PER_ELEMENT} points "
                f"(got {total})"
            )
        return value


class ShapeAnnotationIn(_AnnotationBase):
    """`rect` / `ellipse` outline. `fill` adds a TRANSLUCENT fill (alpha 0.2) of
    the same colour -- never an opaque one, which would be a whiteout that does
    not say it is one."""

    kind: Literal["rect", "ellipse"]
    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    w_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    h_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    stroke_pt: float = Field(default=DEFAULT_STROKE_PT, ge=STROKE_MIN_PT, le=STROKE_MAX_PT, **_finite)
    color: AnnotationColor = "black"
    fill: bool = False


class LineAnnotationIn(_AnnotationBase):
    """`line` / `arrow` from (x1, y1) to (x2, y2); an arrow's head is at (x2, y2)."""

    kind: Literal["line", "arrow"]
    x1_pt: float = Field(**_finite)
    y1_pt: float = Field(**_finite)
    x2_pt: float = Field(**_finite)
    y2_pt: float = Field(**_finite)
    stroke_pt: float = Field(default=DEFAULT_STROKE_PT, ge=STROKE_MIN_PT, le=STROKE_MAX_PT, **_finite)
    color: AnnotationColor = "black"

    @model_validator(mode="after")
    def _has_length(self) -> "LineAnnotationIn":
        length = math.hypot(self.x2_pt - self.x1_pt, self.y2_pt - self.y1_pt)
        if length < MIN_LINE_LENGTH_PT:
            raise ValueError(f"a {self.kind!r} must be at least {MIN_LINE_LENGTH_PT:g} pt long")
        return self


class NoteAnnotationIn(_AnnotationBase):
    """A sticky note: a real PDF `/Text` annotation (icon + popup), not ink.

    `(x_pt, y_pt)` is the icon's TOP-LEFT corner. The text is WinAnsi like every
    other annotation text (checked by the same `undrawable_character`), except
    that a line break (`\\n`) is allowed: a note is multi-line by nature and its
    text never goes through a content-stream font.
    """

    kind: Literal["note"]
    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    text: str = Field(min_length=1, max_length=MAX_NOTE_TEXT_CHARS)
    color: AnnotationColor = "yellow"

    @field_validator("text")
    @classmethod
    def _printable(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A 'note' annotation needs a non-empty text")
        from app.services.pdf_builder import ANNOTATION_FONT, undrawable_character

        found = undrawable_character(value.replace("\n", " "), ANNOTATION_FONT)
        if found is not None:
            index, char = found
            raise ValueError(
                f"{ANNOTATION_FONT} cannot print {char!r} (U+{ord(char):04X}) at position {index}; "
                "the standard PDF fonts cover Western European text only"
            )
        return value


class ImageAnnotationIn(_AnnotationBase):
    """A picture uploaded with POST .../annotation-assets, drawn filling the box
    exactly (the client keeps the aspect ratio if it wants to)."""

    kind: Literal["image"]
    asset_id: str = Field(pattern=ANNOTATION_ASSET_ID_PATTERN)
    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    w_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    h_pt: float = Field(ge=MIN_BOX_PT, **_finite)


class WhiteoutAnnotationIn(_AnnotationBase):
    """An opaque white box. VISUAL ONLY: the covered text stays in the page's
    content stream (copy/search still find it). Not a redaction."""

    kind: Literal["whiteout"]
    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    w_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    h_pt: float = Field(ge=MIN_BOX_PT, **_finite)


# -- markup v7 (PDF editor v2, B2) -------------------------------------------------
#
# Drawn by the PyMuPDF engine (`app.services.pdf_engine.markup`), never by the
# reportlab overlay. Geometry: PDF points, TOP-LEFT origin, the UNROTATED page
# space relative to the CropBox (EDITOR-V2-PLAN §0.2 -- the text model's
# space). `rotation_deg` turns the element about its own box centre,
# clockwise as displayed. Colours: the same closed palette (NO white; white
# paint exists only as `whiteout`). `opacity` 0.05..1.

OPACITY_MIN = 0.05
OPACITY_MAX = 1.0
MAX_TEXTBOX_RUNS = 50
MAX_TEXTBOX_CHARS = 2000
TEXTBOX_SIZE_MIN_PT = 6.0
TEXTBOX_SIZE_MAX_PT = 72.0
MAX_STAMP_TEXT_CHARS = 40
MAX_STAMP_DATE_CHARS = 32
SQUARE_MARK_MIN_PT = 4.0
SQUARE_MARK_MAX_PT = 72.0
FREEHAND_HIGHLIGHT_OPACITY = 0.35
STAMP_PRESETS = (
    "approved", "rejected", "draft", "confidential", "final", "received", "void", "paid", "reviewed",
)
Opacity = Annotated[float, Field(ge=OPACITY_MIN, le=OPACITY_MAX, allow_inf_nan=False)]
RotationDeg = Annotated[float, Field(ge=-360.0, le=360.0, allow_inf_nan=False)]


def _check_printable(text: str, font: str, bold: bool, italic: bool, *, allow_newline: bool) -> None:
    """The v6 rule (`undrawable_character`): refuse, naming the character,
    anything the face cannot print. A line break is allowed where noted."""
    from app.services.fonts import is_embedded, resolve_font
    from app.services.pdf_builder import undrawable_character, undrawable_reason

    probe = text.replace("\n", " ") if allow_newline else text
    found = undrawable_character(probe, font, bold, italic)
    if found is not None:
        index, char = found
        face = resolve_font(font, bold, italic)
        name = face if is_embedded(face) else font
        raise ValueError(
            f"{name} cannot print {char!r} (U+{ord(char):04X}) at position {index}; "
            f"{undrawable_reason(char, font, bold, italic)}"
        )


class TextboxRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TEXTBOX_CHARS)
    font: FontFamily = "LiberationSans"
    size_pt: float = Field(default=DEFAULT_ANNOTATION_SIZE_PT, ge=TEXTBOX_SIZE_MIN_PT,
                           le=TEXTBOX_SIZE_MAX_PT, allow_inf_nan=False)
    color: AnnotationColor = "black"
    bold: StrictBool = False
    italic: StrictBool = False

    @model_validator(mode="after")
    def _printable(self) -> "TextboxRunIn":
        _check_printable(self.text, self.font, self.bold, self.italic, allow_newline=True)
        return self


class TextboxAnnotationIn(_AnnotationBase):
    """A text box (v7): styled runs wrapped inside the box, `\\n` = new line.
    Text past the bottom of the box is still drawn (the client sized it)."""

    kind: Literal["textbox"]
    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    w_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    h_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    runs: list[TextboxRunIn] = Field(min_length=1, max_length=MAX_TEXTBOX_RUNS)
    align: Literal["left", "center", "right", "justify"] = "left"
    line_height: float = Field(default=1.2, ge=0.8, le=3.0, allow_inf_nan=False)
    rotation_deg: RotationDeg = 0.0
    opacity: Opacity = 1.0

    @field_validator("runs")
    @classmethod
    def _budget(cls, value):
        total = sum(len(run.text) for run in value)
        if total > MAX_TEXTBOX_CHARS:
            raise ValueError(f"at most {MAX_TEXTBOX_CHARS} characters per text box (got {total})")
        return value


class StampAnnotationIn(_AnnotationBase):
    """A stamp: a preset word or a short custom text in a (bordered) box.
    `date_text` is typed by the user -- the server never fills in "now"
    (2026-09-25 decision); absent = no date line."""

    kind: Literal["stamp"]
    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    w_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    h_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    preset: Literal[STAMP_PRESETS] | None = None  # type: ignore[valid-type]
    text: str | None = Field(default=None, min_length=1, max_length=MAX_STAMP_TEXT_CHARS)
    color: AnnotationColor = "red"
    border: StrictBool = True
    date_text: str | None = Field(default=None, min_length=1, max_length=MAX_STAMP_DATE_CHARS)
    rotation_deg: RotationDeg = 0.0
    opacity: Opacity = 1.0

    @model_validator(mode="after")
    def _one_label(self) -> "StampAnnotationIn":
        if (self.preset is None) == (self.text is None):
            raise ValueError("a stamp takes exactly one of 'preset' or 'text'")
        if self.text is not None:
            if not self.text.strip():
                raise ValueError("a stamp text must not be blank")
            _check_printable(self.text, "Helvetica", True, False, allow_newline=False)
        if self.date_text is not None:
            if not self.date_text.strip():
                raise ValueError("date_text must not be blank")
            _check_printable(self.date_text, "Helvetica", False, False, allow_newline=False)
        return self


class Image2AnnotationIn(_AnnotationBase):
    """An uploaded image (v7): like `image`, plus rotation and opacity."""

    kind: Literal["image2"]
    asset_id: str = Field(pattern=ANNOTATION_ASSET_ID_PATTERN)
    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    w_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    h_pt: float = Field(ge=MIN_BOX_PT, **_finite)
    rotation_deg: RotationDeg = 0.0
    opacity: Opacity = 1.0


class Shape2AnnotationIn(_AnnotationBase):
    """rect / ellipse (box) or line / arrow (two points), v7 styling.
    `fill_color` null = no fill; `heads` only for line/arrow (arrow default
    `end`)."""

    kind: Literal["shape2"]
    shape: Literal["rect", "ellipse", "line", "arrow"]
    x_pt: float | None = Field(default=None, **_finite)
    y_pt: float | None = Field(default=None, **_finite)
    w_pt: float | None = Field(default=None, ge=MIN_BOX_PT, **_finite)
    h_pt: float | None = Field(default=None, ge=MIN_BOX_PT, **_finite)
    x1_pt: float | None = Field(default=None, **_finite)
    y1_pt: float | None = Field(default=None, **_finite)
    x2_pt: float | None = Field(default=None, **_finite)
    y2_pt: float | None = Field(default=None, **_finite)
    stroke_color: AnnotationColor = "black"
    fill_color: AnnotationColor | None = None
    stroke_pt: float = Field(default=DEFAULT_STROKE_PT, ge=STROKE_MIN_PT, le=STROKE_MAX_PT, **_finite)
    opacity: Opacity = 1.0
    dash: Literal["solid", "dash", "dot"] = "solid"
    heads: Literal["none", "start", "end", "both"] | None = None

    @model_validator(mode="after")
    def _geometry(self) -> "Shape2AnnotationIn":
        box = (self.x_pt, self.y_pt, self.w_pt, self.h_pt)
        seg = (self.x1_pt, self.y1_pt, self.x2_pt, self.y2_pt)
        if self.shape in ("rect", "ellipse"):
            if any(v is None for v in box) or any(v is not None for v in seg):
                raise ValueError(f"a {self.shape!r} takes x_pt, y_pt, w_pt, h_pt only")
            if self.heads is not None:
                raise ValueError("heads apply to a line or an arrow only")
        else:
            if any(v is None for v in seg) or any(v is not None for v in box):
                raise ValueError(f"a {self.shape!r} takes x1_pt, y1_pt, x2_pt, y2_pt only")
            if self.fill_color is not None:
                raise ValueError("a line or an arrow has no fill")
            if math.hypot(self.x2_pt - self.x1_pt, self.y2_pt - self.y1_pt) < MIN_LINE_LENGTH_PT:
                raise ValueError(f"a {self.shape!r} must be at least {MIN_LINE_LENGTH_PT:g} pt long")
        return self

    @property
    def effective_heads(self) -> str:
        if self.heads is not None:
            return self.heads
        return "end" if self.shape == "arrow" else "none"


class Ink2AnnotationIn(InkAnnotationIn):
    """Freehand strokes (v7): `ink` plus opacity; `freehand_highlight` draws a
    translucent marker stroke (opacity defaults to 0.35 then)."""

    kind: Literal["ink2"]  # type: ignore[assignment]
    opacity: Opacity | None = None
    freehand_highlight: StrictBool = False

    @property
    def effective_opacity(self) -> float:
        if self.opacity is not None:
            return float(self.opacity)
        return FREEHAND_HIGHLIGHT_OPACITY if self.freehand_highlight else 1.0


class SquareMarkAnnotationIn(_AnnotationBase):
    """A filled square (Forms ▸ Filled square), centred on (x_pt, y_pt)."""

    kind: Literal["square_mark"]
    x_pt: float = Field(**_finite)
    y_pt: float = Field(**_finite)
    size_pt: float = Field(default=10.0, ge=SQUARE_MARK_MIN_PT, le=SQUARE_MARK_MAX_PT, allow_inf_nan=False)
    color: AnnotationColor = "black"


V7_KINDS = ("textbox", "stamp", "image2", "shape2", "ink2", "square_mark")

AnnotationModel = (
    AnnotationIn
    | MarkupAnnotationIn
    | InkAnnotationIn
    | ShapeAnnotationIn
    | LineAnnotationIn
    | NoteAnnotationIn
    | ImageAnnotationIn
    | WhiteoutAnnotationIn
    | TextboxAnnotationIn
    | StampAnnotationIn
    | Image2AnnotationIn
    | Shape2AnnotationIn
    | Ink2AnnotationIn
    | SquareMarkAnnotationIn
)

ANNOTATION_MODELS: dict[str, type[BaseModel]] = {
    "text": AnnotationIn,
    "date": AnnotationIn,
    "check": AnnotationIn,
    "cross": AnnotationIn,
    "highlight": MarkupAnnotationIn,
    "underline": MarkupAnnotationIn,
    "strikeout": MarkupAnnotationIn,
    "ink": InkAnnotationIn,
    "rect": ShapeAnnotationIn,
    "ellipse": ShapeAnnotationIn,
    "line": LineAnnotationIn,
    "arrow": LineAnnotationIn,
    "note": NoteAnnotationIn,
    "image": ImageAnnotationIn,
    "whiteout": WhiteoutAnnotationIn,
    # v7 (editor v2 B2)
    "textbox": TextboxAnnotationIn,
    "stamp": StampAnnotationIn,
    "image2": Image2AnnotationIn,
    "shape2": Shape2AnnotationIn,
    "ink2": Ink2AnnotationIn,
    "square_mark": SquareMarkAnnotationIn,
}


class AnnotationElement(BaseModel):
    """Used only to report a missing / unknown `kind` the way a plain model does
    (loc `[..., "kind"]`, type `missing` / `literal_error`)."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal[
        "text", "date", "check", "cross", "highlight", "underline", "strikeout", "ink",
        "rect", "ellipse", "line", "arrow", "note", "image", "whiteout",
        "textbox", "stamp", "image2", "shape2", "ink2", "square_mark",
    ]


def _dispatch_annotation(value, handler):
    """Discriminated union on `kind`, dispatched by hand.

    pydantic's built-in tagged union would report a missing `kind` as
    `union_tag_not_found` at the ELEMENT (loc `annotations.0`) and put the tag
    into every nested loc (`annotations.0.highlight.rects.0.x_pt`). The v4 API
    answered `loc = [..., "kind"], type = missing`, and clients rely on the loc
    shape, so the dispatch is done here and the member model validates the
    element with its own, flat locs. The JSON schema is still the tagged union.
    """
    if isinstance(value, BaseModel):
        return value
    if isinstance(value, dict):
        model = ANNOTATION_MODELS.get(value.get("kind")) if isinstance(value.get("kind"), str) else None
        if model is not None:
            return model.model_validate(value)
    # Missing / unknown kind, or not an object at all: raises the plain error.
    AnnotationElement.model_validate(value)
    raise ValueError("unreachable")  # pragma: no cover


Annotation = Annotated[
    AnnotationModel, Field(discriminator="kind"), WrapValidator(_dispatch_annotation)
]


class AnnotationAssetIn(BaseModel):
    """JSON form of POST .../annotation-assets (multipart `file` also works).

    Exactly one of `image_data` or (editor v2 B2) `from_asset_id` -- an image
    of the caller's own gallery, COPIED onto this contract."""

    model_config = ConfigDict(extra="forbid")

    # PNG/JPEG data URL or bare base64, exactly as the signature endpoints take.
    image_data: str | None = None
    from_asset_id: str | None = Field(default=None, pattern=ANNOTATION_ASSET_ID_PATTERN)

    @model_validator(mode="after")
    def _one(self) -> "AnnotationAssetIn":
        if (self.image_data is None) == (self.from_asset_id is None):
            raise ValueError("give exactly one of 'image_data' or 'from_asset_id'")
        return self


class AnnotationAssetOut(BaseModel):
    asset_id: str
    width_px: int
    height_px: int


class ContractFinalization(BaseModel):
    """Dates default to every date field paired with a placed signature, using
    the template's own coordinates rather than an offset from the signature.

    Strict, like every part it is made of (`FinalizeLayout`, `AnnotationIn`,
    `BBoxModel`): an unknown key here is refused, not ignored. The whole body is
    optional, so a misspelt key -- `annotation` for `annotations`, `date` for
    `dates` -- would otherwise be a *silent* loss: 200, a final PDF without the
    marks the user placed, and nothing said until somebody reads the signed
    contract. A 422 naming the key is the only outcome a client can act on.
    """

    model_config = ConfigDict(extra="forbid")

    contract_date: str | None = None
    dates: list[DatePlacementIn] | None = None
    apply_paired_dates: bool = True
    # Absent = defaults. Slot-offset keys are checked against the contract's
    # own slots in finalize (unknown key -> 422).
    layout: FinalizeLayout | None = None
    # Absent (or empty) = nothing is drawn and the output is what it was before
    # contract v4 existed. Page and coordinates are checked against the
    # contract's own PDF in finalize (off the document -> 422).
    annotations: list[Annotation] | None = Field(default=None, max_length=MAX_ANNOTATIONS)

    @field_validator("annotations")
    @classmethod
    def _unique_annotation_ids(cls, value):
        # Refused rather than de-duplicated, exactly as a repeated signature
        # slot is: the id is the client's handle on the element, and two
        # elements sharing one means the client cannot say which was drawn.
        seen: set[str] = set()
        ink_points = 0
        for note in value or []:
            if note.id in seen:
                raise ValueError(f"Annotation id {note.id!r} was given more than once")
            seen.add(note.id)
            if note.kind in ("ink", "ink2"):
                ink_points += sum(len(path) for path in note.paths)
        if ink_points > MAX_INK_POINTS_TOTAL:
            raise ValueError(
                f"At most {MAX_INK_POINTS_TOTAL} ink points are allowed per request "
                f"(got {ink_points})"
            )
        return value


class FinalizeResponse(BaseModel):
    contract_id: UUID
    status: str
    file_hash: str
    download_url: str
