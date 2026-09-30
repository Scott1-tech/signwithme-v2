from datetime import date

import pytest

from app.services import validators as v


@pytest.mark.parametrize(
    "a,b",
    [
        ("Oh Rt305248", "OH-RT305248"),
        ("oh rt305248", "OHRT305248"),
    ],
)
def test_identifier_normalisation_absorbs_ocr_noise(a, b):
    """Raw string comparison would report these as a critical CDL mismatch."""
    assert v.normalize_identifier(a) == v.normalize_identifier(b)


def test_name_matching_tolerates_a_middle_name():
    assert v.names_match("CHARLES FRYE", "Charles  A. Frye")
    assert v.names_match("Frye, Charles", "CHARLES FRYE")
    assert not v.names_match("CHARLES FRYE", "MARIA LOPEZ")


@pytest.mark.parametrize("value", ["01/30/2030", "01.30.2030", "1/30/2030", "2030-01-30"])
def test_date_formats_seen_in_these_contracts_all_parse(value):
    assert v.parse_date(value) == date(2030, 1, 30)


def test_format_date_renders_us_style():
    assert v.format_date("2026-08-29") == "08/29/2026"


def test_blank_markers():
    for marker in ("", "  ", "N/A", "none", "EMPTY"):
        assert v.is_blank(marker)
    assert not v.is_blank("0")


def test_ssn_accepts_either_punctuation():
    assert v.check_ssn("123-45-6789") is None
    assert v.check_ssn("123456789") is None
    assert v.check_ssn("12345") is not None


def test_affirmative_values():
    assert v.is_affirmative("Yes") and v.is_affirmative("X")
    assert not v.is_affirmative("No") and not v.is_affirmative(None)
