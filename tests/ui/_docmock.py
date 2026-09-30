"""
F1b: the editor v2 document endpoints (`/api/contracts/{id}/document/*`), mocked.

`MockApi.document` is `None` by default -- the app then meets a server
WITHOUT the document editor (every `/document*` call answers 404, the way an
older backend does), which is what every check written before F1b expects:
the legacy `/file` viewer, annotations in this browser only.

A check that wants the editor installs a `DocMock` (`enable_document`). It
keeps a small, honest model of B1's behaviour (EDITOR-V2-PLAN §6):
versions with their pages and bytes, the markup layer with its revision,
ONE draft with a revision and a lease owner, previews, the atomic commit
(409 `signatures_present` unless `discard_signatures`), plus a queue of
forced answers (`force`) for the conflict / offline / 422 paths.

The PDFs are generated here, byte for byte (Helvetica / Times / Courier,
AFM widths), and so is the SERVER's text model of them (one block per line,
char boxes from the same widths): the editor sees exactly the geometry the
page shows. A preview is the page drawn again with the draft's ops applied
(and the hidden block left out) -- what the real engine does, in spirit.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qs

PAGE_W = 612.0
PAGE_H = 792.0

# AFM advance widths (1/1000 em), ASCII 32..126 -- same table as test_edit_text.
_W = {
    "Helvetica": [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584],
    "Times-Roman": [250, 333, 408, 500, 500, 833, 778, 180, 333, 333, 500, 564, 250, 333, 250, 278, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 278, 278, 564, 564, 564, 444, 921, 722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889, 722, 722, 556, 722, 667, 556, 611, 722, 722, 944, 722, 722, 611, 333, 278, 333, 469, 500, 333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500, 278, 778, 500, 500, 500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541],
    "Courier": [600] * 95,
}
_RES = {"Helvetica": "F1", "Times-Roman": "F2", "Courier": "F3"}
_FAMILY = {"Helvetica": "LiberationSans", "Times-Roman": "LiberationSerif", "Courier": "LiberationMono"}
ASCENT = 0.905
DESCENT = 0.212


def char_width(c: str, font: str, size: float) -> float:
    code = ord(c)
    table = _W[font]
    return (table[code - 32] if 32 <= code < 127 else 556) * size / 1000.0


def text_width(text: str, font: str, size: float) -> float:
    return sum(char_width(c, font, size) for c in text)


class Line:
    """One line of the synthetic page = one block of the server's model."""

    def __init__(self, text: str, x: float, baseline: float, font: str = "Helvetica", size: float = 12,
                 color: str = "#000000", background: tuple[float, float, float] | None = None,
                 editable: bool = True, reason: str | None = None) -> None:
        self.text = text
        self.x = x
        self.baseline = baseline
        self.font = font
        self.size = size
        self.color = color
        self.background = background
        self.editable = editable
        self.reason = reason


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _rgb(hex_color: str) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return f"{r:.3f} {g:.3f} {b:.3f}"


def page_content(lines: list[Line]) -> str:
    parts: list[str] = []
    for line in lines:
        if line.background:
            r, g, b = line.background
            w = text_width(line.text, line.font, line.size) + 60
            y = PAGE_H - line.baseline - line.size * 0.5
            parts.append(f"{r} {g} {b} rg {line.x - 6:.2f} {y:.2f} {w:.2f} {line.size * 1.6:.2f} re f")
    for line in lines:
        if not line.text:
            continue
        parts.append(
            f"BT {_rgb(line.color)} rg /{_RES[line.font]} {line.size} Tf 1 0 0 1 {line.x:.3f} "
            f"{PAGE_H - line.baseline:.3f} Tm ({_esc(line.text)}) Tj ET"
        )
    return " ".join(parts) or "% empty"


def build_pdf(pages: list[list[Line]], rotations: list[int] | None = None) -> bytes:
    fonts = " ".join(f"/{res} {3 + i} 0 R" for i, res in enumerate(_RES.values()))
    objs: list[str] = ["", ""]
    for name in _RES:
        objs.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>")
    first = len(objs) + 1
    kids = []
    for index, lines in enumerate(pages):
        content = page_content(lines)
        page_obj = first + index * 2
        kids.append(f"{page_obj} 0 R")
        rotate = f"/Rotate {rotations[index]} " if rotations and rotations[index] else ""
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] {rotate}"
            f"/Contents {page_obj + 1} 0 R /Resources << /Font << {fonts} >> >> >>"
        )
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objs[0] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    pdf = "%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objs):
        offsets.append(len(pdf))
        pdf += f"{index + 1} 0 obj\n{obj}\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n"
    pdf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return pdf.encode("latin-1", "replace")


