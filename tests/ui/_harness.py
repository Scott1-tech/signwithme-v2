"""
Shared harness for the SignWithMe browser checks.

Everything the app asks for under `/api/*` is answered from this file. Nothing
reaches the live FastAPI backend, nothing touches `backend/local.db`, and no
real contract is ever opened: the PDF and the signature PNG are generated in
memory, byte for byte, on every run.

Style copied from the retired `swm_layout/zz_mock_run.js`:
  * one router for every `/api/` request (unknown paths answer 404 and are
    recorded, so a silent mock gap shows up as a failure, not as a hang);
  * a mock bearer token injected into `localStorage` before the first script
    runs, so the app believes it is signed in;
  * `window.confirm` replaced by a recorder, so its text can be asserted.

The target is the running Vite dev server (default `http://127.0.0.1:5173`,
override with the `SWM_UI_BASE` environment variable). The browser is this
file's own headless Chromium, so the Playwright MCP browser can stay busy.
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
import time
import traceback
import zlib
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

BASE = os.environ.get("SWM_UI_BASE", "http://127.0.0.1:5173").rstrip("/")

# App text contains em dashes and typographic quotes; a cp1252 console would
# otherwise abort the run while printing a failure message.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - older/odd streams simply keep their encoding
        pass

# US Letter, the size every mock page uses.
PAGE_W = 612.0
PAGE_H = 792.0

# The two placement constants the preview shares with the PDF builder.
SIGNATURE_INK_GAP = 1.0
DATE_INK_GAP = 1.0

#: Sentinel: leave this key out of the JSON body entirely (an older server).
OMIT = object()


# --------------------------------------------------------------------------
# Synthetic documents (never a real contract)
# --------------------------------------------------------------------------

def synthetic_pdf(rule_lines_top_left: list[float]) -> bytes:
    """A one-page US-Letter PDF with a horizontal rule under every given line.

    `rule_lines_top_left` are in the app's coordinate space (top-left origin);
    the PDF itself is bottom-left, hence the `PAGE_H - y`.
    """
    parts = ["1 w"]
    for y in sorted(set(rule_lines_top_left)):
        pdf_y = PAGE_H - y
        parts.append(f"72 {pdf_y:.0f} m 300 {pdf_y:.0f} l S")
        parts.append(f"350 {pdf_y:.0f} m 480 {pdf_y:.0f} l S")
    content = " ".join(parts)

    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
        "/Contents 4 0 R /Resources << >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]

    pdf = "%PDF-1.4\n"
    offsets: list[int] = []
    for index, obj in enumerate(objs):
        offsets.append(len(pdf))
        pdf += f"{index + 1} 0 obj\n{obj}\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n"
    pdf += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF"
    )
    return pdf.encode("ascii")


def synthetic_multipage_pdf(page_count: int, rule_lines_top_left: list[float] | None = None) -> bytes:
    """`page_count` US-Letter pages, every one of them covered in ink.

    The viewer checks assert that a page which *should* be painted really has
    pixels, and a blank page cannot tell "rendered" from "never rendered" --
    so each page carries a big filled rectangle plus its own number in 48 pt
    Helvetica. Page 1 also gets the rules `synthetic_pdf` draws, so the slot
    fixture still sits on a line.

    Object numbering: 1 catalog, 2 page tree, 3 font, then a page/content pair
    per page (4+2i / 5+2i).
    """
    rules = rule_lines_top_left or []
    objs: list[str] = [
        "",  # 1: catalog, filled in once the kids are known
        "",  # 2: page tree
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    kids: list[str] = []
    for index in range(page_count):
        page_obj = 4 + index * 2
        content_obj = page_obj + 1
        kids.append(f"{page_obj} 0 R")

        parts = [
            # A solid block: ~46 % of the page, so even a 120 px thumbnail of
            # it has thousands of non-white pixels.
            "0.08 0.08 0.08 rg 72 300 468 300 re f",
            f"BT /F1 48 Tf 72 650 Td (Page {index + 1} of {page_count}) Tj ET",
        ]
        if index == 0:
            parts.append("0 0 0 RG 1 w")
            for y in sorted(set(rules)):
                pdf_y = PAGE_H - y
                parts.append(f"72 {pdf_y:.0f} m 300 {pdf_y:.0f} l S")
                parts.append(f"350 {pdf_y:.0f} m 480 {pdf_y:.0f} l S")
        content = " ".join(parts)

        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
            f"/Contents {content_obj} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        )
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")

    objs[0] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {page_count} >>"

    pdf = "%PDF-1.4\n"
    offsets: list[int] = []
    for index, obj in enumerate(objs):
        offsets.append(len(pdf))
        pdf += f"{index + 1} 0 obj\n{obj}\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n"
    pdf += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF"
    )
    return pdf.encode("ascii")


def synthetic_signature_png(width: int = 300, height: int = 100) -> bytes:
    """A transparent PNG with one ink stroke, written without any dependency.

    The natural size matters: `signatureRect` fits `natural` into
    `bbox x scale` with `contain`, so 300x100 in a 180x34 bbox is a
    deterministic 102 x 34 pt at 100 %.
    """
    row_bytes = width * 4
    pixels = bytearray(row_bytes * height)
    for x in range(width):
        centre = height / 2 + (height * 0.3) * math.sin(x / 40.0)
        for y in range(height):
            if abs(y - centre) < 6:
                base = y * row_bytes + x * 4
                pixels[base] = 25
                pixels[base + 1] = 30
                pixels[base + 2] = 60
                pixels[base + 3] = 255

    raw = b"".join(
        b"\x00" + bytes(pixels[y * row_bytes:(y + 1) * row_bytes]) for y in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def solid_png(width: int, height: int, rgba: tuple[int, int, int, int] = (29, 78, 216, 255)) -> bytes:
    """A PNG of one colour, `width` x `height` -- a synthetic stamp (W4)."""
    row = b"\x00" + bytes(rgba) * width
    raw = row * height

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def decode_data_url(value: str) -> bytes | None:
    """`data:image/...;base64,XXXX` (or bare base64) -> bytes, else None."""
    import base64
    import binascii

    if not isinstance(value, str) or not value:
        return None
    payload = value.split(",", 1)[1] if value.startswith("data:") else value
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a PNG or baseline/progressive JPEG, from its header."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8"):
        at = 2
        while at + 9 < len(data):
            if data[at] != 0xFF:
                return None
            marker = data[at + 1]
            length = struct.unpack(">H", data[at + 2:at + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", data[at + 5:at + 9])
                return width, height
            at += 2 + length
    return None


# --------------------------------------------------------------------------
# Reading a screenshot back
# --------------------------------------------------------------------------
#
# A check that wants to say "this is really on the screen" has to look at
# pixels, and a full-size page screenshot is well over a million of them --
# far too many to walk in Python per check. So the picture goes back into the
# browser it came from: `createImageBitmap` + `OffscreenCanvas` decode and
# count it in milliseconds, and nothing is added to the page's DOM.
#
# The three colour bands below are the whole vocabulary. They are deliberately
# disjoint, so "is this mark the current one" is a fact about pixels and not
# about a class name:
#
#   dark    the synthetic pages are black on white -- this is ink
#   mark    `.pdf-search-hit`, rgba(255, 214, 0, .55) over white ~= (255,232,115)
#   active  `.pdf-search-hit--active`, rgba(255, 138, 0, .75) ~= (255,167,64)
#
# The `r - g` bounds are what separates the two from each other **and** from
# the amber the review screen antialiases its own marks with (~218,171,44 and
# ~240,199,136, a couple of hundred pixels on a clean page). Measured, not
# guessed: widening either bound puts those back in.

PIXEL_BANDS_JS = """
async (args) => {
  const binary = atob(args.png);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const context = canvas.getContext('2d', { willReadFrequently: true });
  context.drawImage(bitmap, 0, 0);
  const { data } = context.getImageData(0, 0, bitmap.width, bitmap.height);

  const strips = Math.max(1, args.strips | 0);
  const dark = new Array(strips).fill(0);
  const mark = new Array(strips).fill(0);
  const active = new Array(strips).fill(0);

  for (let y = 0; y < bitmap.height; y += 1) {
    for (let x = 0; x < bitmap.width; x += 1) {
      const at = (y * bitmap.width + x) * 4;
      if (data[at + 3] === 0) continue;
      const r = data[at], g = data[at + 1], b = data[at + 2];
      const strip = Math.min(strips - 1, Math.floor((x / bitmap.width) * strips));
      if (r < 120 && g < 120 && b < 120) dark[strip] += 1;
      else if (r >= 220 && g >= 210 && b <= 160 && g - b >= 45 && r - g <= 45) mark[strip] += 1;
      else if (r >= 200 && g >= 130 && g <= 215 && b <= 150 && r - g >= 60 && g - b >= 20)
        active[strip] += 1;
    }
  }

  const sum = (list) => list.reduce((a, b) => a + b, 0);
  return {
    width: bitmap.width,
    height: bitmap.height,
    darkStrips: dark, markStrips: mark, activeStrips: active,
    dark: sum(dark), mark: sum(mark), active: sum(active),
  };
}
"""


#: Where the picture is taken from.
#:
#: NOT `locator.screenshot()`. A `.pdf-page` lives inside `.pdf-stage`, which
#: is its own scrolling box; Playwright's element screenshot resolves the clip
#: against the *document*, and the stage's internal scroll does not move the
#: document, so it happily returns the app header and whichever page happens to
#: be under that rectangle. (Seen and measured: pages 1 and 2 came back byte-
#: for-byte identical while the marks on them were plainly different colours.)
#:
#: So the stage is scrolled by hand, the clip is the page's own rectangle cut
#: down to the part of the stage that is really on screen, and the caller is
#: told whether the whole page fitted.
CLIP_JS = """
(args) => {
  const el = document.querySelector(args.selector);
  const stage = document.querySelector('.pdf-stage');
  if (!el || !stage) return null;
  const a0 = el.getBoundingClientRect();
  const b0 = stage.getBoundingClientRect();
  if (args.align) stage.scrollTop += a0.top - b0.top - 2;
  const a = el.getBoundingClientRect();
  const b = stage.getBoundingClientRect();
  const left = Math.max(a.left, b.left, 0);
  const top = Math.max(a.top, b.top, 0);
  const right = Math.min(a.right, b.right, window.innerWidth);
  const bottom = Math.min(a.bottom, b.bottom, window.innerHeight);
  return {
    x: left, y: top,
    width: Math.max(0, right - left), height: Math.max(0, bottom - top),
    whole:
      a.left >= left - 1 && a.top >= top - 1 &&
      a.right <= right + 1 && a.bottom <= bottom + 1,
  };
}
"""


def pixel_bands(
    page,
    selector: str,
    strips: int = 1,
    *,
    whole: bool = True,
    align: bool = True,
) -> dict[str, Any]:
    """Screenshot one page and count its ink / mark / current-match pixels.

    `strips` splits the image into that many vertical bands, left to right --
    a fingerprint of *where* the ink is, which is what shows that a page has
    been turned rather than merely relabelled.

    `whole=True` insists the page fitted inside the stage, so two readings can
    be compared: a half-captured page would make any such comparison a lie.
    Zoom out first (`zoom_out`) if it does not fit.
    """
    import base64

    clip = page.evaluate(CLIP_JS, {"selector": selector, "align": align})
    if clip is None:
        raise AssertionError(f"no element matching {selector!r} inside .pdf-stage")
    if align:
        page.wait_for_timeout(150)
        clip = page.evaluate(CLIP_JS, {"selector": selector, "align": False})
    if clip["width"] < 2 or clip["height"] < 2:
        raise AssertionError(f"{selector!r} is not on screen ({clip})")
    if whole and not clip["whole"]:
        raise AssertionError(
            f"{selector!r} does not fit inside the stage ({clip}); zoom out first"
        )

    shot = page.screenshot(
        clip={"x": clip["x"], "y": clip["y"], "width": clip["width"], "height": clip["height"]}
    )
    bands = page.evaluate(
        PIXEL_BANDS_JS,
        {"png": base64.b64encode(shot).decode("ascii"), "strips": strips},
    )
    bands["whole"] = clip["whole"]
    return bands


def zoom_out(page, times: int = 1) -> None:
    """Press the toolbar's zoom-out until a whole page fits inside the stage.

    Leaves fit-to-width mode (`applyScale(..., { manual: true })`), which is
    what a pixel check wants: the scale then stops moving under it.
    """
    button = page.get_by_role("button", name="−", exact=True)
    for _ in range(times):
        button.click()
        page.wait_for_timeout(120)
    page.wait_for_timeout(600)


# --------------------------------------------------------------------------
# Default fixture data
# --------------------------------------------------------------------------

def slot(
    field_id: str,
    kind: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    rule_y: float | None,
    label: str | None = None,
    required: bool = True,
    page: int | None = 1,
    paired_date: str | None = None,
    paired_signature: str | None = None,
) -> dict[str, Any]:
    return {
        "field_id": field_id,
        "label": label or field_id.replace("_", " ").title(),
        "page": page,
        "bbox": {"x": x, "y": y, "width": width, "height": height},
        "rule_y": rule_y,
        "kind": kind,
        "required": required,
        "paired_date_field_id": paired_date,
        "paired_signature_field_id": paired_signature,
        "severity": "info",
    }


def default_slots() -> list[dict[str, Any]]:
    """Two signature/date pairs: one with a detected rule, one without.

    Effective lines (`ruleLine`): sig_1/date_1 = 199.5 (rule_y), sig_2 = 394
    and date_2 = 400 (bbox bottom, because `rule_y` is null).
    """
    return [
        slot("sig_1", "signature", 72, 160, 180, 34, rule_y=199.5,
             label="Carrier signature", paired_date="date_1"),
        slot("date_1", "date", 350, 180, 120, 20, rule_y=199.5,
             label="Carrier date", paired_signature="sig_1"),
        slot("sig_2", "signature", 72, 360, 180, 34, rule_y=None,
             label="Driver signature", paired_date="date_2"),
        slot("date_2", "date", 350, 380, 120, 20, rule_y=None,
             label="Driver date", paired_signature="sig_2"),
    ]


def rule_line(a_slot: dict[str, Any]) -> float:
    """Mirror of `lib/layout.ts ruleLine()`."""
    rule_y = a_slot.get("rule_y")
    if isinstance(rule_y, (int, float)):
        return float(rule_y)
    bbox = a_slot["bbox"]
    return float(bbox["y"] + bbox["height"])


INIT_SCRIPT = """
(() => {
  try {
    localStorage.setItem('csa.token', 'swm-ui-mock-token');
    localStorage.setItem('swm.signMode', 'bulk');
    // The first-load automatic fill is a separate feature; keep it out of the
    // way so every check starts from a known, empty page.
    localStorage.setItem('swm.autoFill', 'false');
  } catch (_) { /* about:blank has no storage */ }
  window.__swmConfirms = [];
  window.__swmConfirmResult = true;
  window.confirm = (message) => {
    window.__swmConfirms.push(String(message));
    return window.__swmConfirmResult !== false;
  };
})();
"""


# --------------------------------------------------------------------------
# The mock API
# --------------------------------------------------------------------------

class MockApi:
    """One router for every `/api/` request the app can make."""

    def __init__(self, contract_id: str = "ZZ-UI-1", role: str = "admin") -> None:
        self.contract_id = contract_id
        self.role = role

        self.slots = default_slots()
        self.missing_required: list[dict[str, Any]] = []
        self.inconsistencies: list[dict[str, Any]] = []
        self.fmcsa_violations: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.template_match: Any = OMIT
        self.can_sign = True

        self.status = "signing"
        self.review_status = "approved"
        self.review_override: Any = OMIT
        self.review_override_reason: Any = OMIT
        self.review_override_fields: Any = OMIT

        # Contract-scoped signature rows: by default both signature slots are
        # already signed, so nothing has to be drawn before a layout check.
        self.signed_field_ids = ["sig_1", "sig_2"]
        # The signer's personal library. Empty keeps the picker on "draw".
        self.user_signatures: list[dict[str, Any]] = []

        self.templates: list[dict[str, Any]] = []

        # Per-endpoint canned answers a test may replace.
        self.finalize_response: dict[str, Any] = {
            "status": 200,
            "body": {
                "contract_id": contract_id,
                "status": "completed",
                "file_hash": "zz-mock-hash",
                "download_url": "",
            },
        }
        self.bulk_response: dict[str, Any] | None = None
        self.place_response: dict[str, Any] | None = None
        self.review_response: dict[str, Any] | None = None
        # W4: annotation image assets. `asset_response` (a canned
        # `{"status", "body"}`) replaces the default answer of the upload; by
        # default the mock decodes the image's size from its header, stores the
        # bytes under a fresh 32-hex id and serves them back on GET -- the way
        # the backend does (minus the PNG re-encode).
        self.asset_response: dict[str, Any] | None = None
        self.assets: dict[str, bytes] = {}
        self.asset_uploads: list[Any] = []
        # W5: free placements (initials). Off by default -- every older check
        # keeps the one canned `zz-sig-new` answer above. With `place_echo`
        # on, each POST of a FREE signature answers with ITS OWN row (a fresh
        # id, the page and bbox it was sent), keeps it in `free_rows` (so a
        # reload lists it), and every body lands in `place_bodies`;
        # `place_fail_pages` maps a page number to the HTTP status the POST
        # for that page answers with (a partial failure). `free_rows` can also
        # be seeded (a row "placed on another computer"). DELETEs are recorded
        # in `deleted_signatures` and drop the row from `free_rows`.
        self.place_echo = False
        self.place_bodies: list[Any] = []
        self.place_fail_pages: dict[int, int] = {}
        self.free_rows: list[dict[str, Any]] = []
        self.deleted_signatures: list[str] = []
        # W5: `POST /api/signatures` (save to the personal library) -- the
        # bodies, in order; the saved entry joins `user_signatures`.
        self.library_saves: list[Any] = []

        # Recorded traffic.
        self.calls: list[str] = []
        self.bodies: dict[str, Any] = {}
        self.unmocked: list[str] = []
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []

        self._pdf = synthetic_pdf([rule_line(s) for s in self.slots])
        self._png = synthetic_signature_png()

        # F1b: the editor v2 document (`/document*`). `None` = a server without
        # the editor: every such call answers 404 on purpose (not "unmocked"),
        # and the app keeps the legacy `/file` path. `_docmock.enable_document`
        # installs a `DocMock`.
        self.document: Any = None

        # F2 (B2): the user's gallery (`/api/assets`), the fonts (`/api/fonts`),
        # typed signatures (`POST /api/signatures {typed}`), gallery copies onto
        # the contract (`annotation-assets {from_asset_id}`).
        self.gallery: dict[str, dict[str, Any]] = {}
        self.gallery_bytes: dict[str, bytes] = {}
        self.gallery_deleted: list[str] = []
        self.gallery_copies: list[str] = []
        self.typed_saves: list[Any] = []
        self.fonts = [
            {"family": "LiberationSans", "label": "Liberation Sans", "styles": ["regular", "bold", "italic", "bold_italic"],
             "license": "OFL-1.1", "substitutes_for": ["Arial", "Helvetica"], "scripts": ["latin", "cyrillic"],
             "embedded": True, "category": "text"},
            {"family": "Carlito", "label": "Carlito", "styles": ["regular", "bold", "italic", "bold_italic"],
             "license": "OFL-1.1", "substitutes_for": ["Calibri"], "scripts": ["latin", "cyrillic", "greek"],
             "embedded": True, "category": "text"},
            {"family": "Caladea", "label": "Caladea", "styles": ["regular", "bold", "italic", "bold_italic"],
             "license": "OFL-1.1", "substitutes_for": ["Cambria"], "scripts": ["latin"],
             "embedded": True, "category": "text"},
            {"family": "DejaVuSans", "label": "DejaVu Sans", "styles": ["regular", "bold", "italic", "bold_italic"],
             "license": "Bitstream-Vera", "substitutes_for": [], "scripts": ["latin", "cyrillic", "greek"],
             "embedded": True, "category": "text"},
            {"family": "GreatVibes", "label": "Great Vibes", "styles": ["regular"], "license": "OFL-1.1",
             "substitutes_for": [], "scripts": ["latin"], "embedded": True, "category": "signature"},
            {"family": "Allura", "label": "Allura", "styles": ["regular"], "license": "OFL-1.1",
             "substitutes_for": [], "scripts": ["latin"], "embedded": True, "category": "signature"},
            {"family": "Sacramento", "label": "Sacramento", "styles": ["regular"], "license": "OFL-1.1",
             "substitutes_for": [], "scripts": ["latin"], "embedded": True, "category": "signature"},
        ]

    # -- fixture helpers ---------------------------------------------------

    def set_slots(self, slots: list[dict[str, Any]], signed: list[str] | None = None) -> None:
        self.slots = slots
        if signed is not None:
            self.signed_field_ids = signed
        self._pdf = synthetic_pdf([rule_line(s) for s in self.slots])

    def set_pdf(self, data: bytes) -> None:
        """Serve this document instead of the generated one-pager.

        Call it *after* `set_slots`, which rebuilds the default PDF from the
        slots it is given.
        """
        self._pdf = data

    def slot_by_id(self, field_id: str) -> dict[str, Any]:
        for item in self.slots:
            if item["field_id"] == field_id:
                return item
        raise KeyError(field_id)

    def line_of(self, field_id: str) -> float:
        return rule_line(self.slot_by_id(field_id))

    def contract_payload(self) -> dict[str, Any]:
        now = "2026-09-23T08:00:00+00:00"
        payload: dict[str, Any] = {
            "id": self.contract_id,
            "template_id": "ZZ-UI-T1",
            "contractor_name": "ZZ UI Baseline",
            "contractor_email": None,
            "status": self.status,
            "review_status": self.review_status,
            "error_message": None,
            "reviewed_at": now,
            "review_notes": None,
            "created_at": now,
            "updated_at": now,
            "extracted_data": {},
            "comparison_result": {},
            "final_available": False,
        }
        if self.review_override is not OMIT:
            payload["review_override"] = self.review_override
        if self.review_override_reason is not OMIT:
            payload["review_override_reason"] = self.review_override_reason
        if self.review_override_fields is not OMIT:
            payload["review_override_fields"] = self.review_override_fields
        return payload

    def compare_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract_id": self.contract_id,
            "completion_percentage": 100 if self.can_sign else 80,
            "missing_required": self.missing_required,
            "inconsistencies": self.inconsistencies,
            "fmcsa_violations": self.fmcsa_violations,
            "warnings": self.warnings,
            "signature_slots": self.slots,
            "can_sign": self.can_sign,
            "field_count": 10,
            "filled_count": 10 if self.can_sign else 8,
        }
        if self.template_match is not OMIT:
            payload["template_match"] = self.template_match
        return payload

    def signature_rows(self) -> list[dict[str, Any]]:
        rows = []
        for index, field_id in enumerate(self.signed_field_ids, start=1):
            try:
                target = self.slot_by_id(field_id)
            except KeyError:
                continue
            rows.append({
                "id": f"zz-sig-{index}",
                "contract_id": self.contract_id,
                "signer_name": None,
                "signer_email": None,
                "signer_role": "contractor",
                "page_number": target.get("page") or 1,
                "bbox": target["bbox"],
                "field_id": field_id,
                "signed_at": "2026-09-23T08:00:00+00:00",
            })
        return rows + [dict(row) for row in self.free_rows]

    # -- wiring ------------------------------------------------------------

    def install(self, page) -> None:
        page.add_init_script(INIT_SCRIPT)
        # Only real API calls: Vite serves the app's own modules from
        # `/src/api/*.ts`, which must keep reaching the dev server.
        page.route(lambda url: urlsplit(url).path.startswith("/api/"), self._handle)
        page.on("pageerror", lambda exc: self.page_errors.append(str(exc)))
        page.on(
            "console",
            lambda message: (
                self.console_errors.append(message.text)
                if message.type == "error"
                else None
            ),
        )

    # -- the router --------------------------------------------------------

    def _json(self, route, status: int, body: Any) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    def _handle(self, route) -> None:
        request = route.request
        split = urlsplit(request.url)
        path = split.path
        query = split.query
        method = request.method
        self.calls.append(f"{method} {path}" + (f"?{query}" if query else ""))

        cid = self.contract_id

        def body_json() -> Any:
            try:
                return request.post_data_json
            except Exception:
                return None

        from _sharemock import route_share  # F3: share links, submissions, /api/share/*, form fields
        if route_share(self, route, method, path, query, body_json, request):
            return None

        # --- auth ---------------------------------------------------------
        if path == "/api/auth/me":
            return self._json(route, 200, {
                "id": "zz-user",
                "email": "zz-ui@example.test",
                "full_name": "ZZ UI Runner",
                "role": self.role,
                "is_active": True,
            })

        # --- contract, comparison, document --------------------------------
        if path == f"/api/contracts/{cid}" and method == "GET":
            return self._json(route, 200, self.contract_payload())
        if path == f"/api/contracts/{cid}/compare":
            return self._json(route, 200, self.compare_payload())
        if path == f"/api/contracts/{cid}/file":
            return route.fulfill(status=200, content_type="application/pdf", body=self._pdf)
        # --- editor v2 document (F1b) ---------------------------------------
        prefix = f"/api/contracts/{cid}/document"
        if path == prefix or path.startswith(prefix + "/"):
            if self.document is None:
                # An older server: no editor. Deliberate, so not "unmocked".
                return self._json(route, 404, {"error": "not_found", "message": "Not Found", "details": {}})
            return self.document.handle(self, route, method, path[len(prefix):], query, body_json)
        if path == "/api/contracts" and method == "GET":
            return self._json(route, 200, [])
        if path == f"/api/contracts/{cid}/fields":
            return self._json(route, 200, [])
        if path == f"/api/contracts/{cid}/date-fields":
            return self._json(route, 200, [])

        # --- signatures on this contract -----------------------------------
        if path == f"/api/contracts/{cid}/signatures" and method == "GET":
            return self._json(route, 200, self.signature_rows())
        if path.startswith(f"/api/contracts/{cid}/signatures/") and path.endswith("/image"):
            return route.fulfill(status=200, content_type="image/png", body=self._png)
        if path == f"/api/contracts/{cid}/signatures/bulk" and method == "POST":
            self.bodies["bulk"] = body_json()
            canned = self.bulk_response or {
                "status": 200,
                "body": {"contract_id": cid, "placed": [], "skipped": [], "failed": []},
            }
            return self._json(route, canned["status"], canned["body"])
        if path == f"/api/contracts/{cid}/signatures" and method == "POST":
            self.bodies["place"] = body_json()
            placed = body_json() if isinstance(body_json(), dict) else {}
            if self.place_echo and placed.get("field_id") is None:
                self.place_bodies.append(placed)
                page_number = int(placed.get("page") or 1)
                failure = self.place_fail_pages.get(page_number)
                if failure:
                    return self._json(route, failure, {
                        "error": "zz_refused",
                        "message": f"Refused on page {page_number}",
                        "details": {},
                    })
                row = {
                    "id": f"zz-free-{len(self.place_bodies)}",
                    "contract_id": cid,
                    "signer_name": None,
                    "signer_email": None,
                    "signer_role": "contractor",
                    "page_number": page_number,
                    "bbox": placed.get("bbox"),
                    "field_id": None,
                    "signed_at": "2026-09-23T08:00:00+00:00",
                }
                self.free_rows.append(row)
                return self._json(route, 201, row)
            canned = self.place_response or {
                "status": 200,
                "body": {
                    "id": "zz-sig-new",
                    "contract_id": cid,
                    "signer_name": None,
                    "signer_email": None,
                    "signer_role": "contractor",
                    "page_number": 1,
                    "bbox": {"x": 72, "y": 160, "width": 180, "height": 34},
                    "field_id": None,
                    "signed_at": "2026-09-23T08:00:00+00:00",
                },
            }
            return self._json(route, canned["status"], canned["body"])
        if path.startswith(f"/api/contracts/{cid}/signatures/") and method == "DELETE":
            removed = path.rsplit("/", 1)[-1]
            self.deleted_signatures.append(removed)
            self.free_rows = [row for row in self.free_rows if row["id"] != removed]
            return self._json(route, 200, {"removed": True})

        # --- F2: the gallery, the fonts ------------------------------------
        if path == "/api/fonts" and method == "GET":
            category = (parse_qs(query).get("category") or ["text"])[0]
            return self._json(route, 200, [font for font in self.fonts if font["category"] == category])
        if path == "/api/assets" and method == "GET":
            return self._json(route, 200, list(self.gallery.values()))
        if path == "/api/assets" and method == "POST":
            body = body_json() or {}
            data = decode_data_url(body.get("image_data", "")) if isinstance(body, dict) else None
            size = image_size(data) if data else None
            if not data or size is None:
                return self._json(route, 422, {"error": "validation_error", "message": "Image is corrupt", "details": {}})
            asset_id = f"a{len(self.gallery) + len(self.gallery_deleted) + 1:031x}"
            entry = {"asset_id": asset_id, "kind": body.get("kind", "image"), "label": body.get("label"),
                     "width_px": size[0], "height_px": size[1], "byte_size": len(data), "sha256": "zz",
                     "created_at": "2026-09-30T08:00:00+00:00"}
            self.gallery[asset_id] = entry
            self.gallery_bytes[asset_id] = data
            return self._json(route, 201, entry)
        if path.startswith("/api/assets/") and path.endswith("/image") and method == "GET":
            asset_id = path.split("/")[3]
            data = self.gallery_bytes.get(asset_id)
            if data is None or asset_id not in self.gallery:
                return self._json(route, 404, {"error": "not_found", "message": "No such asset", "details": {}})
            return route.fulfill(status=200, content_type="image/png", body=data)
        if path.startswith("/api/assets/") and method == "DELETE":
            asset_id = path.split("/")[3]
            self.gallery.pop(asset_id, None)
            self.gallery_deleted.append(asset_id)
            return route.fulfill(status=204, body="")

        # --- annotation image assets (contract v5, W4) ------------------------
        if path == f"/api/contracts/{cid}/annotation-assets" and method == "POST":
            body = body_json()
            self.asset_uploads.append(body)
            self.bodies["asset"] = body
            if isinstance(body, dict) and body.get("from_asset_id"):
                source = body["from_asset_id"]
                data = self.gallery_bytes.get(source)
                if data is None or source not in self.gallery:
                    return self._json(route, 404, {"error": "not_found", "message": "No such asset", "details": {}})
                size = image_size(data) or (1, 1)
                asset_id = f"{len(self.assets) + 1:032x}"
                self.assets[asset_id] = data
                self.gallery_copies.append(source)
                return self._json(route, 201, {"asset_id": asset_id, "width_px": size[0], "height_px": size[1]})
            if self.asset_response is not None:
                canned = self.asset_response
                return self._json(route, canned["status"], canned["body"])
            data = decode_data_url((body or {}).get("image_data", "")) if isinstance(body, dict) else None
            size = image_size(data) if data else None
            if not data or size is None:
                return self._json(route, 422, {
                    "error": "validation_error", "message": "Image is corrupt or unreadable", "details": {},
                })
            asset_id = f"{len(self.assets) + 1:032x}"
            self.assets[asset_id] = data
            return self._json(route, 201, {"asset_id": asset_id, "width_px": size[0], "height_px": size[1]})
        if path.startswith(f"/api/contracts/{cid}/annotation-assets/") and method == "GET":
            asset_id = path.rsplit("/", 1)[-1]
            data = self.assets.get(asset_id)
            if data is None:
                return self._json(route, 404, {
                    "error": "not_found", "message": "Annotation image not found on this contract", "details": {},
                })
            return route.fulfill(status=200, content_type="image/png", body=data)

        # --- finalize / final page ------------------------------------------
        if path == f"/api/contracts/{cid}/finalize" and method == "POST":
            self.bodies["finalize"] = body_json()
            canned = self.finalize_response
            return self._json(route, canned["status"], canned["body"])
        if path == f"/api/review/{cid}/audit":
            return self._json(route, 200, [])
        if path == f"/api/download/{cid}/verify":
            return self._json(route, 200, {
                "recorded_hash": "zz-mock-hash",
                "current_hash": "zz-mock-hash",
                "intact": True,
            })

        # --- reviewer decision ----------------------------------------------
        if path == f"/api/review/{cid}" and method == "POST":
            self.bodies["review"] = body_json()
            canned = self.review_response
            if canned:
                return self._json(route, canned["status"], canned["body"])
            payload = self.contract_payload()
            payload["review_status"] = (body_json() or {}).get("decision", "approved")
            self.review_status = payload["review_status"]
            return self._json(route, 200, payload)
        if path == "/api/review/queue":
            return self._json(route, 200, [])

        # --- the signer's personal library -----------------------------------
        if path == "/api/signatures" and method == "GET":
            return self._json(route, 200, self.user_signatures)
        if path == "/api/signatures" and method == "POST":
            saved = body_json() if isinstance(body_json(), dict) else {}
            if saved.get("typed"):
                self.typed_saves.append(saved)
            self.library_saves.append(saved)
            entry = {
                "id": f"zz-lib-{len(self.library_saves)}",
                "label": saved.get("label"),
                "is_default": False,
                "signature_hash": "zz-lib-hash",
                "created_at": "2026-09-23T08:00:00+00:00",
            }
            self.user_signatures.append(entry)
            return self._json(route, 201, entry)
        if path.startswith("/api/signatures/") and path.endswith("/image"):
            return route.fulfill(status=200, content_type="image/png", body=self._png)

        # --- templates ---------------------------------------------------------
        if path == "/api/templates" and method == "GET":
            return self._json(route, 200, self.templates)
        if path.startswith("/api/templates/") and path.endswith("/fields"):
            return self._json(route, 200, {"fields": [], "version": 1})
        if path.startswith("/api/templates/") and method == "GET":
            return self._json(route, 200, self.templates[0] if self.templates else {})

        self.unmocked.append(f"{method} {path}")
        return self._json(route, 404, {
            "error": "zz_unmocked",
            "message": f"unmocked {method} {path}",
            "details": {},
        })


# --------------------------------------------------------------------------
# Page helpers
# --------------------------------------------------------------------------

SNAPSHOT_JS = """
() => {
  const page = document.querySelector('.pdf-page');
  if (!page) return null;
  const rect = page.getBoundingClientRect();
  const scale = rect.width / 612;
  const num = (v) => (v === null || v === undefined || v === '') ? null : Number(v);
  const round = (v) => Math.round(v * 1000) / 1000;
  return {
    scale: round(scale),
    slots: [...document.querySelectorAll('.pdf-page .slot[data-field-id]')].map((el) => {
      const b = el.getBoundingClientRect();
      return {
        field: el.dataset.fieldId,
        kind: el.dataset.slotKind,
        leftPt: num(el.dataset.leftPt),
        bottomPt: num(el.dataset.bottomPt),
        domLeftPt: round((b.left - rect.left) / scale),
        domBottomPt: round((b.bottom - rect.top) / scale),
        domWidthPt: round(b.width / scale),
        domHeightPt: round(b.height / scale),
        dragging: el.classList.contains('is-dragging'),
      };
    }),
    placed: [...document.querySelectorAll('.placed-signature')].map((el) => {
      const b = el.getBoundingClientRect();
      return {
        tag: el.tagName.toLowerCase(),
        signatureId: el.dataset.signatureId || null,
        field: el.dataset.fieldId || null,
        leftPt: num(el.dataset.leftPt),
        bottomPt: num(el.dataset.bottomPt),
        domLeftPt: round((b.left - rect.left) / scale),
        domBottomPt: round((b.bottom - rect.top) / scale),
        widthPt: round(b.width / scale),
        heightPt: round(b.height / scale),
      };
    }),
    dates: [...document.querySelectorAll('.placed-dates text')].map((t) => ({
      field: t.dataset.fieldId,
      xPt: num(t.dataset.xPt),
      baselinePt: num(t.dataset.baselinePt),
      attrX: parseFloat(t.getAttribute('x')),
      attrY: parseFloat(t.getAttribute('y')),
      attrFontSize: parseFloat(t.getAttribute('font-size')),
      fontFamily: t.style.fontFamily,
      text: t.textContent,
    })),
  };
}
"""


def snapshot(page) -> dict[str, Any]:
    data = page.evaluate(SNAPSHOT_JS)
    if data is None:
        raise AssertionError("no .pdf-page in the DOM")
    return data


def by_field(rows: list[dict[str, Any]], field_id: str) -> dict[str, Any]:
    for row in rows:
        if row.get("field") == field_id:
            return row
    raise AssertionError(f"no entry for field {field_id!r} (have {[r.get('field') for r in rows]})")


def goto_blank(page, mock: MockApi) -> None:
    """Same-origin page where `localStorage` can be seeded before the app runs."""
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")


def seed_layout(page, mock: MockApi, layout: dict[str, Any]) -> None:
    goto_blank(page, mock)
    page.evaluate(
        "([key, value]) => localStorage.setItem(key, value)",
        [f"swm.layout.{mock.contract_id}", json.dumps(layout)],
    )


def open_sign(page, mock: MockApi, expect_slots: int | None = None, expect_images: int = 0) -> None:
    """Open the signing page and wait until the overlay is measurable."""
    page.goto(f"{BASE}/contracts/{mock.contract_id}/sign")
    page.wait_for_selector('[data-testid="layout-panel"]', timeout=30000)
    wanted = expect_slots if expect_slots is not None else sum(
        1 for s in mock.slots if (s.get("page") or 1) == 1 and s.get("bbox")
    )
    page.wait_for_function(
        "(n) => document.querySelectorAll('.pdf-page .slot[data-field-id]').length === n",
        arg=wanted,
        timeout=30000,
    )
    page.wait_for_function(
        "() => { const p = document.querySelector('.pdf-page');"
        " return !!p && p.getBoundingClientRect().width > 300; }",
        timeout=30000,
    )
    if expect_images:
        page.wait_for_function(
            "(n) => document.querySelectorAll('img.placed-signature').length === n",
            arg=expect_images,
            timeout=30000,
        )
    # `pageSizes` arrives in its own promise after the document opens, and the
    # per-slot ranges (and the clamp pass) only exist once it has. A short
    # settle keeps every later assertion on the fully bounded layout.
    page.wait_for_timeout(300)


def open_review(page, mock: MockApi) -> None:
    page.goto(f"{BASE}/contracts/{mock.contract_id}/review")
    # `.sidebar` is there before the comparison arrives; `.sidebar__summary`
    # only renders once `result` is loaded, which is what every check needs.
    page.wait_for_selector(".sidebar__summary", timeout=30000)


def open_templates(page, mock: MockApi) -> None:
    page.goto(f"{BASE}/templates")
    page.wait_for_selector("table.table tbody tr", timeout=30000)


def open_section(page, section: str) -> None:
    """Open one accordion section of the workspace panel if it is closed.

    `section` is `review` | `sign` | `layout` | `objects`. The URL segment only
    forces its own section open (`/sign` -> sign + layout, `/review` ->
    review, `/edit` -> objects); anything else is one click on its header.
    """
    toggle = page.locator(f'[data-testid="section-toggle-{section}"]')
    toggle.wait_for(timeout=20000)
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
        page.wait_for_selector(f'[data-testid="section-{section}"][data-open="true"]', timeout=10000)


#: The date the checks choose by hand. There is no automatic date any more
#: (2026-09-25): every date box starts empty, so a check that wants dates on
#: the page has to pick one, exactly as a user must.
CHOSEN_DATE = "2026-09-25"


def apply_dates_to_all(page, date: str = CHOSEN_DATE) -> None:
    """Choose `date` and fill every date slot from the bulk panel (no image is uploaded).

    The bulk panel lives in the workspace's "Signature & date" section, which
    is open on `/sign` and closed by default on `/review` and `/edit` -- so it
    is opened first (a no-op on `/sign`). The date is typed into the panel's
    own box first: "Apply to all" places no date without one.
    """
    open_section(page, "sign")
    page.locator('[data-testid="bulk-date"]').fill(date)
    page.get_by_role("button", name="Apply to all", exact=True).click()
    page.wait_for_function(
        "() => document.querySelectorAll('.placed-dates text').length > 0",
        timeout=15000,
    )


def stored_layout(page, mock: MockApi) -> dict[str, Any] | None:
    raw = page.evaluate(
        "(key) => localStorage.getItem(key)", f"swm.layout.{mock.contract_id}"
    )
    return json.loads(raw) if raw else None


def panel_value(page, test_id: str) -> str:
    return page.locator(f'[data-testid="{test_id}-value"]').inner_text().strip()


def panel_number(page, test_id: str) -> float:
    """`y +1.5 pt` / `x -2.0 pt` -> 1.5 / -2.0."""
    text = panel_value(page, test_id)
    cleaned = text.replace("y", "").replace("x", "").replace("pt", "").strip()
    return float(cleaned.replace("+", ""))


def slot_stepper(page, field_id: str, direction: str):
    """`direction` is one of up / down / left / right (the `aria-label` suffix)."""
    axis = "offset-slot-x" if direction in ("left", "right") else "offset-slot"
    return page.locator(
        f'[data-testid="{axis}-{field_id}"] button[aria-label$="move {direction}"]'
    )


def drag_slot(page, field_id: str, dx_px: float, dy_px: float, steps: int = 12) -> None:
    """Press on a slot box and drag it, crossing the 3 px threshold first."""
    handle = page.locator(f'.pdf-page .slot[data-field-id="{field_id}"]')
    handle.scroll_into_view_if_needed()
    box = handle.bounding_box()
    if box is None:
        raise AssertionError(f"slot {field_id!r} has no box")
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    # 4 px is past DRAG_THRESHOLD_PX (3) and still inside the slot box, so the
    # session becomes a drag and captures the pointer before it travels far.
    page.mouse.move(cx + 4, cy + 4)
    page.mouse.move(cx + dx_px, cy + dy_px, steps=steps)
    page.mouse.up()
    page.wait_for_timeout(120)


def dblclick_slot(page, field_id: str) -> None:
    """Double-click the mark a user would actually aim at.

    A signed slot is covered by its own `.placed-signature`, which carries the
    same drag/reset binding, so that is the real double-click target.
    """
    image = page.locator(f'img.placed-signature[data-field-id="{field_id}"]')
    target = image if image.count() > 0 else page.locator(
        f'.pdf-page .slot[data-field-id="{field_id}"]'
    )
    target.first.dblclick()
    page.wait_for_timeout(200)


def press_without_dragging(page, field_id: str, travel_px: float = 2.0) -> None:
    handle = page.locator(f'.pdf-page .slot[data-field-id="{field_id}"]')
    handle.scroll_into_view_if_needed()
    box = handle.bounding_box()
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + travel_px, cy)
    page.mouse.up()
    page.wait_for_timeout(120)


def arm_tool(page, name: str) -> None:
    """Arm a tool by its toolbar button, wherever it lives (W4).

    A tool that shares a drop-down (`data-menu`, e.g. the markup tools and the
    shapes) is inside a closed list; the list is opened with its ▾ first. A
    plain tool is one click, exactly as before.
    """
    button = page.locator(f'[data-testid="tool-{name}"]')
    button.wait_for(state="attached", timeout=20000)
    if not button.is_visible():
        menu = button.get_attribute("data-menu")
        if menu:
            page.locator(f'[data-testid="toolgroup-{menu}-toggle"]').click()
            button.wait_for(state="visible", timeout=5000)
    button.click()


def confirms(page) -> list[str]:
    return page.evaluate("() => window.__swmConfirms || []")


def set_confirm_result(page, value: bool) -> None:
    page.evaluate("(v) => { window.__swmConfirmResult = v; }", value)


# --------------------------------------------------------------------------
# Assertions
# --------------------------------------------------------------------------

def approx(actual: float | None, expected: float, tol: float = 0.05, what: str = "value") -> None:
    if actual is None:
        raise AssertionError(f"{what}: expected ~{expected}, got None")
    if abs(actual - expected) > tol:
        raise AssertionError(f"{what}: expected {expected} +/- {tol}, got {actual}")


def equals(actual: Any, expected: Any, what: str = "value") -> None:
    if actual != expected:
        raise AssertionError(f"{what}: expected {expected!r}, got {actual!r}")


def contains(haystack: str, needle: str, what: str = "text") -> None:
    if needle not in (haystack or ""):
        raise AssertionError(f"{what}: {needle!r} not found in {haystack!r}")


def missing(haystack: str, needle: str, what: str = "text") -> None:
    if needle in (haystack or ""):
        raise AssertionError(f"{what}: {needle!r} unexpectedly present in {haystack!r}")


def truthy(value: Any, what: str = "condition") -> None:
    if not value:
        raise AssertionError(f"{what}: expected something truthy, got {value!r}")


# --------------------------------------------------------------------------
# Tiny test registry / runner (no pytest, no npm)
# --------------------------------------------------------------------------

class Registry:
    def __init__(self, suite: str) -> None:
        self.suite = suite
        self.tests: list[tuple[str, str, Callable]] = []

    def __call__(self, name: str, description: str = "") -> Callable:
        def decorate(fn: Callable) -> Callable:
            self.tests.append((name, description or (fn.__doc__ or "").strip(), fn))
            return fn
        return decorate


def run_registry(
    registry: Registry,
    *,
    headed: bool = False,
    slow_mo: int = 0,
    only: str | None = None,
    shots: str | None = None,
    quiet: bool = False,
) -> tuple[int, list[tuple[str, str]]]:
    """Run every registered check in its own browser context. Returns (passed, failures)."""
    from playwright.sync_api import sync_playwright

    selected = [t for t in registry.tests if not only or only in t[0]]
    passed = 0
    failures: list[tuple[str, str]] = []

    if not quiet:
        print(f"\n== {registry.suite} == ({len(selected)} checks, target {BASE})")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed, slow_mo=slow_mo)
        try:
            for name, description, fn in selected:
                context = browser.new_context(
                    viewport={"width": 1600, "height": 1200},
                    device_scale_factor=1,
                )
                page = context.new_page()
                page.set_default_timeout(20000)
                mock = MockApi()
                mock.install(page)
                started = time.time()
                try:
                    fn(page, mock)
                    passed += 1
                    if not quiet:
                        print(f"  PASS  {name}  ({time.time() - started:.1f}s)")
                except Exception as exc:  # noqa: BLE001 - the report is the point
                    detail = f"{type(exc).__name__}: {exc}".strip()
                    failures.append((name, detail))
                    print(f"  FAIL  {name}  ({time.time() - started:.1f}s)")
                    print(f"        {detail.splitlines()[0] if detail else ''}")
                    if os.environ.get("SWM_UI_TRACE"):
                        traceback.print_exc()
                    if shots:
                        os.makedirs(shots, exist_ok=True)
                        try:
                            page.screenshot(path=os.path.join(shots, f"{name}.png"), full_page=True)
                        except Exception:
                            pass
                finally:
                    context.close()
        finally:
            browser.close()

    return passed, failures


def cli_main(registry: Registry) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=f"SignWithMe UI checks: {registry.suite}")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--slow-mo", type=int, default=0, help="ms between actions")
    parser.add_argument("--only", help="run only checks whose name contains this")
    parser.add_argument("--shots", help="directory for a screenshot of every failed check")
    args = parser.parse_args()

    passed, failures = run_registry(
        registry,
        headed=args.headed,
        slow_mo=args.slow_mo,
        only=args.only,
        shots=args.shots,
    )
    total = passed + len(failures)
    print(f"\n{registry.suite}: {passed}/{total} passed")
    for name, detail in failures:
        print(f"  - {name}: {detail}")
    return 0 if not failures else 1


def ensure_target_is_up() -> None:
    """Fail loudly and early if the dev server is not answering."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(BASE, timeout=5) as response:
            if response.status >= 400:
                raise RuntimeError(f"{BASE} answered {response.status}")
    except Exception as exc:  # noqa: BLE001
        print(
            f"Cannot reach the frontend at {BASE} ({exc}).\n"
            "Start the Vite dev server first, or set SWM_UI_BASE.",
            file=sys.stderr,
        )
        raise SystemExit(2)
