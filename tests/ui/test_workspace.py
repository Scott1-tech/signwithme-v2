"""
The single contract workspace (W1): review + signing + the editor on one
screen, behind `/contracts/:id/review`, `/sign` and `/edit`.

WHAT THESE CHECKS ARE FOR
-------------------------
The three screens used to be three route elements; each checked its own
behaviour and nothing could check that they agree, because they were never on
the screen together. Now they are one element, and these checks pin the rules
that make that safe (plan §1.5, §2, §5 W1):

  * the three addresses render ONE workspace, and moving between them is a
    change of panel, not a new page: the element survives, the PDF is not
    fetched again, and the chosen dates are still there;
  * approving a contract switches to signing in place (URL `/sign`, no reload);
  * the tool the workspace opens with follows the address (`/sign` → Signature,
    otherwise Select), and so does which panel section is open;
  * a slot is drawn once (`.slot`), a slot signature is `.placed-signature`, a
    free signature is `.swm-sig` -- never both for the same row;
  * with a text/date/check/cross tool armed the slot layer steps aside, so a
    click on a slot places the annotation instead of opening the chooser;
  * finalize is ONE button, and its body carries dates + layout + annotations;
  * on a contract nobody approved yet the Signature tool and finalize are off
    and say why, while the annotation tools still work;
  * a sidebar issue lights up its slot (`slot--active`): one selection;
  * Delete and the arrow keys only ever touch a selected annotation;
  * a turned page says PLACEMENT on `/sign` and READONLY on `/review`;
  * the Objects list is exactly what is on the page.

Nothing here opens a real contract: the document is the synthetic PDF from
`_harness.py`, every `/api/*` call is answered by `MockApi`, and the
screenshots (`c:\\PTG\\.playwright-mcp\\swm_workspace\\`) are synthetic by
construction.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    BASE,
    MockApi,
    Registry,
    apply_dates_to_all,
    approx,
    arm_tool,
    cli_main,
    contains,
    default_slots,
    equals,
    open_review,
    open_sign,
    stored_layout,
    truthy,
)

test = Registry("workspace")

PAGE_W = 612.0
GRID_PT = 0.5
PLACEMENT_NOTE = "Rotated view — placement disabled; rotate back to place signatures"
READONLY_NOTE = "Rotated view — marks are read-only; rotate back to interact"
NOT_APPROVED = "Contract not approved yet"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_workspace")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


def open_edit(page, mock: MockApi) -> None:
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_function(
        "() => { const p = document.querySelector('.pdf-page');"
        " return !!p && p.getBoundingClientRect().width > 300; }",
        timeout=30000,
    )
    page.wait_for_timeout(350)


def page_rect(page) -> dict:
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    if box is None:
        raise AssertionError("page 1 has no box")
    return box


def scale_of(page) -> float:
    return page_rect(page)["width"] / PAGE_W


def click_pt(page, x_pt: float, y_pt: float) -> None:
    """Click page 1 at a point given in PDF points.

    Keep `y_pt` under ~560: at the fitted scale anything lower is below the
    bottom of the 1200 px viewport, and a click there reaches nothing.
    """
    box = page_rect(page)
    scale = box["width"] / PAGE_W
    page.mouse.click(box["x"] + x_pt * scale, box["y"] + y_pt * scale)


def file_fetches(mock: MockApi) -> int:
    return sum(1 for call in mock.calls if call.endswith("/file"))


def mark_workspace(page) -> None:
    """Remember the workspace's DOM node, to prove later it was never remounted."""
    page.evaluate("() => { window.__swmWorkspace = document.querySelector('[data-testid=\"workspace\"]'); }")


def same_workspace(page) -> bool:
    return page.evaluate(
        "() => !!window.__swmWorkspace && window.__swmWorkspace.isConnected &&"
        " window.__swmWorkspace === document.querySelector('[data-testid=\"workspace\"]')"
    )


def spa_navigate(page, path: str) -> None:
    """Change the address the way an in-app link does: history + popstate, no reload."""
    page.evaluate(
        "(url) => { window.history.pushState({}, '', url);"
        " window.dispatchEvent(new PopStateEvent('popstate', { state: {} })); }",
        path,
    )
    page.wait_for_timeout(300)


