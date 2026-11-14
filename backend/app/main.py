"""FastAPI application entry point."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    assets,
    auth,
    contract_admin,
    contracts,
    dates,
    documents,
    download,
    exports,
    fonts,
    review,
    share,
    share_public,
    signatures,
    templates,
    user_signatures,
)
from app.config import settings
from app.core.exceptions import AppError

logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Template-driven contract review, comparison and signing for FMCSA-regulated carriers.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Content-SHA256", "X-Page-Uids"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message, "details": exc.details},
    )


# A refused request must not be quoted back. FastAPI's own 422 handler puts the
# REJECTED VALUE in every entry (`detail[].input`), and the frontend renders the
# whole list verbatim -- `JSON.stringify(data.detail)` in
# frontend/src/api/client.ts:83 -- so the error banner, and every browser
# console / proxy log that sees the response, ends up holding the request body:
# the captured signature PNG (`image_data`), the signer's e-mail, and whatever
# the user typed onto the document (an annotation `text` is a name, a date, an
# SSN). Same reason `_annotation_stats` in app/api/signatures.py keeps counts
# only out of the audit trail. `loc` / `msg` / `type` say what is wrong without
# repeating the value, and are all the client needs.
MAX_VALIDATION_MESSAGE_CHARS = 500
# `loc` is a path, not content -- but its last element can be a client-supplied
# key (an unknown field name, a `slot_offsets_pt` id), so it is bounded too.
MAX_VALIDATION_LOC_CHARS = 128


def _scrub_validation_error(error: dict) -> dict:
    """One pydantic error, without the value that caused it.

    Kept: `loc` (the frontend's only handle on WHICH field failed --
    `isAnnotationsUnsupported` in frontend/src/api/editor.ts:50 recognises an
    older server by the word "annotation" appearing there), `msg` and `type`.
    Dropped: `input` (the rejected value itself), `ctx` (carries the raw
    validator context, e.g. the ValueError instance) and `url` (a link to the
    pydantic docs, of no use to this UI).

    `msg` is a message the validator wrote, not an echo, but a few of ours
    quote a fragment of the input to be useful -- `AnnotationIn` names the
    character a PDF font cannot print. That is one character, so the message is
    kept whole and merely bounded here.
    """
    message = str(error.get("msg", "Invalid value"))
    if len(message) > MAX_VALIDATION_MESSAGE_CHARS:
        message = message[: MAX_VALIDATION_MESSAGE_CHARS - 3] + "..."
    return {
        "loc": [
            part[:MAX_VALIDATION_LOC_CHARS] if isinstance(part, str) else part
            for part in error.get("loc", ())
        ],
        "msg": message,
        "type": str(error.get("type", "value_error")),
    }


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 in FastAPI's own shape (`{"detail": [...]}`), minus the echoed input.

    The status and the shape are unchanged on purpose: the frontend branches on
    both (client.ts:83, editor.ts:50). Only the value comes out.
    """
    return JSONResponse(
        status_code=422,
        content={"detail": [_scrub_validation_error(error) for error in exc.errors()]},
    )


INTERRUPTED_MESSAGE = (
    "Processing was interrupted when the server restarted. Re-run it, or delete this item."
)


@app.on_event("startup")
def release_interrupted_work() -> None:
    """Inline mode only: tasks run in this process, so after a restart nothing
    is still working on a contract left 'uploaded'/'parsing' or a template left
    'processing'. Mark them failed (with the reason) so they can be re-run or
    deleted instead of showing "Parsing" forever. With a separate Celery
    worker this is not true, so nothing is touched."""
    if not settings.run_tasks_inline:
        return
    from sqlalchemy import update

    from app.core.database import SessionLocal
    from app.models.contract import Contract
    from app.models.template import Template

    db = SessionLocal()
    try:
        contracts = db.execute(
            update(Contract)
            .where(Contract.status.in_(("uploaded", "parsing")))
            .values(status="failed", error_message=INTERRUPTED_MESSAGE)
        ).rowcount
        templates = db.execute(
            update(Template)
            .where(Template.status == "processing")
            .values(status="failed", error_message=INTERRUPTED_MESSAGE)
        ).rowcount
        db.commit()
        if contracts or templates:
            logging.getLogger(__name__).warning(
                "Released interrupted work: %s contract(s), %s template(s) marked failed",
                contracts,
                templates,
            )
    except Exception:  # noqa: BLE001 - never block startup on this
        db.rollback()
        logging.getLogger(__name__).exception("Could not release interrupted work")
    finally:
        db.close()


@app.get("/health", tags=["ops"])
def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.get("/ready", tags=["ops"])
def ready() -> dict:
    """Readiness probe: the database must actually answer."""
    from sqlalchemy import text

    from app.core.database import engine

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}


for module in (
    auth,
    templates,
    contracts,
    contract_admin,
    review,
    signatures,
    dates,
    documents,
    exports,
    download,
    user_signatures,
    assets,
    fonts,
    share,
    share_public,
):
    app.include_router(module.router)
