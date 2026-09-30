"""One-page draft previews, cached (EDITOR-V2-PLAN §7 "Preview").

`GET .../draft/pages/{uid}/preview?hide=b12` answers the base version's page
with the draft's ops of that page applied -- except the op on the hidden block
-- and the hidden block's own glyphs removed, so the editor can lay its live
text editor exactly where the block was. Same engine path as a commit, so the
preview is the export.

LRU: at most 64 entries and 200 MB.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict

from app.services.pdf_engine import adapter, errors
from app.services.pdf_engine.apply import execute_plan, page_index
from app.services.pdf_engine.model import PageRef
from app.services.pdf_engine.ops import TextOp
from app.services.pdf_engine.text_edit import plan_page, resolve_target

CACHE_MAX_ENTRIES = 64
CACHE_MAX_BYTES = 200 * 1024 * 1024

_CACHE: "OrderedDict[tuple, bytes]" = OrderedDict()
_CACHE_BYTES = 0
_LOCK = threading.Lock()


def ops_hash(ops: list[dict]) -> str:
    return hashlib.sha256(json.dumps(ops, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _get(key: tuple) -> bytes | None:
    with _LOCK:
        data = _CACHE.get(key)
        if data is not None:
            _CACHE.move_to_end(key)
        return data


def _put(key: tuple, data: bytes) -> None:
    global _CACHE_BYTES
    with _LOCK:
        if key in _CACHE:
            return
        _CACHE[key] = data
        _CACHE_BYTES += len(data)
        while _CACHE and (len(_CACHE) > CACHE_MAX_ENTRIES or _CACHE_BYTES > CACHE_MAX_BYTES):
            _old, dropped = _CACHE.popitem(last=False)
            _CACHE_BYTES -= len(dropped)


def clear_cache() -> None:
    global _CACHE_BYTES
    with _LOCK:
        _CACHE.clear()
        _CACHE_BYTES = 0


def _targets(page, op: TextOp, block_id: str) -> bool:
    """Does `op` edit block `block_id`? By its id, or -- for an op saved
    against an older text model (B1.1 renumbered blocks) -- by where it
    resolves."""
    if op.block_id == block_id:
        return True
    try:
        return resolve_target(page, op).block_id == block_id
    except errors.EngineError:
        return False


def render_page_preview(
    pdf_bytes: bytes,
    base_sha256: str,
    page_refs: list[PageRef],
    page_uid: str,
    ops: list[TextOp],
    ops_digest: str,
    hide_block: str | None = None,
) -> bytes:
    key = (base_sha256, page_uid, ops_digest, hide_block or "")
    cached = _get(key)
    if cached is not None:
        return cached
    index = page_index(page_refs, page_uid, None)
    with adapter.Doc(pdf_bytes) as doc:
        page = doc.page_text(index)
        if hide_block is not None:
            block = page.block(hide_block)
            if block is None:
                raise errors.EngineError(errors.TARGET_NOT_FOUND, "No such text block on this page", {})
        from app.services.pdf_engine.ops import ImageRemoveOp, RedactOp

        for op in ops:
            if op.page_uid != page_uid:
                continue
            if isinstance(op, RedactOp):
                doc.redact_area(index, list(op.rects), op.fill)
            elif isinstance(op, ImageRemoveOp):
                doc.remove_image(index, op.xref, op.bbox)
        page_ops = [
            op for op in ops
            if isinstance(op, TextOp) and op.page_uid == page_uid
            and not (hide_block and _targets(page, op, hide_block))
        ]
        plan = plan_page(page, page_ops) if page_ops else None
        if plan is None:
            from app.services.pdf_engine.text_edit import PagePlan

            plan = PagePlan()
        if hide_block is not None:
            plan.removal.extend(pair for line in page.block(hide_block).lines for pair in line.chars)
        if plan.removal or plan.fragments:
            execute_plan(doc, index, page, plan)
        data = doc.single_page(index)
    _put(key, data)
    return data
