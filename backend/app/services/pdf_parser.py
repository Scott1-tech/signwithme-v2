"""Extract filled values from an uploaded contract, guided by a template schema.

For each field in the template we crop the corresponding region of the uploaded
PDF and read whatever the contractor put there. Signatures are detected by the
presence of non-text content (an embedded image or vector strokes) inside the
region -- which is how DocuSign and most e-sign tools stamp a signature.

What is VISIBLE in a slot is what counts: a signature image or a date the
contract shows is filled, even when the template file shows the very same one
(a signed package uploaded as the form). Leakage from the surrounding form is
kept out by content and geometry, never by subtracting the template: signature
images must sit on the rule or lie mostly inside the box, typed text is read
only from the rule band and never as a ``Label:``, and a date slot only counts
as filled when what it holds is shaped like a date.

The template baseline (``template_baseline``, an additive key on the stored
field schema) is still recorded per signature/date field. It no longer removes
anything; it tells an operator that a template already arrives filled
(:meth:`PdfParser.prefilled_summary`).
"""
from __future__ import annotations

import copy
import hashlib
import logging
import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field as dc_field
from typing import Any

import pdfplumber

from app.services.geometry import BBox
from app.services.validators import is_blank, normalize_text

logger = logging.getLogger(__name__)

# Crops are padded slightly: contractors rarely write exactly inside the ruled box.
CROP_PADDING = 2.0
SIGNATURE_MIN_STROKES = 4
# Measured on the production packages: a stamp on its own rule straddles the
# box's bottom edge (centre in the lower half, 0.07-0.47 of the box covered);
# a stamp hanging down from the row ABOVE has its centre near the box's top
# edge and only 0.13-0.43 of its own area inside the box. Box coverage alone
# cannot separate the two (0.06-0.18 either way), so an image counts only when
# it sits on the rule or lies mostly inside the box.
SIGNATURE_MIN_IMAGE_AREA_RATIO = 0.05
SIGNATURE_MIN_IMAGE_INSIDE_FRACTION = 0.6
# A typed-name signature sits on the rule. Text is only read from the rule row
# and a band this far above it -- 12pt is the tightest common leading, so a
# sentence on the previous line never enters the band.
SIGNATURE_TEXT_BAND = 8.0
SIGNATURE_TEXT_BELOW = 12.0
# Additive field-schema key carrying what the blank template already holds.
BASELINE_KEY = "template_baseline"
BASELINE_FIELD_TYPES = ("signature", "date")
# A template whose own file already fills more than this share of its
# signature or date slots was most likely a signed package, not a blank form.
PREFILLED_WARNING_RATIO = 0.5
# An image marks a checkbox option when its centre lies inside the option box
# and it is at most this much larger than the box on either axis (the
# production tick is an 8x8pt image over a ~10x12pt box; a signature is 50pt+).
CHECKBOX_IMAGE_MAX_SCALE = 1.5
# "Same document as its template": the least similar page's text agrees at
# least this well (token-multiset Dice) and this share of the signature slots
# carry identical images. Measured on production: the re-uploaded template
# scores 1.000 on all 44 pages, while a genuinely filled contract of the same
# form scores 0.954 and 0.979 on its field pages -- 0.98 would sit 0.001 away
# from a false match.
TEMPLATE_MATCH_PAGE_SIMILARITY = 0.995
TEMPLATE_MATCH_IMAGE_RATIO = 0.9


@dataclass
class ExtractedField:
    field_id: str
    field_type: str
    page: int
    value: str | None = None
    confidence: float = 0.0
    detected: bool = False
    selected: list[str] = dc_field(default_factory=list)
    checked: bool = False
    has_signature: bool = False
    rows: list[list[str]] = dc_field(default_factory=list)
    extra: dict = dc_field(default_factory=dict)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "value": self.value,
            "confidence": round(self.confidence, 2),
            "detected": self.detected,
        }
        if self.field_type in ("checkbox_group",):
            data["selected"] = self.selected
        if self.field_type == "checkbox":
            data["checked"] = self.checked
        if self.field_type == "signature":
            data["has_signature"] = self.has_signature
        if self.field_type == "table":
            data["rows"] = self.rows
        if self.extra:
            data["extra"] = self.extra
        return data


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# A page with fewer than this many extractable characters is treated as an
# image of a page rather than a page of text.
MIN_CHARS_PER_TEXT_PAGE = 20


@dataclass
class TextLayerReport:
    page_count: int
    pages_without_text: list[int]
    needs_ocr: bool

    def to_dict(self) -> dict:
        return {
            "page_count": self.page_count,
            "pages_without_text": self.pages_without_text,
            "needs_ocr": self.needs_ocr,
        }

    @property
    def message(self) -> str:
        return (
            f"{len(self.pages_without_text)} of {self.page_count} pages contain no extractable "
            "text, so this file is a scan rather than a digital PDF. Run OCR on it "
            "(for example `ocrmypdf in.pdf out.pdf`) and upload the searchable version."
        )


