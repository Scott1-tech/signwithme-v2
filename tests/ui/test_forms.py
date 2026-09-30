"""
F2b: interactive PDF form fields (EDITOR-V2-PLAN §3 `field.*`, §7.4, §9 F2, §11 Q2).

  * Forms ▾ «Add new form fields»: Text field, Multiline, Dropdown, Radio,
    Checkbox, Signature box (drag = a `field.add` with a unique name) and
    Detect form fields -- only on a server with the document editor;
  * edit mode: 8 handles, move, resize, Delete, Duplicate, undo (`'ops'`),
    the «Form field» panel (name, required, read-only, tooltip, default,
    options, radio group + value, font / size, max length), a taken name is
    refused with «Rename to X_2» (a radio button joins the group instead);
  * fill mode (Select): HTML controls → `field.value`; a character a form
    field cannot hold (Cyrillic) is said up front and never sent; the
    contractor who may only fill (`can_fill`, not `can_edit`) fills and saves;
  * the uploaded PDF's own fields (`source: original`) are drawn, filled and
    changed; a hidden Docusign envelope field is never drawn;
  * Tab order (drag → `tab_index`); Detect form fields for the pages on
    screen (dashed frames; Accept / Reject / Accept all; move / resize /
    retype before accepting; nothing added silently).

The server is `_formmock.FormDocMock`; nothing here reaches the live backend.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _docmock import Line, wait_until  # noqa: E402
from _f2 import at, clean, drag_pt, open_doc, ops_of, select_tool, shot  # noqa: E402
from _formmock import enable_forms, field_out  # noqa: E402
from _harness import BASE, MockApi, Registry, cli_main, contains, equals, slot, truthy  # noqa: E402

test = Registry("forms")

P1 = "pg_000000000001"
P2 = "pg_000000000002"
TYPES = ["text", "multiline", "dropdown", "radio", "checkbox", "signature"]


def install(mock: MockApi, *, pages: int = 2, fields: list[dict] | None = None, **options):
    mock.set_slots([slot("sig_1", "signature", 72, 640, 180, 34, rule_y=679.5)], signed=[])
    return enable_forms(
        mock,
        [[Line(f"Page {index + 1} carrier text", 72, 150), Line("Name: ________", 72, 300)] for index in range(pages)],
        fields=fields,
        **options,
    )


def field_ops(doc) -> list[dict]:
    return [op for op in ops_of(doc) if str(op.get("kind", "")).startswith("field.")]


def wait_field_ops(page, doc, predicate, timeout: int = 9000) -> list[dict]:
    wait_until(page, lambda: predicate(field_ops(doc)), timeout)
    return field_ops(doc)


def arm(page, name: str) -> None:
    select_tool(page, name)


def field_box(page, name: str, index: int = 0):
    target = page.locator(f'[data-testid="form-field"][data-name="{name}"]').nth(index)
    target.wait_for(state="visible", timeout=8000)
    return target.bounding_box()


def rect_pt(page, name: str, index: int = 0) -> list[float]:
    value = page.locator(f'[data-testid="form-field"][data-name="{name}"]').nth(index).get_attribute("data-rect-pt")
    return [float(v) for v in value.split(",")]


def draw(page, tool: str, x1: float, y1: float, x2: float, y2: float, number: int = 1) -> None:
    arm(page, tool)
    drag_pt(page, x1, y1, x2, y2, number)


def undo(page) -> None:
    page.locator("body").click(position={"x": 3, "y": 3})
    page.keyboard.press("Control+z")
    page.wait_for_timeout(250)


def seeded_fields() -> list[dict]:
    """The uploaded PDF's own AcroForm: a text field with a value, a checkbox, a
    read-only field, a radio group of two, and Docusign's hidden envelope field."""
    return [
        field_out("Carrier Name", "text", P1, (300, 140, 200, 18), value="ABC Transportation"),
        field_out("agree", "checkbox", P1, (72, 200, 12, 12), value=False, export_value="Yes"),
        field_out("locked_ref", "text", P1, (300, 200, 120, 18), value="REF-9", readonly=True),
        field_out("shift", "radio", P1, (72, 240, 12, 12), value="", export_value="Day"),
        field_out("shift", "radio", P1, (120, 240, 12, 12), value="", export_value="Night"),
        field_out("DocuSignSignHere", "signature", P1, (0, 0, 0, 0), hidden=True),
    ]


