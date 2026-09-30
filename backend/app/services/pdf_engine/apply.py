"""Apply content/structure ops to a version's bytes (EDITOR-V2-PLAN §3 "Dvigatel tartibi").

Order (B2):
  1. `page.*` -> the final uid order (`pages.plan_pages`); ops on a page that
     the same draft deletes are skipped with a warning (`op_on_removed_page`);
  2. per remaining base page, against the page AS IT WAS (one text model):
     `redact` -> `text.delete` -> `text.replace` -> `image.remove`;
  3. the physical page ops: new blank pages, deletions (MuPDF drops links
     that pointed at a deleted page), moves, absolute rotations;
  4. `link.*` (a link whose target page is gone is not written: warning
     `link_target_removed`);
  5. `field.*` in draft order (`forms.apply_field_ops`), then the tab order
     of every page they touched; fields whose widgets went with a deleted page
     are dropped from the AcroForm;
  6. `/SWMPageId` in every page dictionary;
  7. `save(garbage=3, deflate=True)` (+ `subset_fonts()` when text was drawn).
"""
from __future__ import annotations

from app.services.pdf_engine import adapter, errors, forms
from app.services.pdf_engine import fonts as engine_fonts
from app.services.pdf_engine.model import IGNORABLE, EngineResult, PageRef, PageText, intersects, rect_dict
from app.services.pdf_engine.ops import ImageRemoveOp, LinkOp, PageOp, RedactOp, TextOp
from app.services.pdf_engine.pages import PagesPlan, plan_pages
from app.services.pdf_engine.text_edit import PagePlan, _Grid, core_rect, plan_page, removal_rects

_KIND_ORDER = {"text.delete": 0, "text.replace": 1}
DEFAULT_PAGE_SIZE = (612.0, 792.0)
IMAGE_BBOX_TOLERANCE_PT = 1.0


def group_by_page(ops: list[TextOp]) -> dict[str, list[TextOp]]:
    groups: dict[str, list[TextOp]] = {}
    for op in ops:
        groups.setdefault(op.page_uid, []).append(op)
    for uid, page_ops in groups.items():
        groups[uid] = sorted(page_ops, key=lambda op: _KIND_ORDER.get(op.kind, 9))
    return groups


def page_index(page_refs: list[PageRef], page_uid: str, op_id: str | None) -> int:
    for ref in page_refs:
        if ref.page_uid == page_uid:
            return ref.position - 1
    raise errors.EngineError(
        errors.UNKNOWN_PAGE, "The edit names a page this version does not have", {"op_id": op_id}
    )


ORIGIN_TOLERANCE_PT = 0.05


def _visible(char) -> bool:
    return not char.c.isspace() and char.c not in IGNORABLE


def _still_there(pairs, present: dict) -> list:
    """The visible glyphs of `pairs` the page still has (`present` =
    `Doc.glyph_origins`): same character, same font, same origin
    (±`ORIGIN_TOLERANCE_PT`). Checked BEFORE any new text is drawn, so
    nothing new can be mistaken for an old glyph."""
    out = []
    for span, char in pairs:
        if not _visible(char):
            continue
        x, y = char.origin
        if any(abs(ox - x) <= ORIGIN_TOLERANCE_PT and abs(oy - y) <= ORIGIN_TOLERANCE_PT
               for ox, oy in present.get((char.c, span.font), ())):
            out.append((span, char))
    return out


