"""Detect fillable regions in a blank template PDF.

Unlike a text-only scan, every field produced here carries a real bounding box
derived from word positions returned by pdfplumber -- highlighting in the review
UI and signature placement in the final PDF both depend on those coordinates
being correct.

Label patterns are supplied per template rather than hardcoded, so a second
template can be onboarded without editing this module. The defaults match the
Cargo Freight Trucking driver contract.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field as dc_field
from typing import Any, Iterable

import pdfplumber

from app.services.geometry import BBox

CHECKBOX_GLYPHS = "☐□❑❏⬜◻▫"
CHECKBOX_RE = re.compile(f"[{CHECKBOX_GLYPHS}]")

# Minimum width given to a fill region when no underscore run follows the label.
MIN_FILL_WIDTH = 90.0
DEFAULT_FILL_HEIGHT = 14.0
SIGNATURE_HEIGHT = 34.0

# Largest horizontal gap between two '_' glyphs that still counts as ONE ruled
# fill line. Adjacent underscores abut (gap 0); two separate runs on a row are
# always parted by at least a space (>= ~3pt at 12pt), so this never bridges
# "City: ____ State: __". Used by _extend_over_glyph_run.
UNDERSCORE_GLYPH_GAP = 1.5
# A '_' glyph belongs to a run's row when at least this fraction of its height
# overlaps the run box vertically (rows are >= 12pt apart; overlap there is 0).
UNDERSCORE_ROW_OVERLAP = 0.5


@dataclass
class FieldRegion:
    field_id: str
    label: str
    field_type: str  # text_line | checkbox | checkbox_group | signature | date | table
    page: int
    bbox: dict
    required: bool = True
    options: list[dict] | None = None
    validation_rule: str | None = None
    # Links a signature field to the date field that accompanies it, so the
    # frontend never has to guess the pairing by string substitution.
    paired_date_field_id: str | None = None
    detector: str = "text"

    def to_dict(self) -> dict:
        data = asdict(self)
        if data["options"] is None:
            data.pop("options")
        return data


@dataclass
class LabelPattern:
    pattern: str
    name: str
    field_type: str = "text_line"
    required: bool = True

    @property
    def regex(self) -> re.Pattern:
        return re.compile(self.pattern, re.IGNORECASE)


DEFAULT_LABEL_PATTERNS: list[LabelPattern] = [
    LabelPattern(r"Full\s+(?:Legal\s+)?Name\s*:", "full_name"),
    LabelPattern(r"\bCDL\s*(?:Number|#)?\s*:", "cdl"),
    LabelPattern(r"\bState\s*:", "state"),
    LabelPattern(r"\bExp(?:iration)?(?:\s+Date)?\s*:", "cdl_expiration", "date"),
    LabelPattern(r"Date\s+of\s+Birth\s*:", "date_of_birth", "date"),
    LabelPattern(r"\bPhone\s*:", "phone"),
    LabelPattern(r"\bEmail\s*:", "email"),
    LabelPattern(r"\bSSN\s*:", "ssn"),
    LabelPattern(r"EIN\s*(?:\(if applicable\))?\s*:", "ein", required=False),
    LabelPattern(r"Effective\s+Date\s+of\s+Agreement\s*:", "effective_date", "date"),
    LabelPattern(r"Unit\s+Assigned\s*:", "unit_assigned", required=False),
    LabelPattern(r"Address\s*:", "address", required=False),
    # Accept a signature label ended by a colon OR an underscore fill ("Signature ___",
    # "X_____"). Lookahead only -- the underscores are NOT consumed, so _fill_region
    # still anchors the writable box on them. Requiring a ':' or '_' right after the
    # word keeps "signature" in prose from ever matching. More labels detected => more
    # required signature fields => a STRICTER can_sign gate (never a weaker one).
    # B7 DROPPED: an apostrophe possessive ("Contractor's Signature:") is ALREADY
    # detected as a generic `signature` field below (the "'s " puts a space before
    # "Signature", so the generic pattern matches and the field stays REQUIRED).
    # Adding the apostrophe here only made contractor_signature win the overlap and
    # renamed p{N}_signature -> p{N}_contractor_signature -- a lost id, zero new
    # coverage. Reverted to the plain form; \s+ cannot cross the apostrophe.
    LabelPattern(r"Contractor\s+Signature\s*(?=[:_])", "contractor_signature", "signature"),
    LabelPattern(r"Company\s+Representative\s+Signature\s*(?=[:_])", "company_rep_signature", "signature"),
    # B2: tolerate up to ~40 connective chars between "Signature" and its ':'/'_'
    # anchor ("Signature of Driver:"). The class excludes ':' '_' and newlines so
    # the (lazy) gap can never swallow a following label's colon; longest-match-wins
    # still lets the contractor/company patterns above win the overlap.
    # NOTE: no \b after "Signature". An underscore fill glued to the word
    # ("Signature____") has NO word boundary there ('_' is itself a \w char), so a
    # \b would refuse to match and the date pattern would steal the line (p45 flip).
    # The leading (?<!\w) already prevents matching inside a longer word.
    LabelPattern(r"(?<!\w)Signature[^:_\n]{0,40}?(?=[:_])", "signature", "signature"),
    # B3/B4: extra signing-action labels. Each REQUIRES a ':'/'_' anchor so the word
    # only ever matches a real fill, never prose. Distinct names keep their own id
    # counters, so they can never renumber the existing signature/date ids.
    LabelPattern(r"(?<!\w)Initials?\s*(?=[:_])", "initials", "signature"),
    LabelPattern(r"(?<!\w)Signed\b[^:_\n]{0,20}?(?=[:_])", "signed", "signature"),
    LabelPattern(r"(?<!\w)Sign\s+here\s*(?=[:_])", "sign_here", "signature"),
    LabelPattern(r"/s/\s*(?=[:_])", "s_slash", "signature"),
    LabelPattern(r"(?<!\w)Recorded\s+by\s*(?=[:_])", "recorded_by", "signature"),
    # "By:" DROPPED: a bare "By" + ':' anchor matches prose, not only signature
    # blocks -- e.g. p41 "...motor vehicle record (MVR) for review by:" is a mid-
    # sentence fragment, which would become a spurious REQUIRED field. The longer
    # "Recorded by" label above is kept (its extra word makes prose collisions
    # unlikely). Per the task's explicit escape hatch: drop just "By".
    # LabelPattern(r"(?<!\w)By\s*(?=[:_])", "by", "signature"),
    # B6: a date anchored by ':' OR an underscore fill ("Date ___"), not only a
    # literal colon. Strict superset of the old rule, so no existing date is lost.
    LabelPattern(r"(?<!\w)Date\s*(?=[:_])", "date", "date"),
]


@dataclass
class _Word:
    text: str
    bbox: BBox


@dataclass
class _Line:
    words: list[_Word]
    text: str = ""
    # Character offset -> index into `words`, so a regex match maps back to boxes.
    offsets: list[tuple[int, int, int]] = dc_field(default_factory=list)

    @property
    def bbox(self) -> BBox:
        box = self.words[0].bbox
        for word in self.words[1:]:
            box = box.union(word.bbox)
        return box

    def words_in_span(self, start: int, end: int) -> list[_Word]:
        return [self.words[i] for (s, e, i) in self.offsets if s < end and e > start]

    def span_bbox(self, start: int, end: int) -> BBox | None:
        words = self.words_in_span(start, end)
        if not words:
            return None
        box = words[0].bbox
        for word in words[1:]:
            box = box.union(word.bbox)
        return box


class FieldDetector:
    def __init__(self, label_patterns: list[LabelPattern] | None = None):
        self.label_patterns = label_patterns or DEFAULT_LABEL_PATTERNS

    # -- public API ----------------------------------------------------------

    def analyze_template(self, pdf_path: str) -> list[FieldRegion]:
        fields: list[FieldRegion] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                fields.extend(self._analyze_page(page, page_number))
        fields = self._pair_signatures_with_dates(fields)
        return self._sort(fields)

    def analyze_pages(self, pdf_bytes: bytes, page_numbers: Iterable[int]) -> list[FieldRegion]:
        """The same per-page detection for chosen pages (1-based) of a PDF in
        memory (editor v2 "Detect form fields", B3). Boxes are pdfplumber's:
        top-left of the DISPLAYED MediaBox; signature/date pairing is not
        needed there and is skipped."""
        import io

        wanted = sorted(set(int(n) for n in page_numbers))
        fields: list[FieldRegion] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_number in wanted:
                if 1 <= page_number <= len(pdf.pages):
                    fields.extend(self._analyze_page(pdf.pages[page_number - 1], page_number))
        return self._sort(fields)

    def build_schema(self, pdf_path: str) -> dict:
        """Detector output in the JSONB shape stored on templates.field_schema."""
        fields = self.analyze_template(pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            pages_meta = [
                {"page_number": i, "width": float(p.width), "height": float(p.height),
                 "rotation": int(p.rotation or 0)}
                for i, p in enumerate(pdf.pages, start=1)
            ]
        by_page: dict[int, list[dict]] = {}
        for region in fields:
            by_page.setdefault(region.page, []).append(region.to_dict())
        return {
            "pages": [
                {**meta, "fields": by_page.get(meta["page_number"], [])} for meta in pages_meta
            ],
            "field_count": len(fields),
        }

    # -- per page ------------------------------------------------------------

    def _analyze_page(self, page, page_number: int) -> list[FieldRegion]:
        lines = self._build_lines(page)
        page_width, page_height = float(page.width), float(page.height)
        # B5/B1: fill guides (vector rules + underscore runs) used to anchor a
        # writable box when a label's own trailing text carries no '_' run.
        anchors = self._fill_anchors(page, lines)
        # Every '_' glyph on the page, so a fill line that a pre-filled
        # template's own value has split across word/line buckets can still be
        # measured end to end (see _extend_over_glyph_run).
        glyphs = self._underscore_glyphs(page)

        fields: list[FieldRegion] = []
        counters: dict[str, int] = {}

        for line in lines:
            fields.extend(
                self._detect_line_fields(line, page_number, page_width, counters, anchors, glyphs)
            )
            fields.extend(self._detect_checkboxes(line, page_number, counters))

        fields.extend(self._detect_rect_checkboxes(page, page_number, lines, counters))
        fields.extend(self._detect_tables(page, page_number, page_height))
        return fields

    def _build_lines(self, page) -> list[_Line]:
        words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
        buckets: dict[int, list[dict]] = {}
        for word in words:
            # Cluster words onto a shared baseline; 3pt buckets tolerate jitter.
            key = int(round(float(word["top"]) / 3.0))
            buckets.setdefault(key, []).append(word)

        lines: list[_Line] = []
        for key in sorted(buckets):
            raw = sorted(buckets[key], key=lambda w: float(w["x0"]))
            line = _Line(words=[
                _Word(
                    text=w["text"],
                    bbox=BBox.from_pdfplumber(w["x0"], w["top"], w["x1"], w["bottom"]),
                )
                for w in raw
            ])
            cursor = 0
            parts: list[str] = []
            for index, word in enumerate(line.words):
                start = cursor
                parts.append(word.text)
                cursor += len(word.text)
                line.offsets.append((start, cursor, index))
                cursor += 1  # the joining space
            line.text = " ".join(parts)
            lines.append(line)
        return lines

    # -- text / signature / date fields --------------------------------------

    def _detect_line_fields(
        self, line: _Line, page_number: int, page_width: float, counters: dict[str, int],
        anchors: list[BBox] | None = None,
        glyphs: list[BBox] | None = None,
    ) -> list[FieldRegion]:
        matches: list[tuple[int, int, LabelPattern]] = []
        for pattern in self.label_patterns:
            for match in pattern.regex.finditer(line.text):
                matches.append((match.start(), match.end(), pattern))
        if not matches:
            return []

        # Longer, more specific labels win over the generic fallbacks that
        # overlap them ("Contractor Signature:" beats "Signature:").
        matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))
        chosen: list[tuple[int, int, LabelPattern]] = []
        for start, end, pattern in matches:
            if any(start < c_end and end > c_start for c_start, c_end, _ in chosen):
                continue
            chosen.append((start, end, pattern))
        chosen.sort(key=lambda m: m[0])

        fields: list[FieldRegion] = []
        for index, (start, end, pattern) in enumerate(chosen):
            label_box = line.span_bbox(start, end)
            if label_box is None:
                continue
            next_start = chosen[index + 1][0] if index + 1 < len(chosen) else None
            fill_box = self._fill_region(
                line, end, next_start, label_box, page_width, pattern, anchors, glyphs
            )
            fields.append(
                FieldRegion(
                    field_id=self._unique_id(page_number, pattern.name, counters),
                    label=line.text[start:end].strip(" :"),
                    field_type=pattern.field_type,
                    page=page_number,
                    bbox=fill_box.to_dict(),
                    required=pattern.required,
                )
            )
        return fields

    def _fill_region(
        self,
        line: _Line,
        label_end: int,
        next_label_start: int | None,
        label_box: BBox,
        page_width: float,
        pattern: LabelPattern,
        anchors: list[BBox] | None = None,
        glyphs: list[BBox] | None = None,
    ) -> BBox:
        """The writable area following a label, bounded by the next label on the line."""
        limit = next_label_start if next_label_start is not None else len(line.text)
        trailing = line.text[label_end:limit]

        height = SIGNATURE_HEIGHT if pattern.field_type == "signature" else max(
            label_box.height, DEFAULT_FILL_HEIGHT
        )

        right_edge = page_width - 36.0
        if next_label_start is not None:
            neighbour = line.span_bbox(next_label_start, next_label_start + 1)
            if neighbour is not None:
                right_edge = neighbour.x - 4.0

        underscores = re.search(r"_{2,}", trailing)
        if underscores:
            span_start = label_end + underscores.start()
            span_end = label_end + underscores.end()
            run = line.span_bbox(span_start, span_end)
            if run is not None:
                if pattern.field_type == "text_line" and glyphs:
                    # A value printed over the rule in a pre-filled template
                    # ("_U_t_ah_ ___") splits the '_' text into several words,
                    # some on another baseline bucket, so the regex above saw
                    # only a tail of the line. Widen to the full glyph run.
                    # Signature/date boxes keep their own (baseline-aware) rule.
                    run = self._extend_over_glyph_run(run, label_box.x1, right_edge + 4.0, glyphs)
                # A signature sits on the rule, not under it.
                top = run.y - height if pattern.field_type == "signature" else run.y - height / 2
                return BBox(x=run.x, y=max(top, 0.0), width=run.width, height=height)

        # B5/B1: no '_' run in this label's own trailing text -> try to anchor the
        # writable box on a real fill guide (a drawn rule or an underscore run) at
        # or just below the label baseline before using the blunt page-margin
        # fallback. Returns None on any imperfect fit, so this only ever relocates
        # a box that already exists -- it can never add or drop a field.
        if anchors:
            anchored = self._anchor_from_guides(label_box, right_edge, anchors, height, pattern)
            if anchored is not None:
                return anchored

        width = max(right_edge - label_box.x1 - 4.0, MIN_FILL_WIDTH)
        top = label_box.y - (height - label_box.height) if pattern.field_type == "signature" else label_box.y
        return BBox(x=label_box.x1 + 4.0, y=max(top, 0.0), width=width, height=height)

    def _fill_anchors(self, page, lines: list[_Line]) -> list[BBox]:
        """Horizontal fill guides on the page: thin+wide vector rules (drawn
        underlines from page.lines/page.rects) plus underscore-run glyph boxes.
        B5/B1 anchor a writable box on one of these when a label's own trailing
        text has no '_' run."""
        guides: list[BBox] = []
        for source in (getattr(page, "lines", None) or [], getattr(page, "rects", None) or []):
            for obj in source:
                try:
                    x0, x1 = float(obj["x0"]), float(obj["x1"])
                    top, bottom = float(obj["top"]), float(obj["bottom"])
                except (KeyError, TypeError, ValueError):
                    continue
                if (x1 - x0) > 40.0 and (bottom - top) < 2.0:
                    guides.append(BBox.from_pdfplumber(x0, top, x1, bottom))
        for line in lines:
            for run in re.finditer(r"_{2,}", line.text):
                box = line.span_bbox(run.start(), run.end())
                if box is not None and box.width > 20.0:
                    guides.append(box)
        return guides

    def _underscore_glyphs(self, page) -> list[BBox]:
        """Every '_' character box on the page, sorted left to right. Character
        level on purpose: word/line clustering is exactly what a value printed
        over the rule breaks, while the glyphs themselves still abut."""
        glyphs: list[BBox] = []
        for char in getattr(page, "chars", None) or []:
            if char.get("text") != "_":
                continue
            try:
                glyphs.append(
                    BBox.from_pdfplumber(char["x0"], char["top"], char["x1"], char["bottom"])
                )
            except (KeyError, TypeError, ValueError):
                continue
        glyphs.sort(key=lambda box: box.x)
        return glyphs

    def _extend_over_glyph_run(
        self, run: BBox, left_bound: float, right_bound: float, glyphs: list[BBox]
    ) -> BBox:
        """Grow `run` sideways over the contiguous '_' glyphs on its row, staying
        right of the label (`left_bound`) and left of the next label
        (`right_bound`). Contiguous means a gap <= UNDERSCORE_GLYPH_GAP, i.e.
        glyphs that physically abut -- a space between two separate fills is
        wider, so those stay apart. The box only ever widens: `run` itself is
        always inside the result, and y/height are untouched. A blank form's
        run already holds all of its own glyphs, so it comes back unchanged."""
        row: list[BBox] = []
        for glyph in glyphs:
            if glyph.x < left_bound - 0.5 or glyph.x1 > right_bound + 0.5:
                continue
            overlap = min(glyph.y1, run.y1) - max(glyph.y, run.y)
            if overlap < glyph.height * UNDERSCORE_ROW_OVERLAP:
                continue
            row.append(glyph)
        if not row:
            return run

        seeds = [i for i, g in enumerate(row) if g.x < run.x1 and g.x1 > run.x]
        if not seeds:
            return run

        left = seeds[0]
        while left > 0 and row[left].x - row[left - 1].x1 <= UNDERSCORE_GLYPH_GAP:
            left -= 1
        right = seeds[-1]
        while right + 1 < len(row) and row[right + 1].x - row[right].x1 <= UNDERSCORE_GLYPH_GAP:
            right += 1

        x0 = min(run.x, row[left].x)
        x1 = max(run.x1, row[right].x1)
        if x0 == run.x and x1 == run.x1:
            return run
        return BBox(x=x0, y=run.y, width=x1 - x0, height=run.height)

    def _anchor_from_guides(
        self,
        label_box: BBox,
        right_edge: float,
        anchors: list[BBox],
        height: float,
        pattern: LabelPattern,
    ) -> BBox | None:
        """Pick the nearest fill guide that sits on/just below the label baseline
        and extends to its right, then return a fill box anchored on it. Returns
        None whenever no guide fits cleanly (width below MIN_FILL_WIDTH, wrong
        side, or off baseline), so the caller falls back safely -- this only ever
        relocates an already-created field's box, never adds or drops one."""
        best: tuple[float, BBox] | None = None
        for guide in anchors:
            if guide.x1 <= label_box.x1 + 4.0 or guide.x >= right_edge:
                continue
            dy = guide.y - label_box.y
            if dy < -6.0 or dy > 26.0:  # same row, or ~1-2 baseline buckets below
                continue
            start_x = max(guide.x, label_box.x1 + 4.0)
            width = min(guide.x1, right_edge) - start_x
            if width < MIN_FILL_WIDTH:
                continue
            # A signature sits ON the rule; a date/text sits astride it.
            top = guide.y - height if pattern.field_type == "signature" else guide.y - height / 2
            score = abs(dy)
            if best is None or score < best[0]:
                best = (score, BBox(x=start_x, y=max(top, 0.0), width=width, height=height))
        return best[1] if best is not None else None

    # -- checkboxes ----------------------------------------------------------

    def _detect_checkboxes(
        self, line: _Line, page_number: int, counters: dict[str, int]
    ) -> list[FieldRegion]:
        glyphs = list(CHECKBOX_RE.finditer(line.text))
        if not glyphs:
            return []

        label_part = line.text[: glyphs[0].start()].strip(" :")
        group_label = label_part or "Options"

        options: list[dict] = []
        for index, glyph in enumerate(glyphs):
            end = glyphs[index + 1].start() if index + 1 < len(glyphs) else len(line.text)
            option_label = line.text[glyph.end() : end].strip(" :,;")
            box = line.span_bbox(glyph.start(), end) or line.span_bbox(glyph.start(), glyph.end())
            glyph_box = line.span_bbox(glyph.start(), glyph.end())
            if box is None or glyph_box is None:
                continue
            options.append(
                {
                    "id": _slugify(option_label) or f"option_{index + 1}",
                    "label": option_label,
                    "bbox": glyph_box.to_dict(),
                    "region_bbox": box.to_dict(),
                }
            )

        if not options:
            return []

        group_box = line.bbox
        if len(options) == 1:
            return [
                FieldRegion(
                    field_id=self._unique_id(page_number, _slugify(options[0]["label"]), counters),
                    label=options[0]["label"] or group_label,
                    field_type="checkbox",
                    page=page_number,
                    bbox=options[0]["bbox"],
                    required=False,
                    detector="checkbox_glyph",
                )
            ]
        return [
            FieldRegion(
                field_id=self._unique_id(page_number, _slugify(group_label), counters),
                label=group_label,
                field_type="checkbox_group",
                page=page_number,
                bbox=group_box.to_dict(),
                required=True,
                options=options,
                validation_rule="at_least_one",
                detector="checkbox_glyph",
            )
        ]

    def _detect_rect_checkboxes(
        self, page, page_number: int, lines: list[_Line], counters: dict[str, int]
    ) -> list[FieldRegion]:
        """Fallback for templates that draw checkboxes as vector squares rather
        than emitting a U+2610 glyph."""
        squares: list[BBox] = []
        for rect in page.rects:
            width = float(rect["x1"]) - float(rect["x0"])
            height = float(rect["bottom"]) - float(rect["top"])
            if 5.0 <= width <= 18.0 and 5.0 <= height <= 18.0 and abs(width - height) <= 3.0:
                squares.append(BBox.from_pdfplumber(rect["x0"], rect["top"], rect["x1"], rect["bottom"]))
        if not squares:
            return []

        fields: list[FieldRegion] = []
        for square in squares:
            label, label_box = self._label_right_of(square, lines)
            if label_box is None:
                continue
            fields.append(
                FieldRegion(
                    field_id=self._unique_id(page_number, _slugify(label) or "checkbox", counters),
                    label=label,
                    field_type="checkbox",
                    page=page_number,
                    bbox=square.to_dict(),
                    required=False,
                    detector="checkbox_rect",
                )
            )
        return fields

    def _label_right_of(self, square: BBox, lines: list[_Line]) -> tuple[str, BBox | None]:
        best: tuple[float, str, BBox] | None = None
        for line in lines:
            for word in line.words:
                vertical_gap = abs(word.bbox.y - square.y)
                horizontal_gap = word.bbox.x - square.x1
                if vertical_gap <= 6.0 and 0 <= horizontal_gap <= 60.0:
                    if best is None or horizontal_gap < best[0]:
                        best = (horizontal_gap, word.text, word.bbox)
        if best is None:
            return "", None
        return best[1], best[2]

    # -- tables --------------------------------------------------------------

    def _detect_tables(self, page, page_number: int, page_height: float) -> list[FieldRegion]:
        fields: list[FieldRegion] = []
        try:
            tables = page.find_tables()
        except Exception:  # pdfplumber raises on some malformed pages
            return fields

        for index, table in enumerate(tables):
            x0, top, x1, bottom = table.bbox
            rows = len(table.rows) if getattr(table, "rows", None) else 0
            fields.append(
                FieldRegion(
                    field_id=f"p{page_number}_table_{index + 1}",
                    label=f"Table {index + 1}",
                    field_type="table",
                    page=page_number,
                    bbox=BBox.from_pdfplumber(x0, top, x1, bottom).to_dict(),
                    required=False,
                    validation_rule="at_least_one_row",
                    detector="table",
                )
            )
            _ = rows
        return fields

    # -- assembly ------------------------------------------------------------

    def _pair_signatures_with_dates(self, fields: list[FieldRegion]) -> list[FieldRegion]:
        """Associate each signature with the nearest date field to its right on
        the same page, so date placement uses real coordinates rather than a
        fixed pixel offset from the signature."""
        by_page: dict[int, list[FieldRegion]] = {}
        for region in fields:
            by_page.setdefault(region.page, []).append(region)

        for page_fields in by_page.values():
            dates = [f for f in page_fields if f.field_type == "date"]
            claimed: set[str] = set()
            for signature in (f for f in page_fields if f.field_type == "signature"):
                sig_box = BBox.from_dict(signature.bbox)
                candidates = []
                for candidate in dates:
                    if candidate.field_id in claimed:
                        continue
                    box = BBox.from_dict(candidate.bbox)
                    # B8: accept a date box on the SAME row or the row just BELOW
                    # the signature (larger y = further down the page). Widened
                    # downward only, so every pairing the old +/-40 window made is
                    # still made -- nothing previously paired becomes unpaired.
                    dy = box.y - sig_box.y
                    if dy < -40.0 or dy > 55.0:
                        continue
                    candidates.append((abs(box.x - sig_box.x1), candidate))
                if candidates:
                    candidates.sort(key=lambda pair: pair[0])
                    chosen = candidates[0][1]
                    signature.paired_date_field_id = chosen.field_id
                    claimed.add(chosen.field_id)
        return fields

    def _unique_id(self, page_number: int, name: str, counters: dict[str, int]) -> str:
        base = f"p{page_number}_{name}"
        counters[base] = counters.get(base, 0) + 1
        occurrence = counters[base]
        # 'Date:' appears beside every signature; suffixing keeps the contractor
        # and company-rep dates from collapsing into one field.
        return base if occurrence == 1 else f"{base}_{occurrence}"

    def _sort(self, fields: Iterable[FieldRegion]) -> list[FieldRegion]:
        return sorted(fields, key=lambda f: (f.page, f.bbox["y"], f.bbox["x"]))


def _slugify(text: str | Any) -> str:
    return re.sub(r"[^\w]+", "_", str(text or "").lower()).strip("_")[:60]
