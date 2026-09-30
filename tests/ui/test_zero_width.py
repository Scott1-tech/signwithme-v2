"""
2026-09-30 (`_staging\\zw`): zero-width characters and the engine's new refusals.

The real Docusign template carries U+200B (zero width space) on 51 % of its
lines. The user never sees them, so the in-place text editor must neither show
nor send them and never refuse an edit because of them -- while a character
that DOES change how text reads (U+202E right-to-left override) is still
refused before anything is sent. The engine's two «refused, never dropped»
answers of a commit (`text_move_not_applied`, `old_text_not_removed`) are
explained to the user and pinned to their op.

The server is `_docmock.DocMock`; `model_hook` makes it answer the text model
the way a server before this fix did (the U+200B glyphs in `text` and `chars`).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _docmock import Line, enable_document, wait_until  # noqa: E402
from _harness import (  # noqa: E402
    BASE,
    MockApi,
    Registry,
    arm_tool,
    cli_main,
    contains,
    equals,
    truthy,
)

test = Registry("zero width")

CARRIER = "Carrier: ABC Transportation LLC"
TERMS = "Payment terms: net thirty days"
IGNORABLE = "­​‌‍⁠﻿"


def install(mock: MockApi, zero_width: bool = False):
    doc = enable_document(mock, [[Line(CARRIER, 72, 150, "Helvetica", 12), Line(TERMS, 72, 190, "Times-Roman", 12)]])
    if zero_width:
        doc.model_hook = with_zero_width
    return doc


def with_zero_width(model: dict, _number: int) -> dict:
    """The wire of a server before 2026-09-30 for a Docusign line: a U+200B
    glyph at both ends of every line (zero-width boxes), in `text` too."""
    for block in model["blocks"]:
        for line in block["lines"]:
            span = line["spans"][0]
            first, last = span["chars"][0], span["chars"][-1]
            span["chars"] = ([["​", first[1], first[2], first[1], first[4]]] + span["chars"]
                             + [["​", last[3], last[2], last[3], last[4]]])
            line["text"] = "​" + line["text"] + "​"
    return model


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def open_doc(page, mock: MockApi) -> None:
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_selector('[data-testid="workspace"][data-editor="ready"]', timeout=15000)
    page.locator('[data-testid="editor-edit-pdf"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=10000)
    page.wait_for_selector('[data-testid="editor-save-status"][data-status="saved"]', timeout=10000)
    arm_tool(page, "edit-text")
    page.wait_for_selector('[data-testid="text-block-layer-1"][data-state="ready"]', timeout=15000)


def open_block(page, text: str) -> None:
    box = page.locator(f'.tb-block[data-text="{text}"]').bounding_box()
    truthy(box is not None, f"block {text!r} is on the page (its text without U+200B)")
    page.mouse.click(box["x"] + box["width"] * 0.97, box["y"] + box["height"] / 2)
    page.wait_for_selector('[data-testid="text-block-editor"]', timeout=10000)
    page.wait_for_timeout(80)
    page.keyboard.press("End")


def editor_text(page) -> str:
    return page.evaluate("() => document.querySelector('[data-testid=\"text-block-editor\"]').textContent")


def ops_of(doc) -> list:
    return doc.draft["ops"] if doc.draft else []


def wait_ops(page, doc, count: int) -> None:
    truthy(wait_until(page, lambda: doc.draft is not None and len(doc.draft["ops"]) == count, 8000),
           f"the draft holds {count} op(s) (has {ops_of(doc)})")


def new_text(op: dict) -> str:
    return "".join(run["text"] for run in op["runs"])


def no_note(page) -> None:
    equals(page.locator('[data-testid="editor-note"]').count(), 0, "no refusal note")


@test("served_zero_width_glyphs_are_neither_shown_nor_a_reason_to_refuse")
def _(page, mock: MockApi) -> None:
    doc = install(mock, zero_width=True)
    open_doc(page, mock)
    equals(page.locator(f'.tb-block[data-text="{CARRIER}"]').count(), 1, "the frame's text has no U+200B")
    open_block(page, CARRIER)
    equals(editor_text(page), CARRIER, "the editor holds the visible text only")
    page.keyboard.type(" Inc")
    page.keyboard.press("Enter")
    wait_ops(page, doc, 1)
    op = ops_of(doc)[0]
    equals(new_text(op), CARRIER + " Inc", "the new text")
    truthy(not any(c in new_text(op) for c in IGNORABLE), "no zero-width character is sent to be printed")
    equals(op["target"]["expect_text"].replace("​", ""), CARRIER, "the target (the server compares without them)")
    no_note(page)
    page.wait_for_selector('[data-testid="editor-save-status"][data-status="saved"]', timeout=10000)
    clean(mock)


@test("pasted_zero_width_characters_are_left_out_of_the_op")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    open_block(page, TERMS)
    page.keyboard.insert_text("​﻿ (net 30)­⁠‌‍")
    page.keyboard.press("Enter")
    wait_ops(page, doc, 1)
    equals(new_text(ops_of(doc)[0]), TERMS + " (net 30)", "only what can be seen is sent")
    no_note(page)
    clean(mock)


@test("a_right_to_left_override_is_still_refused_before_anything_is_sent")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    open_block(page, CARRIER)
    page.keyboard.insert_text("‮ X")
    page.keyboard.press("Enter")
    note = page.wait_for_selector('[data-testid="editor-note"]', timeout=5000)
    contains(note.inner_text(), "U+202E", "the character is named")
    contains(note.inner_text(), "Liberation Sans cannot print", "and the face")
    equals(page.locator('[data-testid="text-block-editor"]').count(), 1, "the editor stays open")
    page.wait_for_timeout(400)
    equals(ops_of(doc), [], "nothing was sent")
    clean(mock)


@test("the_engines_move_and_old_text_refusals_are_explained_at_save")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    open_block(page, CARRIER)
    page.keyboard.type(" Inc")
    page.keyboard.press("Enter")
    wait_ops(page, doc, 1)
    page.wait_for_selector('[data-testid="editor-save-status"][data-status="saved"]', timeout=10000)
    op_id = ops_of(doc)[0]["id"]
    cases = [
        ("old_text_not_removed", {"op_id": op_id, "glyphs": 7}, "Part of the old text could not be removed"),
        ("text_move_not_applied", {"op_id": op_id, "dx": 40.0, "dy": 0.0}, "A moved text block could not be placed"),
    ]
    for code, details, words in cases:
        doc.force["POST /draft/commit"] = [(422, {"error": code, "message": "The engine refused", "details": details})]
        before = len(doc.commit_bodies)
        page.locator('[data-testid="editor-save"]').click()
        truthy(wait_until(page, lambda: len(doc.commit_bodies) > before, 8000), f"{code}: the commit was sent")
        error = page.wait_for_selector('[data-testid="session-error"]', timeout=8000)
        contains(error.inner_text(), words, f"{code}: said in words")
        equals(page.locator('[data-testid="editor-save-status"]').get_attribute("data-status"), "error",
               f"{code}: the save failed")
        equals(doc.current, 1, f"{code}: no new version")
        truthy(doc.draft is not None and len(ops_of(doc)) == 1, f"{code}: the draft (and its edit) is kept")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
