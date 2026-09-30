"""Dialect-portable column types.

Production runs on PostgreSQL, but the test suite runs on SQLite so the whole
API can be exercised without standing up a database. These variants keep one
set of models working on both: JSONB/JSON, native UUID/CHAR, INET/VARCHAR.
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

JSONType = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
UUIDType = sa.Uuid(as_uuid=True)
INETType = postgresql.INET().with_variant(sa.String(45), "sqlite")


class UTCDateTime(sa.types.TypeDecorator):
    """`DateTime(timezone=True)` that always comes back timezone-aware, in UTC.

    PostgreSQL's `timestamptz` already round-trips the offset. SQLite has no
    timezone type: `CURRENT_TIMESTAMP` (every `server_default=func.now()`)
    writes UTC wall-clock text such as `2026-09-24 22:15:28`, and SQLAlchemy
    reads it back as a NAIVE datetime. The API then serialised it without an
    offset (`2026-09-24T22:15:28`), and a browser reads an offset-less ISO
    date-time as LOCAL time -- five hours early in Tashkent (UTC+5).

    So: an aware value is normalised to UTC before it is stored (SQLite keeps
    only the wall clock), and a naive value read back is labelled UTC -- which
    is what every writer in this app stores (`func.now()` on SQLite is UTC,
    `datetime.now(timezone.utc)` elsewhere). The DDL is unchanged.
    """

    impl = sa.DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value
