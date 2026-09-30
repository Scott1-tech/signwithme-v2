import copy

import pytest

from app.services.comparison_engine import ComparisonEngine, _index_schema, pending_signature_slots

SCHEMA = {
    "pages": [
        {
            "page_number": 1,
            "fields": [
                {"field_id": "p1_full_name", "label": "Full Name", "field_type": "text_line",
                 "page": 1, "required": True, "bbox": {"x": 1, "y": 1, "width": 10, "height": 10}},
                {"field_id": "p1_cdl", "label": "CDL", "field_type": "text_line", "page": 1,
                 "required": True, "bbox": {"x": 1, "y": 20, "width": 10, "height": 10}},
                {"field_id": "p1_email", "label": "Email", "field_type": "text_line", "page": 1,
                 "required": False, "bbox": {"x": 1, "y": 40, "width": 10, "height": 10}},
            ],
        },
        {
            "page_number": 2,
            "fields": [
                {"field_id": "p2_full_legal_name", "label": "Full Legal Name",
                 "field_type": "text_line", "page": 2, "required": True,
                 "bbox": {"x": 1, "y": 1, "width": 10, "height": 10}},
                {"field_id": "p2_cdl_license", "label": "CDL", "field_type": "text_line", "page": 2,
                 "required": True, "bbox": {"x": 1, "y": 20, "width": 10, "height": 10}},
                {"field_id": "p2_dot_eligible", "label": "DOT Eligible", "field_type": "checkbox",
                 "page": 2, "required": True, "bbox": {"x": 1, "y": 40, "width": 10, "height": 10}},
                {"field_id": "p2_cdl_valid", "label": "CDL Valid", "field_type": "checkbox",
                 "page": 2, "required": True, "bbox": {"x": 1, "y": 60, "width": 10, "height": 10}},
            ],
        },
    ]
}


def value(text):
    return {"value": text, "confidence": 1.0, "detected": True}


def complete_data():
    return {
        "p1_full_name": value("CHARLES FRYE"),
        "p1_cdl": value("Oh Rt305248"),
        "p1_email": value("charles@example.com"),
        "p2_full_legal_name": value("Charles Frye"),
        "p2_cdl_license": value("OH-RT305248"),
        "p2_dot_eligible": {"checked": True, "confidence": 0.9, "detected": True},
        "p2_cdl_valid": {"checked": True, "confidence": 0.9, "detected": True},
    }


def test_a_complete_consistent_contract_can_be_signed():
    result = ComparisonEngine().compare(complete_data(), SCHEMA)
    assert result.missing_required == []
    assert result.inconsistencies == []
    assert result.can_sign is True
    assert result.completion_percentage == 100.0


def test_cdl_casing_and_punctuation_do_not_raise_a_false_mismatch():
    """'Oh Rt305248' and 'OH-RT305248' are the same licence. Comparing raw
    strings would block signing on OCR noise."""
    result = ComparisonEngine().compare(complete_data(), SCHEMA)
    assert not [i for i in result.inconsistencies if i["type"] == "cdl_mismatch"]


def test_a_genuine_cdl_mismatch_is_critical():
    data = complete_data()
    data["p2_cdl_license"] = value("TX9999999")
    result = ComparisonEngine().compare(data, SCHEMA)
    mismatch = next(i for i in result.inconsistencies if i["type"] == "cdl_mismatch")
    assert mismatch["severity"] == "critical"
    # Pages are stated explicitly so the review UI can filter without parsing ids.
    assert mismatch["pages"] == [1, 2]
    assert result.can_sign is False


def test_a_middle_name_is_not_a_name_mismatch():
    data = complete_data()
    data["p2_full_legal_name"] = value("Charles A. Frye")
    result = ComparisonEngine().compare(data, SCHEMA)
    assert not [i for i in result.inconsistencies if i["type"] == "name_mismatch"]


def test_a_different_person_is_a_name_mismatch():
    data = complete_data()
    data["p2_full_legal_name"] = value("Maria Lopez")
    result = ComparisonEngine().compare(data, SCHEMA)
    assert [i for i in result.inconsistencies if i["type"] == "name_mismatch"]


def test_missing_required_fields_carry_coordinates():
    """The highlighter cannot draw a box from a label alone."""
    data = complete_data()
    del data["p1_cdl"]
    result = ComparisonEngine().compare(data, SCHEMA)
    missing = next(m for m in result.missing_required if m["field_id"] == "p1_cdl")
    assert missing["bbox"] == {"x": 1, "y": 20, "width": 10, "height": 10}
    assert result.can_sign is False


