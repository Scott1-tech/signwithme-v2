"""
F2b: the B3 form endpoints, mocked (the `_docmock.DocMock` pattern).

`enable_forms(api, pages, fields=…)` installs a `FormDocMock` -- a `DocMock`
that also answers

  * `GET  …/document/versions/{n}/fields`  the version's widgets (`FieldOut`
    rows, tab order), with the draft's field ops played over them at commit;
  * `POST …/document/detect-fields`        the configured suggestions of the
    asked pages (`page_uids`), recorded in `detect_bodies`;

and checks the field ops of a draft PUT the way the engine does
(`forms.apply_field_ops`): 409-class problems (`field_name_taken`,
`radio_value_taken`, `unknown_field`) and 422-class ones (`field_readonly`,
`field_not_fillable`, `undrawable_character`, `invalid_field_value`) come
back as 422 `invalid_ops {ops:[{id, reason}]}`; a fill-only user
(`capabilities.can_edit == False`, `can_fill == True`) gets 403 `fill_only`
for anything but `field.value`. Nothing here reaches the live backend.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from _docmock import DocMock, Line

FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def field_out(name: str, type_: str, uid: str, rect: tuple[float, float, float, float], *, value: Any = None,
              source: str = "original", hidden: bool = False, **extra: Any) -> dict[str, Any]:
    """One `FieldOut` row."""
    x, y, w, h = rect
    row = {
        "name": name, "type": type_, "page_uid": uid, "rect": {"x_pt": x, "y_pt": y, "w_pt": w, "h_pt": h},
        "required": False, "readonly": False, "tooltip": None, "default": None, "options": None,
        "export_value": None, "font": None, "size_pt": None, "max_len": None, "tab_index": None,
        "value": value if value is not None else ("" if type_ in ("text", "multiline", "dropdown", "radio") else
                                                  (False if type_ == "checkbox" else None)),
        "source": source, "hidden": hidden,
    }
    row.update(extra)
    return row


def _winansi_ok(text: str) -> bool:
    for char in text:
        if char == "\n":
            continue
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            return False
        if ord(char) < 0x20:
            return False
    return True


def apply_field_ops(fields: list[dict], ops: list[dict]) -> tuple[list[dict], list[dict]]:
    """The engine's `apply_field_ops`, in spirit: (widgets after, problems [{id, reason}])."""
    rows = copy.deepcopy(fields)
    problems: list[dict] = []

    def of(name: str) -> list[dict]:
        return [row for row in rows if row["name"] == name]

    for op in ops:
        kind = op.get("kind", "")
        if not kind.startswith("field."):
            continue
        oid = op.get("id")
        if kind == "field.add":
            spec = op["field"]
            existing = of(spec["name"])
            if spec["type"] == "radio":
                if existing and not all(row["type"] == "radio" for row in existing):
                    problems.append({"id": oid, "reason": "field_name_taken"})
                    continue
                if any(row.get("export_value") == spec.get("export_value") for row in existing):
                    problems.append({"id": oid, "reason": "radio_value_taken"})
                    continue
            elif existing:
                problems.append({"id": oid, "reason": "field_name_taken"})
                continue
            value: Any = spec.get("default") if isinstance(spec.get("default"), str) else ""
            if spec["type"] == "checkbox":
                value = bool(spec.get("default"))
            if spec["type"] == "signature":
                value = None
            if spec["type"] == "radio":
                value = next((row["value"] for row in existing if row["value"]), "")
                if not value and spec.get("default") and not existing:
                    value = spec["export_value"]
            rows.append({**field_out(spec["name"], spec["type"], op["page_uid"],
                                     (spec["rect"]["x_pt"], spec["rect"]["y_pt"], spec["rect"]["w_pt"], spec["rect"]["h_pt"]),
                                     value=value, source="editor"),
                         **{k: spec.get(k) for k in ("required", "readonly", "tooltip", "default", "options",
                                                     "export_value", "font", "size_pt", "max_len", "tab_index")},
                         "required": bool(spec.get("required")), "readonly": bool(spec.get("readonly"))})
        elif kind == "field.remove":
            targets = [row for row in of(op["name"]) if op.get("export_value") in (None, row.get("export_value"))]
            if not targets:
                problems.append({"id": oid, "reason": "unknown_field"})
                continue
            rows = [row for row in rows if row not in targets]
        elif kind == "field.update":
            current = op.get("name") or op["field"]["name"]
            targets = [row for row in of(current) if op.get("export_value") in (None, row.get("export_value"))]
            if not targets:
                problems.append({"id": oid, "reason": "unknown_field"})
                continue
            spec = op["field"]
            others = [row for row in of(spec["name"]) if row not in targets]
            if spec["name"] != current and others and not (spec["type"] == "radio" and all(r["type"] == "radio" for r in others)):
                problems.append({"id": oid, "reason": "field_name_taken"})
                continue
            old = targets[0]
            keep = old["value"] if old["type"] == spec["type"] else None
            rows = [row for row in rows if row not in targets]
            rows.append({**old, **{k: spec.get(k) for k in ("name", "type", "tooltip", "default", "options", "export_value",
                                                            "font", "size_pt", "max_len", "tab_index")},
                         "required": bool(spec.get("required")), "readonly": bool(spec.get("readonly")),
                         "rect": dict(spec["rect"]), "page_uid": op["page_uid"],
                         "value": keep if keep is not None else old["value"]})
        elif kind == "field.value":
            targets = of(op["name"])
            if not targets:
                problems.append({"id": oid, "reason": "unknown_field"})
                continue
            first = targets[0]
            value = op["value"]
            if first["type"] == "signature":
                problems.append({"id": oid, "reason": "field_not_fillable"})
                continue
            if any(row["readonly"] for row in targets):
                problems.append({"id": oid, "reason": "field_readonly"})
                continue
            if first["type"] in ("text", "multiline"):
                if not isinstance(value, str) or ("\n" in value and first["type"] == "text"):
                    problems.append({"id": oid, "reason": "invalid_field_value"})
                    continue
                if not _winansi_ok(value):
                    problems.append({"id": oid, "reason": "undrawable_character"})
                    continue
            if first["type"] == "checkbox" and not isinstance(value, bool):
                problems.append({"id": oid, "reason": "invalid_field_value"})
                continue
            if first["type"] == "dropdown" and value and value not in (first.get("options") or []):
                problems.append({"id": oid, "reason": "invalid_field_value"})
                continue
            if first["type"] == "radio" and value and value not in {row.get("export_value") for row in targets}:
                problems.append({"id": oid, "reason": "invalid_field_value"})
                continue
            for row in targets:
                row["value"] = value
    rows.sort(key=lambda row: (row.get("tab_index") is None, row.get("tab_index") or 0))
    return rows, problems


