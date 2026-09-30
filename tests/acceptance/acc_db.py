"""Database / storage guard for the live acceptance run (read-only, except the
online backup it WRITES next to the live DB and the one guarded purge of
soft-deleted ZZSYNTH gallery rows that no API route can hard-delete)."""
from __future__ import annotations

import json
import os
import sqlite3

DB = r"C:\PTG\signwithme\backend\local.db"
STORAGE = r"C:\PTG\signwithme\backend\storage"
V2_TABLES = ("documents", "document_versions", "document_pages", "document_drafts", "assets",
             "export_jobs", "share_links", "form_submissions")
# Tables whose count may legitimately grow: the audit trail keeps every
# action of the run (it is the evidence), nothing else may differ.
ALLOWED_GROWTH = ("audit_logs",)


def counts(path: str = DB) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()


def storage_counts() -> dict:
    files = size = versions = exports = 0
    for root, _d, names in os.walk(STORAGE):
        for n in names:
            files += 1
            size += os.path.getsize(os.path.join(root, n))
            if n.startswith("version-") and n.endswith(".pdf"):
                versions += 1
            if n.startswith("export"):
                exports += 1
    return {"files": files, "bytes": size, "version_pdfs": versions, "export_files": exports}


def backup(dst: str) -> dict:
    if os.path.exists(dst):
        raise SystemExit(f"REFUSE: backup already exists: {dst}")
    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    integrity = con.execute("PRAGMA integrity_check").fetchall()
    con.close()
    return {"path": dst, "bytes": os.path.getsize(dst), "integrity": integrity[0][0] if integrity else None}


def snapshot() -> dict:
    return {"db": counts(), "storage": storage_counts()}


def diff(before: dict, after: dict) -> dict:
    db = {k: [before["db"].get(k), v] for k, v in after["db"].items() if before["db"].get(k) != v}
    unexpected = {k: v for k, v in db.items() if k not in ALLOWED_GROWTH}
    st = {k: [before["storage"][k], after["storage"][k]] for k in after["storage"]
          if before["storage"][k] != after["storage"][k]}
    return {"db_changed": db, "db_unexpected": unexpected, "storage_changed": st,
            "equal": not unexpected and not st}


def residue(ids: dict) -> dict:
    """ZZSYNTH leftovers anywhere in the v2 tables (and by marker in the old ones)."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        q = lambda sql, a=(): con.execute(sql, a).fetchone()[0]  # noqa: E731
        cids = [v for k, v in ids.items() if k.startswith("contract") and isinstance(v, str)]
        keys = [c.replace("-", "") for c in cids] + cids
        marks = ",".join("?" * len(keys)) or "''"
        out = {
            "contracts ZZSYNTH-ACC": q("SELECT count(*) FROM contracts WHERE contractor_name LIKE 'ZZSYNTH-ACC%'"),
            "templates ZZSYNTH-ACC": q("SELECT count(*) FROM templates WHERE name LIKE 'ZZSYNTH-ACC%'"),
            "documents (run contracts)": q(f"SELECT count(*) FROM documents WHERE contract_id IN ({marks})", keys),
            "document_versions orphan": q("SELECT count(*) FROM document_versions WHERE document_id NOT IN (SELECT id FROM documents)"),
            "document_pages orphan": q("SELECT count(*) FROM document_pages WHERE version_id NOT IN (SELECT id FROM document_versions)"),
            "document_drafts orphan": q("SELECT count(*) FROM document_drafts WHERE document_id NOT IN (SELECT id FROM documents)"),
            "export_jobs orphan": q("SELECT count(*) FROM export_jobs WHERE document_id NOT IN (SELECT id FROM documents)"),
            "share_links (run contracts)": q(f"SELECT count(*) FROM share_links WHERE contract_id IN ({marks})", keys),
            "form_submissions orphan": q("SELECT count(*) FROM form_submissions WHERE share_link_id NOT IN (SELECT id FROM share_links)"),
            "assets (run contracts)": q(f"SELECT count(*) FROM assets WHERE contract_id IN ({marks})", keys),
            "assets user ZZ label": q("SELECT count(*) FROM assets WHERE label LIKE 'zzacc%'"),
            "signatures (run contracts)": q(f"SELECT count(*) FROM signatures WHERE contract_id IN ({marks})", keys),
        }
        return out
    finally:
        con.close()


def purge_soft_deleted_gallery(label_prefix: str = "zzacc") -> dict:
    """The gallery DELETE route only soft-deletes (api/assets.py). Remove
    EXACTLY the rows this run made: scope user, our label prefix, deleted_at
    set, and their bytes already gone from storage. Anything else: untouched."""
    con = sqlite3.connect(DB, timeout=15)
    con.execute("PRAGMA busy_timeout=15000")
    try:
        where = "scope='user' AND label LIKE ? AND deleted_at IS NOT NULL"
        rows = con.execute(f"SELECT storage_key FROM assets WHERE {where}", (label_prefix + "%",)).fetchall()
        present = [r for r in rows if r[0] and os.path.exists(os.path.join(STORAGE, *r[0].split("/")))]
        deleted = 0
        if rows and not present:
            with con:
                deleted = con.execute(f"DELETE FROM assets WHERE {where}", (label_prefix + "%",)).rowcount
        return {"soft_deleted_rows": len(rows), "bytes_still_on_disk": len(present), "deleted": deleted,
                "quick_check": con.execute("PRAGMA quick_check").fetchone()[0]}
    finally:
        con.close()


def stray_files(ids: dict) -> list[str]:
    keys = set()
    for v in ids.values():
        if isinstance(v, str) and len(v) >= 32:
            keys.add(v.replace("-", ""))
            keys.add(v)
    return [os.path.join(r, n) for r, _d, ns in os.walk(STORAGE) for n in ns
            if any(k in r or k in n for k in keys)]


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1))