def text_model(uid: str, version_no: int, file_hash: str, lines: list[Line]) -> dict[str, Any]:
    blocks = []
    for index, line in enumerate(line for line in lines if line.text):
        chars = []
        cursor = line.x
        for c in line.text:
            w = char_width(c, line.font, line.size)
            chars.append([c, round(cursor, 3), round(line.baseline - ASCENT * line.size, 3),
                          round(cursor + w, 3), round(line.baseline + DESCENT * line.size, 3)])
            cursor += w
        bbox = {"x_pt": line.x, "y_pt": round(line.baseline - ASCENT * line.size, 3),
                "w_pt": round(cursor - line.x, 3), "h_pt": round((ASCENT + DESCENT) * line.size, 3)}
        blocks.append({
            "id": f"b{index}",
            "bbox": bbox,
            "editable": line.editable,
            "reason": line.reason,
            "lines": [{
                "id": f"b{index}l0",
                "bbox": bbox,
                "text": line.text,
                "spans": [{
                    "font": line.font,
                    "family": _FAMILY[line.font],
                    "size_pt": line.size,
                    "color": line.color,
                    "bold": False,
                    "italic": False,
                    "baseline": line.baseline,
                    "chars": chars,
                }],
            }],
        })
    return {"page_uid": uid, "version_no": version_no, "file_hash": file_hash, "width_pt": PAGE_W,
            "height_pt": PAGE_H, "rotation": 0, "has_text": bool(blocks), "scanned": False, "blocks": blocks}


def apply_ops(lines: list[Line], ops: list[dict], uid: str, hide: str | None = None) -> list[Line]:
    """The page's lines with the ops of `uid` applied (block `bN` = N-th non-empty line)."""
    visible = [line for line in lines if line.text]
    out: list[Line] = []
    # F2: the list also carries page / link / redact / image ops (no `target`).
    by_block = {op["target"]["block_id"]: op for op in ops
                if op.get("page_uid") == uid and str(op.get("kind", "")).startswith("text.")}
    for index, line in enumerate(visible):
        block_id = f"b{index}"
        if block_id == hide:
            continue
        op = by_block.get(block_id)
        if op is None:
            out.append(line)
            continue
        if op["kind"] == "text.delete":
            continue
        text = "".join(run["text"] for run in op["runs"]).replace("\n", " ")
        first = op["runs"][0]
        font = {"LiberationSans": "Helvetica", "LiberationSerif": "Times-Roman", "LiberationMono": "Courier"}[first["font"]]
        box = op.get("box")
        dy = (box["y_pt"] - line.baseline + ASCENT * line.size) if box else 0.0
        x = box["x_pt"] if box else line.x
        out.append(Line(text, x, line.baseline + dy, font, first["size_pt"], first["color"], line.background))
    return out