def execute_plan(doc: adapter.Doc, index: int, page: PageText, plan: PagePlan) -> tuple[bool, list[dict]]:
    """Carry out a page plan. Returns (text drawn?, warnings).

    2026-09-30: every glyph the plan removes is checked to be gone before the
    new text is drawn. A glyph still there gets a second redaction with its
    plain core band (inside any glyph's hit box, `text_edit.core_rect`); if it
    is STILL there the page is refused (422 `old_text_not_removed`) -- the old
    text is never committed showing next to the new one. Neighbours a
    redaction took with it are drawn again and their boxes are reported in
    `plan.changed_regions`."""
    warnings: list[dict] = []
    removing = {id(char) for _span, char in plan.removal}
    protected = [
        pair for _block, line in page.all_lines() for pair in line.chars if id(pair[1]) not in removing
    ]
    rects, _predicted = removal_rects(plan.removal, protected)
    doc.redact(index, rects)  # fix_zero_td / quote guard -> apply -> restore_zero_td, inside
    restore: list[dict] = []
    if rects:
        after = doc.glyph_origins(index)
        left = _still_there(plan.removal, after)
        if left:
            retry = [core_rect(span, char) for span, char in left]
            doc.redact(index, retry)
            rects = rects + retry
            after = doc.glyph_origins(index)
            left = _still_there(left, after)
            if left:
                raise errors.EngineError(
                    errors.OLD_TEXT_NOT_REMOVED,
                    "Part of the old text could not be removed from the page",
                    {"op_id": plan.owners.get(id(left[0][1])), "glyphs": len(left)},
                )
        # Every other glyph a rectangle can reach (rawdict box: a superset of
        # MuPDF's hit box) and that is gone now is drawn again.
        grid = _Grid(protected)
        reach: dict[int, tuple] = {}
        for rect in rects:
            for pair in grid.near(rect):
                if intersects(pair[1].bbox, rect):
                    reach[id(pair[1])] = pair
        kept = {id(pair[1]) for pair in _still_there(list(reach.values()), after)}
        collateral = [pair for key, pair in reach.items() if key not in kept]
        lost = 0
        for span, char in collateral:
            if not _visible(char):
                continue
            face = engine_fonts.face_for(engine_fonts.suggest_family(span.font), span.bold, span.italic)
            if engine_fonts.refusal(char.c, face) is not None:
                lost += 1
                continue
            color = ((span.color >> 16) & 255, (span.color >> 8) & 255, span.color & 255)
            restore.append(
                {
                    "x": char.origin[0],
                    "baseline": char.origin[1],
                    "text": char.c,
                    "font_path": engine_fonts.font_path(face),
                    "size": span.size,
                    "color": tuple(c / 255.0 for c in color),
                }
            )
            plan.changed_regions.append(char.bbox)  # redrawn: its pixels may differ
        if restore:
            warnings.append({"code": "neighbor_glyphs_redrawn", "count": len(restore)})
        if lost:
            warnings.append({"code": "neighbor_glyphs_lost", "count": lost})
    fragments = restore + plan.fragments
    doc.write_text(index, fragments)
    return bool(fragments), warnings


def _split(ops: list):
    text = [op for op in ops if isinstance(op, TextOp)]
    redacts = [op for op in ops if isinstance(op, RedactOp)]
    images = [op for op in ops if isinstance(op, ImageRemoveOp)]
    pages = [op for op in ops if isinstance(op, PageOp)]
    links = [op for op in ops if isinstance(op, LinkOp)]
    fields = [op for op in ops if isinstance(op, forms.FieldOp)]
    return text, redacts, images, pages, links, fields


def _base_uids(page_refs: list[PageRef]) -> list[str]:
    return [ref.page_uid for ref in sorted(page_refs, key=lambda ref: ref.position)]


def _check_rects_on_page(rects, size: tuple[float, float], op_id: str) -> None:
    width, height = size
    for x0, y0, x1, y1 in rects:
        if x0 < -0.5 or y0 < -0.5 or x1 > width + 0.5 or y1 > height + 0.5:
            raise errors.EngineError(errors.OP_OUTSIDE_PAGE, "The edit reaches outside the page", {"op_id": op_id})


def _find_image(doc: adapter.Doc, index: int, op: ImageRemoveOp) -> None:
    for image in doc.page_images(index):
        if image["xref"] == op.xref and all(
            abs(a - b) <= IMAGE_BBOX_TOLERANCE_PT for a, b in zip(image["bbox"], op.bbox)
        ):
            return
    raise errors.EngineError(
        errors.STALE_TARGET, "That image is not on this page (any more)", {"op_id": op.id}
    )