@pytest.mark.parametrize("field_id", ["p2_dot_eligible", "p2_cdl_valid"])
def test_an_absent_compliance_answer_is_a_violation_not_a_pass(field_id):
    """`if value and value != 'yes'` silently approves a blank box; a blank
    DOT-eligibility answer must fail."""
    data = complete_data()
    del data[field_id]
    result = ComparisonEngine().compare(data, SCHEMA)
    assert [v for v in result.fmcsa_violations if field_id in v["field_ids"]]
    assert result.can_sign is False


def test_rules_for_fields_the_template_lacks_are_skipped():
    """The equipment-experience rule names page-4 fields this template has no
    trace of; asserting on them would fail every contract."""
    result = ComparisonEngine().compare(complete_data(), SCHEMA)
    assert not [v for v in result.fmcsa_violations if v["rule"] == "fmcsa_equipment_experience"]


def test_format_warnings_do_not_block_signing():
    data = complete_data()
    data["p1_email"] = value("not-an-email")
    result = ComparisonEngine().compare(data, SCHEMA)
    assert [w for w in result.warnings if w["field_id"] == "p1_email"]
    assert result.can_sign is True


def test_format_checks_apply_only_to_text_and_date_fields():
    """A Yes/No checkbox group whose id mentions 'cdl' carries the value
    'yes' (its selected option). Running the CDL format check on that invents
    a warning about a licence number that was never written there."""
    schema = copy.deepcopy(SCHEMA)
    schema["pages"][1]["fields"].append(
        {"field_id": "p2_is_your_cdl_currently_valid_and_unrestricted", "label": "CDL valid?",
         "field_type": "checkbox_group", "page": 2, "required": True,
         "bbox": {"x": 1, "y": 80, "width": 10, "height": 10},
         "options": [{"id": "yes", "label": "Yes", "bbox": {"x": 1, "y": 80, "width": 5, "height": 5}}]}
    )
    data = complete_data()
    data["p2_is_your_cdl_currently_valid_and_unrestricted"] = {
        "value": "yes", "selected": ["yes"], "confidence": 0.9, "detected": True,
    }
    result = ComparisonEngine().compare(data, schema)
    assert not [w for w in result.warnings if w["field_id"] == "p2_is_your_cdl_currently_valid_and_unrestricted"]
    assert result.missing_required == []
    # The text-line check itself is untouched: the same 'yes' on a real CDL line warns.
    data["p1_cdl"] = value("yes")
    assert [w for w in ComparisonEngine().compare(data, schema).warnings if w["field_id"] == "p1_cdl"]


def test_warnings_use_the_same_shape_as_inconsistencies():
    """One payload shape means the sidebar renders both from one type."""
    data = complete_data()
    data["p1_email"] = value("not-an-email")
    result = ComparisonEngine().compare(data, SCHEMA)
    warning = result.warnings[0]
    assert {"type", "issue", "values", "pages", "severity"} <= set(warning)


def test_completion_percentage_counts_filled_fields():
    data = complete_data()
    del data["p1_email"]
    result = ComparisonEngine().compare(data, SCHEMA)
    assert result.filled_count == 6
    assert result.field_count == 7
    assert result.completion_percentage == pytest.approx(85.7, abs=0.1)


# --------------------------------------------------------------------------
# Signing-time slots: empty signature/date fields are reported separately and
# never gate approval; the data gate is untouched.
# --------------------------------------------------------------------------

SCHEMA_WITH_SLOTS = copy.deepcopy(SCHEMA)
SCHEMA_WITH_SLOTS["pages"][0]["fields"] += [
    {"field_id": "p1_signature", "label": "Contractor Signature", "field_type": "signature",
     "page": 1, "required": True, "paired_date_field_id": "p1_date",
     "bbox": {"x": 200, "y": 549, "width": 152, "height": 34}},
    {"field_id": "p1_date", "label": "Date", "field_type": "date", "page": 1,
     "required": True, "bbox": {"x": 440, "y": 576, "width": 55, "height": 14}},
    {"field_id": "p1_effective_date", "label": "Effective Date", "field_type": "date",
     "page": 1, "required": True, "bbox": {"x": 440, "y": 100, "width": 55, "height": 14}},
    {"field_id": "p1_optional_sig", "label": "Witness Signature", "field_type": "signature",
     "page": 1, "required": False, "bbox": {"x": 200, "y": 600, "width": 152, "height": 34}},
]
SLOT_IDS = {"p1_signature", "p1_date", "p1_effective_date", "p1_optional_sig"}


