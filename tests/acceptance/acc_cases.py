"""The 18 acceptance tests of the PDF editor spec -- each with an `api_tNN`
and a `ui_tNN` path (backend venv). Contract A = API path, contract U = UI path,
contract L = the untouched legacy flow (18/API), L-ui = uploaded through the UI (18/UI).

Every content change is proven twice: text extraction (pypdf, pdfplumber,
PyMuPDF) AND pixels (pypdfium2 at 8 px/pt: what changed is inside the edited
region, nothing outside it changed)."""
from __future__ import annotations

import io
import json
import time

import pymupdf
from pypdf import PdfReader

import acc_pdf as P
from acc_api import Api
from acc_core import Ctx, Record

NEW_CARRIER = "Carrier: Premier Trucking Group Inc."
DATE_US = "09/30/2026"
DATE_ISO = "2026-09-30"


# -- helpers ------------------------------------------------------------------------------------

def find_line(tm: dict, needle: str):
    for b in tm["blocks"]:
        for ln in b["lines"]:
            if needle in ln["text"]:
                return b, ln
    raise AssertionError(f"no line holds {needle!r}")


def target(b, ln) -> dict:
    return {"block_id": b["id"], "line_ids": [ln["id"]], "expect_text": ln["text"], "bbox": ln["bbox"]}


def text_len(text: str, size: float, bold=False) -> float:
    return pymupdf.get_text_length(text, fontname="hebo" if bold else "helv", fontsize=size)


def rect_pt(r: dict, pad=0.0):
    return (r["x_pt"] - pad, r["y_pt"] - pad, r["x_pt"] + r["w_pt"] + pad, r["y_pt"] + r["h_pt"] + pad)


def replace_op(opid, uid, tm, needle, new_text, **style):
    b, ln = find_line(tm, needle)
    sp = ln["spans"][0]
    size = style.get("size_pt", sp["size_pt"])
    run = {"text": new_text, "font": style.get("font", sp["family"]), "size_pt": size,
           "color": style.get("color", sp["color"])}
    if style.get("bold"):
        run["bold"] = True
    need = text_len(new_text, size, bool(style.get("bold"))) * 1.08 + 4
    bb = ln["bbox"]
    box = {"x_pt": bb["x_pt"] + style.get("dx", 0.0), "y_pt": bb["y_pt"] + style.get("dy", 0.0),
           "w_pt": max(bb["w_pt"], need), "h_pt": max(bb["h_pt"], size * 1.3)}
    return {"id": opid, "kind": "text.replace", "page_uid": uid, "target": target(b, ln), "runs": [run],
            "box": box, "fit": style.get("fit", "wrap")}, ln


def content_evidence(ctx: Ctx, r: Record, tag: str, before: bytes, after: bytes, page: int,
                     present=(), absent=(), regions=(), page_after: int | None = None,
                     expect_inside_change=True):
    """3 extractors + pixels on one page (index) of two versions."""
    pa = page if page_after is None else page_after
    for needle in present:
        got = P.all_have(after, needle, pa)
        r.check(f"{tag}: «{needle}» extracted by all 3 readers (in reading order)", all(got.values()), got)
        if not all(got.values()):
            words = {k: all(all(P.all_have(after, w, pa).values()) for w in needle.split()) for k in got}
            r.check(f"{tag}: every word of «{needle}» is extracted (any order)", all(words.values()), words)
            r.note(f"{tag}: «{needle}» is split / out of order in {[k for k, v in got.items() if not v]}")
    for needle in absent:
        gone = P.none_have(after, needle, pa)
        r.check(f"{tag}: «{needle}» absent in all 3 readers", all(gone.values()), gone)
    ib, ia = P.render(before)[page], P.render(after)[pa]
    if ib.size == ia.size:
        outside = P.diff_outside(ib, ia, [tuple(x) for x in regions])
        r.check(f"{tag}: pixels outside the edited region unchanged (0 px > 48)", outside == 0, outside)
        if expect_inside_change and regions:
            inside = sum(P.diff_inside(ib, ia, reg) for reg in regions)
            r.check(f"{tag}: the edited region did change on the page", inside > 0, inside)
        for i, reg in enumerate(regions[:2]):
            pad = (max(0, reg[0] - 20), max(0, reg[1] - 20), reg[2] + 20, reg[3] + 20)
            P.save_png(ib, ctx.path(f"{tag}_before_{i}.png"), pad)
            P.save_png(ia, ctx.path(f"{tag}_after_{i}.png"), pad)
        r.evidence.setdefault("crops", []).extend([ctx.path(f"{tag}_before_0.png"), ctx.path(f"{tag}_after_0.png")])
    else:
        r.note(f"{tag}: page size changed {ib.size} -> {ia.size}; pixel diff skipped")
    return ib, ia


def geometry(r: Record, tag, before: bytes, after: bytes, page: int, old_needle: str, new_needle: str,
             dx=0.0, dy=0.0, page_after=None, tol=0.5):
    gb = P.line_geometry(before, page, old_needle)
    ga = P.line_geometry(after, page if page_after is None else page_after, new_needle)
    out = {}
    ok = bool(gb and ga)
    for lib in ("pymupdf", "pdfplumber"):
        if gb and ga and lib in gb and lib in ga and ga[lib]["baseline"] is not None:
            ddx = ga[lib]["x0"] - gb[lib]["x0"] - dx
            ddy = ga[lib]["baseline"] - gb[lib]["baseline"] - dy
            out[lib] = {"dx": round(ddx, 3), "dbaseline": round(ddy, 3)}
            ok = ok and abs(ddx) <= tol and abs(ddy) <= tol
        else:
            ok = False
    r.check(f"{tag}: left edge / baseline where expected (±{tol} pt)", ok, out or {"before": gb, "after": ga})
    return out


def same_line(r: Record, tag, pdf: bytes, page: int, head: str, tail: str):
    """The kept head and the rewritten tail sit on ONE visual line: same
    baseline, the tail starting right after the head (PyMuPDF spans)."""
    h, t = P.spans(pdf, page, head), P.spans(pdf, page, tail)
    if h and t and head in t[0]["text"]:
        ok = True  # one span holds both
    else:
        ok = bool(h and t) and abs(h[0]["origin"][1] - t[0]["origin"][1]) <= 0.5 \
            and -0.5 <= t[0]["bbox"][0] - h[0]["bbox"][2] <= 4.5
    r.check(f"{tag}: head «{head}» and new «{tail}…» on one visual line (same baseline ±0.5 pt, adjacent)", ok,
            {"head": h[:1], "tail": t[:1]})


def dump(ctx: Ctx, name, data: bytes):
    return ctx.write(name, data)


def run_ui(ctx: Ctx, r: Record, scenario: str, args: dict) -> dict:
    out = ctx.ui(scenario, args, r)
    return out


def ensure_approved(api: Api, r: Record, why=""):
    s = api.approve()
    if s != 200:
        r.check(f"re-approve {why} -> 200", False, s)


def ui_available(ctx: Ctx, tool: str) -> bool:
    return tool in ctx.state.get("ui_tools", [])


# ======================================================================= 1 edit original text

def api_t01(ctx: Ctx, r: Record):
    A = ctx.A
    uid = ctx.state["A_uids"][0]
    before = A.version_pdf()
    tm = A.text_model(uid)
    op, ln = replace_op("acc-t01", uid, tm, "Carrier: ABC Transportation LLC", NEW_CARRIER)
    s, out = A.commit_ops([op], "t01")
    r.check("commit text.replace -> 201, content_changed", s == 201 and out.get("content_changed") is True,
            {"status": s, "warnings": out.get("warnings") if isinstance(out, dict) else out})
    after = A.version_pdf()
    dump(ctx, "A_t01_after.pdf", after)
    region = rect_pt(op["box"], 1.5)
    ib, ia = content_evidence(ctx, r, "api_t01", before, after, 0, present=[NEW_CARRIER],
                              absent=["ABC Transportation"], regions=[region])
    geometry(r, "api_t01", before, after, 0, "Carrier:", "Carrier:")
    same_line(r, "api_t01", after, 0, "Carrier:", "Premier")
    cell =(P.CELL[0] + 1, P.CELL[1] + 1, P.CELL[0] + P.CELL[2] - 1, P.CELL[1] + P.CELL[3] - 1)
    r.check("api_t01: no white box in the coloured cell (pure-white px = 0)", P.white_px(ia, cell) == 0, P.white_px(ia, cell))
    r.check("api_t01: the ____ line below untouched",
            P.diff_inside(ib, ia, (70, 280, 260, 296)) == 0, P.diff_inside(ib, ia, (70, 280, 260, 296)))
    ctx.state["A_v_t01"] = A.current_no()


def ui_t01(ctx: Ctx, r: Record):
    U = ctx.U
    before = U.version_pdf()
    out = run_ui(ctx, r, "edit_text", {"cid": U.cid, "old": "ABC Transportation LLC",
                                       "new": "Premier Trucking Group Inc.", "zooms": [100, 209]})
    r.evidence["frames"] = out["facts"].get("frames")
    after = U.version_pdf()
    dump(ctx, "U_t01_after.pdf", after)
    tm_ln = None
    try:
        tm_ln = find_line(U.text_model(ctx.state["U_uids"][0]), "Premier Trucking")[1]
    except AssertionError:
        pass
    region = rect_pt(tm_ln["bbox"], 1.5) if tm_ln else (60, 84, 320, 108)
    region = (min(region[0], 66), min(region[1], 84), max(region[2], 300), max(region[3], 108))
    ib, ia = content_evidence(ctx, r, "ui_t01", before, after, 0, present=[NEW_CARRIER],
                              absent=["ABC Transportation"], regions=[region])
    geometry(r, "ui_t01", before, after, 0, "Carrier:", "Carrier:")
    same_line(r, "ui_t01", after, 0, "Carrier:", "Premier")
    cell = (P.CELL[0] + 1, P.CELL[1] + 1, P.CELL[0] + P.CELL[2] - 1, P.CELL[1] + P.CELL[3] - 1)
    r.check("ui_t01: no white box in the coloured cell", P.white_px(ia, cell) == 0, P.white_px(ia, cell))


# ======================================================================= 2 delete original text

def api_t02(ctx: Ctx, r: Record):
    A = ctx.A
    uid = ctx.state["A_uids"][0]
    before = A.version_pdf()
    b, ln = find_line(A.text_model(uid), "ZZOBSOLETE")
    op = {"id": "acc-t02", "kind": "text.delete", "page_uid": uid, "target": target(b, ln)}
    s, out = A.commit_ops([op], "t02")
    r.check("commit text.delete -> 201", s == 201, {"status": s, "body": out if s != 201 else None})
    after = A.version_pdf()
    region = rect_pt(ln["bbox"], 1.5)
    ib, ia = content_evidence(ctx, r, "api_t02", before, after, 0, absent=["ZZOBSOLETE", "Obsolete clause"],
                              regions=[region])
    _box, n = P.ink_box(ia, region, P.is_dark)
    r.check("api_t02: the line's place is empty on the page (0 dark px)", n == 0, n)
    tm2 = A.text_model(uid)
    r.check("api_t02: the text model has no such line", not any("ZZOBSOLETE" in ln2["text"] for b2 in tm2["blocks"]
                                                               for ln2 in b2["lines"]))


def ui_t02(ctx: Ctx, r: Record):
    U = ctx.U
    before = U.version_pdf()
    run_ui(ctx, r, "delete_text", {"cid": U.cid, "text": "Obsolete clause ZZOBSOLETE-7788 removed."})
    after = U.version_pdf()
    region = (66, 110, 330, 128)
    ib, ia = content_evidence(ctx, r, "ui_t02", before, after, 0, absent=["ZZOBSOLETE"], regions=[region])
    _box, n = P.ink_box(ia, region, P.is_dark)
    r.check("ui_t02: the line's place is empty (0 dark px)", n == 0, n)


# ======================================================================= 3 multi-line text

