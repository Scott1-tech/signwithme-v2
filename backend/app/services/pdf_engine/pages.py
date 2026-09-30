"""Page operations as a plan over stable page ids (EDITOR-V2-PLAN §3 step 1, §5.4).

`plan_pages` replays `page.insert / delete / move / rotate` IN ORDER on the
list of base page uids and says what the version will look like: the final uid
order, which base pages are gone, which pages are new (and their size), and
the absolute rotation of every page that got one. Nothing is touched here --
`apply.apply_ops` carries the plan out on the PDF.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.pdf_engine import errors
from app.services.pdf_engine.ops import PageOp


@dataclass
class PagesPlan:
    base_uids: list[str]
    final_uids: list[str]
    deleted: set[str] = field(default_factory=set)
    inserted: dict[str, object] = field(default_factory=dict)  # uid -> "same_as_previous" | (w, h)
    rotations: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.deleted or self.inserted or self.rotations) or self.final_uids != self.base_uids

    def position_of(self, uid: str) -> int | None:
        """1-based position of `uid` in the new version, None if it is gone."""
        try:
            return self.final_uids.index(uid) + 1
        except ValueError:
            return None


def _fail(code: str, message: str, op_id: str) -> errors.EngineError:
    return errors.EngineError(code, message, {"op_id": op_id})


def plan_pages(base_uids: list[str], ops: list[PageOp]) -> PagesPlan:
    order = list(base_uids)
    plan = PagesPlan(base_uids=list(base_uids), final_uids=order)
    known = set(base_uids)
    for op in ops:
        if op.kind == "page.insert":
            if op.new_page_uid in known:
                raise _fail(errors.DUPLICATE_PAGE_UID, "That page id is already in use", op.id)
            index = _after_index(order, op.after_page_uid, op.id)
            order.insert(index, op.new_page_uid)
            plan.inserted[op.new_page_uid] = op.size
            known.add(op.new_page_uid)
            continue
        if op.page_uid not in order:
            raise _fail(errors.UNKNOWN_PAGE, "The page op names a page this draft does not have", op.id)
        if op.kind == "page.delete":
            if len(order) == 1:
                raise _fail(errors.LAST_PAGE, "The last page of a document cannot be deleted", op.id)
            order.remove(op.page_uid)
            if op.page_uid in plan.inserted:
                del plan.inserted[op.page_uid]
            else:
                plan.deleted.add(op.page_uid)
            plan.rotations.pop(op.page_uid, None)
        elif op.kind == "page.move":
            if op.after_page_uid == op.page_uid:
                raise _fail(errors.UNKNOWN_PAGE, "A page cannot be moved after itself", op.id)
            order.remove(op.page_uid)
            order.insert(_after_index(order, op.after_page_uid, op.id), op.page_uid)
        elif op.kind == "page.rotate":
            plan.rotations[op.page_uid] = int(op.rotation) % 360
    return plan


def _after_index(order: list[str], after_uid: str | None, op_id: str) -> int:
    if after_uid is None:
        return 0
    if after_uid not in order:
        raise _fail(errors.UNKNOWN_PAGE, "The page op names a page this draft does not have", op_id)
    return order.index(after_uid) + 1