# --------------------------------------------------------------------------


@test("forms_menu_lists_the_six_types_and_detect_only_with_the_editor")
def _(page, mock: MockApi) -> None:
    install(mock)
    open_doc(page, mock)
    page.locator('[data-testid="toolgroup-forms-toggle"]').click()
    menu = page.locator('[data-testid="toolgroup-forms-menu"]')
    menu.wait_for(state="visible")
    ids = menu.evaluate("(el) => [...el.querySelectorAll('[data-testid^=\"tool-\"]')].map((b) => b.dataset.testid)")
    equals(ids, ["tool-check", "tool-cross", "tool-square", "tool-field-text", "tool-field-multiline",
                 "tool-field-dropdown", "tool-field-radio", "tool-field-checkbox", "tool-field-signature",
                 "tool-field-detect"], "Forms ▾, in order")
    contains(menu.text_content(), "Add new form fields", "the section heading")
    for tool in ("field-text", "field-detect"):
        button = page.locator(f'[data-testid="tool-{tool}"]')
        truthy(button.is_enabled(), f"{tool} enabled for an admin")
        truthy(len(button.get_attribute("title") or "") > 10, f"{tool} has a tooltip")
        truthy(len(button.get_attribute("aria-label") or "") > 10, f"{tool} has an accessible name")
    page.keyboard.press("Escape")
    # A server without the document editor: no form tool at all (no dead button).
    legacy = MockApi()
    legacy.install(page.context.new_page())
    page2 = page.context.pages[-1]
    page2.goto(f"{BASE}/contracts/{legacy.contract_id}/edit")
    page2.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page2.wait_for_timeout(400)
    equals(page2.locator('[data-testid^="tool-field-"]').count(), 0, "no form tool without the editor")
    clean(mock)


@test("each_of_the_six_types_is_drawn_as_one_field_add_with_a_unique_name")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    boxes = {"text": (80, 380, 240, 400), "multiline": (80, 420, 300, 480), "dropdown": (330, 380, 480, 400),
             "radio": (330, 420, 344, 434), "checkbox": (360, 420, 374, 434), "signature": (330, 500, 510, 540)}
    for kind in TYPES:
        draw(page, f"field-{kind}", *boxes[kind])
        panel = page.locator('[data-testid="field-properties"]')
        panel.wait_for(state="visible", timeout=8000)
        equals(panel.get_attribute("data-type"), kind, f"the panel opens on the new {kind}")
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 6)
    equals([op["kind"] for op in ops], ["field.add"] * 6, "six field.add ops")
    by_type = {op["field"]["type"]: op for op in ops}
    equals(sorted(by_type), sorted(TYPES), "one of each type")
    names = [op["field"]["name"] for op in ops]
    equals(len(set(names)), 6, f"unique names ({names})")
    equals(by_type["text"]["field"]["name"], "Text_1", "Text_1")
    equals(by_type["dropdown"]["field"]["options"], ["Option 1", "Option 2"], "a dropdown starts with two options")
    equals(by_type["radio"]["field"]["export_value"], "Choice1", "a radio button has its value")
    for kind in TYPES:
        op = by_type[kind]
        equals(op["page_uid"], P1, f"{kind} on page 1")
        x1, y1, x2, y2 = boxes[kind]
        rect = op["field"]["rect"]
        truthy(abs(rect["x_pt"] - x1) <= 1 and abs(rect["y_pt"] - y1) <= 1, f"{kind} where it was drawn ({rect})")
        truthy(abs(rect["w_pt"] - (x2 - x1)) <= 1.5 and abs(rect["h_pt"] - (y2 - y1)) <= 1.5, f"{kind} size ({rect})")
        truthy("layer" not in op, "no layer is sent")
    tabs = [op["field"]["tab_index"] for op in ops]
    equals(tabs, sorted(tabs), "tab order follows creation")
    equals(page.locator('[data-testid="form-field"]').count(), 6, "six boxes on the page")
    equals(page.locator('[data-testid="workspace"]').get_attribute("data-session"), "edit", "the session started")
    # A click (no drag) makes a field of the usual size, centred there.
    draw(page, "field-checkbox", 470, 560, 470, 560)
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 7)
    rect = ops[-1]["field"]["rect"]
    equals((rect["w_pt"], rect["h_pt"]), (14, 14), "a click = a 14 pt checkbox")
    shot(page, "forms_six_types")
    clean(mock)


