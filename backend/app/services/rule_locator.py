"""Where the visible fill line under a signature/date slot actually is.

`rule_y` is the canonical (top-left origin, PDF points) y of the TOP edge of the
rule's ink -- the thing a reader sees -- not of a text baseline or a glyph box.

Why rendering and not font metrics: in the production Docusign package the
underscore ink starts +2.0...+2.13pt below the text baseline, while reportlab's
Helvetica puts it ~0.8pt below. Any metric-based guess is therefore wrong on one
of them. The page is rendered (pypdfium2) in a narrow window under the field box
and the dark pixel rows are measured directly, so glyph runs, vector rules and
thin filled rects are all handled by one code path.

Also here: the contract-facing glue the API needs around it -- lazily adding
`rule_y` to stored template schemas, the contract's real page count, and the
signing view of a stored comparison (slots on pages the document does not have
removed, `rule_y` taken from the current template schema).
"""
from __future__ import annotations

import copy
import io
import logging
import math
import threading
from collections import OrderedDict

from app.services.geometry import BBox

logger = logging.getLogger(__name__)

RULE_Y_KEY = "rule_y"

# Search window relative to the field box's lower edge (canonical y grows down).
# Above: a box already sitting on or slightly below the ink. Below: one font
# body, since a text-detected box can end a whole glyph height above the ink.
RULE_SEARCH_ABOVE = 6.0
RULE_SEARCH_BELOW = 16.0
# The rule must darken at least this fraction of the field width on one pixel
# row: a label, a pre-printed date or a stray glyph never does.
RULE_MIN_COVERAGE = 0.5
# Thicker than this is a border or a filled block, not a fill line.
RULE_MAX_THICKNESS = 2.0
# Render resolution. 8 px/pt gives a 0.125pt row; the 50%-grey threshold puts
# the measured edge within half a row (~0.06pt) of the true ink edge.
RENDER_SCALE = 8.0
DARK_LUMA = 128

# pdfium is not thread-safe and sync FastAPI routes run in a thread pool.
_PDFIUM_LOCK = threading.RLock()


class RuleLocator:
    """Open a PDF once and locate rules under any number of boxes.

    Use as a context manager. Never raises from `locate`: a line that cannot be
    found is `None`, and the caller falls back to the box's own lower edge.
    """

    def __init__(self, pdf: bytes | str | None):
        self._doc = None
        self._plumber = None
        self._source = pdf
        if pdf is None:
            return
        try:
            import pypdfium2 as pdfium

            with _PDFIUM_LOCK:
                self._doc = pdfium.PdfDocument(pdf if isinstance(pdf, str) else io.BytesIO(pdf))
        except Exception:  # noqa: BLE001 - measured rules are an improvement only
            logger.exception("Could not open the PDF to locate fill rules")
            self._doc = None

    def __enter__(self) -> "RuleLocator":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with _PDFIUM_LOCK:
            if self._doc is not None:
                try:
                    self._doc.close()
                except Exception:  # noqa: BLE001
                    pass
                self._doc = None
        if self._plumber is not None:
            try:
                self._plumber.close()
            except Exception:  # noqa: BLE001
                pass
            self._plumber = None

    @property
    def page_count(self) -> int | None:
        if self._doc is None:
            return None
        with _PDFIUM_LOCK:
            return len(self._doc)

    def locate(self, page_number: int, bbox: BBox) -> float | None:
        """Top edge of the rule's ink under `bbox` on 1-indexed `page_number`."""
        try:
            if self._doc is not None:
                with _PDFIUM_LOCK:
                    if not 1 <= page_number <= len(self._doc):
                        return None
                    page = self._doc[page_number - 1]
                    try:
                        rotation = page.get_rotation() % 360
                        if rotation == 0:
                            return _rendered_rule_top(page, bbox)
                        if rotation in (90, 180, 270):
                            # Same measurement, on the page AS DISPLAYED (the
                            # space canonical boxes live in). Before this, a
                            # rotated page only saw vector rules, so a '____'
                            # glyph run was never found (rule_y None -> the
                            # signature sat on the box bottom, 11 pt high).
                            return _rendered_rule_top_rotated(page, bbox, rotation)
                    finally:
                        page.close()
            # A page pdfium cannot open (or a non-quarter /Rotate): vector rules only.
            return self._vector_rule_top(page_number, bbox)
        except Exception:  # noqa: BLE001
            logger.exception("Could not locate the fill rule for %s on page %s", bbox, page_number)
            return None

    def _vector_rule_top(self, page_number: int, bbox: BBox) -> float | None:
        if self._source is None:
            return None
        import pdfplumber

        if self._plumber is None:
            source = self._source
            self._plumber = pdfplumber.open(source if isinstance(source, str) else io.BytesIO(source))
        if not 1 <= page_number <= len(self._plumber.pages):
            return None
        page = self._plumber.pages[page_number - 1]
        low, high = bbox.y1 - RULE_SEARCH_ABOVE, bbox.y1 + RULE_SEARCH_BELOW
        covered: dict[float, float] = {}
        try:
            objects = list(page.lines or []) + list(page.rects or [])
        finally:
            page.close()  # do not keep every parsed page in memory
        for obj in objects:
            top, bottom = float(obj["top"]), float(obj["bottom"])
            if bottom - top > RULE_MAX_THICKNESS or not low <= top <= high:
                continue
            width = min(bbox.x1, float(obj["x1"])) - max(bbox.x, float(obj["x0"]))
            if width > 0:
                key = round(top, 2)
                covered[key] = covered.get(key, 0.0) + width
        candidates = [y for y, w in covered.items() if w >= bbox.width * RULE_MIN_COVERAGE]
        return min(candidates, key=lambda y: abs(y - bbox.y1)) if candidates else None


