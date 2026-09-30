"""
F3: «Share for others to fill», mocked (EDITOR-V2-PLAN §8; backend F3).

`MockApi.share` is absent by default. Then (a server WITH the editor) the
routes the workspace reads on every open answer the way a server with
nothing shared does -- deliberately, so not "unmocked":

  * `GET .../document/versions/{n}/fields` -> `[]` (no form fields; only while
    `MockApi.document` is set -- without the editor it stays the 404 above);
  * `GET .../share-links`, `GET .../submissions` -> `[]`.

`enable_share(api, fields)` installs a `ShareMock`: a small, honest model of
the backend's links (token shown once, `sha256` kept, status, revoke), the
public routes (`X-Share-Token` only, every refusal the SAME 404, 429 when
`locked`), the submissions and accept / reject (accept writes a draft of
`field.value` ops into the `DocMock` under the given `client_session_id`,
409 `draft_locked` when another draft is there). `force` queues canned
answers by "METHOD /shape" like `DocMock.force`.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

SHARE_BASE = "http://10.31.0.58:5173"
WINANSI_EXTRA = set("€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ")
SHAREABLE = ("text", "multiline", "dropdown", "radio", "checkbox")


def _iso(delta_hours: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


def winansi_ok(text: str) -> bool:
    for char in text:
        code = ord(char)
        if char in "\n\r\t" or 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF or char in WINANSI_EXTRA:
            continue
        return False
    return True


def field(name: str, kind: str = "text", *, page_uid: str = "pg_000000000001", x: float = 72, y: float = 300,
          w: float = 200, h: float = 20, required: bool = False, readonly: bool = False, hidden: bool = False,
          options: list[str] | None = None, export_value: str | None = None, value: Any = None,
          max_len: int | None = None, tooltip: str | None = None) -> dict[str, Any]:
    """One `FieldOut` row (one widget)."""
    if value is None:
        value = False if kind == "checkbox" else ("" if kind in SHAREABLE else None)
    return {
        "name": name, "type": kind, "page_uid": page_uid,
        "rect": {"x_pt": x, "y_pt": y, "w_pt": w, "h_pt": h},
        "required": required, "readonly": readonly, "tooltip": tooltip, "default": None,
        "options": options, "export_value": export_value, "font": None, "size_pt": None,
        "max_len": max_len, "tab_index": None, "value": value, "source": "editor", "hidden": hidden,
    }


def default_fields() -> list[dict[str, Any]]:
    """A text (required), a checkbox, a dropdown, a two-button radio group, a
    sensitive text, and two that cannot be shared (signature, read-only)."""
    return [
        field("carrier_name", "text", y=300, required=True, tooltip="Carrier name"),
        field("agree", "checkbox", x=72, y=340, w=14, h=14),
        field("state", "dropdown", x=300, y=300, w=120, options=["TX", "CA", "NY"]),
        field("pay", "radio", x=72, y=380, w=14, h=14, export_value="ach"),
        field("pay", "radio", x=120, y=380, w=14, h=14, export_value="check"),
        field("driver_ssn", "text", x=300, y=340, w=120),
        field("sig_box", "signature", x=72, y=420, w=180, h=30),
        field("office_code", "text", x=300, y=420, w=80, readonly=True, value="HQ"),
    ]


class ShareMock:
    def __init__(self, fields: list[dict[str, Any]]) -> None:
        self.fields = fields
        self.links: list[dict[str, Any]] = []
        self.tokens: dict[str, str] = {}  # sha256 -> link id
        self.submissions: list[dict[str, Any]] = []
        self.force: dict[str, list[tuple[int, Any]]] = {}
        self.locked = False  # public 429
        # Recorded traffic.
        self.create_bodies: list[Any] = []
        self.revokes: list[str] = []
        self.accept_bodies: list[Any] = []
        self.reject_bodies: list[Any] = []
        self.public: list[dict[str, Any]] = []  # {method, path, url, token, auth}
        self.submit_bodies: list[Any] = []

    # -- fixtures ------------------------------------------------------------

    def add_link(self, *, label: str | None = "ZZ Driver", allowed: list[str] | None = None, max_submissions: int = 1,
                 status: str = "active", hours: float = 72) -> tuple[dict[str, Any], str]:
        token = secrets.token_urlsafe(32)
        link = {
            "id": f"zz-link-{len(self.links) + 1}",
            "version_no": 1,
            "allowed_fields": allowed or ["carrier_name", "agree", "state", "pay", "driver_ssn"],
            "recipient_label": label,
            "expires_at": _iso(hours if status != "expired" else -1),
            "max_submissions": max_submissions,
            "submission_count": 0,
            "submissions_pending": 0,
            "status": status,
            "revoked_at": _iso() if status == "revoked" else None,
            "last_used_at": None,
            "created_by_name": "ZZ UI Runner",
            "created_at": _iso(-1),
        }
        self.links.insert(0, link)
        self.tokens[hashlib.sha256(token.encode()).hexdigest()] = link["id"]
        return link, token

    def add_submission(self, link: dict[str, Any], values: dict[str, Any], *, name: str | None = "ZZ Filler",
                       email: str | None = "zz@example.test", status: str = "received") -> dict[str, Any]:
        row = {
            "id": f"zz-sub-{len(self.submissions) + 1}",
            "share_link_id": link["id"],
            "recipient_label": link["recipient_label"],
            "version_no": link["version_no"],
            "status": status,
            "submitter_name": name,
            "submitter_email": email,
            "field_count": len(values),
            "values": values,
            "values_readable": True,
            "ip_address": "10.31.0.77",
            "created_at": _iso(-0.5),
            "reviewed_by_name": None,
            "reviewed_at": None,
            "reject_reason": None,
        }
        self.submissions.insert(0, row)
        link["submission_count"] += 1
        self._refresh(link)
        return row

    # -- helpers ---------------------------------------------------------------

    def _refresh(self, link: dict[str, Any]) -> None:
        link["submissions_pending"] = sum(
            1 for row in self.submissions if row["share_link_id"] == link["id"] and row["status"] == "received")
        if link["status"] == "active" and link["submission_count"] >= link["max_submissions"]:
            link["status"] = "used_up"

    def _link(self, link_id: str) -> dict[str, Any] | None:
        return next((link for link in self.links if link["id"] == link_id), None)

    def rows_of(self, name: str) -> list[dict[str, Any]]:
        return [row for row in self.fields if row["name"] == name]

    def problem(self, name: str) -> str | None:
        rows = self.rows_of(name)
        if not rows:
            return "unknown_field"
        if any(row["type"] not in SHAREABLE for row in rows):
            return "field_not_fillable"
        if any(row["readonly"] for row in rows):
            return "field_readonly"
        if any(row["hidden"] for row in rows):
            return "field_hidden"
        return None

    @staticmethod
    def public_link(link: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in link.items() if key not in ("token", "url")}


def enable_share(api, fields: list[dict[str, Any]] | None = None) -> ShareMock:
    mock = ShareMock(default_fields() if fields is None else fields)
    api.share = mock
    return mock


def _json(route, status: int, body: Any) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body),
                  headers={"Cache-Control": "no-store"})


def _err(route, status: int, code: str, message: str, details: dict | None = None) -> None:
    _json(route, status, {"error": code, "message": message, "details": details or {}})


def route_share(api, route, method: str, path: str, query: str, body_json: Callable[[], Any], request) -> bool:
    """Answer an F3 route; False = not F3's (the harness carries on)."""
    cid = api.contract_id
    base = f"/api/contracts/{cid}"
    share: ShareMock | None = getattr(api, "share", None)
    fields_path = re.fullmatch(rf"{re.escape(base)}/document/versions/\d+/fields", path)
    internal = path in (f"{base}/share-links", f"{base}/submissions") or path.startswith(
        (f"{base}/share-links/", f"{base}/submissions/"))
    public = path.startswith("/api/share/")

    if share is None:
        # `GET …/versions/{n}/fields` is left to the document mock: `_docmock`
        # answers `[]` and F2b's `_formmock` serves the real fields.
        if internal and method == "GET":
            _json(route, 200, [])
            return True
        return False

    if not (fields_path or internal or public):
        return False
    shape = re.sub(r"/versions/\d+/", "/versions/{n}/", path.replace(base, ""))
    shape = re.sub(r"/(zz-link-\d+|zz-sub-\d+)", "/{id}", shape)
    forced = share.force.get(f"{method} {shape}")
    if forced:
        status, body = forced.pop(0)
        if method == "POST" and shape == "/share-links":
            share.create_bodies.append({"forced": status, **(body_json() or {})})
        if method == "POST" and shape == "/api/share/submissions":
            share.submit_bodies.append({"forced": status, **(body_json() or {})})
        if public:
            share.public.append({"method": method, "path": path, "url": request.url,
                                 "token": request.headers.get("x-share-token"),
                                 "auth": request.headers.get("authorization")})
        _json(route, status, body)
        return True

    # -- the editor's fields --------------------------------------------------------
    if fields_path and method == "GET":
        _json(route, 200, share.fields)
        return True

    # -- internal ---------------------------------------------------------------------
    if path == f"{base}/share-links" and method == "POST":
        body = body_json() or {}
        share.create_bodies.append(body)
        names = body.get("allowed_fields") or []
        hours = body.get("expires_in_hours") or 72
        if hours > 336:
            _err(route, 422, "expiry_too_long", "A share link lasts at most 14 days", {"max_hours": 336})
            return True
        problems = [{"index": i, "reason": share.problem(n)} for i, n in enumerate(names) if share.problem(n)]
        if problems:
            _err(route, 422, "fields_not_shareable", "Some fields cannot be shared for filling", {"fields": problems})
            return True
        link, token = share.add_link(label=(body.get("recipient_label") or None), allowed=names,
                                     max_submissions=body.get("max_submissions", 1), hours=hours)
        _json(route, 201, {**link, "token": token, "url": f"{SHARE_BASE}/s#t={token}"})
        return True
    if path == f"{base}/share-links" and method == "GET":
        _json(route, 200, [share.public_link(link) for link in share.links])
        return True
    match = re.fullmatch(rf"{re.escape(base)}/share-links/([^/]+)", path)
    if match and method == "DELETE":
        share.revokes.append(match.group(1))
        link = share._link(match.group(1))
        if link is None:
            _err(route, 404, "not_found", "Share link not found")
            return True
        if link["revoked_at"] is None:
            link["revoked_at"] = _iso()
            link["status"] = "revoked"
        route.fulfill(status=204, body="")
        return True
    if path == f"{base}/submissions" and method == "GET":
        wanted = dict(part.split("=", 1) for part in query.split("&") if "=" in part).get("status")
        _json(route, 200, [row for row in share.submissions if not wanted or row["status"] == wanted])
        return True
    match = re.fullmatch(rf"{re.escape(base)}/submissions/([^/]+)/(accept|reject)", path)
    if match and method == "POST":
        row = next((item for item in share.submissions if item["id"] == match.group(1)), None)
        body = body_json() or {}
        if row is None:
            _err(route, 404, "not_found", "Submission not found")
            return True
        if row["status"] != "received":
            _err(route, 409, "submission_not_pending", "This submission was already reviewed", {"status": row["status"]})
            return True
        link = share._link(row["share_link_id"])
        if match.group(2) == "reject":
            share.reject_bodies.append(body)
            reason = (body.get("reason") or "").strip()
            if not reason:
                _err(route, 422, "validation_error", "a reason is required")
                return True
            row.update(status="rejected", reject_reason=reason, reviewed_by_name="ZZ UI Runner", reviewed_at=_iso())
            if link:
                share._refresh(link)
            _json(route, 200, row)
            return True
        share.accept_bodies.append(body)
        doc = api.document
        if doc.draft:
            _err(route, 409, "draft_locked", "The document has a draft; save or cancel it first", {
                "owner_name": doc.draft["owner_name"], "lease_expires_at": doc.draft["lease"],
                "is_mine": doc.draft["is_mine"] and doc.draft["session"] == body.get("client_session_id"),
                "lease_active": doc.draft["lease_active"],
            })
            return True
        ops = [{"id": f"share{i}", "kind": "field.value", "name": name, "value": value}
               for i, (name, value) in enumerate(row["values"].items())]
        doc.draft = {"revision": 0, "base": doc.current, "ops": ops, "markup": None,
                     "session": body.get("client_session_id"), "is_mine": True, "owner_name": "ZZ UI Runner",
                     "lease": _iso(2 / 60), "lease_active": True}
        row.update(status="accepted", reviewed_by_name="ZZ UI Runner", reviewed_at=_iso())
        if link:
            share._refresh(link)
        _json(route, 200, {"submission": row, "draft": doc._draft_out()})
        return True

    # -- public -------------------------------------------------------------------------
    if public:
        token = request.headers.get("x-share-token")
        share.public.append({"method": method, "path": path, "url": request.url, "token": token,
                             "auth": request.headers.get("authorization")})
        if share.locked:
            _err(route, 429, "too_many_attempts", "Too many failed attempts. Try again later.",
                 {"retry_after_seconds": 600})
            return True
        link_id = share.tokens.get(hashlib.sha256((token or "").encode()).hexdigest())
        link = share._link(link_id) if link_id else None
        if link is None or link["status"] != "active":
            _err(route, 404, "not_found", "Not found")
            return True
        allowed = link["allowed_fields"]
        if path == "/api/share/session" and method == "GET":
            rows = [{k: row[k] for k in ("name", "type", "page_uid", "rect", "required", "tooltip", "options",
                                         "export_value", "font", "size_pt", "max_len", "tab_index", "value")}
                    for row in share.fields if row["name"] in allowed and share.problem(row["name"]) is None]
            uids = api.document.versions[1]["uids"] if api.document is not None else ["pg_000000000001"]
            _json(route, 200, {
                "document_title": "ZZ Synthetic Carrier Packet",
                "version_no": link["version_no"],
                "fields": rows,
                "pages": [{"page_uid": uid, "position": i + 1, "width_pt": 612.0, "height_pt": 792.0, "rotation": 0}
                          for i, uid in enumerate(uids)],
                "expires_at": link["expires_at"],
                "submissions_left": link["max_submissions"] - link["submission_count"],
            })
            return True
        if path == "/api/share/file" and method == "GET":
            data = api.document.versions[1]["bytes"] if api.document is not None else api._pdf
            route.fulfill(status=200, content_type="application/pdf", body=data)
            return True
        if path == "/api/share/submissions" and method == "POST":
            body = body_json() or {}
            share.submit_bodies.append(body)
            values = body.get("values") or {}
            if not values:
                _err(route, 422, "empty_submission", "Nothing was filled in")
                return True
            problems, not_allowed = [], sum(1 for name in values if name not in allowed)
            for name, value in values.items():
                if name in allowed and isinstance(value, str) and not winansi_ok(value):
                    problems.append({"name": name, "reason": "undrawable_character"})
            for name in allowed:
                rows = share.rows_of(name)
                if any(row["required"] for row in rows):
                    value = values.get(name)
                    if value in (None, False, "") or (isinstance(value, str) and not value.strip()):
                        problems.append({"name": name, "reason": "required"})
            if problems or not_allowed:
                _err(route, 422, "invalid_submission", "Some values cannot be accepted",
                     {"fields": problems, "not_allowed": not_allowed})
                return True
            wanted = {name: value for name, value in values.items() if name in allowed}
            share.add_submission(link, wanted, name=body.get("submitter_name"), email=body.get("submitter_email"))
            _json(route, 201, {"status": "received",
                               "submissions_left": link["max_submissions"] - link["submission_count"]})
            return True
    return False