class PdfParser:
    def page_count(self, pdf_path: str) -> int:
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)

    def analyze_text_layer(self, pdf_path: str) -> TextLayerReport:
        """Distinguish a digital PDF from a scan.

        Without this check a scanned contract extracts nothing and is reported
        as 100% of fields missing -- which looks like a broken comparison
        rather than a document that needs OCR first.
        """
        empty: list[int] = []
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            for number, page in enumerate(pdf.pages, start=1):
                if len(page.chars) < MIN_CHARS_PER_TEXT_PAGE:
                    empty.append(number)
        # Tolerate a few genuinely blank or image-only pages in an otherwise
        # digital document; flag the file only when most of it is unreadable.
        needs_ocr = total > 0 and len(empty) > total / 2
        return TextLayerReport(page_count=total, pages_without_text=empty, needs_ocr=needs_ocr)

    def extract(
        self, pdf_path: str, field_schema: dict, page_map: dict[int, int | None] | None = None
    ) -> dict[str, dict]:
        """Return {field_id: extracted-dict} for every field in the schema.

        Each slot is read for what it visibly holds; the template baseline is
        never subtracted (see the module docstring).

        `page_map` (PDF editor v2): template page N -> its position in the
        document being read, `None` for a page that is no longer there (its
        fields come out missing). Absent = identity, the behaviour before v2.
        """
        results: dict[str, dict] = {}
        # Filled AcroForm widgets (editor v2 B3 `field.value`, or an uploaded
        # fillable PDF): their values live in the widget, not in the page's
        # text, so the page reading alone never sees them. {} without a form.
        widgets = _form_widgets(pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            for page_schema in field_schema.get("pages", []):
                page_number = int(page_schema.get("page_number", 0))
                position = _mapped(page_number, page_map)
                if position is None or not 1 <= position <= len(pdf.pages):
                    continue
                page = pdf.pages[position - 1]
                context = _PageContext(page)
                page_widgets = widgets.get(position) or []
                for field in page_schema.get("fields", []):
                    try:
                        extracted = self._extract_field(context, field, page_number)
                        if page_widgets:
                            extracted = _with_widget_value(extracted, field, page_widgets)
                    except Exception:  # never let one bad region abort the document
                        logger.exception("Failed to extract field %s", field.get("field_id"))
                        extracted = ExtractedField(
                            field_id=field.get("field_id", "unknown"),
                            field_type=field.get("field_type", "text_line"),
                            page=page_number,
                        )
                    results[extracted.field_id] = extracted.to_dict()
        return results

    # -- template baseline ---------------------------------------------------

    @staticmethod
    def needs_template_baseline(field_schema: dict) -> bool:
        """True when any signature/date field lacks its baseline -- schemas
        analysed before the baseline existed."""
        for page_schema in field_schema.get("pages", []):
            for field in page_schema.get("fields", []):
                if field.get("field_type") in BASELINE_FIELD_TYPES and BASELINE_KEY not in field:
                    return True
        return False

    def with_template_baseline(self, template_pdf_path: str, field_schema: dict) -> dict:
        """Return a copy of the schema with ``template_baseline`` on every
        signature and date field. Purely additive: no existing key changes.

        The baseline is what the template file already shows inside the box --
        image hashes, vector stroke count, the text tokens -- plus a
        ``prefilled`` flag meaning the template alone already reads as filled
        (a signed package uploaded as a template, a pre-printed date). It is
        informational only: extraction never subtracts it.
        """
        schema = copy.deepcopy(field_schema)
        with pdfplumber.open(template_pdf_path) as pdf:
            for page_schema in schema.get("pages", []):
                page_number = int(page_schema.get("page_number", 0))
                if not 1 <= page_number <= len(pdf.pages):
                    continue
                context = _PageContext(pdf.pages[page_number - 1])
                for field in page_schema.get("fields", []):
                    if field.get("field_type") not in BASELINE_FIELD_TYPES:
                        continue
                    try:
                        field[BASELINE_KEY] = context.baseline(
                            BBox.from_dict(field["bbox"]), field["field_type"]
                        )
                    except Exception:  # a bad region must not block the template
                        logger.exception("Failed to baseline field %s", field.get("field_id"))
                        field[BASELINE_KEY] = {"image_hashes": [], "strokes": 0, "text_tokens": [], "prefilled": False}
        return schema

    @staticmethod
    def baseline_summary(field_schema: dict) -> dict:
        """How many signature/date slots the template already fills by itself.
        Surfaced in the audit log so an operator sees a signed package was
        uploaded where a blank form was expected."""
        prefilled_signatures = 0
        prefilled_dates = 0
        for page_schema in field_schema.get("pages", []):
            for field in page_schema.get("fields", []):
                baseline = field.get(BASELINE_KEY) or {}
                if not baseline.get("prefilled"):
                    continue
                if field.get("field_type") == "signature":
                    prefilled_signatures += 1
                elif field.get("field_type") == "date":
                    prefilled_dates += 1
        return {"prefilled_signatures": prefilled_signatures, "prefilled_dates": prefilled_dates}

    @staticmethod
    def prefilled_summary(field_schema: dict | None) -> dict | None:
        """Read-time warning data for a template: how many of its signature
        and date slots the template file itself already fills. ``warning`` is
        set when MORE than half of either kind is filled -- a signed package
        uploaded where a blank form was expected. None while the schema has
        no baseline (analysed before baselines existed)."""
        if not field_schema or PdfParser.needs_template_baseline(field_schema):
            return None
        counts = {"signature": [0, 0], "date": [0, 0]}
        for page_schema in field_schema.get("pages", []):
            for field in page_schema.get("fields", []):
                kind = field.get("field_type")
                if kind not in counts:
                    continue
                counts[kind][1] += 1
                if (field.get(BASELINE_KEY) or {}).get("prefilled"):
                    counts[kind][0] += 1
        warning = any(
            total > 0 and filled / total > PREFILLED_WARNING_RATIO
            for filled, total in counts.values()
        )
        return {
            "signatures": {"filled": counts["signature"][0], "total": counts["signature"][1]},
            "dates": {"filled": counts["date"][0], "total": counts["date"][1]},
            "warning": warning,
        }

    # -- same document as the template? -------------------------------------

    def template_match(
        self,
        template_pdf_path: str,
        contract_pdf_path: str,
        field_schema: dict,
        page_map: dict[int, int | None] | None = None,
    ) -> dict:
        """Whether the uploaded contract is, in effect, its template file again.

        ``page_similarity`` is the LEAST similar page (token-multiset Dice of
        the page text; a page only one file has scores 0). ``image_match_ratio``
        is the share of signature slots -- among those where either file has
        an image -- whose image hashes are identical (1.0 when neither file has
        any). Never blocks anything: it only warns that the wrong file may have
        been uploaded.

        `page_map` (PDF editor v2): template page N -> position in the contract
        document; the contract's pages are compared in TEMPLATE order. Absent =
        identity, the behaviour before v2.
        """
        template = _document_fingerprint(template_pdf_path, field_schema)
        contract = _document_fingerprint(contract_pdf_path, field_schema, page_map)
        page_total = max(len(template["pages"]), len(contract["pages"]))
        similarities = [
            _dice(template["pages"][index], contract["pages"][index])
            if index < len(template["pages"]) and index < len(contract["pages"])
            else 0.0
            for index in range(page_total)
        ]
        page_similarity = min(similarities) if similarities else 1.0

        considered = matched = 0
        for field_id in set(template["signatures"]) | set(contract["signatures"]):
            ours = template["signatures"].get(field_id) or set()
            theirs = contract["signatures"].get(field_id) or set()
            if not ours and not theirs:
                continue
            considered += 1
            if ours == theirs:
                matched += 1
        image_match_ratio = matched / considered if considered else 1.0

        identical = (
            len(template["pages"]) == len(contract["pages"])
            and page_similarity >= TEMPLATE_MATCH_PAGE_SIMILARITY
            and image_match_ratio >= TEMPLATE_MATCH_IMAGE_RATIO
        )
        return {
            "identical": bool(identical),
            "page_similarity": round(page_similarity, 4),
            "image_match_ratio": round(image_match_ratio, 4),
        }

    # -- per field -----------------------------------------------------------

    def _extract_field(self, context: "_PageContext", field: dict, page_number: int) -> ExtractedField:
        field_id = field["field_id"]
        field_type = field.get("field_type", "text_line")
        bbox = BBox.from_dict(field["bbox"])
        result = ExtractedField(field_id=field_id, field_type=field_type, page=page_number)

        if field_type == "signature":
            result.has_signature, result.confidence = context.has_ink(bbox)
            result.detected = result.has_signature
            return result

        if field_type == "date":
            result.value, result.confidence = context.date_in(bbox)
            result.detected = result.value is not None
            return result

        if field_type == "checkbox":
            result.checked, result.confidence = context.is_checked(bbox)
            result.detected = result.checked
            return result

        if field_type == "checkbox_group":
            for option in field.get("options", []):
                checked, _ = context.is_checked(BBox.from_dict(option["bbox"]))
                if checked:
                    result.selected.append(option["id"])
            result.detected = bool(result.selected)
            result.confidence = 0.9 if result.selected else 0.0
            result.value = ", ".join(result.selected) or None
            return result

        if field_type == "table":
            result.rows = context.table_rows(bbox)
            result.detected = any(any(cell for cell in row) for row in result.rows)
            result.confidence = 0.8 if result.detected else 0.0
            return result

        text, confidence = context.text_in(bbox)
        result.value = normalize_text(text)
        result.confidence = confidence
        result.detected = not is_blank(result.value)
        if not result.detected:
            result.value = None
        return result


class _PageContext:
    """Cached per-page views so each field crop does not re-walk the page."""

    def __init__(self, page):
        self.page = page
        self.width = float(page.width)
        self.height = float(page.height)

    def _crop(self, bbox: BBox):
        x0 = max(bbox.x - CROP_PADDING, 0.0)
        top = max(bbox.y - CROP_PADDING, 0.0)
        x1 = min(bbox.x1 + CROP_PADDING, self.width)
        bottom = min(bbox.y1 + CROP_PADDING, self.height)
        if x1 <= x0 or bottom <= top:
            return None
        return self.page.crop((x0, top, x1, bottom), strict=False)

    def text_in(self, bbox: BBox) -> tuple[str | None, float]:
        crop = self._crop(bbox)
        if crop is None:
            return None, 0.0
        text = crop.extract_text() or ""
        # The ruled fill line extracts as underscores interleaved with whatever
        # was written on top of it ('_C__H_A_R_L_E__S_ F_R__Y_E___'), so they are
        # removed outright rather than turned into spaces -- real word breaks
        # survive as actual space characters.
        text = text.replace("_", "")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None, 0.0
        # Digital text is exact; confidence only drops below 1.0 for OCR output.
        return text, 1.0

    def has_ink(self, bbox: BBox) -> tuple[bool, float]:
        """A signature region counts as signed when it visibly holds a
        signature image, a meaningful number of vector strokes, or a typed
        name on the rule -- whether or not the template file shows the same."""
        for image in self.page.images:
            image_box = BBox.from_pdfplumber(image["x0"], image["top"], image["x1"], image["bottom"])
            if not image_box.overlaps(bbox):
                continue
            # Guard B: an image hanging down from the row above is not this
            # slot's signature -- it must sit on the rule or lie mostly inside.
            overlap = _intersection_area(image_box, bbox)
            if overlap / max(bbox.width * bbox.height, 1.0) < SIGNATURE_MIN_IMAGE_AREA_RATIO:
                continue
            centre_y = image_box.y + image_box.height / 2
            on_rule = centre_y >= bbox.y + bbox.height / 2
            mostly_inside = (
                overlap / max(image_box.width * image_box.height, 1.0)
                >= SIGNATURE_MIN_IMAGE_INSIDE_FRACTION
            )
            if on_rule or mostly_inside:
                return True, 0.95

        if self.stroke_count(bbox) >= SIGNATURE_MIN_STROKES:
            return True, 0.8

        # Guard A: some tools stamp a typed name instead of an image. Only the
        # rule band qualifies (prose on the line above never enters it), and a
        # "Label:" is never a name.
        text = _without_labels(self.text_on_rule(bbox))
        if text and len(text) >= 3 and not text.endswith(":"):
            return True, 0.6
        return False, 0.0

    def date_in(self, bbox: BBox) -> tuple[str | None, float]:
        """The date a date slot visibly holds, or (None, 0.0).

        The slot counts as filled only when its text contains something shaped
        like a date (:func:`_find_date`): leaked prose ("sideration."), labels
        ("Date:") and bare placeholders ("//") never qualify. When the box only
        catches the start of a date written on past its right edge ("Date: ___
        01/30/2026" with a 30pt box), the date is read from the rest of that
        row -- but only a date that STARTS inside the box.
        """
        text, confidence = self.text_in(bbox)
        found = _find_date(_without_labels(text))
        if found is None:
            found = self.date_starting_in(bbox)
        if found is None:
            return None, 0.0
        return found, confidence or 1.0

    def date_starting_in(self, bbox: BBox) -> str | None:
        """A date on the box's own row(s) whose first character lies inside
        the box horizontally; the row may run on past the box's right edge."""
        # Lower half only: the reach is for the box's own rule row, never the
        # line above it (12pt leading puts that row's centre in the top half).
        top, bottom = bbox.y + bbox.height / 2, bbox.y1 + CROP_PADDING
        left, right = bbox.x - CROP_PADDING, bbox.x1 + CROP_PADDING
        rows: dict[int, list[dict]] = {}
        for char in self.page.chars:
            # A typed date straddles the box's bottom edge (it sits on the
            # rule), so a glyph belongs to the box by its vertical centre.
            middle = (float(char["top"]) + float(char["bottom"])) / 2
            if top <= middle <= bottom and float(char["x1"]) > left:
                rows.setdefault(round(float(char["top"])), []).append(char)
        for key in sorted(rows):
            text, positions = _row_text(sorted(rows[key], key=lambda char: float(char["x0"])))
            for start, value in _all_date_matches(text):
                if left <= positions[start] <= right:
                    return value
        return None

    def stroke_count(self, bbox: BBox) -> int:
        """Vector strokes inside the box, excluding the ruled signature line
        itself (wide and flat)."""
        strokes = 0
        for shape in list(self.page.curves) + list(self.page.lines):
            shape_box = BBox.from_pdfplumber(shape["x0"], shape["top"], shape["x1"], shape["bottom"])
            if not shape_box.overlaps(bbox):
                continue
            if shape_box.height < 2.0 and shape_box.width > bbox.width * 0.5:
                continue
            strokes += 1
        return strokes

    def text_on_rule(self, bbox: BBox) -> str | None:
        """Text sitting on the signature rule: glyph rows that start within
        SIGNATURE_TEXT_BAND above the box's bottom edge (the rule) or on the
        rule row itself. Rows further up belong to the paragraph above."""
        low = bbox.y1 - SIGNATURE_TEXT_BAND
        high = bbox.y1 + SIGNATURE_TEXT_BELOW
        x0, x1 = bbox.x - CROP_PADDING, bbox.x1 + CROP_PADDING
        chars = [
            char
            for char in self.page.chars
            if low <= float(char["top"]) <= high
            and float(char["x1"]) > x0
            and float(char["x0"]) < x1
        ]
        if not chars:
            return None
        chars.sort(key=lambda char: (round(float(char["top"])), float(char["x0"])))
        parts: list[str] = []
        previous = None
        for char in chars:
            if previous is not None:
                new_row = round(float(char["top"])) != round(float(previous["top"]))
                gap = float(char["x0"]) - float(previous["x1"])
                if new_row or gap > 1.5:
                    parts.append(" ")
            parts.append(char.get("text") or "")
            previous = char
        text = "".join(parts).replace("_", "")
        text = re.sub(r"\s+", " ", text).strip()
        return text or None

    def baseline(self, bbox: BBox, field_type: str) -> dict:
        """What the blank template already shows inside this box."""
        hashes: list[str] = []
        for image in self.page.images:
            image_box = BBox.from_pdfplumber(image["x0"], image["top"], image["x1"], image["bottom"])
            if image_box.overlaps(bbox):
                digest = _image_hash(image)
                if digest not in hashes:
                    hashes.append(digest)
        whole, _ = self.text_in(bbox)
        tokens = set(_tokens(whole))
        if field_type == "signature":
            tokens |= set(_tokens(self.text_on_rule(bbox)))
            prefilled, _ = self.has_ink(bbox)
        else:
            # The same reading a contract gets: "prefilled" means a contract
            # showing exactly this would count the slot as filled.
            prefilled = self.date_in(bbox)[0] is not None
        return {
            "image_hashes": hashes,
            "strokes": self.stroke_count(bbox),
            "text_tokens": sorted(tokens),
            "prefilled": bool(prefilled),
        }

    def is_checked(self, bbox: BBox) -> tuple[bool, float]:
        """A checkbox is marked when something was drawn or typed inside it."""
        for char in self.page.chars:
            char_box = BBox.from_pdfplumber(char["x0"], char["top"], char["x1"], char["bottom"])
            if not char_box.overlaps(bbox):
                continue
            text = (char.get("text") or "").strip()
            if text in {"x", "X", "✓", "✔", "☑", "☒", "■", "●"}:
                return True, 0.95
        for image in self.page.images:
            # A tick placed as a small image over the box glyph. Its centre
            # decides which option it belongs to, so a neighbour's tick never
            # marks this one; the size cap keeps a signature stamp out.
            image_box = BBox.from_pdfplumber(image["x0"], image["top"], image["x1"], image["bottom"])
            centre_x = image_box.x + image_box.width / 2
            centre_y = image_box.y + image_box.height / 2
            if not (bbox.x <= centre_x <= bbox.x1 and bbox.y <= centre_y <= bbox.y1):
                continue
            if (
                image_box.width <= bbox.width * CHECKBOX_IMAGE_MAX_SCALE
                and image_box.height <= bbox.height * CHECKBOX_IMAGE_MAX_SCALE
            ):
                return True, 0.9
        for shape in list(self.page.curves) + list(self.page.lines):
            shape_box = BBox.from_pdfplumber(shape["x0"], shape["top"], shape["x1"], shape["bottom"])
            if shape_box.overlaps(bbox) and shape_box.width < bbox.width * 1.5:
                # Ignore the box outline itself: it matches the region exactly.
                if abs(shape_box.width - bbox.width) > 2.0 or abs(shape_box.height - bbox.height) > 2.0:
                    return True, 0.7
        return False, 0.0

    def table_rows(self, bbox: BBox) -> list[list[str]]:
        crop = self._crop(bbox)
        if crop is None:
            return []
        try:
            tables = crop.extract_tables()
        except Exception:
            return []
        rows: list[list[str]] = []
        for table in tables:
            for row in table:
                rows.append([(cell or "").strip() for cell in row])
        return rows


def _intersection_area(a: BBox, b: BBox) -> float:
    width = max(0.0, min(a.x1, b.x1) - max(a.x, b.x))
    height = max(0.0, min(a.y1, b.y1) - max(a.y, b.y))
    return width * height


def _image_hash(image: dict) -> str:
    """Identity of an embedded image's bytes, so the template's own artwork
    can be recognised wherever it reappears. Falls back to name+size when the
    stream cannot be read (identity only ever *removes* a match)."""
    stream = image.get("stream")
    try:
        raw = stream.get_rawdata() if stream is not None else None
    except Exception:  # noqa: BLE001 - unreadable stream, fall through
        raw = None
    if raw:
        return hashlib.sha256(raw).hexdigest()[:16]
    return f"{image.get('name')}:{image.get('srcsize')}"


def _tokens(text: str | None) -> list[str]:
    return [token.lower() for token in (text or "").split() if token]


def _without_labels(text: str | None) -> str | None:
    """Drop label fragments ('Date:', 'Agreement:') that bleed in from
    neighbouring labels; no real value ends with a colon."""
    if not text:
        return text
    kept = [token for token in text.split() if not token.endswith(":")]
    return " ".join(kept) or None


# -- date shape ------------------------------------------------------------------
#
# A date slot is filled only by something shaped like a date. Accepted shapes
# (anything else -- prose, labels, a lone number, "//" -- reads as empty):
#   numeric   01/30/2026, 1-5-26, 01.30.2026, "01 / 30 / 2026", 2026-01-30
#   named     Aug 22, 2026 / August 22nd 2026 / 22 Aug 2026
#   spaced    01 30 2026        (separate boxes; must be a real calendar date)
#   month/yr  08/2029           (expiry-style; month 1-12)
#   compact   01302026 / 013026 (only when some reading is a real calendar
#             date; returned as written)
# Deliberately NOT accepted: "Aug 2026" / "August" (month name without a day:
# indistinguishable from prose), a bare year or day number.

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_ORDINAL = r"(?:st|nd|rd|th)?"
_SEP = r"\s*[/.\-]\s*"
# Ordered most specific first; a later shape never re-reads a span already taken.
_DATE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("numeric", re.compile(rf"(?<!\d)\d{{1,2}}{_SEP}\d{{1,2}}{_SEP}(?:\d{{4}}|\d{{2}})(?!\d)")),
    ("numeric", re.compile(rf"(?<!\d)\d{{4}}{_SEP}\d{{1,2}}{_SEP}\d{{1,2}}(?!\d)")),
    ("named", re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}{_ORDINAL}\s*,?\s*\d{{4}}(?!\d)", re.I)),
    ("named", re.compile(rf"(?<!\d)\d{{1,2}}{_ORDINAL}\s+{_MONTH}\.?\s*,?\s*\d{{4}}(?!\d)", re.I)),
    ("spaced", re.compile(r"(?<!\d)(\d{1,2})\s+(\d{1,2})\s+(\d{4})(?!\d)")),
    ("month_year", re.compile(r"(?<!\d)(\d{1,2})\s*[/\-]\s*(\d{4})(?!\d)")),
    ("compact", re.compile(r"(?<![\w])(\d{8}|\d{6})(?![\w])")),
]