def _rendered_rule_top(page, bbox: BBox) -> float | None:
    """Render the window under `bbox` and return the nearest thin dark band's top."""
    mb_left, _mb_bottom, _mb_right, mb_top = (float(v) for v in page.get_mediabox())
    cb_left, cb_bottom, cb_right, cb_top = (float(v) for v in page.get_cropbox())

    # Window in PDF user space (y up). Canonical x is relative to the mediabox
    # left edge, canonical y is measured down from the mediabox top (pdfplumber).
    win_left = max(mb_left + bbox.x, cb_left)
    win_right = min(mb_left + bbox.x1, cb_right)
    win_top = min(mb_top - (bbox.y1 - RULE_SEARCH_ABOVE), cb_top)
    win_bottom = max(mb_top - (bbox.y1 + RULE_SEARCH_BELOW), cb_bottom)
    if win_right - win_left < 1.0 or win_top - win_bottom < 1.0:
        return None

    crop = (win_left - cb_left, win_bottom - cb_bottom, cb_right - win_right, cb_top - win_top)
    bitmap = page.render(scale=RENDER_SCALE, crop=crop, may_draw_forms=True)
    try:
        image = bitmap.to_pil().convert("L")
    finally:
        bitmap.close()

    width, height = image.size
    if width <= 0 or height <= 0:
        return None
    dark = image.point(lambda v: 255 if v < DARK_LUMA else 0).tobytes()
    # The coverage requirement is against the FIELD width; the window can be
    # narrower only where the page edge clipped it.
    needed = max(1, int(RULE_MIN_COVERAGE * bbox.width * RENDER_SCALE + 0.5))
    needed = min(needed, width)
    qualifying = [dark[r * width:(r + 1) * width].count(255) >= needed for r in range(height)]

    anchor_row = (win_top - (mb_top - bbox.y1)) * RENDER_SCALE  # box lower edge, in rows
    best = _nearest_band(qualifying, anchor_row)
    if best is None:
        return None
    pdf_y = win_top - best / RENDER_SCALE
    return round(mb_top - pdf_y, 3)


def _nearest_band(qualifying: list[bool], anchor_row: float) -> int | None:
    """First row of the thin run of qualifying rows nearest `anchor_row`."""
    height = len(qualifying)
    max_rows = RULE_MAX_THICKNESS * RENDER_SCALE
    best: tuple[float, int] | None = None
    row = 0
    while row < height:
        if not qualifying[row]:
            row += 1
            continue
        start = row
        while row < height and qualifying[row]:
            row += 1
        if row - start > max_rows:
            continue  # a filled block or a thick border
        distance = abs(start - anchor_row)
        if best is None or distance < best[0]:
            best = (distance, start)
    return None if best is None else best[1]