def _now_plus(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class DocMock:
    """The document of one contract, as B1 keeps it."""

    def __init__(self, pages: list[list[Line]], *, can_edit: bool = True, reason: str | None = None) -> None:
        self.pages = pages  # version 1's lines per page
        self.uids = [f"pg_{index + 1:012x}" for index in range(len(pages))]
        self.versions: dict[int, dict[str, Any]] = {}
        self._add_version(1, pages, content_changed=False)
        self.current = 1
        self.markup: list[dict] = []
        self.markup_revision = 0
        self.draft: dict[str, Any] | None = None
        self.capabilities = {"can_edit": can_edit, "can_edit_markup": True, "reason": reason}
        # Forced answers: key "METHOD /sub" (e.g. "PUT /draft") -> [(status, body), ...] (popped).
        self.force: dict[str, list[tuple[int, Any]]] = {}
        # Network failures: key -> count of aborts still to do.
        self.abort: dict[str, int] = {}
        self.review_reset_on_commit = True
        # Recorded traffic.
        self.draft_posts: list[Any] = []
        self.draft_puts: list[Any] = []
        self.draft_deletes: list[str] = []
        self.lease_posts: list[Any] = []
        self.commit_bodies: list[Any] = []
        self.markup_puts: list[Any] = []
        self.previews: list[str] = []
        self.text_gets: list[str] = []
        self.warnings_for_put: Callable[[list[dict]], list[dict]] | None = None
        # F2 (B2): the pictures and links of a page (by uid), exports, the page-op traffic.
        self.images: dict[str, list[dict]] = {}
        self.links: dict[str, list[dict]] = {}
        self.exports: dict[str, dict] = {}
        self.export_bodies: list[Any] = []
        self.draft_pdf_gets: list[str] = []
        # 403 `page_has_slots` for a page.delete of these uids (the uploader's refusal).
        self.slot_page_uids: set[str] = set()
        # B1.1 shapes: rewrite the served text model (mixed-font lines, synthetic spans).
        self.model_hook: Callable[[dict, int], dict] | None = None

    # -- versions -----------------------------------------------------------

    def _add_version(self, number: int, pages: list[list[Line]], *, content_changed: bool,
                     uids: list[str] | None = None, rotations: list[int] | None = None) -> None:
        data = build_pdf(pages, rotations)
        self.versions[number] = {
            "pages": pages,
            "uids": list(uids) if uids is not None else [f"pg_{index + 1:012x}" for index in range(len(pages))],
            "rotations": list(rotations) if rotations else [0] * len(pages),
            "bytes": data,
            "hash": hashlib.sha256(data).hexdigest(),
            "content_changed": content_changed,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def version_out(self, number: int) -> dict[str, Any]:
        version = self.versions[number]
        return {
            "version_no": number,
            "kind": "original" if number == 1 else "edit",
            "file_hash": version["hash"],
            "page_count": len(version["pages"]),
            "content_changed": version["content_changed"],
            "review_reset": False,
            "created_by_name": "ZZ UI Runner",
            "created_at": version["created_at"],
            "op_summary": {},
        }

    def pdf_v1(self) -> bytes:
        return self.versions[1]["bytes"]

    # -- the router ---------------------------------------------------------

    def _json(self, route, status: int, body: Any) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    def _err(self, route, status: int, code: str, message: str, details: dict | None = None) -> None:
        self._json(route, status, {"error": code, "message": message, "details": details or {}})

    def handle(self, api, route, method: str, sub: str, query: str, body_json: Callable[[], Any]) -> None:
        shape = re.sub(r"/pg_[0-9a-f]{12}", "/{uid}", re.sub(r"/versions/\d+", "/versions/{n}", sub))
        key = method + " " + shape
        if self.abort.get(key, 0) > 0:
            self.abort[key] -= 1
            if method == "PUT" and sub == "/draft":
                self.draft_puts.append({"aborted": True, **(body_json() or {})})
            return route.abort("failed")
        forced = self.force.get(key)
        if forced:
            status, body = forced.pop(0)
            if method == "PUT" and sub == "/draft":
                self.draft_puts.append({"forced": status, **(body_json() or {})})
            if method == "POST" and sub == "/draft/commit":
                self.commit_bodies.append({"forced": status, **(body_json() or {})})
            return self._json(route, status, body)
        params = {k: v[0] for k, v in parse_qs(query).items()}

        if sub == "" and method == "GET":
            draft = None
            if self.draft:
                draft = {"owner_name": self.draft["owner_name"], "is_mine": self.draft["is_mine"],
                         "lease_expires_at": self.draft["lease"], "revision": self.draft["revision"]}
            return self._json(route, 200, {
                "document_id": "zz-doc-1",
                "current_version": self.version_out(self.current),
                "versions_count": len(self.versions),
                "markup_revision": self.markup_revision,
                "draft": draft,
                "capabilities": self.capabilities,
                "warnings": [],
            })
        match = re.fullmatch(r"/versions/(\d+)/file", sub)
        if match and method == "GET":
            number = int(match.group(1))
            if number not in self.versions:
                return self._err(route, 404, "version_not_found", "No such version")
            return route.fulfill(status=200, content_type="application/pdf", body=self.versions[number]["bytes"])
        match = re.fullmatch(r"/versions/(\d+)/pages", sub)
        if match and method == "GET":
            number = int(match.group(1))
            version = self.versions.get(number)
            if version is None:
                return self._err(route, 404, "version_not_found", "No such version")
            return self._json(route, 200, [
                {"page_uid": version["uids"][i], "position": i + 1,
                 "origin_page": (int(version["uids"][i][3:], 16) if int(version["uids"][i][3:], 16) <= len(self.pages) else None),
                 "rotation": version["rotations"][i],
                 "width_pt": PAGE_W, "height_pt": PAGE_H, "has_text": True, "scanned": False}
                for i in range(len(version["pages"]))
            ])
        match = re.fullmatch(r"/versions/(\d+)/pages/(pg_[0-9a-f]{12})/text", sub)
        if match and method == "GET":
            number, uid = int(match.group(1)), match.group(2)
            version = self.versions[number]
            self.text_gets.append(f"{number}/{uid}")
            index = version["uids"].index(uid)
            model = text_model(uid, number, version["hash"], version["pages"][index])
            if self.model_hook:
                model = self.model_hook(model, number)
            return self._json(route, 200, model)

        match = re.fullmatch(r"/versions/(\d+)/pages/(pg_[0-9a-f]{12})/images", sub)
        if match and method == "GET":
            return self._json(route, 200, self.images.get(match.group(2), []))
        match = re.fullmatch(r"/versions/(\d+)/pages/(pg_[0-9a-f]{12})/links", sub)
        if match and method == "GET":
            return self._json(route, 200, self.links.get(match.group(2), []))
        if sub == "/draft/preview.pdf" and method == "GET":
            self.draft_pdf_gets.append(query)
            if not self.draft:
                return self._err(route, 404, "draft_not_found", "No draft")
            if int(params.get("revision", -1)) != self.draft["revision"]:
                return self._err(route, 409, "revision_conflict", "Changed",
                                 {"current_revision": self.draft["revision"]})
            order = self.page_order(self.draft["base"], self.draft["ops"])
            data = build_pdf([lines for _, lines, _ in order], [rotation for _, _, rotation in order])
            return route.fulfill(status=200, content_type="application/pdf", body=data,
                                 headers={"X-Page-Uids": ",".join(uid for uid, _, _ in order),
                                          "Access-Control-Expose-Headers": "X-Page-Uids"})
        if sub == "/exports" and method == "POST":
            body = body_json() or {}
            self.export_bodies.append(body)
            job_id = f"zzexport{len(self.exports) + 1:024d}"
            job = {"id": job_id, "status": "done", "version_no": self.current,
                   "include_markup": body.get("include_markup", True), "flatten_forms": False,
                   "result_sha256": "zz", "result_bytes": 1, "error": None, "warnings": [],
                   "created_at": datetime.now(timezone.utc).isoformat(), "finished_at": None}
            self.exports[job_id] = job
            return self._json(route, 202, job)
        match = re.fullmatch(r"/exports/([A-Za-z0-9]+)(/file)?", sub)
        if match:
            job = self.exports.get(match.group(1))
            if job is None:
                return self._err(route, 404, "not_found", "No such export")
            if method == "DELETE":
                return route.fulfill(status=204, body="")
            if match.group(2):
                marker = b"% ZZ export include_markup=" + (b"1" if job["include_markup"] else b"0") + b"\n"
                return route.fulfill(status=200, content_type="application/pdf",
                                     body=self.versions[self.current]["bytes"] + marker)
            return self._json(route, 200, job)

        if sub == "/markup" and method == "GET":
            return self._json(route, 200, {"revision": self.markup_revision, "elements": self.markup})
        if sub == "/markup" and method == "PUT":
            body = body_json() or {}
            self.markup_puts.append(body)
            if self.draft and self.draft["lease_active"]:
                return self._err(route, 409, "draft_active", "The document is being edited")
            if body.get("revision") != self.markup_revision:
                return self._err(route, 409, "revision_conflict", "The markup has changed",
                                 {"current_revision": self.markup_revision})
            if any("page" in element for element in body.get("elements", [])):
                return self._err(route, 422, "invalid_markup", "page given")
            self.markup = body.get("elements", [])
            self.markup_revision += 1
            return self._json(route, 200, {"revision": self.markup_revision, "review_reset": False})

        if sub == "/draft" and method == "POST":
            body = body_json() or {}
            self.draft_posts.append(body)
            if body.get("base_version_no") != self.current:
                return self._err(route, 409, "base_outdated", "Newer version", {"current_version_no": self.current})
            if self.draft:
                mine = self.draft["session"] == body.get("client_session_id") and self.draft["is_mine"]
                if not mine and self.draft["lease_active"] and not body.get("take_over"):
                    return self._err(route, 409, "draft_locked", "Someone else is editing",
                                     {"owner_name": self.draft["owner_name"], "lease_expires_at": self.draft["lease"]})
                if not mine:
                    self.draft.update(session=body.get("client_session_id"), is_mine=True,
                                      owner_name="ZZ UI Runner", revision=self.draft["revision"] + 1)
                self.draft["lease"] = _now_plus(120)
                return self._json(route, 200, self._draft_out())
            self.draft = {"revision": 0, "base": self.current, "ops": [], "markup": None,
                          "session": body.get("client_session_id"), "is_mine": True,
                          "owner_name": "ZZ UI Runner", "lease": _now_plus(120), "lease_active": True}
            return self._json(route, 201, self._draft_out())
        if sub == "/draft" and method == "GET":
            if not self.draft:
                return self._err(route, 404, "draft_not_found", "No draft")
            return self._json(route, 200, self._draft_out())
        if sub == "/draft" and method == "PUT":
            body = body_json() or {}
            self.draft_puts.append(body)
            if not self.draft:
                return self._err(route, 404, "draft_not_found", "No draft")
            if body.get("client_session_id") != self.draft["session"]:
                return self._err(route, 409, "draft_taken_over", "Edited elsewhere")
            if body.get("revision") != self.draft["revision"]:
                return self._err(route, 409, "revision_conflict", "Changed",
                                 {"current_revision": self.draft["revision"]})
            ops = body.get("ops") or []
            markup = body.get("markup")
            for op in ops:
                if op.get("kind") == "page.delete" and op.get("page_uid") in self.slot_page_uids and api.role not in ("admin", "reviewer"):
                    self.draft_puts.append({"forced": 403, **body})
                    return self._err(route, 403, "page_has_slots", "Only a reviewer or an admin can delete a page with slots",
                                     {"pages": [1]})
            if markup is not None and any("page" in element for element in markup):
                return self._err(route, 422, "invalid_ops", "page given", {"ops": []})
            self.draft.update(ops=ops, markup=markup, revision=self.draft["revision"] + 1, lease=_now_plus(120))
            warnings = self.warnings_for_put(ops) if self.warnings_for_put else []
            return self._json(route, 200, {"revision": self.draft["revision"],
                                           "saved_at": datetime.now(timezone.utc).isoformat(),
                                           "lease_expires_at": self.draft["lease"], "warnings": warnings})
        if sub == "/draft/lease" and method == "POST":
            body = body_json() or {}
            self.lease_posts.append(body)
            if not self.draft:
                return self._err(route, 404, "draft_not_found", "No draft")
            return self._json(route, 200, {"lease_expires_at": _now_plus(120)})
        if sub == "/draft" and method == "DELETE":
            self.draft_deletes.append(query)
            if not self.draft:
                return self._err(route, 404, "draft_not_found", "No draft")
            if int(params.get("revision", -1)) != self.draft["revision"]:
                return self._err(route, 409, "revision_conflict", "Changed",
                                 {"current_revision": self.draft["revision"]})
            self.draft = None
            return route.fulfill(status=204, body="")
        match = re.fullmatch(r"/draft/pages/(pg_[0-9a-f]{12})/preview", sub)
        if match and method == "GET":
            uid = match.group(1)
            self.previews.append(f"{uid}?{query}")
            if not self.draft:
                return self._err(route, 404, "draft_not_found", "No draft")
            if int(params.get("revision", -1)) != self.draft["revision"]:
                return self._err(route, 409, "revision_conflict", "Changed",
                                 {"current_revision": self.draft["revision"]})
            base_version = self.versions[self.draft["base"]]
            base = base_version["pages"][base_version["uids"].index(uid)] if uid in base_version["uids"] else []
            lines = apply_ops(base, self.draft["ops"], uid, params.get("hide"))
            return route.fulfill(status=200, content_type="application/pdf", body=build_pdf([lines]))
        if sub == "/draft/commit" and method == "POST":
            body = body_json() or {}
            self.commit_bodies.append(body)
            if not self.draft:
                return self._err(route, 404, "draft_not_found", "No draft")
            if body.get("revision") != self.draft["revision"]:
                return self._err(route, 409, "revision_conflict", "Changed",
                                 {"current_revision": self.draft["revision"]})
            ops = self.draft["ops"]
            signatures = len(api.signature_rows())
            if ops and signatures and not body.get("discard_signatures"):
                return self._err(route, 409, "signatures_present", "Has signatures", {"count": signatures})
            removed = 0
            if ops:
                order = self.page_order(self.draft["base"], ops)
                self._add_version(self.current + 1, [lines for _, lines, _ in order], content_changed=True,
                                  uids=[uid for uid, _, _ in order], rotations=[rotation for _, _, rotation in order])
                self.current += 1
                if body.get("discard_signatures"):
                    removed = signatures
                    api.signed_field_ids = []
                    api.free_rows = []
            if self.draft["markup"] is not None:
                self.markup = self.draft["markup"]
                self.markup_revision += 1
            self.draft = None
            reset = bool(ops) and self.review_reset_on_commit
            if reset:
                api.review_status = "pending"
            return self._json(route, 201, {
                "version": self.version_out(self.current),
                "content_changed": bool(ops),
                "review_reset": reset,
                "reparse_started": bool(ops),
                "remapped": {"signatures_kept": 0 if ops else signatures, "signatures_removed": removed},
                "warnings": [],
            })
        # F2b: a document without form fields (`_formmock.FormDocMock` serves real ones).
        if method == "GET" and re.fullmatch(r"/versions/\d+/fields", sub):
            return self._json(route, 200, [])
        api.unmocked.append(f"{method} /document{sub}")
        return self._err(route, 404, "zz_unmocked", f"unmocked {method} /document{sub}")

    def page_order(self, base_no: int, ops: list[dict]) -> list[tuple[str, list[Line], int]]:
        """(uid, lines with the text ops applied, rotation) in the order the page ops make."""
        version = self.versions[base_no]
        order = [(uid, apply_ops(lines, ops, uid), rot)
                 for uid, lines, rot in zip(version["uids"], version["pages"], version["rotations"])]
        for op in ops:
            kind = op.get("kind")
            if kind == "page.insert":
                after = op.get("after_page_uid")
                at = 0 if after is None else next(i for i, entry in enumerate(order) if entry[0] == after) + 1
                order.insert(at, (op["new_page_uid"], [], 0))
            elif kind == "page.delete":
                order = [entry for entry in order if entry[0] != op["page_uid"]]
            elif kind == "page.move":
                moving = next(entry for entry in order if entry[0] == op["page_uid"])
                order = [entry for entry in order if entry[0] != op["page_uid"]]
                after = op.get("after_page_uid")
                at = 0 if after is None else next(i for i, entry in enumerate(order) if entry[0] == after) + 1
                order.insert(at, moving)
            elif kind == "page.rotate":
                order = [(uid, lines, op["rotation"] if uid == op["page_uid"] else rot) for uid, lines, rot in order]
        return order

    def _draft_out(self) -> dict[str, Any]:
        draft = self.draft or {}
        return {"revision": draft["revision"], "base_version_no": draft["base"], "ops": draft["ops"],
                "markup": draft["markup"], "lease_expires_at": draft["lease"], "is_mine": draft["is_mine"],
                "owner_name": draft["owner_name"]}


def enable_document(api, pages: list[list[Line]], **options: Any) -> DocMock:
    """Install the editor on this MockApi: version 1 = these pages (also served on `/file`)."""
    doc = DocMock(pages, **options)
    api.document = doc
    api.set_pdf(doc.pdf_v1())
    return doc


def seeded_draft(doc: DocMock, *, mine: bool = True, lease_active: bool = True, ops: list | None = None,
                 revision: int = 3, session: str = "zz-other-session-000") -> None:
    """A draft left on the server (recovery / lock checks)."""
    doc.draft = {"revision": revision, "base": doc.current, "ops": ops or [], "markup": None, "session": session,
                 "is_mine": mine, "owner_name": "ZZ UI Runner" if mine else "Other Editor",
                 "lease": _now_plus(100 if lease_active else -30), "lease_active": lease_active}


def wait_until(page, predicate: Callable[[], bool], timeout_ms: int = 10000, step_ms: int = 50) -> bool:
    """Poll a Python-side condition WHILE the browser keeps running (routes are served in
    `page.wait_for_timeout`, never in `time.sleep`)."""
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        page.wait_for_timeout(step_ms)
        waited += step_ms
    return predicate()