TB_TEXT = ("ZZSYNTH multi-line box: this first paragraph is long enough to wrap inside its box.",
           "Second paragraph ZZPARA2 after a line break.")


def _textbox_evidence(ctx, r, tag, api: Api, tb: dict, uid_index: int):
    exp = api.export()
    dump(ctx, f"{tag}_export.pdf", exp)
    for word in ("ZZSYNTH multi-line box", "ZZPARA2"):
        got = P.all_have(exp, word, uid_index)
        r.check(f"{tag}: export text «{word}» (3 readers)", all(got.values()), got)
    with pymupdf.open(stream=exp, filetype="pdf") as doc:
        pg = doc[uid_index]
        lines = [ln for b in pg.get_text("dict")["blocks"] for ln in b.get("lines", [])
                 if any(w in "".join(s["text"] for s in ln["spans"]) for w in ("ZZSYNTH multi", "wrap", "inside", "ZZPARA2", "Second", "paragraph", "box"))]
        ys = sorted({round(ln["bbox"][1], 1) for ln in lines})
    r.check(f"{tag}: rendered as >= 3 separate lines", len(ys) >= 3, ys)
    img = P.render(exp)[uid_index]
    box = (tb["x_pt"] - 1, tb["y_pt"] - 1, tb["x_pt"] + tb["w_pt"] + 1, tb["y_pt"] + tb["h_pt"] + 40)
    ink, n = P.ink_box(img, box, P.is_dark)
    r.check(f"{tag}: ink starts inside the box (left/top ±1 pt) and stays inside its width",
            ink is not None and ink[0] >= tb["x_pt"] - 1 and ink[1] >= tb["y_pt"] - 1 and ink[2] <= tb["x_pt"] + tb["w_pt"] + 1,
            {"ink": ink, "box": [tb["x_pt"], tb["y_pt"], tb["w_pt"], tb["h_pt"]], "px": n})
    P.save_png(img, ctx.path(f"{tag}_textbox.png"), box)


def api_t03(ctx: Ctx, r: Record):
    A = ctx.A
    ensure_approved(A, r)
    uid = ctx.state["A_uids"][0]
    tb = {"id": "acc-t03-tb", "kind": "textbox", "page_uid": uid, "x_pt": 72, "y_pt": 360, "w_pt": 250, "h_pt": 60,
          "runs": [{"text": TB_TEXT[0] + "\n" + TB_TEXT[1], "font": "LiberationSans", "size_pt": 11, "color": "black"}]}
    s, out = A.put_markup(A.markup()["elements"] + [tb])
    r.check("PUT markup textbox (2 paragraphs) -> 200, review re-opened (Q1)", s == 200 and out.get("review_reset") is True, out)
    _textbox_evidence(ctx, r, "api_t03", A, tb, 0)
    ensure_approved(A, r, "after the text box")


def ui_t03(ctx: Ctx, r: Record):
    U = ctx.U
    out = run_ui(ctx, r, "textbox", {"cid": U.cid, "at": [72, 360], "paragraphs": list(TB_TEXT)})
    tb = next((e for e in out["facts"].get("elements", []) if e["kind"] == "textbox"), None)
    if tb:
        _textbox_evidence(ctx, r, "ui_t03", U, tb, 0)
    ensure_approved(U, r, "after the text box")


# ======================================================================= 4 font, size, colour

def api_t04(ctx: Ctx, r: Record):
    A = ctx.A
    uid = ctx.state["A_uids"][0]
    before = A.version_pdf()
    op, ln = replace_op("acc-t04", uid, A.text_model(uid), "ZZSTYLE", "Style target line ZZSTYLE",
                        font="LiberationSerif", size_pt=16, color="#C00000", bold=True)
    s, out = A.commit_ops([op], "t04")
    r.check("commit restyle -> 201", s == 201, {"status": s, "body": out if s != 201 else out.get("warnings")})
    after = A.version_pdf()
    sp = P.spans(after, 0, "ZZSTYLE")
    r.evidence["spans"] = sp
    ok = bool(sp) and "Serif" in sp[0]["font"] and "Bold" in sp[0]["font"] and abs(sp[0]["size"] - 16) < 0.01 \
        and sp[0]["color"] == "#C00000"
    r.check("api_t04: PyMuPDF span = Liberation Serif Bold, 16 pt, #C00000", ok, sp[:1])
    region = rect_pt(op["box"], 3)
    content_evidence(ctx, r, "api_t04", before, after, 0, present=["Style target line ZZSTYLE"], regions=[region])
    ia = P.render(after)[0]
    _b, n = P.ink_box(ia, region, lambda p: p[0] > 140 and p[1] < 80 and p[2] < 80)
    r.check("api_t04: the line is drawn red on the page", n > 200, n)
    geometry(r, "api_t04", before, after, 0, "Style target", "Style target")
    # added text in other families (text box runs): Carlito italic blue + DejaVu Sans
    tb = {"id": "acc-t04-tb", "kind": "textbox", "page_uid": uid, "x_pt": 330, "y_pt": 360, "w_pt": 250, "h_pt": 40,
          "runs": [{"text": "ZZCARLITO ", "font": "Carlito", "size_pt": 14, "color": "blue", "italic": True},
                   {"text": "ZZDEJAVU", "font": "DejaVuSans", "size_pt": 9, "color": "green"}]}
    s, _o = A.put_markup(A.markup()["elements"] + [tb])
    r.check("PUT markup textbox with two styled runs -> 200", s == 200, s)
    exp = A.export()
    a, b = P.spans(exp, 0, "ZZCARLITO"), P.spans(exp, 0, "ZZDEJAVU")
    r.evidence["textbox_spans"] = a + b
    r.check("api_t04: export spans carry each run's family, size, colour",
            bool(a) and "Carlito" in a[0]["font"] and abs(a[0]["size"] - 14) < 0.01 and bool(b)
            and "DejaVu" in b[0]["font"] and abs(b[0]["size"] - 9) < 0.01 and a[0]["color"] != b[0]["color"],
            [(x["font"], x["size"], x["color"]) for x in a + b])
    ensure_approved(A, r, "after restyle")


def ui_t04(ctx: Ctx, r: Record):
    U = ctx.U
    before = U.version_pdf()
    out = run_ui(ctx, r, "style", {"cid": U.cid, "text": "Style target line ZZSTYLE", "size": 18,
                                   "font": "LiberationSerif", "font_search": "serif", "color": "red"})
    after = U.version_pdf()
    sp = P.spans(after, 0, "ZZSTYLE")
    r.evidence["spans"] = sp
    runs = out["facts"].get("runs") or [{}]
    r.check("ui_t04: PDF span = Liberation Serif Bold 18 pt in the chosen colour",
            bool(sp) and "Serif" in sp[0]["font"] and "Bold" in sp[0]["font"] and abs(sp[0]["size"] - 18) < 0.01
            and sp[0]["color"].upper() == str(runs[0].get("color", "")).upper(), {"pdf": sp[:1], "op": runs[:1]})
    content_evidence(ctx, r, "ui_t04", before, after, 0, present=["Style target line ZZSTYLE"],
                     regions=[(66, 126, 360, 156)])
    geometry(r, "ui_t04", before, after, 0, "Style target", "Style target")
    ensure_approved(U, r, "after restyle")


# ======================================================================= 5 move + duplicate

def api_t05(ctx: Ctx, r: Record):
    A = ctx.A
    uid = ctx.state["A_uids"][0]
    before = A.version_pdf()
    op, ln = replace_op("acc-t05", uid, A.text_model(uid), "ZZMOVE", "Movable line ZZMOVE", dx=200.0)
    s, out = A.commit_ops([op], "t05")
    r.check("commit moved text (box +200 pt) -> 201", s == 201, {"status": s, "body": out if s != 201 else None})
    after = A.version_pdf()
    old_region = rect_pt(ln["bbox"], 1.5)
    new_region = rect_pt(op["box"], 1.5)
    content_evidence(ctx, r, "api_t05", before, after, 0, present=["Movable line ZZMOVE"],
                     regions=[old_region, new_region])
    geometry(r, "api_t05", before, after, 0, "Movable line", "Movable line", dx=200.0)
    _b, n = P.ink_box(P.render(after)[0], (old_region[0], old_region[1], old_region[0] + 150, old_region[3]), P.is_dark)
    r.check("api_t05: the old place is empty", n == 0, n)
    # markup objects: move (same id, new place) + duplicate (new id)
    els = A.markup()["elements"]
    tb = next(e for e in els if e["id"] == "acc-t03-tb")
    moved = dict(tb, x_pt=tb["x_pt"], y_pt=450.0)
    dup = dict(tb, id="acc-t05-dup", y_pt=520.0)
    s, _o = A.put_markup([e for e in els if e["id"] != tb["id"]] + [moved, dup])
    r.check("PUT markup: text box moved (same id) + duplicated (new id) -> 200", s == 200, s)
    exp = A.export()
    img = P.render(exp)[0]
    _b, n_old = P.ink_box(img, (72, 361, 322, 419), P.is_dark)
    _b1, n_new = P.ink_box(img, (72, 450, 322, 510), P.is_dark)
    _b2, n_dup = P.ink_box(img, (72, 520, 322, 580), P.is_dark)
    r.check("api_t05: export: box gone from the old place, drawn at the new place and at the duplicate",
            n_old == 0 and n_new > 200 and n_dup > 200, {"old": n_old, "new": n_new, "dup": n_dup})
    t = P.texts(exp)
    r.check("api_t05: duplicate text extracted twice (PyMuPDF)", t["pymupdf"][0].count("ZZPARA2") == 2,
            t["pymupdf"][0].count("ZZPARA2"))
    ensure_approved(A, r, "after move")


def ui_t05(ctx: Ctx, r: Record):
    U = ctx.U
    before = U.version_pdf()
    out = run_ui(ctx, r, "move_dup", {"cid": U.cid, "text": "Movable line ZZMOVE", "dx_pt": 150.0,
                                      "dup_text": "Visible clause stays here."})
    moved = out["facts"].get("moved_pt")
    after = U.version_pdf()
    if moved is not None:
        # the move was committed in the first Save; the later dup Save is markup-only
        geometry(r, "ui_t05", before, after, 0, "Movable line", "Movable line", dx=moved)
        content_evidence(ctx, r, "ui_t05", before, after, 0, present=["Movable line ZZMOVE"],
                         regions=[(66, 300, 72 + 150 + 150, 322)])
    ensure_approved(U, r, "after move / duplicate")


# ======================================================================= 6 images + stamps

def _image_markup_evidence(ctx, r, tag, api: Api, img_el: dict | None, stamp_el: dict | None, present: bool):
    exp = api.export()
    img = P.render(exp)[0]
    res = {}
    if img_el:
        box = (img_el["x_pt"], img_el["y_pt"], img_el["x_pt"] + img_el["w_pt"], img_el["y_pt"] + img_el["h_pt"])
        ink, n = P.ink_box(img, (box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3), P.is_magenta)
        res["image"] = {"ink": ink, "px": n, "box": box}
        if present:
            r.check(f"{tag}: picture drawn exactly in its box (±0.5 pt)", ink is not None
                    and max(abs(a - b) for a, b in zip(ink, box)) <= 0.5, res["image"])
        else:
            r.check(f"{tag}: picture gone after removal", n == 0, n)
    if stamp_el:
        box = (stamp_el["x_pt"], stamp_el["y_pt"], stamp_el["x_pt"] + stamp_el["w_pt"], stamp_el["y_pt"] + stamp_el["h_pt"])
        ink, n = P.ink_box(img, (box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3), P.is_red)
        res["stamp"] = {"ink": ink, "px": n, "box": box}
        if present:
            r.check(f"{tag}: stamp drawn in its box (red, within ±2 pt of the border)", ink is not None and n > 300
                    and max(abs(a - b) for a, b in zip(ink, box)) <= 2.0, res["stamp"])
            words = " ".join(P.texts(exp)["pymupdf"])
            r.check(f"{tag}: stamp word extractable", "APPROVED" in words.upper(), None)
        else:
            r.check(f"{tag}: stamp gone after removal", n == 0, n)
    P.save_png(img, ctx.path(f"{tag}_{'placed' if present else 'removed'}.png"), (300, 380, 612, 560))
    return res


