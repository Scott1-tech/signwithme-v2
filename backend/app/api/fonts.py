"""`GET /api/fonts` -- the font registry (EDITOR-V2-PLAN §6, B2).

The only list of families the editor's font picker shows: what the server can
set text in, with its license and the proprietary fonts it stands in for
(`Calibri -> Carlito (substitute)`). `?category=signature` lists the script
fonts of typed signatures instead. Any signed-in user may read it.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from app.core.security import get_current_user
from app.models.user import User
from app.services.fonts import font_catalog

router = APIRouter(prefix="/api/fonts", tags=["fonts"])


class FontOut(BaseModel):
    family: str
    label: str
    styles: list[str]
    license: str
    substitutes_for: list[str]
    scripts: list[str]
    embedded: bool
    category: str


@router.get("", response_model=list[FontOut])
def list_fonts(
    response: Response,
    category: Literal["text", "signature"] = Query(default="text"),
    _user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, max-age=3600"
    return font_catalog(category)