def tool_pressed(page, name: str) -> bool:
    return page.locator(f'[data-testid="tool-{name}"]').get_attribute("aria-pressed") == "true"


def section_open(page, name: str) -> bool:
    return page.locator(f'[data-testid="section-{name}"]').get_attribute("data-open") == "true"


def draw_on_pad(page) -> None:
    box = page.locator(".signature-pad__canvas").bounding_box()
    if box is None:
        raise AssertionError("the signature pad has no box")
    mid = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 30, mid)
    page.mouse.down()
    page.mouse.move(box["x"] + 120, mid - 22, steps=8)
    page.mouse.move(box["x"] + 230, mid + 18, steps=8)
    page.mouse.up()
    page.wait_for_timeout(150)


ANNS_JS = """
() => [...document.querySelectorAll('.swm-ann')].map((el) => ({
  id: el.dataset.annId, kind: el.dataset.kind, page: Number(el.dataset.page),
  x: Number(el.dataset.xPt), y: Number(el.dataset.yPt),
  selected: el.dataset.selected === 'true',
}))
"""


def anns(page) -> list[dict]:
    return page.evaluate(ANNS_JS)


def wait_for_anns(page, count: int) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('.swm-ann').length === n", arg=count, timeout=15000
    )


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def rotate_once(page) -> None:
    page.locator('[data-testid="pdf-rotate"]').click()
    page.wait_for_function(
        "() => document.querySelector('.pdf-page[data-page-number=\"1\"]')"
        "?.getAttribute('data-rotation') === '90'",
        timeout=15000,
    )
    page.wait_for_timeout(500)


# --------------------------------------------------------------------------
# One workspace, three addresses
# --------------------------------------------------------------------------

@test("three_addresses_render_one_workspace")
def _(page, mock: MockApi) -> None:
    """Each URL is the same element; the segment picks the open section and the first tool."""
    expected = {
        "review": ("select", ["review"]),
        "sign": ("signature", ["sign", "layout"]),
        "edit": ("select", ["objects"]),
    }
    for panel, (tool, opened) in expected.items():
        page.goto(f"{BASE}/contracts/{mock.contract_id}/{panel}")
        page.wait_for_selector(f'[data-testid="workspace"][data-panel="{panel}"]', timeout=30000)
        ws = page.locator('[data-testid="workspace"]')
        equals(ws.count(), 1, f"/{panel}: exactly one workspace")
        classes = ws.get_attribute("class") or ""
        truthy("review" in classes.split() and "workspace" in classes.split(),
               f"/{panel}: carries `review workspace` ({classes})")
        equals(page.locator(".review__document").count(), 1, f"/{panel}: one document column")
        equals(page.locator(".review__panel").count(), 1, f"/{panel}: one panel")
        equals(page.locator('[data-testid="editor-toolbar"]').count(), 1, f"/{panel}: the toolbar")
        equals(page.locator('[data-testid="editor-finalize"]').count(), 1,
               f"/{panel}: exactly one finalize button")
        truthy(tool_pressed(page, tool), f"/{panel}: opens with the {tool} tool")
        for name in ("review", "sign", "layout", "objects"):
            equals(section_open(page, name), name in opened,
                   f"/{panel}: section {name} is {'open' if name in opened else 'closed'}")
        for gone in ("document-editor", "open-editor-link"):
            equals(page.locator(f'[data-testid="{gone}"]').count(), 0, f"/{panel}: no {gone}")
        page.wait_for_timeout(250)
        shot(page, f"{panel}_direct")

    # An unknown segment goes to the review, and so does a bare contract URL
    # of an approved contract go to signing.
    page.goto(f"{BASE}/contracts/{mock.contract_id}/nonsense")
    page.wait_for_selector('[data-testid="workspace"][data-panel="review"]', timeout=30000)
    truthy(page.url.endswith("/review"), f"an unknown panel lands on /review ({page.url})")
    page.goto(f"{BASE}/contracts/{mock.contract_id}")
    page.wait_for_selector('[data-testid="workspace"][data-panel="sign"]', timeout=30000)
    truthy(page.url.endswith("/sign"), f"an approved contract opens on /sign ({page.url})")
    clean(mock)


