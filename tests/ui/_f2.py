"""
F2 helpers: a workspace WITH the document editor (B2 mocked by `_docmock`),
page geometry, drags, the edit session, reading the draft's ops and markup.
"""

from __future__ import annotations

import os
from typing import Any

from _docmock import Line, enable_document, wait_until
from _harness import BASE, MockApi, arm_tool, equals, slot

SHOTS = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_f2")


def shot(page, name: str) -> None:
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, f"{name}.png"))


def install(mock: MockApi, pages: int = 2, *, signed: list[str] | None = None, slots=None, **options):
    """The editor over `pages` synthetic pages (one text line each)."""
    mock.set_slots(slots if slots is not None else [slot("sig_1", "signature", 72, 640, 180, 34, rule_y=679.5)],
                   signed=signed if signed is not None else [])
    return enable_document(
        mock,
        [[Line(f"Page {index + 1} carrier text", 72, 150)] for index in range(pages)],
        **options,
    )


def open_doc(page, mock: MockApi) -> None:
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_selector('[data-testid="workspace"][data-editor-v2="true"]', timeout=15000)
    page.wait_for_timeout(300)


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")
    warnings = [text for text in mock.console_errors if "Warning:" in text and "React Router" not in text]
    equals(warnings, [], "no React warning")


def page_box(page, number: int = 1) -> dict:
    target = page.locator(f'.pdf-page[data-page-number="{number}"]')
    target.scroll_into_view_if_needed()
    return target.bounding_box()


def scale_of(page, number: int = 1) -> float:
    return page_box(page, number)["width"] / 612


def at(page, x_pt: float, y_pt: float, number: int = 1) -> tuple[float, float]:
    """A PDF point of page `number` in viewport pixels."""
    box = page_box(page, number)
    scale = box["width"] / 612
    return box["x"] + x_pt * scale, box["y"] + y_pt * scale


def click_pt(page, x_pt: float, y_pt: float, number: int = 1) -> None:
    x, y = at(page, x_pt, y_pt, number)
    page.mouse.click(x, y)


def drag_pt(page, x1: float, y1: float, x2: float, y2: float, number: int = 1, steps: int = 10) -> None:
    ax, ay = at(page, x1, y1, number)
    bx, by = at(page, x2, y2, number)
    page.mouse.move(ax, ay)
    page.mouse.down()
    page.mouse.move(ax + 4, ay + 4)
    page.mouse.move(bx, by, steps=steps)
    page.mouse.up()
    page.wait_for_timeout(150)


def start_session(page) -> None:
    page.locator('[data-testid="editor-edit-pdf"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=10000)


def ops_of(doc) -> list[dict]:
    return (doc.draft or {}).get("ops") or []


def wait_ops(page, doc, predicate, timeout: int = 8000) -> list[dict]:
    wait_until(page, lambda: predicate(ops_of(doc)), timeout)
    return ops_of(doc)


def last_markup(doc) -> list[dict]:
    """The markup the server was last given (a PUT, else the draft's)."""
    if doc.draft and doc.draft.get("markup") is not None:
        return doc.draft["markup"]
    if doc.markup_puts:
        return doc.markup_puts[-1].get("elements", [])
    return doc.markup


def wait_markup(page, doc, predicate, timeout: int = 8000) -> list[dict]:
    wait_until(page, lambda: predicate(last_markup(doc)), timeout)
    return last_markup(doc)


def select_tool(page, name: str) -> None:
    page.locator("body").click(position={"x": 3, "y": 3})
    arm_tool(page, name)
    page.wait_for_timeout(80)


def workspace_attr(page, name: str) -> Any:
    return page.locator('[data-testid="workspace"]').get_attribute(name)


def png_file(tmpdir: str, name: str = "zz-f2.png", width: int = 60, height: int = 30) -> str:
    from _harness import solid_png

    os.makedirs(tmpdir, exist_ok=True)
    path = os.path.join(tmpdir, name)
    with open(path, "wb") as handle:
        handle.write(solid_png(width, height))
    return path


__all__ = [
    "Line", "wait_until", "SHOTS", "shot", "install", "open_doc", "clean", "page_box", "scale_of", "at",
    "click_pt", "drag_pt", "start_session", "ops_of", "wait_ops", "last_markup", "wait_markup", "select_tool",
    "workspace_attr", "png_file",
]