@test("properties_are_one_step_each_and_undo_takes_them_back")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    draw(page, "field-text", 80, 380, 260, 400)
    page.locator('[data-testid="field-properties"]').wait_for()
    page.locator('[data-testid="field-required"]').check()
    page.locator('[data-testid="field-tooltip"]').fill("Carrier legal name")
    page.keyboard.press("Enter")
    page.locator('[data-testid="field-default"]').fill("N/A")
    page.keyboard.press("Enter")
    page.locator('[data-testid="field-max-len"]').fill("40")
    page.keyboard.press("Enter")
    page.locator('[data-testid="field-font"]').select_option("LiberationSerif")
    page.locator('[data-testid="field-size"]').fill("11")
    page.keyboard.press("Enter")
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 1 and ops[0]["field"].get("size_pt") == 11)
    field = ops[0]["field"]
    equals(ops[0]["kind"], "field.add", "still ONE add op (the draft's field is edited in place)")
    equals((field.get("required"), field.get("tooltip"), field.get("default"), field.get("max_len"), field.get("font")),
           (True, "Carrier legal name", "N/A", 40, "LiberationSerif"), "every property")
    # Undo takes the last change back (the size), then the font.
    undo(page)
    ops = wait_field_ops(page, doc, lambda ops: ops and "size_pt" not in ops[0]["field"])
    equals(ops[0]["field"].get("font"), "LiberationSerif", "one step back = the size only")
    undo(page)
    ops = wait_field_ops(page, doc, lambda ops: ops and "font" not in ops[0]["field"])
    # A dropdown's options: add, reorder, remove, default.
    draw(page, "field-dropdown", 80, 440, 240, 460)
    page.locator('[data-testid="field-properties"][data-type="dropdown"]').wait_for()
    page.locator('[data-testid="field-option-new"]').fill("Flatbed")
    page.locator('[data-testid="field-option-add"]').click()
    page.locator('[data-testid="field-option-remove-0"]').click()
    page.locator('[data-testid="field-default"]').select_option("Flatbed")
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 2 and ops[1]["field"].get("default") == "Flatbed")
    equals(ops[1]["field"]["options"], ["Option 2", "Flatbed"], "options added and removed")
    clean(mock)


@test("a_taken_name_is_refused_with_rename_to_x_2_and_a_radio_joins_its_group")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    draw(page, "field-text", 80, 380, 240, 400)
    draw(page, "field-text", 80, 420, 240, 440)
    wait_field_ops(page, doc, lambda ops: len(ops) == 2)
    name = page.locator('[data-testid="field-name"]')
    equals(name.input_value(), "Text_2", "the second one is Text_2")
    name.fill("Text_1")
    page.keyboard.press("Enter")
    warning = page.locator('[data-testid="field-name-taken"]')
    warning.wait_for(state="visible")
    contains(warning.inner_text(), "Text_1", "says which name")
    button = page.locator('[data-testid="field-rename-suggest"]')
    equals(button.inner_text(), "Rename to Text_1_2", "offers X_2")
    page.wait_for_timeout(1800)
    equals(sorted(op["field"]["name"] for op in field_ops(doc)), ["Text_1", "Text_2"], "nothing renamed yet")
    button.click()
    wait_field_ops(page, doc, lambda ops: sorted(op["field"]["name"] for op in ops) == ["Text_1", "Text_1_2"])
    # An invalid name is refused too.
    name.fill("bad name!")
    page.keyboard.press("Enter")
    page.locator('[data-testid="field-name-invalid"]').wait_for(state="visible")
    # Radio: two buttons, the second joins the first one's group -- no warning.
    select_tool(page, "select")
    draw(page, "field-radio", 300, 380, 314, 394)
    draw(page, "field-radio", 340, 380, 354, 394)
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 4)
    radios = [op["field"] for op in ops if op["field"]["type"] == "radio"]
    equals(radios[0]["name"], radios[1]["name"], "a new radio joins the selected button's group")
    equals(sorted(r["export_value"] for r in radios), ["Choice1", "Choice2"], "with a new value")
    # A third, separate group, renamed onto the first: joins, value made unique.
    select_tool(page, "select")
    draw(page, "field-radio", 380, 460, 394, 474)
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 5)
    equals(ops[-1]["field"]["name"], "Radio_2", "a separate group")
    page.locator('[data-testid="field-name"]').fill("Radio_1")
    page.keyboard.press("Enter")
    ops = wait_field_ops(page, doc, lambda ops: sum(1 for op in ops if op["field"]["name"] == "Radio_1") == 3)
    equals(page.locator('[data-testid="field-name-taken"]').count(), 0, "no collision warning for a radio")
    values = sorted(op["field"]["export_value"] for op in ops if op["field"]["name"] == "Radio_1")
    equals(values, ["Choice1", "Choice2", "Choice3"], "its value was made unique in the group")
    clean(mock)