def _rendered_rule_top_rotated(page, bbox: BBox, rotation: int) -> float | None:
    """`_rendered_rule_top` for a page with /Rotate 90/180/270.

    Canonical boxes are in the page AS DISPLAYED (pdfplumber applies /Rotate;
    top-left origin of the displayed mediabox), and pdfium renders the page as
    displayed too, with `crop` applied after rotation. So the window is taken in
    displayed coordinates, relative to the DISPLAYED cropbox (the unrotated
    cropbox mapped with the same `unrotated_to_displayed` the builder uses).

    The bitmap's first row is placed where pypdfium2 actually starts it
    (`ceil(crop * scale)` pixels), not at the requested window edge.
    """
    from app.services.geometry import unrotated_to_displayed

    mb_left, mb_bottom, mb_right, mb_top = (float(v) for v in page.get_mediabox())
    cb_left, cb_bottom, cb_right, cb_top = (float(v) for v in page.get_cropbox())
    mb_width, mb_height = mb_right - mb_left, mb_top - mb_bottom
    corners = [
        unrotated_to_displayed(x - mb_left, mb_top - y, mb_width, mb_height, rotation)
        for x, y in ((cb_left, cb_bottom), (cb_right, cb_top))
    ]
    cx0, cx1 = sorted(point[0] for point in corners)
    cy0, cy1 = sorted(point[1] for point in corners)  # displayed, y grows down

    win_left = max(bbox.x, cx0)
    win_right = min(bbox.x1, cx1)
    win_top = max(bbox.y1 - RULE_SEARCH_ABOVE, cy0)
    win_bottom = min(bbox.y1 + RULE_SEARCH_BELOW, cy1)
    if win_right - win_left < 1.0 or win_bottom - win_top < 1.0:
        return None

    crop = (win_left - cx0, cy1 - win_bottom, cx1 - win_right, win_top - cy0)
    bitmap = page.render(scale=RENDER_SCALE, crop=crop, may_draw_forms=True)
    try:
        image = bitmap.to_pil().convert("L")
    finally:
        bitmap.close()

    width, height = image.size
    if width <= 0 or height <= 0:
        return None
    dark = image.point(lambda v: 255 if v < DARK_LUMA else 0).tobytes()
    needed = max(1, int(RULE_MIN_COVERAGE * bbox.width * RENDER_SCALE + 0.5))
    needed = min(needed, width)
    qualifying = [dark[r * width:(r + 1) * width].count(255) >= needed for r in range(height)]

    first_row_y = cy0 + math.ceil(crop[3] * RENDER_SCALE) / RENDER_SCALE
    anchor_row = (bbox.y1 - first_row_y) * RENDER_SCALE  # box lower edge, in rows
    best = _nearest_band(qualifying, anchor_row)
    if best is None:
        return None
    return round(first_row_y + best / RENDER_SCALE, 3)


# -- template schema ---------------------------------------------------------


def _slot_fields(field_schema: dict):
    """Every signature/date slot field (incl. a date paired by id), with its page."""
    pages = (field_schema or {}).get("pages", []) or []
    paired = {
        field.get("paired_date_field_id")
        for page in pages
        for field in page.get("fields", []) or []
        if field.get("field_type") == "signature" and field.get("paired_date_field_id")
    }
    for page in pages:
        page_number = page.get("page_number")
        for field in page.get("fields", []) or []:
            if field.get("field_type") in ("signature", "date") or field.get("field_id") in paired:
                yield int(field.get("page") or page_number or 0), field


def needs_rule_y(field_schema: dict) -> bool:
    """True when any slot field lacks `rule_y` (schemas analysed before it existed)."""
    return any(RULE_Y_KEY not in field for _, field in _slot_fields(field_schema))


def with_rule_y(pdf: bytes | str, field_schema: dict) -> dict:
    """A copy of the schema with `rule_y` on every slot field. Purely additive:
    no existing key is changed or removed."""
    schema = copy.deepcopy(field_schema)
    with RuleLocator(pdf) as locator:
        for page_number, field in _slot_fields(schema):
            rule = None
            try:
                if field.get("bbox") and page_number >= 1:
                    rule = locator.locate(page_number, BBox.from_dict(field["bbox"]))
            except Exception:  # noqa: BLE001 - one bad field must not block the template
                logger.exception("Failed to locate the rule for %s", field.get("field_id"))
            field[RULE_Y_KEY] = rule
    return schema


def rule_summary(field_schema: dict) -> dict:
    found = missing = 0
    for _, field in _slot_fields(field_schema):
        if field.get(RULE_Y_KEY) is None:
            missing += 1
        else:
            found += 1
    return {"rules_found": found, "rules_missing": missing}


