"""Authentication, roles and throttling, exercised through the real routes."""
from tests.conftest import auth


def test_registration_always_creates_a_contractor(api):
    """Self-service registration must not be able to mint an admin."""
    response = api.post(
        "/api/auth/register",
        json={
            "email": "sneaky@example.com",
            "password": "password123",
            "full_name": "Sneaky",
            "role": "admin",
        },
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "contractor"


def test_login_rejects_a_wrong_password_without_confirming_the_account_exists(api, contractor_token):
    known = api.post("/api/auth/login", json={"email": "driver@example.com", "password": "wrong"})
    unknown = api.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
    assert known.status_code == unknown.status_code == 401
    assert known.json()["message"] == unknown.json()["message"]


def test_repeated_failures_are_throttled(api, contractor_token):
    """The login form is the one endpoint reachable with no credentials."""
    last = None
    for _ in range(12):
        last = api.post("/api/auth/login", json={"email": "driver@example.com", "password": "wrong"})
    assert last is not None and last.status_code == 429
    # A correct password is refused too while the lockout stands.
    blocked = api.post(
        "/api/auth/login", json={"email": "driver@example.com", "password": "password123"}
    )
    assert blocked.status_code == 429


def test_protected_routes_reject_an_absent_or_bad_token(api):
    assert api.get("/api/contracts").status_code == 401
    assert api.get("/api/contracts", headers=auth("not-a-jwt")).status_code == 401


def test_template_upload_requires_admin(api, contractor_token, blank_template_bytes):
    response = api.post(
        "/api/templates",
        headers=auth(contractor_token),
        files={"file": ("t.pdf", blank_template_bytes, "application/pdf")},
        data={"name": "Nope"},
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------
# POST /api/auth/users/{user_id}/role
# --------------------------------------------------------------------------


def _user_id_of(api, token: str) -> str:
    return api.get("/api/auth/me", headers=auth(token)).json()["id"]


def test_admin_can_change_a_users_role(api, admin_token, contractor_token):
    """Regression: a str path param reached db.get() on a UUID column -> 500."""
    from sqlalchemy import select

    from app.models.user import User

    target_id = _user_id_of(api, contractor_token)
    response = api.post(
        f"/api/auth/users/{target_id}/role",
        params={"role": "reviewer"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "reviewer"

    db = api.db_factory()
    try:
        stored = db.scalar(select(User).where(User.email == "driver@example.com"))
        assert stored.role == "reviewer"
    finally:
        db.close()


def test_role_change_rejects_a_malformed_user_id(api, admin_token):
    response = api.post(
        "/api/auth/users/not-a-uuid/role",
        params={"role": "admin"},
        headers=auth(admin_token),
    )
    assert response.status_code == 422


def test_role_change_reports_an_unknown_user_as_404(api, admin_token):
    response = api.post(
        "/api/auth/users/00000000-0000-4000-8000-000000000000/role",
        params={"role": "admin"},
        headers=auth(admin_token),
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_role_change_requires_admin(api, contractor_token):
    """A contractor must not be able to promote themselves (or anyone)."""
    own_id = _user_id_of(api, contractor_token)
    response = api.post(
        f"/api/auth/users/{own_id}/role",
        params={"role": "admin"},
        headers=auth(contractor_token),
    )
    assert response.status_code == 403
    assert api.get("/api/auth/me", headers=auth(contractor_token)).json()["role"] == "contractor"