def _find_date(text: str | None) -> str | None:
    """The first date-shaped value in ``text``, normalised, or None."""
    matches = _all_date_matches(text)
    return matches[0][1] if matches else None


def _all_date_matches(text: str | None) -> list[tuple[int, str]]:
    """Every date-shaped span in ``text`` as (offset in ``text``, value), in
    order. A date typed over a "__/__/____" placeholder interleaves with its
    slashes ("01//30//2026"), so runs of one separator collapse first. Only
    when that finds nothing is letter-spaced typing ("0 1 / 3 0 / 2 0 2 6")
    squeezed, so two separate numbers are never glued into one date."""
    if not text:
        return []
    offsets = list(range(len(text)))
    collapsed, offsets = _drop_chars(
        text, offsets, lambda s, i: s[i] in "/.-" and i > 0 and s[i - 1] == s[i]
    )
    found = _match_dates(collapsed, offsets)
    if not found:
        squeezed, squeezed_offsets = _drop_chars(
            collapsed,
            offsets,
            lambda s, i: s[i] == " " and 0 < i < len(s) - 1 and s[i - 1].isdigit() and s[i + 1].isdigit(),
        )
        if squeezed != collapsed:
            found = _match_dates(squeezed, squeezed_offsets)
    return found


def _drop_chars(text: str, offsets: list[int], drop) -> tuple[str, list[int]]:
    kept_text: list[str] = []
    kept_offsets: list[int] = []
    for index, char in enumerate(text):
        if drop(text, index):
            continue
        kept_text.append(char)
        kept_offsets.append(offsets[index])
    return "".join(kept_text), kept_offsets


