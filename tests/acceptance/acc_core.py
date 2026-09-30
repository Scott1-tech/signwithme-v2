"""Run context + result recorder (backend venv)."""
from __future__ import annotations

import json
import os
import subprocess
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PYTHON = os.environ.get("SWM_UI_PYTHON", r"C:\Program Files\Python312\python.exe")

PASS, FAIL, PENDING, SKIP = "PASS", "FAIL", "PENDING", "SKIP"


class Record:
    """Checks of ONE test on ONE path (api | ui)."""

    def __init__(self, num: int, path: str):
        self.num, self.path = num, path
        self.checks: list[dict] = []
        self.pending_reasons: list[str] = []
        self.notes: list[str] = []
        self.evidence: dict = {}
        self.error: str | None = None
        self.seconds = 0.0
        self.ran = False

    def check(self, name, ok, detail=None) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"    {'PASS' if ok else 'FAIL'} [{self.num:02d}/{self.path}] {name}"
              + (f"  {json.dumps(detail, default=str)[:260]}" if detail is not None else ""), flush=True)
        return bool(ok)

    def pending(self, reason):
        self.pending_reasons.append(reason)
        print(f"    PENDING [{self.num:02d}/{self.path}] {reason}", flush=True)

    def note(self, text):
        self.notes.append(text)
        print(f"    NOTE [{self.num:02d}/{self.path}] {text}", flush=True)

    @property
    def status(self) -> str:
        if not self.ran:
            return SKIP
        if self.error or any(not c["ok"] for c in self.checks):
            return FAIL
        if self.pending_reasons and not self.checks:
            return PENDING
        if self.pending_reasons:
            return PASS + "*"  # passed, part of it still PENDING
        return PASS if self.checks else PENDING

    def as_dict(self):
        return {"num": self.num, "path": self.path, "status": self.status, "checks": self.checks,
                "pending": self.pending_reasons, "notes": self.notes, "evidence": self.evidence,
                "error": self.error, "seconds": round(self.seconds, 1)}


class Ctx:
    def __init__(self, run_dir: str, token: str):
        self.run_dir = run_dir
        self.token = token
        self.ids: dict = {}
        self.state: dict = {}  # facts shared between tests (uids, versions, ...)
        self.records: dict[tuple[int, str], Record] = {}
        self.ids_file = os.path.join(run_dir, "ids.json")

    def save_ids(self):
        json.dump(self.ids, open(self.ids_file, "w", encoding="utf-8"), indent=1)

    def record(self, num, path) -> Record:
        key = (num, path)
        if key not in self.records:
            self.records[key] = Record(num, path)
        return self.records[key]

    def path(self, name) -> str:
        return os.path.join(self.run_dir, name)

    def write(self, name, data: bytes) -> str:
        p = self.path(name)
        open(p, "wb").write(data)
        return p

    def ui(self, scenario: str, args: dict, rec: Record | None = None, timeout=900) -> dict:
        """Run one UI scenario in the system Python; its checks join `rec`."""
        args = dict(args)
        args.setdefault("shots", os.path.join(self.run_dir, "shots"))
        args.setdefault("ids_file", self.ids_file)
        args.setdefault("dir", self.run_dir)
        a = self.path(f"ui_{scenario}_args.json")
        o = self.path(f"ui_{scenario}_out.json")
        if os.path.exists(o):
            os.remove(o)
        json.dump(args, open(a, "w", encoding="utf-8"), indent=1)
        t0 = time.time()
        proc = subprocess.run([SYSTEM_PYTHON, os.path.join(HERE, "acc_ui.py"), scenario, a, o],
                              cwd=HERE, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
        log = self.path(f"ui_{scenario}.log")
        open(log, "w", encoding="utf-8").write(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
        if not os.path.exists(o):
            out = {"checks": [[f"ui scenario {scenario} produced a result", False, proc.stderr[-800:]]],
                   "pending": [], "facts": {}, "files": {}, "errors": [], "http_errors": []}
        else:
            out = json.load(open(o, encoding="utf-8"))
        # a scenario in a new ids (legacy upload) wrote ids.json itself -> reload
        try:
            self.ids.update(json.load(open(self.ids_file, encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
        if rec is not None:
            for name, ok, detail in out["checks"]:
                rec.check(name, ok, detail)
            for reason in out["pending"]:
                rec.pending(reason)
            rec.evidence.setdefault("ui_seconds", 0)
            rec.evidence["ui_seconds"] += round(time.time() - t0, 1)
            if out.get("traceback"):
                rec.evidence["ui_traceback"] = out["traceback"]
            if out.get("errors"):
                rec.evidence["page_errors"] = out["errors"][:10]
            if out.get("http_errors"):
                rec.evidence["http_errors"] = out["http_errors"][:20]
            rec.evidence["ui_log"] = log
            shots = {k: v for k, v in out.get("files", {}).items() if k.startswith("shot_")}
            if shots:
                rec.evidence["screenshots"] = shots
        return out


def run_record(rec: Record, fn, *a):
    rec.ran = True
    t0 = time.time()
    try:
        fn(*a)
    except Exception as exc:  # noqa: BLE001
        rec.error = f"{type(exc).__name__}: {str(exc)[:500]}"
        rec.evidence["traceback"] = traceback.format_exc()[-2500:]
        print(f"    ERROR [{rec.num:02d}/{rec.path}] {rec.error}", flush=True)
    rec.seconds += time.time() - t0
