"""
Continuous-scroll viewer + thumbnail strip: does it actually *draw*?

These checks exist because the rest of this directory could not tell the
difference between a document and an empty one. Every other suite reads
geometry out of inline styles and `data-*` attributes, which a viewer that
renders nothing still produces perfectly -- the pages would be the right size,
in the right place, and completely blank, and the run would be green.

So the assertions here are about pixels and about memory:

  * a page that is on screen has a canvas with a bitmap **and ink in it**
    (`getImageData`), not merely a sized box;
  * a page far from the viewport has handed its bitmap back (`canvas.width`
    is 0, `data-rendered` is gone) -- this is what keeps a 66-page contract
    from being 300 MB of canvas;
  * scrolling, Prev/Next, the thumbnail strip and zoom all agree on which page
    the reader is on, and zoom keeps that page under the eye.

The document is a synthetic multi-page PDF built in memory
(`_harness.synthetic_multipage_pdf`): every page carries a large filled block
and its own number, precisely so "painted" and "not painted" are told apart by
counting dark pixels. No real contract is ever opened.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    MockApi,
    Registry,
    cli_main,
    equals,
    open_review,
    rule_line,
    synthetic_multipage_pdf,
    truthy,
)

test = Registry("viewer + thumbs")

#: Enough pages that the render window is a small part of the document, few
#: enough that opening it stays quick.
DOC_PAGES = 24

#: Mirrors of the two ceilings the code owns. If either constant moves, this
#: file is the other half of the change.
MAX_RENDERED_PAGES = 6       # hooks/usePdfViewer.ts
MAX_DRAWN_THUMBS = 14        # components/PdfThumbnails.tsx


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def page_canvas(page_number: int) -> str:
    return f'.pdf-page[data-page-number="{page_number}"] canvas.pdf-canvas'


def thumb_canvas(page_number: int) -> str:
    return f'[data-testid="pdf-thumb-{page_number}"] canvas.pdf-thumb__canvas'


CANVAS_JS = """
(selector) => {
  const canvas = document.querySelector(selector);
  if (!canvas) return null;
  const width = canvas.width;
  const height = canvas.height;
  // A released canvas is 0 x 0; asking it for pixels would throw.
  if (!width || !height) return { width, height, ink: 0, dataUrl: 0 };
  const context = canvas.getContext('2d', { willReadFrequently: true });
  const data = context.getImageData(0, 0, width, height).data;
  let ink = 0;
  // Every fourth pixel is plenty to prove a page is not blank and keeps the
  // read quick on a full-size bitmap.
  for (let i = 0; i < data.length; i += 16) {
    if (data[i + 3] === 0) continue;                      // nothing drawn here
    if (data[i] < 200 || data[i + 1] < 200 || data[i + 2] < 200) ink += 1;
  }
  return { width, height, ink, dataUrl: canvas.toDataURL('image/png').length };
}
"""


def canvas_state(page, selector: str) -> dict:
    state = page.evaluate(CANVAS_JS, selector)
    if state is None:
        raise AssertionError(f"no canvas matching {selector!r}")
    return state


PAINTED_PAGES_JS = """
() => [...document.querySelectorAll('.pdf-page canvas.pdf-canvas')]
  .map((c) => (c.width > 0 && c.height > 0
    ? Number(c.closest('.pdf-page').dataset.pageNumber) : 0))
  .filter(Boolean)
"""

PAINTED_THUMBS_JS = """
() => [...document.querySelectorAll('.pdf-thumb canvas.pdf-thumb__canvas')]
  .map((c) => (c.width > 0 && c.height > 0
    ? Number(c.closest('.pdf-thumb').dataset.pageNumber) : 0))
  .filter(Boolean)
