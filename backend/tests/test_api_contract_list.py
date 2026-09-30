"""The dashboard list: GET /api/contracts.

The list is what the home screen renders, so it has to answer "which template
is this?" and "how bad is it?" for every row at once. These tests pin the two
properties that makes it usable: the derived fields are right, and the number
of statements the endpoint runs does not depend on how many contracts come
back.

Contracts are inserted straight into the database here. Going through the real
upload would run the parser once per row, which says nothing extra about a
read-only list endpoint and would make the query-count test cost seconds per
contract; what matters is the stored `comparison_result`, and that is written
verbatim.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

from sqlalchemy import delete, event, select

from tests.conftest import auth


def _comparison(
    *,
    can_sign: bool = False,
    slots: int = 4,
    critical_missing: int = 3,
    critical_inconsistencies: int = 2,
    critical_fmcsa: int = 1,
) -> dict:
    """A stored comparison with a known critical/non-critical mix.

    Every category carries warnings as well, so a count that simply took
    `len()` of the list would be caught.
    """

    def entries(critical: int, warnings: int, body) -> list[dict]:
        return [body(i, "critical") for i in range(critical)] + [
            body(i, "warning") for i in range(critical, critical + warnings)
        ]

    return {
        "contract_id": None,
        "completion_percentage": 72.5,
        "missing_required": entries(
            critical_missing,
            2,
            lambda i, severity: {
                "field_id": f"p1_field_{i}",
                "label": f"Field {i}",
                "page": 1,
                "type": "text",
                "severity": severity,
            },
        ),
        "inconsistencies": entries(
            critical_inconsistencies,
            1,
            lambda i, severity: {
                "type": f"mismatch_{i}",
                "issue": "differs between pages",
                "values": {"p1_cdl": "OH-RT305248"},
                "pages": [1, 2],
                "severity": severity,
            },
        ),
        "fmcsa_violations": entries(
            critical_fmcsa,
            1,
            lambda i, severity: {
                "rule": f"391.2{i}",
                "issue": "required disclosure is blank",
                "page": 2,
                "field_ids": [f"p2_field_{i}"],
                "severity": severity,
            },
        ),
        "warnings": [],
        "signature_slots": [
            {
                "field_id": f"sig_{i}",
                "label": "Contractor Signature",
                "page": 1,
                "kind": "signature",
                "required": True,
                "severity": "info",
                "rule_y": 100.0,
            }
            for i in range(slots)
        ],
        "can_sign": can_sign,
        "field_count": 40,
        "filled_count": 29,
    }


def _user_id(db, email: str):
    from app.models.user import User

    return db.scalar(select(User).where(User.email == email)).id


def _seed_template(api, owner_email: str, name: str = "Driver package", status: str = "ready") -> str:
    from app.models.template import Template

    db = api.db_factory()
    try:
        template = Template(
            id=uuid.uuid4(),
            name=name,
            file_path="templates/seed.pdf",
            file_hash="a" * 64,
            page_count=3,
            field_schema={"pages": [], "field_count": 40},
            status=status,
            created_by=_user_id(db, owner_email),
        )
        db.add(template)
        db.commit()
        return str(template.id)
    finally:
        db.close()


def _seed_contract(
    api,
    uploader_email: str,
    template_id: str,
    comparison: dict | None = None,
    *,
    review_override: bool = False,
    status: str = "analyzed",
) -> str:
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        contract = Contract(
            id=uuid.uuid4(),
            template_id=uuid.UUID(template_id),
            contractor_name="Charles Frye",
            contractor_email="charles@example.com",
            file_path=f"contracts/{uuid.uuid4()}.pdf",
            file_hash=f"{uuid.uuid4().hex}{uuid.uuid4().hex}",
            status=status,
            extracted_data={},
            comparison_result=comparison or {},
            review_override=review_override,
            uploaded_by=_user_id(db, uploader_email),
        )
        db.add(contract)
        db.commit()
        return str(contract.id)
    finally:
        db.close()


def _row(api, token: str, contract_id: str) -> dict:
    response = api.get("/api/contracts", headers=auth(token))
    assert response.status_code == 200, response.text
    matches = [row for row in response.json() if row["id"] == contract_id]
    assert len(matches) == 1, response.text
    return matches[0]


# -- derived fields ----------------------------------------------------------


def test_a_row_carries_the_template_name_and_its_critical_counts(api, admin_token):
    template_id = _seed_template(api, "admin@example.com")
    contract_id = _seed_contract(
        api, "admin@example.com", template_id, _comparison(can_sign=False, slots=4)
    )

    row = _row(api, admin_token, contract_id)
    assert row["template_name"] == "Driver package"
    # Warnings in the same categories are not counted.
    assert row["critical_missing_count"] == 3
    assert row["critical_inconsistency_count"] == 2
    assert row["critical_fmcsa_count"] == 1
    assert row["can_sign"] is False
    assert row["review_override"] is False
    assert row["stored_signature_slot_count"] == 4


def test_a_signable_contract_reports_can_sign_and_a_recorded_override(api, admin_token):
    template_id = _seed_template(api, "admin@example.com")
    clean = _seed_contract(
        api,
        "admin@example.com",
        template_id,
        _comparison(can_sign=True, critical_missing=0, critical_inconsistencies=0, critical_fmcsa=0),
    )
    overridden = _seed_contract(
        api, "admin@example.com", template_id, _comparison(can_sign=False), review_override=True
    )

    assert _row(api, admin_token, clean)["can_sign"] is True
    assert _row(api, admin_token, clean)["critical_missing_count"] == 0
    assert _row(api, admin_token, clean)["review_override"] is False

    # can_sign stays the engine's own verdict; the override is reported beside
    # it, not folded into it.
    row = _row(api, admin_token, overridden)
    assert row["can_sign"] is False
    assert row["review_override"] is True
    assert row["critical_missing_count"] == 3


def test_a_contract_without_a_comparison_reports_null_not_zero(api, admin_token):
    """A contract still parsing has no counts -- and "no counts" must not read
    as "nothing is wrong"."""
    template_id = _seed_template(api, "admin@example.com")
    contract_id = _seed_contract(api, "admin@example.com", template_id, {}, status="parsing")

    row = _row(api, admin_token, contract_id)
    assert row["critical_missing_count"] is None
    assert row["critical_inconsistency_count"] is None
    assert row["critical_fmcsa_count"] is None
    assert row["can_sign"] is None
    assert row["stored_signature_slot_count"] is None
    # The parts that come from columns are still there.
    assert row["template_name"] == "Driver package"
    assert row["review_override"] is False
    assert row["status"] == "parsing"


def test_a_comparison_stored_before_slots_existed_reports_no_slot_count(api, admin_token):
    template_id = _seed_template(api, "admin@example.com")
    old = _comparison()
    del old["signature_slots"]
    contract_id = _seed_contract(api, "admin@example.com", template_id, old)

    row = _row(api, admin_token, contract_id)
    assert row["stored_signature_slot_count"] is None
    assert row["critical_missing_count"] == 3  # the rest still counts


def test_the_list_carries_counts_but_never_the_comparison_itself(api, admin_token):
    """Numbers travel; labels, values and field ids do not."""
    template_id = _seed_template(api, "admin@example.com")
    contract_id = _seed_contract(api, "admin@example.com", template_id, _comparison())

    row = _row(api, admin_token, contract_id)
    assert "comparison_result" not in row
    assert "extracted_data" not in row
    assert "OH-RT305248" not in str(row)
    assert "p1_field_0" not in str(row)


# -- the template side of the join ------------------------------------------


def test_an_archived_template_still_names_its_contracts(api, admin_token):
    template_id = _seed_template(api, "admin@example.com", name="Retired package")
    contract_id = _seed_contract(api, "admin@example.com", template_id, _comparison())
    api.delete(f"/api/templates/{template_id}", headers=auth(admin_token))

    row = _row(api, admin_token, contract_id)
    assert row["template_name"] == "Retired package"
    assert row["critical_missing_count"] == 3


def test_a_contract_whose_template_row_is_gone_is_still_listed(api, admin_token):
    """Defensive: the FK is RESTRICT and purge refuses while contracts point at
    the template, so this should not happen -- but if it ever did, the outer
    join must cost a null name, not a missing contract."""
    from app.models.template import Template

    template_id = _seed_template(api, "admin@example.com")
    contract_id = _seed_contract(api, "admin@example.com", template_id, _comparison())

    db = api.db_factory()
    try:
        # A Core DELETE: the ORM's own cascade would try to null the
        # contract's NOT NULL template_id instead of orphaning the row.
        db.execute(delete(Template).where(Template.id == uuid.UUID(template_id)))
        db.commit()
    finally:
        db.close()

    row = _row(api, admin_token, contract_id)
    assert row["template_name"] is None
    assert row["critical_missing_count"] == 3


# -- one query, whatever the row count --------------------------------------


@contextmanager
def _recorded_statements(api):
    engine = api.db_factory.kw["bind"]
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_listing_ten_contracts_costs_the_same_queries_as_listing_one(api, admin_token):
    """The N+1 guard. Anything that reaches back to the database per row -- a
    template lookup, a lazy `contract.template`, a per-contract comparison read
    -- shows up here as a query count that grows with the list."""
    template_id = _seed_template(api, "admin@example.com")
    _seed_contract(api, "admin@example.com", template_id, _comparison())

    with _recorded_statements(api) as one_row:
        assert len(api.get("/api/contracts", headers=auth(admin_token)).json()) == 1
    assert one_row, "no statements were recorded; the listener never attached"

    for _ in range(9):
        _seed_contract(api, "admin@example.com", template_id, _comparison())

    with _recorded_statements(api) as ten_rows:
        body = api.get("/api/contracts", headers=auth(admin_token)).json()
    assert len(body) == 10
    assert all(row["template_name"] == "Driver package" for row in body)

    assert len(ten_rows) == len(one_row), (
        f"query count grew with the list: {len(one_row)} -> {len(ten_rows)}\n"
        + "\n".join(ten_rows)
    )
    assert len([s for s in ten_rows if "FROM contracts" in s]) == 1
    assert not [s for s in ten_rows if "FROM templates" in s and "FROM contracts" not in s]


# -- who sees what -----------------------------------------------------------


def test_a_contractor_still_sees_only_their_own_contracts(api, admin_token, contractor_token):
    template_id = _seed_template(api, "admin@example.com")
    mine = _seed_contract(api, "driver@example.com", template_id, _comparison())
    _seed_contract(api, "admin@example.com", template_id, _comparison())

    response = api.get("/api/contracts", headers=auth(contractor_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body] == [mine]
    # The enrichment reaches the contractor's own row too.
    assert body[0]["template_name"] == "Driver package"
    assert body[0]["critical_fmcsa_count"] == 1

    assert len(api.get("/api/contracts", headers=auth(admin_token)).json()) == 2


def test_the_template_filter_keeps_working(api, admin_token):
    first = _seed_template(api, "admin@example.com", name="First")
    second = _seed_template(api, "admin@example.com", name="Second")
    _seed_contract(api, "admin@example.com", first, _comparison())
    wanted = _seed_contract(api, "admin@example.com", second, _comparison())

    body = api.get(
        "/api/contracts", headers=auth(admin_token), params={"template_id": second}
    ).json()
    assert [row["id"] for row in body] == [wanted]
    assert body[0]["template_name"] == "Second"


# -- the detail response is not dragged along --------------------------------


def test_the_contract_detail_response_is_unchanged(api, admin_token, uploaded_contract):
    """The new fields live on the list schema only.

    Putting them on `ContractOut` would have inherited them into
    `ContractDetailOut`, where the row has no such attributes and every one of
    them would serialise as null -- next to a full `comparison_result` that
    plainly contradicts them.
    """
    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token)).json()
    assert "comparison_result" in detail
    for field in (
        "template_name",
        "critical_missing_count",
        "critical_inconsistency_count",
        "critical_fmcsa_count",
        "can_sign",
        "stored_signature_slot_count",
    ):
        assert field not in detail, field
