"""HTTP helpers for the live acceptance suite (stdlib only -- runs in either
interpreter). Credentials are read from backend/.env at run time; no password
or token is ever written to disk or printed."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get("SWM_API", "http://10.31.0.58:8000")
UI_BASE = os.environ.get("SWM_UI", "http://10.31.0.58:5173")
ENV_PATH = r"C:\PTG\signwithme\backend\.env"


def env() -> dict[str, str]:
    values: dict[str, str] = {}
    with open(ENV_PATH, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def request(method, path, token=None, body=None, raw=False, data=None, content_type=None, timeout=180,
            want_headers=False):
    url = path if path.startswith("http") else API + path
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    def _decode(status, payload, hdrs):
        if raw:
            out = (status, payload)
        else:
            text = payload.decode("utf-8", "replace")
            try:
                out = (status, json.loads(text) if text else None)
            except json.JSONDecodeError:
                out = (status, text)
        return out + (dict(hdrs),) if want_headers else out

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return _decode(response.status, response.read(), response.headers)
    except urllib.error.HTTPError as exc:
        return _decode(exc.code, exc.read(), exc.headers)


def login() -> str:
    values = env()
    status, payload = request("POST", "/api/auth/login", body={
        "email": values["SEED_ADMIN_EMAIL"], "password": values["SEED_ADMIN_PASSWORD"]})
    if status != 200:
        raise SystemExit(f"login failed: HTTP {status}")
    return payload["access_token"]


def multipart(fields: dict[str, str], filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = "----swmZZSYNTHacc" + str(int(time.time() * 1000))
    out = []
    for key, value in fields.items():
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    out.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/pdf\r\n\r\n".encode())
    out.append(data)
    out.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def wait_for(path, token, key, done, bad=("failed", "error"), timeout=240):
    t0 = time.time()
    state = None
    detail = None
    while time.time() - t0 < timeout:
        _status, detail = request("GET", path, token)
        state = detail.get(key) if isinstance(detail, dict) else None
        if state in done:
            return detail
        if state in bad:
            raise RuntimeError(f"{path}: {state}")
        time.sleep(1.0)
    raise RuntimeError(f"{path}: timeout, last {state}")


class Api:
    """Bound client for one contract (document routes under `D`)."""

    def __init__(self, token: str, cid: str | None = None):
        self.token = token
        self.cid = cid

    # generic
    def get(self, path, **kw):
        return request("GET", path, self.token, **kw)

    def post(self, path, body=None, **kw):
        return request("POST", path, self.token, body=body, **kw)

    def put(self, path, body=None, **kw):
        return request("PUT", path, self.token, body=body, **kw)

    def delete(self, path, **kw):
        return request("DELETE", path, self.token, **kw)

    # document
    @property
    def D(self) -> str:
        return f"/api/contracts/{self.cid}/document"

    def document(self) -> dict:
        s, d = self.get(self.D)
        if s != 200:
            raise RuntimeError(f"GET document -> {s} {d}")
        return d

    def current_no(self) -> int:
        return self.document()["current_version"]["version_no"]

    def pages(self, no: int | None = None) -> list[dict]:
        no = no or self.current_no()
        s, p = self.get(f"{self.D}/versions/{no}/pages")
        if s != 200:
            raise RuntimeError(f"GET pages -> {s}")
        return p

    def text_model(self, uid: str, no: int | None = None) -> dict:
        no = no or self.current_no()
        s, t = self.get(f"{self.D}/versions/{no}/pages/{uid}/text")
        if s != 200:
            raise RuntimeError(f"GET text -> {s}")
        return t

    def version_pdf(self, no: int | None = None) -> bytes:
        no = no or self.current_no()
        s, b = self.get(f"{self.D}/versions/{no}/file", raw=True)
        if s != 200:
            raise RuntimeError(f"GET version file -> {s}")
        return b

    def contract(self) -> dict:
        return self.get(f"/api/contracts/{self.cid}")[1]

    def wait_idle(self, timeout=240) -> dict:
        t0 = time.time()
        c = None
        while time.time() - t0 < timeout:
            c = self.contract()
            if c.get("status") not in ("parsing", "uploaded"):
                return c
            time.sleep(1.0)
        return c

    def approve(self, note="ZZSYNTH acceptance suite - synthetic override for test only") -> int:
        self.wait_idle()
        c = self.contract()
        if c.get("review_status") == "approved":
            return 200
        _s, cmp_ = self.get(f"/api/contracts/{self.cid}/compare")
        decision = {"decision": "approved"}
        if not (isinstance(cmp_, dict) and cmp_.get("can_sign")):
            decision.update({"override_missing": True, "notes": note})
        s, _o = self.post(f"/api/review/{self.cid}", decision)
        return s

    def commit_ops(self, ops, tag, markup=None, discard=False) -> tuple[int, dict]:
        """Draft -> PUT ops -> commit. Returns (commit status, commit body)."""
        sid = f"zzacc-{tag}-sess"[:64]
        s, d = self.post(f"{self.D}/draft", {"base_version_no": self.current_no(), "client_session_id": sid})
        if s not in (200, 201):
            return s, {"stage": "draft", "body": d}
        s, put = self.put(f"{self.D}/draft", {"revision": d["revision"], "client_session_id": sid,
                                               "ops": ops, "markup": markup})
        if s != 200:
            self.delete(f"{self.D}/draft?revision={d['revision']}")
            return s, {"stage": "put", "body": put}
        body = {"revision": put["revision"], "commit_request_id": f"zzacc-{tag}-commit"[:64]}
        if discard:
            body["discard_signatures"] = True
        s, out = self.post(f"{self.D}/draft/commit", body)
        if s != 201:
            # leave nothing behind
            _s, cur = self.get(f"{self.D}/draft")
            if isinstance(cur, dict) and "revision" in cur:
                self.delete(f"{self.D}/draft?revision={cur['revision']}")
            return s, {"stage": "commit", "body": out, "warnings": put.get("warnings")}
        out["put_warnings"] = put.get("warnings")
        return s, out

    def markup(self) -> dict:
        return self.get(f"{self.D}/markup")[1]

    def put_markup(self, elements) -> tuple[int, dict]:
        rev = self.markup()["revision"]
        return self.put(f"{self.D}/markup", {"revision": rev, "elements": elements})

    def export(self, include_markup=True, flatten_forms=False, version_no=None) -> bytes:
        body = {"include_markup": include_markup, "flatten_forms": flatten_forms}
        if version_no:
            body["version_no"] = version_no
        s, ex = self.post(f"{self.D}/exports", body)
        if s != 202:
            raise RuntimeError(f"POST exports -> {s} {ex}")
        t0 = time.time()
        while ex.get("status") not in ("done", "failed") and time.time() - t0 < 120:
            time.sleep(0.5)
            ex = self.get(f"{self.D}/exports/{ex['id']}")[1]
        s, data = self.get(f"{self.D}/exports/{ex['id']}/file", raw=True)
        if s != 200:
            raise RuntimeError(f"GET export file -> {s}")
        self.delete(f"{self.D}/exports/{ex['id']}")
        return data
