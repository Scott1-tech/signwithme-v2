"""
Run every SignWithMe browser check in one go.

    python tests/ui/run_all.py
    python tests/ui/run_all.py --suite layout --headed
    python tests/ui/run_all.py --only clamp --shots %TEMP%\\swm_ui_shots

Nothing here touches the live database: every `/api/*` request is answered by
`_harness.MockApi`. The only thing that must be running is the Vite dev server
(default `http://127.0.0.1:5173`, override with `SWM_UI_BASE`).
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import BASE, ensure_target_is_up, run_registry  # noqa: E402

SUITES = [
    ("layout", "test_layout_drag"),
    ("move", "test_move_all"),
    ("warnings", "test_warnings"),
    ("override", "test_override"),
    ("viewer", "test_viewer"),
    ("search", "test_search"),
    ("rotation", "test_rotation"),
    ("editor", "test_editor"),
    ("workspace", "test_workspace"),
    ("toolbar", "test_toolbar"),
    ("dates", "test_dates"),
    ("edittext", "test_edit_text"),
    ("markup", "test_markup"),
    ("draw", "test_draw"),
    ("redact", "test_redact"),
    ("geometry", "test_geometry"),
    ("toolbar2", "test_toolbar_v2"),
    # F1b: the edit session, editing the PDF's own text, markup on the server.
    ("session", "test_session"),
    ("textedit2", "test_text_edit_v2"),
    ("markupmig", "test_markup_migration"),
    # F2: the rest of the editor's interface (B2 live).
    ("textbox", "test_textbox"),
    ("links", "test_links"),
    ("pages", "test_pages"),
    ("images", "test_images"),
    ("signmenu", "test_sign_menu"),
    ("annotate2", "test_annotate_v2"),
    ("shapes2", "test_shapes_v2"),
    ("export", "test_export"),
    ("fontsmore", "test_fonts_more"),
    # F2b: interactive PDF form fields (B3 live).
    ("forms", "test_forms"),
    # F3: «Share for others to fill», the public form, submissions.
    ("share", "test_share"),
    # ROTFIX: pages turned by the file's own /Rotate (`page.rotate`).
    ("rotated", "test_rotated_pages"),
    # ZW: zero-width characters (Docusign U+200B) + the engine's commit refusals.
    ("zerowidth", "test_zero_width"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="SignWithMe UI baseline checks")
    parser.add_argument("--suite", action="append",
                        help="run only this suite (layout | move | warnings | override | viewer | "
                             "search | rotation | editor | workspace | toolbar | dates | edittext | markup | "
                             "draw | redact | geometry | toolbar2 | session | textedit2 | markupmig | textbox | "
                             "links | pages | images | signmenu | annotate2 | shapes2 | export | fontsmore | forms | share | rotated | zerowidth); "
                             "repeatable")
    parser.add_argument("--only", help="run only checks whose name contains this")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--slow-mo", type=int, default=0, help="ms between actions")
    parser.add_argument("--shots", help="directory for a screenshot of every failed check")
    args = parser.parse_args()

    ensure_target_is_up()

    wanted = set(args.suite or [name for name, _ in SUITES])
    unknown = wanted - {name for name, _ in SUITES}
    if unknown:
        print(f"Unknown suite(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    started = time.time()
    totals: list[tuple[str, int, int]] = []
    all_failures: list[tuple[str, str, str]] = []

    for name, module_name in SUITES:
        if name not in wanted:
            continue
        module = importlib.import_module(module_name)
        registry = module.test
        passed, failures = run_registry(
            registry,
            headed=args.headed,
            slow_mo=args.slow_mo,
            only=args.only,
            shots=args.shots,
        )
        totals.append((registry.suite, passed, passed + len(failures)))
        all_failures.extend((registry.suite, check, detail) for check, detail in failures)

    grand_pass = sum(p for _, p, _ in totals)
    grand_total = sum(t for _, _, t in totals)

    print("\n" + "=" * 62)
    print(f"target {BASE}   ({time.time() - started:.0f}s)")
    for suite, passed, total in totals:
        flag = "OK  " if passed == total else "FAIL"
        print(f"  {flag}  {suite:<16} {passed}/{total}")
    print(f"  TOTAL            {grand_pass}/{grand_total}")
    if all_failures:
        print("\nfailures:")
        for suite, check, detail in all_failures:
            print(f"  [{suite}] {check}\n      {detail}")
    print("=" * 62)
    return 0 if not all_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
