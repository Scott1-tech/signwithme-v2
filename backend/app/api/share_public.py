"""Public share-for-fill API (EDITOR-V2-PLAN §8, §11 Q4) -- NO account.

`/api/share/*`, the token in the `X-Share-Token` header (the page reads it
from the URL fragment, which no browser sends to a server or in a Referer).

Every request, in this order:
  1. the client address (`client_ip`: the socket peer, or -- only when that
     peer is a proxy named in `settings.trusted_proxy_cidrs` -- the address the
     proxy saw; X-Forwarded-For from anyone else is ignored) must be inside
     `settings.share_allowed_cidrs` -- else 404;
  2. the address must not be locked by `share_throttle` -- else 429;
  3. the token must name a link that is not revoked, not expired, not used
     up, whose contract is not finalised -- else the SAME 404, and one failure
     for the address.
Only then is a body looked at, so an outsider learns nothing but "404".

Every response (errors included) carries `Cache-Control: no-store`,
`Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`. No
response names the contract (no id, no contractor), and nothing here logs a
token.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppError, PayloadTooLargeError
from app.core.security import client_ip
from app.core.throttle import share_throttle
from app.models.contract import Contract
from app.models.document import DocumentVersion
from app.models.share import ShareLink
from app.models.template import Template
from app.schemas.share import (
    MAX_SUBMISSION_BODY_BYTES,
    SHARE_TOKEN_PATTERN,
    ShareSessionOut,
    SubmissionIn,
    SubmissionReceivedOut,
)
from app.services import audit, share
from app.services.documents import now_utc
from app.services.storage import storage

_TOKEN_RE = re.compile(SHARE_TOKEN_PATTERN)
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class _PublicRoute(APIRoute):
    """Adds the security headers to every answer, the error ones too (those
    are otherwise built by the app-wide exception handlers, past any route)."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except AppError as exc:
                response = JSONResponse(
                    status_code=exc.status_code,
                    content={"error": exc.code, "message": exc.message, "details": exc.details},
                )
            except RequestValidationError as exc:
                from app.main import _scrub_validation_error

                response = JSONResponse(
                    status_code=422,
                    content={"detail": [_scrub_validation_error(error) for error in exc.errors()]},
                )
            for key, value in SECURITY_HEADERS.items():
                response.headers[key] = value
            return response

        return handler


router = APIRouter(prefix="/api/share", tags=["share (public)"], route_class=_PublicRoute)


def _peer(request: Request) -> str | None:
    """The requesting client's address, for the network check and the throttle.

    Behind the frontend's nginx the socket peer is always the nginx container
    -- a private address, so every visitor passed `share_allowed_cidrs`, and
    every visitor shared one throttle bucket (20 bad tokens from anyone locked
    out all share links). `client_ip` returns the socket peer unless that peer
    is a proxy listed in `settings.trusted_proxy_cidrs`, in which case it
    returns the address that proxy saw. With that setting empty (the default)
    X-Forwarded-For is ignored exactly as before."""
    return client_ip(request)


def share_link(request: Request, db: Session = Depends(get_db)) -> ShareLink:
    """Steps 1-3 above. Returns a link that may be used right now."""
    peer = _peer(request)
    if not share.peer_allowed(peer):
        raise share.not_found()
    locked_for = share_throttle.is_locked(peer)
    if locked_for:
        raise share.TooManyAttempts(
            "Too many failed attempts. Try again later.", {"retry_after_seconds": locked_for}
        )
    token = request.headers.get("x-share-token", "")
    link = None
    if _TOKEN_RE.match(token):
        link = db.scalar(select(ShareLink).where(ShareLink.token_hash == share.token_hash(token)))
    if link is None or not share.usable(db, link):
        share_throttle.record_failure(peer)
        raise share.not_found()
    return link


def _body_limit(request: Request) -> None:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            too_big = int(length) > MAX_SUBMISSION_BODY_BYTES
        except ValueError:
            too_big = False
        if too_big:
            raise PayloadTooLargeError("Request body too large", {"limit": MAX_SUBMISSION_BODY_BYTES})


def _touch(db: Session, link: ShareLink) -> None:
    link.last_used_at = now_utc()
    db.commit()


@router.get("/session", response_model=ShareSessionOut)
def get_session(link: ShareLink = Depends(share_link), db: Session = Depends(get_db)):
    """What the recipient needs to draw the form: the allowed fields of the
    pinned version (positions in pt, top-left, per `page_uid`) and the pages."""
    version = db.get(DocumentVersion, link.version_id)
    contract = db.get(Contract, link.contract_id)
    template = db.get(Template, contract.template_id) if contract is not None else None
    allowed = set(link.allowed_fields or [])
    fields = [
        {
            "name": row["name"],
            "type": row["type"],
            "page_uid": row["page_uid"],
            "rect": row["rect"],
            "required": row["required"],
            "tooltip": row["tooltip"],
            "options": row["options"],
            "export_value": row["export_value"],
            "font": row["font"],
            "size_pt": row["size_pt"],
            "max_len": row["max_len"],
            "tab_index": row["tab_index"],
            "value": row["value"],
        }
        for row in share.version_fields(version)
        if row["name"] in allowed and share.shareable_problem([row]) is None
    ]
    pages = [
        {
            "page_uid": page.page_uid,
            "position": page.position,
            "width_pt": page.width_pt,
            "height_pt": page.height_pt,
            "rotation": page.rotation,
        }
        for page in version.pages
    ]
    out = {
        "document_title": template.name if template is not None else "Document",
        "version_no": version.version_no,
        "fields": fields,
        "pages": pages,
        "expires_at": share.documents._aware(link.expires_at),
        "submissions_left": share.submissions_left(link),
    }
    _touch(db, link)
    return out


@router.get("/file")
def get_file(link: ShareLink = Depends(share_link), db: Session = Depends(get_db)):
    """The pinned version's PDF. A version never holds markup or signatures
    (those are drawn only into an export / the final), so none leak here."""
    version = db.get(DocumentVersion, link.version_id)
    data = storage.get_bytes(version.file_path)
    _touch(db, link)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="document.pdf"'},
    )


@router.post("/submissions", response_model=SubmissionReceivedOut, status_code=201)
def submit(
    body: SubmissionIn,
    request: Request,
    link: ShareLink = Depends(share_link),
    _limit: None = Depends(_body_limit),
    db: Session = Depends(get_db),
):
    """The token is checked BEFORE the size and the body (dependencies run
    in order, the body is validated after them)."""
    version = db.get(DocumentVersion, link.version_id)
    ops = share.validate_submission(version, list(link.allowed_fields or []), body.values)
    now = now_utc()
    # One more submission, only while the link is still good (a second
    # request racing for the last submission gets the same 404).
    claimed = db.execute(
        update(ShareLink)
        .where(
            ShareLink.id == link.id,
            ShareLink.revoked_at.is_(None),
            ShareLink.expires_at > now,
            ShareLink.submission_count < ShareLink.max_submissions,
        )
        .values(submission_count=ShareLink.submission_count + 1, last_used_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise share.not_found()
    peer = _peer(request)
    submission = share.new_submission(link, ops, body, peer, request.headers.get("user-agent"))
    db.add(submission)
    db.flush()
    audit.log(
        db,
        audit.FORM_SUBMISSION_RECEIVED,
        contract_id=link.contract_id,
        details={
            "submission_id": str(submission.id),
            "share_link_id": str(link.id),
            "fields": submission.field_count,
        },
        ip_address=peer,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(link)
    return {"status": "received", "submissions_left": share.submissions_left(link)}
