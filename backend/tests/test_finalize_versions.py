"""PDF editor v2, B1: the final PDF is built from the CURRENT version; a legacy
contract (no `documents` row) is finalised byte for byte as before."""
from __future__ import annotations

import io
import re
from uuid import UUID

from tests.conftest import auth
from tests.editor_v2_helpers import all_extractors, compact, edit_and_commit, get_document
from tests.test_api_annotations import _timeless
from tests.test_api_signing import _force_signable

TITLE = "INDEPENDENT CONTRACTOR AGREEMENT"


def _signature_data_url():
    import base64

    from PIL import Image

    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        for y in range(28, 34):
            image.putpixel((x, y), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _sign_and_finalize(api, token, contract_id):
    _force_signable(api, contract_id)
    placed = api.post(f"/api/contracts/{contract_id}/signatures", headers=auth(token),
                      json={"field_id": "p1_contractor_signature", "image_data": _signature_data_url()})
    assert placed.status_code == 201, placed.text
    response = api.post(f"/api/contracts/{contract_id}/finalize", headers=auth(token),
                        json={"contract_date": "2026-09-29"})
    assert response.status_code == 200, response.text
    return response.json()


def _final(api, token, contract_id) -> bytes:
    return api.get(f"/api/download/{contract_id}/final", headers=auth(token)).content


def _audit(api, contract_id, action):
    from sqlalchemy import select

    from app.models.audit import AuditLog

    db = api.db_factory()
    try:
        return [row.details for row in db.scalars(
            select(AuditLog).where(AuditLog.contract_id == UUID(contract_id), AuditLog.action == action))]
    finally:
        db.close()


def _image_draws(pdf: bytes) -> int:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return sum(len(page.images) for page in document.pages)


def test_final_is_built_from_the_current_version(api, contractor_token, uploaded_contract):
    committed = edit_and_commit(api, contractor_token, uploaded_contract, TITLE, "SERVICES AGREEMENT")
    assert committed.status_code == 201, committed.text
    version = committed.json()["version"]
    result = _sign_and_finalize(api, contractor_token, uploaded_contract)

    final = _final(api, contractor_token, uploaded_contract)
    for text in all_extractors(final):
        assert "INDEPENDENT" not in text
        assert "SERVICESAGREEMENT" in compact(text)
    finalized = _audit(api, uploaded_contract, "contract_finalized")[0]
    assert finalized["source_version_no"] == 2
    assert finalized["source_file_hash"] == version["file_hash"]
    assert finalized["final_file_hash"] == result["file_hash"]
    # The signature is drawn exactly once: the version PDF never carries one.
    assert _image_draws(final) == 1


def test_finalize_reads_the_version_not_the_original(api, contractor_token, uploaded_contract, monkeypatch):
    from app.services import storage as storage_module

    assert edit_and_commit(api, contractor_token, uploaded_contract, TITLE, "SERVICES AGREEMENT").status_code == 201
    reads: list[str] = []
    real = storage_module.storage.get_bytes

    def spy(key):
        reads.append(key)
        return real(key)

    monkeypatch.setattr(storage_module.storage, "get_bytes", spy)
    _sign_and_finalize(api, contractor_token, uploaded_contract)
    pdf_reads = [key for key in reads if key.endswith(".pdf") and not key.startswith(("templates/", "final/"))]
    assert pdf_reads and all(re.search(r"/version-2-[0-9a-f]{8}\.pdf$", key) for key in pdf_reads), pdf_reads


def test_legacy_finalize_is_byte_for_byte_unchanged(api, contractor_token, ready_template, filled_contract_bytes, monkeypatch):
    """Same upload twice: one contract never touches the editor (legacy), the
    other has a document with only version 1. Their finals are identical
    (modulo /CreationDate), and the legacy one reads `contracts.file_path`."""
    from app.services import storage as storage_module

    ids = []
    for _ in range(2):
        response = api.post(
            "/api/contracts",
            headers=auth(contractor_token),
            files={"file": ("contract.pdf", filled_contract_bytes, "application/pdf")},
            data={"template_id": ready_template, "contractor_name": "Charles Frye"},
        )
        ids.append(response.json()["contract_id"])
    legacy, with_document = ids
    get_document(api, contractor_token, with_document)

    reads: list[str] = []
    real = storage_module.storage.get_bytes
    monkeypatch.setattr(storage_module.storage, "get_bytes", lambda key: (reads.append(key), real(key))[1])
    _sign_and_finalize(api, contractor_token, legacy)
    assert f"contracts/{legacy}/original.pdf" in reads
    _sign_and_finalize(api, contractor_token, with_document)

    a = _timeless(_final(api, contractor_token, legacy))
    b = _timeless(_final(api, contractor_token, with_document))
    assert a == b
    legacy_audit = _audit(api, legacy, "contract_finalized")[0]
    assert "source_version_no" not in legacy_audit and "source_file_hash" not in legacy_audit


def test_legacy_contract_gets_no_page_uid_and_old_date_fields(api, contractor_token, uploaded_contract):
    from app.models.signature import Signature

    fields = api.get(f"/api/contracts/{uploaded_contract}/date-fields", headers=auth(contractor_token)).json()
    assert fields and all("page_uid" not in field for field in fields)
    _force_signable(api, uploaded_contract)
    api.post(f"/api/contracts/{uploaded_contract}/signatures", headers=auth(contractor_token),
             json={"field_id": "p1_contractor_signature", "image_data": _signature_data_url()})
    db = api.db_factory()
    try:
        assert db.query(Signature).one().page_uid is None
    finally:
        db.close()
    from app.models.document import Document

    db = api.db_factory()
    try:
        assert db.query(Document).count() == 0  # nothing created behind the user's back
    finally:
        db.close()


def test_page_count_comes_from_the_version(api, contractor_token, uploaded_contract):
    from app.models.contract import Contract
    from app.services.rule_locator import contract_page_count

    edit_and_commit(api, contractor_token, uploaded_contract, TITLE, "SERVICES AGREEMENT")
    db = api.db_factory()
    try:
        assert contract_page_count(db.get(Contract, UUID(uploaded_contract))) == 1
    finally:
        db.close()
    fields = api.get(f"/api/contracts/{uploaded_contract}/date-fields", headers=auth(contractor_token)).json()
    assert all(re.fullmatch(r"pg_[0-9a-f]{12}", field["page_uid"]) for field in fields if field["page"])
