"""Reviewer override of a comparison that the engine would not let through.

The engine's `can_sign` is the last word on FMCSA violations: no reviewer can
approve past those. What a reviewer may knowingly accept, with a written
reason, is (2026-09-17 policy):

* an empty required field (`missing_required`) -- a blank the reviewer has
  verified is acceptable for this signing;
* a critical cross-field inconsistency (`inconsistencies`) -- e.g. a CDL number
  printed differently on two pages, checked against the licence itself.

An override records exactly which critical issues it accepted (see
`critical_issue_keys`). The signing gate re-derives the same keys from the
CURRENT comparison and only lets signing through while they are a subset of
the recorded ones: anything new that surfaces after the approval (a re-run
comparison, a re-parse) needs a fresh review.

Both gates (review approval and signing) share these helpers so they can never
disagree about what an override may cover. Nothing here changes how the engine
scores a contract.
"""
from __future__ import annotations

from app.services.comparison_engine import SEVERITY_CRITICAL

# Categories a reviewer override can never cover, in the order they are reported.
BLOCKING_CATEGORIES = ("fmcsa_violations",)

# Prefix of an accepted inconsistency in `review_override_fields`; missing
# fields are stored as their bare field id.
INCONSISTENCY_PREFIX = "inconsistency:"
# Prefix of an FMCSA violation in a "new issues" report (never stored).
FMCSA_PREFIX = "fmcsa:"
NEW_ISSUES_REASON = "new_issues_after_approval"
_UNTYPED = "untyped"


def _critical(entries: list | None) -> list[dict]:
    return [e for e in (entries or []) if isinstance(e, dict) and e.get("severity") == SEVERITY_CRITICAL]


def _dedup(items) -> list[str]:
    return list(dict.fromkeys(items))


def critical_counts(comparison: dict | None) -> dict[str, int]:
    """How many CRITICAL entries each reported category carries.

    Counts only -- never a field id, a label or a value -- so a list response
    can say "3 problems" while carrying none of the contract's data. Uses the
    same severity rule as the gates above, so a badge on the dashboard can
    never disagree with what review and signing actually do.
    """
    comparison = comparison if isinstance(comparison, dict) else {}
    return {
        name: len(_critical(comparison.get(name)))
        for name in ("missing_required", "inconsistencies", "fmcsa_violations")
    }


def blocking_categories(comparison: dict | None) -> list[str]:
    """Which non-overridable categories still carry a critical entry."""
    comparison = comparison or {}
    return [name for name in BLOCKING_CATEGORIES if _critical(comparison.get(name))]


def critical_missing_field_ids(comparison: dict | None) -> list[str]:
    """The required-but-empty fields an override would be accepting."""
    comparison = comparison or {}
    return _dedup(
        str(entry.get("field_id"))
        for entry in _critical(comparison.get("missing_required"))
        if entry.get("field_id")
    )


def critical_inconsistencies(comparison: dict | None) -> list[dict]:
    """The critical inconsistencies an override would be accepting, as
    PII-free summaries (type, issue text, pages -- never the values)."""
    comparison = comparison or {}
    return [
        {
            "type": str(entry.get("type") or _UNTYPED),
            "issue": str(entry.get("issue") or ""),
            "pages": [p for p in (entry.get("pages") or []) if p is not None],
        }
        for entry in _critical(comparison.get("inconsistencies"))
    ]


def inconsistency_key(kind: str) -> str:
    return f"{INCONSISTENCY_PREFIX}{kind}"


def critical_issue_keys(comparison: dict | None) -> list[str]:
    """Every overridable critical issue in the comparison, as the stable keys
    stored in `review_override_fields`: missing field ids first, then
    `inconsistency:<type>`. Ordered, no duplicates."""
    return _dedup(
        [
            *critical_missing_field_ids(comparison),
            *(inconsistency_key(i["type"]) for i in critical_inconsistencies(comparison)),
        ]
    )


def override_new_issues(contract) -> list[str]:
    """Critical issues in the contract's CURRENT comparison that the recorded
    override did not accept: any critical FMCSA violation (`fmcsa:<rule>`, never
    overridable), then missing field ids / `inconsistency:<type>` keys absent
    from `review_override_fields`. Keys only -- no values, no PII."""
    comparison = contract.comparison_result or {}
    fmcsa = _dedup(
        f"{FMCSA_PREFIX}{entry.get('rule') or _UNTYPED}"
        for entry in _critical(comparison.get("fmcsa_violations"))
    )
    accepted = {str(key) for key in (getattr(contract, "review_override_fields", None) or [])}
    return fmcsa + [key for key in critical_issue_keys(comparison) if key not in accepted]


def override_permits_signing(contract) -> bool:
    """Whether a recorded reviewer override still stands against the contract's
    CURRENT comparison.

    It does only while the comparison has no critical FMCSA violation and the
    critical issues it now reports are a SUBSET of those the reviewer accepted.
    Fewer issues (one got corrected) is fine; a new one -- a different missing
    field or inconsistency type surfacing after a re-run or re-parse -- is not."""
    if not getattr(contract, "review_override", False):
        return False
    return not override_new_issues(contract)
