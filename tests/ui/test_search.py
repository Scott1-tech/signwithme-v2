"""
Find in document: does the viewer really find the words, and mark them where
they are?

This viewer paints pages onto canvases and has no text layer, so the browser's
own Ctrl+F has nothing to search and a reader cannot select a word. The search
box is the replacement, and these checks hold it to three things:

  * it **finds** -- the count is right, the matches are in document order, and
    a query that matches nothing says so instead of silently doing nothing;
  * the marks are **where the words are**, in pixels: a screenshot of the page
    has highlighter yellow over the text and nothing but paper before the
    search, and the current match is a different colour from the rest;
  * it **changes nothing else**. The marks sit under the overlay and take no
    pointer events, so a slot box is still on its rule, still the thing under
    the cursor, and still draggable while a search is open.

The document is the same synthetic multi-page PDF the viewer checks use: every
page carries `Page N of M` in 48 pt Helvetica at a known spot (PDF 72, 650 --
i.e. 72 pt from the left and a box from y = 94 to y = 142 in the app's
top-left space), which is what makes "the mark is on the word" an arithmetic
statement rather than an impression. No real contract is ever opened.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    MockApi,
    Registry,
    approx,
    cli_main,
    equals,
    open_review,
    open_sign,
    pixel_bands,
    rule_line,
    snapshot,
    synthetic_multipage_pdf,
    truthy,
    zoom_out,
)

test = Registry("find in document")

#: Same document as `test_viewer.py`, so a page's contents are already known.
DOC_PAGES = 24
#: A long document, for the first-search progress and the cache behind it.
LONG_PAGES = 66

#: Where `Page N of M` sits on every synthetic page, in PDF points, top-left
#: origin: 48 pt Helvetica with its baseline at 142, so the box is 94..142.
TEXT_TOP_PT = 94.0
TEXT_HEIGHT_PT = 48.0
TEXT_LEFT_PT = 72.0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def open_doc(page, mock: MockApi, pages: int = DOC_PAGES) -> None:
    """The review screen on a synthetic document of `pages` pages."""
    mock.set_pdf(synthetic_multipage_pdf(pages, [rule_line(s) for s in mock.slots]))
    open_review(page, mock)
    page.wait_for_function(
        "(n) => document.querySelectorAll('.pdf-page').length === n",
        arg=pages,
        timeout=30000,
    )
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_timeout(400)


#: The counter is the only thing that says a search is over, and it is empty
#: both *before* a query is debounced and *after* one that matched nothing --
#: so "finished" is "the counter stopped changing and does not say Searching".
SETTLE_MS = 500

SETTLED_JS = """
(settle) => {
  const el = document.querySelector('[data-testid="pdf-search-count"]');
  const text = el ? el.textContent.trim() : '';
  const now = performance.now();
  const seen = window.__swmSearchSettle;
  if (!seen || seen.text !== text) {
    window.__swmSearchSettle = { text, at: now };
    return false;
  }
  return !text.startsWith('Searching') && now - seen.at > settle;
}
"""


def search_for(page, text: str, *, timeout: int = 40000) -> None:
    """Type a query and wait until the search has really finished running."""
    page.evaluate("() => { window.__swmSearchSettle = null; }")
    page.locator('[data-testid="pdf-search-input"]').fill(text)
    page.wait_for_function(SETTLED_JS, arg=SETTLE_MS, timeout=timeout)


def count_text(page) -> str:
    element = page.locator('[data-testid="pdf-search-count"]')
    return " ".join(element.inner_text().split()) if element.count() else ""


def counter(page) -> str:
    return " ".join(page.locator(".pdf-toolbar__page").inner_text().split())


def wait_for_counter(page, page_number: int, timeout: int = 15000) -> None:
    page.wait_for_function(
        "(n) => { const el = document.querySelector('.pdf-toolbar__page');"
        " return !!el && el.textContent.replace(/\\s+/g, ' ').trim()"
        ".startsWith('Page ' + n + ' of '); }",
        arg=page_number,
        timeout=timeout,
    )


MARKS_JS = """
(pageNumber) => {
  const box = document.querySelector(`.pdf-page[data-page-number="${pageNumber}"]`);
  if (!box) return null;
  const rect = box.getBoundingClientRect();
  const scale = rect.width / 612;   // upright page, US Letter
  return [...box.querySelectorAll('.pdf-search-hit')].map((mark) => {
    const b = mark.getBoundingClientRect();
    return {
      index: Number(mark.dataset.searchIndex),
      active: mark.classList.contains('pdf-search-hit--active'),
      xPt: (b.left - rect.left) / scale,
      yPt: (b.top - rect.top) / scale,
      widthPt: b.width / scale,
      heightPt: b.height / scale,
    };
  });
}
"""


def marks_on(page, page_number: int) -> list[dict]:
    found = page.evaluate(MARKS_JS, page_number)
    if found is None:
        raise AssertionError(f"page {page_number} is not in the DOM")
    return found


#: Zoom-out presses that bring a whole US-Letter page inside the stage at this
#: viewport (1600 x 1200). A pixel reading has to see the whole page or two of
#: them cannot be compared.
FIT_WHOLE_PAGE_CLICKS = 3


def yellow_on(page, page_number: int) -> tuple[int, int]:
    """(plain marks, current match) in real pixels of that page's screenshot."""
    bands = pixel_bands(page, f'.pdf-page[data-page-number="{page_number}"]')
    return bands["mark"], bands["active"]