def _match_dates(text: str, offsets: list[int]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue
            value = _normalise_date(kind, match)
            if value is None:
                continue
            taken.append((start, end))
            found.append((offsets[start], value))
    found.sort(key=lambda item: item[0])
    return found


def _normalise_date(kind: str, match: re.Match) -> str | None:
    raw = match.group(0)
    if kind == "numeric":
        # The contractor's own separators are kept; only spacing is dropped.
        return re.sub(r"\s+", "", raw)
    if kind == "named":
        return re.sub(r"\s+", " ", raw).strip()
    if kind == "month_year":
        month = int(match.group(1))
        return f"{month:02d}/{match.group(2)}" if 1 <= month <= 12 else None
    if kind == "spaced":
        month, day, year = (int(part) for part in match.groups())
        try:
            dt.date(year, month, day)
        except ValueError:
            return None
        return f"{month:02d}/{day:02d}/{year}"
    if kind == "compact":
        # Counts as a date when ANY common reading is a real calendar date,
        # but is returned exactly as written: "121990" could be 12/19/90 or
        # 1/21/1990, and guessing would hide that from the date validator.
        digits = match.group(1)
        for fmt in ("%m%d%Y", "%Y%m%d", "%m%d%y"):
            try:
                dt.datetime.strptime(digits, fmt)
            except ValueError:
                continue
            return digits
        return None
    return None


def _row_text(row: list[dict]) -> tuple[str, list[float]]:
    """Join one row of chars as :meth:`_PageContext.text_on_rule` does, keeping
    each output character's x0 so a match can be placed on the page.
    Underscores take part in gap detection and are then dropped: a date typed
    over a fill line interleaves with it ("0_1/_3_0/...")."""
    parts: list[str] = []
    positions: list[float] = []
    previous = None
    for char in row:
        if previous is not None and float(char["x0"]) - float(previous["x1"]) > 1.5:
            parts.append(" ")
            positions.append(float(char["x0"]))
        previous = char
        glyphs = (char.get("text") or "").replace("_", "")
        for glyph in glyphs:
            parts.append(glyph)
            positions.append(float(char["x0"]))
    return "".join(parts), positions


# -- AcroForm widget values ------------------------------------------------------
#
# Precedence rule: the PAGE reading wins. A widget value is used only for a
# slot the page text left empty (not detected). Reasons: (1) every existing
# result -- a document without widgets, or a slot already filled in the page
# text -- stays exactly what it was; (2) what is printed in the page content is
# part of the document itself, while a widget is a layer over it that a
# viewer may or may not show; (3) a filled template slot's region is blank on
# the form, so a conflict means the page itself was written there, which is
# the stronger evidence. Signature slots never take a widget value (a /Sig
# field carries no signature image; signatures come from the signing flow),
# nor do tables.

# A text widget belongs to a template field when the overlap covers at least
# this share of the widget OR of the field box (a widget drawn over the fill
# line is usually shorter than the label-to-line box, or vice versa); a
# neighbour that merely touches the box never reaches it.
WIDGET_MIN_OVERLAP = 0.25
_WIDGET_HIDDEN_FLAGS = (1 << 1) | (1 << 5)  # /F Hidden | NoView
WIDGET_SOURCE = "form_field"


@dataclass(frozen=True)
class _FormWidget:
    box: BBox  # canonical: the page AS DISPLAYED, top-left origin (pdfplumber's)
    kind: str  # text | choice | button
    value: str | None  # text/choice value
    on: bool  # button widget shown in an "on" state


def _inherited(node, key: str):
    """An inheritable field key (/FT /V /Ff): the widget's own, else the
    nearest /Parent's (PDF 32000-1 §12.7.3.1)."""
    seen = 0
    while node is not None and seen < 32:
        if key in node:
            return node[key]
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
        seen += 1
    return None


def _widget_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value if str(item).strip()]
        return ", ".join(parts) or None
    text = str(value)
    return text if text.strip() else None


