"""Test fixtures.

Two kinds live here: synthetic PDFs, so the detector and parser can be tested
without shipping a real driver contract (which contains personal data), and a
full API harness -- SQLite database, in-memory object storage and inline Celery
-- so every route can be exercised with no external services running.
"""
from __future__ import annotations

import io
import os

# Must be set before app.config is imported anywhere.
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-in-production")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

PAGE_WIDTH, PAGE_HEIGHT = letter


def _draw_common(pdf, filled: bool):
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, 720, "INDEPENDENT CONTRACTOR AGREEMENT")

    rows = [
        (680, "Full Name:", "CHARLES FRYE"),
        (660, "CDL:", "Oh Rt305248"),
        (640, "Exp:", "01/30/2030"),
        (620, "Email:", "charles@example.com"),
        (600, "SSN:", "123-45-6789"),
    ]
    for y, label, value in rows:
        pdf.drawString(72, y, label)
        pdf.drawString(200, y, "_" * 30)
        if filled:
            pdf.drawString(205, y + 3, value)

    # Checkboxes drawn as vector squares -- the common real-world case. Base-14
    # fonts have no U+2610 glyph, so a template authored in Word or reportlab
    # emits rectangles, not ballot-box characters.
    pdf.drawString(72, 560, "W-9 Status:")
    for index, (x, label) in enumerate(((150, "Individual"), (260, "Sole Proprietor"), (390, "LLC"))):
        pdf.rect(x, 558, 10, 10, stroke=1, fill=0)
        pdf.drawString(x + 14, 560, label)
        if filled and index == 0:
            pdf.drawString(x + 2, 560, "X")

    # A certification sentence sits 12pt above the signature line, as in real
    # contracts (12pt is the tightest common leading). The parser's signature
    # box reaches 34pt above the rule, so template prose lands inside it; an
    # unsigned slot must still read as unsigned. The wording deliberately avoids
    # every label keyword ("Signature", "Date", "Name", ...).
    pdf.drawString(72, 212, "I certify that the information provided above is true and complete.")

    # Signature line with an accompanying date on the same row. The date uses
    # the "__/__/____" placeholder found in the production packages; stripping
    # the underscores leaves "//", which must count as blank.
    pdf.drawString(72, 200, "Contractor Signature:")
    pdf.drawString(200, 200, "_" * 25)
    pdf.drawString(400, 200, "Date:")
    pdf.drawString(440, 200, "__/__/____")


def _make_pdf(filled: bool, pages: int = 1) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for _ in range(pages):
        _draw_common(pdf, filled)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@pytest.fixture
def blank_template_bytes() -> bytes:
    return _make_pdf(filled=False)


@pytest.fixture
def filled_contract_bytes() -> bytes:
    return _make_pdf(filled=True)


@pytest.fixture
def blank_template_path(tmp_path, blank_template_bytes) -> str:
    path = tmp_path / "template.pdf"
    path.write_bytes(blank_template_bytes)
    return str(path)


@pytest.fixture
def filled_contract_path(tmp_path, filled_contract_bytes) -> str:
    path = tmp_path / "contract.pdf"
    path.write_bytes(filled_contract_bytes)
    return str(path)


