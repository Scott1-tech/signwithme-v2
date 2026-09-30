"""The final (and export) PDF pipeline of the editor v2 (EDITOR-V2-PLAN §4; B2).

    source (the signed version)
      -> flatten forms                                 (B3: `pdf_engine.forms`)
      -> v7 elements of `markup.UNDER_KINDS`          (below signatures)
      -> PdfBuilder.build_final_contract(..., finish=False)
             legacy whiteouts -> signatures + slot dates -> other legacy marks
      -> v7 markup above everything                    (`pdf_engine.markup`)
      -> metadata (+ owner password) + sha256          (`pdf_builder.finish_pdf`)

With NO v7 element the builder runs exactly as before (`finish=True`): the
default path is byte-for-byte the pre-v2 one (pinned by a test).

Forms are flattened FIRST, so a filled value is page content under the
signatures and the markup, and nothing interactive reaches the final. Only a
source with a VISIBLE widget is touched: every uploaded Docusign package
carries one hidden, zero-size envelope signature field, and a contract whose
document has no field of its own keeps its byte-for-byte final.

Signatures are drawn ONCE, by the builder only; the number of image XObjects
a final carries for them equals the number of `signatures` rows (test).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.pdf_builder import BuildResult, PdfBuilder, finish_pdf
from app.services.pdf_engine import markup as engine_markup


@dataclass
class FinalInputs:
    source: bytes
    signatures: list = field(default_factory=list)
    dates: list = field(default_factory=list)
    legacy_annotations: list = field(default_factory=list)  # AnnotationPlacement
    v7: list = field(default_factory=list)  # engine_markup.Placement
    layout: object | None = None
    metadata: dict = field(default_factory=dict)  # title, author, subject, keywords, owner_password
    flatten_forms: bool = True


def build_final(inputs: FinalInputs) -> BuildResult:
    builder = PdfBuilder()
    source = inputs.source
    if inputs.flatten_forms and has_visible_widgets(source):
        source = flatten_forms(source)
    if not inputs.v7:
        return builder.build_final_contract(
            source,
            inputs.signatures,
            inputs.dates,
            layout=inputs.layout,
            annotations=inputs.legacy_annotations,
            **inputs.metadata,
        )
    under = [p for p in inputs.v7 if p.element["kind"] in engine_markup.UNDER_KINDS]
    over = [p for p in inputs.v7 if p.element["kind"] not in engine_markup.UNDER_KINDS]
    source = engine_markup.draw_markup(source, under) if under else source
    owner_password = inputs.metadata.get("owner_password")
    built = builder.build_final_contract(
        source,
        inputs.signatures,
        inputs.dates,
        layout=inputs.layout,
        annotations=inputs.legacy_annotations,
        finish=False,
        **{k: v for k, v in inputs.metadata.items() if k != "owner_password"},
    )
    data = engine_markup.draw_markup(built.pdf_bytes, over)
    return finish_pdf(data, owner_password=owner_password,
                      **{k: v for k, v in inputs.metadata.items() if k != "owner_password"})


def flatten_forms(pdf_bytes: bytes) -> bytes:
    """Every form widget -> page content (its current appearance: the filled
    value, the checked box ...), no widget and no AcroForm left. No widget ->
    the same bytes."""
    from app.services.pdf_engine.forms import flatten

    return flatten(pdf_bytes)


def has_visible_widgets(pdf_bytes: bytes) -> bool:
    from app.services.pdf_engine.forms import has_visible_widgets as _visible

    return _visible(pdf_bytes)