def _find_link(doc: adapter.Doc, index: int, op: LinkOp) -> None:
    if not any(link["link_id"] == op.link_id for link in doc.links(index)):
        raise errors.EngineError(errors.UNKNOWN_LINK, "That link is not on this page (any more)", {"op_id": op.id})


def _content_pass(doc, page_refs, plan: PagesPlan, text, redacts, images, warnings, regions, substituted, extra):
    """Step 2: every base page's content ops, against the page as it was."""
    wrote = False
    by_page: dict[str, dict[str, list]] = {}
    for op in [*text, *redacts, *images]:
        by_page.setdefault(op.page_uid, {"text": [], "redact": [], "image": []})
        bucket = "text" if isinstance(op, TextOp) else "redact" if isinstance(op, RedactOp) else "image"
        by_page[op.page_uid][bucket].append(op)
    for uid, groups in by_page.items():
        all_ops = groups["text"] + groups["redact"] + groups["image"]
        if uid in plan.deleted:
            warnings.extend({"code": "op_on_removed_page", "op_id": op.id, "page_uid": uid} for op in all_ops)
            continue
        index = page_index(page_refs, uid, all_ops[0].id)
        size = doc.page_size(index)
        page = doc.page_text(index)
        text_plan = plan_page(page, sorted(groups["text"], key=lambda op: _KIND_ORDER.get(op.kind, 9))) \
            if groups["text"] else None
        for op in groups["image"]:
            _find_image(doc, index, op)
        # redact -> text.delete / text.replace -> image.remove
        for op in groups["redact"]:
            _check_rects_on_page(op.rects, size, op.id)
            doc.redact_area(index, list(op.rects), op.fill)
            regions.extend({"page_uid": uid, "rect": rect_dict(rect)} for rect in op.rects)
            extra["redactions"].extend({"page_uid": uid, "rect": rect_dict(rect)} for rect in op.rects)
        if text_plan is not None:
            drawn, page_warnings = execute_plan(doc, index, page, text_plan)
            wrote = wrote or drawn
            warnings.extend(text_plan.warnings)
            warnings.extend({**w, "page_uid": uid} for w in page_warnings)
            substituted.extend(text_plan.substituted_from)
            regions.extend({"page_uid": uid, "rect": rect_dict(rect)} for rect in text_plan.changed_regions)
        for op in groups["image"]:
            others = doc.remove_image(index, op.xref, op.bbox)
            extra["images_removed"] += 1
            regions.append({"page_uid": uid, "rect": rect_dict(op.bbox)})
            if others:
                warnings.append({"code": "other_images_removed", "op_id": op.id, "count": others})
    return wrote


def _physical_pass(doc, plan: PagesPlan) -> None:
    """Step 3: inserts at the end, deletions, then moves into the final order;
    then absolute rotations. `cur` tracks which uid sits at each index."""
    cur = list(plan.base_uids)
    for uid in [u for u in plan.final_uids if u in plan.inserted]:
        width, height = _new_page_size(doc, plan, uid, cur)
        doc.insert_blank_page(width, height)
        cur.append(uid)
    for index in range(len(cur) - 1, -1, -1):
        if cur[index] not in plan.final_uids:
            doc.delete_page(index)
            cur.pop(index)
    for target, uid in enumerate(plan.final_uids):
        index = cur.index(uid)
        if index != target:
            doc.move_page(index, target)
            cur.insert(target, cur.pop(index))
    assert cur == plan.final_uids
    for uid, rotation in plan.rotations.items():
        doc.set_rotation(plan.final_uids.index(uid), rotation)


def _new_page_size(doc, plan: PagesPlan, uid: str, cur: list[str]) -> tuple[float, float]:
    size = plan.inserted[uid]
    if isinstance(size, tuple):
        return size
    position = plan.final_uids.index(uid)
    # The page before it in the FINAL order (or after it, for a new first page)
    # that exists in the base document.
    neighbours = plan.final_uids[:position][::-1] + plan.final_uids[position + 1:]
    for other in neighbours:
        if other in plan.base_uids:
            return doc.page_size(plan.base_uids.index(other))
    return DEFAULT_PAGE_SIZE