def _form_widgets(pdf_path: str) -> dict[int, list[_FormWidget]]:
    """Visible text/choice/button widgets per 1-indexed page, with their boxes
    in the parser's canonical space. {} when the file has no AcroForm (then
    extraction is exactly the page reading) or cannot be read this way."""
    from pypdf import PdfReader

    from app.services.geometry import unrotated_to_displayed

    try:
        reader = PdfReader(pdf_path, strict=False)
        if "/AcroForm" not in reader.trailer["/Root"]:
            return {}
        out: dict[int, list[_FormWidget]] = {}
        for position, page in enumerate(reader.pages, start=1):
            annots = page.get("/Annots")
            if annots is None:
                continue
            rotation = int(page.get("/Rotate") or 0) % 360
            if rotation not in (0, 90, 180, 270):
                continue
            box = page.mediabox
            left, bottom = float(box.left), float(box.bottom)
            width, height = float(box.width), float(box.height)
            top = bottom + height
            for ref in annots.get_object() or []:
                try:
                    annot = ref.get_object()
                    if annot.get("/Subtype") != "/Widget":
                        continue
                    if int(annot.get("/F", 0) or 0) & _WIDGET_HIDDEN_FLAGS:
                        continue
                    ftype = _inherited(annot, "/FT")
                    if ftype not in ("/Tx", "/Ch", "/Btn"):
                        continue  # /Sig and anything else carry no slot value
                    x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
                    corners = [
                        unrotated_to_displayed(x - left, top - y, width, height, rotation)
                        for x, y in ((x0, y0), (x1, y1))
                    ]
                    (ax, ay), (bx, by) = corners
                    widget_box = BBox(min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay))
                    if widget_box.width <= 0.01 or widget_box.height <= 0.01:
                        continue
                    if ftype == "/Btn":
                        state = annot.get("/AS")
                        on = state is not None and str(state) not in ("/Off", "Off")
                        widget = _FormWidget(widget_box, "button", None, on)
                    else:
                        kind = "text" if ftype == "/Tx" else "choice"
                        value = _inherited(annot, "/V")
                        value = value.get_object() if hasattr(value, "get_object") else value
                        widget = _FormWidget(widget_box, kind, _widget_text(value), False)
                    out.setdefault(position, []).append(widget)
                except Exception:  # noqa: BLE001 - one malformed widget is skipped
                    logger.debug("Skipping an unreadable widget on page %s", position, exc_info=True)
        return out
    except Exception:  # noqa: BLE001 - the page reading still stands on its own
        logger.exception("Could not read the form widgets of %s", pdf_path)
        return {}


