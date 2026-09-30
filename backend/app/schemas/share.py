"""Share-for-fill links (EDITOR-V2-PLAN §8, F3) -- request/response shapes.

Mirrored by `frontend/src/api/share.ts`. Internal routes live under
`/api/contracts/{contract_id}/share-links` and `.../submissions`; the public,
account-less routes under `/api/share` (token in the `X-Share-Token` header).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, field_validator

from app.schemas.document import (
    FIELD_REF_PATTERN,
    MAX_FIELD_OPTIONS,
    MAX_FIELD_VALUE_CHARS,
    MAX_OPTION_CHARS,
    SESSION_ID_PATTERN,
    DraftOut,
    Rect,
    _Strict,
)

MAX_SHARED_FIELDS = 200
MAX_RECIPIENT_LABEL = 120
MAX_SUBMISSIONS_PER_LINK = 100
MAX_SUBMITTER_NAME = 120
MAX_SUBMITTER_EMAIL = 254
MAX_REJECT_REASON = 1000
# Public submission body cap (a form is small; the editor's 2 MB is not needed).
MAX_SUBMISSION_BODY_BYTES = 256 * 1024
# `secrets.token_urlsafe(32)` = 43 characters; anything far off is not a token.
SHARE_TOKEN_PATTERN = r"^[A-Za-z0-9_-]{32,128}$"
_NO_CONTROL = r"^[^\x00-\x1f\x7f]*$"
EMAIL_PATTERN = r"^[^@\s\x00-\x1f\x7f]{1,64}@[^@\s\x00-\x1f\x7f]{1,189}$"

ShareStatus = Literal["active", "expired", "revoked", "used_up"]
SubmissionStatus = Literal["received", "accepted", "rejected"]
FieldValue = StrictBool | Annotated[str, Field(max_length=MAX_FIELD_VALUE_CHARS)] | list[
    Annotated[str, Field(max_length=MAX_OPTION_CHARS)]
]


# -- internal ---------------------------------------------------------------------


class ShareLinkCreateIn(_Strict):
    """`allowed_fields`: names of fields of the CURRENT version (text,
    multiline, dropdown, radio, checkbox; not read-only, not hidden). The
    link is pinned to that version. `expires_in_hours` default 72, at most
    14 days (336); `max_submissions` default 1."""

    allowed_fields: list[Annotated[str, Field(pattern=FIELD_REF_PATTERN)]] = Field(
        min_length=1, max_length=MAX_SHARED_FIELDS
    )
    recipient_label: str | None = Field(default=None, max_length=MAX_RECIPIENT_LABEL, pattern=_NO_CONTROL)
    expires_in_hours: int | None = Field(default=None, ge=1)
    max_submissions: int = Field(default=1, ge=1, le=MAX_SUBMISSIONS_PER_LINK)

    @field_validator("allowed_fields")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("allowed_fields must be unique")
        return value

    @field_validator("recipient_label")
    @classmethod
    def _blank_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ShareLinkOut(BaseModel):
    """A link as listed -- never its token, never its hash."""

    id: str
    version_no: int
    allowed_fields: list[str]
    recipient_label: str | None
    expires_at: datetime
    max_submissions: int
    submission_count: int
    submissions_pending: int
    status: ShareStatus
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_by_name: str | None
    created_at: datetime


class ShareLinkCreatedOut(ShareLinkOut):
    """The create response: the ONLY place the token ever appears. `url` =
    `<share_base_url>/s#t=<token>` (fragment: never sent to a server)."""

    token: str
    url: str


class SubmissionOut(BaseModel):
    """`values`: `{name: value}`, sensitive field names (ssn, dob, bank ...)
    masked like everywhere else (`app.core.pii`); None + `values_readable`
    false when the encryption key changed since."""

    id: str
    share_link_id: str
    recipient_label: str | None
    version_no: int | None
    status: SubmissionStatus
    submitter_name: str | None
    submitter_email: str | None
    field_count: int
    values: dict[str, bool | str | list[str]] | None
    values_readable: bool
    ip_address: str | None
    created_at: datetime
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    reject_reason: str | None


class AcceptIn(_Strict):
    """The browser session that will own the new draft (the editor then
    resumes it with `POST .../document/draft` and the same session id)."""

    client_session_id: str = Field(pattern=SESSION_ID_PATTERN)


class AcceptOut(BaseModel):
    submission: SubmissionOut
    draft: DraftOut


class RejectIn(_Strict):
    reason: str = Field(min_length=1, max_length=MAX_REJECT_REASON)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a reason is required")
        return value.strip()


# -- public -----------------------------------------------------------------------


class SharedFieldOut(BaseModel):
    """One widget the recipient may fill (a radio group = one entry per
    button, same `name`). `value` = its value in the pinned version."""

    name: str
    type: Literal["text", "multiline", "dropdown", "radio", "checkbox"]
    page_uid: str
    rect: Rect
    required: bool
    tooltip: str | None
    options: list[str] | None
    export_value: str | None
    font: str | None
    size_pt: float | None
    max_len: int | None
    tab_index: int | None
    value: bool | str | list[str] | None


class SharedPageOut(BaseModel):
    page_uid: str
    position: int
    width_pt: float
    height_pt: float
    rotation: int


class ShareSessionOut(BaseModel):
    """`GET /api/share/session`. No contract id, no contractor data."""

    document_title: str
    version_no: int
    fields: list[SharedFieldOut]
    pages: list[SharedPageOut]
    expires_at: datetime
    submissions_left: int


class SubmissionIn(_Strict):
    """`values`: `{field name: value}` for allowed fields only. text /
    multiline / dropdown / radio: a string ("" = empty); checkbox: a bool."""

    values: dict[Annotated[str, Field(pattern=FIELD_REF_PATTERN)], FieldValue] = Field(
        max_length=MAX_SHARED_FIELDS
    )
    submitter_name: str | None = Field(default=None, max_length=MAX_SUBMITTER_NAME, pattern=_NO_CONTROL)
    submitter_email: str | None = Field(default=None, max_length=MAX_SUBMITTER_EMAIL, pattern=EMAIL_PATTERN)

    @field_validator("values")
    @classmethod
    def _list_len(cls, value: dict) -> dict:
        for item in value.values():
            if isinstance(item, list) and len(item) > MAX_FIELD_OPTIONS:
                raise ValueError(f"at most {MAX_FIELD_OPTIONS} values")
        return value

    @field_validator("submitter_name")
    @classmethod
    def _blank_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class SubmissionReceivedOut(BaseModel):
    status: Literal["received"]
    submissions_left: int