@test("switching_panels_never_reloads_the_document")
def _(page, mock: MockApi) -> None:
    """Continue to signing, and history moves: same element, no second `/file`."""
    open_review(page, mock)
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=20000)
    mark_workspace(page)
    before = file_fetches(mock)
    truthy(before >= 1, "the document was fetched once to open it")

    page.get_by_role("button", name="Continue to signing").click()
    page.wait_for_selector('[data-testid="workspace"][data-panel="sign"]', timeout=15000)
    truthy(page.url.endswith("/sign"), f"the address is /sign ({page.url})")
    truthy(same_workspace(page), "the workspace was not remounted")
    # The sections open in the effect that follows the address change.
    page.wait_for_selector('[data-testid="section-layout"][data-open="true"]', timeout=10000)
    truthy(section_open(page, "sign") and section_open(page, "layout"),
           "the signing sections were opened")
    equals(page.locator('[data-testid="layout-panel"]').count(), 1, "the layout panel is there")

    spa_navigate(page, f"/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="workspace"][data-panel="edit"]', timeout=15000)
    truthy(same_workspace(page), "still the same workspace on /edit")
    page.wait_for_selector('[data-testid="section-objects"][data-open="true"]', timeout=10000)
    truthy(section_open(page, "objects"), "the Objects section was opened")
    # The canvas kept its bitmap: nothing had to be drawn again from a new file.
    truthy(page.locator('.pdf-page[data-page-number="1"][data-rendered]').count() == 1,
           "page 1 is still painted")
    equals(file_fetches(mock), before, "switching panels fetched the PDF zero more times")
    clean(mock)


@test("approving_switches_to_signing_in_place")
def _(page, mock: MockApi) -> None:
    """Approve → URL `/sign`, same element, no reload; the signing gate opens."""
    mock.review_status = "pending"
    open_review(page, mock)
    page.wait_for_selector(".review__decision", timeout=20000)
    mark_workspace(page)
    fetched = file_fetches(mock)
    truthy(page.locator('[data-testid="tool-signature"]').is_disabled(),
           "before approval the Signature tool is off")

    page.get_by_role("button", name="Approve for signing").click()
    page.wait_for_url(f"**/contracts/{mock.contract_id}/sign", timeout=20000)
    page.wait_for_function(
        "() => !document.querySelector('[data-testid=\"tool-signature\"]').disabled",
        timeout=15000,
    )
    equals((mock.bodies.get("review") or {}).get("decision"), "approved", "the decision was sent")
    truthy(same_workspace(page), "approval did not remount the workspace")
    equals(file_fetches(mock), fetched, "nor fetch the PDF again")
    page.wait_for_selector('[data-testid="section-sign"][data-open="true"]', timeout=10000)
    truthy(section_open(page, "sign"), "the signing section is open")
    # The tool is whatever it was: the address only picks the FIRST tool.
    truthy(tool_pressed(page, "select"), "the armed tool did not change behind the user's back")
    equals(page.locator('[data-testid="finalize-blocked-reason"]').count(), 0,
           "finalize no longer says the contract is unapproved")
    clean(mock)


# --------------------------------------------------------------------------
# Slots, slot signatures, free signatures
# --------------------------------------------------------------------------

@test("a_text_tool_click_on_a_slot_places_text")
def _(page, mock: MockApi) -> None:
    """Placing tools make the slot layer step aside; Signature and Select keep it."""
    mock.set_slots(default_slots(), signed=[])
    open_sign(page, mock)
    slot = page.locator('.pdf-page .slot[data-field-id="sig_1"]')
    box = slot.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    hit = "(p) => String(document.elementFromPoint(p[0], p[1])?.className || '')"
    contains(page.evaluate(hit, [cx, cy]), "slot", "with the Signature tool the slot is the target")

    arm_tool(page, "text")
    page.wait_for_timeout(120)
    under = page.evaluate(hit, [cx, cy])
    truthy(not any(name.startswith("slot") for name in under.split()),
           f"with Text the slot takes no pointer events ({under!r})")

    page.mouse.click(cx, cy)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    page.keyboard.type("N/A")
    page.keyboard.press("Enter")
    wait_for_anns(page, 1)
    placed = anns(page)[0]
    equals(placed["kind"], "text", "a text annotation, not a signature")
    scale = scale_of(page)
    page_box = page_rect(page)
    approx(placed["x"], round((cx - page_box["x"]) / scale / GRID_PT) * GRID_PT, 1.0,
           "at the slot's centre (x, pt)")
    equals(page.locator(".modal__panel").count(), 0, "the chooser did not open")
    equals(mock.bodies.get("place"), None, "nothing was placed on the server")

    # Back to Select: the slot is live again, and a press only focuses it. (Its
    # top-left corner: the new run now sits on the centre, above the slot.)
    page.locator('[data-testid="tool-select"]').click()
    page.wait_for_timeout(120)
    contains(page.evaluate(hit, [box["x"] + 6, box["y"] + 6]), "slot",
             "with Select the slot is the target again")
    slot.click(position={"x": 6, "y": 6})
    page.wait_for_timeout(200)
    contains(slot.get_attribute("class") or "", "slot--active", "Select focuses the slot")
    equals(page.locator(".modal__panel").count(), 0, "and does not open the chooser")
    clean(mock)


@test("slot_and_free_signatures_are_drawn_once_each")
def _(page, mock: MockApi) -> None:
    """Slot rows are `.placed-signature`, the free row is `.swm-sig` -- no duplicates."""
    mock.place_response = {
        "status": 200,
        "body": {
            "id": "zz-sig-free",
            "contract_id": mock.contract_id,
            "signer_name": None,
            "signer_email": None,
            "signer_role": "contractor",
            "page_number": 1,
            "bbox": {"x": 310, "y": 560, "width": 180, "height": 34},
            "field_id": None,
            "signed_at": "2026-09-25T08:00:00+00:00",
        },
    }
    open_sign(page, mock, expect_images=2)
    # The Signature tool is armed on /sign: empty paper opens the free chooser.
    click_pt(page, 400, 577)
    page.wait_for_selector(".modal__panel", timeout=15000)
    draw_on_pad(page)
    page.get_by_role("button", name="Apply signature").click()
    page.wait_for_function("() => document.querySelectorAll('.swm-sig').length === 1", timeout=20000)

    equals((mock.bodies.get("place") or {}).get("field_id"), None, "a free placement (field_id null)")
    placed = page.evaluate(
        "() => [...document.querySelectorAll('.placed-signature')].map((e) => e.dataset.signatureId)"
    )
    free = page.evaluate(
        "() => [...document.querySelectorAll('.swm-sig')].map((e) => e.dataset.signatureId)"
    )
    equals(sorted(placed), ["zz-sig-1", "zz-sig-2"], "the two slot rows, as .placed-signature")
    equals(free, ["zz-sig-free"], "the free row, as .swm-sig")
    truthy(not set(placed) & set(free), "no row is drawn twice")
    contains(page.locator('[data-testid="sig-count"]').inner_text(), "1 signature",
             "the toolbar counts the free one")
    shot(page, "sign_free_and_slot_signatures")
    clean(mock)


# --------------------------------------------------------------------------
# Finalize
# --------------------------------------------------------------------------

@test("one_finalize_sends_dates_layout_and_annotations")
def _(page, mock: MockApi) -> None:
    """The signing gate, the signing body, and the editor's annotations -- in one request."""
    open_sign(page, mock, expect_images=2)
    shot(page, "sign_open")
    finalize = page.locator('[data-testid="editor-finalize"]')
    truthy(finalize.is_disabled(), "off while a required date is missing")
    contains(page.locator('[data-testid="finalize-readiness"]').inner_text(), "2 dates",
             "and it says what is missing")

    apply_dates_to_all(page)
    arm_tool(page, "check")
    click_pt(page, 520, 470)
    wait_for_anns(page, 1)
    truthy(finalize.is_enabled(), "on once every required slot is filled")
    finalize.click()
    page.wait_for_function("() => window.location.pathname.endsWith('/final')", timeout=20000)

    body = mock.bodies.get("finalize") or {}
    equals(sorted(e["field_id"] for e in body.get("dates") or []), ["date_1", "date_2"],
           "the chosen dates")
    truthy(isinstance(body.get("layout"), dict), "the layout")
    sent = body.get("annotations") or []
    equals([e["kind"] for e in sent], ["check"], "the annotation")
    equals(body.get("apply_paired_dates"), False, "and the signing body's own flag")
    clean(mock)


@test("an_unapproved_contract_keeps_the_local_tools_only")
def _(page, mock: MockApi) -> None:
    """Signature tool and finalize off, with the reason; text still works."""
    mock.review_status = "pending"
    open_sign(page, mock)
    signature = page.locator('[data-testid="tool-signature"]')
    truthy(signature.is_disabled(), "the Signature tool is off")
    contains(signature.get_attribute("aria-label") or "", NOT_APPROVED, "and says why on itself")
    contains(page.locator('[data-testid="tool-disabled-reason"]').inner_text(), NOT_APPROVED,
             "and beside the tools")
    truthy(page.locator('.pdf-page[data-page-number="1"]').get_attribute("class").find(
        "pdf-page--interactive") < 0, "the page does not take a signature click")
    click_pt(page, 400, 480)
    page.wait_for_timeout(300)
    equals(page.locator(".modal__panel").count(), 0, "a click on the page opens no chooser")

    finalize = page.locator('[data-testid="editor-finalize"]')
    truthy(finalize.is_disabled(), "finalize is off")
    contains(page.locator('[data-testid="finalize-blocked-reason"]').inner_text(), NOT_APPROVED,
             "and says why")

    arm_tool(page, "text")
    click_pt(page, 300, 520)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    page.keyboard.type("Pending note")
    page.keyboard.press("Enter")
    wait_for_anns(page, 1)
    equals(anns(page)[0]["kind"], "text", "a local annotation is still placed")
    shot(page, "unapproved")
    clean(mock)


# --------------------------------------------------------------------------
# State that must survive a panel switch
# --------------------------------------------------------------------------

@test("dates_survive_sign_edit_sign")
def _(page, mock: MockApi) -> None:
    """`/sign` → `/edit` → `/sign`: the chosen dates are drawn and stored throughout."""
    open_sign(page, mock, expect_images=2)
    apply_dates_to_all(page)
    mark_workspace(page)
    fetched = file_fetches(mock)
    texts = lambda: page.evaluate(  # noqa: E731
        "() => [...document.querySelectorAll('.placed-dates text')].map((t) => t.dataset.fieldId).sort()"
    )
    equals(texts(), ["date_1", "date_2"], "both dates drawn on /sign")

    spa_navigate(page, f"/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="workspace"][data-panel="edit"]', timeout=15000)
    equals(texts(), ["date_1", "date_2"], "still drawn on /edit")
    shot(page, "edit_open")

    spa_navigate(page, f"/contracts/{mock.contract_id}/sign")
    page.wait_for_selector('[data-testid="workspace"][data-panel="sign"]', timeout=15000)
    equals(texts(), ["date_1", "date_2"], "and back on /sign")
    truthy(same_workspace(page), "one workspace the whole way")
    equals(file_fetches(mock), fetched, "no PDF fetch on the way")
    raw = page.evaluate("(k) => localStorage.getItem(k)", f"swm.dates.{mock.contract_id}")
    equals(sorted(json.loads(raw or "{}").keys()), ["date_1", "date_2"], "and stored")
    clean(mock)


# --------------------------------------------------------------------------
# One selection
# --------------------------------------------------------------------------

@test("a_sidebar_issue_lights_up_its_slot")
def _(page, mock: MockApi) -> None:
    """The review draws no slot highlight of its own; a sidebar pick marks the `.slot`."""
    open_review(page, mock)
    page.wait_for_function(
        "() => document.querySelectorAll('.pdf-page .slot[data-field-id]').length === 4",
        timeout=30000,
    )
    slot_ids = ["sig_1", "date_1", "sig_2", "date_2"]
    highlights = page.evaluate(
        "(ids) => [...document.querySelectorAll('.highlight')]"
        ".filter((h) => ids.includes(h.dataset.fieldId)).length",
        slot_ids,
    )
    equals(highlights, 0, "no .highlight for a slot: slots are drawn once, as .slot")

    page.locator(".sidebar .issue", has_text="Carrier signature").first.click()
    page.wait_for_timeout(250)
    contains(page.locator('.pdf-page .slot[data-field-id="sig_1"]').get_attribute("class") or "",
             "slot--active", "the slot is the active one")
    truthy("slot--active" not in (page.locator('.pdf-page .slot[data-field-id="sig_2"]')
                                  .get_attribute("class") or ""), "and only that one")
    shot(page, "review_open")
    clean(mock)


@test("delete_and_arrows_only_touch_a_selected_annotation")
def _(page, mock: MockApi) -> None:
    """With a slot selected the keys do nothing; with an annotation they act on it."""
    open_edit(page, mock)
    arm_tool(page, "text")
    click_pt(page, 300, 500)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    page.keyboard.type("Keep me")
    page.keyboard.press("Enter")
    wait_for_anns(page, 1)
    before = anns(page)[0]
    layout_before = stored_layout(page, mock)

    page.locator('[data-testid="tool-select"]').click()
    page.locator('.pdf-page .slot[data-field-id="date_1"]').click()
    page.wait_for_timeout(200)
    contains(page.locator('.pdf-page .slot[data-field-id="date_1"]').get_attribute("class") or "",
             "slot--active", "the slot is selected")
    truthy(not anns(page)[0]["selected"], "and the annotation is not")

    page.keyboard.press("Delete")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Shift+ArrowDown")
    page.wait_for_timeout(200)
    equals(len(anns(page)), 1, "Delete removed nothing")
    approx(anns(page)[0]["x"], before["x"], 0.01, "the arrows moved no annotation")
    equals(stored_layout(page, mock), layout_before, "nor the slot")

    page.locator(f'[data-testid="ann-{before["id"]}"]').click()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(150)
    approx(anns(page)[0]["x"], before["x"] + 0.5, 0.01, "with the annotation selected, it moves")
    page.keyboard.press("Delete")
    wait_for_anns(page, 0)
    clean(mock)


# --------------------------------------------------------------------------
# Rotation and the Objects list
# --------------------------------------------------------------------------

@test("a_turned_page_says_placement_on_sign_and_read_only_on_review")
def _(page, mock: MockApi) -> None:
    """The notice follows the armed tool: Signature on /sign places, Select on /review reads."""
    open_sign(page, mock, expect_images=2)
    rotate_once(page)
    note = page.locator('[data-testid="pdf-rotation-note"]')
    equals(" ".join(note.inner_text().split()), PLACEMENT_NOTE, "/sign: the placement wording")
    shot(page, "rotated_sign")

    open_review(page, mock)
    rotate_once(page)
    equals(" ".join(note.inner_text().split()), READONLY_NOTE, "/review: the read-only wording")
    shot(page, "rotated_review")
    clean(mock)


@test("the_objects_list_matches_the_page")
def _(page, mock: MockApi) -> None:
    """One row per annotation, same kind and page; Show selects it."""
    open_edit(page, mock)
    arm_tool(page, "text")
    click_pt(page, 250, 480)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    page.keyboard.type("Rider")
    page.keyboard.press("Enter")
    wait_for_anns(page, 1)
    arm_tool(page, "check")
    click_pt(page, 450, 530)
    wait_for_anns(page, 2)

    on_page = anns(page)
    rows = page.evaluate(
        "() => [...document.querySelectorAll('[data-testid^=\"object-ann-\"]')].map((li) => ({"
        " id: li.dataset.testid.replace('object-ann-', ''), kind: li.dataset.kind,"
        " page: Number(li.dataset.page) }))"
    )
    equals([(r["id"], r["kind"], r["page"]) for r in rows],
           [(a["id"], a["kind"], a["page"]) for a in on_page],
           "the list is the page, in order")
    contains(page.locator('[data-testid="section-objects-badge"]').inner_text(), "2", "and counted")
    contains(page.locator('[data-testid="editor-hint"]').inner_text(),
             "saved in this browser only", "and it says where they are kept")

    first = on_page[0]["id"]
    page.locator(f'[data-testid="object-ann-{first}"]').get_by_role("button", name="Show").click()
    page.wait_for_timeout(200)
    selected = [a["id"] for a in anns(page) if a["selected"]]
    equals(selected, [first], "Show selects that annotation")
    truthy(tool_pressed(page, "select"), "with the Select tool armed")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