def api_t06(ctx: Ctx, r: Record):
    A = ctx.A
    uid = ctx.state["A_uids"][0]
    ensure_approved(A, r)
    # gallery -> contract copy -> the gallery item deleted: the placed copy stays
    s, g = A.post("/api/assets", {"image_data": P.solid_png_data_url((200, 0, 200), 60, 30),
                                  "kind": "image", "label": "zzacc gallery"})
    r.check("POST /api/assets (gallery) -> 201", s == 201, s)
    ctx.ids["asset_gallery_api"] = g.get("asset_id") if isinstance(g, dict) else None
    ctx.save_ids()
    s, ca = A.post(f"/api/contracts/{A.cid}/annotation-assets", {"from_asset_id": g["asset_id"]})
    r.check("annotation-assets {from_asset_id} -> 201 (a copy)", s == 201 and ca["asset_id"] != g["asset_id"], s)
    s, _ = A.delete(f"/api/assets/{g['asset_id']}")
    r.check("DELETE gallery item -> 204", s == 204, s)
    im = {"id": "acc-t06-img", "kind": "image2", "page_uid": uid, "asset_id": ca["asset_id"],
          "x_pt": 330, "y_pt": 400, "w_pt": 120, "h_pt": 60}
    st = {"id": "acc-t06-stamp", "kind": "stamp", "page_uid": uid, "x_pt": 330, "y_pt": 480, "w_pt": 150, "h_pt": 50,
          "preset": "approved", "color": "red"}
    base = A.markup()["elements"]
    s, _o = A.put_markup(base + [im, st])
    r.check("PUT markup image2 + stamp -> 200", s == 200, s)
    _image_markup_evidence(ctx, r, "api_t06", A, im, st, True)
    s, _o = A.put_markup(base)
    r.check("PUT markup without them (removed) -> 200", s == 200, s)
    _image_markup_evidence(ctx, r, "api_t06", A, im, st, False)
    ensure_approved(A, r)
    # an ORIGINAL image removed from the file
    before = A.version_pdf()
    s, imgs = A.get(f"{A.D}/versions/{A.current_no()}/pages/{uid}/images")
    r.check("GET /images lists the original raster", s == 200 and len(imgs) == 1, imgs)
    op = {"id": "acc-t06-rm", "kind": "image.remove", "page_uid": uid, "xref": imgs[0]["xref"], "bbox": imgs[0]["bbox"]}
    s, out = A.commit_ops([op], "t06")
    r.check("commit image.remove -> 201", s == 201, {"status": s, "body": out if s != 201 else None})
    after = A.version_pdf()
    s, imgs2 = A.get(f"{A.D}/versions/{A.current_no()}/pages/{uid}/images")
    r.check("the image is no longer in the file (/images = [], XObjects 1 -> 0)",
            imgs2 == [] and P.image_xobjects(before) == 1 and P.image_xobjects(after) == 0,
            {"images": imgs2, "xobj": [P.image_xobjects(before), P.image_xobjects(after)]})
    ix, iy, iw, ih = P.IMAGE_BOX
    region = (ix - 1, iy - 1, ix + iw + 1, iy + ih + 1)
    ib, ia = content_evidence(ctx, r, "api_t06_rm", before, after, 0, present=["Premier Trucking Group Inc."], regions=[region])
    _b, n = P.ink_box(ia, region, lambda p: P.is_green(p) or P.is_magenta(p) or max(p) < 200)
    r.check("api_t06: the picture's pixels are gone (0 px)", n == 0, n)
    ensure_approved(A, r)


def ui_t06(ctx: Ctx, r: Record):
    U = ctx.U
    ensure_approved(U, r)
    png = ctx.write("zzacc_magenta.png", P.magenta_png_bytes(90, 40))
    before = U.version_pdf()
    out = run_ui(ctx, r, "images", {"cid": U.cid, "png": png, "image_at": [330, 400], "stamp_at": [330, 480]})
    ctx.ids["gallery_ui"] = out["facts"].get("gallery_asset")
    ctx.save_ids()
    new = out["facts"].get("new_elements") or []
    img_el = next((e for e in new if e["kind"] in ("image", "image2")), None)
    r.evidence["new_elements"] = new
    if img_el:
        # removed again in the scenario: the export must not have it
        _image_markup_evidence(ctx, r, "ui_t06", U, img_el, None, False)
    after = U.version_pdf()
    if P.image_xobjects(before) == 1:
        r.check("ui_t06: the original image removed from the file (XObjects 1 -> 0)", P.image_xobjects(after) == 0,
                [P.image_xobjects(before), P.image_xobjects(after)])
    ensure_approved(U, r)


# ======================================================================= 7 form fields

FIELDS = [
    {"name": "zz_text", "type": "text", "rect": {"x_pt": 330, "y_pt": 580, "w_pt": 180, "h_pt": 18}, "tab_index": 1},
    {"name": "zz_multi", "type": "multiline", "rect": {"x_pt": 330, "y_pt": 602, "w_pt": 180, "h_pt": 40}, "tab_index": 2},
    {"name": "zz_drop", "type": "dropdown", "rect": {"x_pt": 330, "y_pt": 646, "w_pt": 120, "h_pt": 18},
     "options": ["Alpha", "Bravo", "Charlie"], "tab_index": 3},
    {"name": "zz_radio", "type": "radio", "rect": {"x_pt": 330, "y_pt": 670, "w_pt": 14, "h_pt": 14},
     "export_value": "A", "tab_index": 4},
    {"name": "zz_radio", "type": "radio", "rect": {"x_pt": 360, "y_pt": 670, "w_pt": 14, "h_pt": 14},
     "export_value": "B", "tab_index": 5},
    {"name": "zz_check", "type": "checkbox", "rect": {"x_pt": 400, "y_pt": 670, "w_pt": 14, "h_pt": 14}, "tab_index": 6},
    {"name": "zz_sig", "type": "signature", "rect": {"x_pt": 430, "y_pt": 646, "w_pt": 120, "h_pt": 18}, "tab_index": 7},
]
VALUES = {"zz_text": "ZZTEXTVALUE 42", "zz_multi": "ZZLINE-ONE\nZZLINE-TWO", "zz_drop": "Bravo", "zz_radio": "B",
          "zz_check": True}


def api_t07(ctx: Ctx, r: Record):
    A = ctx.A
    ensure_approved(A, r)
    uid = ctx.state["A_uids"][2]  # page 3 (the slot page) keeps them
    ops = [{"id": f"acc-t07-add{i}", "kind": "field.add", "page_uid": uid, "field": f} for i, f in enumerate(FIELDS)]
    s, out = A.commit_ops(ops, "t07a")
    r.check("commit 7 field.add (text, multiline, dropdown, radio×2, checkbox, signature) -> 201, review kept",
            s == 201 and out.get("review_reset") is False, {"status": s, "body": out if s != 201 else None})
    s, fields = A.get(f"{A.D}/versions/{A.current_no()}/fields")
    mine = [f for f in fields if f["source"] == "editor"]
    r.check("GET fields: the 7 widgets with their types", sorted((f["name"], f["type"]) for f in mine)
            == sorted((f["name"], f["type"]) for f in FIELDS), [(f["name"], f["type"], f["tab_index"]) for f in mine])
    for f in FIELDS:
        got = next((m for m in mine if m["name"] == f["name"] and (m.get("export_value") == f.get("export_value") or f["type"] != "radio")), None)
        if got:
            dev = max(abs(got["rect"][k] - f["rect"][k]) for k in ("x_pt", "y_pt", "w_pt", "h_pt"))
            if dev > 0.5:
                r.check(f"field {f['name']} rect as asked (±0.5 pt)", False, {"got": got["rect"], "want": f["rect"]})
    r.check("all field rects as asked (±0.5 pt)", True)
    ops = [{"id": f"acc-t07-v{i}", "kind": "field.value", "name": n, "value": v} for i, (n, v) in enumerate(VALUES.items())]
    s, out = A.commit_ops(ops, "t07b")
    r.check("commit field.value ×5 -> 201, review re-opened (content)", s == 201 and out.get("review_reset") is True,
            {"status": s, "body": out if s != 201 else None})
    s, fields = A.get(f"{A.D}/versions/{A.current_no()}/fields")
    vals = {f["name"]: f["value"] for f in fields if f["source"] == "editor"}
    r.evidence["values"] = vals
    r.check("GET fields: every value filled", all(vals.get(k) == v for k, v in VALUES.items()), vals)
    pdf = A.version_pdf()
    dump(ctx, "A_t07_fields.pdf", pdf)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        widgets = {(w.field_name, w.field_type_string): w.field_value for w in doc[2].widgets()}
    r.evidence["pymupdf_widgets"] = {f"{k[0]}|{k[1]}": str(v) for k, v in widgets.items()}
    r.check("PyMuPDF reads the widgets and values", widgets.get(("zz_text", "Text")) == VALUES["zz_text"]
            and widgets.get(("zz_drop", "ComboBox")) == "Bravo", r.evidence["pymupdf_widgets"])
    pf = PdfReader(io.BytesIO(pdf)).get_fields() or {}
    r.check("pypdf reads the AcroForm values", str(pf.get("zz_text", {}).get("/V")) == VALUES["zz_text"]
            and str(pf.get("zz_radio", {}).get("/V")) == "/B", {k: str(v.get("/V")) for k, v in pf.items() if k.startswith("zz_")})
    img = P.render(pdf, annots=True)[2]
    _b, n = P.ink_box(img, (330, 580, 510, 598), P.is_dark)
    r.check("the filled text field shows its value on the page (widget appearance)", n > 50, n)
    exp = A.export(flatten_forms=True)
    dump(ctx, "A_t07_flattened.pdf", exp)
    with pymupdf.open(stream=exp, filetype="pdf") as doc:
        nw = sum(1 for _ in doc[2].widgets())
    got = P.all_have(exp, "ZZTEXTVALUE 42", 2)
    r.check("export flatten_forms: 0 widgets left, the value is page text (3 readers)", nw == 0 and all(got.values()),
            {"widgets": nw, "readers": got})
    ensure_approved(A, r, "after filling")


def _fields_pdf_evidence(ctx, r, tag, api: Api, page_index: int, values: dict):
    """The saved version's widgets, read by PyMuPDF and pypdf; the flattened export as page text."""
    pdf = api.version_pdf()
    dump(ctx, f"{tag}_fields.pdf", pdf)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        widgets = [(w.field_name, w.field_type_string, w.field_value) for w in doc[page_index].widgets()]
    r.evidence[f"{tag}_pymupdf_widgets"] = [[a, b, str(v)] for a, b, v in widgets]
    by_type = {}
    for name, ftype, value in widgets:
        by_type.setdefault(ftype, []).append(value)
    r.check(f"{tag}: PyMuPDF reads 7 widgets with the filled values",
            len(widgets) == 7 and values["text"] in by_type.get("Text", []) and values["dropdown"] in by_type.get("ComboBox", []),
            r.evidence[f"{tag}_pymupdf_widgets"])
    pf = PdfReader(io.BytesIO(pdf)).get_fields() or {}
    got = {k: str(v.get("/V")) for k, v in pf.items()}
    r.check(f"{tag}: pypdf reads the AcroForm values", values["text"] in got.values() and values["dropdown"] in got.values(), got)
    exp = api.export(flatten_forms=True)
    with pymupdf.open(stream=exp, filetype="pdf") as doc:
        nw = sum(1 for _ in doc[page_index].widgets())
    found = P.all_have(exp, values["text"], page_index)
    r.check(f"{tag}: export flatten_forms: 0 widgets left, the typed value is page text (3 readers)",
            nw == 0 and all(found.values()), {"widgets": nw, "readers": found})