"""


def painted_pages(page) -> list[int]:
    return page.evaluate(PAINTED_PAGES_JS)


def painted_thumbs(page) -> list[int]:
    return page.evaluate(PAINTED_THUMBS_JS)


def open_doc(page, mock: MockApi, pages: int = DOC_PAGES) -> None:
    """Open the review screen on a synthetic document of `pages` pages."""
    mock.set_pdf(synthetic_multipage_pdf(pages, [rule_line(s) for s in mock.slots]))
    open_review(page, mock)
    page.wait_for_function(
        "(n) => document.querySelectorAll('.pdf-page').length === n",
        arg=pages,
        timeout=30000,
    )
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    # The initial fit re-renders at the fitted scale; settle so no assertion
    # lands between the two paints.
    page.wait_for_timeout(400)


def counter(page) -> str:
    """`Page 6 of 24`, whitespace normalised."""
    return " ".join(page.locator(".pdf-toolbar__page").inner_text().split())


def wait_for_counter(page, page_number: int, timeout: int = 15000) -> None:
    page.wait_for_function(
        "(n) => { const el = document.querySelector('.pdf-toolbar__page');"
        " return !!el && el.textContent.replace(/\\s+/g, ' ').trim()"
        ".startsWith('Page ' + n + ' of '); }",
        arg=page_number,
        timeout=timeout,
    )


SCROLL_TO_JS = """
(n) => {
  const stage = document.querySelector('.pdf-stage');
  const target = document.querySelector(`.pdf-page[data-page-number="${n}"]`);
  if (!stage || !target) return false;
  // What a wheel does, not what the app's own scrollToPage does: the point is
  // that `currentPage` follows the scroll position.
  stage.scrollTop += target.getBoundingClientRect().top
    - stage.getBoundingClientRect().top - 8;
  return true;
}
"""


def scroll_document_to(page, page_number: int) -> None:
    truthy(page.evaluate(SCROLL_TO_JS, page_number), f"page {page_number} is in the DOM")
    wait_for_counter(page, page_number)
    page.wait_for_timeout(400)


def current_page_marked(page) -> int:
    boxes = page.locator(".pdf-page--current")
    equals(boxes.count(), 1, "exactly one page is marked current")
    return int(boxes.first.get_attribute("data-page-number"))


def scale_of(page) -> float:
    """CSS pixels per PDF point, read the way every other suite reads it."""
    return page.evaluate(
        "() => { const p = document.querySelector('.pdf-page');"
        " return p ? p.getBoundingClientRect().width / 612 : 0; }"
    )


def stored_thumbs_flag(page):
    return page.evaluate("() => localStorage.getItem('swm.thumbs')")


# --------------------------------------------------------------------------
# The document really is drawn
# --------------------------------------------------------------------------

@test("visible_page_is_painted")
def _(page, mock: MockApi) -> None:
    """Page 1 holds a bitmap with ink in it, not just a correctly sized box."""
    open_doc(page, mock)
    state = canvas_state(page, page_canvas(1))
    truthy(state["width"] > 0 and state["height"] > 0,
           f"page 1 bitmap ({state['width']} x {state['height']})")
    truthy(state["ink"] > 1000, f"dark pixels on page 1 (counted {state['ink']})")
    truthy(state["dataUrl"] > 1000, "page 1 encodes to a non-trivial PNG")
    equals(page.locator('.pdf-page[data-page-number="1"][data-rendered]').count(), 1,
           "page 1 is flagged rendered")


@test("distant_page_is_released")
def _(page, mock: MockApi) -> None:
    """The far end of the document holds no memory: 0 x 0, no `data-rendered`."""
    open_doc(page, mock)
    far = DOC_PAGES
    state = canvas_state(page, page_canvas(far))
    equals(state["width"], 0, f"page {far} canvas width")
    equals(state["height"], 0, f"page {far} canvas height")
    equals(page.locator(f'.pdf-page[data-page-number="{far}"][data-rendered]').count(), 0,
           f"page {far} is not flagged rendered")
    # ... and its box is still the right size, or the scrollbar would lie.
    box = page.locator(f'.pdf-page[data-page-number="{far}"]').bounding_box()
    truthy(box and box["height"] > 300, "a released page still reserves its space")


@test("painted_pages_stay_bounded")
def _(page, mock: MockApi) -> None:
    """However far the reader scrolls, at most MAX_RENDERED_PAGES hold pixels."""
    open_doc(page, mock)
    for target in (8, 16, 3):
        scroll_document_to(page, target)
        painted = painted_pages(page)
        truthy(len(painted) <= MAX_RENDERED_PAGES,
               f"at page {target}: {len(painted)} painted pages {painted}")
        truthy(target in painted, f"page {target} itself is painted (have {painted})")


@test("scrolling_updates_the_page_counter")
def _(page, mock: MockApi) -> None:
    """`currentPage` is derived from the scroll position, marker included."""
    open_doc(page, mock)
    equals(counter(page), f"Page 1 of {DOC_PAGES}", "counter before scrolling")
    equals(current_page_marked(page), 1, "marked page before scrolling")

    scroll_document_to(page, 6)
    equals(counter(page), f"Page 6 of {DOC_PAGES}", "counter after scrolling")
    equals(current_page_marked(page), 6, "marked page after scrolling")

    state = canvas_state(page, page_canvas(6))
    truthy(state["ink"] > 1000, f"page 6 is painted where the reader is ({state['ink']})")


@test("prev_next_walk_the_document")
def _(page, mock: MockApi) -> None:
    """The toolbar buttons move one page and disable at the ends."""
    open_doc(page, mock)
    prev = page.get_by_role("button", name="‹ Prev")
    nxt = page.get_by_role("button", name="Next ›")
    truthy(prev.is_disabled(), "Prev is disabled on page 1")

    nxt.click()
    wait_for_counter(page, 2)
    equals(current_page_marked(page), 2, "Next moved one page")

    nxt.click()
    wait_for_counter(page, 3)
    prev.click()
    wait_for_counter(page, 2)
    equals(current_page_marked(page), 2, "Prev moved one page back")
    # The page under the eye is the one that was asked for.
    top = page.evaluate(
        "() => { const stage = document.querySelector('.pdf-stage');"
        " const el = document.querySelector('.pdf-page[data-page-number=\"2\"]');"
        " return el.getBoundingClientRect().top - stage.getBoundingClientRect().top; }"
    )
    truthy(abs(top) < 40, f"page 2 sits at the top of the stage (offset {top:.0f}px)")


@test("zoom_keeps_the_page_under_the_eye")
def _(page, mock: MockApi) -> None:
    """Zooming is anchored: the page being read must not change."""
    open_doc(page, mock)
    scroll_document_to(page, 7)
    before = scale_of(page)

    zoom_in = page.get_by_role("button", name="+", exact=True)
    zoom_in.click()
    page.wait_for_timeout(300)
    zoom_in.click()
    page.wait_for_timeout(500)

    after = scale_of(page)
    truthy(after > before + 0.1, f"scale grew ({before:.3f} -> {after:.3f})")
    equals(counter(page), f"Page 7 of {DOC_PAGES}", "counter after zooming")
    equals(current_page_marked(page), 7, "marked page after zooming")


@test("zoom_repaints_at_the_new_scale")
def _(page, mock: MockApi) -> None:
    """A zoomed page is re-rendered, not stretched: the bitmap itself grows.

    Waiting for the canvas to be *resized* is not enough -- `paintPage` sizes
    the bitmap and only then draws into it, so a check that reads the pixels
    the moment it grows finds an empty page. The wait below is therefore on
    ink at the new size, which is the only thing that proves a repaint.
    """
    open_doc(page, mock)
    before = canvas_state(page, page_canvas(1))

    page.get_by_role("button", name="+", exact=True).click()
    page.wait_for_function(
        """(args) => {
          const canvas = document.querySelector(args.selector);
          if (!canvas || canvas.width <= args.width) return false;
          const context = canvas.getContext('2d', { willReadFrequently: true });
          const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
          for (let i = 0; i < data.length; i += 64) {
            if (data[i + 3] !== 0 && data[i] < 200) return true;
          }
          return false;
        }""",
        arg={"selector": page_canvas(1), "width": before["width"]},
        timeout=20000,
    )
    after = canvas_state(page, page_canvas(1))
    truthy(after["width"] > before["width"],
           f"bitmap width grew ({before['width']} -> {after['width']})")
    truthy(after["ink"] > before["ink"],
           f"the bigger bitmap carries more ink ({before['ink']} -> {after['ink']})")


# --------------------------------------------------------------------------
# The thumbnail strip
# --------------------------------------------------------------------------

@test("thumbnails_list_every_page")
def _(page, mock: MockApi) -> None:
    """One thumbnail per page, labelled the way a screen reader will read it."""
    open_doc(page, mock)
    equals(page.locator('[data-testid="pdf-thumbs"]').count(), 1, "the strip is there")
    equals(page.locator(".pdf-thumb").count(), DOC_PAGES, "thumbnail count")
    equals(page.locator('[data-testid="pdf-thumb-5"]').get_attribute("aria-label"),
           "Page 5", "aria-label of the fifth thumbnail")


@test("visible_thumbnail_is_painted")
def _(page, mock: MockApi) -> None:
    """The first thumbnails are really drawn -- same pixel test as a page."""
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="pdf-thumb-1"][data-rendered]', timeout=20000)
    state = canvas_state(page, thumb_canvas(1))
    truthy(state["width"] > 0, f"thumbnail 1 bitmap ({state['width']} x {state['height']})")
    truthy(state["ink"] > 100, f"dark pixels in thumbnail 1 (counted {state['ink']})")


@test("distant_thumbnail_is_not_painted")
def _(page, mock: MockApi) -> None:
    """A thumbnail far down the strip costs nothing until it is scrolled to."""
    open_doc(page, mock)
    state = canvas_state(page, thumb_canvas(DOC_PAGES))
    equals(state["width"], 0, f"thumbnail {DOC_PAGES} canvas width")
    equals(
        page.locator(f'[data-testid="pdf-thumb-{DOC_PAGES}"][data-rendered]').count(), 0,
        f"thumbnail {DOC_PAGES} is not flagged rendered",
    )


@test("painted_thumbnails_stay_bounded")
def _(page, mock: MockApi) -> None:
    """Scrolling the whole strip must not leave every thumbnail in memory."""
    open_doc(page, mock)
    for position in (0.5, 1.0, 0.0):
        page.evaluate(
            "(fraction) => { const list = document.querySelector('.pdf-thumbs__list');"
            " list.scrollTop = (list.scrollHeight - list.clientHeight) * fraction; }",
            position,
        )
        page.wait_for_timeout(900)
        painted = painted_thumbs(page)
        truthy(len(painted) <= MAX_DRAWN_THUMBS,
               f"at {position:.0%} of the strip: {len(painted)} painted thumbnails")
    truthy(len(painted_thumbs(page)) > 0, "the strip still shows something at the top")


@test("thumbnail_click_moves_the_document")
def _(page, mock: MockApi) -> None:
    """The strip is navigation: a click lands on that page, painted."""
    open_doc(page, mock)
    page.locator('[data-testid="pdf-thumb-9"]').click()
    wait_for_counter(page, 9)
    equals(current_page_marked(page), 9, "marked page after the click")
    top = page.evaluate(
        "() => { const stage = document.querySelector('.pdf-stage');"
        " const el = document.querySelector('.pdf-page[data-page-number=\"9\"]');"
        " return el.getBoundingClientRect().top - stage.getBoundingClientRect().top; }"
    )
    truthy(abs(top) < 40, f"page 9 sits at the top of the stage (offset {top:.0f}px)")
    page.wait_for_selector('.pdf-page[data-page-number="9"][data-rendered]', timeout=20000)
    truthy(canvas_state(page, page_canvas(9))["ink"] > 1000, "page 9 is painted")


@test("current_thumbnail_is_marked_and_followed")
def _(page, mock: MockApi) -> None:
    """The strip marks the page being read and scrolls it into its own view."""
    open_doc(page, mock)
    marked = page.locator(".pdf-thumb--current")
    equals(marked.count(), 1, "exactly one thumbnail is current")
    equals(marked.first.get_attribute("data-page-number"), "1", "current thumbnail at rest")

    scroll_document_to(page, 15)
    equals(page.locator(".pdf-thumb--current").count(), 1, "still exactly one current")
    equals(page.locator(".pdf-thumb--current").first.get_attribute("data-page-number"),
           "15", "current thumbnail after scrolling")
    equals(page.locator('[data-testid="pdf-thumb-15"]').get_attribute("aria-current"),
           "page", "aria-current on the current thumbnail")

    # The panel scrolled itself (smooth), so give the animation a moment.
    page.wait_for_function(
        """() => {
          const list = document.querySelector('.pdf-thumbs__list');
          const item = document.querySelector('[data-testid="pdf-thumb-15"]');
          if (!list || !item) return false;
          const a = item.getBoundingClientRect();
          const b = list.getBoundingClientRect();
          return a.top >= b.top - 1 && a.bottom <= b.bottom + 1;
        }""",
        timeout=15000,
    )


@test("thumbnail_keyboard_navigation")
def _(page, mock: MockApi) -> None:
    """Arrow keys walk the strip, Enter goes to the page that has focus."""
    open_doc(page, mock)
    page.locator('[data-testid="pdf-thumb-1"]').focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    focused = page.evaluate("() => document.activeElement?.dataset?.pageNumber || null")
    equals(focused, "3", "focus after two ArrowDowns")

    page.keyboard.press("Enter")
    wait_for_counter(page, 3)
    equals(current_page_marked(page), 3, "Enter moved the document")

    page.keyboard.press("ArrowUp")
    equals(page.evaluate("() => document.activeElement?.dataset?.pageNumber || null"),
           "2", "focus after ArrowUp")


@test("thumbnail_panel_collapse_survives_reload")
def _(page, mock: MockApi) -> None:
    """Folding the strip away is remembered, and frees its canvases."""
    open_doc(page, mock)
    toggle = page.locator('[data-testid="pdf-thumbs-toggle"]')
    equals(toggle.get_attribute("aria-expanded"), "true", "expanded at first")

    toggle.click()
    page.wait_for_timeout(300)
    equals(page.locator('[data-testid="pdf-thumbs"]').count(), 0, "the strip is gone")
    equals(stored_thumbs_flag(page), "false", "the choice is stored")

    open_doc(page, mock)
    equals(page.locator('[data-testid="pdf-thumbs"]').count(), 0,
           "still folded away after a reload")
    equals(page.locator('[data-testid="pdf-thumbs-toggle"]').get_attribute("aria-expanded"),
           "false", "the toggle agrees after a reload")

    page.locator('[data-testid="pdf-thumbs-toggle"]').click()
    page.wait_for_selector('[data-testid="pdf-thumbs"]', timeout=15000)
    equals(stored_thumbs_flag(page), "true", "re-opening is stored too")
    # The document survived the toggle: the stage was never remounted.
    equals(page.locator(".pdf-page").count(), DOC_PAGES, "pages after re-opening the strip")


@test("thumbnails_do_not_reopen_the_document")
def _(page, mock: MockApi) -> None:
    """The strip draws out of the document the viewer already has open.

    The page itself fetches the file twice in dev -- `StrictMode` in
    `main.tsx` mounts every effect twice -- so the count at load says nothing.
    What must hold is that *drawing thumbnails* never fetches anything: the
    whole strip is scrolled through with the traffic log cleared.
    """
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="pdf-thumb-1"][data-rendered]', timeout=20000)
    at_load = [call for call in mock.calls if call.endswith("/file")]
    truthy(len(at_load) <= 2, f"the document is not fetched per page ({at_load})")

    mock.calls.clear()
    for position in (0.35, 0.7, 1.0):
        page.evaluate(
            "(fraction) => { const list = document.querySelector('.pdf-thumbs__list');"
            " list.scrollTop = (list.scrollHeight - list.clientHeight) * fraction; }",
            position,
        )
        page.wait_for_timeout(700)
    page.wait_for_selector(f'[data-testid="pdf-thumb-{DOC_PAGES}"][data-rendered]', timeout=20000)

    fetches = [call for call in mock.calls if call.endswith("/file")]
    equals(len(fetches), 0, f"PDF fetches while drawing thumbnails ({fetches})")


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