def _links_pass(doc, plan: PagesPlan, links: list[LinkOp], warnings: list[dict]) -> None:
    for op in links:
        if op.page_uid not in plan.final_uids:
            warnings.append({"code": "op_on_removed_page", "op_id": op.id, "page_uid": op.page_uid})
            continue
        index = plan.final_uids.index(op.page_uid)
        if op.kind in ("link.update", "link.remove"):
            if not doc.remove_link(index, op.link_id):
                raise errors.EngineError(errors.UNKNOWN_LINK, "That link is not on this page (any more)",
                                         {"op_id": op.id})
            if op.kind == "link.remove":
                continue
        _check_rects_on_page([op.rect], doc.page_size(index), op.id)
        if op.target_type == "uri":
            doc.add_link(index, op.rect, uri=op.uri)
            continue
        target = plan.position_of(op.target_page_uid)
        if target is None:
            warnings.append({"code": "link_target_removed", "op_id": op.id})
            continue
        doc.add_link(index, op.rect, page_index=target - 1, y=op.target_y)


def _removed_field_names(doc, plan: PagesPlan, fields: list) -> set[str]:
    """Names of the fields on pages this draft deletes (their ops are
    skipped with a warning, like any op on a removed page)."""
    if not fields or not plan.deleted:
        return set()
    return forms.names_on_pages(doc, [plan.base_uids.index(uid) for uid in plan.deleted])


def apply_ops(pdf_bytes: bytes, page_refs: list[PageRef], ops: list) -> EngineResult:
    text, redacts, images, page_ops, links, fields = _split(ops)
    plan = plan_pages(_base_uids(page_refs), page_ops)
    regions: list[dict] = []
    warnings: list[dict] = []
    substituted: list[dict] = []
    extra = {"redactions": [], "images_removed": 0}
    with adapter.Doc(pdf_bytes) as doc:
        warnings.extend(doc.warnings)
        wrote = _content_pass(doc, page_refs, plan, text, redacts, images, warnings, regions, substituted, extra)
        removed_names = _removed_field_names(doc, plan, fields)
        _physical_pass(doc, plan)
        if plan.deleted:
            forms.prune_orphan_fields(doc)
        _links_pass(doc, plan, links, warnings)
        forms.apply_field_ops(doc, plan.final_uids, fields, removed_names=removed_names,
                              deleted_uids=plan.deleted, warnings=warnings, regions=regions)
        doc.set_page_ids(plan.final_uids)
        data = doc.save(subset_fonts=wrote)
    with adapter.Doc(data) as out:
        pages = [out.page_info(i) for i in range(out.page_count)]
    return EngineResult(
        pdf_bytes=data,
        sha256=adapter.sha256_hex(data),
        page_count=len(pages),
        pages=pages,
        changed_regions=regions,
        warnings=warnings,
        substituted_from=substituted,
        page_uids=list(plan.final_uids),
        redactions=extra["redactions"],
        images_removed=extra["images_removed"],
        pages_plan=plan,
        fields=forms.op_stats(fields) if fields else None,
    )