def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_search")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


# --------------------------------------------------------------------------
# Finding
# --------------------------------------------------------------------------

@test("search_finds_every_page_and_counts_them")
def _(page, mock: MockApi) -> None:
    """`of 24` is on all 24 pages exactly once, and the counter says so."""
    open_doc(page, mock)
    equals(count_text(page), "", "no counter before anything is typed")

    search_for(page, "of 24")
    equals(count_text(page), f"1 / {DOC_PAGES}", "match counter")
    equals(page.locator(".pdf-search-hit").count(), DOC_PAGES, "marks in the document")
    equals(page.locator(".pdf-search-hit--active").count(), 1, "exactly one current match")
    # Every page of the document carries its own mark, not just the ones the
    # viewer has painted: the search reads text, the render window is separate.
    for page_number in (1, 12, DOC_PAGES):
        equals(len(marks_on(page, page_number)), 1, f"marks on page {page_number}")
    shot(page, "search_found")


@test("search_marks_sit_on_the_words")
def _(page, mock: MockApi) -> None:
    """A mark is the text's own box, not a guess at the middle of the page."""
    open_doc(page, mock)
    search_for(page, "of 24")

    marks = marks_on(page, 1)
    equals(len(marks), 1, "one mark on page 1")
    mark = marks[0]
    approx(mark["yPt"], TEXT_TOP_PT, 2.0, "mark top (pt)")
    approx(mark["heightPt"], TEXT_HEIGHT_PT, 2.0, "mark height (pt)")
    # `of 24` starts 7 characters into `Page 1 of 24`, so it must begin well
    # right of the line's left edge and still end on the page.
    truthy(mark["xPt"] > TEXT_LEFT_PT + 80, f"mark starts inside the line (x {mark['xPt']:.1f})")
    truthy(20 < mark["widthPt"] < 300, f"mark is the width of a word (got {mark['widthPt']:.1f})")

    # And a *narrower* query is a narrower mark, in the same line: the box
    # follows the characters, it is not one box per text run.
    search_for(page, "24")
    narrow = marks_on(page, 1)[0]
    approx(narrow["yPt"], TEXT_TOP_PT, 2.0, "narrow mark top (pt)")
    truthy(
        narrow["widthPt"] < mark["widthPt"] - 10,
        f"`24` is narrower than `of 24` ({narrow['widthPt']:.1f} vs {mark['widthPt']:.1f})",
    )
    truthy(
        narrow["xPt"] > mark["xPt"] + 10,
        f"`24` starts further right than `of 24` ({narrow['xPt']:.1f} vs {mark['xPt']:.1f})",
    )