def _widget_overlap(widget: BBox, bbox: BBox) -> float:
    """The larger of the overlap's share of the widget and of the field box."""
    overlap = _intersection_area(widget, bbox)
    if overlap <= 0:
        return 0.0
    return max(
        overlap / max(widget.width * widget.height, 1e-6),
        overlap / max(bbox.width * bbox.height, 1e-6),
    )


def _centre_inside(widget: BBox, bbox: BBox) -> bool:
    cx, cy = widget.x + widget.width / 2, widget.y + widget.height / 2
    return bbox.x <= cx <= bbox.x1 and bbox.y <= cy <= bbox.y1


def _with_widget_value(result: ExtractedField, field: dict, widgets: list[_FormWidget]) -> ExtractedField:
    """Fill a slot the page text left empty from a widget over it (see the
    precedence rule above). Anything taken from a widget is marked
    `extra.source = "form_field"`."""
    kind = result.field_type
    if kind in ("signature", "table"):
        return result
    bbox = BBox.from_dict(field["bbox"])

    if kind == "checkbox":
        if result.checked:
            return result
        if any(w.kind == "button" and w.on and _centre_inside(w.box, bbox) for w in widgets):
            result.checked, result.detected, result.confidence = True, True, 0.9
            result.extra["source"] = WIDGET_SOURCE
        return result

    if kind == "checkbox_group":
        if result.selected:
            return result
        for option in field.get("options", []) or []:
            option_box = BBox.from_dict(option["bbox"])
            if any(w.kind == "button" and w.on and _centre_inside(w.box, option_box) for w in widgets):
                result.selected.append(option["id"])
        if result.selected:
            result.detected, result.confidence = True, 0.9
            result.value = ", ".join(result.selected)
            result.extra["source"] = WIDGET_SOURCE
        return result

    if result.detected:
        return result
    candidates = sorted(
        (
            (_widget_overlap(w.box, bbox), n, w)
            for n, w in enumerate(widgets)
            if w.kind in ("text", "choice") and w.value
        ),
        key=lambda item: (-item[0], item[1]),
    )
    for overlap, _, widget in candidates:
        if overlap < WIDGET_MIN_OVERLAP:
            break
        if kind == "date":
            value = _find_date(_without_labels(widget.value))
        else:
            value = normalize_text(widget.value)
            value = None if is_blank(value) else value
        if value is None:
            continue
        result.value, result.detected, result.confidence = value, True, 1.0
        result.extra["source"] = WIDGET_SOURCE
        return result
    return result