def validate_ops(pdf_bytes: bytes, page_refs: list[PageRef], ops: list) -> tuple[list[dict], list[dict]]:
    """Plan every op without writing: (problems [{id, reason}], warnings).

    Used by the draft autosave: structural problems (unknown page, scanned
    page, stale target, undrawable character, unsupported span, two ops on one
    line, a page plan that cannot be carried out, an unknown image or link)
    refuse the save; a text that does not fit is only a warning there -- the
    user is still typing -- and is refused at commit.
    """
    problems: list[dict] = []
    warnings: list[dict] = []
    text, redacts, images, page_ops, links, fields = _split(ops)
    try:
        plan = plan_pages(_base_uids(page_refs), page_ops)
    except errors.EngineError as exc:
        problems.append({"id": exc.details.get("op_id"), "reason": exc.code})
        plan = plan_pages(_base_uids(page_refs), [])
    groups = group_by_page(text)
    with adapter.Doc(pdf_bytes) as doc:
        warnings.extend(doc.warnings)
        for uid, page_ops_ in groups.items():
            if uid in plan.deleted:
                warnings.extend({"code": "op_on_removed_page", "op_id": op.id} for op in page_ops_)
                continue
            try:
                index = page_index(page_refs, uid, page_ops_[0].id)
            except errors.EngineError:
                problems.extend({"id": op.id, "reason": errors.UNKNOWN_PAGE} for op in page_ops_)
                continue
            page = doc.page_text(index)
            for op in page_ops_:
                try:
                    text_plan = plan_page(page, [op])
                    warnings.extend(text_plan.warnings)
                except errors.EngineError as exc:
                    if exc.code == errors.TEXT_OVERFLOW:
                        warnings.append({"op_id": op.id, "code": exc.code, **_numbers(exc.details)})
                    else:
                        problems.append({"id": op.id, "reason": exc.code})
            try:
                plan_page_targets_unique(page, page_ops_)
            except errors.EngineError as exc:
                if exc.code == errors.DUPLICATE_TARGET:
                    problems.append({"id": exc.details.get("op_id"), "reason": exc.code})
        for op in [*redacts, *images]:
            if op.page_uid in plan.deleted:
                warnings.append({"code": "op_on_removed_page", "op_id": op.id})
                continue
            try:
                index = page_index(page_refs, op.page_uid, op.id)
                if isinstance(op, RedactOp):
                    _check_rects_on_page(op.rects, doc.page_size(index), op.id)
                else:
                    _find_image(doc, index, op)
            except errors.EngineError as exc:
                problems.append({"id": op.id, "reason": exc.code})
        for op in links:
            if op.page_uid not in plan.final_uids and op.page_uid not in plan.deleted:
                problems.append({"id": op.id, "reason": errors.UNKNOWN_PAGE})
                continue
            if op.target_type == "page" and op.target_page_uid not in plan.final_uids:
                if op.target_page_uid in plan.deleted:
                    warnings.append({"code": "link_target_removed", "op_id": op.id})
                else:
                    problems.append({"id": op.id, "reason": errors.UNKNOWN_PAGE})
                continue
            if op.kind != "link.add" and op.page_uid in plan.base_uids and op.page_uid not in plan.deleted:
                try:
                    _find_link(doc, page_index(page_refs, op.page_uid, op.id), op)
                except errors.EngineError as exc:
                    problems.append({"id": op.id, "reason": exc.code})
    if fields:
        _validate_fields(pdf_bytes, plan, fields, problems, warnings)
    return problems, warnings


def _validate_fields(pdf_bytes: bytes, plan: PagesPlan, fields: list, problems: list, warnings: list) -> None:
    """The field ops carried out on a scratch copy (page ops applied first,
    text ops skipped -- they do not move a field): every refused op is a
    problem; nothing is saved. Cheap: no text model is built."""
    with adapter.Doc(pdf_bytes) as doc:
        removed_names = _removed_field_names(doc, plan, fields)
        try:
            _physical_pass(doc, plan)
        except Exception:  # noqa: BLE001 - the page plan itself was reported above
            return
        if plan.deleted:
            forms.prune_orphan_fields(doc)
        forms.apply_field_ops(doc, plan.final_uids, fields, removed_names=removed_names,
                              deleted_uids=plan.deleted, warnings=warnings, regions=[], problems=problems)


def plan_page_targets_unique(page: PageText, ops: list[TextOp]) -> None:
    from app.services.pdf_engine.text_edit import resolve_target

    claimed: dict[str, str] = {}
    for op in ops:
        try:
            target = resolve_target(page, op)
        except errors.EngineError:
            continue
        for line in target.lines:
            if line.id in claimed:
                raise errors.EngineError(
                    errors.DUPLICATE_TARGET, "Two edits target the same line", {"op_id": op.id}
                )
            claimed[line.id] = op.id


def _numbers(details: dict) -> dict:
    return {k: v for k, v in details.items() if k in ("lines_over", "needed_h_pt", "reason")}
