"""Synthetic ZZSYNTH PDFs and the PDF evidence helpers (backend venv:
reportlab, pypdf, pdfplumber, PyMuPDF, pypdfium2, Pillow).

Geometry everywhere: PDF points, TOP-LEFT origin (the editor's space).
Renders are pypdfium2 at S px/pt, so a pixel box / S is in points."""
from __future__ import annotations

import base64
import io
import math

import pdfplumber
import pymupdf
import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader

S = 8.0  # px per pt for measurements
PAGE_W, PAGE_H = 612.0, 792.0

# -- the synthetic document ----------------------------------------------------------
# baseline (top-left y) of every line on page 1; the tests look them up by text.
P1_LINES = {
    "title": (72, 60, "ZZSYNTH ACCEPTANCE AGREEMENT (synthetic)"),
    "carrier": (72, 100, "Carrier: ABC Transportation LLC"),          # 1 edit (in a light-blue cell)
    "obsolete": (72, 124, "Obsolete clause ZZOBSOLETE-7788 removed."),  # 2 delete
    "style": (72, 148, "Style target line ZZSTYLE"),                   # 4 font/size/colour
    "secret": (72, 172, "Account: ZZSECRET-9876-REDACT-ME"),          # 10 redact
    "white": (72, 196, "Whiteout target ZZWHITE"),                     # 10 whiteout
    "visible": (72, 220, "Visible clause stays here."),
    "link": (72, 244, "Website link area"),                             # 8 link
    "highlight": (72, 268, "Highlight this sentence ZZHL please."),     # 9 highlight
    "name": (72, 292, "Name: ____________________"),                    # neighbour of edits
    "move": (72, 316, "Movable line ZZMOVE"),                           # 5 move / duplicate
}
CELL = (66, 86, 300, 20)  # light-blue cell behind the carrier line (x, y, w, h), top-left
IMAGE_BOX = (430, 90, 80, 40)  # the original raster image (6 image.remove)
CELL_RGB = (217, 237, 255)


def _sig_block(pdf, y_rl):
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, y_rl + 12, "I certify that the information provided above is true and complete.")
    pdf.drawString(72, y_rl, "Contractor Signature:")
    pdf.drawString(200, y_rl, "_" * 25)
    pdf.drawString(400, y_rl, "Date:")
    pdf.drawString(440, y_rl, "__/__/____")


def _raster() -> Image.Image:
    img = Image.new("RGB", (40, 20), (0, 160, 0))
    for x in range(20, 40):
        for y in range(20):
            img.putpixel((x, y), (160, 0, 160))
    return img


def make_contract(copy_label: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=letter)
    pdf.setTitle("ZZSYNTH acceptance")
    # page 1
    x, y, w, h = CELL
    pdf.setFillColorRGB(*(c / 255 for c in CELL_RGB))
    pdf.rect(x, PAGE_H - y - h, w, h, stroke=0, fill=1)
    pdf.setFillColorRGB(0, 0, 0)
    for key, (lx, ly, text) in P1_LINES.items():
        pdf.setFont("Helvetica-Bold" if key == "title" else "Helvetica", 14 if key == "title" else 12)
        pdf.drawString(lx, PAGE_H - ly, text)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(72, PAGE_H - 340, f"Copy: {copy_label}")
    ix, iy, iw, ih = IMAGE_BOX
    pdf.drawImage(ImageReader(_raster()), ix, PAGE_H - iy - ih, width=iw, height=ih)
    _sig_block(pdf, 200)
    pdf.showPage()
    # page 2 (deleted in 12)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, PAGE_H - 60, "ZZSYNTH page two -- body text, no slots.")
    pdf.drawString(72, PAGE_H - 84, "Second paragraph on page two ZZPAGE2.")
    pdf.showPage()
    # page 3 (slot page)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, PAGE_H - 60, "ZZSYNTH page three -- slot page ZZPAGE3.")
    _sig_block(pdf, 300)
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


# -- evidence ------------------------------------------------------------------------------

def texts(data: bytes) -> dict[str, list[str]]:
    """Per-page text by three independent extractors."""
    """A reader that CRASHES on a page yields `<ERROR ...>` for it (and the
    error is kept in `READER_ERRORS`), so a check fails instead of the test."""
    out: dict[str, list[str]] = {}

    def safe(fn):
        try:
            return fn() or ""
        except Exception as exc:  # noqa: BLE001
            msg = f"<ERROR {type(exc).__name__}: {str(exc)[:80]}>"
            READER_ERRORS.append(msg)
            return msg

    out["pypdf"] = [safe(p.extract_text) for p in PdfReader(io.BytesIO(data)).pages]
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        out["pdfplumber"] = [safe(p.extract_text) for p in pdf.pages]
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        out["pymupdf"] = [safe(p.get_text) for p in doc]
    return out


READER_ERRORS: list[str] = []


def all_have(data: bytes, needle: str, page: int | None = None) -> dict[str, bool]:
    t = texts(data)
    norm = lambda s: " ".join(s.split())  # noqa: E731
    return {k: (norm(needle) in norm(v[page] if page is not None else " ".join(v))) for k, v in t.items()}