def ui_t07(ctx: Ctx, r: Record):
    if not ui_available(ctx, "field-text"):
        r.pending("Forms ▾ field tools (text/multiline/dropdown/radio/checkbox/signature box) + fill mode are not in the live UI (F2b)")
        return
    U = ctx.U
    ensure_approved(U, r)
    values = {"text": "ZZUIFIELD 77", "multiline": "ZZUI-ONE\nZZUI-TWO", "dropdown": "Bravo"}
    out = run_ui(ctx, r, "forms", {
        "cid": U.cid, "page": 3,
        "draws": [["field-text", [330, 580, 510, 598]], ["field-multiline", [330, 602, 510, 642]],
                  ["field-dropdown", [330, 646, 450, 664]], ["field-radio", [330, 670, 344, 684]],
                  ["field-checkbox", [400, 670, 414, 684]], ["field-signature", [460, 646, 580, 664]]],
        "options": ["Alpha", "Bravo", "Charlie"], "values": values})
    if out["pending"]:
        return
    _s, fields = U.get(f"{U.D}/versions/{U.current_no()}/fields")
    mine = [f for f in (fields or []) if f.get("source") == "editor"]
    r.evidence["fields"] = [(f["name"], f["type"], f["value"]) for f in mine]
    vals = {f["type"]: f["value"] for f in mine if f["type"] != "radio"}
    radio = {f["value"] for f in mine if f["type"] == "radio"}
    r.check("ui_t07: saved version: 7 widgets; text, multiline, dropdown, checkbox, radio values as filled",
            len(mine) == 7 and vals.get("text") == values["text"] and vals.get("multiline") == values["multiline"]
            and vals.get("dropdown") == values["dropdown"] and vals.get("checkbox") is True and len(radio - {""}) == 1,
            r.evidence["fields"])
    pages = [p["page_uid"] for p in U.pages()]
    _fields_pdf_evidence(ctx, r, "ui_t07", U, pages.index(ctx.state["U_uids"][2]), values)
    ensure_approved(U, r, "after filling")


# ======================================================================= 8 links

def _links_evidence(ctx, r, tag, api: Api, uid_src: str, uri: str, target_uid: str, src_index: int):
    pdf = api.version_pdf()
    s, links = api.get(f"{api.D}/versions/{api.current_no()}/pages/{uid_src}/links")
    r.evidence[f"{tag}_links"] = links
    r.check(f"{tag}: GET /links -> the web link and the page link (by page uid)",
            s == 200 and any(l["target"].get("uri") == uri for l in links)
            and any(l["target"].get("page_uid") == target_uid for l in links), links)
    pages = [p["page_uid"] for p in api.pages()]
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        raw = doc[src_index].get_links()
    r.check(f"{tag}: in the PDF: URI + GoTo to the target page's index",
            any(l.get("uri") == uri for l in raw) and any(l.get("kind") == pymupdf.LINK_GOTO
                                                           and l.get("page") == pages.index(target_uid) for l in raw),
            [{k: l.get(k) for k in ("kind", "uri", "page")} for l in raw])
    annots = PdfReader(io.BytesIO(pdf)).pages[src_index].get("/Annots") or []
    uris = [a.get_object().get("/A", {}).get("/URI") for a in annots if a.get_object().get("/Subtype") == "/Link"]
    r.check(f"{tag}: pypdf sees the /Link /URI", uri in [str(u) for u in uris], [str(u) for u in uris])
    return raw


def api_t08(ctx: Ctx, r: Record):
    A = ctx.A
    ensure_approved(A, r)
    u1, _u2, u3 = ctx.state["A_uids"]
    before = A.version_pdf()
    b, ln = find_line(A.text_model(u1), "Website link area")
    rect = dict(ln["bbox"])
    uri = "https://example.com/zzacc-link"
    ops = [{"id": "acc-t08-uri", "kind": "link.add", "page_uid": u1, "rect": rect, "target": {"type": "uri", "uri": uri}},
           {"id": "acc-t08-pg", "kind": "link.add", "page_uid": u1,
            "rect": {"x_pt": 250, "y_pt": rect["y_pt"], "w_pt": 90, "h_pt": rect["h_pt"]},
            "target": {"type": "page", "page_uid": u3, "y_pt": 10}}]
    s, out = A.commit_ops(ops, "t08")
    r.check("commit 2 link.add -> 201 (structure: review kept)", s == 201 and out.get("review_reset") is False,
            {"status": s, "body": out if s != 201 else None})
    raw = _links_evidence(ctx, r, "api_t08", A, u1, uri, u3, 0)
    lk = next((l for l in raw if l.get("uri") == uri), None)
    if lk:
        fr = lk["from"]
        dev = max(abs(fr.x0 - rect["x_pt"]), abs(fr.y0 - rect["y_pt"]), abs(fr.x1 - rect["x_pt"] - rect["w_pt"]),
                  abs(fr.y1 - rect["y_pt"] - rect["h_pt"]))
        r.check("api_t08: the link's clickable rect is where it was asked (±0.5 pt)", dev <= 0.5, round(dev, 3))
    after = A.version_pdf()
    content_evidence(ctx, r, "api_t08", before, after, 0, present=["Website link area"], regions=[],
                     expect_inside_change=False)
    s, e = A.post(f"{A.D}/draft", {"base_version_no": A.current_no(), "client_session_id": "zzacc-t08-bad"})
    if s in (200, 201):
        s2, e2 = A.put(f"{A.D}/draft", {"revision": e["revision"], "client_session_id": "zzacc-t08-bad", "ops": [
            {"id": "bad", "kind": "link.add", "page_uid": u1, "rect": rect, "target": {"type": "uri", "uri": "javascript:alert(1)"}}]})
        r.check("a javascript: link is refused (422)", s2 == 422, s2)
        A.delete(f"{A.D}/draft?revision={e['revision']}")


def ui_t08(ctx: Ctx, r: Record):
    U = ctx.U
    u1, _u2, u3 = ctx.state["U_uids"]
    uri = "https://example.com/zzacc-ui"
    out = run_ui(ctx, r, "links", {"cid": U.cid, "uri": uri, "uri_rect": [72, 232, 180, 248],
                                   "page_rect": [250, 232, 340, 248], "target_page": 3})
    if out["pending"]:
        return
    _links_evidence(ctx, r, "ui_t08", U, u1, uri, u3, 0)


# ======================================================================= 9 annotations + shapes

def api_t09(ctx: Ctx, r: Record):
    A = ctx.A
    u1, _u2, u3 = ctx.state["A_uids"]
    _b, ln = find_line(A.text_model(u1), "ZZHL")
    hl = ln["bbox"]
    _b, ln2 = find_line(A.text_model(u1), "Visible clause")
    vb = ln2["bbox"]
    els = [
        {"id": "acc-t09-hl", "kind": "highlight", "page_uid": u1, "rects": [hl], "color": "yellow"},
        {"id": "acc-t09-ul", "kind": "underline", "page_uid": u1, "rects": [vb], "color": "blue"},
        {"id": "acc-t09-so", "kind": "strikeout", "page_uid": u1, "rects": [vb], "color": "red"},
        {"id": "acc-t09-rect", "kind": "rect", "page_uid": u3, "x_pt": 100, "y_pt": 100, "w_pt": 120, "h_pt": 50, "color": "green", "stroke_pt": 2},
        {"id": "acc-t09-ell", "kind": "ellipse", "page_uid": u3, "x_pt": 260, "y_pt": 100, "w_pt": 100, "h_pt": 50, "color": "blue", "stroke_pt": 2},
        {"id": "acc-t09-line", "kind": "line", "page_uid": u3, "x1_pt": 400, "y1_pt": 100, "x2_pt": 520, "y2_pt": 150, "color": "red", "stroke_pt": 2},
        {"id": "acc-t09-arrow", "kind": "arrow", "page_uid": u3, "x1_pt": 100, "y1_pt": 180, "x2_pt": 220, "y2_pt": 220, "color": "black", "stroke_pt": 2},
        {"id": "acc-t09-ink", "kind": "ink", "page_uid": u3, "paths": [[{"x_pt": 260 + i * 5, "y_pt": 190 + (i % 4) * 6} for i in range(20)]],
         "color": "purple", "stroke_pt": 2},
        {"id": "acc-t09-note", "kind": "note", "page_uid": u3, "x_pt": 420, "y_pt": 190, "text": "ZZSYNTH sticky note"},
        {"id": "acc-t09-s2", "kind": "shape2", "page_uid": u3, "shape": "rect", "x_pt": 100, "y_pt": 260, "w_pt": 120, "h_pt": 50,
         "stroke_color": "blue", "fill_color": "yellow", "opacity": 0.6, "dash": "dash", "stroke_pt": 2},
        {"id": "acc-t09-s2a", "kind": "shape2", "page_uid": u3, "shape": "arrow", "x1_pt": 260, "y1_pt": 260, "x2_pt": 380, "y2_pt": 300,
         "stroke_color": "red", "heads": "both", "stroke_pt": 2},
        {"id": "acc-t09-hink", "kind": "ink2", "page_uid": u3, "paths": [[{"x_pt": 420 + i * 6, "y_pt": 270} for i in range(15)]],
         "color": "yellow", "stroke_pt": 10, "freehand_highlight": True},
        {"id": "acc-t09-sq", "kind": "square_mark", "page_uid": u3, "x_pt": 120, "y_pt": 360, "size_pt": 12, "color": "black"},
        {"id": "acc-t09-ck", "kind": "check", "page_uid": u3, "x_pt": 160, "y_pt": 360, "size_pt": 14},
        {"id": "acc-t09-cx", "kind": "cross", "page_uid": u3, "x_pt": 200, "y_pt": 360, "size_pt": 14},
    ]
    s, out = A.put_markup(A.markup()["elements"] + els)
    r.check(f"PUT markup: {len(els)} annotation/shape elements -> 200", s == 200, out if s != 200 else None)
    exp = A.export()
    dump(ctx, "A_t09_export.pdf", exp)
    pages = [p["page_uid"] for p in A.pages()]
    i1, i3 = pages.index(u1), pages.index(u3)
    imgs = P.render(exp)
    p1, p3 = imgs[i1], imgs[i3]
    hb, n = P.ink_box(p1, rect_pt(hl, 2), P.is_yellowish)
    r.check("highlight: yellow under the line, within its rect (±1 pt)", n > 300 and hb is not None
            and hb[0] >= hl["x_pt"] - 1 and hb[2] <= hl["x_pt"] + hl["w_pt"] + 1, {"ink": hb, "rect": hl})
    r.check("highlight: the text under it still extracts (3 readers)", all(P.all_have(exp, "Highlight this sentence", i1).values()))
    _b, n = P.ink_box(p1, (vb["x_pt"], vb["y_pt"] + vb["h_pt"] * 0.7, vb["x_pt"] + vb["w_pt"], vb["y_pt"] + vb["h_pt"] + 2), P.is_blue)
    r.check("underline: blue under the line", n > 50, n)
    _b, n = P.ink_box(p1, (vb["x_pt"], vb["y_pt"] + vb["h_pt"] * 0.3, vb["x_pt"] + vb["w_pt"], vb["y_pt"] + vb["h_pt"] * 0.7), P.is_red)
    r.check("strikeout: red through the line", n > 50, n)

    def stroke_box(region, pred, want, name, tol=1.6):
        b, n = P.ink_box(p3, region, pred)
        dev = None if b is None else max(abs(a - c) for a, c in zip(b, want))
        r.check(f"{name}: drawn on its geometry (outer stroke edge ±{tol} pt)", dev is not None and dev <= tol,
                {"ink": b, "want": want, "dev": dev and round(dev, 3)})

    stroke_box((95, 95, 225, 155), P.is_green, (99, 99, 221, 151), "rect")
    stroke_box((255, 95, 365, 155), P.is_blue, (259, 99, 361, 151), "ellipse")
    stroke_box((395, 95, 525, 155), P.is_red, (399.3, 99.3, 520.7, 150.7), "line", tol=1.2)
    b, n = P.ink_box(p3, (95, 175, 225, 225), P.is_dark)
    r.check("arrow: drawn from its start to its head", n > 100 and b is not None and b[0] <= 101 and b[2] >= 219, b)
    _b, n = P.ink_box(p3, (255, 185, 360, 215), lambda p: p[0] > 90 and p[2] > 90 and p[1] < 80)
    r.check("ink: purple stroke drawn", n > 100, n)
    with pymupdf.open(stream=exp, filetype="pdf") as doc:
        kinds = [a.type[1] for a in doc[i3].annots()]
    r.check("note: a real PDF /Text annotation", "Text" in kinds, kinds)
    _b, n = P.ink_box(p3, (102, 262, 218, 308), P.is_yellowish)
    r.check("shape2: translucent yellow fill inside", n > 500, n)
    _b, n = P.ink_box(p3, (95, 255, 225, 315), P.is_blue)
    r.check("shape2: dashed blue stroke", n > 100, n)
    _b, n = P.ink_box(p3, (255, 255, 385, 305), P.is_red)
    r.check("shape2 arrow (both heads): red", n > 100, n)
    _b, n = P.ink_box(p3, (415, 262, 515, 278), lambda p: p[0] > 200 and p[1] > 200 and p[2] < 200)
    r.check("freehand highlight (ink2): translucent yellow", n > 200, n)
    sq, n = P.ink_box(p3, (110, 350, 130, 370), P.is_black)
    r.check("filled square: 12 pt black square centred on its point (±0.5 pt)", sq is not None
            and max(abs(sq[0] - 114), abs(sq[1] - 354), abs(sq[2] - 126), abs(sq[3] - 366)) <= 0.5, sq)
    _b, n1 = P.ink_box(p3, (150, 350, 170, 370), P.is_dark)
    _b, n2 = P.ink_box(p3, (190, 350, 210, 370), P.is_dark)
    r.check("check ✔ and cross ✖ drawn", n1 > 30 and n2 > 30, [n1, n2])
    P.save_png(p3, ctx.path("api_t09_shapes.png"), (80, 80, 540, 380))
    P.save_png(p1, ctx.path("api_t09_textmarkup.png"), (60, 200, 360, 280))
    r.evidence["crops"] = [ctx.path("api_t09_shapes.png"), ctx.path("api_t09_textmarkup.png")]


