# UI baseline checks (Playwright, Python)

Browser checks that record how the SignWithMe frontend behaves **today**, so a
large UI rework can be done without silently losing an existing behaviour.

They replace the throwaway scripts that used to live in `%TEMP%\swm_drag_e2e\`
and `%TEMP%\swm_ovr2_e2e\` (deleted in a security clean-up). These live in the
repository so they cannot go missing again.

| File | Checks | Covers |
|---|---|---|
| `test_layout_drag.py` | 30 | slot boxes on their rule, mouse dragging, clamping, steppers, size/font, persistence (layout and `swm.dates.<id>`), finalize payload |
| `test_move_all.py` | 11 | four directions for every move: vertical drag of the slot box / signature image / date text (px → pt, up = positive), date-slot ▲▼, the "Move together" pads (signatures / dates / everything), per-slot page-edge clamp incl. mixed 792/817 pt pages, finalize body |
| `test_warnings.py` | 14 | pre-filled template marker, "same file as the template" banner/badge, FMCSA rows, real blockers, upload-pad wording |
| `test_override.py` | 15 | reviewer override: eligibility, notes minimum, confirm, request body, FMCSA refusal, override badge, post-approval 409 |
| `test_viewer.py` | 16 | the viewer actually **draws**: painted pixels on visible pages, released bitmaps off-screen, page counter follows the scroll, Prev/Next, anchored zoom, thumbnail strip (paint, bounds, click, current marker, keyboard, collapse) |
| `test_search.py` | 11 | find in document: counts, next/previous, case, nothing-found, Escape, marks painted in real pixels and on the right words, marks under the overlay and inert, first-search progress and the text cache |
| `test_rotation.py` | 10 | rotate to read: the box and the **ink** turn, the bitmap is re-rendered (not stretched), one page at a time, thumbnails follow, slot boxes land exactly where the quarter turn puts them, placement/drag refused with the notice, a full circle restores everything |
| `test_editor.py` | 29 | the editor tools inside the workspace: text/date/check/cross placed in PDF points, select/drag/resize/retype/delete, one undo step per gesture, storage, free signature through the placement endpoint, finalize body |
| `test_workspace.py` | 12 | W1: `/review` `/sign` `/edit` are ONE workspace (no remount, no second `/file`), approve → `/sign` in place, first tool from the URL, slot vs free signature drawn once each, text tool on a slot places text, one finalize (dates + layout + annotations), unapproved contract, dates survive `/sign`→`/edit`→`/sign`, sidebar issue → `slot--active`, keys only touch a selected annotation, rotation notice per URL, Objects list |
| `test_toolbar.py` | 19 | W2: the floating toolbar (docked default with every old hook, grip drag, persist + reload, rubbish `swm.toolbar` ignored, clamp when the window shrinks, top/left/right edge snapping, collapse, V T D K X S shortcuts and never while typing, roving focus + Alt+T + Esc, grip keyboard, 640 px bottom bar, «More ▾», one-row disabled reason), the colour palette (backend hex in the preview, `color` in the finalize body, black = no key, stored non-palette colour dropped), the ONE history (slot drag Ctrl+Z / Ctrl+Y, panel controls are steps and a slider run is one, the page-edge clamp is not a step, a server write never enters it), the memo (a drag re-renders only its own page) |
| `test_dates.py` | 9 | no automatic date anywhere (2026-09-26): every date box starts empty, "Choose the date first", the clock pinned to 02:30 Tashkent |
| `test_edit_text.py` | 20 | WE: edit the PDF's OWN text — pdf.js text layer only with the tool, only on the render window's pages (≤ 6), never on a turned page; word items merged into lines, two columns stay two lines; hover frame; the box opens with the original text in the matching face (serif / sans / mono, bold, italic, size); commit = whiteout (line ink box ± 0.5 pt) + text on the baseline, one `group_id`, ONE undo step; untouched line adds nothing; a second click re-opens the same edit; whiteout locked, text draggable, Delete / «Revert» take the group; six font families + B / I for Text and Date; Cyrillic / `oʻ` pass in Liberation and are refused up front in Helvetica, an emoji anywhere; `bold: false` never sent; stored rubbish dropped; "wider than the original line"; search still works; a 49-page document stays bounded |
| `test_markup.py` | 19 | W3: Highlight (H) / Underline (U) / Strikeout (C) by SELECTING the page's text — layer in select mode only with the tool, never on a turned page; a word's box to ±0.5 pt of the font metrics (x / width) and the line's ink box (y / height); a real drag over two lines = one box per line; a double-click marks one word; two columns stay two boxes; a page break = one mark per page, ONE step; 60 lines → marks of ≤ 50 boxes; finalize body `{id, kind, page, rects, color}` (colour always named); preview = print (alpha 0.35, bar `max(0.75, h·0.08)` bottom / middle); select / drag whole / recolour / Delete / Objects / undo; clamped to the page; stored rubbish dropped; «Highlight all matches» one step from the search's boxes, no second copy, all-or-nothing at 500; the 500 ceiling (counter, refusals); slot date: six families + B / I, `false` never sent |
| `test_draw.py` | 23 | W4: Draw (P) / Rectangle (R) / Ellipse (O) / Line (L) / Arrow (A) / Note (N) / Image (M) — 17 tools in one row at 1600 px («Markup ▾» / «Shapes ▾» by mouse and keyboard, overflow 0 also with stroke + fill shown); properties follow the kind (stroke / fill / colour only); a drag = its box / ends in pt (±0.5), one step, preview only on its page (other pages not re-rendered), drawn even over a slot; Shift = square / circle / 45° (kept on the page along the ray); fill alpha 0.2 + stroke width in the preview and the body; arrow head 6·3·stroke, butt caps; ink RDP (straight stroke = 2 points, a click = a dot), a 4500-point stroke split to ≤ 2000 / 4000 / 20 in ONE step, the 20 000 ceiling refused; 8 handles (rect Shift = square, image keeps its ratio, Shift frees it, page edge), 2 line ends (Shift 45°), ink / note move whole; a note is multi-line, shows its text when selected, refuses Cyrillic up front; an image is checked here (2 MB, PNG/JPEG magic, 4000 px), uploaded ONCE as JSON `image_data`, previewed by ONE `GET`, placed at its ratio; 409 / 409-limit / 413 / 422 in words; Image off on an unapproved contract (drawing still works); Esc drops a drag; one undo step per tool; a turned page draws nothing; stored rubbish dropped + Objects names; the finalize body of every kind has exactly the backend's keys |
| `test_redact.py` | 13 | W5: Whiteout (W) / Initials (I) / the clamp of stored annotations — 19 tools in one row at 1600 px also with "12 signatures" on it; a whiteout drag = its box in pt (±0.5), one step, preview white + dashed screen-only edge, body exactly `{id, kind, page, x_pt, y_pt, w_pt, h_pt}`, Objects «Whiteout · p.N»; a free whiteout's 8 handles / move / page edge / Delete / undo; a slot signature stays ON a whiteout (pixels: the page's rule under it is gone, the signature's ink is not; paint order); the once-only "only hides" notice (`swm.whiteout.noticed`); an edited line's whiteout stays locked, no handles, no edge; initials = the free-signature POST (`field_id: null`), box inside 60 × 24 at the image's ratio centred on the click, not an undo step, off on an unapproved contract; «Every page» = one POST per other page, ≤ 3 in flight, partial failure named by page, a second press only retries what is missing, the bottom-right corner kept on A4 / landscape pages; «Remove all initials» deletes only initials (geometry rule for rows placed elsewhere); saved initials labelled `Initials` and marked in My signatures; stored annotations off their page pulled on once the size is known (not a step, said once, finalize body inside the page) |
| `test_toolbar_v2.py` | 14 | F1a (Sejda shell): the row is the spec's order (Select \| Text ▼ \| Forms ▼ \| Images ▼ \| Sign ▼ \| Whiteout \| Annotate ▼ \| Shapes ▼ \| Undo \| Redo, nothing that does not work yet — no Links / Filled square / form fields / Detect / «coming»); each drop-down's members in order with their letters; computed styles (`#0099FF` accent, `#E9E9E9` canvas, white pages, 34 ± 1 px controls, 3–4 px radius, 13–14 px text, white menus with a 1 px `#D0D0D0` border and a shadow, light-blue hover / armed); the other pages keep their theme (Dashboard's primary `#1F6FEB`, no `--sj-*` outside `.workspace`); every list inside the viewport (docked, after a shrink, vertical on the right / left edge, a short window → scroll, ≤ 640 px above the bottom bar); a list keeps its place while the pages scroll; every toolbar / top-bar button has a name and a tooltip, every icon an `aria-hidden` inline SVG; keyboard (roving, Alt+↓ / F4, ↑ ↓ Home End, Enter, Esc, letters press their menu); 1600 px overflow 0 whatever is armed; top bar: the contract's name, `editor-finalize` here (same body, same gate + tooltip), no Edit / Save / Cancel yet, «Download PDF» = the uploaded PDF (one `/file` GET) or the signed final (`/api/download/{id}/final`, bearer) |
| `test_geometry.py` | 4 | fix 2026-09-27: with a note on screen, a whiteout / rect / line / ink drag keeps the page's `getBoundingClientRect().top` still from press to release and stores the geometry under the pointer (±0.5 pt); an error banner that arrives mid-drag waits until the press is over; a free signature and an initial are drawn where the PDF prints them (the backend's own `_ink_metrics`, global offset 3 pt + size 120 %, ±0.05 pt); an initial's box has the ratio of the image the server keeps (`prepare_signature_image`, imported from the backend) |
| `test_textbox.py` | 6 | F2: Add text = the v7 text box with the editor (the W2 run without it); Shift+Enter lines, Enter saves, Esc cancels, a paste is plain NFC text, an empty box is nothing; greedy wrap at 200 pt (3 lines), height grown to the lines; wire `{kind: textbox, runs, align, line_height, x/y/w/h, page_uid}`; align / line spacing / size / colour / bold of the selected box (one step each, Ctrl+Z); move, east-handle resize, Duplicate (+12 pt, selected), double-click edits, Delete; finalize sends NO `annotations` when the server holds the markup (B2 `annotations_in_two_places`) |
| `test_links.py` | 5 | F2: Links on the row only with the editor (Select \| Text ▼ \| Links \| …); a dragged box → the dialog: `javascript:` / `ftp:` / host-less refused in the dialog, then `link.add {rect, target: uri}` (the session starts itself), framed, Ctrl+Z; a link to page 3 by uid; a server link (`lk12`) framed, click edits (never navigates), `link.update` for the target, drag moves, corner resizes (the same op), Delete → `link.remove` only; «Link» on the text toolbar links the edited block's box |
| `test_pages.py` | 6 | F2: «Insert page here» starts the session, `page.insert {after, same_as_previous}`, the viewer shows the WHOLE draft (`draft/preview.pdf`, order from `X-Page-Uids`); the delete dialog counts slot / link / annotation of the page, the uploader is refused a slot page BEFORE anything is sent, a page without slots goes (`page.delete`); an admin deletes a page, its annotation is set aside (not sent), the others renumbered by uid, Ctrl+Z brings page and annotation back; drag moves a page (`page.move`), the slot follows, the search runs again on the draft; ◀ ▶ is ONE absolute `page.rotate` per page (90 → 180 → 90), the viewer's ⟳ says «view only»; Save commits the new order and the annotation keeps its page's uid |
| `test_images.py` | 5 | F2: Images ▾ = Image, Stamp, Delete original image + the gallery; «Add to gallery…» → `/api/assets`, a thumbnail pick + a click COPIES it (`annotation-assets {from_asset_id}`), removing it from the gallery leaves the placed picture; «New Stamp…» date line starts EMPTY, RECEIVED red bordered without `date_text`, own text + typed date + blue; «Delete original image» frame → `image.remove {xref, bbox}`, «Removed when you save», ✕ keeps it; a placed picture turned 90° at 50 % = `image2`, duplicated |
| `test_sign_menu.py` | 4 | F2: Sign ▾ = Signature, Initials + saved thumbnails + «New Signature…» + «My signatures…»; a saved thumbnail + a click places it (free, no dialog); the Type tab lists `/api/fonts?category=signature` (Great Vibes, Allura, Sacramento), previews in the script, `POST /api/signatures {typed: {text, font}}` then places; an older server: no Type tab, no extras |
| `test_annotate_v2.py` | 5 | F2: Freehand highlight (F) = `ink2 {freehand_highlight}` yellow ≥ 8 pt drawn at 35 %; Hide annotations is screen-only (layers hidden, Objects still 2, the export dialog counts 2); Filled square (Q) = `square_mark` centred, green; Redact («permanently removes» vs Whiteout «only hides») = a black `redact` op, drawn black until saved, Ctrl+Z; at 1600 px with the editor nothing overflows for 14 armed tools, every list inside the viewport, every button named + tooltip |
| `test_shapes_v2.py` | 3 | F2: a plain rect keeps the v5 keys; «Style ▾» dash + yellow fill + 75 % makes the selected one and the next one `shape2`; arrow heads both (two drawn), a dotted line (heads none); restyling a v5 ellipse is ONE step and Ctrl+Z gives the v5 ellipse back |
| `test_export.py` | 3 | F2: with the editor «Download PDF» opens the export dialog, «Include annotations (N)»; ticked → `include_markup: true` and the export file, unticked → false; in a session the dialog says unsaved edits are not in the file |
| `test_fonts_more.py` | 4 | F2: «More fonts…» lists the TEXT families of `/api/fonts` with licence, «Calibri → Carlito (substitute)», the name set in itself, search by the replaced font; a pick sets the edited block's family in the `text.replace` runs; a text box takes a family too; the text toolbar's alignment / line spacing travel in the op |
| `test_share.py` | 12 | F3: «Share for others to fill» only with `can_edit` + a shareable field; the dialog's LAN warning, nothing created before «I understand» + Create; the URL once (Copy, never again, never stored); 14-day cap + the server's refusal by field name; Revoke after a confirm; `/s` clears the fragment first, `X-Share-Token` only, inputs on the rects, Submit → Thank you, per-field errors, Cyrillic refused before sending, one neutral message for every refused link (+ 429); Submissions: masked values marked, Accept → this tab's draft → Save (signatures dialog) → a new version, `draft_locked` in words, Reject needs a reason |
| `test_rotated_pages.py` | 6 | ROTFIX: a page REALLY turned by its own `/Rotate` (`page.rotate`), not the view turn. PyMuPDF (`Rect * page.rotation_matrix`) is the PDF's side, ±0.5 pt: at 90 / 180 / 270 the form widget, the link and the server's text block (all UNROTATED, the engine's space) are where the displayed page has them; the slot, its signature (`data-left-pt` / `data-bottom-pt`) and a legacy tick keep their displayed points, a v7 text box (unrotated on the wire, `rotation_deg` 360 − R) is upright at the mapped centre, nothing is PUT on open; on a /Rotate 90 page a click / drag stores the points under the pointer (tick displayed; text box `rotation_deg` 270, field / link / redaction unrotated and mapped back by PyMuPDF, framed there); a draft `page.rotate` turns the slot (inert wrapper `server-marks-turned`) and the tick with the page (draft markup), leaves the text box's unrotated wire alone, Ctrl+Z turns back, Save keeps it turned with no later PUT putting it back; /Rotate 90 + a view turn composes to 180 with the view notice + shield (a real /Rotate has neither); the search marks a word of a turned page (along the line to the advance widths) |
| **total** | **360** | 288 before F2 + 41 (F2) + 13 (F2b forms) + 12 (F3) + 6 (ROTFIX) |

Last full run: **94 / 94 green** against the running dev server.
2026-09-25: `run_all.py` (all suites incl. `test_editor.py` and `test_move_all.py`) **134 / 134 green**.
2026-09-25 (W0 refactor): **136 / 136 green** — the two new checks are `dates_survive_a_reload` and `a_broken_stored_date_is_dropped`.
2026-09-25 (W1 single workspace): **148 / 148 green** — `test_workspace.py` (12) is new; five checks in `test_editor.py` were moved, see "Moved in W1" below.
2026-09-25 (W2 registry + floating toolbar + one history): **167 / 167 green** — `test_toolbar.py` (19) is new; no existing check was changed.
2026-09-26 (WE edit existing text + fonts): see "WE" below — `test_edit_text.py` (20) is new; `test_toolbar.py` learnt the seventh tool (not weakened, see "Changed in WE").
2026-09-27 (W3 markup + 500 + slot date font): `test_markup.py` (19) is new; `test_toolbar.py` learnt tools 8–10 and five counters read `/ 500` (not weakened, see "Changed in W3").
2026-09-27 (W4 drawing tools + toolbar drop-downs): `test_draw.py` (23) is new; `test_toolbar.py` learnt tools 11–17 and the drop-downs, `test_markup.py` arms its tools through the «Markup ▾» list (not weakened, see "Changed in W4").
2026-09-27 (W5 whiteout + initials + stored-annotation clamp): `test_redact.py` (13) is new; `test_toolbar.py` learnt tools 18–19, `test_draw.py` counts twelve buttons in the row (not weakened, see "Changed in W5").
2026-09-28 (fix: page jump under a drag + free-signature preview): `test_geometry.py` (4) is new; one assertion of `test_redact.py` now pins the SERVER's crop ratio instead of the raw pad's 3:1 (not weakened, see "Changed in the geometry fix"). `test_geometry.py` reads the backend's code (never changes it): `signature_image.py` is imported (Pillow only) and `pdf_builder._ink_metrics` runs in `backend/.venv` (it needs reportlab).
2026-09-30 (F2 the rest of the editor's interface, B2 live): nine new files, **41** checks; **329 / 329 green** (288 + 41). Two old checks changed, none weakened — see "Changed in F2".
2026-09-30 (F3-UI share for others to fill, F3 backend live): `test_share.py` (12) + `_sharemock.py` are new; **341 / 341 green** (329 + 12). No existing check changed.
2026-09-30 (ROTFIX pages turned by their own `/Rotate`): `test_rotated_pages.py` (6) is new (suite `rotated`); no existing check changed.
2026-09-30 (ZW zero-width characters + commit refusals): `test_zero_width.py` (4) is new (suite `zerowidth`); no existing check changed.
2026-09-28 (F1a Sejda toolbar + top bar shell): `test_toolbar_v2.py` (14) is new; `test_toolbar.py`, `test_draw.py`, `test_redact.py` learnt the spec's menus, and a click on a tool that now sits in a drop-down goes through `arm_tool` in `test_toolbar.py` / `test_workspace.py` / `test_editor.py` / `test_dates.py` (not weakened, see "Changed in F1a").

> `test_viewer.py`, `test_search.py` and `test_rotation.py` are the suites that
> look at pixels. The other three read geometry out of inline styles and
> `data-*` attributes, which a viewer that renders **nothing** still produces
> perfectly — they were all green while the pages were blank. Anything about
> "is this really on screen" belongs in a pixel suite.

---

## Running them

The Vite dev server must be up (`http://127.0.0.1:5173`). The backend does
**not** need to be running — nothing here talks to it.

```powershell
# everything
python tests\ui\run_all.py

# one suite
python tests\ui\run_all.py --suite layout
python tests\ui\run_all.py --suite warnings --suite override
python tests\ui\run_all.py --suite viewer
python tests\ui\run_all.py --suite search --suite rotation

# one check (substring match on its name)
python tests\ui\run_all.py --only clamp

# watch it happen
python tests\ui\run_all.py --suite override --headed --slow-mo 250

# a screenshot of every failed check (synthetic pages only)
python tests\ui\run_all.py --shots %TEMP%\swm_ui_shots
```

A suite can also be run on its own:

```powershell
python tests\ui\test_layout_drag.py --only drag_clamps
```

Environment:

| Variable | Meaning |
|---|---|
| `SWM_UI_BASE` | target origin, default `http://127.0.0.1:5173` (a LAN address such as `http://10.31.0.58:5173` works too) |
| `SWM_UI_TRACE` | set to anything to print a full traceback for each failure |

Requirements: Python 3.10+, `playwright` (already installed on this machine,
1.63) and its Chromium. No `npm` dependency is added, nothing is installed into
`frontend/`, and `pytest` is not used — the runner is 60 lines in `_harness.py`.

Exit code is `0` only when every check passes.

---

## How the mocking works

Same style as the retired `swm_layout/zz_mock_run.js`:

* **Every `/api/*` request is answered from `_harness.MockApi`.** One router,
  no exceptions. A path nobody mocked answers `404 {"error":"zz_unmocked"}` and
  is recorded in `mock.unmocked`, so a gap fails loudly instead of hanging.
  Requests that are *not* under `/api/` (Vite's own `/src/api/*.ts` modules,
  assets) go through untouched.
* **The session is faked in `localStorage`** before the first script runs:
  `csa.token = "swm-ui-mock-token"`. No password or real token is ever written
  to disk or to a file in this directory.
* **The documents are generated in memory.** A one-page US-Letter PDF is built
  byte by byte (`synthetic_pdf`), a multi-page one by `synthetic_multipage_pdf`
  (each page a big filled block plus its own number in Helvetica — deliberately
  full of ink, so "painted" and "never painted" can be told apart by counting
  dark pixels; install it with `mock.set_pdf(...)` *after* any `set_slots`),
  and the signature image is a hand-written PNG
  (`synthetic_signature_png`, 300 x 100, `zlib` + `struct`, no Pillow). **No
  real contract is ever opened**, so any screenshot is synthetic by
  construction.
* **`window.confirm` is replaced** by a recorder (`window.__swmConfirms`) whose
  answer a test can set with `set_confirm_result(page, False)`.
* **Pixels are counted in the browser, not in Python.** `_harness.pixel_bands`
  clips a screenshot to the page's rectangle *inside `.pdf-stage`* and hands
  the PNG back to the tab, where `createImageBitmap` + `OffscreenCanvas` count
  it (nothing is added to the DOM). Do **not** reach for
  `locator.screenshot()` here: a `.pdf-page` lives in an internally scrolled
  box, Playwright resolves an element clip against the document, and it
  happily returns the app header or the wrong page — measured, two different
  pages came back with identical pixel counts. A page taller than the stage
  cannot be read whole, so `zoom_out(page, 3)` first.
* **Its own headless Chromium**, launched by the runner, so the Playwright MCP
  browser can stay busy. One browser per run, one fresh context (and therefore
  fresh `localStorage`) per check.
* **The live database is never touched.** Verified after the first green run:
  `backend/local.db` kept its previous timestamp.

The fixture contract `ZZ-UI-1` has four slots on one page — deliberately two
different geometries:

| field id | kind | bbox (x, y, w, h) | `rule_y` | effective line |
|---|---|---|---|---|
| `sig_1` | signature | 72, 160, 180, 34 | 199.5 | 199.5 |
| `date_1` | date | 350, 180, 120, 20 | 199.5 | 199.5 |
| `sig_2` | signature | 72, 360, 180, 34 | `null` | 394 (bbox bottom) |
| `date_2` | date | 350, 380, 120, 20 | `null` | 400 (bbox bottom) |

---

## DOM contract

The checks are bound to the hooks below. **Keep them during the UI rework.**
If one has to change, the matching check in `tests/ui/` must be updated in the
same commit — otherwise the baseline stops protecting anything.

### The workspace (W1, `test_workspace.py`)

`/contracts/:id/review`, `/sign` and `/edit` are **one** route element
(`workspace/Workspace.tsx`). The segment only forces a panel section open and
picks the tool the page *opens* with; switching panels never remounts it.

| Hook | On | Meaning |
|---|---|---|
| `data-testid="workspace"` | the root | classes `review workspace editor--<tool>` (+ `workspace--placing` while text/date/check/cross is armed); `data-panel` = `review` \| `sign` \| `edit`; `data-tool` = the armed tool |
| `.review__document` / `.review__panel` | document column / right panel | **kept** from the old review/sign screens (`test_override.py` reads `.review__document .alert--error`) |
| `section-<id>` / `section-toggle-<id>` | accordion section / its header button | `id` = `review` \| `sign` \| `layout` \| `objects`; `data-open="true"` on an open section, `aria-expanded` on the button. A closed section renders only its header. `/review` forces `review`, `/sign` forces `sign` + `layout`, `/edit` forces `objects` |
| `section-review-badge`, `section-sign-badge`, `section-objects-badge` | section headers | issue count / `n/m signed` (or the block reason) / object count |
| `editor-finalize` | the ONE "Generate final contract" — F1a: in the editor's top bar (`workspace/EditorTopBar.tsx`), no longer in `FinalizeBar` | off while busy, blocked, no signature at all, or a required slot empty (`finalizeDisabled`); its `title` says why |
| `finalize-bar`, `finalize-readiness`, `finalize-blocked-reason` (+ F1a `finalize-no-signature`, `finalize-ready`) | the panel's sticky bar / its "Still missing: …" line / its reason while blocked / "Place at least one signature first." / "Ready: …" | the bar keeps the readiness line (with **Show**); the button is in the top bar |
| `editor-error` | the workspace's one error block (`panels/FinalizeErrors.tsx`), `.alert--error` inside `.review__document` | a finalize 409 lists what is missing, each with **Show** |
| `editor-note`, `editor-hint` | document column / Objects section | the editor's notes; the hint says annotations are "saved in this browser only" |
| `tool-<id>` + `data-disabled-reason` | toolbar buttons | a tool that cannot be used is `disabled` and says why (aria-label, title); the Signature tool on an unapproved contract: `Contract not approved yet` |
| `tool-disabled-reason` | beside the tools | the same reason(s) in text |
| `objects-section`, `objects-list`, `object-ann-<id>` (`data-kind`, `data-page`), `objects-signatures`, `object-sig-<id>` | Objects section | one row per annotation / free signature, in finalize order, each with **Show** |
| `.slot[data-field-id]` | slot boxes | the ONLY drawing of a slot — the review no longer draws a `.highlight` for a slot issue. `slot--active` = the one selection (a slot, or the issue a sidebar click picked) |
| `.placed-signature` | slot signatures | ONLY rows in a template slot |
| `.swm-sig[data-signature-id]` (`data-signature-kind="signature"`, `is-selected`) | free signatures | ONLY rows with no slot (`field_id: null`) — never both classes for one row |

Pointer rules: with a text/date/check/cross tool armed, `.slot`,
`.placed-signature`, `.placed-dates text`, the mini panel and the grip take
**no** pointer events (a click on a slot places the annotation). With Select or
Signature they are live; Signature's click on an unsigned slot opens the
chooser, Select's only focuses it. Delete / arrows act only on a selected
annotation, never on a slot or a signature.

Gone (W1): `data-testid="document-editor"`, `open-editor-link` (review and sign
panels). `dash-open-editor` on the dashboard row stays.

Harness: `open_section(page, id)` opens a closed section; `apply_dates_to_all`
opens `sign` first (a no-op on `/sign`).

### The toolbar, the palette and the one history (W2, `test_toolbar.py`)

The toolbar is `workspace/toolbar/FloatingToolbar.tsx`; its tools come from
the registry (`workspace/tools/<id>/index.ts`, found by `import.meta.glob`).
Every hook of the old `EditorToolbar` is kept **on the docked default**
(`tool-<id>`, `ann-font`, `ann-size`, `ann-date`, `ann-delete`,
`editor-undo`, `editor-redo`, `undo-depth`, `redo-depth`, `ann-count`,
`sig-count`, `tool-disabled-reason`); in the vertical panel, the folded one
and at ≤ 640 px the property controls live in a pop-over and are only in the
DOM while it is open.

| Hook | On | Meaning |
|---|---|---|
| `editor-toolbar` | the panel (`role="toolbar"`) | `data-mode` = `docked-top` \| `floating` \| `mobile` (≤ 640 px); `data-orientation` = `horizontal` \| `vertical`; `data-edge` = `left` \| `right` (vertical only); `data-collapsed` = `true` \| `false`; `data-overflow` = how many property groups are under «More ▾». `docked-top` is in the column's flow, one row, where the old toolbar was — the page below starts at the same y |
| `toolbar-grip` | drag handle | pointer drag (≥ 3 px) floats the panel; released ≤ 24 px from the column's top → docked, pointer ≤ 24 px from the left / right edge → vertical on that edge, elsewhere → floating row. ≥ 48 px always stays inside the column (also when it shrinks). Keys: arrows 10 px, Shift 50 px, Enter docks / floats. Disabled at ≤ 640 px |
| `toolbar-collapse` | fold button (`aria-expanded`) | folded: only the armed `tool-<id>` is left |
| `toolbar-properties` / `toolbar-properties-popover` | vertical / folded / mobile | the property groups, stacked |
| `toolbar-more` / `toolbar-more-menu` | a row that does not fit | the LAST property groups (order: appearance, stroke, fill, date, edit, history, status — `stroke` / `fill` only for a drawing that has them, W4). Before anything moves there, the tool labels are hidden (`ws-toolbar--compact`) — at 1600 x 1200 the docked row is compact and nothing is under «More ▾» |
| `tool-<id>` | tool buttons | `aria-keyshortcuts` = `V` select, `T` text, `D` date, `K` check, `X` cross, `S` signature, `H` highlight, `U` underline, `C` strikeout (W3), `E` edit text (WE), `P` draw, `R` rectangle, `O` ellipse, `L` line, `A` arrow, `N` note, `M` image (W4), `I` initials, `W` whiteout (W5); one tab stop for all of them (roving `tabIndex`: arrows, Home, End); Alt+T focuses the armed one; Esc inside the panel hands the focus to `.pdf-stage`. The letters are ignored inside input / textarea / select, with Ctrl / Alt / Meta, while a `.modal` is open, and for a disabled tool |
| `tool-disabled-reason` | badge beside the tools | ONE line: ⚠ + the reason, clipped with an ellipsis; the full text is its `innerText` and `title` |
| `ann-color` (`data-color`) / `ann-color-palette` / `ann-color-<name>` (`data-hex`) | colour button / its palette / 7 swatches | `black blue red green yellow orange purple` — no white; the swatch and the preview use the backend's hex (`#000000 #1D4ED8 #DC2626 #16A34A #FACC15 #EA580C #7E22CE`). Per tool (Text remembers its colour, Check its own); with an annotation selected it recolours that one (one undo step). Greyed out for Signature |
| `data-color` | `.swm-ann`, `.swm-ann__text`, `.swm-ann__mark` | the colour it prints in; `black` when the stored entry has no `color` |
| `editor-undo` / `editor-redo` | history buttons | ONE history for annotations **and** slot positions (`title` names the step: `Move slot`, `Resize signature`, `Move together`, `Signature size`, `Reset layout`, `Place text`…). A server write (signature, bulk, autofill) is never in it; the page-edge clamp and the fit before finalize are not steps. A slider run (≤ 1.2 s between changes) is one step. 100 steps, all channels together |
| `window.__swmOverlayRenders` | dev build only | `{page: renders}` of `PageOverlay`; a drag re-renders only the page it is on |

Finalize body: an annotation carries `color` only for a non-black palette name
— an annotation nobody recoloured is the v4 entry byte for byte.

### The Sejda toolbar and the top bar (F1a, `test_toolbar_v2.py`)

EDITOR-V2-PLAN §7 / §9 F1a — a shell, no new backend function. The look is
`styles/sejda.css`, every rule under `.workspace` (tokens `--sj-accent
#0099FF`, `--sj-canvas #E9E9E9`, `--sj-border 1px solid #D0D0D0`,
`--sj-radius 4px`, `--sj-ctl-h 34px`, `--sj-font 13px`); icons are inline SVG
(`workspace/toolbar/icons.tsx`, `aria-hidden`, `stroke: currentColor`).

| Hook | On | Meaning |
|---|---|---|
| row (`.ws-toolbar__tools .editor__tool`) | the docked row | `tool-select`, `toolgroup-text`, `toolgroup-forms`, `toolgroup-images`, `toolgroup-sign`, `tool-whiteout`, `toolgroup-annotate`, `toolgroup-shapes` — in that order. «Links» is not drawn until its tool exists (F2) |
| `toolgroup-<menu>` / `-toggle` / `-menu` | the split buttons | menus `text` (text «Add text», edit-text, date), `forms` (check, cross), `images` (image), `sign` (signature, initials), `annotate` (highlight, underline, strikeout — «Text markup»; ink «Freehand»; note «Comment»), `shapes` (rect, ellipse, line, arrow). `whiteout` is a `ToolMenu` name but no tool declares it yet, so Whiteout is a plain button (its ▾ Redact comes with the tool) |
| `.ws-toolmenu__section` | inside a list | light-grey heading, `aria-hidden`, drawn where `menuSection` changes |
| `data-placed` = `below` \| `above` \| `left` \| `right` | an open `toolgroup-<menu>-menu` | the list is `position: fixed`, placed inside the viewport by `FloatingToolbar` (below its button; above when there is more room — the ≤ 640 px bar; beside a vertical panel, to the left on the right edge), `max-height` = the room left (it scrolls), placed again on scroll / resize / `visualViewport` zoom and when the panel moves. Hidden (`visibility`) until placed |
| `.ws-toolbar__history` (`editor-undo`, `editor-redo`) | right after the tools, in a wide row | «… \| Shapes ▼ \| Undo \| Redo»; never under «More ▾». In the vertical / folded / mobile pop-over they are still the `history` group |
| `editor-topbar` | first child of `.workspace__stage` | the top bar; fixed height |
| `editor-topbar-title` | top bar | `contractor_name`, else `Contract <id…>` |
| `editor-download` (`data-source` = `original` \| `final`) | top bar | «Download PDF»: `GET /api/contracts/{id}/file` (the uploaded PDF), or `GET /api/download/{id}/final` once `final_available`; bearer token, handed to the browser as a blob (`contract-<id>.pdf` / `contract-<id>-signed.pdf`). The annotated export is F3 / B2 |
| `editor-finalize` | top bar | see the W1 table |
| `editor-session`, `editor-save-status` | top bar | not drawn in F1a; **F1b** fills the `session` slot (see «The edit session» below). Still absent where the server has no document editor (every pre-F1b check) |

### The edit session and Edit text v2 (F1b, `test_session.py`, `test_text_edit_v2.py`, `test_markup_migration.py`)

EDITOR-V2-PLAN §3, §6, §7, §9 F1b; backend B1 (`/api/contracts/{id}/document/*`).
The server is `_docmock.DocMock` (B1's endpoints mocked, byte-for-byte synthetic
PDF + ITS text model, one block per line). `MockApi.document` is `None` by
default: every `/document*` call then answers **404 on purpose** (not
"unmocked") -- a server without the editor -- and every pre-F1b check runs on
the legacy path exactly as before (`/file`, annotations in this browser).

| Hook | On | Meaning |
|---|---|---|
| `workspace` `data-editor` | root | `loading` \| `ready` (GET `/document` answered) \| `unavailable` (404 / 422 `pdf_encrypted` / `pdf_corrupt`, or unreachable: legacy `/file`) |
| `workspace` `data-session` | root | `view` \| `edit`; `workspace--editing` class while editing |
| `workspace` `data-markup-sync` | root | `local` (no editor) \| `loading` \| `synced` \| `saving` \| `blocked` (409 `draft_active`) \| `error` |
| `editor-edit-pdf` | top bar | «Edit PDF» -> `POST …/draft {base_version_no, client_session_id}` (one id per tab, `sessionStorage["swm.editSession.<cid>"]`); hidden for `capabilities.reason == not_permitted`; disabled + `editor-edit-pdf-reason` for `contract_completed` / `contract_busy` / someone else's lease |
| `editor-save`, `editor-cancel` | top bar, edit mode | «Save changes» (commit) / «Cancel editing» (`confirm` «Discard N unsaved edit(s)…» when there are edits, then `DELETE …/draft?revision=N`) |
| `editor-save-status` (`data-status`) | top bar, edit mode | `starting` Opening… · `saving` Saving… · `saved` All changes saved · `dirty` Unsaved changes · `offline` Offline — retrying… · `conflict` · `committing` · `error` Not saved |
| `editor-finalize` `title` | top bar | «Save or cancel your edits first» while editing (disabled) |
| `tool-signature`, `tool-initials` `data-disabled-reason` | Sign ▾ | «Save or cancel your edits first» while editing; an armed Signature / Initials goes back to Select |
| `session-recovery` (`data-source` = `server` \| `local`), `session-resume`, `session-restore-local`, `session-discard` | banners | «Unsaved changes from HH:MM» — a draft of mine on the server (Resume = `POST …/draft` with `take_over: true`) and/or a newer copy in `localStorage["swm.draft.<cid>"]` («Restore from this browser») |
| `session-lock`, `session-take-over` | banners | someone else's leased draft (owner, lease end); Take over after a `confirm` |
| `session-error`, `session-committed`, `session-review-reopened`, `session-signatures-removed` | banners | after a commit: «Saved as version N.» + «Review reopened: …» (content commit, `review_reset`) + «N signature(s) were removed …» |
| `signatures-dialog`, `-text`, `-confirm`, `-cancel` | modal | 409 `signatures_present{count}` -> «N signature(s) will be removed and the contract must be signed again.» (Q3); confirm = the SAME `commit_request_id` + `discard_signatures: true` |
| `conflict-dialog`, `-text`, `conflict-reload`, `conflict-download` | modal | 409 `revision_conflict` / `draft_taken_over` / `base_outdated` / 404 `draft_not_found` on a PUT; «Download my changes (JSON)» = `{contract_id, base_version_no, ops, markup}`; Reload keeps the local copy |
| `markup-migration-dialog`, `-text`, `markup-import`, `markup-keep-server` | modal | server markup and this browser's `swm.annotations.<cid>` differ (first contact) |
| `text-block-layer-<page>` (`data-state`, `data-block-count`) | `.tb-layer`, in the text-layer slot, `z-index: 3` | Edit text on a server WITH the editor: the server's blocks (`GET …/versions/{no}/pages/{uid}/text`, one read per version / hash / page, ≤ 10 cached) |
| `.tb-block`, `data-testid="text-block"` | one per block | `data-block-id`, `data-page-uid`, `data-text` (rows joined by a newline), `data-editable`, `data-edited`, `data-x-pt/y-pt/w-pt/h-pt`, `data-baseline-pt`, `data-family`, `data-size`; hover = #0099FF frame; not editable -> `cursor: not-allowed` + the reason as `title`. A click in view mode starts the session (= Edit PDF) |
| `text-block-editor` | `contenteditable="plaintext-only"` over the block | `data-family` (the server's Liberation substitute), `data-size`, `data-bold`, `data-italic`, `data-color`, `data-x-pt`, `data-baseline-pt` (first baseline on the PDF's), `data-w-pt`; caret at the nearest char boundary; 2× word, 3× row; Enter commits, Shift+Enter new line, Esc cancels, Ctrl+B / Ctrl+I; paste = plain text NFC; nothing typed reaches the workspace shortcuts |
| `text-block-width`, `text-block-move-handle` | editor frame | width handle (right edge); ✥ handle while Move is on |
| `text-block-mask` | until the editor's `preview?hide=<block>` is painted | the median colour of the canvas under the block (never a fixed white); screen only |
| `text-block-pending` | an edit the page preview does not show yet | the new text in the server's face over the mask; gone when the preview is painted |
| `.pdf-page[data-override]` | page | painted from the server's one-page preview (`usePdfViewer.setPageOverride`, ≤ 6 pages) |
| `text-toolbar`: `text-bold`, `text-italic`, `text-size` (+ `text-size-toggle`, `text-size-<n>` for 8 9 10 11 12 14 18 24 30 36 48 60 72; ↑↓ ±1, Shift ±4), `text-font` (`text-font-menu`, `text-font-search`, `text-font-substitute` «Times-Roman → Liberation Serif (substitute)», `text-font-<Family>`), `text-color` (`text-color-menu`, `text-color-<name>`), `text-move`, `text-duplicate`, `text-delete` | floating toolbar | no Link button until the link tool (F2); Duplicate = a new selected `text` annotation (markup), one undo step |
| `text-overflow`, `text-overflow-message`, `text-overflow-shrink`, `text-overflow-overlap`, `text-overflow-keep` | red dashed zone + panel | `text_overflow` from a draft PUT warning / a preview / the commit: «Text overflows by N lines.» or «…run into other text…» -> `fit: shrink` / `overflow` / reopen |
| `object-convert-<groupId>` | Objects, legacy edited line | «Convert to real edit» -> a `text.replace` on the block under the white box; the group leaves the markup |
| `editor-hint` `data-storage` | Objects | `server` («saved on the server») \| `browser` (the old text) |

Draft body: `{revision, client_session_id, ops, markup}` — `markup` is `null`
unless the annotations changed during the session (then the whole layer, wire
form). Ops: one per block, `id` `op_…` kept across re-edits; `text.replace
{target{block_id, line_ids (all lines), expect_text (lines joined by a newline,
NFC), bbox}, runs (a kept prefix / suffix keeps its own span style; a toolbar
style applies to the whole block), box? (one row grown to fit, or moved /
widened by hand), line_height? (multi-line), fit}`; an emptied block is
`text.delete`. Autosave: 1500 ms debounce, 10 s max wait, one PUT in flight,
back-off 1 / 2 / 5 / 10 s on a network failure, `pagehide` keepalive PUT
(≤ 64 KB); lease renewed every 45 s. Markup wire (`lib/markupWire.ts`) = the
finalize entry with `page_uid` instead of `page`.

### Edit existing text, fonts (WE, `test_edit_text.py`)

**F1b:** on a server WITH the document editor Edit text is v2 (above). The
layer below is what Edit text shows where there is no editor (and what
`test_markup.py` reads its geometry from); a click there only says why nothing
can be edited — the whiteout + text commit is gone (Replaced in F1b).

The page's own text is a layer of `.pdf-page`, BETWEEN `.pdf-search-layer`
and `.pdf-overlay` (plan §2.4 layer 3). It exists only while a tool with
`needsTextLayer` is armed (today: `edit-text`), only on the pages of the render
window (`usePdfViewer().windowPages`, ≤ `MAX_RENDERED_PAGES` = 6), and never on
a turned page.

| Hook | On | Meaning |
|---|---|---|
| `.pdf-text-layer`, `data-testid="pdf-text-layer-<page>"` | one per window page | `data-page-number`; `data-state` = `loading` \| `ready`; `data-line-count`; `--scale-factor` (CSS px per pt) inline. Takes no pointer events itself |
| `.pdf-text-layer .textLayer` | inside it | pdf.js 4.8.69 `TextLayer` spans (one per item, % + `var(--scale-factor)`); take the pointer only with `workspace--text-select` (W3, below) |
| `.pdf-text-line`, `data-testid="pdf-text-line"` | one per merged LINE | `data-text`, `data-x-pt`, `data-y-pt`, `data-w-pt`, `data-h-pt` (ink box: ascent above to descent below the baseline), `data-baseline-pt`, `data-font-size`, `data-font-family` (`sans-serif` \| `serif` \| `monospace`), `data-bold`, `data-italic`, `data-runs` (how many pdf.js items it joins). Hover = solid outline. Clickable only with `workspace--text-tools` |
| `workspace--text-tools` | `workspace` root | a text tool is armed: the lines take the click; `.highlight`, `.swm-ann`, `.swm-ann-handle`, `.swm-sig` step aside (the inline box stays live); the slot layer steps aside too (`workspace--placing`) |
| `tool-edit-text` | toolbar | `aria-keyshortcuts="E"`, group `redact` (after Date) |
| `ann-input` | the inline box | `data-mode` = `text` \| `edit-text`; `data-font`, `data-bold`, `data-italic`, `data-size-pt`, `data-wider="true"` (edit-text: the new text is wider than the line), `aria-invalid` after a refusal. Moving the focus to the font / B / I / size / colour controls keeps it open |
| `ann-input-error` | under the box | why the text cannot be printed: `<Face> cannot print "<char>" (U+XXXX) at position N — <reason>.` + the fix (e.g. `Switch the font to Liberation Sans, which can print this text.`); the same sentence in `editor-note` |
| `ann-input-wider` | under the box | `Text is wider than the original line` (warning, never blocks) |
| `ann-bold`, `ann-italic` | toolbar (appearance) | toggles, `aria-pressed`; disabled for a mark / whiteout |
| `ann-font` | toolbar | six families in two `<optgroup>`s: `Helvetica`, `Times-Roman`, `Courier` / `LiberationSans`, `LiberationSerif`, `LiberationMono`. In a compact row the word "Font" is hidden (the select keeps its accessible name) |
| `ann-whiteout-<id>` | SVG `<rect class="swm-ann__whiteout">` | white; since W5 in its own layer `ann-whiteouts-<page>` UNDER the slots and signatures (was: first child of `ann-layer-<page>`) |
| `.swm-ann` | hit boxes | + `data-group-id`, `data-locked="true"` (a grouped whiteout: selectable, never moves), `data-w-pt` / `data-h-pt` (whiteout), `data-bold` / `data-italic`, `is-grouped` (the non-primary member of a selected group) |
| `object-group-<groupId>` (`data-kind="edited-text"`, `data-page`, `data-members`), `object-show-<groupId>`, `object-revert-<groupId>` | Objects section | an edited line is ONE row «Edited text: …», **Show** and **Revert** (removes the group, one step); its members have no `object-ann-<id>` row |

Finalize body: a whiteout is `{id, kind: "whiteout", page, x_pt, y_pt, w_pt,
h_pt, group_id?}`; a run carries `group_id` only in a group, `bold` / `italic`
only as `true` (a run nobody bolded is the v5 entry byte for byte), `text` in
NFC. The preview draws every family with the Liberation files in
`frontend/public/fonts/liberation/` (`@font-face` in `styles/fonts.css`, names
exactly `LiberationSans` / `LiberationSerif` / `LiberationMono`), metric-
compatible with Helvetica / Times / Courier.

### Highlight, underline, strikeout; the 500 ceiling; the slot date font (W3, `test_markup.py`)

The three markup tools are `workspace/tools/{highlight,underline,strikeout}`
(one factory, `tools/markup.ts`), input `text-selection`. With one armed the
text layer is mounted in SELECT mode and the browser's own selection runs over
pdf.js' spans; `workspace/text/useTextSelection.ts` turns a finished selection
(pointer released) into boxes and clears it.

| Hook | On | Meaning |
|---|---|---|
| `tool-highlight` / `tool-underline` / `tool-strikeout` | toolbar, group `markup` (after Date, before Edit text) | `aria-keyshortcuts` = `H` / `U` / `C` (C = "cross out": S is Signature, X / K the marks) |
| `workspace--text-select` | `workspace` root | a markup tool is armed: `.pdf-text-layer .textLayer` takes the pointer (upright pages only); the slot layer (`workspace--placing`) and `.highlight`, `.swm-ann`, `.swm-ann-handle`, `.swm-sig` (`workspace--text-tools`) step aside |
| `.pdf-text-layer[data-mode]` | the layer | `lines` (edit-text: `.pdf-text-line` buttons) \| `select` (markup: spans only, no line buttons) |
| `ann-markup-<id>` (`g.swm-ann__markup.swm-ann__markup--<kind>`) | SVG, after the whiteouts, before runs / marks | `data-kind`, `data-color`, `data-ann-rects` = JSON `[[x, y, w, h], …]` in pt; `fill` = palette hex, `fill-opacity` 0.35 (highlight) / 1; one `<rect data-ink-pt>` per box: highlight = the box, underline = bar `max(0.75, h·0.08)` whose lower edge is the box's, strikeout = the same bar centred |
| `ann-<id>` (`.swm-ann--<kind>`) | hit box over the FIRST box | + `data-ann-rects`, `data-rect-count`; no `data-size-pt`, no corner grip; drawn under every other annotation's hit box |
| `ann-<id>-part-<n>` (`.swm-ann--part`) | one more hit box per further line | same gestures, `tabIndex=-1`, `aria-hidden` |
| `pdf-search-highlight-all` | search box, after ✕ | «Highlight all»: disabled with no match / while searching; one highlight per match from the search's own boxes, the Highlight tool's colour, ONE undo step; a match already carrying a highlight with the same boxes is skipped |
| `ann-count` | toolbar | `N / 500` (was `/ 200`) |
| `layout-date-font` (`select[aria-label="Date font"]`), `layout-date-bold`, `layout-date-italic` | «Position & appearance» | six families (two `<optgroup>`s) + B / I toggles (`aria-pressed`); drawn on `.placed-dates text` (`data-font`, `data-bold`, `data-italic`) |

Finalize body: a markup is `{id, kind, page, rects: [{x_pt, y_pt, w_pt,
h_pt}], color, group_id?}` — 1…50 boxes, each ≥ 1 pt and inside its page;
`color` ALWAYS present (default `yellow` / `red`); no `x_pt` / `y_pt` at the top
level. `layout.date_bold` / `date_italic` are sent only as `true` — an untouched
layout body has exactly the pre-v6 keys.

A selection's box: x / width from the run's own start and PDF width, split at
the selected characters in the Liberation face of its family (no kerning);
y / height = the run's ink box (ascent → descent, the same box edit-text
reads). Blanks at either end are not marked. Boxes of one line merge (tops
< 2 pt apart, or overlapping by half the smaller height) unless a gap wider
than the line is tall separates them. The search's boxes are split the same
way now (they were split by character count).

### Drawing tools, notes, images, drop-downs (W4, `test_draw.py`)

The drag tools (`ink`, `rect`, `ellipse`, `line`, `arrow`) draw on
`overlay/ToolCaptureLayer.tsx`: the FIRST child of each page's `.pdf-overlay`,
present only while one is armed. `note` and `image` are click tools.

| Hook | On | Meaning |
|---|---|---|
| `tool-ink` `P`, `tool-rect` `R`, `tool-ellipse` `O`, `tool-line` `L`, `tool-arrow` `A`, `tool-note` `N`, `tool-image` `M` | toolbar | groups `draw` (ink, shapes) and `insert` (note, image) — F1a: ink / note in `annotate`, shapes in `shapes`, image in `images`. `tool-image` is `serverWrite` (off with the reason on an unapproved contract) but steps the slots aside (`slotsStepAside`) |
| `toolgroup-<menu>` (`data-tool`, `aria-pressed`) / `toolgroup-<menu>-toggle` (`aria-expanded`) / `toolgroup-<menu>-menu` (`role="menu"`, `hidden` while closed) | the drop-downs (W4: `markup`, `shapes`, `insert`; since F1a the spec's `text`, `forms`, `images`, `sign`, `annotate`, `shapes` — see the F1a section) | the main half is an `.editor__tool` in the row and arms the member used last (it starts on the first); the ▾ opens the list; the members' own `tool-<id>` buttons (`.ws-toolmenu__item`, `role="menuitemradio"`, `data-menu`) live in the list — in the DOM even when it is closed. Keys: Enter / Space / ↓ on the ▾ open it onto the armed (or shown) member; ↑ ↓ Home End move; Enter arms; Esc closes (focus back on the ▾). Harness: `arm_tool(page, id)` opens the list first when needed |
| `workspace--drawing` | `workspace` root | a drag tool is armed: `.highlight`, `.swm-ann`, `.swm-ann-handle`, `.swm-sig`, `.swm-note-popup` step aside (and the slots, `workspace--placing`) |
| `ws-capture-<page>` (`.ws-capture`, `data-drawing`) | first child of `.pdf-overlay` | the press → `beginGesture` (pt, clamped to the page); pointer captured; Esc / pointercancel drop the gesture; ONE store commit on release |
| `capture-preview` (`data-kind`; `data-box-pt` rect/ellipse, `data-line-pt` line/arrow, `data-point-count` ink) | inside `.ws-capture__preview` of THAT page only | the live drawing; its state is the capture layer's own, so no `PageOverlay` re-renders while the pointer moves |
| `ann-shape-<id>` (`rect` / `ellipse`, `data-fill`), `ann-line-<id>` (`g` with `line` + arrow `polygon[data-head-pt]`), `ann-ink-<id>` (`path`), `ann-image-<id>` (`image[preserveAspectRatio=none]`, `data-state` `loading` \| `ready` \| `failed`), `ann-note-<id>` (`g`) | SVG, after the markups and before runs / marks; notes last | the print: `stroke` / `fill` = palette hex, `stroke-width` = stroke_pt × scale (centred), `fill-opacity` 0.2, ink round caps / joins, lines butt caps, arrow head 6 × 3 × stroke with the shaft stopping at its base |
| `ann-<id>` | hit box | + `data-stroke-pt`, `data-fill`, `data-x1-pt`…`data-y2-pt` (lines; the box is a bar rotated along the line), `data-path-count` / `data-point-count` (ink), `data-asset-id`, `data-w-pt` / `data-h-pt` (rect, ellipse, image) |
| `ann-handle-<nw\|n\|ne\|e\|se\|s\|sw\|w>` | selected rect / ellipse / image | resize; the opposite side stays; ≥ 1 pt; on the page; an image keeps its ratio (Shift frees it); a rect / ellipse corner with Shift is a square; arrows move the handle 0.5 pt (Shift 2); one step per drag |
| `ann-handle-start` / `ann-handle-end` | selected line / arrow | move one end (Shift: 45° around the other); one step |
| `ann-stroke` (number 0.5…12, step 0.5), `ann-fill` (`aria-pressed`) | PropertiesBar groups `stroke` / `fill` | only while the kind has them; with a drawing selected they change it (one step each); a drawing's `appearance` group is the colour alone. `swm.toolProps` keeps `stroke` / `fill` |
| `note-editor`, `note-input` (textarea, `maxLength` 1000), `note-count`, `note-save`, `note-cancel`, `note-input-error` | the sticky-note box | Enter = new line, Ctrl+Enter / Save / a click elsewhere saves, Esc cancels; an empty note is a cancel; a character outside WinAnsi is refused with its code point (same sentence in `editor-note`) |
| `note-popup-<id>` | beside a selected note | its text, `white-space: pre-wrap` |
| `ann-image-file` | hidden `input[type=file]` | the Image tool's dialog (a click on the page opens it) |

Upload: `POST /api/contracts/{id}/annotation-assets` with JSON `{image_data:
"data:image/png|jpeg;base64,…"}` (MIME from the file's magic bytes), ONCE per
picture; preview `GET …/annotation-assets/{asset_id}` once per asset (the cache
survives React StrictMode's rehearsal remount). Refused before any upload:
> 2 MB, not PNG/JPEG by magic bytes, > 4000 px a side / 20 M pixels. Server
refusals in words: 413 "too large for the server", 422 "could not use this
image", 409 "once the contract is approved", 409 with `details.limit` "maximum
of N uploaded images". Placed at 0.75 pt / px, fitted to ≤ 200 pt on the
longer side and to the page, top-left at the click. MockApi: `mock.assets`
(id → bytes), `mock.asset_uploads`, `mock.asset_response` (canned answer).

Finalize body (always every key; `group_id` only in a group):
`ink {id, kind, page, paths: [[{x_pt, y_pt}]], stroke_pt, color}`,
`rect|ellipse {…, x_pt, y_pt, w_pt, h_pt, stroke_pt, color, fill}`,
`line|arrow {…, x1_pt, y1_pt, x2_pt, y2_pt, stroke_pt, color}`,
`note {…, x_pt, y_pt, text, color}`, `image {…, asset_id, x_pt, y_pt, w_pt,
h_pt}`. Ink: RDP 0.5 pt; a stroke over the limits is split into several ink
elements (≤ 2000 points a path, ≤ 4000 and ≤ 20 paths an element) in one step;
the list's ink points are capped at 20 000 (a stroke past it is refused, a
stored entry past it dropped).

### Whiteout, initials, the stored-annotation clamp (W5, `test_redact.py`)

| Hook | On | Meaning |
|---|---|---|
| `tool-whiteout` `W` | toolbar, group `redact` (after Edit text) | a drag tool (`workspace--drawing`); `title` carries the notice "…only hides … the original text stays inside the file…" |
| `capture-preview[data-kind="whiteout"]` (`rect.ws-capture__whiteout`, `data-box-pt`) | the capture layer of that page | white fill, dashed grey stroke (screen only) |
| `ann-whiteouts-<page>` (`svg.swm-ann-layer.swm-ann-layer--whiteout`) | `.pdf-overlay`, right after `.ws-capture` and BEFORE the slots / signatures / `ann-layer-<page>` | every whiteout of the page (`ann-whiteout-<id>`, `data-box-pt`); a free one has `swm-ann__whiteout--free` (dashed edge, screen only), a grouped one (edit-text) not. Paint order = print order: whiteout → signatures → the rest |
| `ann-<id>` of a free whiteout | hit box (above everything) | 8 `ann-handle-<dir>` when selected (Shift on a corner: square); moves, Delete, undo; a grouped one keeps `data-locked="true"` and no handles |
| `swm.whiteout.noticed` | `localStorage` | set by the first whiteout, which puts the notice in `editor-note` once |
| `tool-initials` `I` | toolbar, group `fill` (after Signature) | `serverWrite` (off with `Contract not approved yet`), not in the undo history (`title` says so); its click opens the chooser (`.modal__title` "Place your initials here", "Apply initials") |
| `.swm-sig[data-signature-kind="initials"]` (`swm-sig--initials`) | free signature layer | a free row whose box fits 60 × 24 pt, or whose id is in `swm.initials.<contractId>` (`lib/initials.ts`) |
| `object-sig-<id>` `data-kind` = `initials` \| `signature`; `initials-every-page-<id>`; `initials-remove-all`; `initials-progress`; `initials-report` (+ `initials-failures`) | Objects | «Every page»: one POST per other page (same distance from the bottom-right corner), `BULK_CONCURRENCY` = 3, pages that already have it are skipped; «Remove all initials» (confirm) deletes only initials |
| `sig-card-initials`, `sign-library-initials` | My signatures / the signature dialog | a library entry labelled `Initials` (a copy saved from the initials chooser is labelled so) |
| `editor-note` "N annotations were moved to stay on the page …" | document column | stored annotations pulled onto their page once its size is known — `replace`, not an undo step, said once |

MockApi (W5): `mock.place_echo = True` answers each free POST with its own row (`zz-free-<n>`, the sent page / bbox) and keeps it in `mock.free_rows` (listed by GET; seedable); `mock.place_bodies`, `mock.place_fail_pages = {page: status}`, `mock.deleted_signatures`, `mock.library_saves` (`POST /api/signatures`). All off / empty by default.

### The rest of the editor (F2; `test_textbox.py`, `test_links.py`, `test_pages.py`, `test_images.py`, `test_sign_menu.py`, `test_annotate_v2.py`, `test_shapes_v2.py`, `test_export.py`, `test_fonts_more.py`)

EDITOR-V2-PLAN §7.2–7.4, §9 F2; backend B2. Everything below exists ONLY
where the server has the document editor (`MockApi.document` set,
`workspace[data-editor-v2="true"]`); without it the toolbar, the dialogs and
every hook are F1a/F1b's exactly. `_harness.MockApi` learnt `/api/assets`
(gallery, `gallery_*`), `/api/fonts` (`fonts`), typed signatures
(`typed_saves`) and gallery copies (`gallery_copies`); `_docmock.DocMock`
learnt page ops (per-version `uids` / `rotations`, `page_order`), the whole
draft (`/draft/preview.pdf` + `X-Page-Uids`, `draft_pdf_gets`), `images`,
`links`, exports (`export_bodies`) and the uploader's 403 `page_has_slots`
(`slot_page_uids`). `_f2.py` holds the shared helpers.

| Hook | On | Meaning |
|---|---|---|
| `tool-textbox` (T), `tool-links`, `tool-square` (Q), `tool-stamp`, `tool-image-remove`, `tool-redact`, `tool-highlight-ink` (F) | toolbar | the F2 tools (`requiresEditor`); `tool-text` is hidden with the editor (`hiddenWithEditor`); Whiteout becomes `toolgroup-whiteout` (▾ Redact) |
| `menu-extras-<menu>`; `gallery-thumbs`, `gallery-<asset>`, `gallery-remove-<asset>`, `gallery-file`, `menu-gallery-add`, `menu-new-stamp`; `sign-thumbs`, `sign-saved-<id>`, `menu-new-signature`, `menu-my-signatures`; `menu-toggle-annotations` (`data-hidden`) | drop-downs | what a list holds besides its tools (never a `tool-*` testid) |
| `workspace` `data-editor-v2`, `data-markup-hidden`, `data-pages` | root | the editor is there; annotations hidden on screen; the page uids in the order SHOWN (the draft's when it has page ops) |
| `textbox-editor` (`data-font`, `data-size-pt`), `textbox-editor-error`; `ann-textbox-<id>` (`data-line-count`, `data-align`, `data-line-height`, lines `g[data-baseline-pt][data-line-width-pt]`); `ann-stamp-<id>` (`data-label`), `ann-stamp-date-<id>` | page | the v7 boxes as the engine prints them |
| `ann-align`, `ann-line-height`, `textbox-font` (+ `-menu`, `-more`), `ann-style` → `ann-style-panel`: `ann-dash-<solid\|dash\|dot>`, `ann-fill-none`, `ann-fill-color-<c>`, `ann-heads-<end\|start\|both>`, `ann-opacity`, `ann-rotate-<deg>`; `ann-duplicate` | PropertiesBar | text box alignment / spacing / family; the v7 look; Duplicate |
| `link-layer-<page>`, `link-<lkN\|op id>` (`data-target`, `data-rect-pt`), `link-grip-<id>`; `link-dialog`: `link-kind-uri`, `link-kind-page`, `link-url`, `link-page`, `link-error`, `link-save`, `link-delete`, `link-cancel`; `text-link` | page / modal / text toolbar | the links while the tool is armed |
| `page-images-<page>`, `page-image-<page>-<xref>`; `pending-ops-<page>`, `redact-<op>`, `redact-drop-<op>`, `image-removed-<xref>`, `image-removed-drop-<xref>` | page | Delete original image; what the draft will do until saved |
| `page-insert-<after>`, `page-item-<n>` (draggable, `data-rotation`), `page-rotate-left-<n>`, `page-rotate-right-<n>`, `page-rotation-<n>`, `page-delete-<n>`, `pages-error`; `page-delete-dialog`: `page-delete-counts` (`data-signatures/slots/links/annotations`), `page-delete-reason`, `page-delete-confirm`, `page-delete-cancel` | thumbnail strip / modal | the page tools |
| `export-dialog`: `export-include` (+ `-label` «Include annotations (N)»), `export-hidden-note`, `export-unsaved-note`, `export-error`, `export-download`, `export-cancel`; `editor-download[data-source="export"]` | top bar / modal | «Download PDF» with the editor |
| `stamp-dialog`: `stamp-mode-preset`, `stamp-mode-text`, `stamp-preset`, `stamp-text`, `stamp-date`, `stamp-color-<c>`, `stamp-border`, `stamp-picture`, `stamp-save`, `stamp-cancel` | modal | «New Stamp…» |
| `sign-type-panel`, `sign-type-text`, `sign-type-font-<Script>`, `sign-type-apply`; tab `[data-tab="type"]` | signature dialog | the Type tab |
| `fonts-dialog`, `fonts-search`, `fonts-option-<family>`, `fonts-preview-<family>`, `fonts-license-<family>`, `fonts-substitute-<family>`, `fonts-close`; `text-font-more`; `text-align`, `text-line-height` | text toolbar / modal | «More fonts…»; a block's alignment and line spacing |

### Interactive form fields (F2b, `test_forms.py`)

EDITOR-V2-PLAN §3 `field.*`, §7.4, §9 F2, §11 Q2; backend B3. Only with the
document editor (`requiresEditor`). The server is `_formmock.FormDocMock`
(`enable_forms(api, pages, fields=…)`): a `DocMock` that also serves
`GET …/versions/{n}/fields` (`fields[n]`, `field_gets`), `POST …/detect-fields`
(`suggestions`, `detect_bodies`), checks a PUT's field ops like the engine
(422 `invalid_ops` with `field_name_taken` / `radio_value_taken` /
`unknown_field` / `field_readonly` / `field_not_fillable` /
`undrawable_character` / `invalid_field_value`; 403 `fill_only` for a
`can_fill`-only user) and plays them over the fields at commit. A plain
`DocMock` answers `/fields` with `[]` (a document without form fields).
Field ops ride the session's op list in ONE canonical order (`field.remove`,
`field.update`, `field.add`, `field.value`; `workspace/forms/fieldModel.ts`).

| Hook | On | Meaning |
|---|---|---|
| `tool-field-text`, `-multiline`, `-dropdown`, `-radio`, `-checkbox`, `-signature`, `tool-field-detect` | Forms ▾, heading «Add new form fields» | drag = a field (a click = the usual size, centred); off with the reason without `can_edit` |
| `field-layer-<page>` (`data-mode` = `edit` \| `fill` \| `detect` \| `passive`) | page, last child of `.pdf-overlay` | a field tool armed / Select / Detect / any other tool |
| `form-field` (`data-key`, `data-name`, `data-type`, `data-source` = `original` \| `editor` \| `draft`, `data-rect-pt`, `data-export-value`, `data-selected`, `data-locked`, `data-problem`, `data-required`) | page | one widget (a Docusign `hidden` one is never drawn) |
| `field-handle-<nw\|n\|ne\|e\|se\|s\|sw\|w>` | selected field / suggestion | resize; the body moves; Delete / arrows (Shift ×10) on the selected field |
| `field-input` (`data-kind`) | fill mode | the HTML control → `field.value` (text: Enter / blur; Esc = back); `field-charset-warning` = a non-WinAnsi character, nothing sent |
| `forms-dock`, `forms-count`, `forms-hint`, `field-tab-order-open` | right, over the panel | edit mode |
| `field-properties` (`data-type`): `field-name`, `field-name-taken` + `field-rename-suggest` «Rename to X_2», `field-name-invalid`, `field-radio-group`, `field-export-value` (+ `field-export-issue`, `field-export-suggest`), `field-required`, `field-readonly`, `field-tooltip`, `field-default`, `field-options` / `field-option-<i>` / `field-option-remove-<i>` / `field-option-new` / `field-option-add`, `field-font`, `field-size`, `field-max-len`, `field-text-issue`, `field-locked`, `field-source`, `field-geometry`, `field-duplicate`, `field-delete` | dock | one step per committed change |
| `tab-order-panel`, `tab-order-page`, `tab-row` (`data-key`, `data-name`, draggable), `tab-up`, `tab-down` | dock | the page's order → `tab_index` 10, 20, … |
| `detect-panel` (`data-status`), `detect-status`, `detect-row`, `suggestion-type`, `detect-accept`, `detect-reject`, `detect-accept-all`, `detect-run`; `field-suggestion` (`data-suggestion-id`, `data-type`, `data-rect-pt`), `suggestion-accept`, `suggestion-reject` | dock / page | Detect form fields (pages on screen only; nothing added until accepted; Accept all = one step) |
| `editor-save` for a `can_fill`-only user | top bar | once filling started the session (no Edit PDF for them) |

### Share for others to fill (F3, `test_share.py`)

EDITOR-V2-PLAN §8, §11 Q4 (LAN only, fill only); backend F3. `_sharemock.py`
is the server: without `enable_share` (`MockApi.share` absent) the routes the
workspace reads on every open answer the way a server with nothing shared does
-- deliberately, so not "unmocked": `GET …/document/versions/{n}/fields` →
`[]` (only while `MockApi.document` is set), `GET …/share-links` and
`GET …/submissions` → `[]`. `enable_share(api, fields)` installs `ShareMock`
(links with a sha256'd token, revoke, submissions, accept → a `DocMock` draft
of `field.value` ops under the given `client_session_id` / 409
`draft_locked`, reject; the public `/api/share/*` with the same 404 for every
refusal and 429 when `locked`; `force` queues answers by "METHOD /shape").
`_harness._handle` asks `_sharemock.route_share` first (3 lines).

| Hook | On | Meaning |
|---|---|---|
| `share-open` (`aria-label` «Share for others to fill»), `share-active-count` | top bar | only with `capabilities.can_edit` AND a shareable field (or a link to manage); never disabled-and-dead |
| `share-dialog`: `share-field-<name>` (checkboxes, one per name), `share-all`, `share-none`, `share-blocked-fields`, `share-no-fields`, `share-label`, `share-expiry` (+ `-unit` hours/days; default 72 h, max 14 days), `share-max` (default 1), `share-warning`, `share-ack`, `share-create` (+ `share-create-reason` while off), `share-error`, `share-close` | modal | nothing is created before `share-ack` is ticked AND Create is pressed |
| `share-created`: `share-url`, `share-copy`, `share-copy-status` (`data-state` idle/copied/failed), `share-once-note`, `share-done` | modal | the URL of the create answer, shown once; gone after Done (never stored) |
| `share-links`, `share-link-<id>` (`data-status` active/expired/revoked/used_up), `share-revoke-<id>`, `share-links-empty` | modal | the contract's links; Revoke asks `confirm` first |
| `shared-form` (`data-state` loading/ready/invalid/throttled/error/thanks), `shared-invalid`, `shared-throttled`, `shared-thanks`, `shared-left`, `shared-title`, `shared-page-<n>` (`data-scale`), `shared-field-<name>` (radio: `shared-field-<name>-<export>`), `.sf-cover`, `shared-name`, `shared-email`, `shared-submit`, `shared-errors`, `shared-field-error-<name>` | `/s` | the public page: no app chrome; the fragment is cleared before any request; the token only in `X-Share-Token` |
| `section-submissions` (+ `section-toggle-submissions`, `section-submissions-badge`), `submissions-refresh`, `submissions-empty`, `submissions-accepted`, `submissions-load-error`, `submission-<id>` (`data-status`), `submission-values` (`[data-field]`), `submission-masked`, `submission-name`, `submission-unreadable`, `submission-accept-<id>`, `submission-reject-<id>`, `submission-reject-form`, `submission-reject-reason-input`, `submission-reject-confirm`, `submission-reject-reason`, `submission-error` (`data-code`) | right panel | a section of its own (not in `SECTION_IDS`), drawn only with a link or a submission |

### Zero-width characters and the commit refusals (ZW, `test_zero_width.py`)

The real Docusign template carries U+200B on 51 % of its lines. The server
leaves the zero-width FORMAT characters (`IGNORABLE`: U+00AD, U+200B, U+200C,
U+200D, U+2060, U+FEFF) out of `line.text` and `chars` and out of new text;
the in-place text editor does the same (`lib/textCheck.ts` `stripIgnorable`,
`isIgnorable`; `adaptModel` skips them should a server still send them;
`commitOpen` / `opFor` strip them). Annotation texts keep the stricter rule.
`DocMock.model_hook = with_zero_width` serves the pre-fix wire.

| Hook | On | Meaning |
|---|---|---|
| `.tb-block[data-text]` | text layer | the visible text only (no U+200B) |
| `editor-note` | workspace | still names U+202E (`Liberation Sans cannot print … (U+202E)`): direction controls are refused before anything is sent |
| `session-error` | top bar | `Part of the old text could not be removed from the page, so nothing was saved; …` (422 `old_text_not_removed`) / `A moved text block could not be placed at its new position; …` (422 `text_move_not_applied`); status `error`, the draft kept |

### Changed in F2 (none weakened)

| Check | Before | Now | Why |
|---|---|---|---|
| `test_markup_migration.py` `place_text` helper (used by `a_change_is_put_with_the_revision_and_in_a_session_it_rides_the_draft`, `cancel_puts_the_servers_markup_back`, `without_the_editor_…`) | armed `tool-text`, typed into `ann-input`, read `element["text"]` | WITH the editor arms `tool-textbox` (Add text is the text box now), types into `textbox-editor`; `text_of()` reads a run's `text` or a text box's runs; the no-editor check still uses `tool-text` + `ann-input` | same assertions (revision, the text, page_uid only, the draft carries the whole layer, no markup PUT in a session, Cancel puts the server's back) |
| `test_text_edit_v2.py` `the_toolbar_restyles_the_block_the_font_list_names_the_substitute_and_there_is_no_link` → `…_and_offers_a_link` | `missing(labels, "Link")` (no link tool before F2) | `contains(labels, "Link")` + exactly one `text-link` | the link tool exists now; what the button does is pinned by `test_links.py` |

### Replaced in F1b (user decision «oq quti TAQIQ» — no white box)

`test_edit_text.py` pinned the WE commit (a `whiteout` + a `text` over an
existing line). That behaviour is removed, so these 11 checks stay in the
file UNREGISTERED (`replaced = Registry(...)`), replaced by:

| Old check (WE) | Replaced by (`test_text_edit_v2.py` unless said) |
|---|---|
| `a_click_opens_the_box_with_the_original_text_in_the_right_face` | `a_click_starts_the_session_and_opens_the_editor_at_the_nearest_character`, `the_server_blocks_are_framed_and_a_locked_block_says_why` (family per block) |
| `a_commit_is_one_group_one_step_whiteout_over_the_line_text_on_its_baseline` | `enter_commits_one_text_replace_and_a_re_edit_keeps_its_id` (no whiteout in any body) |
| `an_untouched_line_adds_nothing` | `a_click_starts_the_session_and_opens_the_editor…` (Esc / unchanged -> no op) |
| `a_second_click_re_opens_the_same_edit_never_a_second_whiteout` | `enter_commits_one_text_replace_and_a_re_edit_keeps_its_id` (same op id) |
| `the_whiteout_stays_put_the_text_moves_and_delete_takes_the_group` | `an_emptied_block_is_a_text_delete_and_undo_takes_it_back`, toolbar Move / Delete |
| `the_objects_list_shows_an_edited_line_with_show_and_revert` | `a_legacy_edited_line_converts_to_a_real_edit` (Show / Revert stay; + Convert) |
| `cyrillic_and_uzbek_go_through_in_liberation_and_helvetica_refuses_them_up_front` | `paste_is_plain_text_in_nfc` (U+02BB goes through); new text is always a Liberation face (B1), Helvetica is never offered |
| `an_emoji_is_refused_with_a_clear_message` | `an_emoji_is_refused_by_name_before_anything_is_sent` |
| `bold_false_is_never_sent_and_bold_true_is` | `enter_commits…` (no false booleans in runs) + `the_toolbar_restyles_the_block…` (`bold: true`); the Text-tool half stays covered by `text_and_date_tools_take_the_six_families_and_bold_italic` |
| `a_longer_text_is_flagged_as_wider_but_not_blocked` | `enter_commits…` (one row grows to fit) + `overflow_shows_the_zone_and_shrink_sets_the_fit` |
| `the_font_list_shows_both_groups` | `the_toolbar_restyles_the_block_the_font_list_names_the_substitute_and_there_is_no_link` |

Baseline after F1b: 269 − 11 = **258** pre-F1b checks + the new suites.

### Changed in F1b (none weakened)

* `_harness.MockApi`: `/api/contracts/{id}/document*` answers 404 on purpose
  while `document is None` (every pre-F1b check); `_docmock.py` is the editor.
* `test_edit_text.py`: the 11 WE checks above unregistered; the 9 others
  (layer only with the tool / in the window, lines merged, hover frame, turned
  page, six families, measured face, stored rubbish, search, 49 pages) run
  unchanged on the no-editor path.

### Changed in F1a (none weakened)

The toolbar was regrouped into the spec's menus (EDITOR-V2-PLAN §7): Select \|
Text ▼ \| Forms ▼ \| Images ▼ \| Sign ▼ \| Whiteout \| Annotate ▼ \| Shapes ▼ \|
Undo \| Redo. Every `tool-<id>` and every letter is kept; what moved is WHERE
a button is (in a list instead of the row), so a check that clicked a tool
now in a list opens the list first (`arm_tool`, the same helper W4's checks
use — it clicks the same `tool-<id>` button).

| Check | Old | New | Why |
|---|---|---|---|
| `test_toolbar.py` `ALL_TOOLS` / `TOOL_ORDER` / `MENU_MEMBERS` | 19 tools in the W5 registry order; row `select, text, check, cross, signature, initials, date, toolgroup-markup, toolgroup-shapes, toolgroup-insert, edit-text, whiteout` (12); members `highlight … image` (10) | same 19 tools in the F1a order; row `select, toolgroup-text, toolgroup-forms, toolgroup-images, toolgroup-sign, whiteout, toolgroup-annotate, toolgroup-shapes` (8); members = the other 17 in registry order | the spec's menus; `KEPT_HOOKS` still asserts every `tool-<id>` once, `SHORTCUTS` every letter |
| `test_toolbar.py` `place_text`, `collapse_folds_…`, `one_letter_…` (inline box), `at_640_…`, `the_palette_…` (×3), `a_server_write_…` | `tool-text` / `tool-check` / `tool-signature` `.click()` | `arm_tool(page, id)` | those tools are in «Text ▼» / «Forms ▼» / «Sign ▼» |
| `test_toolbar.py` `roving_focus_…` | → from Select lands on `tool-text` | → lands on `tid(TOOL_ORDER[1])` = `toolgroup-text`; + a new assertion that Home → → is `toolgroup-forms` before Enter arms Check | Text is a drop-down's main half now; «Enter arms the focused tool» (Check) unchanged |
| `test_toolbar.py` `when_room_runs_out_…` | «More ▾» menu contains `editor-undo` («so is Undo») | «More ▾» does NOT contain Undo; ONE Undo, visible in the row (`.ws-toolbar__main`), and Redo too | spec: «… \| Shapes ▼ \| Undo \| Redo» — Undo / Redo stay in the row; the counts still move under «More ▾» (asserted as before) |
| `test_draw.py` `seventeen_tools_fit_one_row…` | `row[7:10]` = markup / shapes / insert, `len(row) == 12`; Shapes starts on `ink`, Insert on `note`; `h` presses `toolgroup-markup` | `row[6:8]` = annotate / shapes, `len(row) == 8`; Shapes starts on `rect`, Annotate on `highlight`, Images on `image`; `ink` / `note` have `data-menu="annotate"`, `image` `images`; `h` presses `toolgroup-annotate` | Draw and Note moved to «Annotate ▼», Image to «Images ▼»; the Shapes mouse / keyboard walk (Ellipse → Line) is unchanged |
| `test_redact.py` `the_toolbar_still_fits_one_row…` | `tool-initials.is_visible()` «is a button of the row» | Whiteout visible in the row; Initials is inside `toolgroup-sign-menu` and visible once the list opens | Initials sits in «Sign ▼» after Signature; the one-row / overflow-0 / 12-signatures checks are unchanged |
| `test_workspace.py` (×6), `test_editor.py` (×6 + the `place_mark` helper), `test_dates.py` (×2), `test_edit_text.py` `arm()` helper | `tool-text` / `tool-check` / `tool-cross` / `tool-date` / `tool-signature` `.click()` | `arm_tool(page, id)` | in a drop-down now |
| `test_redact.py` / `test_geometry.py` `px()` helper | page point → viewport pixel, no scroll | the same, but a point that falls below the visible page area is first scrolled into view (`.pdf-stage.scrollTop`), then re-measured | the top bar (40 px, glued to the 40 px toolbar) starts the pages 25 px lower (219 vs 194 at 1600 × 1200), so y = 600 pt at the fit scale (1189 px) fell just below the viewer (1188 px). Every assertion stays in PDF points; only the click is made reachable |
| `test_toolbar_v2.py` (own) | — | the toolbar's #D0D0D0 border is read on its bottom edge; its top edge is the lighter seam under the top bar | docked, the two bars form one header block |
| `editor-finalize` (every suite that finalizes) | in `panels/FinalizeBar.tsx` | in `workspace/EditorTopBar.tsx`; same testid, same accessible name «Generate final contract», same gate | spec §7 top bar; no locator changed — the checks find it by testid / role name |

### Changed in the geometry fix (none weakened)

| Check | Old | New | Why |
|---|---|---|---|
| `test_redact.py` `initials_go_through_the_free_signature_endpoint_…` | `bbox w/h ≈ 3.0` ("the pad's ratio (480 × 160) kept") | `bbox w/h ≈` the ratio of the image the server stores for the posted `image_data` (`prepare_signature_image`, ±2 %) | the old number pinned the bug: the server crops the pad to its ink (e.g. 426 × 38), so a 3:1 box printed the initials as a 5 pt strip inside a 20 pt box. The 60 × 24 limit, "as large as the box allows" and "centred on the click" are unchanged |

### Changed in W5 (none weakened)

| Check | Old | New | Why |
|---|---|---|---|
| `test_toolbar.py` `ALL_TOOLS` / `TOOL_ORDER` / `SHORTCUTS` | 17 tools, 10 row buttons | 19 tools: `initials` after `signature`, `whiteout` after `edit-text` (12 row buttons), + `I`, `W` | two tools were added; every assertion kept |
| `one_letter_shortcuts_…` | … P R O L A N M | + `i` Initials, `w` Whiteout | more coverage |
| `test_draw.py` `seventeen_tools_fit_one_row…` | `row[6:9]` = the drop-downs, `len(row) == 10` | `row[7:10]`, `len(row) == 12` | Initials sits before the drop-downs; the drop-downs and "nothing under «More ▾»" are unchanged |

### Changed in W4 (none weakened)

| Check | Old | New | Why |
|---|---|---|---|
| `test_toolbar.py` `TOOL_ORDER` / `SHORTCUTS` / `KEPT_HOOKS` | ten tools, one button each | `ALL_TOOLS` (17, every `tool-<id>` still asserted present with its key) + `TOOL_ORDER` = the ROW: `…, date, toolgroup-markup, ink, toolgroup-shapes, note, image, edit-text`; a new assertion pins the drop-downs' members in registry order | the markup tools and the shapes share a drop-down so 17 tools fit one row at 1600 px |
| `the_toolbar_opens_docked…` | order via `.replace('tool-', '')` | `.replace(/^tool-/, '')` (a `toolgroup-` id has no such prefix) | same order rule |
| `roving_focus_…` | End / ← / ↑ → `tool-<last>` / `tool-<last-but-one>` | the same through `tid()` (a row entry may be a drop-down) | same |
| `collapse_folds_…` | `== len(TOOL_ORDER)` | unchanged expression; the row now has 12 `.editor__tool` | same |
| `one_letter_shortcuts_…` | V T D K X S E | + P R O L A N M | more coverage |
| `test_markup.py` `arm()` | `tool-<name>` click | `arm_tool(page, name)` (opens «Markup ▾» first) | the three tools are in the drop-down |

### Changed in W3 (none weakened)

| Check | Old | New | Why |
|---|---|---|---|
| `test_toolbar.py` `TOOL_ORDER`, `SHORTCUTS` | seven tools | ten: `…, date, highlight, underline, strikeout, edit-text`, + `H U C` | tools were added; every assertion kept (1600 px row still has nothing under «More ▾») |
| `test_editor.py` 393, 465, 545, 704-705; `test_toolbar.py` 616 | `"0 / 200"`, `"1 / 200"`, `"2 / 200"`, "…the 200 annotations…" | `/ 500` | `MAX_ANNOTATIONS` 200 → 500 (backend v5) |

### Changed in WE (none weakened)

| Check | Old | New | Why |
|---|---|---|---|
| `test_toolbar.py` `TOOL_ORDER`, `SHORTCUTS` | six tools, V T D K X S | seven: `…, date, edit-text`, + `E` | a tool was added; every assertion kept |
| `collapse_folds_it_to_the_armed_tool_and_stays_folded` | "all six back" (`== 6`) | `== len(TOOL_ORDER)` | same |
| `roving_focus_…` | End / ← → `tool-date`, ↑ → `tool-signature` | End / ← → the last tool, ↑ → the one before it | same order rule, one more tool |
| `one_letter_shortcuts_…` | V T D K X S | + `e` arms Edit text | more coverage |

### Moved in W1 (none weakened)

| Check | Old | New | Why |
|---|---|---|---|
| `test_editor.py` `editor_opens_as_its_own_screen` | `[data-testid="document-editor"]` count 1 | `[data-testid="workspace"]` count 1 **and** `data-panel="edit"` **and** `document-editor` count 0 | the editor is the workspace now |
| `a_mixed_list_survives_a_reload_and_travels_whole`, `finalize_sends_the_annotations_and_the_saved_layout`, `finalize_without_annotations_leaves_the_key_out`, `an_older_server_is_explained_rather_than_blamed` | click `editor-finalize` straight away | `apply_dates_to_all(page)` first; `finalize_sends_…` additionally asserts `dates` = `date_1`, `date_2` | one finalize, gated the signing way (plan §7-9); every other assertion unchanged |
| `the_editor_is_one_link_away_from_the_three_screens` | `open-editor-link` on review + sign, click → `document-editor` | renamed `the_editor_is_one_workspace_behind_three_addresses`: dashboard link unchanged **and now clicked through** to `workspace[data-panel=edit]`; `/review` and `/sign` are the workspace with `editor-toolbar`, `open-editor-link` count 0, `.sidebar__summary` and `layout-panel` still there | the link is gone because the editor is already on both |

### Attributes on the page overlay

| Hook | On | Meaning |
|---|---|---|
| `data-field-id` | slot boxes, placed signatures, date `<text>` | template field id |
| `data-slot-kind` | slot boxes | `signature` \| `date` |
| `data-left-pt` | slot boxes, placed signatures | left edge in PDF points (`bbox.x + dx`), 2 decimals |
| `data-bottom-pt` | slot boxes, placed signatures | bottom edge in PDF points; box = `line − offset`, image = `line − 1 − offset` |
| `data-x-pt` | `.placed-dates text` | date x in PDF points (`bbox.x + dx`) |
| `data-baseline-pt` | `.placed-dates text` | date baseline in PDF points (`line − 1 − offset`) |
| `data-signature-id` | placed signatures | signature row id |
| `data-override-id` | override badge items | the raw `review_override_fields` entry |

### Elements and classes

### The viewer itself (`test_viewer.py`)

| Hook | On | Meaning |
|---|---|---|
| `data-page-number` | `.pdf-page`, `.pdf-thumb` | which page this box is; every page of the document is in the DOM from the start |
| `data-rendered` | `.pdf-page`, `.pdf-thumb` | set when that canvas has been painted, removed when the bitmap is handed back. **A flag only** — the checks still read the pixels |
| `.pdf-page--current` | one `.pdf-page` | the page the toolbar is counting (derived from the scroll position) |
| `canvas.pdf-canvas` / `canvas.pdf-thumb__canvas` | inside a page / a thumbnail | `width === 0` means the bitmap was released; `> 0` plus dark pixels means it is really drawn |
| `.pdf-thumbs__list` | the strip | its own scroll box **and** the `IntersectionObserver` root; `position: relative`, because the auto-scroll measures `offsetTop` against it |

Ceilings the checks mirror (move one, move the other): `MAX_RENDERED_PAGES = 6`
in `hooks/usePdfViewer.ts`, `MAX_DRAWN = 14` in `components/PdfThumbnails.tsx`.

### Find in document (`test_search.py`)

| Hook | On | Meaning |
|---|---|---|
| `.pdf-search-layer` | one per `.pdf-page`, **before** `.pdf-overlay` | the marks' own layer. Its position in the child list is the contract: a highlight must paint *under* the slot boxes and the placed signatures |
| `.pdf-search-hit` | inside that layer | one rectangle of one match. `pointer-events: none` — `document.elementFromPoint` over a mark must return whatever is behind it |
| `.pdf-search-hit--active` | exactly one of them | the match the reader is on. Its colour is checked in **pixels**, not by class name |
| `data-search-index` | each mark | index into the full result list, so a mark knows which match it is |

The counter (`pdf-search-count`) is the only thing that says a search is over:
it is absent for a blank query, reads `Searching… 12 / 66` while the document
is being read for the first time, `0` when nothing matched, and `3 / 17`
(`+` appended when `MAX_HITS = 500` in `hooks/usePdfSearch.ts` cut it short)
otherwise. `_harness.pixel_bands` distinguishes plain marks from the current
one by colour band, so "which one is current" is a fact about pixels.

### Rotate to read (`test_rotation.py`)

| Hook | On | Meaning |
|---|---|---|
| `data-rotation` | `.pdf-page` | the reader's quarter turn in degrees (`90` / `180` / `270`); **absent** at 0 |
| `.pdf-page--rotated` | that page | same thing as a class, and where the CSS hangs |
| `.pdf-overlay__shield` | last child of a turned page's `.pdf-overlay` | what makes a turned page's marks inert. It is *above* every mark, so `elementFromPoint` over a slot returns the shield and no drag can start |

Rotation is **per page** and **view only**: the file is not touched, no stored
coordinate moves, and the toolbar button turns the page the counter is on.
`.pdf-page--interactive` is dropped while a page is turned, and the notice
below is shown. A turned page's box is the *turned* size, which is why
`slot_points()` in that suite reads the scale as `rect.width / 792` instead of
the `rect.width / 612` every other suite uses — see the geometry contract at
the top of `styles/viewer.css`.

### Resize a signature (D7)

Not covered by a suite in this directory yet; listed because the same rule
applies — these hooks are what a check would bind to, so keep them.

| Hook | On | Meaning |
|---|---|---|
| `.slot-resize` | the corner grip, a **sibling** of the mark inside `.pdf-overlay` | drawn on the mark's TOP-RIGHT corner. The bottom-left corner and the middle of the slot box must stay free: that corner is what `data-left-pt` / `data-bottom-pt` anchor on, and the centre is where `_harness.py` presses to start a drag. `.slot-resize--own` once the slot has a size of its own, `.is-resizing` during the gesture |
| `data-slot-resize` | that grip | the field id it resizes |
| `data-scale` | that grip | the size it prints at right now, 2 decimals (live while dragging) |
| `.slot-resize-ghost` | one per page, while an **unsigned** signature slot is being resized | what the signature will fill; carries `data-field-id`. A signed slot needs none — its own image is the live preview |

| Hook | Used for |
|---|---|
| `.pdf-page` | the render scale is read as `rect.width / 612` |
| `img.placed-signature` / `div.placed-signature` | the signature as it will print (`<div>` while the image loads or fails) |
| `.placed-dates` (SVG) with `<text>` | dates; `x`, `y` and `font-size` must stay **attributes** (they are read with `getAttribute`), already multiplied by the render scale |
| `.slot[data-field-id]` | the draggable slot box |
| `.sidebar` | review issue list (FMCSA group and its `issue` text) |
| `.review__decision` | the reviewer Decision panel, incl. its `textarea` |
| `.modal__panel`, `.modal__extra`, `.modal input[type="file"]` | signature pad / upload pad |
| `.alert--error` inside `.review__document` | the workspace's errors (signing, review, finalize — `editor-error`) |

### `data-testid`

`layout-panel`, `offset-all-signatures(-value)`, `offset-all-dates(-value)`,
`offset-slot-<fieldId>(-value)`, `offset-slot-x-<fieldId>(-value)`,
`slot-offset-row-<fieldId>`, `clamp-note`, `override-eligible`,
`override-missing`, `override-inconsistencies`, `override-blocker`,
`override-badge`, `override-items`, `notes-hint`, `notes-example`,
`blocked-hint`, `template-match-warning`, `template-match-badge`,
`prefilled-warning`, `pdf-thumbs`, `pdf-thumbs-toggle`, `pdf-thumb-<pageNumber>`,
`pdf-search-input`, `pdf-search-count`, `pdf-search-prev`, `pdf-search-next`,
`pdf-search-clear`, `pdf-rotate`, `pdf-rotation-value`, `pdf-rotation-note`,
`pdf-rotation-shield-<pageNumber>`, `slot-scale-<fieldId>`,
`slot-scale-<fieldId>-value`, `slot-resize-value-<fieldId>`, `own-size-note`,
`shift-group`, `shift-signatures`, `shift-dates`, `shift-everything`.

The last four are the "Move together" block of `layout-panel`: one ◀ ▲ ▼ ▶ pad
per group. A press adds the same step to every slot's **own**
`slot_offsets_x_pt` / `slot_offsets_pt` entry (±200 pt, each slot clamped to
its own page edge); the global ±20 pt `signature_offset_pt` / `date_offset_pt`
and the finalize body's keys are untouched. A direction is disabled once no
slot of the group can move that way. Buttons are labelled
`Signatures together: move up`, `Dates together: move left`,
`Everything together: move right`, …

The last four are D7 (per-signature size): `slot-scale-<fieldId>` is the
− / value / + / Auto stepper on that slot's row in `layout-panel` and
`slot-scale-<fieldId>-value` its percentage (`… auto` appended while the slot
still follows the global slider); `slot-resize-value-<fieldId>` is the same
percentage on the corner grip; `own-size-note` is the line under the
`Signature size` slider, shown **only** while at least one slot has a size of
its own (`N signature(s) … ignore this slider.`).

### `aria-label`

* Steppers end in `move up` / `move down` / `move left` / `move right`
  (prefixed by the row's label, e.g. `All signatures: move up`,
  `Carrier signature (signature): move up`).
* Layout controls: `Signature size`, `Date font`, `Date size` — on the
  `<input>` / `<select>` itself.
* Thumbnails: each one is `Page <n>`, and the current one also carries
  `aria-current="page"`. The strip's toggle is `Show page thumbnails` /
  `Hide page thumbnails` with `aria-expanded`.
* Search: `Find in document` on the `<input>`, then `Previous match`,
  `Next match`, `Clear search` on its three buttons. **Enter** is next,
  **Shift+Enter** is previous, **Escape** clears — all three on the field.
* Rotation: `Rotate page clockwise` on the button (the label does **not**
  carry the page number; the `title` does).
* Per-signature size (D7), all prefixed by the row's / slot's own label:
  `<label> (signature): smaller`, `<label> (signature): larger` and
  `<label> (signature): use the global size` on the three buttons of the
  `layout-panel` stepper (the `(signature)` suffix is the row's *kind*, the
  same shape the move steppers use); `<label>: resize signature` on the corner
  grip; `<label>: use the global signature size` on the mini panel's
  **Auto size** button, which appears only once that slot has a size of its
  own.

### Accessible names clicked by the checks

`Apply to all`, `Generate final contract`, `Reset` (inside `layout-panel`),
`Choose signature`, `Upload an image instead`, `Approve anyway (override)`,
`Approve for signing`.

### `localStorage` keys

| Key | Written by | Read by the checks |
|---|---|---|
| `csa.token` | `api/client.ts` | injected to fake the session |
| `swm.layout.<contractId>` | `hooks/useContractLayout.ts` | the whole persistence group. Carries an **optional** `slot_scales` map (D7): it is written only while at least one signature has a size of its own, so a layout nobody resized is stored in exactly the pre-v3 shape — do not assert the key is present |
| `swm.signMode` | sign page | forced to `bulk` |
| `swm.autoFill` | sign page | forced to `false`, to keep the first-load fill out of the way |
| `swm.dates.<contractId>` | `hooks/useDateValues.ts` | the sign page's per-slot dates (`{field_id: "yyyy-mm-dd"}`); anything else stored there is dropped on load, and nothing is written until a date is chosen |
| `swm.thumbs` | `components/PdfViewer.tsx` | the thumbnail strip's open/folded state (`'true'` / `'false'`); absent means "never chosen", and the default is then open above 1024 px wide |
| `swm.panel.sections` | `workspace/PanelSections.tsx` | JSON list of the sections the user opened/closed by hand; written only on a toggle, unknown ids dropped. The URL's own sections are always opened on top of it |
| `swm.annotations.<contractId>` | `hooks/useAnnotations.ts` | the editor's annotations (`test_editor.py` reads and seeds it); up to 500 are read back; an entry's optional `color` is kept only if it is a palette name; `bold` / `italic` only if exactly `true`; `group_id` only if it matches the id pattern; a `whiteout` needs `w_pt`, `h_pt` ≥ 1; an unknown `font` falls back to Helvetica. A markup (W3) needs ≥ 1 box of `w_pt`, `h_pt` ≥ 1 (≤ 50 kept); no / non-palette `color` → its kind's default; its `x_pt` / `y_pt` are always re-derived from the boxes |
| `swm.toolbar` | `workspace/toolbar/useFloatingPanel.ts` | `{mode, x, y, orientation, collapsed, edge}`; written only once the user moves / folds the panel; anything unreadable → the docked default, field by field |
| `swm.toolProps` | `workspace/state/useToolState.ts` | font, bold, italic, the two sizes and one colour per tool for the NEXT annotation (not the date) |
| `swm.initials.<contractId>` | `hooks/useInitials.ts` | JSON list of the free-signature ids this browser placed as initials (W5); rubbish dropped. The 60 × 24 pt box rule labels initials without it |
| `swm.whiteout.noticed` | `workspace/tools/drawing.tsx` | `'1'` once the first whiteout has shown its "only hides" notice (W5) |


F1b: `swm.draft.<cid>` (the edit session's local copy: `{v, base_version_no, revision, ops, markup, changed_at, acked}`), `swm.annotations.<cid>.migrated` (the browser's annotations after the move to the server — renamed, never deleted); `sessionStorage["swm.editSession.<cid>"]` (this tab's `client_session_id`).
### Exact wording asserted

Changing any of these strings breaks a check on purpose — they are what the
user reads:

* `N slot(s) was/were moved to stay on the page.`
* `This contract appears to be the template file itself (page similarity N%). Check that the correct file was uploaded.`
* `Same file as template? (N%)`
* `Pre-filled template` / `Already contains a/b signatures and c/d dates`
* `Background is removed automatically.` (static text since `SignatureUploadPad.tsx:69` — the old *Remove background* checkbox is gone)
* `Override is not available: FMCSA violation must be corrected first: <issue> (page N)`
* `Approval is blocked by 1 required field and 2 inconsistencies.`
* `N/10 characters · at least 10 required to approve with override` / `· reason recorded in the audit trail`
* `You are approving a contract with … . This is recorded in the audit trail and the final PDF. Continue?`
* `Approved with override` / `1 required field and 2 inconsistencies skipped`
* `New issues appeared after approval — the contract must be reviewed again.` (em dash)
* `Rotated view — placement disabled; rotate back to place signatures` (em dash;
  shown by `PdfViewer` while a page is turned **and** the viewer is
  `interactive` — the signing screen)
* `Rotated view — marks are read-only; rotate back to interact` (em dash; the
  same notice on a read-only viewer — the review screen)
* `Searching… N / M` (horizontal ellipsis) — the search counter while the
  document is being read for the first time
* `This contract already has the maximum of 500 annotations.` (a placement or
  a selection on a full list)
* `Highlighting N matches would pass the maximum of 500 annotations (M left).
  Nothing was highlighted — narrow the search or delete some annotations.`
* `Every match is already highlighted.`

---

## Ground rules

1. These checks record **current** behaviour. A red result is a regression in
   the app *or* a stale expectation here. Investigate, say which of the two it
   is — do not "fix" the app to make a check pass without deciding that first.
2. They never write to the database, never call the real backend, never open a
   real contract, and never store a password or a token value.
3. They must not be moved into `frontend/src`: this directory is outside the
   Vite build and adds nothing to the bundle.