def none_have(data: bytes, needle: str, page: int | None = None) -> dict[str, bool]:
    return {k: not v for k, v in all_have(data, needle, page).items()}


def stream_bytes(data: bytes) -> bytes:
    raw = [data]
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for xref in range(1, doc.xref_length()):
            try:
                if doc.xref_is_stream(xref):
                    raw.append(doc.xref_stream(xref) or b"")
            except Exception:  # noqa: BLE001
                pass
    return b"\n".join(raw)


def render(data: bytes, scale: float = S, annots: bool = False) -> list[Image.Image]:
    doc = pdfium.PdfDocument(data)
    if annots:
        doc.init_forms()
    try:
        return [doc[i].render(scale=scale, draw_annots=annots, may_draw_forms=annots).to_pil().convert("RGB") for i in range(len(doc))]
    finally:
        doc.close()


def region_px(region, scale=S):
    x0, y0, x1, y1 = region
    return (max(0, math.floor(x0 * scale)), max(0, math.floor(y0 * scale)),
            math.ceil(x1 * scale), math.ceil(y1 * scale))


def ink_box(img: Image.Image, region, pred, scale=S):
    """Bounding box (pt) of pixels matching `pred` inside `region` (pt), and their count."""
    bx = region_px(region, scale)
    bx = (bx[0], bx[1], min(img.size[0], bx[2]), min(img.size[1], bx[3]))
    crop = img.crop(bx)
    w, h = crop.size
    px = crop.load()
    xs, ys = [], []
    for yy in range(h):
        for xx in range(w):
            if pred(px[xx, yy]):
                xs.append(xx)
                ys.append(yy)
    if not xs:
        return None, 0
    return [round((bx[0] + min(xs)) / scale, 3), round((bx[1] + min(ys)) / scale, 3),
            round((bx[0] + max(xs) + 1) / scale, 3), round((bx[1] + max(ys) + 1) / scale, 3)], len(xs)


def rule_top(img: Image.Image, x0, x1, y0, y1, frac=0.6, scale=S):
    """Top (pt) of the LOWEST horizontal dark band in [y0, y1] whose rows are
    at least `frac` dark across [x0, x1] -- a fill line (a ____ run or a rule)."""
    bx = region_px((x0, y0, x1, y1), scale)
    crop = img.crop(bx).convert("L")
    w, h = crop.size
    px = crop.load()
    rows = [sum(1 for xx in range(w) if px[xx, yy] < 128) / max(w, 1) >= frac for yy in range(h)]
    y = h - 1
    while y >= 0 and not rows[y]:
        y -= 1
    if y < 0:
        return None
    while y - 1 >= 0 and rows[y - 1]:
        y -= 1
    return round((bx[1] + y) / scale, 3)


def diff_outside(a: Image.Image, b: Image.Image, allowed: list[tuple], thr: int = 48) -> int:
    """Pixels that differ by more than `thr` OUTSIDE every allowed region (pt)."""
    if a.size != b.size:
        return -1
    d = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > thr else 0)
    mask = Image.new("L", a.size, 255)
    draw = ImageDraw.Draw(mask)
    for region in allowed:
        draw.rectangle(region_px(region), fill=0)
    d = ImageChops.multiply(d, mask)
    return sum(1 for v in d.getdata() if v)


def diff_box(a: Image.Image, b: Image.Image, region, thr: int = 48, scale=S):
    """Bounding box (pt) of the pixels that differ between two renders inside `region`."""
    bx = region_px(region, scale)
    bx = (bx[0], bx[1], min(a.size[0], bx[2]), min(a.size[1], bx[3]))
    d = ImageChops.difference(a.crop(bx), b.crop(bx)).convert("L").point(lambda v: 255 if v > thr else 0)
    box = d.getbbox()
    if not box:
        return None, 0
    n = sum(1 for v in d.getdata() if v)
    return [round((bx[0] + box[0]) / scale, 3), round((bx[1] + box[1]) / scale, 3),
            round((bx[0] + box[2]) / scale, 3), round((bx[1] + box[3]) / scale, 3)], n


def diff_inside(a: Image.Image, b: Image.Image, region, thr: int = 48) -> int:
    bx = region_px(region)
    d = ImageChops.difference(a.crop(bx), b.crop(bx)).convert("L")
    return sum(1 for v in d.getdata() if v > thr)


def white_px(img: Image.Image, region) -> int:
    crop = img.crop(region_px(region))
    return sum(1 for p in crop.getdata() if min(p) >= 250)


is_dark = lambda p: max(p) < 110  # noqa: E731
is_black = lambda p: max(p) < 60  # noqa: E731
is_red = lambda p: p[0] > 150 and p[0] - p[1] > 70 and p[0] - p[2] > 70  # noqa: E731
is_blue = lambda p: p[2] > 120 and p[2] - p[0] > 60 and p[2] - p[1] > 40  # noqa: E731
is_green = lambda p: p[1] > 90 and p[1] - p[0] > 50 and p[1] - p[2] > 20  # noqa: E731
is_yellowish = lambda p: p[0] > 200 and p[1] > 200 and p[2] < 170  # noqa: E731
is_cyan = lambda p: p[1] > 150 and p[2] > 150 and p[0] < 90  # noqa: E731
is_magenta = lambda p: p[0] > 120 and p[2] > 120 and p[1] < 60  # noqa: E731