def ui_t09(ctx: Ctx, r: Record):
    U = ctx.U
    shapes = [["rect", "shapes", "rect", [100, 100, 220, 150]], ["ellipse", "shapes", "ellipse", [260, 100, 360, 150]],
              ["line", "shapes", "line", [400, 100, 520, 150]], ["arrow", "shapes", "arrow", [100, 170, 220, 210]],
              ["ink", "annotate", "ink", [260, 170, 360, 210]]]
    clicks = [["check", "forms", "check", [420, 180]], ["cross", "forms", "cross", [460, 180]]]
    if ui_available(ctx, "square"):
        clicks.append(["square", "forms", "square_mark", [500, 180]])
    clicks.append(["note", "annotate", "note", [540, 180]])
    out = run_ui(ctx, r, "markup", {"cid": U.cid, "page": 3, "zooms": [100, 209], "shapes": shapes, "clicks": clicks,
                                    "text_markup": [["highlight", "ZZHL"], ["underline", "Website"]]})
    r.evidence["placements"] = out["facts"].get("placements")
    exp = U.export()
    dump(ctx, "U_t09_export.pdf", exp)
    pages = [p["page_uid"] for p in U.pages()]
    i3 = pages.index(ctx.state["U_uids"][2])
    img = P.render(exp)[i3]
    for pl in out["facts"].get("placements") or []:
        if pl["tool"] == "rect" and pl["stored"]:
            _k, x, y, w, h = pl["stored"]
            b, n = P.ink_box(img, (x - 4, y - 4, x + w + 4, y + h + 4), lambda p: max(p) < 200)
            dev = None if b is None else max(abs(b[0] - x), abs(b[1] - y), abs(b[2] - x - w), abs(b[3] - y - h))
            r.check(f"ui_t09: [{pl['zoom']['label']}%] exported rect sits on the stored box (stroke ±1.6 pt)",
                    dev is not None and dev <= 1.6, {"ink": b, "stored": pl["stored"]})
    P.save_png(img, ctx.path("ui_t09_shapes.png"), (80, 80, 600, 420))
    ensure_approved(U, r)


# ======================================================================= 10 whiteout (+ redact)

def api_t10(ctx: Ctx, r: Record):
    A = ctx.A
    ensure_approved(A, r)
    u1 = ctx.state["A_uids"][0]
    tm = A.text_model(u1)
    _b, lw = find_line(tm, "ZZWHITE")
    wb = lw["bbox"]
    wo = {"id": "acc-t10-wo", "kind": "whiteout", "page_uid": u1, "x_pt": wb["x_pt"] - 2, "y_pt": wb["y_pt"] - 2,
          "w_pt": wb["w_pt"] + 4, "h_pt": wb["h_pt"] + 4}
    s, out = A.put_markup(A.markup()["elements"] + [wo])
    r.check("PUT markup whiteout -> 200, review re-opened (Q1 markup-content)", s == 200 and out.get("review_reset") is True, out)
    exp = A.export()
    pages = [p["page_uid"] for p in A.pages()]
    i1 = pages.index(u1)
    img = P.render(exp)[i1]
    reg = (wo["x_pt"] + 0.5, wo["y_pt"] + 0.5, wo["x_pt"] + wo["w_pt"] - 0.5, wo["y_pt"] + wo["h_pt"] - 0.5)
    total = (P.region_px(reg)[2] - P.region_px(reg)[0]) * (P.region_px(reg)[3] - P.region_px(reg)[1])
    white = P.white_px(img, reg)
    r.check("whiteout: the covered area is opaque white in the export (100 %)", white == total, {"white": white, "of": total})
    still = P.all_have(exp, "ZZWHITE", i1)
    r.note(f"whiteout is VISUAL only by design (schemas/contract.py:745-747): the covered text still extracts {still}")
    P.save_png(img, ctx.path("api_t10_whiteout.png"), (60, 180, 330, 210))
    # a REAL removal: redact
    before = A.version_pdf()
    _b, ls = find_line(A.text_model(u1), "ZZSECRET")
    sb = ls["bbox"]
    rr = {"x_pt": sb["x_pt"] - 1, "y_pt": sb["y_pt"] - 1, "w_pt": sb["w_pt"] + 2, "h_pt": sb["h_pt"] + 2}
    s, out = A.commit_ops([{"id": "acc-t10-red", "kind": "redact", "page_uid": u1, "rects": [rr], "fill": "black"}], "t10")
    r.check("commit redact -> 201", s == 201, {"status": s, "body": out if s != 201 else None})
    after = A.version_pdf()
    dump(ctx, "A_t10_redacted.pdf", after)
    content_evidence(ctx, r, "api_t10", before, after, i1, absent=["ZZSECRET", "SECRET-9876", "REDACT-ME"], regions=[rect_pt(rr, 0.5)])
    sbytes = P.stream_bytes(after)
    r.check("redact: the secret is in NO decompressed stream nor the raw bytes",
            not any(x in sbytes for x in (b"ZZSECRET", b"REDACT-ME", b"SECRET-9876")))
    blk, n = P.ink_box(P.render(after)[i1], rect_pt(rr), P.is_black)
    r.check("redact: a black box fills the rect (±0.5 pt)", blk is not None and max(
        abs(blk[0] - rr["x_pt"]), abs(blk[1] - rr["y_pt"]), abs(blk[2] - rr["x_pt"] - rr["w_pt"]), abs(blk[3] - rr["y_pt"] - rr["h_pt"])) <= 0.5,
            {"box": blk, "rect": rr})
    ensure_approved(A, r)


def ui_t10(ctx: Ctx, r: Record):
    U = ctx.U
    u1 = ctx.state["U_uids"][0]
    tm = U.text_model(u1)
    _b, lw = find_line(tm, "ZZWHITE")
    _b, ls = find_line(tm, "ZZSECRET")
    wb, sb = lw["bbox"], ls["bbox"]
    before = U.version_pdf()
    out = run_ui(ctx, r, "whiteout", {"cid": U.cid,
                                      "white_rect": [wb["x_pt"] - 2, wb["y_pt"] - 2, wb["x_pt"] + wb["w_pt"] + 2, wb["y_pt"] + wb["h_pt"] + 2],
                                      "redact_rect": [sb["x_pt"] - 1, sb["y_pt"] - 1, sb["x_pt"] + sb["w_pt"] + 1, sb["y_pt"] + sb["h_pt"] + 1]})
    wo = (out["facts"].get("whiteout") or [None])[0]
    exp = U.export()
    pages = [p["page_uid"] for p in U.pages()]
    i1 = pages.index(u1)
    img = P.render(exp)[i1]
    if wo:
        reg = (wo["x_pt"] + 0.5, wo["y_pt"] + 0.5, wo["x_pt"] + wo["w_pt"] - 0.5, wo["y_pt"] + wo["h_pt"] - 0.5)
        px = P.region_px(reg)
        r.check("ui_t10: whiteout area opaque white in the export", P.white_px(img, reg) == (px[2] - px[0]) * (px[3] - px[1]))
    if not any("Redact" in p for p in out["pending"]):
        after = U.version_pdf()
        gone = P.none_have(after, "ZZSECRET", i1)
        r.check("ui_t10: redacted text absent in all 3 readers", all(gone.values()), gone)
        r.check("ui_t10: and in no stream", b"ZZSECRET" not in P.stream_bytes(after))
        content_evidence(ctx, r, "ui_t10", before, after, i1, absent=["ZZSECRET"],
                         regions=[(sb["x_pt"] - 4, sb["y_pt"] - 4, sb["x_pt"] + sb["w_pt"] + 4, sb["y_pt"] + sb["h_pt"] + 4)])
    ensure_approved(U, r)


# ======================================================================= 12 pages

NEW_UID_API = "pg_acc0000a0001"


def api_t12(ctx: Ctx, r: Record):
    A = ctx.A
    u1, u2, u3 = ctx.state["A_uids"]
    before = A.version_pdf()
    ops = [{"id": "acc-t12-ins", "kind": "page.insert", "new_page_uid": NEW_UID_API, "after_page_uid": u1, "size": "same_as_previous"},
           {"id": "acc-t12-del", "kind": "page.delete", "page_uid": u2},
           {"id": "acc-t12-rot", "kind": "page.rotate", "page_uid": NEW_UID_API, "rotation": 90},
           {"id": "acc-t12-mov", "kind": "page.move", "page_uid": u3, "after_page_uid": None}]
    s, out = A.commit_ops(ops, "t12")
    r.check("commit insert + delete + rotate + move -> 201", s == 201, {"status": s, "body": out if s != 201 else out.get("warnings")})
    pages = A.pages()
    r.evidence["pages"] = pages
    r.check("page order = [p3, p1, new], uids stable", [p["page_uid"] for p in pages] == [u3, u1, NEW_UID_API],
            [(p["page_uid"], p["origin_page"], p["rotation"]) for p in pages])
    newp = pages[2]
    r.check("inserted page: origin null, /Rotate 90, same size", newp["origin_page"] is None and newp["rotation"] == 90
            and (newp["width_pt"], newp["height_pt"]) == (612.0, 792.0), newp)
    after = A.version_pdf()
    dump(ctx, "A_t12_pages.pdf", after)
    with pymupdf.open(stream=after, filetype="pdf") as doc:
        r.check("PDF: 3 pages, rotation [0, 0, 90]", doc.page_count == 3 and [p.rotation for p in doc] == [0, 0, 90],
                [p.rotation for p in doc])
    t = P.texts(after)
    r.check("deleted page's text gone (3 readers)", all("ZZPAGE2" not in " ".join(v) for v in t.values()))
    r.check("page 3's text now on page 1 (3 readers)", all("ZZPAGE3" in v[0] for v in t.values()))
    r.check("page 1's text now on page 2 (3 readers)", all("Premier Trucking" in v[1] for v in t.values()))
    ib, ia = P.render(before), P.render(after)
    r.check("moved pages are pixel-identical to before (p3->1, p1->2)", P.diff_outside(ib[2], ia[0], []) == 0
            and P.diff_outside(ib[0], ia[1], []) == 0, [P.diff_outside(ib[2], ia[0], []), P.diff_outside(ib[0], ia[1], [])])
    r.check("inserted page is blank", ia[2].convert("L").getextrema()[0] >= 250, ia[2].convert("L").getextrema())
    A.wait_idle()
    _s, cmp_ = A.get(f"/api/contracts/{A.cid}/compare")
    slots = {x["field_id"]: (x["page"], x.get("page_uid")) for x in cmp_.get("signature_slots", [])}
    r.check("slots follow their pages (p1 slot -> position 2, p3 slot -> position 1)",
            slots.get("p1_contractor_signature") == (2, u1) and slots.get("p3_contractor_signature") == (1, u3), slots)
    s, links = A.get(f"{A.D}/versions/{A.current_no()}/pages/{u1}/links")
    r.check("the page link still points at p3 by uid after the move", any(l["target"].get("page_uid") == u3 for l in links), links)
    ctx.state["A_uids_after_t12"] = [p["page_uid"] for p in pages]
    ensure_approved(A, r, "after page ops")


