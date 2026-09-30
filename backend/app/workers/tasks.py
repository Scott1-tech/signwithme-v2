"""Background PDF work. Parsing a 40-page template is far too slow for a request."""
from __future__ import annotations

import logging
from uuid import UUID

from app.core.database import SessionLocal
from app.services import audit
from app.services.comparison_engine import ComparisonEngine
from app.services.field_detector import FieldDetector
from app.services.pdf_parser import PdfParser
from app.services.rule_locator import needs_rule_y, rule_summary, with_rule_y
from app.services.storage import storage
from app.services.template_mapper import (
    apply_corrections,
    extracted_cache,
    restore_correction_state,
    reviewer_corrections,
    sync_contract_fields,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="templates.analyze", bind=True, max_retries=2)
def analyze_template_task(self, template_id: str) -> dict:
    """Detect every fillable region in a template and store the field schema."""
    from app.models.template import Template

    db = SessionLocal()
    try:
        template = db.get(Template, UUID(template_id))
        if template is None:
            logger.error("Template %s vanished before analysis", template_id)
            return {"status": "missing"}

        template.status = "processing"
        db.commit()

        with storage.local_copy(template.file_path) as path:
            text_layer = PdfParser().analyze_text_layer(path)
            if text_layer.needs_ocr:
                template.status = "failed"
                template.error_message = text_layer.message
                audit.log(
                    db,
                    audit.TEMPLATE_ANALYZED,
                    template_id=template.id,
                    details={"needs_ocr": True, **text_layer.to_dict()},
                )
                db.commit()
                return {"status": "failed", "needs_ocr": True}
            schema = FieldDetector().build_schema(path)
            # Record what the blank form already shows in each signature/date
            # slot, so a contract is only "signed" where the contractor added ink.
            schema = PdfParser().with_template_baseline(path, schema)
            # Where each slot's visible fill line is (additive `rule_y` key), so
            # the final PDF and the signing preview sit marks on the same ink.
            schema = with_rule_y(path, schema)

        template.field_schema = schema
        template.page_count = len(schema.get("pages", []))
        template.status = "ready"
        template.error_message = None
        audit.log(
            db,
            audit.TEMPLATE_ANALYZED,
            template_id=template.id,
            details={
                "field_count": schema.get("field_count", 0),
                "pages": template.page_count,
                # Non-zero means a signed/filled package was uploaded as the form.
                **PdfParser.baseline_summary(schema),
                **rule_summary(schema),
            },
        )
        db.commit()
        return {"status": "ready", "field_count": schema.get("field_count", 0)}
    except Exception as exc:  # noqa: BLE001 - surface the failure on the record
        logger.exception("Template analysis failed for %s", template_id)
        db.rollback()
        template = db.get(Template, UUID(template_id))
        if template is not None:
            template.status = "failed"
            template.error_message = str(exc)[:1000]
            db.commit()
        raise
    finally:
        db.close()


def _template_match(
    parser: PdfParser, template, contract_path: str, schema: dict, page_map=None
) -> dict | None:
    """Best effort: a failure here only loses the warning, never the parse."""
    try:
        with storage.local_copy(template.file_path) as template_path:
            if page_map is None:
                return parser.template_match(template_path, contract_path, schema)
            return parser.template_match(template_path, contract_path, schema, page_map)
    except Exception:  # noqa: BLE001
        logger.exception("Template match failed for template %s", template.id)
        return None


def _parse_copy(source):
    """A local file of the document to parse. Template coordinates are in the
    ORIGINAL pages' displayed space, so a page rotated since (editor v2
    `page.rotate`) is parsed with its original /Rotate restored -- the
    rotation changes how a page is shown, never what its fields hold."""
    import contextlib
    import io
    import os
    import tempfile

    changed = {
        position: source.origin_rotation.get(origin, 0)
        for origin, position in (source.origin_to_position or {}).items()
        if position is not None and source.rotated_since_template(origin)
    }
    if not changed:
        return storage.local_copy(source.bytes_key)

    @contextlib.contextmanager
    def normalised():
        from pypdf import PdfWriter
        from pypdf.generic import NameObject, NumberObject

        writer = PdfWriter(clone_from=io.BytesIO(storage.get_bytes(source.bytes_key)))
        for position, rotation in changed.items():
            writer.pages[position - 1][NameObject("/Rotate")] = NumberObject(rotation)
        handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            writer.write(handle)
            handle.close()
            yield handle.name
        finally:
            handle.close()
            os.unlink(handle.name)

    return normalised()