@test("search_marks_are_really_painted")
def _(page, mock: MockApi) -> None:
    """Pixels, not class names: the page is highlighter yellow where the word is."""
    open_doc(page, mock)
    zoom_out(page, FIT_WHOLE_PAGE_CLICKS)
    before_plain, before_active = yellow_on(page, 1)
    equals(before_plain, 0, "yellow pixels before searching")
    equals(before_active, 0, "orange pixels before searching")

    search_for(page, "of 24")
    after_plain, after_active = yellow_on(page, 1)
    # Page 1 holds the *current* match, so its pixels are the orange ones.
    truthy(after_active > 500, f"current match is painted (counted {after_active})")
    shot(page, "search_marks_pixels")

    # A page that is not the current match is the plain yellow. The two
    # colours meet at the edges of every glyph, so a handful of antialiased
    # pixels always land in the other band -- what has to hold is which colour
    # the mark *is*, hence the ratio rather than a zero.
    page.locator('[data-testid="pdf-search-next"]').click()
    wait_for_counter(page, 2)
    page.wait_for_timeout(500)
    plain_on_1, active_on_1 = yellow_on(page, 1)
    truthy(plain_on_1 > 500, f"page 1 keeps a plain mark (counted {plain_on_1})")
    truthy(
        active_on_1 * 20 < plain_on_1,
        f"page 1 is no longer the current match ({active_on_1} orange vs {plain_on_1} yellow)",
    )

    plain_on_2, active_on_2 = yellow_on(page, 2)
    truthy(active_on_2 > 500, f"page 2 is now the current match (counted {active_on_2})")
    truthy(
        plain_on_2 * 20 < active_on_2,
        f"page 2 is drawn as the current match, not a plain one "
        f"({plain_on_2} yellow vs {active_on_2} orange)",
    )


@test("search_next_and_previous_walk_the_document")
def _(page, mock: MockApi) -> None:
    """Enter / Shift+Enter and the two buttons step through the matches."""
    open_doc(page, mock)
    search_for(page, "of 24")
    equals(count_text(page), f"1 / {DOC_PAGES}", "at the first match")

    page.locator('[data-testid="pdf-search-input"]').press("Enter")
    wait_for_counter(page, 2)
    equals(count_text(page), f"2 / {DOC_PAGES}", "after Enter")

    page.locator('[data-testid="pdf-search-input"]').press("Enter")
    wait_for_counter(page, 3)
    equals(count_text(page), f"3 / {DOC_PAGES}", "after a second Enter")
    # The match really is on screen, not merely counted.
    truthy(
        page.evaluate(
            """() => {
              const mark = document.querySelector('.pdf-search-hit--active');
              const stage = document.querySelector('.pdf-stage');
              if (!mark || !stage) return false;
              const a = mark.getBoundingClientRect();
              const b = stage.getBoundingClientRect();
              return a.top >= b.top - 2 && a.bottom <= b.bottom + 2;
            }"""
        ),
        "the current match is inside the stage",
    )

    page.locator('[data-testid="pdf-search-input"]').press("Shift+Enter")
    wait_for_counter(page, 2)
    equals(count_text(page), f"2 / {DOC_PAGES}", "after Shift+Enter")

    page.locator('[data-testid="pdf-search-prev"]').click()
    wait_for_counter(page, 1)
    equals(count_text(page), f"1 / {DOC_PAGES}", "after the Previous button")

    # Wrapping: one step back from the first match is the last one.
    page.locator('[data-testid="pdf-search-prev"]').click()
    wait_for_counter(page, DOC_PAGES)
    equals(count_text(page), f"{DOC_PAGES} / {DOC_PAGES}", "wrapped to the last match")


@test("search_ignores_case")
def _(page, mock: MockApi) -> None:
    """`PAGE 7 OF` and `page 7 of` are the same query."""
    open_doc(page, mock)
    search_for(page, "Page 7 of")
    equals(count_text(page), "1 / 1", "matches for the typed case")
    marked = marks_on(page, 7)
    equals(len(marked), 1, "the match is on page 7")

    search_for(page, "pAgE 7 Of")
    equals(count_text(page), "1 / 1", "matches for a mangled case")
    equals(len(marks_on(page, 7)), 1, "still on page 7")


