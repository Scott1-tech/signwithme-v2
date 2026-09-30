"""Share-for-fill links (EDITOR-V2-PLAN §8, §11 Q4: LAN only, fill only).

* Token: `secrets.token_urlsafe(32)`, returned once by the create route; only
  `sha256(token)` is stored. Nothing here logs a token.
* Public access: the peer address (`request.client.host` -- X-Forwarded-For
  is ignored on purpose) must be inside `settings.share_allowed_cidrs`, else
  404. An unknown / expired / revoked / used-up token -> the SAME 404, and a
  failure for `share_throttle` (20 per 10 minutes per address -> 429).
* Values: validated exactly like a `field.value` op of the editor (B3:
  type, options, max_len, read-only, WinAnsi glyphs) against the pinned
  version, plus `required`; stored encrypted (`app.core.pii`).
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
from datetime import datetime, timedelta
from functools import lru_cache

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import pii
from app.core.exceptions import AppError
from app.models.contract import Contract
from app.models.document import Document, DocumentVersion
from app.models.share import FormSubmission, ShareLink
from app.schemas.document import FieldValueOp
from app.services import documents
from app.services.documents import DocumentError, now_utc
from app.services.storage import storage

SHAREABLE_TYPES = ("text", "multiline", "dropdown", "radio", "checkbox")
_FIELDS_CACHE: dict[str, list] = {}
_FIELDS_CACHE_MAX = 32


class TooManyAttempts(AppError):
    status_code = 429
    code = "too_many_attempts"


def not_found() -> AppError:
    """The one answer for every refused public request (§8: same 404)."""
    return DocumentError(404, "not_found", "Not found", {})


# -- tokens -----------------------------------------------------------------------


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()


def share_url(token: str) -> str:
    return f"{settings.share_base_url.rstrip('/')}/s#t={token}"


# -- LAN --------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _networks(spec: str) -> tuple:
    return tuple(ipaddress.ip_network(part.strip(), strict=False) for part in spec.split(",") if part.strip())


def peer_allowed(host: str | None) -> bool:
    """Is this socket peer address inside `share_allowed_cidrs`? A host that
    is not an IP address (a test client's "testclient", a unix socket) is not."""
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address.version == net.version and address in net for net in _networks(settings.share_allowed_cidrs))


# -- state ------------------------------------------------------------------------


def status_of(link: ShareLink, now: datetime | None = None) -> str:
    now = now or now_utc()
    if link.revoked_at is not None:
        return "revoked"
    if documents._aware(link.expires_at) <= now:
        return "expired"
    if link.submission_count >= link.max_submissions:
        return "used_up"
    return "active"


def usable(db: Session, link: ShareLink) -> bool:
    if status_of(link) != "active":
        return False
    contract = db.get(Contract, link.contract_id)
    return contract is not None and contract.status != "completed"


def submissions_left(link: ShareLink) -> int:
    return max(link.max_submissions - link.submission_count, 0)


# -- fields -----------------------------------------------------------------------


def version_fields(version: DocumentVersion) -> list[dict]:
    """`FieldOut` rows of a version (cached by file hash + page uids)."""
    from app.services import pdf_engine

    uid_by_index = {page.position - 1: page.page_uid for page in version.pages}
    key = version.file_hash + "|" + ",".join(uid for _, uid in sorted(uid_by_index.items()))
    cached = _FIELDS_CACHE.get(key)
    if cached is None:
        try:
            cached = pdf_engine.field_dicts(storage.get_bytes(version.file_path), uid_by_index)
        except pdf_engine.EngineError as exc:
            raise documents.from_engine(exc) from exc
        _FIELDS_CACHE[key] = cached
        while len(_FIELDS_CACHE) > _FIELDS_CACHE_MAX:
            _FIELDS_CACHE.pop(next(iter(_FIELDS_CACHE)))
    return cached


def shareable_problem(rows: list[dict]) -> str | None:
    """Why the widgets of one field name cannot be shared (None = they can)."""
    if not rows:
        return "unknown_field"
    if any(row["type"] not in SHAREABLE_TYPES for row in rows):
        return "field_not_fillable"
    if any(row["readonly"] for row in rows):
        return "field_readonly"
    if any(row["hidden"] for row in rows):
        return "field_hidden"
    return None


def check_allowed_fields(version: DocumentVersion, names: list[str]) -> None:
    """422 `fields_not_shareable` naming the POSITIONS of the refused names
    in the request (a name is never quoted back)."""
    fields = version_fields(version)
    problems = []
    for index, name in enumerate(names):
        reason = shareable_problem([row for row in fields if row["name"] == name])
        if reason:
            problems.append({"index": index, "reason": reason})
    if problems:
        raise DocumentError(422, "fields_not_shareable", "Some fields cannot be shared for filling",
                            {"fields": problems[:200]})


