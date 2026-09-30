"""
F2: Shapes ▾ with the v7 look (EDITOR-V2-PLAN §7.4, B2 `shape2`).

  * «Style ▾» on the toolbar: line style solid / dash / dot, a fill COLOUR
    or none, opacity, arrow heads (end / start / both);
  * a shape drawn with the plain look is the v5 `rect` / `arrow` byte for
    byte; one with a v7 look is `shape2` (`stroke_color`, `fill_color`,
    `dash`, `opacity`, `heads`);
  * changing the look of a selected legacy shape makes it `shape2` in ONE
    undo step (Ctrl+Z gives the v5 shape back).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _f2 import clean, drag_pt, install, open_doc, select_tool, shot, wait_markup  # noqa: E402
from _harness import MockApi, Registry, cli_main, equals, truthy  # noqa: E402

test = Registry("shapes v2 (F2)")


def kinds(elements, kind):
    return [element for element in elements if element.get("kind") == kind]


def style(page):
    page.locator('[data-testid="ann-style"]').click()
    page.locator('[data-testid="ann-style-panel"]').wait_for()


@test("a_plain_rect_is_the_v5_rect_and_a_styled_one_is_shape2")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    select_tool(page, "rect")
    drag_pt(page, 80, 300, 180, 360)
    rect = kinds(wait_markup(page, doc, lambda els: len(kinds(els, "rect")) == 1), "rect")[0]
    equals(sorted(rect), sorted(["id", "kind", "page_uid", "x_pt", "y_pt", "w_pt", "h_pt", "stroke_pt", "color", "fill"]),
           "the v5 keys only")
    page.locator('[data-testid="ann-style"]').click()
    page.locator('[data-testid="ann-dash-dash"]').click()
    page.locator('[data-testid="ann-fill-color-yellow"]').click()
    page.locator('[data-testid="ann-opacity"]').select_option("75")
    page.keyboard.press("Escape")
    # The selected one changed; the next one is drawn with the same look.
    select_tool(page, "rect")
    drag_pt(page, 250, 300, 350, 360)
    elements = wait_markup(page, doc, lambda els: len(kinds(els, "shape2")) == 2)
    for shape in kinds(elements, "shape2"):
        equals((shape["shape"], shape["dash"], shape["fill_color"], shape["opacity"], shape["stroke_color"]),
               ("rect", "dash", "yellow", 0.75, "black"), "dash, yellow fill, 75 %, black border")
        truthy("fill" not in shape and "color" not in shape, "no v5 keys in a shape2")
    drawn = page.locator(f'[data-testid="ann-shape-{kinds(elements, "shape2")[1]["id"]}"]')
    equals(drawn.get_attribute("data-dash"), "dash", "drawn dashed")
    truthy(drawn.get_attribute("stroke-dasharray"), "with a dash array")
    shot(page, "shapes_v2")
    clean(mock)


@test("an_arrow_points_at_the_start_or_both_ends_and_a_line_can_be_dotted")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    select_tool(page, "arrow")
    style(page)
    page.locator('[data-testid="ann-heads-both"]').click()
    page.keyboard.press("Escape")
    drag_pt(page, 80, 420, 260, 420)
    arrow = kinds(wait_markup(page, doc, lambda els: len(kinds(els, "shape2")) == 1), "shape2")[0]
    equals((arrow["shape"], arrow["heads"]), ("arrow", "both"), "heads at both ends")
    heads = page.locator(f'[data-testid="ann-line-{arrow["id"]}"] polygon').count()
    equals(heads, 2, "two heads drawn")
    select_tool(page, "line")
    style(page)
    page.locator('[data-testid="ann-dash-dot"]').click()
    page.keyboard.press("Escape")
    drag_pt(page, 80, 480, 260, 480)
    elements = wait_markup(page, doc, lambda els: len(kinds(els, "shape2")) == 2)
    line = [item for item in kinds(elements, "shape2") if item["shape"] == "line"][0]
    equals((line["dash"], line["heads"]), ("dot", "none"), "a dotted line, no heads")
    clean(mock)


@test("restyling_a_v5_shape_is_one_step_and_undo_gives_the_v5_shape_back")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    select_tool(page, "ellipse")
    drag_pt(page, 100, 300, 200, 380)
    wait_markup(page, doc, lambda els: len(kinds(els, "ellipse")) == 1)
    depth = int(page.locator('[data-testid="undo-depth"]').inner_text())
    style(page)
    page.locator('[data-testid="ann-fill-color-blue"]').click()
    elements = wait_markup(page, doc, lambda els: len(kinds(els, "shape2")) == 1)
    equals(kinds(elements, "shape2")[0]["fill_color"], "blue", "blue fill")
    equals(int(page.locator('[data-testid="undo-depth"]').inner_text()), depth + 1, "one step")
    page.keyboard.press("Escape")
    page.locator("body").click(position={"x": 3, "y": 3})
    page.keyboard.press("Control+z")
    elements = wait_markup(page, doc, lambda els: len(kinds(els, "ellipse")) == 1)
    equals(kinds(elements, "shape2"), [], "back to the v5 ellipse")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
