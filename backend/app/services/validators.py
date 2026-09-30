"""Value normalisation and per-field / FMCSA validation rules.

Normalisation matters as much as validation here: the comparison engine's
cross-field checks compare CDL numbers and dates across pages, and OCR routinely
varies casing, spacing and punctuation. Comparing raw strings would raise
critical mismatches on noise alone.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from dateutil import parser as date_parser

EMPTY_MARKERS = {"", "-", "--", "n/a", "na", "none", "empty", "null", "x/x", "not applicable"}
# A fill placeholder with its underscores stripped: "__/__/____" -> "//",
# "___-___" -> "-", "..." -- nothing but rule/separator characters.
PLACEHOLDER_RE = re.compile(r"^[\s/\-_.]*$")

DATE_FORMAT_HINTS = ("%m/%d/%Y", "%m.%d.%Y", "%m-%d-%Y", "%Y-%m-%d")

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")
CDL_RE = re.compile(r"^[A-Z]{0,2}[A-Z0-9]{5,14}$")
SSN_RE = re.compile(r"^\d{9}$")
EIN_RE = re.compile(r"^\d{9}$")
PHONE_RE = re.compile(r"^\d{10,11}$")


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip().lower()
    return text in EMPTY_MARKERS or PLACEHOLDER_RE.fullmatch(text) is not None


def normalize_text(value: Any) -> str | None:
    if is_blank(value):
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_identifier(value: Any) -> str | None:
    """Uppercase, strip every non-alphanumeric character.

    'Oh Rt305248' and 'OH-RT305248' both normalise to 'OHRT305248', so they no
    longer read as a cross-page CDL mismatch.
    """
    if is_blank(value):
        return None
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).upper() or None


def normalize_digits(value: Any) -> str | None:
    if is_blank(value):
        return None
    return re.sub(r"\D", "", str(value)) or None


def normalize_name(value: Any) -> str | None:
    """Uppercase, collapse whitespace, drop punctuation, and reorder 'LAST, FIRST'."""
    if is_blank(value):
        return None
    text = re.sub(r"[^A-Za-z,\s]", " ", str(value))
    if "," in text:
        last, _, first = text.partition(",")
        text = f"{first} {last}"
    text = re.sub(r"\s+", " ", text).strip().upper()
    return text or None


def name_tokens(value: Any) -> set[str]:
    normalized = normalize_name(value)
    if not normalized:
        return set()
    return {t for t in normalized.split(" ") if len(t) > 1}


def names_match(a: Any, b: Any) -> bool:
    """True when both names agree, tolerating an added or omitted middle name."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return True
    return ta.issubset(tb) or tb.issubset(ta)


def parse_date(value: Any) -> date | None:
    """Parse the formats seen in these contracts: 01/30/2030, 01.01.2029, 11-3-2025."""
    if is_blank(value):
        return None
    text = str(value).strip().replace(".", "/").replace("-", "/")
    try:
        return date_parser.parse(text, dayfirst=False).date()
    except (ValueError, OverflowError, TypeError):
        return None


def is_valid_date(value: Any) -> bool:
    return parse_date(value) is not None


def format_date(value: date | str, fmt: str = "%m/%d/%Y") -> str:
    """Render a date the way the rest of the contract writes them (MM/DD/YYYY)."""
    parsed = value if isinstance(value, date) else parse_date(value)
    if parsed is None:
        raise ValueError(f"Cannot format value as a date: {value!r}")
    return parsed.strftime(fmt)


def is_affirmative(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"yes", "y", "x", "true", "checked", "1"}


# --- Per-field format checks -------------------------------------------------
# Each returns an error message, or None when the value is acceptable.


def check_email(value: Any) -> str | None:
    normalized = normalize_text(value)
    if normalized and not EMAIL_RE.match(normalized):
        return "Invalid email format"
    return None


def check_cdl(value: Any) -> str | None:
    normalized = normalize_identifier(value)
    if normalized and not CDL_RE.match(normalized):
        return "CDL number does not look like a valid licence number"
    return None


def check_ssn(value: Any) -> str | None:
    digits = normalize_digits(value)
    if digits is None:
        return None
    if not SSN_RE.match(digits):
        return "SSN must contain exactly 9 digits (XXX-XX-XXXX)"
    return None


def check_ein(value: Any) -> str | None:
    digits = normalize_digits(value)
    if digits is None:
        return None
    if not EIN_RE.match(digits):
        return "EIN must contain exactly 9 digits (XX-XXXXXXX)"
    return None


def check_phone(value: Any) -> str | None:
    digits = normalize_digits(value)
    if digits is None:
        return None
    if not PHONE_RE.match(digits):
        return "Phone number must contain 10 digits"
    return None


def check_date(value: Any) -> str | None:
    if not is_blank(value) and parse_date(value) is None:
        return "Invalid or ambiguous date format"
    return None


def check_expiry_in_future(value: Any, today: date | None = None) -> str | None:
    parsed = parse_date(value)
    if parsed is None:
        return None
    if parsed <= (today or date.today()):
        return "Expiration date is in the past"
    return None