def _empty(value) -> bool:
    return value is None or value is False or (isinstance(value, str) and not value.strip()) or value == []


def value_ops(values: dict) -> tuple[list[dict], dict[str, str], list[dict]]:
    """`{name: value}` -> stored `field.value` op dicts, `{op_id: name}`, and
    schema problems `[{name, reason}]` (the value is never quoted)."""
    ops: list[dict] = []
    names: dict[str, str] = {}
    problems: list[dict] = []
    for n, (name, value) in enumerate(values.items()):
        op_id = f"share{n}"
        try:
            op = FieldValueOp(id=op_id, kind="field.value", name=name, value=value)
        except PydanticValidationError:
            problems.append({"name": name, "reason": "invalid_field_value"})
            continue
        ops.append(op.model_dump(mode="json"))
        names[op_id] = name
    return ops, names, problems


def validate_against(version: DocumentVersion, ops: list[dict], names: dict[str, str]) -> list[dict]:
    """The B3 engine check of every `field.value` op on `version` (type,
    options, max_len, read-only, drawable characters): `[{name, reason}]`."""
    from app.services import pdf_engine

    if not ops:
        return []
    try:
        problems, _warnings = pdf_engine.validate_ops(
            storage.get_bytes(version.file_path), documents.page_refs(version), pdf_engine.from_stored(ops)
        )
    except pdf_engine.EngineError as exc:
        raise documents.from_engine(exc) from exc
    return [{"name": names.get(p.get("id"), ""), "reason": p.get("reason")} for p in problems]


def validate_submission(version: DocumentVersion, allowed: list[str], values: dict) -> list[dict]:
    """Returns the stored ops; raises 422 `invalid_submission` with
    `{fields: [{name, reason}], not_allowed: n}` -- allowed names only, never
    a value, never a name the link does not allow (that is client text)."""
    if not values:
        raise DocumentError(422, "empty_submission", "Nothing was filled in", {})
    not_allowed = sum(1 for name in values if name not in allowed)
    wanted = {name: value for name, value in values.items() if name in allowed}
    ops, names, problems = value_ops(wanted)
    if not problems and not not_allowed:
        problems = validate_against(version, ops, names)
    fields = version_fields(version)
    for name in allowed:
        rows = [row for row in fields if row["name"] == name]
        if not rows or not any(row["required"] for row in rows):
            continue
        if name in wanted:
            missing = _empty(wanted[name])
        else:
            missing = all(_empty(row["value"]) for row in rows)
        if missing and not any(p["name"] == name for p in problems):
            problems.append({"name": name, "reason": "required"})
    if problems or not_allowed:
        raise DocumentError(
            422, "invalid_submission", "Some values cannot be accepted",
            {"fields": problems[:200], "not_allowed": not_allowed},
        )
    return ops


# -- rows -------------------------------------------------------------------------


def create_link(db: Session, contract: Contract, document: Document, user, body) -> tuple[ShareLink, str]:
    documents.assert_not_completed(contract)
    hours = body.expires_in_hours or settings.share_default_ttl_hours
    if hours > settings.share_max_ttl_hours:
        raise DocumentError(422, "expiry_too_long", "A share link lasts at most 14 days",
                            {"max_hours": settings.share_max_ttl_hours})
    version = db.get(DocumentVersion, document.current_version_id)
    check_allowed_fields(version, body.allowed_fields)
    token = new_token()
    link = ShareLink(
        document_id=document.id,
        contract_id=contract.id,
        version_id=version.id,
        purpose="fill",
        token_hash=token_hash(token),
        allowed_fields=list(body.allowed_fields),
        recipient_label=body.recipient_label,
        expires_at=now_utc() + timedelta(hours=hours),
        max_submissions=body.max_submissions,
        submission_count=0,
        created_by=user.id,
    )
    db.add(link)
    db.flush()
    return link, token


def encrypt_values(values: dict) -> str:
    return pii.encrypt(json.dumps(values, sort_keys=True, separators=(",", ":")))


def decrypt_values(blob: str) -> dict | None:
    plain = pii.decrypt(blob)
    if plain is None:
        return None
    try:
        data = json.loads(plain)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def new_submission(link: ShareLink, ops: list[dict], body, ip: str | None, user_agent: str | None) -> FormSubmission:
    values = {op["name"]: op["value"] for op in ops}
    return FormSubmission(
        share_link_id=link.id,
        document_id=link.document_id,
        version_id=link.version_id,
        values_encrypted=encrypt_values(values),
        field_count=len(values),
        submitter_name=body.submitter_name,
        submitter_email=body.submitter_email,
        ip_address=ip,
        user_agent=(user_agent or "")[:500] or None,
        status="received",
    )
