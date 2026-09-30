"""Engine errors: a closed set of machine-readable codes (EDITOR-V2-PLAN §4).

Messages and details carry ids, counts, code points and coordinates only --
never the document's text or the text a user typed (the same rule as
`app.main._scrub_validation_error` and the audit trail).
"""
from __future__ import annotations

STALE_TARGET = "stale_target"
TARGET_NOT_FOUND = "target_not_found"
SCANNED_PAGE_NOT_EDITABLE = "scanned_page_not_editable"
UNDRAWABLE_CHARACTER = "undrawable_character"
TEXT_OVERFLOW = "text_overflow"
PDF_ENCRYPTED = "pdf_encrypted"
PDF_CORRUPT = "pdf_corrupt"
UNSUPPORTED_SPAN = "unsupported_span"
OP_OUTSIDE_PAGE = "op_outside_page"
UNKNOWN_PAGE = "unknown_page"
DUPLICATE_TARGET = "duplicate_target"
# QA 2026-09-30: a ✥ Move that cannot be carried out is refused, never dropped.
TEXT_MOVE_NOT_APPLIED = "text_move_not_applied"
# 2026-09-30: after the redaction a glyph of the replaced/deleted text is still
# on the page (checked in `apply.execute_plan`): refused, never committed with
# the old text showing. details: op_id, glyphs (a count).
OLD_TEXT_NOT_REMOVED = "old_text_not_removed"
# B2
DUPLICATE_PAGE_UID = "duplicate_page_uid"
LAST_PAGE = "cannot_delete_last_page"
UNKNOWN_LINK = "unknown_link"
PAGE_REMOVED = "page_removed"
# B3: form fields
FIELD_NAME_TAKEN = "field_name_taken"
UNKNOWN_FIELD = "unknown_field"
RADIO_VALUE_TAKEN = "radio_value_taken"
INVALID_FIELD_VALUE = "invalid_field_value"
FIELD_READONLY = "field_readonly"
FIELD_NOT_FILLABLE = "field_not_fillable"
UNSUPPORTED_FIELD = "unsupported_field"

# HTTP status per code. A target that no longer matches the document is a
# conflict with the current state (409); everything else is a request the
# engine cannot carry out as asked (422).
STATUS = {
    STALE_TARGET: 409,
    TARGET_NOT_FOUND: 409,
    UNKNOWN_LINK: 409,
    FIELD_NAME_TAKEN: 409,
    UNKNOWN_FIELD: 409,
    RADIO_VALUE_TAKEN: 409,
}


class EngineError(Exception):
    """A refusal by the PDF engine. `code` is one of the constants above."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def status(self) -> int:
        return STATUS.get(self.code, 422)