# --------------------------------------------------------------------------
# API harness
# --------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    """A TestClient wired to SQLite, in-memory storage and synchronous tasks.

    The models use dialect-portable column types precisely so this can run
    without Postgres; nothing here stubs the application's own logic.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.database import get_db
    from app.core.throttle import login_throttle, share_throttle
    from app.main import app
    from app.models import Base

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.workers.tasks.SessionLocal", TestSession)

    # Object storage, in memory.
    objects: dict[str, bytes] = {}
    from app.services import storage as storage_module

    monkeypatch.setattr(storage_module.storage, "ensure_bucket", lambda: None)
    monkeypatch.setattr(
        storage_module.storage,
        "put_bytes",
        lambda key, data, content_type="application/pdf": (objects.__setitem__(key, data), key)[1],
    )
    monkeypatch.setattr(storage_module.storage, "get_bytes", lambda key: objects[key])
    monkeypatch.setattr(storage_module.storage, "stream", lambda key: io.BytesIO(objects[key]))
    monkeypatch.setattr(
        storage_module.storage, "presigned_url", lambda key, filename=None, ttl=None: f"memory://{key}"
    )
    monkeypatch.setattr(storage_module.storage, "delete", lambda key: objects.pop(key, None))

    from contextlib import contextmanager
    import tempfile

    @contextmanager
    def local_copy(key, suffix=".pdf"):
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        handle.write(objects[key])
        handle.close()
        try:
            yield handle.name
        finally:
            os.unlink(handle.name)

    monkeypatch.setattr(storage_module.storage, "local_copy", local_copy)

    # Celery runs inline: the flow under test is upload -> parse -> compare.
    from app.workers import tasks as task_module

    class _Inline:
        def __init__(self, func):
            self._func = func

        def delay(self, *args, **kwargs):
            # Celery's __wrapped__ already carries the task instance for a
            # bind=True task, so the arguments pass straight through.
            self._func(*args, **kwargs)
            return type("Result", (), {"id": "inline"})()

    monkeypatch.setattr(
        "app.api.templates.analyze_template_task",
        _Inline(task_module.analyze_template_task.__wrapped__),
    )
    monkeypatch.setattr(
        "app.api.contracts.process_contract_task",
        _Inline(task_module.process_contract_task.__wrapped__),
    )

    login_throttle.clear()
    share_throttle.clear()

    client = TestClient(app)
    client.db_factory = TestSession  # type: ignore[attr-defined]
    client.objects = objects  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        login_throttle.clear()
        share_throttle.clear()


def _register(api, email: str, password: str = "password123") -> str:
    response = api.post(
        "/api/auth/register",
        json={"email": email, "password": password, "full_name": email.split("@")[0]},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def _promote(api, email: str, role: str) -> None:
    """Grant a role directly, as an operator would via the seed script."""
    from sqlalchemy import select

    from app.models.user import User

    db = api.db_factory()
    try:
        user = db.scalar(select(User).where(User.email == email))
        user.role = role
        db.commit()
    finally:
        db.close()


def _verify_email(api, email: str, verified: bool = True) -> None:
    """Vouch for an account's e-mail, as an admin would via
    POST /api/auth/users/{id}/verify-email."""
    from sqlalchemy import select

    from app.models.user import User

    db = api.db_factory()
    try:
        user = db.scalar(select(User).where(User.email == email))
        user.email_verified = verified
        db.commit()
    finally:
        db.close()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_token(api) -> str:
    token = _register(api, "admin@example.com")
    _promote(api, "admin@example.com", "admin")
    # Re-login so the token carries the elevated role.
    response = api.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "password123"}
    )
    return response.json()["access_token"]


@pytest.fixture
def contractor_token(api) -> str:
    """The driver the suite's contracts are addressed to. Their e-mail is
    verified, so `contractor_email="driver@example.com"` opens for them; the
    unverified case is covered in test_api_auth.py."""
    token = _register(api, "driver@example.com")
    _verify_email(api, "driver@example.com")
    return token


@pytest.fixture
def ready_template(api, admin_token, blank_template_bytes) -> str:
    response = api.post(
        "/api/templates",
        headers=auth(admin_token),
        files={"file": ("template.pdf", blank_template_bytes, "application/pdf")},
        data={"name": "Driver contract"},
    )
    assert response.status_code == 202, response.text
    template_id = response.json()["template_id"]
    detail = api.get(f"/api/templates/{template_id}", headers=auth(admin_token)).json()
    assert detail["status"] == "ready", detail
    return template_id


@pytest.fixture
def uploaded_contract(api, contractor_token, ready_template, filled_contract_bytes) -> str:
    response = api.post(
        "/api/contracts",
        headers=auth(contractor_token),
        files={"file": ("contract.pdf", filled_contract_bytes, "application/pdf")},
        data={"template_id": ready_template, "contractor_name": "Charles Frye"},
    )
    assert response.status_code == 202, response.text
    return response.json()["contract_id"]