@test("move_resize_with_the_handles_delete_duplicate_and_undo")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    draw(page, "field-text", 100, 400, 260, 420)
    wait_field_ops(page, doc, lambda ops: len(ops) == 1)
    equals(page.locator('[data-testid^="field-handle-"]').count(), 8, "8 handles on the selected field")
    # Move by (+40, +20) pt.
    box = field_box(page, "Text_1")
    x0, y0 = at(page, 150, 410)
    x1, y1 = at(page, 190, 430)
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x1, y1, steps=8)
    page.mouse.up()
    ops = wait_field_ops(page, doc, lambda ops: ops and abs(ops[0]["field"]["rect"]["x_pt"] - 140) < 0.6)
    rect = ops[0]["field"]["rect"]
    truthy(abs(rect["y_pt"] - 420) < 0.6 and rect["w_pt"] == 160, f"moved, same size ({rect})")
    # Resize by the SE handle (+20, +10 pt).
    handle = page.locator('[data-testid="field-handle-se"]').bounding_box()
    hx, hy = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    scale = box["width"] / 160
    page.mouse.move(hx, hy)
    page.mouse.down()
    page.mouse.move(hx + 20 * scale, hy + 10 * scale, steps=8)
    page.mouse.up()
    ops = wait_field_ops(page, doc, lambda ops: ops and abs(ops[0]["field"]["rect"]["w_pt"] - 180) < 0.6)
    rect = ops[0]["field"]["rect"]
    truthy(abs(rect["h_pt"] - 30) < 0.6 and abs(rect["x_pt"] - 140) < 0.6, f"resized from the corner ({rect})")
    # Arrow key nudge.
    page.keyboard.press("ArrowRight")
    wait_field_ops(page, doc, lambda ops: ops and abs(ops[0]["field"]["rect"]["x_pt"] - 141) < 0.6)
    # Duplicate: a new name, below.
    page.locator('[data-testid="field-duplicate"]').click()
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 2)
    equals(ops[1]["field"]["name"], "Text_2", "the copy gets a new name")
    truthy(ops[1]["field"]["rect"]["y_pt"] > rect["y_pt"] + rect["h_pt"], "below the original")
    # Delete key removes the selected (the copy); undo brings it back.
    page.locator("body").click(position={"x": 3, "y": 3})
    page.locator('[data-testid="form-field"][data-name="Text_2"]').click()
    page.keyboard.press("Delete")
    wait_field_ops(page, doc, lambda ops: len(ops) == 1)
    equals(page.locator('[data-testid="form-field"][data-name="Text_2"]').count(), 0, "gone from the page")
    undo(page)
    wait_field_ops(page, doc, lambda ops: len(ops) == 2)
    page.locator('[data-testid="form-field"][data-name="Text_2"]').wait_for()
    clean(mock)