@test("search_with_no_match_says_zero")
def _(page, mock: MockApi) -> None:
    """Nothing found is a result: `0`, and not one mark anywhere."""
    open_doc(page, mock)
    zoom_out(page, FIT_WHOLE_PAGE_CLICKS)
    search_for(page, "zzqqxx")
    equals(count_text(page), "0", "counter with no matches")
    equals(page.locator(".pdf-search-hit").count(), 0, "marks with no matches")
    plain, active = yellow_on(page, 1)
    equals(plain + active, 0, "painted pixels with no matches")


@test("escape_clears_the_search")
def _(page, mock: MockApi) -> None:
    """Escape empties the field and drops every mark; so does the ✕."""
    open_doc(page, mock)
    zoom_out(page, FIT_WHOLE_PAGE_CLICKS)
    search_for(page, "of 24")
    truthy(page.locator(".pdf-search-hit").count() > 0, "marks before Escape")

    page.locator('[data-testid="pdf-search-input"]').press("Escape")
    page.wait_for_timeout(400)
    equals(page.locator('[data-testid="pdf-search-input"]').input_value(), "",
           "the field after Escape")
    equals(page.locator(".pdf-search-hit").count(), 0, "marks after Escape")
    equals(count_text(page), "", "the counter is gone")
    plain, active = yellow_on(page, 1)
    equals(plain + active, 0, "painted pixels after Escape")

    search_for(page, "of 24")
    page.locator('[data-testid="pdf-search-clear"]').click()
    page.wait_for_timeout(400)
    equals(page.locator(".pdf-search-hit").count(), 0, "marks after the clear button")


@test("a_blank_query_marks_nothing")
def _(page, mock: MockApi) -> None:
    """Spaces are not a search: no counter, no marks, no work."""
    open_doc(page, mock)
    page.locator('[data-testid="pdf-search-input"]').fill("   ")
    page.wait_for_timeout(700)
    equals(count_text(page), "", "counter for a blank query")
    equals(page.locator(".pdf-search-hit").count(), 0, "marks for a blank query")


# --------------------------------------------------------------------------
# It must not get in the way
# --------------------------------------------------------------------------

@test("search_marks_stay_under_the_slots")
def _(page, mock: MockApi) -> None:
    """A highlight is below the overlay and invisible to the pointer.

    Two separate claims, both of which the placement depends on: the marks
    paint *under* the slot boxes and the placed signatures (paint order), and
    they cannot be clicked, dragged or hovered (`pointer-events: none`), so a
    mark that happens to fall on a slot changes nothing about that slot.
    """
    mock.set_pdf(synthetic_multipage_pdf(DOC_PAGES, [rule_line(s) for s in mock.slots]))
    open_sign(page, mock, expect_images=2)
    before = snapshot(page)

    search_for(page, "Page 1 of")
    truthy(page.locator(".pdf-search-hit").count() > 0, "the line is marked")

    after = snapshot(page)
    equals(after["slots"], before["slots"], "slot geometry is untouched by a search")
    equals(after["placed"], before["placed"], "placed signatures are untouched")

    # Paint order: the mark is before the overlay in the page, so the overlay
    # wins. And hit-testing agrees -- the slot, never the mark, is on top.
    order = page.evaluate(
        """() => {
          const box = document.querySelector('.pdf-page[data-page-number="1"]');
          const kids = [...box.children].map((el) => el.className);
          const layer = kids.findIndex((c) => String(c).includes('pdf-search-layer'));
          const overlay = kids.findIndex((c) => String(c).includes('pdf-overlay'));
          return { layer, overlay };
        }"""
    )
    truthy(order["layer"] >= 0, "the search layer is a child of the page")
    truthy(order["layer"] < order["overlay"], f"marks paint under the overlay ({order})")

    # Hit-testing: the middle of a mark belongs to whatever is behind it, and
    # the middle of a slot is still the slot.
    over_mark = page.evaluate(
        """() => {
          const mark = document.querySelector('.pdf-search-hit');
          const b = mark.getBoundingClientRect();
          const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
          return String(el?.className || el?.tagName || '');
        }"""
    )
    truthy(
        "pdf-search-hit" not in over_mark,
        f"a mark takes no pointer events (cursor found {over_mark!r})",
    )
    over_slot = page.evaluate(
        """() => {
          const slot = document.querySelector('.pdf-page .slot[data-field-id="sig_1"]');
          const b = slot.getBoundingClientRect();
          const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
          return el?.closest('[data-field-id]')?.dataset?.fieldId || String(el?.className || '');
        }"""
    )
    equals(over_slot, "sig_1", "the slot is still what the cursor lands on")
    shot(page, "search_under_slots")


