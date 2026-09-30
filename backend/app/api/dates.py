"""Date field placement.

Dates are never positioned by offsetting from a signature. The template records
each date field's own bounding box, and signature fields carry
`paired_date_field_id`, so the contract date lands where the document actually
prints one.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import assert_can_view_contract, get_contract_or_404, get_template_or_404
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.services.comparison_engine import _index_schema
from app.services.documents import signing_source
from app.services.validators import format_date, parse_date

router = APIRouter(prefix="/api/contracts", tags=["dates"])


@router.get("/{contract_id}/date-fields")
def list_date_fields(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every date field in the template, flagged with the signature it belongs to."""
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    template = get_template_or_404(db, contract.template_id)
    index = _index_schema(template.field_schema)

    paired_from = {
        field["paired_date_field_id"]: field["field_id"]
        for field in index.values()
        if field.get("field_type") == "signature" and field.get("paired_date_field_id")
    }

    # PDF editor v2: template page -> position in the current version. A
    # legacy contract gets exactly the old response (no `page_uid` key).
    source = signing_source(contract)
    out = []
    for field in index.values():
        if field.get("field_type") != "date":
            continue
        position = source.position_for(field.get("page"))
        if not source.legacy and field.get("page") is not None and position is None:
            # B2: its page was removed from the current version -- not offered
            # (finalize answers 422 `page_removed` if it is sent anyway).
            continue
        entry = {
            "field_id": field["field_id"],
            "label": field.get("label"),
            "page": position,
            "bbox": source.template_bbox(field.get("page"), field.get("bbox")),
            "required": field.get("required", False),
            "paired_signature_field_id": paired_from.get(field["field_id"]),
            "current_value": (contract.extracted_data.get(field["field_id"]) or {}).get("value"),
        }
        if not source.legacy:
            entry["page_uid"] = source.page_uid_at(entry["page"])
        out.append(entry)
    return out


@router.post("/{contract_id}/date-preview")
def preview_date(
    contract_id: UUID,
    value: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Show how a chosen date will render in the PDF (MM/DD/YYYY), so the UI
    never displays an ISO date the document will not carry."""
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    parsed = parse_date(value)
    if parsed is None:
        return {"valid": False, "rendered": None}
    return {"valid": True, "iso": parsed.isoformat(), "rendered": format_date(parsed)}