@test("the_uploaded_pdfs_fields_are_drawn_filled_and_changed_the_hidden_one_never")
def _(page, mock: MockApi) -> None:
    doc = install(mock, fields=seeded_fields())
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="field-layer-1"]', timeout=10000)
    equals(page.locator('[data-testid="field-layer-1"]').get_attribute("data-mode"), "fill", "Select = fill mode")
    names = page.locator('[data-testid="form-field"]').evaluate_all("(els) => els.map((e) => e.dataset.name)")
    equals(sorted(names), sorted(["Carrier Name", "agree", "locked_ref", "shift", "shift"]), "drawn, the hidden one not")
    equals(page.locator('[data-testid="form-field"][data-name="DocuSignSignHere"]').count(), 0, "hidden field not drawn")
    carrier = page.locator('[data-testid="form-field"][data-name="Carrier Name"] [data-testid="field-input"]')
    equals(carrier.input_value(), "ABC Transportation", "the saved value")
    truthy(page.locator('[data-testid="form-field"][data-name="locked_ref"] input').evaluate("(e) => e.readOnly"),
           "read-only stays read-only")
    # Fill: starts the session, one field.value.
    carrier.fill("Premier Trucking Group")
    carrier.press("Enter")
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 1)
    equals(ops[0], {"id": ops[0]["id"], "kind": "field.value", "name": "Carrier Name", "value": "Premier Trucking Group"},
           "field.value by the original name")
    # Edit mode: the original field is a box; moving it is a field.update named by its saved name.
    arm(page, "field-text")
    page.wait_for_selector('[data-testid="field-layer-1"][data-mode="edit"]')
    agree = page.locator('[data-testid="form-field"][data-name="agree"]')
    agree.click()
    page.keyboard.press("ArrowDown")
    ops = wait_field_ops(page, doc, lambda ops: any(op["kind"] == "field.update" for op in ops))
    update = next(op for op in ops if op["kind"] == "field.update")
    equals((update["name"], update["field"]["name"], update["field"]["type"], update["field"]["export_value"]),
           ("agree", "agree", "checkbox", "Yes"), "an update of the original")
    equals(update["field"]["rect"]["y_pt"], 201, "moved 1 pt down")
    # A field whose name the editor cannot write back is fill-only here.
    page.locator('[data-testid="form-field"][data-name="Carrier Name"]').click()
    page.locator('[data-testid="field-locked"]').wait_for(state="visible")
    truthy(page.locator('[data-testid="field-name"]').is_disabled(), "its name cannot be edited")
    # Deleting a radio button of the original group: field.remove with its value; the value op stays last.
    page.locator('[data-testid="form-field"][data-name="shift"]').nth(1).click()
    page.locator('[data-testid="field-delete"]').click()
    ops = wait_field_ops(page, doc, lambda ops: any(op["kind"] == "field.remove" for op in ops))
    remove = next(op for op in ops if op["kind"] == "field.remove")
    equals((remove["name"], remove.get("export_value")), ("shift", "Night"), "one button of the group")
    equals([op["kind"] for op in ops], ["field.remove", "field.update", "field.value"], "canonical order")
    clean(mock)


@test("fill_mode_every_type_and_cyrillic_is_refused_before_sending")
def _(page, mock: MockApi) -> None:
    fields = [
        field_out("name", "text", P1, (300, 360, 200, 18)),
        field_out("notes", "multiline", P1, (300, 390, 200, 50)),
        field_out("trailer", "dropdown", P1, (300, 450, 120, 18), options=["Dry van", "Reefer"]),
        field_out("hazmat", "checkbox", P1, (300, 480, 12, 12), export_value="Yes"),
        field_out("pay", "radio", P1, (300, 500, 12, 12), export_value="Weekly"),
        field_out("pay", "radio", P1, (340, 500, 12, 12), export_value="Monthly"),
        field_out("sig", "signature", P1, (300, 530, 160, 30)),
    ]
    doc = install(mock, fields=fields)
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="field-layer-1"][data-mode="fill"]', timeout=10000)
    name = page.locator('[data-testid="form-field"][data-name="name"] [data-testid="field-input"]')
    # Cyrillic: said at once, nothing sent -- not even a draft.
    name.fill("Иван Петров")
    warning = page.locator('[data-testid="field-charset-warning"]')
    warning.wait_for(state="visible")
    contains(warning.inner_text(), "“И”", "names the character")
    contains(warning.inner_text(), "Latin", "and why")
    name.press("Enter")
    page.wait_for_timeout(1800)
    equals(doc.draft_posts, [], "no draft started for a refused value")
    equals(field_ops(doc), [], "nothing sent")
    name.fill("Ivan Petrov")
    name.press("Enter")
    notes = page.locator('[data-testid="form-field"][data-name="notes"] textarea')
    notes.fill("Line one\nLine two")
    page.locator('[data-testid="form-field"][data-name="trailer"] select').select_option("Reefer")
    page.locator('[data-testid="form-field"][data-name="hazmat"] input').check()
    page.locator('[data-testid="form-field"][data-name="pay"]').nth(1).locator("input").check()
    sig = page.locator('[data-testid="form-field"][data-name="sig"] [data-testid="field-input"]')
    equals(sig.get_attribute("data-kind"), "signature", "a signature box is not an input")
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 5)
    values = {op["name"]: op["value"] for op in ops}
    equals(values, {"name": "Ivan Petrov", "notes": "Line one\nLine two", "trailer": "Reefer", "hazmat": True,
                    "pay": "Monthly"}, "one value op per field")
    truthy(all(op["kind"] == "field.value" for op in ops), "only field.value")
    equals(len(doc.draft_posts), 1, "one session")
    # Undo takes the last value back.
    undo(page)
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 4)
    truthy(not page.locator('[data-testid="form-field"][data-name="pay"]').nth(1).locator("input").is_checked(),
           "the radio is unchecked again")
    clean(mock)