@test("search_leaves_dragging_alone")
def _(page, mock: MockApi) -> None:
    """`useSlotDrag` is untouched: a slot still drags with a search open."""
    from _harness import drag_slot, stored_layout  # local: only this check needs them

    mock.set_pdf(synthetic_multipage_pdf(DOC_PAGES, [rule_line(s) for s in mock.slots]))
    open_sign(page, mock, expect_images=2)
    search_for(page, "Page 1 of")

    scale = snapshot(page)["scale"]
    drag_slot(page, "sig_1", 30, -20)
    stored = stored_layout(page, mock)
    truthy(stored, "a layout was written while the search was open")
    approx(stored["slot_offsets_x_pt"].get("sig_1"), 30 / scale, 0.35, "dx in points")
    approx(stored["slot_offsets_pt"].get("sig_1"), 20 / scale, 0.35, "dy in points")
    truthy(page.locator(".pdf-search-hit").count() > 0, "the marks survived the drag")


# --------------------------------------------------------------------------
# The cache behind it
# --------------------------------------------------------------------------

@test("first_search_reports_progress_then_runs_from_the_cache")
def _(page, mock: MockApi) -> None:
    """66 pages: the first query shows how far it has got; the next is cheap.

    The point is the text cache in `hooks/usePdfViewer.ts`. Reading a page's
    text is a round trip through the pdf.js worker; searching a string that is
    already in hand is not. So the first query over a long document reports
    progress, and a second query -- which has to touch every page again -- must
    come back markedly faster without fetching the document a second time.
    """
    open_doc(page, mock, LONG_PAGES)

    # Record every value the counter takes, before a single character is typed.
    page.evaluate(
        """() => {
          window.__swmCounts = [];
          const root = document.querySelector('.pdf-search');
          new MutationObserver(() => {
            const el = document.querySelector('[data-testid="pdf-search-count"]');
            const text = el ? el.textContent.trim() : '';
            const seen = window.__swmCounts;
            if (text && seen[seen.length - 1] !== text) seen.push(text);
          }).observe(root, { childList: true, subtree: true, characterData: true });
        }"""
    )

    mock.calls.clear()
    started = time.time()
    search_for(page, "of 66")
    cold = time.time() - started
    equals(count_text(page), f"1 / {LONG_PAGES}", "matches over a long document")

    seen = page.evaluate("() => window.__swmCounts || []")
    truthy(
        any(value.startswith("Searching") for value in seen),
        f"the first search reported progress (saw {seen[:6]})",
    )
    truthy(
        any(value.startswith("Searching") and "/ 66" in value for value in seen),
        f"progress counts pages, not matches (saw {seen[:6]})",
    )

    # Second query, every page again, this time out of the cache.
    page.locator('[data-testid="pdf-search-input"]').press("Escape")
    page.wait_for_timeout(300)
    started = time.time()
    search_for(page, "age 4 of")
    warm = time.time() - started
    equals(count_text(page), "1 / 1", "the second query found its one match")

    truthy(warm < cold, f"the cached search is faster (cold {cold:.2f}s, warm {warm:.2f}s)")
    fetches = [call for call in mock.calls if call.endswith("/file")]
    equals(len(fetches), 0, f"searching never re-fetches the document ({fetches})")
    print(f"        cold {cold:.2f}s -> warm {warm:.2f}s over {LONG_PAGES} pages")


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
