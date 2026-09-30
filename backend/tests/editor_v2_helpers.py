"""Shared helpers for the PDF editor v2 tests (B1)."""
from __future__ import annotations

import io
import uuid

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from tests.conftest import auth

PAGE_UID = "pg_000000000001"


def reportlab_pdf(draw, pagesize=letter, pages: int = 1) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=pagesize)
    for index in range(pages):
        draw(pdf, index)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def chrome_style_pdf() -> bytes:
    """Text positioned the way Chrome/Skia (Docusign) writes it: `(A) Tj 0 0 Td
    (B) Tj` -- the zero Td resets the text matrix to the line start. MuPDF's
    content filter drops it as a "no-op" (spike §10.2.1)."""

    def draw(pdf, _index):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 750, "Header line to delete")
        pdf.setFillColorRGB(0.8, 0.9, 1.0)
        pdf.rect(60, 300, 300, 30, fill=1, stroke=0)
        pdf.setFillColorRGB(0, 0, 0)
        for row in range(20):
            y = 700 - row * 20
            pdf._code.append(f"BT /F1 12 Tf 72 {y} Td (AAAA) Tj 0 0 Td (BBBB) Tj 0 0 Td 30 0 Td (CC) Tj ET")

    return reportlab_pdf(draw)


def render_gray(pdf_bytes: bytes, index: int = 0, scale: float = 2.0):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[index]
        try:
            bitmap = page.render(scale=scale)
            try:
                return bitmap.to_pil().convert("L")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def pixel_diff(a, b, exclude_pt=(), scale: float = 2.0, threshold: int = 48) -> int:
    """Pixels that differ by more than `threshold` outside the excluded
    top-left-origin point rectangles (x0, y0, x1, y1)."""
    assert a.size == b.size
    width, height = a.size
    pa, pb = a.tobytes(), b.tobytes()
    excluded = [
        (int(x0 * scale) - 1, int(y0 * scale) - 1, int(x1 * scale) + 2, int(y1 * scale) + 2)
        for x0, y0, x1, y1 in exclude_pt
    ]
    count = 0
    for y in range(height):
        row = y * width
        for x in range(width):
            if abs(pa[row + x] - pb[row + x]) <= threshold:
                continue
            if any(ex0 <= x <= ex1 and ey0 <= y <= ey1 for ex0, ey0, ex1, ey1 in excluded):
                continue
            count += 1
    return count


def plumber_text(pdf_bytes: bytes, index: int = 0) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return pdf.pages[index].extract_text() or ""


def pypdf_text(pdf_bytes: bytes, index: int = 0) -> str:
    from pypdf import PdfReader

    return PdfReader(io.BytesIO(pdf_bytes)).pages[index].extract_text() or ""


def pdfium_text(pdf_bytes: bytes, index: int = 0) -> str:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[index]
        textpage = page.get_textpage()
        try:
            return textpage.get_text_range()
        finally:
            textpage.close()
            page.close()
    finally:
        document.close()


def all_extractors(pdf_bytes: bytes, index: int = 0) -> list[str]:
    return [plumber_text(pdf_bytes, index), pypdf_text(pdf_bytes, index), pdfium_text(pdf_bytes, index)]


def compact(text: str) -> str:
    return "".join(text.split())


# -- API helpers ------------------------------------------------------------------


def doc_url(contract_id: str, suffix: str = "") -> str:
    return f"/api/contracts/{contract_id}/document{suffix}"


def get_document(api, token, contract_id):
    response = api.get(doc_url(contract_id), headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def first_page_uid(api, token, contract_id, version_no: int = 1) -> str:
    response = api.get(doc_url(contract_id, f"/versions/{version_no}/pages"), headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()[0]["page_uid"]


def text_model(api, token, contract_id, page_uid, version_no: int = 1) -> dict:
    response = api.get(doc_url(contract_id, f"/versions/{version_no}/pages/{page_uid}/text"), headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def find_line(model: dict, text: str) -> tuple[dict, dict]:
    for block in model["blocks"]:
        for line in block["lines"]:
            if line["text"] == text:
                return block, line
    raise AssertionError(f"line {text!r} not in text model")


def replace_op(model: dict, text: str, new_text: str, *, op_id="op1", page_uid=None, size=11.0, **extra) -> dict:
    block, line = find_line(model, text)
    op = {
        "id": op_id,
        "kind": "text.replace",
        "page_uid": page_uid,
        "target": {
            "block_id": block["id"],
            "line_ids": [line["id"]],
            "expect_text": text,
            "bbox": line["bbox"],
        },
        "runs": [{"text": new_text, "font": "LiberationSans", "size_pt": size, "color": "#000000"}],
    }
    op.update(extra)
    return op


def delete_op(model: dict, text: str, *, op_id="del1", page_uid=None) -> dict:
    block, line = find_line(model, text)
    return {
        "id": op_id,
        "kind": "text.delete",
        "page_uid": page_uid,
        "target": {
            "block_id": block["id"],
            "line_ids": [line["id"]],
            "expect_text": text,
            "bbox": line["bbox"],
        },
    }


def session_id() -> str:
    return "sess_" + uuid.uuid4().hex[:12]


def start_draft(api, token, contract_id, session: str, base_version_no: int = 1, **extra):
    return api.post(
        doc_url(contract_id, "/draft"),
        headers=auth(token),
        json={"base_version_no": base_version_no, "client_session_id": session, **extra},
    )


def put_draft(api, token, contract_id, session: str, revision: int, ops, markup=None):
    body = {"revision": revision, "client_session_id": session, "ops": ops}
    if markup is not None:
        body["markup"] = markup
    return api.put(doc_url(contract_id, "/draft"), headers=auth(token), json=body)


def commit(api, token, contract_id, revision: int, request_id: str | None = None, **extra):
    return api.post(
        doc_url(contract_id, "/draft/commit"),
        headers=auth(token),
        json={"revision": revision, "commit_request_id": request_id or ("commit_" + uuid.uuid4().hex[:16]), **extra},
    )


def edit_and_commit(api, token, contract_id, text: str, new_text: str, **commit_extra):
    """Start a draft, replace one line, commit. Returns the commit response."""
    uid = first_page_uid(api, token, contract_id)
    model = text_model(api, token, contract_id, uid)
    session = session_id()
    started = start_draft(api, token, contract_id, session)
    assert started.status_code in (200, 201), started.text
    revision = started.json()["revision"]
    saved = put_draft(
        api, token, contract_id, session, revision,
        [replace_op(model, text, new_text, page_uid=uid, box=_wide(find_line(model, text)[1]["bbox"]))],
    )
    assert saved.status_code == 200, saved.text
    return commit(api, token, contract_id, saved.json()["revision"], **commit_extra)


def _wide(bbox: dict) -> dict:
    return {**bbox, "w_pt": bbox["w_pt"] + 200}


def db_session(api):
    return api.db_factory()
