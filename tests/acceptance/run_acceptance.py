"""SignWithMe PDF editor -- live acceptance suite (the spec's 18 tests, API + UI paths).

Run with the BACKEND venv (PDF libraries); the UI steps start the SYSTEM Python
(Playwright) themselves:

    C:\\PTG\\signwithme\\backend\\.venv\\Scripts\\python.exe run_acceptance.py [options]

    --only 1,4,9        a subset (numbers of the spec)
    --path api|ui|both  which path(s) (default both)
    --keep              do not clean up (debugging); clean later with --cleanup-only
    --cleanup-only DIR  remove what the run in DIR created, then compare counts
    --resume DIR        reuse the contracts of a kept run (no new backup / setup)

Every run: online backup of the live DB, synthetic ZZSYNTH data only, full
cleanup (contracts DELETE, template ?purge=true, new signatures / gallery items
deleted, soft-deleted gallery rows of this run purged), table counts before =
after, 0 residue in the editor-v2 tables, storage file count equal.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import acc_db as DB  # noqa: E402
import acc_pdf as P  # noqa: E402
from acc_api import Api, login, multipart, request, wait_for  # noqa: E402
from acc_cases import API_ORDER, TESTS, UI_ORDER  # noqa: E402
from acc_core import Ctx, run_record  # noqa: E402

BACKEND = r"C:\PTG\signwithme\backend"


def save_state(ctx: Ctx):
    json.dump({"ids": ctx.ids, "state": ctx.state}, open(ctx.path("state.json"), "w", encoding="utf-8"), indent=1, default=str)
    ctx.save_ids()


def load_state(ctx: Ctx):
    data = json.load(open(ctx.path("state.json"), encoding="utf-8"))
    ctx.ids.update(data["ids"])
    ctx.state.update(data["state"])


def upload_contract(token, tid, label, stamp, ctx: Ctx) -> str:
    data = P.make_contract(label)
    body, ctype = multipart({"template_id": tid, "contractor_name": f"ZZSYNTH-ACC-{label}-{stamp}"}, f"zzacc_{label}.pdf", data)
    s, created = request("POST", "/api/contracts", token, data=body, content_type=ctype)
    if s not in (200, 201, 202):
        raise RuntimeError(f"upload {label}: {s} {created}")
    cid = created["contract_id"]
    ctx.ids[f"contract_{label.lower()}"] = cid
    save_state(ctx)
    return cid


def setup(ctx: Ctx):
    token = ctx.token
    stamp = ctx.state["stamp"]
    name = f"ZZSYNTH-ACC-TPL-{stamp}"
    body, ctype = multipart({"name": name, "description": "ZZSYNTH acceptance suite (synthetic)"}, "zzacc_tpl.pdf",
                            P.make_contract("TEMPLATE"))
    s, created = request("POST", "/api/templates", token, data=body, content_type=ctype)
    if s not in (200, 201, 202):
        raise RuntimeError(f"template upload: {s} {created}")
    ctx.ids["template"] = created["template_id"]
    ctx.state["template_name"] = name
    save_state(ctx)
    wait_for(f"/api/templates/{ctx.ids['template']}", token, "status", ("ready",))
    for label in ("API", "UI", "LEGACY"):
        upload_contract(token, ctx.ids["template"], label, stamp, ctx)
    for key in ("contract_api", "contract_ui", "contract_legacy"):
        wait_for(f"/api/contracts/{ctx.ids[key]}", token, "status", ("analyzed", "reviewing"))
    ctx.A, ctx.U = Api(token, ctx.ids["contract_api"]), Api(token, ctx.ids["contract_ui"])
    for key, api in (("A", ctx.A), ("U", ctx.U)):
        s = api.approve()
        if s != 200:
            raise RuntimeError(f"approve {key}: {s}")
        ctx.state[f"{key}_uids"] = [p["page_uid"] for p in api.pages(1)]
    _s, cmp_ = request("GET", f"/api/contracts/{ctx.ids['contract_api']}/compare", token)
    ctx.state["slots_v1"] = [s["field_id"] for s in cmp_.get("signature_slots", [])]
    save_state(ctx)
    print("setup:", json.dumps({"ids": ctx.ids, "slots": ctx.state["slots_v1"]}), flush=True)


def before_lists(ctx: Ctx):
    _s, sigs = request("GET", "/api/signatures", ctx.token)
    _s, assets = request("GET", "/api/assets", ctx.token)
    ctx.state["sigs_before"] = [x["id"] for x in sigs] if isinstance(sigs, list) else []
    ctx.state["assets_before"] = [x["asset_id"] for x in assets] if isinstance(assets, list) else []


def cleanup(ctx: Ctx) -> dict:
    token = login()
    rep: dict = {"deleted": {}}
    for key, cid in list(ctx.ids.items()):
        if key.startswith("contract") and isinstance(cid, str):
            api = Api(token, cid)
            _s, d = api.get(f"{api.D}/draft")
            if isinstance(d, dict) and "revision" in d:
                api.delete(f"{api.D}/draft?revision={d['revision']}")
            s, _p = request("DELETE", f"/api/contracts/{cid}", token)
            rep["deleted"][key] = s
    if ctx.ids.get("template"):
        s, _p = request("DELETE", f"/api/templates/{ctx.ids['template']}?purge=true", token)
        rep["deleted"]["template(purge)"] = s
    _s, sigs = request("GET", "/api/signatures", token)
    new_sigs = [x["id"] for x in (sigs or []) if x["id"] not in ctx.state.get("sigs_before", [])]
    for sid in new_sigs:
        s, _p = request("DELETE", f"/api/signatures/{sid}", token)
        rep["deleted"][f"user_signature {sid[:8]}"] = s
    _s, assets = request("GET", "/api/assets", token)
    new_assets = [x["asset_id"] for x in (assets or []) if x["asset_id"] not in ctx.state.get("assets_before", [])]
    for aid in new_assets:
        s, _p = request("DELETE", f"/api/assets/{aid}", token)
        rep["deleted"][f"gallery {aid[:8]}"] = s
    rep["gallery_purge"] = DB.purge_soft_deleted_gallery("zzacc")
    before = ctx.state.get("snapshot_before")
    after = DB.snapshot()
    rep["snapshot_after"] = after
    rep["diff"] = DB.diff(before, after) if before else None
    rep["residue"] = DB.residue(ctx.ids)
    rep["stray_files"] = DB.stray_files(ctx.ids)
    rep["v2_tables_after"] = {t: after["db"].get(t) for t in DB.V2_TABLES}
    json.dump(rep, open(ctx.path("cleanup.json"), "w", encoding="utf-8"), indent=1, default=str)
    return rep


def write_report(ctx: Ctx, cleanup_rep: dict | None, started: float):
    rows = []
    for num in sorted(TESTS):
        title = TESTS[num][0]
        a = ctx.records.get((num, "api"))
        u = ctx.records.get((num, "ui"))
        rows.append({"num": num, "title": title, "api": a.as_dict() if a else None, "ui": u.as_dict() if u else None})
    results = {"run_dir": ctx.run_dir, "stamp": ctx.state.get("stamp"), "ids": ctx.ids,
               "backup": ctx.state.get("backup"), "seconds": round(time.time() - started, 1),
               "ui_tools": ctx.state.get("ui_tools"), "tests": rows, "cleanup": cleanup_rep}
    json.dump(results, open(ctx.path("results.json"), "w", encoding="utf-8"), indent=1, default=str)

    def cell(rec):
        if rec is None:
            return "—"
        d = rec
        n_ok = sum(1 for c in d["checks"] if c["ok"])
        extra = f" {n_ok}/{len(d['checks'])}" if d["checks"] else ""
        return f"{d['status']}{extra}"

    lines = [f"# SignWithMe — qabul to'plami natijasi ({ctx.state.get('stamp')})", "",
             f"Run: `{ctx.run_dir}` · davomiylik {round((time.time() - started) / 60, 1)} daq · zaxira `{(ctx.state.get('backup') or {}).get('path')}`", "",
             "| # | Test | API | UI |", "|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['num']} | {row['title']} | {cell(row['api'])} | {cell(row['ui'])} |")
    lines += ["", "`PASS*` = o'tdi, lekin bir qismi hali UI'da yo'q (PENDING). `SKIP` = bu yurishda tanlanmagan.", "",
              "## Yiqilgan tekshiruvlar va PENDING sabablari", ""]
    for row in rows:
        for path in ("api", "ui"):
            d = row[path]
            if not d:
                continue
            bad = [c for c in d["checks"] if not c["ok"]]
            if bad or d["pending"] or d["error"]:
                lines.append(f"### {row['num']}/{path.upper()} — {row['title']}")
                if d["error"]:
                    lines.append(f"- ERROR: `{d['error']}`")
                for c in bad:
                    lines.append(f"- FAIL: {c['name']} — `{json.dumps(c['detail'], default=str)[:400]}`")
                for p in d["pending"]:
                    lines.append(f"- PENDING: {p}")
                for n in d["notes"]:
                    lines.append(f"- NOTE: {n}")
                lines.append("")
    if cleanup_rep:
        lines += ["## Tozalash", "", "```", json.dumps({k: cleanup_rep[k] for k in ("deleted", "gallery_purge", "diff", "residue",
                                                                                   "v2_tables_after", "stray_files")},
                                                      indent=1, default=str), "```"]
    open(ctx.path("report.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines[:26]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--path", default="both", choices=("api", "ui", "both"))
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--cleanup-only")
    ap.add_argument("--resume")
    a = ap.parse_args()
    started = time.time()
    if a.cleanup_only:
        ctx = Ctx(a.cleanup_only, login())
        load_state(ctx)
        rep = cleanup(ctx)
        print(json.dumps({k: rep[k] for k in ("deleted", "diff", "residue", "v2_tables_after", "stray_files")}, indent=1, default=str))
        return 0
    only = [int(x) for x in a.only.split(",")] if a.only else None
    token = login()
    if a.resume:
        ctx = Ctx(a.resume, token)
        load_state(ctx)
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = os.path.join(HERE, "runs", stamp)
        os.makedirs(os.path.join(run_dir, "shots"), exist_ok=True)
        ctx = Ctx(run_dir, token)
        ctx.state["stamp"] = stamp
        ctx.state["snapshot_before"] = DB.snapshot()
        ctx.state["backup"] = DB.backup(os.path.join(BACKEND, f"local.db.bak-{stamp}-acceptance"))
        print("backup:", ctx.state["backup"], flush=True)
        if ctx.state["backup"]["integrity"] != "ok":
            print("REFUSE: backup integrity not ok")
            return 2
        before_lists(ctx)
        save_state(ctx)
    ctx.A = Api(token, ctx.ids.get("contract_api"))
    ctx.U = Api(token, ctx.ids.get("contract_ui"))
    ctx.L = Api(token, ctx.ids.get("contract_legacy"))
    cleanup_rep = None
    try:
        if not a.resume:
            setup(ctx)
            ctx.A = Api(token, ctx.ids["contract_api"])
            ctx.U = Api(token, ctx.ids["contract_ui"])
            ctx.L = Api(token, ctx.ids["contract_legacy"])
        if a.path in ("ui", "both") and "ui_tools" not in ctx.state:
            probe = ctx.ui("probe", {"cid": ctx.U.cid})
            ctx.state["ui_tools"] = probe["facts"].get("tools", [])
            ctx.state["ui_features"] = probe["facts"].get("features", {})
            print("live UI tools:", ctx.state["ui_tools"], flush=True)
            save_state(ctx)
        paths = ["api", "ui"] if a.path == "both" else [a.path]
        for path in paths:
            order = API_ORDER if path == "api" else UI_ORDER
            for num in order:
                if only and num not in only:
                    continue
                title, api_fn, ui_fn = TESTS[num]
                print(f"\n== {num:02d} {path.upper()} — {title}", flush=True)
                rec = ctx.record(num, path)
                run_record(rec, api_fn if path == "api" else ui_fn, ctx, rec)
                save_state(ctx)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        ctx.state["fatal"] = traceback.format_exc()[-3000:]
    finally:
        if not a.keep:
            cleanup_rep = cleanup(ctx)
        save_state(ctx)
        write_report(ctx, cleanup_rep, started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
