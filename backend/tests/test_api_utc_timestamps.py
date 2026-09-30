"""Every timestamp the API returns carries its UTC offset.

SQLite (this machine's `local.db`, and the test database) has no timezone
type: `CURRENT_TIMESTAMP` stores UTC wall-clock text and SQLAlchemy used to read
it back NAIVE, so the API sent `2026-09-24T22:15:28`. A browser reads an
offset-less ISO date-time as LOCAL time, so the audit trail on the final page
showed 10:15 PM for an action taken at 03:15 in Tashkent (UTC+5).
`models.types.UTCDateTime` labels those values UTC on the way out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text

from tests.conftest import auth


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, f"{value!r} has no UTC offset"
    assert parsed.utcoffset() == timedelta(0), f"{value!r} is not UTC"
    return parsed


def test_audit_contract_and_list_timestamps_are_utc_aware(api, admin_token, uploaded_contract):
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    assert trail, "the upload itself is audited"
    for entry in trail:
        _utc(entry["timestamp"])

    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token)).json()
    _utc(detail["created_at"])
    _utc(detail["updated_at"])

    listed = api.get("/api/contracts", headers=auth(admin_token)).json()
    rows = listed if isinstance(listed, list) else listed.get("items", [])
    assert rows
    for row in rows:
        _utc(row["created_at"])


def test_a_naive_stored_value_is_read_as_utc_not_shifted(api, admin_token, uploaded_contract):
    """The exact shape SQLite writes: `YYYY-MM-DD HH:MM:SS`, no offset."""
    db = api.db_factory()
    try:
        db.execute(
            text("UPDATE audit_logs SET timestamp = '2026-09-24 22:15:28' WHERE contract_id = :cid"),
            {"cid": UUID(uploaded_contract).hex},
        )
        db.commit()
    finally:
        db.close()

    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    assert trail
    for entry in trail:
        # 22:15:28 UTC -- the wall clock is kept, only the offset is added.
        assert _utc(entry["timestamp"]) == datetime(2026, 9, 24, 22, 15, 28, tzinfo=timezone.utc)


def test_an_aware_non_utc_value_is_stored_as_utc(api):
    """`reviewed_at` is written from Python; a non-UTC aware value is normalised."""
    from app.models.types import UTCDateTime

    column = UTCDateTime(timezone=True)
    tashkent = timezone(timedelta(hours=5))
    bound = column.process_bind_param(datetime(2026, 9, 25, 3, 15, 28, tzinfo=tashkent), None)
    assert bound == datetime(2026, 9, 24, 22, 15, 28, tzinfo=timezone.utc)
    assert bound.utcoffset() == timedelta(0)
    assert column.process_bind_param(None, None) is None
    assert column.process_result_value(None, None) is None