class FormDocMock(DocMock):
    """`DocMock` + the B3 endpoints (see the module header)."""

    def __init__(self, pages: list[list[Line]], *, fields: list[dict] | None = None, can_fill: bool = True,
                 **options: Any) -> None:
        super().__init__(pages, **options)
        self.capabilities["can_fill"] = can_fill
        self.fields: dict[int, list[dict]] = {1: list(fields or [])}
        self.suggestions: list[dict] = []
        self.detect_bodies: list[Any] = []
        self.field_gets: list[int] = []
        self.field_puts: list[list[dict]] = []

    def handle(self, api, route, method: str, sub: str, query: str, body_json) -> None:
        match = re.fullmatch(r"/versions/(\d+)/fields", sub)
        if match and method == "GET":
            number = int(match.group(1))
            self.field_gets.append(number)
            if number not in self.versions:
                return self._err(route, 404, "version_not_found", "No such version")
            return self._json(route, 200, self.fields.get(number, []))
        if sub == "/detect-fields" and method == "POST":
            body = body_json() or {}
            self.detect_bodies.append(body)
            if not self.capabilities.get("can_edit"):
                return self._err(route, 403, "not_permitted", "Not allowed")
            wanted = body.get("page_uids") or list(self.versions[self.current]["uids"])
            unknown = [uid for uid in wanted if uid not in self.versions[self.current]["uids"]]
            if unknown:
                return self._err(route, 422, "unknown_page", "Unknown pages", {"page_uids": unknown})
            return self._json(route, 200, {"version_no": self.current,
                                           "suggestions": [s for s in self.suggestions if s["page_uid"] in wanted]})
        if sub == "/draft" and method == "PUT" and self.draft and not self.force.get("PUT /draft") \
                and not self.abort.get("PUT /draft"):
            body = body_json() or {}
            ops = body.get("ops") or []
            field_ops = [op for op in ops if str(op.get("kind", "")).startswith("field.")]
            if field_ops:
                self.field_puts.append(field_ops)
            if not self.capabilities.get("can_edit") and self.capabilities.get("can_fill"):
                bad = [{"id": op.get("id"), "reason": "fill_only"} for op in ops if op.get("kind") != "field.value"]
                if bad:
                    self.draft_puts.append({"forced": 403, **body})
                    return self._err(route, 403, "fill_only", "You may fill the form fields of this document, not change it",
                                     {"ops": bad})
            if body.get("client_session_id") == self.draft["session"] and body.get("revision") == self.draft["revision"]:
                _rows, problems = apply_field_ops(self.fields.get(self.draft["base"], []), field_ops)
                if problems:
                    self.draft_puts.append({"forced": 422, **body})
                    return self._err(route, 422, "invalid_ops", "Some edits cannot be applied", {"ops": problems})
        if sub == "/draft/commit" and method == "POST" and self.draft:
            before = self.current
            base = self.draft["base"]
            field_ops = [op for op in (self.draft.get("ops") or []) if str(op.get("kind", "")).startswith("field.")]
            super().handle(api, route, method, sub, query, body_json)
            if self.current > before:
                self.fields[self.current], _problems = apply_field_ops(self.fields.get(base, []), field_ops)
            return None
        return super().handle(api, route, method, sub, query, body_json)


def enable_forms(api, pages: list[list[Line]], *, fields: list[dict] | None = None, **options: Any) -> FormDocMock:
    """Install the editor WITH forms on this MockApi (version 1 = these pages, these widgets)."""
    doc = FormDocMock(pages, fields=fields, **options)
    api.document = doc
    api.set_pdf(doc.pdf_v1())
    return doc