def test_empty_signature_and_date_fields_are_slots_not_missing():
    result = ComparisonEngine().compare(complete_data(), SCHEMA_WITH_SLOTS)
    assert result.missing_required == []
    slots = {s["field_id"]: s for s in result.signature_slots}
    assert set(slots) == SLOT_IDS

    assert slots["p1_signature"]["kind"] == "signature"
    assert slots["p1_signature"]["required"] is True
    assert slots["p1_signature"]["paired_date_field_id"] == "p1_date"
    assert slots["p1_signature"]["paired_signature_field_id"] is None
    assert slots["p1_signature"]["bbox"] == {"x": 200, "y": 549, "width": 152, "height": 34}

    assert slots["p1_date"]["kind"] == "date"
    assert slots["p1_date"]["paired_signature_field_id"] == "p1_signature"
    assert slots["p1_date"]["paired_date_field_id"] is None

    assert slots["p1_effective_date"]["kind"] == "date"
    assert slots["p1_effective_date"]["paired_signature_field_id"] is None

    assert slots["p1_optional_sig"]["required"] is False
    assert all(s["severity"] == "info" and s["page"] == 1 for s in slots.values())
    # Slots are for the signing step; review approval is not held up by them.
    assert result.can_sign is True


def _drop_cdl(data):
    del data["p1_cdl"]


def _mismatch_cdl(data):
    data["p2_cdl_license"] = value("TX9999999")


def _drop_dot(data):
    del data["p2_dot_eligible"]


@pytest.mark.parametrize(
    "corrupt, expect",
    [
        (_drop_cdl, "missing"),
        (_mismatch_cdl, "inconsistency"),
        (_drop_dot, "fmcsa"),
    ],
    ids=["critical-missing", "critical-inconsistency", "critical-fmcsa"],
)
def test_data_gate_is_unchanged_when_slots_are_empty(corrupt, expect):
    """Regression: moving slots out of `missing_required` must not loosen the
    data gate. Each critical data problem still blocks, with the slots still
    reported alongside it."""
    data = complete_data()
    corrupt(data)
    result = ComparisonEngine().compare(data, SCHEMA_WITH_SLOTS)

    assert result.can_sign is False
    if expect == "missing":
        assert [m["field_id"] for m in result.missing_required] == ["p1_cdl"]
    elif expect == "inconsistency":
        assert result.missing_required == []
        assert [i for i in result.inconsistencies if i["type"] == "cdl_mismatch" and i["severity"] == "critical"]
    else:
        # A blank DOT box is both a missing required field and an FMCSA
        # violation, exactly as it was before slots existed.
        assert [m["field_id"] for m in result.missing_required] == ["p2_dot_eligible"]
        assert [v for v in result.fmcsa_violations if "p2_dot_eligible" in v["field_ids"]]
    # Slot fields never leak into the missing bucket, whatever else is wrong.
    assert not SLOT_IDS & {m["field_id"] for m in result.missing_required}
    assert {s["field_id"] for s in result.signature_slots} == SLOT_IDS


def test_detected_ink_and_printed_date_leave_the_slot_bucket():
    data = complete_data()
    data["p1_signature"] = {"has_signature": True, "confidence": 0.9, "detected": True}
    data["p1_date"] = value("01/02/2026")
    result = ComparisonEngine().compare(data, SCHEMA_WITH_SLOTS)
    slot_ids = {s["field_id"] for s in result.signature_slots}
    assert slot_ids == {"p1_effective_date", "p1_optional_sig"}
    assert result.missing_required == []
    assert result.can_sign is True


def test_slots_do_not_change_completion_counting():
    """An empty slot is an unfilled field, exactly as before."""
    result = ComparisonEngine().compare(complete_data(), SCHEMA_WITH_SLOTS)
    assert result.field_count == 11
    assert result.filled_count == 7
    assert result.completion_percentage == pytest.approx(7 / 11 * 100, abs=0.1)

    data = complete_data()
    data["p1_signature"] = {"has_signature": True, "confidence": 0.9, "detected": True}
    assert ComparisonEngine().compare(data, SCHEMA_WITH_SLOTS).filled_count == 8


def test_pending_signature_slots_helper_matches_compare():
    """Finalize enforces what the comparison reported: one helper, one truth."""
    data = complete_data()
    data["p1_date"] = value("01/02/2026")
    reported = ComparisonEngine().compare(data, SCHEMA_WITH_SLOTS).signature_slots
    assert reported
    assert pending_signature_slots(data, SCHEMA_WITH_SLOTS) == reported
    # The same answer whether handed the schema or a pre-built index.
    assert pending_signature_slots(data, _index_schema(SCHEMA_WITH_SLOTS)) == reported
    # And nothing pending once every slot is filled.
    data["p1_signature"] = {"has_signature": True, "detected": True}
    data["p1_optional_sig"] = {"has_signature": True, "detected": True}
    data["p1_effective_date"] = value("01/02/2026")
    assert pending_signature_slots(data, SCHEMA_WITH_SLOTS) == []


def test_slots_are_reported_on_a_template_without_slot_fields_as_empty():
    result = ComparisonEngine().compare(complete_data(), SCHEMA)
    assert result.signature_slots == []
    assert "signature_slots" in result.to_dict()
