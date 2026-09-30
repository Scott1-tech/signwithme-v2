"""
No automatic date, anywhere -- and server times read as UTC.

The user's decision (2026-09-25): a date is never put on a contract on its
own; it is chosen by hand, every time, before it is placed. The old default was
`new Date().toISOString().slice(0, 10)` -- the UTC date -- so between 00:00 and
05:00 in Tashkent (UTC+5) a contract was dated YESTERDAY (seen live: signed at
09-25 03:14, printed 09/24/2026).

    * every date input starts empty: the bulk "Date for every date field",
      the slot-mode "Contract date", each slot's own picker (the card and the
      on-page mini panel, which no longer has a "Today" button), and the
      editor's Date tool (`ann-date`);
    * trying to place a date without one places nothing, focuses the date
      box and says "Choose the date first";
    * the automatic fill still signs, but never dates, says how many date
      fields are left, and its Undo still takes back exactly what it did;
    * finalize stays off while a required date is empty and says to choose it;
    * at 02:30 in Tashkent (21:30 UTC the day before) no yesterday appears;
    * an audit time the server sends without an offset is read as UTC.

    python tests/ui/test_dates.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    BASE,
    MockApi,
    PAGE_W,
    Registry,
    cli_main,
    contains,
    ensure_target_is_up,
    equals,
    open_section,
    open_sign,
    truthy,
    arm_tool,
)

test = Registry("no automatic date")

CHOOSE_FIRST = "Choose the date first"

#: 02:30 on 2026-09-25 in Tashkent is 21:30 UTC on 2026-09-24.
TASHKENT_0230 = datetime(2026, 9, 24, 21, 30, 0, tzinfo=timezone.utc)
YESTERDAY_ISO = "2026-09-24"
YESTERDAY_US = "09/24/2026"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def dates_drawn(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.placed-dates text')].map((t) => t.textContent)"
    )


def stored_dates(page, mock: MockApi):
    raw = page.evaluate("(k) => localStorage.getItem(k)", f"swm.dates.{mock.contract_id}")
    return json.loads(raw) if raw else None


def focused_testid(page) -> str | None:
    return page.evaluate("() => document.activeElement?.dataset?.testid ?? null")


def all_date_inputs(page) -> list[dict]:
    return page.evaluate(
        """() => [...document.querySelectorAll('input[type="date"]')].map((el) => ({
             testid: el.dataset.testid || null,
             label: el.getAttribute('aria-label') || el.closest('label')?.textContent || '',
             value: el.value,
           }))"""
    )


def use_slot_mode(page) -> None:
    page.get_by_role("radio", name="Each one separately").click()
    page.wait_for_selector('[data-testid="contract-date"]', timeout=10000)


def hover_slot(page, field_id: str) -> None:
    page.locator(f'.pdf-page .slot[data-field-id="{field_id}"]').hover()
    page.wait_for_selector(f'[data-slot-tools="{field_id}"]', timeout=10000)


def open_edit(page, mock: MockApi) -> None:
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="ann-date"]', timeout=30000)
    page.wait_for_function(
        "() => { const p = document.querySelector('.pdf-page');"
        " return !!p && p.getBoundingClientRect().width > 300; }",
        timeout=30000,
    )
    page.wait_for_timeout(350)


def click_pt(page, x_pt: float, y_pt: float) -> None:
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    scale = box["width"] / PAGE_W
    page.mouse.click(box["x"] + x_pt * scale, box["y"] + y_pt * scale)


def library_signature(mock: MockApi) -> None:
    """A default saved signature, so the automatic fill has something to use."""
    mock.user_signatures = [{
        "id": "zz-lib-1",
        "label": "ZZ saved",
        "is_default": True,
        "signature_hash": "zz",
        "created_at": "2026-09-01T08:00:00+00:00",
    }]


def bulk_places_both(mock: MockApi) -> None:
    mock.signed_field_ids = []
    mock.bulk_response = {
        "status": 200,
        "body": {
            "contract_id": mock.contract_id,
            "placed": [
                {"field_id": s["field_id"], "signature_id": f"zz-auto-{s['field_id']}",
                 "page_number": 1, "bbox": s["bbox"]}
                for s in mock.slots if s["kind"] == "signature"
            ],
            "skipped": [],
            "failed": [],
        },
    }


def enable_auto_fill(page) -> None:
    # Runs after the harness' own init script, which turns the fill off.
    page.add_init_script("(() => { try { localStorage.setItem('swm.autoFill', 'true'); } catch (_) {} })();")


def in_tashkent_at_0230(page, mock: MockApi):
    """A second context, in Asia/Tashkent, with the clock pinned to 02:30 local."""
    context = page.context.browser.new_context(
        viewport={"width": 1600, "height": 1200},
        device_scale_factor=1,
        timezone_id="Asia/Tashkent",
        locale="en-US",
    )
    other = context.new_page()
    other.set_default_timeout(20000)
    other.clock.install(time=TASHKENT_0230)
    other.clock.pause_at(TASHKENT_0230)
    mock.install(other)
    return context, other


# --------------------------------------------------------------------------
# Every date input starts empty
# --------------------------------------------------------------------------

@test("every_date_input_starts_empty")
def _(page, mock: MockApi) -> None:
    """Bulk, contract date, slot cards, mini panel and the Date tool: all blank."""
    open_sign(page, mock, expect_images=2)
    equals(page.locator('[data-testid="bulk-date"]').input_value(), "", "bulk date is empty")

    use_slot_mode(page)
    equals(page.locator('[data-testid="contract-date"]').input_value(), "", "contract date is empty")
    values = [row["value"] for row in all_date_inputs(page)]
    truthy(len(values) >= 3, f"the slot cards' pickers are on screen too ({values})")
    equals(set(values), {""}, "every date picker on the page is empty")

    hover_slot(page, "date_1")
    mini = page.locator('[data-slot-tools="date_1"] input[type="date"]')
    equals(mini.input_value(), "", "the mini panel's picker is empty")
    equals(page.locator('[data-slot-tools="date_1"] button', has_text="Today").count(), 0,
           "the mini panel has no 'Today' shortcut any more")
    equals(page.get_by_role("button", name="use today as the date").count(), 0,
           "no 'use today' button anywhere")
    equals(dates_drawn(page), [], "nothing is drawn")
    equals(stored_dates(page, mock), None, "nothing is stored")

    open_edit(page, mock)
    equals(page.locator('[data-testid="ann-date"]').input_value(), "", "the Date tool's date is empty")
    clean(mock)


# --------------------------------------------------------------------------
# Placing without a date is refused
# --------------------------------------------------------------------------

@test("bulk_apply_without_a_date_places_no_date")
def _(page, mock: MockApi) -> None:
    open_sign(page, mock, expect_images=2)
    open_section(page, "sign")
    page.get_by_role("button", name="Apply to all", exact=True).click()
    page.wait_for_timeout(300)
    equals(dates_drawn(page), [], "no date was placed")
    equals(stored_dates(page, mock), None, "and none stored")
    contains(page.locator('[data-testid="choose-date-message"]').inner_text(), CHOOSE_FIRST,
             "the panel says why")
    equals(focused_testid(page), "bulk-date", "the date box has the focus")

    # Choosing one then works, with exactly that date.
    page.locator('[data-testid="bulk-date"]').fill("2026-09-25")
    page.get_by_role("button", name="Apply to all", exact=True).click()
    page.wait_for_function("() => document.querySelectorAll('.placed-dates text').length === 2")
    equals(sorted(dates_drawn(page)), ["09/25/2026", "09/25/2026"], "the chosen date, twice")
    equals(page.locator('[data-testid="choose-date-message"]').count(), 0, "the message is gone")
    clean(mock)


@test("bulk_apply_with_a_signature_but_no_date_signs_only")
def _(page, mock: MockApi) -> None:
    """Signatures go on; the dates stay empty and the user is sent to the date box."""
    library_signature(mock)
    bulk_places_both(mock)
    open_sign(page, mock, expect_images=0)
    open_section(page, "sign")
    page.get_by_role("button", name="Choose signature").click()
    page.locator(".sign-library__button").first.click()
    page.wait_for_function("() => !document.querySelector('.modal__panel')", timeout=10000)
    page.get_by_role("button", name="Apply to all", exact=True).click()
    page.wait_for_function("() => document.querySelectorAll('img.placed-signature').length === 2")
    page.wait_for_timeout(200)
    equals(dates_drawn(page), [], "no date was placed")
    contains(page.locator('[data-testid="choose-date-message"]').inner_text(), CHOOSE_FIRST,
             "the panel says to choose the date")
    equals(focused_testid(page), "bulk-date", "the date box has the focus")
    clean(mock)


@test("slot_mode_apply_date_without_a_date_is_refused")
def _(page, mock: MockApi) -> None:
    open_sign(page, mock, expect_images=2)
    use_slot_mode(page)
    button = page.get_by_role("button", name="Apply to all date fields")
    truthy(button.is_enabled(), "the button can be pressed, so it can explain itself")
    button.click()
    page.wait_for_timeout(200)
    equals(dates_drawn(page), [], "no date was placed")
    equals(stored_dates(page, mock), None, "and none stored")
    contains(page.locator('[data-testid="choose-date-message"]').inner_text(), CHOOSE_FIRST,
             "the card says why")
    equals(focused_testid(page), "contract-date", "the contract date box has the focus")

    page.locator('[data-testid="contract-date"]').fill("2026-10-02")
    button.click()
    page.wait_for_function("() => document.querySelectorAll('.placed-dates text').length === 2")
    equals(sorted(dates_drawn(page)), ["10/02/2026", "10/02/2026"], "the chosen date")
    clean(mock)


@test("the_date_tool_without_a_date_places_nothing")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm_tool(page, "date")
    click_pt(page, 260, 340)
    page.wait_for_timeout(300)
    equals(page.locator(".swm-ann").count(), 0, "no date annotation")
    contains(page.locator('[data-testid="editor-note"]').inner_text(), CHOOSE_FIRST,
             "the note line says why")
    equals(focused_testid(page), "ann-date", "the toolbar's date box has the focus")

    page.locator('[data-testid="ann-date"]').fill("2026-03-09")
    arm_tool(page, "date")
    click_pt(page, 260, 340)
    page.wait_for_function("() => document.querySelectorAll('.swm-ann').length === 1")
    texts = page.evaluate("() => [...document.querySelectorAll('.swm-ann')].map((a) => a.textContent)")
    contains(" ".join(texts), "03/09/2026", "the chosen date is placed")
    equals(page.locator('[data-testid="editor-note"]').count(), 0, "the note is cleared")
    clean(mock)


# --------------------------------------------------------------------------
# The automatic fill signs, never dates
# --------------------------------------------------------------------------

@test("auto_fill_signs_but_never_dates_and_undo_still_works")
def _(page, mock: MockApi) -> None:
    library_signature(mock)
    bulk_places_both(mock)
    enable_auto_fill(page)
    open_sign(page, mock, expect_images=2)
    page.wait_for_selector('[data-testid="auto-fill-note"]', timeout=15000)

    equals(dates_drawn(page), [], "the fill placed no date")
    equals(stored_dates(page, mock), None, "and stored none")
    equals(page.locator('[data-testid="bulk-date"]').input_value(), "", "the bulk date stays empty")
    note = page.locator('[data-testid="auto-fill-note"]').inner_text()
    contains(note, "2 signatures", "the note counts the signatures")
    needed = page.locator('[data-testid="dates-needed"]').inner_text()
    contains(needed, "Choose the date for 2 date fields", "and asks for the dates")
    truthy(page.locator('[data-testid="editor-finalize"]').is_disabled(), "finalize waits for the dates")

    page.get_by_role("button", name="Undo", exact=True).click()
    page.wait_for_function("() => document.querySelectorAll('img.placed-signature').length === 0")
    deletes = sorted(c for c in mock.calls if c.startswith("DELETE"))
    equals(len(deletes), 2, f"undo removed exactly the two automatic signatures ({deletes})")
    equals(page.locator('[data-testid="auto-fill-note"]').count(), 0, "the note is gone")
    equals(dates_drawn(page), [], "still no date")
    clean(mock)


@test("finalize_without_dates_is_blocked_and_points_at_the_date")
def _(page, mock: MockApi) -> None:
    open_sign(page, mock, expect_images=2)
    finalize = page.locator('[data-testid="editor-finalize"]')
    truthy(finalize.is_disabled(), "off while the dates are empty")
    text = page.locator('[data-testid="finalize-readiness"]').inner_text()
    contains(text, "2 dates", "it counts them")
    contains(text, "Choose the date for 2 date fields", "and says to choose the date")
    equals("finalize" in mock.bodies, False, "nothing was sent")
    clean(mock)


# --------------------------------------------------------------------------
# 02:30 in Tashkent: no yesterday anywhere
# --------------------------------------------------------------------------

@test("at_0230_tashkent_no_yesterday_appears")
def _(page, mock: MockApi) -> None:
    library_signature(mock)
    bulk_places_both(mock)
    context, other = in_tashkent_at_0230(page, mock)
    try:
        enable_auto_fill(other)
        open_sign(other, mock, expect_images=2)
        clock = other.evaluate(
            "() => ({ utc: new Date().toISOString(), localDay: new Date().getDate(),"
            " tz: Intl.DateTimeFormat().resolvedOptions().timeZone })"
        )
        equals(clock["tz"], "Asia/Tashkent", "the browser is in Tashkent")
        equals(clock["utc"][:16], "2026-09-24T21:30", "the clock is 21:30 UTC")
        equals(clock["localDay"], 25, "which is already the 25th locally")

        other.wait_for_selector('[data-testid="auto-fill-note"]', timeout=15000)
        equals(dates_drawn(other), [], "the automatic fill placed no date")
        use_slot_mode(other)
        values = [row["value"] for row in all_date_inputs(other)]
        equals(set(values), {""}, f"every date input is empty ({values})")
        hover_slot(other, "date_1")
        equals(other.locator('[data-slot-tools="date_1"] input[type="date"]').input_value(), "",
               "the mini panel too")
        open_edit(other, mock)
        equals(other.locator('[data-testid="ann-date"]').input_value(), "", "and the Date tool")

        html = other.content()
        truthy(YESTERDAY_US not in html and f'value="{YESTERDAY_ISO}"' not in html,
               "yesterday's date is nowhere on the page")
        equals(stored_dates(other, mock), None, "nor in storage")
        clean(mock)
    finally:
        context.close()


# --------------------------------------------------------------------------
# Server times are UTC
# --------------------------------------------------------------------------

@test("audit_and_list_times_are_read_as_utc")
def _(page, mock: MockApi) -> None:
    """`2026-09-24 22:15:28` from the server is 03:15:28 on the 25th in Tashkent."""
    context, other = in_tashkent_at_0230(page, mock)
    try:
        trail = [
            {"action": "contract_uploaded", "actor_email": None, "actor_role": None,
             "details": {}, "ip_address": None, "timestamp": "2026-09-24 22:15:28"},
            {"action": "signature_placed", "actor_email": None, "actor_role": None,
             "details": {}, "ip_address": None, "timestamp": "2026-09-24T22:15:28"},
            {"action": "contract_finalized", "actor_email": None, "actor_role": None,
             "details": {}, "ip_address": None, "timestamp": "2026-09-24T22:15:28+00:00"},
            {"action": "downloaded", "actor_email": None, "actor_role": None,
             "details": {}, "ip_address": None, "timestamp": "2026-09-24T22:15:28Z"},
        ]
        other.route(
            f"**/api/review/{mock.contract_id}/audit",
            lambda route: route.fulfill(status=200, content_type="application/json",
                                        body=json.dumps(trail)),
        )
        other.goto(f"{BASE}/contracts/{mock.contract_id}/final")
        other.wait_for_selector("table.table tbody tr", timeout=20000)
        cells = other.evaluate(
            "() => [...document.querySelectorAll('table.table tbody tr td:first-child')]"
            ".map((td) => td.textContent)"
        )
        equals(cells, ["9/25/2026, 3:15:28 AM"] * 4,
               "naive, 'T', '+00:00' and 'Z' all show 03:15 local, not 10:15 PM the day before")

        row = dict(mock.contract_payload(), created_at="2026-09-24T22:15:28")
        other.route("**/api/review/queue", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps([row])))
        other.goto(f"{BASE}/review")
        other.wait_for_selector(".list__item", timeout=20000)
        contains(other.locator(".list__item").first.inner_text(), "uploaded 9/25/2026",
                 "the review queue's date is the local one")
        clean(mock)
    finally:
        context.close()


if __name__ == "__main__":
    ensure_target_is_up()
    raise SystemExit(cli_main(test))