def ensure_template_rules(db, template) -> dict:
    """The template schema with `rule_y` present, computing and storing it once
    for templates analysed before it existed (same lazy pattern as the
    template baseline in `process_contract_task`)."""
    schema = template.field_schema or {}
    if not needs_rule_y(schema) or not template.file_path:
        return schema
    from app.services.storage import storage

    try:
        schema = with_rule_y(storage.get_bytes(template.file_path), schema)
    except Exception:  # noqa: BLE001 - never block signing on an improvement
        logger.exception("Could not derive fill rules for template %s", template.id)
        return template.field_schema or {}
    template.field_schema = schema  # reassign: the JSON column must see the change
    db.commit()
    return schema


# -- contract page count -------------------------------------------------------

_PAGE_COUNTS: "OrderedDict[tuple[str, str], int | None]" = OrderedDict()
_PAGE_COUNTS_MAX = 512
_PAGE_COUNTS_LOCK = threading.Lock()


def pdf_page_count(data: bytes) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data), strict=False).pages)
    except Exception:  # noqa: BLE001
        logger.exception("Could not count the pages of a stored PDF")
        return None


def contract_page_count(contract) -> int | None:
    """Pages in the contract's own uploaded PDF (the document that is signed).

    There is no column for it and `extracted_data` carries no page numbers, so
    it is read from the stored file. The upload is immutable and identified by
    its hash, so the result is cached per (path, hash). `None` = unknown.
    """
    # PDF editor v2: the signed document is the CURRENT VERSION, whose page
    # count is stored on its row. No document (legacy) -> the original upload,
    # exactly as before.
    from app.services.documents import current_version

    version = current_version(contract)
    if version is not None:
        return int(version.page_count)
    if not getattr(contract, "file_path", None):
        return None
    key = (str(contract.file_path), str(contract.file_hash or ""))
    with _PAGE_COUNTS_LOCK:
        if key in _PAGE_COUNTS:
            _PAGE_COUNTS.move_to_end(key)
            return _PAGE_COUNTS[key]
    from app.services.storage import storage

    try:
        count = pdf_page_count(storage.get_bytes(contract.file_path))
    except Exception:  # noqa: BLE001
        logger.exception("Could not read contract %s to count its pages", contract.id)
        return None  # not cached: a transient storage error must not stick
    with _PAGE_COUNTS_LOCK:
        _PAGE_COUNTS[key] = count
        while len(_PAGE_COUNTS) > _PAGE_COUNTS_MAX:
            _PAGE_COUNTS.popitem(last=False)
    return count


def page_in_range(page, page_count: int | None) -> bool:
    """Unknown page count never filters (nothing is hidden on a guess)."""
    if page_count is None or page is None:
        return True
    try:
        return 1 <= int(page) <= page_count
    except (TypeError, ValueError):
        return True


def signing_comparison(db, contract) -> dict:
    """The stored comparison as the signing UI must see it.

    Read-time only (nothing about the contract is rewritten): slots on pages the
    uploaded document does not have are dropped, and every remaining slot gets
    `rule_y` from the template's CURRENT schema, so existing contracts need no
    re-parse. `can_sign` and every other key are returned untouched.
    """
    from app.models.template import Template
    from app.services.comparison_engine import _index_schema

    result = dict(contract.comparison_result or {})
    slots = result.get("signature_slots") or []
    if not slots:
        return result
    template = db.get(Template, contract.template_id)
    index = _index_schema(ensure_template_rules(db, template)) if template is not None else {}
    page_count = contract_page_count(contract)
    # PDF editor v2 (plan §5.4): the stable page id of each slot's page in the
    # current version. Legacy contract (no document yet): no id, key stays null.
    from app.services.documents import signing_source

    source = signing_source(contract)
    view = []
    for slot in slots:
        entry = dict(slot)
        entry[RULE_Y_KEY] = (index.get(slot.get("field_id")) or {}).get(RULE_Y_KEY)
        if not source.legacy:
            # B2 (plan §5.4): `page` is the POSITION in the current version
            # (what the viewer shows), `page_uid` its stable id; a slot whose
            # page was removed is not offered at all; on a page rotated since
            # the template the box follows the page (and `rule_y` is dropped).
            template_page = slot.get("page")
            position = source.position_for(template_page)
            if template_page is not None and position is None:
                continue
            entry["page"] = position
            entry["page_uid"] = source.page_uid_at(position)
            if source.rotated_since_template(template_page):
                entry["bbox"] = source.template_bbox(template_page, slot.get("bbox"))
                entry[RULE_Y_KEY] = None
        if not page_in_range(entry.get("page"), page_count):
            continue
        view.append(entry)
    result["signature_slots"] = view
    return result