@test("a_contractor_who_may_only_fill_fills_and_saves")
def _(page, mock: MockApi) -> None:
    doc = install(mock, fields=[field_out("mc_number", "text", P1, (300, 360, 160, 18))],
                  can_edit=False, reason="not_permitted", can_fill=True)
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="field-layer-1"][data-mode="fill"]', timeout=10000)
    equals(page.locator('[data-testid="editor-edit-pdf"]').count(), 0, "no Edit PDF for the contractor")
    page.locator('[data-testid="toolgroup-forms-toggle"]').click()
    tool = page.locator('[data-testid="tool-field-text"]')
    tool.wait_for(state="visible")
    truthy(tool.is_disabled(), "form tools are off")
    contains(tool.get_attribute("data-disabled-reason") or "", "uploader", "with the reason")
    page.keyboard.press("Escape")
    field = page.locator('[data-testid="form-field"][data-name="mc_number"] input')
    field.fill("MC-123456")
    field.press("Enter")
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 1)
    equals(ops[0]["kind"], "field.value", "a fill-only draft")
    save = page.locator('[data-testid="editor-save"]')
    save.wait_for(state="visible", timeout=8000)
    page.locator('[data-testid="editor-save-status"][data-status="saved"]').wait_for(timeout=10000)
    save.click()
    wait_until(page, lambda: len(doc.commit_bodies) == 1 and doc.current == 2, 10000)
    wait_until(page, lambda: 2 in doc.field_gets, 10000)
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=10000)
    equals(page.locator('[data-testid="form-field"][data-name="mc_number"] input').input_value(), "MC-123456",
           "the new version's value")
    equals(page.locator('[data-testid="editor-save"]').count(), 0, "the session group is gone again")
    clean(mock)


@test("tab_order_is_changed_by_dragging_a_row")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    for index, y in enumerate((380, 420, 460)):
        draw(page, "field-text", 80, y, 240, y + 20)
        wait_field_ops(page, doc, lambda ops, n=index + 1: len(ops) == n)
    page.locator('[data-testid="field-tab-order-open"]').click()
    panel = page.locator('[data-testid="tab-order-panel"]')
    panel.wait_for(state="visible")
    rows = page.locator('[data-testid="tab-row"]')
    equals(rows.evaluate_all("(els) => els.map((e) => e.dataset.name)"), ["Text_1", "Text_2", "Text_3"], "creation order")
    rows.nth(2).drag_to(rows.nth(0))
    ops = wait_field_ops(page, doc, lambda ops: {op["field"]["name"]: op["field"]["tab_index"] for op in ops}
                         .get("Text_3") == 10)
    tabs = {op["field"]["name"]: op["field"]["tab_index"] for op in ops}
    equals(tabs, {"Text_3": 10, "Text_1": 20, "Text_2": 30}, "dragged to the top")
    equals(rows.evaluate_all("(els) => els.map((e) => e.dataset.name)"), ["Text_3", "Text_1", "Text_2"], "the list follows")
    # The ↓ button, then undo.
    rows.nth(0).locator('[data-testid="tab-down"]').click()
    wait_field_ops(page, doc, lambda ops: {op["field"]["name"]: op["field"]["tab_index"] for op in ops}.get("Text_1") == 10)
    undo(page)
    wait_field_ops(page, doc, lambda ops: {op["field"]["name"]: op["field"]["tab_index"] for op in ops}.get("Text_3") == 10)
    clean(mock)