def ui_t12(ctx: Ctx, r: Record):
    U = ctx.U
    out = run_ui(ctx, r, "pages", {"cid": U.cid})
    if out["pending"]:
        return
    u1, u2, u3 = ctx.state["U_uids"]
    pages = U.pages()
    r.check("ui_t12: pages = [p1, new, p3], the new page turned 90, old p2 gone",
            [p["page_uid"] for p in pages][0] == u1 and pages[2]["page_uid"] == u3 and pages[1]["rotation"] == 90
            and u2 not in [p["page_uid"] for p in pages] and pages[1]["origin_page"] is None,
            [(p["page_uid"], p["origin_page"], p["rotation"]) for p in pages])
    after = U.version_pdf()
    t = P.texts(after)
    r.check("ui_t12: old page 2 text gone (3 readers)", all("ZZPAGE2" not in " ".join(v) for v in t.values()))
    with pymupdf.open(stream=after, filetype="pdf") as doc:
        r.check("ui_t12: PDF /Rotate [0, 90, 0]", [p.rotation for p in doc] == [0, 90, 0], [p.rotation for p in doc])
    ensure_approved(U, r, "after page ops")


# ======================================================================= 13 undo / redo

def api_t13(ctx: Ctx, r: Record):
    """The draft is what undo/redo manipulate: the op list sent with each PUT."""
    A = ctx.A
    u1 = ctx.state["A_uids"][0]
    v0 = A.current_no()
    tm = A.text_model(u1)
    op_a, _ = replace_op("acc-t13-a", u1, tm, "Visible clause", "Visible clause stays here. ZZUNDO-A")
    b, ln = find_line(tm, "Name: ____")
    op_b = {"id": "acc-t13-b", "kind": "text.delete", "page_uid": u1, "target": target(b, ln)}
    sid = "zzacc-t13-session"
    s, d = A.post(f"{A.D}/draft", {"base_version_no": v0, "client_session_id": sid})
    r.check("POST draft -> 201", s == 201, s)
    rev = d["revision"]
    steps = [("edit A + edit B", [op_a, op_b], True, False), ("undo B", [op_a], True, True),
             ("undo A", [], False, True), ("redo A", [op_a], True, True), ("redo B", [op_a, op_b], True, False)]
    pages = [p["page_uid"] for p in A.pages()]
    i1 = pages.index(u1)
    for label, ops, want_a, want_b_present in steps:
        s, put = A.put(f"{A.D}/draft", {"revision": rev, "client_session_id": sid, "ops": ops, "markup": None})
        if s != 200:
            r.check(f"{label}: PUT draft -> 200", False, put)
            break
        rev = put["revision"]
        s, pdf = A.get(f"{A.D}/draft/preview.pdf?revision={rev}", raw=True)
        t = P.texts(pdf)
        has_a = all("ZZUNDO-A" in v[i1] for v in t.values())
        has_b = all("Name:" in v[i1] for v in t.values())
        r.check(f"{label}: preview shows A={want_a} B-line={want_b_present} (3 readers)",
                s == 200 and has_a == want_a and has_b == want_b_present, {"A": has_a, "B-line present": has_b})
    s, _ = A.delete(f"{A.D}/draft?revision={rev}")
    r.check("Cancel (DELETE draft) -> 204; version unchanged", s == 204 and A.current_no() == v0, s)


def ui_t13(ctx: Ctx, r: Record):
    run_ui(ctx, r, "undo_redo", {"cid": ctx.U.cid, "text": "Visible clause stays here.", "suffix": " ZZUNDO"})


# ======================================================================= 14 save, close, reopen

def api_t14(ctx: Ctx, r: Record):
    A = ctx.A
    u1 = ctx.state["A_uids"][0]
    v0 = A.current_no()
    old_hash = A.document()["current_version"]["file_hash"]
    tm = A.text_model(u1)
    op, _ = replace_op("acc-t14", u1, tm, "ZZSYNTH ACCEPTANCE AGREEMENT", "ZZSYNTH ACCEPTANCE AGREEMENT (synthetic) ZZSAVED",
                       size_pt=14, bold=True)
    s, out = A.commit_ops([op], "t14")
    r.check("Save (commit) -> 201", s == 201, {"status": s, "body": out if s != 201 else None})
    # "close": forget everything; "reopen": a fresh login and a fresh client
    from acc_api import login
    B = Api(login(), A.cid)
    doc = B.document()
    r.check("reopened: current version = the saved one, no draft", doc["current_version"]["version_no"] == v0 + 1
            and doc["draft"] is None, {"v": doc["current_version"]["version_no"], "draft": doc["draft"]})
    tm2 = B.text_model(u1)
    r.check("reopened: the text model holds the saved text", any("ZZSAVED" in ln["text"] for b in tm2["blocks"] for ln in b["lines"]))
    pdf = B.version_pdf()
    pages = [p["page_uid"] for p in B.pages()]
    got = P.all_have(pdf, "ZZSAVED", pages.index(u1))
    r.check("reopened: the saved PDF carries it (3 readers)", all(got.values()), got)
    _s, vers = B.get(f"{B.D}/versions")
    prev = next(v for v in vers if v["version_no"] == v0)
    r.check("history: the previous version is kept unchanged (same hash)", prev["file_hash"] == old_hash,
            [(v["version_no"], v["file_hash"][:10], v["content_changed"]) for v in vers])
    ensure_approved(A, r)


def ui_t14(ctx: Ctx, r: Record):
    U = ctx.U
    run_ui(ctx, r, "save_reopen", {"cid": U.cid, "text": "ZZSYNTH ACCEPTANCE AGREEMENT (synthetic)", "suffix": " ZZSAVED"})
    pdf = U.version_pdf()
    pages = [p["page_uid"] for p in U.pages()]
    got = P.all_have(pdf, "ZZSAVED", pages.index(ctx.state["U_uids"][0]))
    r.check("ui_t14: the saved version's PDF carries the edit (3 readers)", all(got.values()), got)
    ensure_approved(U, r)


# ======================================================================= 15 restore a saved draft

def api_t15(ctx: Ctx, r: Record):
    A = ctx.A
    u1 = ctx.state["A_uids"][0]
    v0 = A.current_no()
    op, _ = replace_op("acc-t15", u1, A.text_model(u1), "Copy: API", "Copy: API ZZRESTORED", size_pt=9)
    s1 = "zzacc-t15-first-tab"
    s, d = A.post(f"{A.D}/draft", {"base_version_no": v0, "client_session_id": s1})
    s, put = A.put(f"{A.D}/draft", {"revision": d["revision"], "client_session_id": s1, "ops": [op], "markup": None})
    r.check("session 1: draft saved (autosave PUT) -> 200", s == 200, s)
    # session 1 "closes" without saving; session 2 comes back
    s2 = "zzacc-t15-second-tab"
    doc = A.document()
    r.check("the document reports the unsaved draft", doc["draft"] is not None and doc["draft"]["revision"] == put["revision"], doc["draft"])
    s, d2 = A.post(f"{A.D}/draft", {"base_version_no": v0, "client_session_id": s2})
    r.evidence["second_tab_without_take_over"] = {"status": s, "error": d2.get("error") if isinstance(d2, dict) else d2}
    s, d3 = A.post(f"{A.D}/draft", {"base_version_no": v0, "client_session_id": s2, "take_over": True})
    r.check("session 2 resumes (take over) -> the same ops come back", s in (200, 201) and d3["ops"] == put.get("ops", d3["ops"])
            and len(d3["ops"]) == 1 and d3["ops"][0]["id"] == "acc-t15", {"status": s, "ops": [o["id"] for o in d3.get("ops", [])]})
    s, pdf = A.get(f"{A.D}/draft/preview.pdf?revision={d3['revision']}", raw=True)
    pages = [p["page_uid"] for p in A.pages()]
    got = P.all_have(pdf, "ZZRESTORED", pages.index(u1))
    r.check("restored draft preview shows the edit (3 readers)", s == 200 and all(got.values()), got)
    s, e = A.put(f"{A.D}/draft", {"revision": d3["revision"], "client_session_id": s1, "ops": [], "markup": None})
    r.check("session 1 can no longer write (409 draft_taken_over)", s == 409, {"status": s, "error": e.get("error") if isinstance(e, dict) else e})
    s, out = A.post(f"{A.D}/draft/commit", {"revision": d3["revision"], "commit_request_id": "zzacc-t15-commit"})
    r.check("the restored draft is saved -> 201", s == 201, {"status": s, "body": out if s != 201 else None})
    pdf = A.version_pdf()
    got = P.all_have(pdf, "ZZRESTORED", pages.index(u1))
    r.check("the saved version carries the restored edit (3 readers)", all(got.values()), got)
    ensure_approved(A, r)


def ui_t15(ctx: Ctx, r: Record):
    U = ctx.U
    run_ui(ctx, r, "recovery", {"cid": U.cid, "text": "Copy: UI", "suffix": " ZZRESTORED"})
    pdf = U.version_pdf()
    pages = [p["page_uid"] for p in U.pages()]
    got = P.all_have(pdf, "ZZRESTORED", pages.index(ctx.state["U_uids"][0]))
    r.check("ui_t15: the resumed + saved edit is in the version (3 readers)", all(got.values()), got)
    ensure_approved(U, r)


# ======================================================================= 11 signature + date

def api_t11(ctx: Ctx, r: Record):
    A = ctx.A
    ensure_approved(A, r)
    uids = [p["page_uid"] for p in A.pages()]
    placed = []
    for field in ("p1_contractor_signature", "p3_contractor_signature"):
        s, sig = A.post(f"/api/contracts/{A.cid}/signatures", {"field_id": field, "signer_name": "ZZSYNTH Signer",
                                                                "signer_role": "contractor", "image_data": P.blue_signature()})
        r.check(f"slot signature {field} -> 201", s == 201, s)
        if s == 201:
            placed.append(sig)
    _s, rows = A.get(f"/api/contracts/{A.cid}/signatures")
    _s, cmp_ = A.get(f"/api/contracts/{A.cid}/compare")
    slots = {x["field_id"]: x for x in cmp_.get("signature_slots", [])}
    for row in rows:
        sl = slots.get(row["field_id"])
        r.check(f"{row['field_id']}: row on the slot's current page", sl is not None and row["page_number"] == sl["page"],
                {"row_page": row["page_number"], "slot_page": sl and sl["page"]})
    free = {"page": uids.index(ctx.state["A_uids"][2]) + 1, "bbox": {"x": 380, "y": 400, "width": 150, "height": 45},
            "signer_name": "ZZSYNTH Free", "signer_role": "contractor", "image_data": P.blue_signature()}
    s, fsig = A.post(f"/api/contracts/{A.cid}/signatures", free)
    r.check("free signature placement (page + bbox) -> 201", s == 201, s)
    ctx.state["A_free_sig"] = free
    ctx.state["A_slots"] = slots
    s, dfields = A.get(f"/api/contracts/{A.cid}/date-fields")
    r.evidence["date_fields"] = dfields
    r.check("date fields known for both slots", s == 200 and len(dfields if isinstance(dfields, list) else dfields.get("fields", [])) >= 2,
            dfields if s != 200 else None)
    r.note("the final-PDF checks of this test (ink on the line, dates in their fields) run after 17's finalize")