# -- template match --------------------------------------------------------------


def _dice(a: Counter, b: Counter) -> float:
    total = sum(a.values()) + sum(b.values())
    if total == 0:
        return 1.0
    return 2 * sum((a & b).values()) / total


def _mapped(page_number: int, page_map: dict[int, int | None] | None) -> int | None:
    """Template page -> position in the document read (identity without a map)."""
    if page_map is None:
        return page_number
    return page_map.get(page_number)


def _document_fingerprint(
    pdf_path: str, field_schema: dict, page_map: dict[int, int | None] | None = None
) -> dict:
    """Per-page text tokens and per-signature-slot image hashes.

    With `page_map` the pages come back in TEMPLATE order (a removed page is an
    empty page) and slots are looked up at their mapped position."""
    slots_by_page: dict[int, list[dict]] = {}
    for page_schema in field_schema.get("pages", []):
        position = _mapped(int(page_schema.get("page_number", 0)), page_map)
        if position is None:
            continue
        slots_by_page[position] = [
            field for field in page_schema.get("fields", []) if field.get("field_type") == "signature"
        ]
    pages: list[Counter] = []
    signatures: dict[str, set[str]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            pages.append(Counter((page.extract_text() or "").replace("_", "").split()))
            slots = slots_by_page.get(number) or []
            if not slots:
                continue
            images = [
                (BBox.from_pdfplumber(image["x0"], image["top"], image["x1"], image["bottom"]), image)
                for image in page.images
            ]
            for field in slots:
                bbox = BBox.from_dict(field["bbox"])
                signatures[field["field_id"]] = {
                    _image_hash(image) for box, image in images if box.overlaps(bbox)
                }
    if page_map is not None:
        ordered = [
            pages[position - 1] if position is not None and 1 <= position <= len(pages) else Counter()
            for _origin, position in sorted(page_map.items())
        ]
        placed = {position for position in page_map.values() if position is not None}
        ordered.extend(page for index, page in enumerate(pages, start=1) if index not in placed)
        pages = ordered
    return {"pages": pages, "signatures": signatures}
