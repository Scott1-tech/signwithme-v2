"""
F2: «Download PDF» → the export dialog (EDITOR-V2-PLAN §7 «Top bar», B2
`POST …/document/exports`).

  * with the editor the top-bar button opens the dialog: «Include
    annotations (N)» -- N counts every annotation (hidden ones too);
  * ticked → `include_markup: true`, unticked → false; the job is followed
    and its file handed to the browser, then deleted on the server;
  * without the editor (older server) the button downloads the uploaded
    PDF as before; once the signed final exists, it downloads that.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _f2 import clean, click_pt, install, open_doc, select_tool, shot, wait_markup  # noqa: E402
from _harness import MockApi, Registry, cli_main, contains, equals, truthy  # noqa: E402

test = Registry("export (F2)")


@test("the_export_includes_the_annotations_when_ticked")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    select_tool(page, "square")
    click_pt(page, 200, 200)
    wait_markup(page, doc, lambda els: len(els) == 1)
    button = page.locator('[data-testid="editor-download"]')
    equals(button.get_attribute("data-source"), "export", "the export dialog")
    button.click()
    page.locator('[data-testid="export-dialog"]').wait_for()
    equals(page.locator('[data-testid="export-include-label"]').inner_text(), "Include annotations (1)", "counted")
    truthy(page.locator('[data-testid="export-include"]').is_checked(), "ticked by default")
    shot(page, "export_dialog")
    with page.expect_download(timeout=15000) as info:
        page.locator('[data-testid="export-download"]').click()
    download = info.value
    equals(doc.export_bodies[-1], {"include_markup": True}, "include_markup: true")
    with open(download.path(), "rb") as handle:
        data = handle.read()
    truthy(data.startswith(b"%PDF-"), "a PDF")
    truthy(data.endswith(b"% ZZ export include_markup=1\n"), "the server's export file")
    contains(download.suggested_filename, "annotated", f"named ({download.suggested_filename})")
    page.wait_for_selector('[data-testid="export-dialog"]', state="detached", timeout=5000)
    clean(mock)


@test("unticked_the_export_leaves_the_annotations_out")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    select_tool(page, "square")
    click_pt(page, 200, 200)
    wait_markup(page, doc, lambda els: len(els) == 1)
    page.locator('[data-testid="editor-download"]').click()
    page.locator('[data-testid="export-include"]').uncheck()
    with page.expect_download(timeout=15000) as info:
        page.locator('[data-testid="export-download"]').click()
    download = info.value
    equals(doc.export_bodies[-1], {"include_markup": False}, "include_markup: false")
    with open(download.path(), "rb") as handle:
        truthy(handle.read().endswith(b"% ZZ export include_markup=0\n"), "the plain export")
    clean(mock)


@test("in_an_edit_session_the_dialog_says_unsaved_edits_are_not_in_the_file")
def _(page, mock: MockApi) -> None:
    install(mock)
    open_doc(page, mock)
    page.locator('[data-testid="page-insert-1"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    page.locator('[data-testid="editor-download"]').click()
    page.locator('[data-testid="export-unsaved-note"]').wait_for()
    contains(page.locator('[data-testid="export-unsaved-note"]').inner_text(), "Save changes first", "says so")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
