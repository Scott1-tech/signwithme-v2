"""Shared helpers for the share-for-fill tests (F3)."""
from __future__ import annotations

from starlette.testclient import TestClient

from tests.conftest import auth
from tests.editor_v2_helpers import commit, first_page_uid, put_draft, session_id, start_draft

LAN_IP = "10.31.0.77"


def R(x, y, w, h):
    return {"x_pt": x, "y_pt": y, "w_pt": w, "h_pt": h}


def add(uid, name, ftype, rect, tab, op_id=None, **extra):
    return {"id": op_id or f"a_{name}_{tab}", "kind": "field.add", "page_uid": uid,
            "field": {"name": name, "type": ftype, "rect": rect, "tab_index": tab, **extra}}


FORM = [
    ("carrier", "text", R(300, 400, 200, 18), {"required": True}),
    ("notes", "multiline", R(300, 300, 200, 60), {}),
    ("pay", "radio", R(300, 430, 14, 14), {"export_value": "Weekly"}),
    ("pay", "radio", R(340, 430, 14, 14), {"export_value": "Monthly"}),
    ("agree", "checkbox", R(300, 460, 14, 14), {}),
    ("plan", "dropdown", R(300, 490, 120, 18), {"options": ["Basic", "Plus"]}),
    ("ssn", "text", R(300, 520, 120, 18), {}),
    ("short", "text", R(300, 550, 120, 18), {"max_len": 5}),
    ("locked", "text", R(300, 580, 120, 18), {"readonly": True}),
    ("sign_here", "signature", R(300, 610, 180, 36), {}),
]
FILLABLE = ["carrier", "notes", "pay", "agree", "plan", "ssn", "short"]


def commit_ops(api, token, contract_id, ops, base_version_no=1, **commit_extra):
    session = session_id()
    started = start_draft(api, token, contract_id, session, base_version_no=base_version_no)
    assert started.status_code in (200, 201), started.text
    saved = put_draft(api, token, contract_id, session, started.json()["revision"], ops)
    assert saved.status_code == 200, saved.text
    return commit(api, token, contract_id, saved.json()["revision"], **commit_extra)


def with_form(api, token, contract_id) -> str:
    """Commit FORM onto page 1 -> version 2. Returns the page uid."""
    uid = first_page_uid(api, token, contract_id)
    ops = [add(uid, name, ftype, rect, n, **extra) for n, (name, ftype, rect, extra) in enumerate(FORM)]
    done = commit_ops(api, token, contract_id, ops)
    assert done.status_code == 201, done.text
    return uid


def links_url(contract_id, suffix=""):
    return f"/api/contracts/{contract_id}/share-links{suffix}"


def submissions_url(contract_id, suffix=""):
    return f"/api/contracts/{contract_id}/submissions{suffix}"


def create_link(api, token, contract_id, fields=None, **extra):
    body = {"allowed_fields": fields if fields is not None else list(FILLABLE), **extra}
    return api.post(links_url(contract_id), headers=auth(token), json=body)


def created(api, token, contract_id, fields=None, **extra) -> dict:
    response = create_link(api, token, contract_id, fields, **extra)
    assert response.status_code == 201, response.text
    return response.json()


def client_at(api, ip: str = LAN_IP) -> TestClient:
    """A client whose socket peer address is `ip` (Starlette's TestClient
    always reports "testclient"). Same app, same dependency overrides."""
    from app.main import app

    async def wrapped(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (ip, 50000)}
        await app(scope, receive, send)

    return TestClient(wrapped)


def tok(token: str) -> dict[str, str]:
    return {"X-Share-Token": token}


def submit(client, token, values, **extra):
    return client.post("/api/share/submissions", headers=tok(token), json={"values": values, **extra})


def db_rows(api, model):
    from sqlalchemy import select

    db = api.db_factory()
    try:
        return list(db.scalars(select(model)))
    finally:
        db.close()


def audit_rows(api, action):
    from sqlalchemy import select

    from app.models.audit import AuditLog

    db = api.db_factory()
    try:
        return [(row.details, row.actor_id, row.ip_address) for row in db.scalars(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.timestamp))]
    finally:
        db.close()


def dump_database(api) -> str:
    """Every value of every row of every table, as one string."""
    from sqlalchemy import text

    from app.models import Base

    db = api.db_factory()
    try:
        parts = []
        for name in Base.metadata.tables:
            for row in db.execute(text(f'SELECT * FROM "{name}"')):
                parts.append(repr(tuple(row)))
        return "\n".join(parts)
    finally:
        db.close()