@celery_app.task(name="contracts.process", bind=True, max_retries=2)
def process_contract_task(self, contract_id: str) -> dict:
    """Extract every field from an uploaded contract, then compare it against
    its template."""
    from app.models.contract import Contract
    from app.models.template import Template

    db = SessionLocal()
    try:
        contract = db.get(Contract, UUID(contract_id))
        if contract is None:
            return {"status": "missing"}
        template = db.get(Template, contract.template_id)
        if template is None or template.status != "ready":
            contract.status = "failed"
            contract.error_message = "Template is not ready for comparison"
            db.commit()
            return {"status": "failed"}

        contract.status = "parsing"
        db.commit()

        parser = PdfParser()
        schema = template.field_schema
        if parser.needs_template_baseline(schema):
            # Template analysed before baselines existed: derive it once from the
            # stored form and keep it (additive keys only) for the next contract.
            with storage.local_copy(template.file_path) as template_path:
                schema = parser.with_template_baseline(template_path, schema)
            template.field_schema = schema
            db.commit()
        if needs_rule_y(schema):
            # Same lazy, additive upgrade for the fill-line position.
            with storage.local_copy(template.file_path) as template_path:
                schema = with_rule_y(template_path, schema)
            template.field_schema = schema
            db.commit()

        # PDF editor v2: read the document that is signed -- the CURRENT
        # version (original upload for a legacy contract, identity page map).
        from app.services.documents import signing_source

        source = signing_source(contract)
        page_map = source.page_map
        with _parse_copy(source) as path:
            text_layer = parser.analyze_text_layer(path)
            if text_layer.needs_ocr:
                # Reporting every field as missing would be technically true and
                # completely misleading; say what is actually wrong.
                contract.status = "failed"
                contract.error_message = text_layer.message
                audit.log(
                    db,
                    audit.CONTRACT_PARSED,
                    contract_id=contract.id,
                    template_id=template.id,
                    details={"needs_ocr": True, **text_layer.to_dict()},
                )
                db.commit()
                return {"status": "failed", "needs_ocr": True}
            if page_map is None:
                extracted = parser.extract(path, schema)
            else:
                extracted = parser.extract(path, schema, page_map)
            template_match = _template_match(parser, template, path, schema, page_map)

        # A re-parse (POST .../reparse) must not undo what a reviewer already
        # corrected by hand; on a first parse there are no rows, so this is empty.
        corrections = reviewer_corrections(db, contract.id)
        extracted = apply_corrections(extracted, corrections)

        from app.services.document_review import present_pages

        present = present_pages(source)
        if present is None:
            comparison = ComparisonEngine().compare(
                extracted, schema, contract_id=str(contract.id)
            ).to_dict()
        else:
            comparison = ComparisonEngine().compare(
                extracted, schema, contract_id=str(contract.id), present_pages=present
            ).to_dict()
        # Warning only (never part of can_sign): the upload is its template
        # file again, so every visible signature/date is the template's own.
        comparison["template_match"] = template_match

        rows = sync_contract_fields(db, contract.id, schema, extracted, comparison)
        restore_correction_state(rows, corrections)
        contract.extracted_data = extracted_cache(rows)
        contract.comparison_result = comparison
        contract.status = "analyzed"
        contract.error_message = None

        audit.log(
            db,
            audit.CONTRACT_PARSED,
            contract_id=contract.id,
            template_id=template.id,
            details={"fields": len(rows), "corrections_kept": sorted(corrections)},
        )
        audit.log(
            db,
            audit.COMPARISON_RUN,
            contract_id=contract.id,
            details={
                "completion_percentage": comparison["completion_percentage"],
                "missing": len(comparison["missing_required"]),
                "inconsistencies": len(comparison["inconsistencies"]),
                "fmcsa_violations": len(comparison["fmcsa_violations"]),
                "signature_slots": len(comparison.get("signature_slots", [])),
                "can_sign": comparison["can_sign"],
                "template_identical": (template_match or {}).get("identical"),
            },
        )
        db.commit()
        return {"status": "analyzed", "can_sign": comparison["can_sign"]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Contract processing failed for %s", contract_id)
        db.rollback()
        contract = db.get(Contract, UUID(contract_id))
        if contract is not None:
            contract.status = "failed"
            contract.error_message = str(exc)[:1000]
            db.commit()
        raise
    finally:
        db.close()