@test("detect_suggests_for_the_pages_on_screen_and_adds_only_what_is_accepted")
def _(page, mock: MockApi) -> None:
    doc = install(mock, pages=4)
    doc.suggestions = [
        {"suggestion_id": "sg_000000000001", "page_uid": P1, "type": "text", "rect": {"x_pt": 120, "y_pt": 288, "w_pt": 150, "h_pt": 16},
         "label": "Name", "name": "Name", "confidence": 0.82},
        {"suggestion_id": "sg_000000000002", "page_uid": P1, "type": "checkbox", "rect": {"x_pt": 72, "y_pt": 330, "w_pt": 12, "h_pt": 12},
         "label": "Hazmat", "name": "Hazmat", "confidence": 0.7},
        {"suggestion_id": "sg_000000000003", "page_uid": P1, "type": "signature", "rect": {"x_pt": 72, "y_pt": 560, "w_pt": 180, "h_pt": 30},
         "label": "Carrier signature", "name": "Carrier_signature", "confidence": 0.9},
        {"suggestion_id": "sg_000000000004", "page_uid": "pg_000000000004", "type": "text",
         "rect": {"x_pt": 72, "y_pt": 100, "w_pt": 100, "h_pt": 16}, "label": "Far", "name": "Far", "confidence": 0.9},
    ]
    open_doc(page, mock)
    arm(page, "field-detect")
    panel = page.locator('[data-testid="detect-panel"]')
    panel.wait_for(state="visible")
    page.wait_for_selector('[data-testid="detect-panel"][data-status="ready"]', timeout=10000)
    equals(len(doc.detect_bodies), 1, "one request")
    asked = doc.detect_bodies[0]["page_uids"]
    truthy(P1 in asked and "pg_000000000004" not in asked, f"only the pages on screen ({asked})")
    frames = page.locator('[data-testid="field-suggestion"]')
    equals(frames.count(), 3, "three dashed frames on page 1")
    equals(frames.first.evaluate("(e) => getComputedStyle(e).borderTopStyle"), "dashed", "dashed")
    equals(field_ops(doc), [], "nothing added silently")
    equals(doc.draft_posts, [], "not even a draft")
    # Reject one: gone, still nothing added.
    page.locator('[data-testid="field-suggestion"][data-suggestion-id="sg_000000000002"] [data-testid="suggestion-reject"]').click()
    equals(frames.count(), 2, "rejected")
    # Retype and move one, then accept it: one field.add, one undo step.
    row = page.locator('[data-testid="detect-row"][data-suggestion-id="sg_000000000001"]')
    row.locator('[data-testid="suggestion-type"]').select_option("multiline")
    frame = page.locator('[data-testid="field-suggestion"][data-suggestion-id="sg_000000000001"]')
    box = frame.bounding_box()
    scale = box["width"] / 150
    page.mouse.move(box["x"] + 30, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + 30 + 10 * scale, box["y"] + box["height"] / 2 + 20 * scale, steps=6)
    page.mouse.up()
    moved = [float(v) for v in frame.get_attribute("data-rect-pt").split(",")]
    truthy(abs(moved[0] - 130) <= 0.5 and abs(moved[1] - 308) <= 0.5 and moved[2:] == [150, 16],
           f"moved before accepting ({moved})")
    row.locator('[data-testid="detect-accept"]').click()
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 1)
    equals((ops[0]["kind"], ops[0]["field"]["type"], ops[0]["field"]["name"]), ("field.add", "multiline", "Name"),
           "accepted as retyped")
    equals(ops[0]["field"]["rect"], {"x_pt": moved[0], "y_pt": moved[1], "w_pt": 150, "h_pt": 16}, "where it was moved")
    equals(frames.count(), 1, "the accepted frame is now a field")
    undo(page)
    wait_field_ops(page, doc, lambda ops: len(ops) == 0)
    # Accept all: ONE step for all that remain.
    page.locator('[data-testid="detect-accept-all"]').click()
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 1)
    equals(ops[0]["field"]["type"], "signature", "the rest (one)")
    equals(frames.count(), 0, "no suggestion left")
    undo(page)
    wait_field_ops(page, doc, lambda ops: len(ops) == 0)
    clean(mock)