def spans(data: bytes, page: int, needle: str) -> list[dict]:
    """PyMuPDF spans whose text contains `needle`: font, size, color hex, origin, bbox (top-left pt)."""
    out = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        d = doc[page].get_text("dict")
        for b in d["blocks"]:
            for ln in b.get("lines", []):
                for sp in ln["spans"]:
                    if needle in sp["text"]:
                        out.append({"text": sp["text"], "font": sp["font"], "size": round(sp["size"], 2),
                                    "color": f"#{sp['color']:06X}", "origin": [round(v, 3) for v in sp["origin"]],
                                    "bbox": [round(v, 3) for v in sp["bbox"]], "flags": sp["flags"]})
    return out


def line_geometry(data: bytes, page: int, needle: str) -> dict | None:
    """pdfplumber + PyMuPDF: left edge and baseline of the first char of the line holding `needle`."""
    res = {}
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for b in doc[page].get_text("rawdict")["blocks"]:
            for ln in b.get("lines", []):
                text = "".join(c["c"] for sp in ln["spans"] for c in sp["chars"])
                if needle in text:
                    first = ln["spans"][0]["chars"][0]
                    res["pymupdf"] = {"x0": round(first["bbox"][0], 3), "baseline": round(first["origin"][1], 3)}
                    break
            if "pymupdf" in res:
                break
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pg = pdf.pages[page]
        for line in pg.extract_text_lines():
            if needle in line["text"]:
                ch = line["chars"][0]
                # the baseline is the text matrix's translation (bottom-left y) -> top-left
                m = ch.get("matrix")
                res["pdfplumber"] = {"x0": round(ch["x0"], 3),
                                     "baseline": round(pg.height - m[5], 3) if m else None}
                break
    return res or None


def image_xobjects(data: bytes) -> int:
    reader = PdfReader(io.BytesIO(data))
    total = 0

    def walk(res, seen):
        nonlocal total
        xo = (res or {}).get("/XObject")
        if not xo:
            return
        xo = xo.get_object()
        for _n, ref in xo.items():
            key = getattr(ref, "idnum", id(ref))
            if key in seen:
                continue
            seen.add(key)
            obj = ref.get_object()
            if obj.get("/Subtype") == "/Image":
                total += 1
            elif obj.get("/Subtype") == "/Form":
                walk(obj.get("/Resources"), seen)

    for page in reader.pages:
        walk(page.get("/Resources"), set())
    return total


def image_placements(data: bytes) -> int:
    """How many times an image is DRAWN (PyMuPDF image info, every page). Unlike
    the XObject count this is not fooled by identical images merged into one
    object on save (garbage collection de-duplicates equal streams)."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return sum(len(page.get_image_info()) for page in doc)


def open_everywhere(data: bytes) -> dict:
    """Opens + page count in pypdf, pdfplumber, PyMuPDF, pypdfium2 (render)."""
    out = {}
    try:
        out["pypdf"] = len(PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:  # noqa: BLE001
        out["pypdf"] = f"ERR {exc!r}"[:200]
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            out["pdfplumber"] = len(pdf.pages)
    except Exception as exc:  # noqa: BLE001
        out["pdfplumber"] = f"ERR {exc!r}"[:200]
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            out["pymupdf"] = doc.page_count
            out["pymupdf_repaired"] = bool(getattr(doc, "is_repaired", False))
    except Exception as exc:  # noqa: BLE001
        out["pymupdf"] = f"ERR {exc!r}"[:200]
    start = len(READER_ERRORS)
    texts(data)
    out["text_errors"] = READER_ERRORS[start:][:5]
    try:
        imgs = render(data, scale=1.0)
        out["pdfium_rendered"] = len(imgs)
        out["pdfium_nonblank"] = sum(1 for im in imgs if im.convert("L").getextrema()[0] < 200)
    except Exception as exc:  # noqa: BLE001
        out["pdfium_rendered"] = f"ERR {exc!r}"[:200]
    return out


def png_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def blue_signature() -> str:
    img = Image.new("RGBA", (300, 90), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(10 + i * 4, 45 + 25 * math.sin(i / 5.0)) for i in range(70)]
    d.line(pts, fill=(20, 40, 210, 255), width=6)
    return png_data_url(img)


def solid_png_data_url(rgb, w, h) -> str:
    return png_data_url(Image.new("RGB", (w, h), rgb))


def cyan_picture(w=100, h=50) -> str:
    return png_data_url(Image.new("RGB", (w, h), (0, 200, 200)))


def magenta_png_bytes(w=60, h=30) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 0, 200)).save(buf, format="PNG")
    return buf.getvalue()


def save_png(img: Image.Image, path: str, region=None, scale=S, shrink=2):
    if region is not None:
        img = img.crop(region_px(region, scale))
    img.resize((max(1, img.size[0] // shrink), max(1, img.size[1] // shrink))).save(path)
