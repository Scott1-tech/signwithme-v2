"""Security review of 2026-11-09: the four fixes, each pinned by a test.

1. A contract addressed by e-mail opens only for a VERIFIED account.
2. The share-link network check and throttle see the real client behind a
   trusted proxy (and nobody else's X-Forwarded-For).
3. The login throttle cannot be dodged by forging X-Forwarded-For, and one
   e-mail has a limit of its own.
4. The example PII key / admin password are refused outside debug.
"""
from __future__ import annotations

import pytest
from starlette.requests import Request

from app.config import settings
from app.core.security import client_ip
from tests.conftest import _register, auth
from tests.share_helpers import client_at

NGINX = "172.28.0.10"


def _request(peer: str | None, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    scope = {"type": "http", "headers": headers, "client": (peer, 40000) if peer else None}
    return Request(scope)


def _addressed_contract(api, admin_token, ready_template, pdf: bytes, email: str) -> str:
    response = api.post(
        "/api/contracts",
        headers=auth(admin_token),
        files={"file": ("c.pdf", pdf, "application/pdf")},
        data={"template_id": ready_template, "contractor_email": email},
    )
    assert response.status_code == 202, response.text
    return response.json()["contract_id"]


def _user_id(api, token: str) -> str:
    return api.get("/api/auth/me", headers=auth(token)).json()["id"]


# -- 1. access by e-mail needs a verified address ------------------------------


def test_registering_a_drivers_email_does_not_open_their_contract(
    api, admin_token, ready_template, filled_contract_bytes
):
    contract = _addressed_contract(
        api, admin_token, ready_template, filled_contract_bytes, "victim@example.com"
    )
    squatter = _register(api, "victim@example.com")
    assert api.get("/api/auth/me", headers=auth(squatter)).json()["email_verified"] is False
    assert api.get(f"/api/contracts/{contract}", headers=auth(squatter)).status_code == 403
    assert api.get(f"/api/download/{contract}/final", headers=auth(squatter)).status_code == 403
    listed = api.get("/api/contracts", headers=auth(squatter))
    assert listed.status_code == 200 and contract not in listed.text


def test_an_admin_verifies_the_email_and_the_contract_opens(
    api, admin_token, ready_template, filled_contract_bytes
):
    contract = _addressed_contract(
        api, admin_token, ready_template, filled_contract_bytes, "pat@example.com"
    )
    pat = _register(api, "pat@example.com")
    url = f"/api/auth/users/{_user_id(api, pat)}/verify-email"
    assert api.post(url, headers=auth(pat)).status_code == 403  # not by themselves
    verified = api.post(url, headers=auth(admin_token))
    assert verified.status_code == 200 and verified.json()["email_verified"] is True
    assert api.get(f"/api/contracts/{contract}", headers=auth(pat)).status_code == 200
    # ...and it can be withdrawn again.
    assert api.post(url, params={"verified": "false"}, headers=auth(admin_token)).status_code == 200
    assert api.get(f"/api/contracts/{contract}", headers=auth(pat)).status_code == 403


def test_an_uploader_still_sees_their_own_contract_unverified(api, ready_template, filled_contract_bytes):
    token = _register(api, "self@example.com")
    response = api.post(
        "/api/contracts",
        headers=auth(token),
        files={"file": ("c.pdf", filled_contract_bytes, "application/pdf")},
        data={"template_id": ready_template},
    )
    assert response.status_code == 202, response.text
    own = response.json()["contract_id"]
    assert api.get(f"/api/contracts/{own}", headers=auth(token)).status_code == 200


def test_admin_made_accounts_are_verified_and_registration_can_be_closed(
    api, admin_token, monkeypatch
):
    made = api.post(
        "/api/auth/users",
        headers=auth(admin_token),
        json={"email": "New.Driver@Example.com", "password": "password123", "role": "contractor"},
    )
    assert made.status_code == 201, made.text
    assert made.json()["email"] == "new.driver@example.com" and made.json()["email_verified"] is True
    assert "new.driver@example.com" in api.get("/api/auth/users", headers=auth(admin_token)).text

    monkeypatch.setattr(settings, "allow_self_registration", False)
    closed = api.post(
        "/api/auth/register", json={"email": "walkin@example.com", "password": "password123"}
    )
    assert closed.status_code == 403
    login = api.post(
        "/api/auth/login", json={"email": "new.driver@example.com", "password": "password123"}
    )
    assert login.status_code == 200


def test_account_admin_routes_require_admin(api, contractor_token):
    assert api.get("/api/auth/users", headers=auth(contractor_token)).status_code == 403
    made = api.post(
        "/api/auth/users",
        headers=auth(contractor_token),
        json={"email": "x@example.com", "password": "password123", "role": "admin"},
    )
    assert made.status_code == 403


def test_role_change_rejects_an_unknown_role_and_keeps_the_last_admin(api, admin_token):
    admin_id = _user_id(api, admin_token)
    unknown = api.post(
        f"/api/auth/users/{admin_id}/role", params={"role": "owner"}, headers=auth(admin_token)
    )
    assert unknown.status_code == 422
    demote = api.post(
        f"/api/auth/users/{admin_id}/role", params={"role": "reviewer"}, headers=auth(admin_token)
    )
    assert demote.status_code == 409
    assert api.get("/api/auth/me", headers=auth(admin_token)).json()["role"] == "admin"


# -- 2 + 3. whose address is it ------------------------------------------------


def test_forwarded_for_is_ignored_unless_the_peer_is_a_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "")
    assert client_ip(_request("203.0.113.5", "10.0.0.1")) == "203.0.113.5"
    assert client_ip(_request(NGINX, "10.0.0.1")) == NGINX

    monkeypatch.setattr(settings, "trusted_proxy_cidrs", f"{NGINX}/32")
    # An outsider who reaches the API directly is not believed.
    assert client_ip(_request("203.0.113.5", "10.0.0.1")) == "203.0.113.5"
    # Through nginx: the entry nginx appended (the rightmost) is the client;
    # anything to its left is whatever the client typed.
    assert client_ip(_request(NGINX, "198.51.100.7")) == "198.51.100.7"
    assert client_ip(_request(NGINX, "10.0.0.1, 198.51.100.7")) == "198.51.100.7"
    assert client_ip(_request(NGINX, "not-an-ip")) == NGINX
    assert client_ip(_request(NGINX)) == NGINX
    assert client_ip(_request(None, "10.0.0.1")) is None

    # Two proxies of ours in a row: skip both.
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", f"{NGINX}/32, 172.28.0.20")
    assert client_ip(_request(NGINX, "1.2.3.4, 198.51.100.7, 172.28.0.20")) == "198.51.100.7"