@test("save_commits_the_fields_and_the_new_version_draws_them")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    draw(page, "field-text", 80, 380, 240, 400)
    draw(page, "field-checkbox", 260, 383, 274, 397)
    wait_field_ops(page, doc, lambda ops: len(ops) == 2)
    page.locator('[data-testid="editor-save-status"][data-status="saved"]').wait_for(timeout=10000)
    page.locator('[data-testid="editor-save"]').click()
    wait_until(page, lambda: doc.current == 2, 10000)
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=10000)
    wait_until(page, lambda: 2 in doc.field_gets, 10000)
    select_tool(page, "select")
    page.wait_for_selector('[data-testid="field-layer-1"][data-mode="fill"]', timeout=10000)
    sources = page.locator('[data-testid="form-field"]').evaluate_all("(els) => els.map((e) => [e.dataset.name, e.dataset.source])")
    equals(sorted(sources), [["Checkbox_1", "editor"], ["Text_1", "editor"]], "the saved fields, from version 2")
    clean(mock)


@test("a_field_the_server_refuses_is_marked_and_the_session_says_why")
def _(page, mock: MockApi) -> None:
    doc = install(mock, fields=[field_out("Taken", "text", P1, (300, 360, 160, 18))])
    open_doc(page, mock)
    draw(page, "field-text", 80, 380, 240, 400)
    ops = wait_field_ops(page, doc, lambda ops: len(ops) == 1)
    doc.force["PUT /draft"] = [(422, {"error": "invalid_ops", "message": "Some edits cannot be applied",
                                      "details": {"ops": [{"id": ops[0]["id"], "reason": "field_name_taken"}]}})]
    page.locator('[data-testid="field-required"]').check()
    page.wait_for_selector('[data-testid="form-field"][data-problem]', timeout=10000)
    contains(page.locator('[data-testid="form-field"][data-problem]').get_attribute("data-problem"), "already has this name",
             "the field says why")
    page.locator('[data-testid="session-error"]').wait_for(state="visible")
    clean(mock)


@test("at_1600_the_panels_fit_and_every_control_is_named")
def _(page, mock: MockApi) -> None:
    install(mock, fields=seeded_fields())
    open_doc(page, mock)
    arm(page, "field-dropdown")
    drag_pt(page, 80, 380, 240, 400)
    page.locator('[data-testid="field-properties"]').wait_for()
    for panel in ('[data-testid="forms-dock"]',):
        box = page.locator(panel).bounding_box()
        view = page.evaluate("() => ({w: document.documentElement.clientWidth, h: window.innerHeight})")
        truthy(box["x"] >= 0 and box["x"] + box["width"] <= view["w"] and box["y"] + 20 <= view["h"], f"{panel} inside ({box})")
    overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    truthy(overflow <= 0, f"no page overflow ({overflow})")
    report = page.evaluate(
        """() => [...document.querySelectorAll('[data-testid="forms-dock"] button, [data-testid="forms-dock"] input, [data-testid="forms-dock"] select')]
             .map((b) => ({ id: b.dataset.testid || b.className,
                            name: (b.getAttribute('aria-label') || b.textContent || b.closest('label')?.textContent || '').trim() }))""")
    for control in report:
        truthy(len(control["name"]) > 1, f"{control['id']}: named")
    shot(page, "forms_properties_1600")
    page.locator('[data-testid="field-tab-order-open"]').click()
    page.locator('[data-testid="tab-order-panel"]').wait_for()
    overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    truthy(overflow <= 0, f"no page overflow with the tab order ({overflow})")
    shot(page, "forms_tab_order_1600")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
