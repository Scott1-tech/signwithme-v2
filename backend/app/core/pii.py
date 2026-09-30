"""Handling for personally identifying values.

A driver file carries SSNs, EINs and dates of birth. Three rules apply
everywhere those values travel:

* at rest they are encrypted, not stored as plaintext;
* in API responses they are masked unless an admin explicitly reveals them,
  and revealing is itself an audited event;
* they never reach the audit log or the comparison result, both of which are
  long-lived JSON blobs that nobody expects to contain a social security number.
"""
from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

# Field-id fragments that mark a value as sensitive.
SENSITIVE_TOKENS = (
    "ssn",
    "social_security",
    "ein",
    "tax_id",
    "date_of_birth",
    "dob",
    "birth",
    "bank",
    "routing",
    "account_number",
    "passport",
)

REDACTED = "[redacted]"
_ENCRYPTED_PREFIX = "enc:v1:"


def is_sensitive(field_id: str | None) -> bool:
    if not field_id:
        return False
    lowered = field_id.lower()
    return any(token in lowered for token in SENSITIVE_TOKENS)


def _key() -> bytes:
    """Derive a Fernet key from the configured secret.

    A dedicated PII_ENCRYPTION_KEY is preferred; falling back to the JWT secret
    keeps a single-operator deployment working without a second secret to lose.
    """
    material = settings.pii_encryption_key or settings.jwt_secret
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    token = Fernet(_key()).encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_ENCRYPTED_PREFIX}{token}"


def decrypt(value: str | None) -> str | None:
    if value is None or not value.startswith(_ENCRYPTED_PREFIX):
        return value  # written before encryption was enabled, or not sensitive
    try:
        return Fernet(_key()).decrypt(value[len(_ENCRYPTED_PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken:
        # The key changed. Refusing to guess is safer than handing back
        # ciphertext that a caller would render as if it were the real value.
        return None


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(_ENCRYPTED_PREFIX)


def mask(value: str | None, field_id: str | None = None) -> str | None:
    """Show enough to recognise the value, never enough to use it."""
    if value is None or value == "":
        return value
    text = str(value)
    if is_encrypted(text):
        return REDACTED
    if not is_sensitive(field_id):
        return text
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 4:
        return f"XXX-XX-{digits[-4:]}" if len(digits) == 9 else f"****{digits[-4:]}"
    return REDACTED


def redact_mapping(values: dict[str, Any]) -> dict[str, Any]:
    """Mask every sensitive entry in a {field_id: value} mapping."""
    return {
        field_id: (mask(value, field_id) if is_sensitive(field_id) else value)
        for field_id, value in values.items()
    }


def redact_audit_details(details: dict[str, Any]) -> dict[str, Any]:
    """Scrub an audit payload.

    Correcting a misread SSN would otherwise write both the old and the new
    value into an append-only table, permanently.
    """
    field_id = details.get("field_id")
    if not is_sensitive(field_id if isinstance(field_id, str) else None):
        return details
    scrubbed = dict(details)
    for key in ("from", "to", "value", "raw_value", "normalized_value"):
        if key in scrubbed and scrubbed[key] is not None:
            scrubbed[key] = mask(str(scrubbed[key]), field_id)
    return scrubbed