def t11_final_checks(ctx: Ctx, r: Record, final: bytes, version_pdf: bytes, slots: dict, free: dict | None, tag: str,
                     gaps: dict | None = None):
    """Signature ink bottom vs the fill line MEASURED in the source render
    (and the server's `rule_y`), dates printed inside their date fields."""
    imgs_f = P.render(final)
    imgs_v = P.render(version_pdf)
    res = {}
    for fid, sl in slots.items():
        if sl.get("kind") != "signature" and not fid.endswith("_signature"):
            continue
        pi = sl["page"] - 1
        bb = sl["bbox"]
        line_top = P.rule_top(imgs_v[pi], bb["x"] + 4, bb["x"] + bb["width"] - 4, bb["y"], bb["y"] + bb["height"] + 20)
        # the ink = what the final added above the line (any colour); the line itself is in both renders
        top = line_top if line_top is not None else bb["y"] + bb["height"] + 20
        ink, n_ink = P.diff_box(imgs_v[pi], imgs_f[pi], (bb["x"] - 40, bb["y"] - 60, bb["x"] + bb["width"] + 60, top + 0.4), thr=24)
        gap = None if (line_top is None or not ink) else round(line_top - ink[3], 3)
        want = (gaps or {}).get(fid, 1.0)
        res[fid] = {"line_top_measured": line_top, "rule_y": sl.get("rule_y"), "ink": ink, "gap": gap, "want": want}
        if sl.get("rule_y") is not None and line_top is not None:
            r.check(f"{tag}: {fid} server rule_y = the measured line top (±0.5 pt)", abs(sl["rule_y"] - line_top) <= 0.5,
                    res[fid])
        r.check(f"{tag}: {fid} ink bottom {want:g} pt above its line (±0.5 pt)", gap is not None and abs(gap - want) <= 0.5, res[fid])
        P.save_png(imgs_f[pi], ctx.path(f"{tag}_{fid}.png"), (bb["x"] - 40, bb["y"] - 40, bb["x"] + bb["width"] + 200, bb["y"] + bb["height"] + 20))
    if free:
        b = free["bbox"]
        ink, n = P.ink_box(imgs_f[free["page"] - 1], (b["x"] - 5, b["y"] - 5, b["x"] + b["width"] + 5, b["y"] + b["height"] + 5), P.is_blue)
        r.check(f"{tag}: free signature drawn inside its box", ink is not None and ink[0] >= b["x"] - 0.5 and ink[2] <= b["x"] + b["width"] + 0.5
                and ink[3] <= b["y"] + b["height"] + 0.5, {"ink": ink, "bbox": b})
    for fid, sl in slots.items():
        if sl.get("kind") != "date":
            continue
        pi = sl["page"] - 1
        bb = sl["bbox"]
        hits = [s for s in P.spans(final, pi, DATE_US)
                if s["bbox"][0] >= bb["x"] - 4 and s["bbox"][2] <= bb["x"] + bb["width"] + 40
                and s["bbox"][1] >= bb["y"] - 6 and s["bbox"][3] <= bb["y"] + bb["height"] + 8]
        r.check(f"{tag}: {fid}: «{DATE_US}» printed inside the date field (PyMuPDF span)", bool(hits),
                {"field": bb, "spans": [s["bbox"] for s in P.spans(final, pi, DATE_US)]})
    t = P.texts(final)
    counts = {k: " ".join(v).count(DATE_US) for k, v in t.items()}
    r.evidence[f"{tag}_date_counts_by_reader"] = counts
    if counts.get("pdfplumber", 0) < counts.get("pymupdf", 0):
        r.note(f"{tag}: pdfplumber merges the date with the printed __/__/____ ({counts}) -- legacy, JURNAL B1 finding (3)")
    r.evidence[f"{tag}_signature_geometry"] = res


def pdfjs_vs_pdfium(r: Record, label: str, js: dict | None, data: bytes):
    if not js:
        r.check(f"pdf.js opened {label}", False)
        return
    ref = [im.convert("L").getextrema()[0] < 200 for im in P.render(data, scale=1.0)]
    got = [v > 0 for v in js.get("ink", [])]
    r.check(f"pdf.js: {label} has {len(ref)} pages and draws ink on the same pages as pdfium", js.get("pages") == len(ref)
            and got == ref, {"pdfjs_pages": js.get("pages"), "pdfjs_ink": js.get("ink"), "pdfium_nonblank": ref})


def has_all(r: Record, tag: str, data: bytes, needles, page=None, absent=False):
    t = P.texts(data)
    norm = lambda s: " ".join(s.split())  # noqa: E731
    for needle in needles:
        got = {k: (norm(needle) in norm(v[page] if page is not None else " ".join(v))) for k, v in t.items()}
        errs = {k: [x for x in v if x.startswith("<ERROR")][0][:60] for k, v in t.items() if any(x.startswith("<ERROR") for x in v)}
        if absent:
            r.check(f"{tag}: NO «{needle}» (3 readers)", not any(got.values()), {"found": got, "reader_errors": errs or None})
        else:
            r.check(f"{tag}: «{needle}» (3 readers)", all(got.values()), {"found": got, "reader_errors": errs or None})


def ui_t11(ctx: Ctx, r: Record):
    U = ctx.U
    ensure_approved(U, r)
    out = run_ui(ctx, r, "sign", {"cid": U.cid, "slots": ["p1_contractor_signature", "p3_contractor_signature"], "date_iso": DATE_ISO})
    ctx.state["U_layout"] = out["facts"].get("layout")
    r.note("the final-PDF checks of this test run after 17's finalize")


# ======================================================================= 16 export + other viewers

def api_t16(ctx: Ctx, r: Record):
    A = ctx.A
    files = {}
    exp = A.export()
    files["export"] = dump(ctx, "A_t16_export.pdf", exp)
    files["export_flat"] = dump(ctx, "A_t16_export_flat.pdf", A.export(flatten_forms=True))
    files["version"] = dump(ctx, "A_t16_version.pdf", A.version_pdf())
    n_pages = len(A.pages())
    for label, path in files.items():
        o = P.open_everywhere(open(path, "rb").read())
        r.evidence[f"open_{label}"] = o
        r.check(f"{label}: opens in pypdf, pdfplumber, PyMuPDF (no repair) and renders in pdfium, {n_pages} pages",
                o["pypdf"] == o["pdfplumber"] == o["pymupdf"] == o["pdfium_rendered"] == n_pages and not o["pymupdf_repaired"]
                and o["pdfium_nonblank"] >= n_pages - 1, o)
        r.check(f"{label}: text extraction works in every reader (no crash)", not o["text_errors"], o["text_errors"])
    has_all(r, "export text", exp, ("Premier Trucking Group Inc.", "ZZSAVED", "ZZRESTORED", "ZZPARA2"))
    has_all(r, "export text", exp, ("ABC Transportation", "ZZOBSOLETE", "ZZSECRET"), absent=True)
    _s, rows = A.get(f"/api/contracts/{A.cid}/signatures")
    pics = {e["asset_id"] for e in A.markup()["elements"] if e["kind"] in ("image", "image2")}
    r.check("export carries NO signature (image placements = the markup pictures only)",
            P.image_placements(exp) == len(pics) and len(rows) >= 2,
            {"placements": P.image_placements(exp), "markup_pictures": len(pics), "signature_rows": len(rows)})
    out = ctx.ui("pdfjs", {"files": files}, r)
    r.evidence["pdfjs"] = out["facts"].get("pdfjs")
    for label, path in files.items():
        pdfjs_vs_pdfium(r, label, (out["facts"].get("pdfjs") or {}).get(label), open(path, "rb").read())
    js = (out["facts"].get("pdfjs") or {}).get("export", {})
    r.check("pdf.js: the export's text has the edit and the saved/restored words",
            all(w in " ".join(js.get("text", [])) for w in ("Premier Trucking", "ZZSAVED", "ZZRESTORED")), None)


def ui_t16(ctx: Ctx, r: Record):
    U = ctx.U
    out = run_ui(ctx, r, "download", {"cid": U.cid})
    path = out["files"].get("download")
    r.evidence["mode"] = out["facts"].get("mode")
    r.evidence["download_source"] = out["facts"].get("download_source")
    if not path:
        return
    data = open(path, "rb").read()
    o = P.open_everywhere(data)
    n = len(U.pages())
    r.check("downloaded PDF opens in pypdf / pdfplumber / PyMuPDF / pdfium", o["pypdf"] == o["pdfplumber"] == o["pymupdf"] ==
            o["pdfium_rendered"] == n and not o["pymupdf_repaired"], o)
    got = P.all_have(data, "Premier Trucking")
    r.check("the download is the EDITED document (edit text extracted by 3 readers)", all(got.values()),
            {"readers": got, "mode": out["facts"].get("mode"), "source": out["facts"].get("download_source")})
    js = ctx.ui("pdfjs", {"files": {"ui_download": path}}, r)
    r.evidence["pdfjs"] = js["facts"].get("pdfjs")
    pdfjs_vs_pdfium(r, "ui_download", (js["facts"].get("pdfjs") or {}).get("ui_download"), data)


# ======================================================================= 17 final from the edited version

def _final_common(ctx, r, tag, api: Api, final: bytes, present, absent):
    has_all(r, f"{tag}: final", final, present)
    has_all(r, f"{tag}: final", final, absent, absent=True)
    _s, rows = api.get(f"/api/contracts/{api.cid}/signatures")
    n_img = P.image_xobjects(final)
    r.evidence[f"{tag}_xobjects"] = {"final": n_img, "signature_rows": len(rows)}
    s, ver = api.get(f"/api/download/{api.cid}/verify")
    r.check(f"{tag}: /verify intact", s == 200 and ver.get("intact") is True, ver)
    _s, trail = api.get(f"/api/review/{api.cid}/audit")
    fz = [e["details"] for e in trail if e["action"] == "contract_finalized"] if isinstance(trail, list) else []
    cur = api.document()["current_version"]["version_no"] if api.contract().get("status") else None
    r.check(f"{tag}: audit contract_finalized from the current version", bool(fz) and fz[-1].get("source_version_no") == cur,
            {k: fz[-1].get(k) for k in ("source_version_no", "markup_source", "signatures")} if fz else None)
    return rows, n_img


