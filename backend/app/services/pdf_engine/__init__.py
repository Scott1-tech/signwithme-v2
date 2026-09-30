"""PDF editor v2 engine (EDITOR-V2-PLAN §4).

B1: `text.replace` / `text.delete`. B2: `redact`, `image.remove`, `page.*`,
`link.*`, `/SWMPageId`, v7 markup (`markup.py`). B3: `field.*`, form
flattening, field listing (`forms.py`).

Public surface; everything PyMuPDF-specific stays in `adapter.py`.
"""
from app.services.pdf_engine.apply import apply_ops, validate_ops
from app.services.pdf_engine.errors import EngineError
from app.services.pdf_engine.forms import field_dicts, has_visible_widgets
from app.services.pdf_engine.forms import flatten as flatten_forms
from app.services.pdf_engine.inspect import (
    inspect_pages,
    open_warnings,
    page_images,
    page_links,
    page_text_model,
    text_model_dict,
)
from app.services.pdf_engine.model import EngineResult, PageInfo, PageRef
from app.services.pdf_engine.ops import from_model, from_stored, parse_stored, text_ops
from app.services.pdf_engine.pages import PagesPlan, plan_pages
from app.services.pdf_engine.render import ops_hash, render_page_preview


def engine_ref() -> str:
    from app.services.pdf_engine.adapter import ENGINE_REF

    return ENGINE_REF


__all__ = [
    "EngineError",
    "EngineResult",
    "PageInfo",
    "PageRef",
    "PagesPlan",
    "apply_ops",
    "engine_ref",
    "field_dicts",
    "flatten_forms",
    "has_visible_widgets",
    "from_model",
    "from_stored",
    "inspect_pages",
    "open_warnings",
    "ops_hash",
    "page_images",
    "page_links",
    "page_text_model",
    "parse_stored",
    "plan_pages",
    "render_page_preview",
    "text_model_dict",
    "text_ops",
    "validate_ops",
]