def test_login_throttle_is_not_dodged_by_a_forged_forwarded_for(api, contractor_token):
    last = None
    for attempt in range(12):
        last = api.post(
            "/api/auth/login",
            json={"email": "driver@example.com", "password": "wrong"},
            headers={"X-Forwarded-For": f"10.9.8.{attempt}"},
        )
    assert last is not None and last.status_code == 429


def test_one_email_has_a_limit_across_addresses(api, contractor_token, monkeypatch):
    monkeypatch.setattr(settings, "login_email_max_attempts", 5)
    codes = [
        client_at(api, f"198.51.100.{n}")
        .post("/api/auth/login", json={"email": "driver@example.com", "password": "wrong"})
        .status_code
        for n in range(1, 8)
    ]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]


def test_share_gate_sees_the_real_client_behind_the_proxy(api, monkeypatch):
    """Behind nginx every request arrives from the nginx container. Without a
    trusted proxy that private address passed the LAN check for everyone."""
    from app.core.throttle import share_throttle

    monkeypatch.setattr(settings, "trusted_proxy_cidrs", f"{NGINX}/32")
    proxy = client_at(api, NGINX)
    bad = {"X-Share-Token": "x" * 43}

    outside = proxy.get("/api/share/session", headers={**bad, "X-Forwarded-For": "203.0.113.5"})
    assert outside.status_code == 404
    # Refused at the network check: not counted as a token failure at all...
    assert share_throttle.is_locked("203.0.113.5") == 0

    monkeypatch.setattr(settings, "share_fail_limit", 3)
    for _ in range(3):
        proxy.get("/api/share/session", headers={**bad, "X-Forwarded-For": "10.31.0.66"})
    # ...and a LAN client's bad tokens lock THAT client, not nginx and so not
    # every other visitor.
    assert share_throttle.is_locked("10.31.0.66") > 0
    assert share_throttle.is_locked(NGINX) == 0
    other = proxy.get("/api/share/session", headers={**bad, "X-Forwarded-For": "10.31.0.67"})
    assert other.status_code == 404  # an unknown token, but not a 429


# -- 4. example secrets ----------------------------------------------------------


def _load_settings(monkeypatch, **env):
    from app.config import get_settings

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return get_settings.__wrapped__()


def test_the_example_pii_key_is_refused_outside_debug(monkeypatch):
    with pytest.raises(RuntimeError, match="PII_ENCRYPTION_KEY"):
        _load_settings(
            monkeypatch,
            DEBUG="false",
            JWT_SECRET="a-real-secret-for-this-test",
            PII_ENCRYPTION_KEY="change-me-to-a-second-long-random-string",
        )
    # Unset still falls back to JWT_SECRET; a real value is accepted.
    assert _load_settings(
        monkeypatch, DEBUG="false", JWT_SECRET="a-real-secret-for-this-test", PII_ENCRYPTION_KEY=""
    )
    assert _load_settings(
        monkeypatch, DEBUG="false", JWT_SECRET="a-real-secret-for-this-test", PII_ENCRYPTION_KEY="k" * 40
    )


def test_seed_refuses_the_example_admin_password_outside_debug(monkeypatch, capsys):
    from app import seed

    monkeypatch.setenv("SEED_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "change-me-admin")
    monkeypatch.setattr(seed.settings, "debug", False)
    # Returns before a database session is opened.
    monkeypatch.setattr(seed, "SessionLocal", lambda: pytest.fail("no session expected"))
    assert seed.main() == 1
    assert "example value" in capsys.readouterr().err