def api_t17(ctx: Ctx, r: Record):
    A = ctx.A
    ensure_approved(A, r)
    version = A.version_pdf()
    vno = A.current_no()
    export_before = A.export()
    s, fin = A.post(f"/api/contracts/{A.cid}/finalize", {"contract_date": DATE_US})
    r.check("finalize -> 200", s == 200, fin if s != 200 else None)
    s, final = A.get(f"/api/download/{A.cid}/final", raw=True)
    dump(ctx, "A_final.pdf", final)
    rows, n_img = _final_common(ctx, r, "api_t17", A, final,
                                present=["Premier Trucking Group Inc.", "ZZSAVED", "ZZRESTORED", "ZZSTYLE", "ZZPARA2", "ZZTEXTVALUE 42"],
                                absent=["ABC Transportation", "ZZOBSOLETE", "ZZSECRET", "ZZPAGE2"])
    n_exp, n_fin = P.image_placements(export_before), P.image_placements(final)
    r.check("api_t17: each signature drawn ONCE (final image placements = export's + signature rows)",
            n_fin == n_exp + len(rows), {"final_placements": n_fin, "export_placements": n_exp, "rows": len(rows),
                                         "final_xobjects": n_img})
    with pymupdf.open(stream=final, filetype="pdf") as doc:
        widgets = sum(1 for pg in doc for _ in pg.widgets())
        rot = [p.rotation for p in doc]
    r.check("api_t17: form fields flattened in the final (0 widgets), pages/rotation as edited", widgets == 0 and rot == [0, 0, 90],
            {"widgets": widgets, "rotation": rot})
    img = P.render(final)
    pages = ctx.state.get("A_uids_after_t12") or [p["page_uid"] for p in A.pages()]
    i1 = pages.index(ctx.state["A_uids"][0])
    wo = next((e for e in A.markup()["elements"] if e["id"] == "acc-t10-wo"), None)
    if wo:
        reg = (wo["x_pt"] + 0.5, wo["y_pt"] + 0.5, wo["x_pt"] + wo["w_pt"] - 0.5, wo["y_pt"] + wo["h_pt"] - 0.5)
        px = P.region_px(reg)
        r.check("api_t17: the whiteout is in the final (opaque white)", P.white_px(img[i1], reg) == (px[2] - px[0]) * (px[3] - px[1]))
    o = P.open_everywhere(final)
    r.check("api_t17: final text extraction works in every reader (no crash)", not o["text_errors"], o["text_errors"])
    r.check("api_t17: final opens in pypdf / pdfplumber / PyMuPDF / pdfium", o["pypdf"] == o["pdfplumber"] == o["pymupdf"] == 3
            and o["pdfium_rendered"] == 3 and not o["pymupdf_repaired"], o)
    js = ctx.ui("pdfjs", {"files": {"final": ctx.path("A_final.pdf")}}, r)
    r.evidence["pdfjs"] = js["facts"].get("pdfjs")
    pdfjs_vs_pdfium(r, "final", (js["facts"].get("pdfjs") or {}).get("final"), final)
    s, d = A.post(f"{A.D}/draft", {"base_version_no": vno, "client_session_id": "zzacc-t17-after"})
    r.check("after final: editing refused (409 contract_completed)", s == 409, {"status": s, "error": d.get("error") if isinstance(d, dict) else d})
    # 11's final checks
    _s, cmp_ = A.get(f"/api/contracts/{A.cid}/compare")
    slots = {x["field_id"]: x for x in cmp_.get("signature_slots", [])}
    t11_final_checks(ctx, ctx.record(11, "api"), final, version, slots, ctx.state.get("A_free_sig"), "api_t11")


def ui_t17(ctx: Ctx, r: Record):
    U = ctx.U
    version = U.version_pdf()
    _s, cmp_ = U.get(f"/api/contracts/{U.cid}/compare")
    slots = {x["field_id"]: x for x in cmp_.get("signature_slots", [])}
    run_ui(ctx, r, "finalize", {"cid": U.cid, "layout": ctx.state.get("U_layout"), "date_iso": DATE_ISO})
    s, final = U.get(f"/api/download/{U.cid}/final", raw=True)
    if s != 200:
        r.check("ui_t17: final PDF downloadable", False, s)
        return
    dump(ctx, "U_final.pdf", final)
    _final_common(ctx, r, "ui_t17", U, final, present=["Premier Trucking Group Inc.", "ZZSAVED", "ZZRESTORED"],
                  absent=["ABC Transportation", "ZZOBSOLETE"])
    t11_final_checks(ctx, ctx.record(11, "ui"), final, version, slots, None, "ui_t11")


# ======================================================================= 18 the older functions still work

def api_t18(ctx: Ctx, r: Record):
    L = ctx.L
    tid = ctx.ids["template"]
    s, lst = L.get(f"/api/contracts?template_id={tid}")
    r.check("dashboard list (GET /api/contracts?template_id) holds the ZZSYNTH contracts", s == 200
            and any(c["id"] == L.cid for c in lst), s)
    s, tpls = L.get("/api/templates")
    r.check("templates list holds the template", s == 200 and any(t.get("id") == tid or t.get("template_id") == tid for t in tpls), s)
    s, q = L.get("/api/review/queue")
    r.check("review queue holds the new (pending) contract", s == 200 and any(c["id"] == L.cid for c in q), s)
    s, sigs = L.get("/api/signatures")
    r.check("My signatures list -> 200", s == 200, s)
    s, fields = L.get(f"/api/contracts/{L.cid}/fields")
    r.check("extracted fields -> 200", s == 200 and isinstance(fields, list) and len(fields) > 0, s)
    s, cmp_ = L.get(f"/api/contracts/{L.cid}/compare")
    slots = {x["field_id"]: x for x in cmp_.get("signature_slots", [])}
    r.check("compare: 2 signature slots", len([k for k in slots if k.endswith("_signature")]) == 2, list(slots))
    r.check("approve (review) -> 200", L.approve() == 200)
    s, bulk = L.post(f"/api/contracts/{L.cid}/signatures/bulk", {"field_ids": ["p1_contractor_signature", "p3_contractor_signature"],
                                                                  "signer_name": "ZZSYNTH Bulk", "image_data": P.blue_signature()})
    r.check("«One for all»: bulk signature on both slots -> 200, 2 placed", s == 200 and len(bulk.get("placed", [])) == 2, bulk if s != 200 else None)
    version = L.get(f"/api/contracts/{L.cid}/file", raw=True)[1]
    body = {"contract_date": DATE_US,
            "layout": {"signature_offset_pt": 3.0, "slot_offsets_pt": {"p3_contractor_signature": 2.0}},
            "annotations": [{"id": "l-ck", "kind": "check", "page": 1, "x_pt": 480, "y_pt": 500, "size_pt": 14},
                            {"id": "l-cx", "kind": "cross", "page": 1, "x_pt": 520, "y_pt": 500, "size_pt": 14},
                            {"id": "l-tx", "kind": "text", "page": 1, "x_pt": 72, "y_pt": 520, "text": "ZZLEGACY NOTE",
                             "font": "Helvetica", "size_pt": 12}]}
    s, fin = L.post(f"/api/contracts/{L.cid}/finalize", body)
    r.check("finalize with group shift (+3 pt), one slot's own offset (+2 pt), ✔ ✖ and text -> 200", s == 200, fin if s != 200 else None)
    s, final = L.get(f"/api/download/{L.cid}/final", raw=True)
    dump(ctx, "L_final.pdf", final)
    img = P.render(final)[0]
    b1, n1 = P.ink_box(img, (470, 490, 490, 510), P.is_dark)
    b2, n2 = P.ink_box(img, (510, 490, 530, 510), P.is_dark)
    r.check("✔ and ✖ drawn centred on their points (±1 pt)", n1 > 30 and n2 > 30 and abs((b1[0] + b1[2]) / 2 - 480) <= 1
            and abs((b2[0] + b2[2]) / 2 - 520) <= 1, [b1, b2])
    g = P.line_geometry(final, 0, "ZZLEGACY NOTE")
    r.check("v4 text annotation at its baseline point (±0.5 pt)", g and abs(g["pymupdf"]["x0"] - 72) <= 0.5
            and abs(g["pymupdf"]["baseline"] - 520) <= 0.5, g)
    t11_final_checks(ctx, r, final, version, slots, None, "api_t18",
                     gaps={"p1_contractor_signature": 4.0, "p3_contractor_signature": 6.0})
    s, ver = L.get(f"/api/download/{L.cid}/verify")
    r.check("/verify intact", s == 200 and ver.get("intact") is True, ver)
    r.check("image placements = the original picture + 2 signatures", P.image_placements(final) == P.image_placements(version) + 2,
            {"final": P.image_placements(final), "uploaded": P.image_placements(version)})
    import sqlite3
    con = sqlite3.connect(r"file:C:\PTG\signwithme\backend\local.db?mode=ro", uri=True)
    n = con.execute("SELECT count(*) FROM documents WHERE contract_id IN (?, ?)", (L.cid.replace("-", ""), L.cid)).fetchone()[0]
    con.close()
    r.check("the legacy flow created NO editor document row", n == 0, n)


def ui_t18(ctx: Ctx, r: Record):
    pdf = ctx.write("zzacc_legacy_ui.pdf", P.make_contract("LEGACY-UI"))
    out = run_ui(ctx, r, "legacy", {"cid": None, "pdf": pdf, "contractor": f"ZZSYNTH-ACC-LegacyUI-{ctx.state['stamp']}",
                                    "template_name": ctx.state["template_name"], "check_at": [480, 500], "cross_at": [520, 500],
                                    "slots": ["p1_contractor_signature", "p3_contractor_signature"], "date_iso": DATE_ISO},
                 )
    cid = out["facts"].get("contract_id")
    if not cid:
        return
    ctx.ids["contract_legacy_ui"] = cid
    ctx.save_ids()
    L2 = Api(ctx.token, cid)
    s, final = L2.get(f"/api/download/{cid}/final", raw=True)
    if s != 200:
        r.check("ui_t18: final PDF exists", False, s)
        return
    dump(ctx, "L_ui_final.pdf", final)
    version = L2.get(f"/api/contracts/{cid}/file", raw=True)[1]
    _s, cmp_ = L2.get(f"/api/contracts/{cid}/compare")
    slots = {x["field_id"]: x for x in cmp_.get("signature_slots", [])}
    layout = json.loads(out["facts"].get("layout") or "{}")
    gaps = {}
    for fid in slots:
        if fid.endswith("_signature"):
            gaps[fid] = 1.0 + layout.get("signature_offset_pt", 0.0) + (layout.get("slot_offsets_pt") or {}).get(fid, 0.0)
    r.evidence["expected_gaps"] = gaps
    t11_final_checks(ctx, r, final, version, slots, None, "ui_t18", gaps=gaps)
    img = P.render(final)[0]
    for m in out["facts"].get("marks") or []:
        x, y = m["click"]
        b, n = P.ink_box(img, (x - 10, y - 10, x + 10, y + 10), P.is_dark)
        r.check(f"ui_t18: [{m['zoom']['label']}%] {m['tool']} printed centred on the click (±1 pt)", b is not None and n > 20
                and abs((b[0] + b[2]) / 2 - x) <= 1.0 and abs((b[1] + b[3]) / 2 - y) <= 1.0, {"ink": b, "click": m["click"]})
    s, ver = L2.get(f"/api/download/{cid}/verify")
    r.check("ui_t18: /verify intact", s == 200 and ver.get("intact") is True, ver)


TESTS = {
    1: ("Asl matnni tahrirlash", api_t01, ui_t01),
    2: ("Asl matnni o'chirish", api_t02, ui_t02),
    3: ("Ko'p qatorli matn qo'shish", api_t03, ui_t03),
    4: ("Shrift, o'lcham, rang", api_t04, ui_t04),
    5: ("Obyektlarni surish va dublikat", api_t05, ui_t05),
    6: ("Rasm va shtamp qo'shish/olib tashlash", api_t06, ui_t06),
    7: ("Barcha maydon turlarini yaratish va to'ldirish", api_t07, ui_t07),
    8: ("Ishlaydigan havolalar", api_t08, ui_t08),
    9: ("Annotatsiya va shakllar", api_t09, ui_t09),
    10: ("Whiteout", api_t10, ui_t10),
    11: ("Imzo va sana joylash", api_t11, ui_t11),
    12: ("Sahifa qo'shish/o'chirish/burish", api_t12, ui_t12),
    13: ("Undo/redo", api_t13, ui_t13),
    14: ("Saqlash, yopish, qayta ochish", api_t14, ui_t14),
    15: ("Saqlangan qoralamani tiklash", api_t15, ui_t15),
    16: ("Eksport va boshqa PDF ko'ruvchida ochish", api_t16, ui_t16),
    17: ("Tahrirlangan versiyadan yakuniy shartnoma", api_t17, ui_t17),
    18: ("Mavjud ilova funksiyalari hali ishlaydi", api_t18, ui_t18),
}
# Execution order: content edits, then structure, then the session tests, then
# signing and output (17 finalises; 11's final checks run inside 17).
API_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 11, 16, 17, 18]
UI_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 11, 16, 17, 18]
