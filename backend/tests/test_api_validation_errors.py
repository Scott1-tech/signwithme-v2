"""A refused request must not be quoted back to the client (app/main.py).

FastAPI's own 422 handler puts the rejected VALUE in every entry
(`detail[].input`), and the frontend renders that list verbatim
(`JSON.stringify(data.detail)`, frontend/src/api/client.ts:83). Left alone, the
422 for a mistyped signature placement carries the captured signature PNG and
the signer's e-mail back into the error banner and the browser console, and the
422 for a bad annotation carries whatever the user typed onto the document -- a
name, a date of birth, an SSN.

What these tests hold onto:

* No part of the submitted body comes back. The probes below plant a recognisable
  secret and a large `image_data` in the request and assert neither is in the
  response, on every endpoint that takes a captured image or free text.
* `loc` / `msg` / `type` stay, and the list shape stays: the frontend needs to
  say WHICH field failed, and `isAnnotationsUnsupported`
  (frontend/src/api/editor.ts:50) recognises an older server by the word
  "annotation" appearing in the refusal -- which only `loc` provides.
* A message our own validators write may name a single offending character
  (the standard-PDF-font check); that stays readable, and is length-bounded.
"""
import json
from uuid import uuid4

import pytest

from tests.conftest import auth

# Planted in the request body. Nothing in a 422 may contain it.
SECRET = "SSN 123-45-6789"
# Stands in for the canvas signature: big, and squarely the user's own data.
LONG_IMAGE = "data:image/png;base64," + "QUJDREVG" * 500

FORBIDDEN_KEYS = ("input", "ctx", "url")


@pytest.fixture
def contract_id() -> str:
    """Any well-formed id: body validation happens before the handler runs, so
    these refusals never reach the contract."""
    return str(uuid4())


def _detail(response) -> list:
    body = response.json()
    assert isinstance(body["detail"], list), body
    return body["detail"]


def _assert_no_echo(response) -> None:
    """Neither the planted secret nor the image may survive anywhere in the body."""
    assert SECRET not in response.text
    assert LONG_IMAGE[-200:] not in response.text
    for entry in _detail(response):
        for key in FORBIDDEN_KEYS:
            assert key not in entry, entry


def test_a_422_does_not_quote_the_submitted_body_back(api, contractor_token, contract_id):
    """The case that motivated this: an annotation the user typed on the page.

    `kind` is missing, so pydantic reports the error against the whole element
    -- which is exactly when FastAPI's default handler echoes the entire
    annotation, text and all.
    """
    response = api.post(
        f"/api/contracts/{contract_id}/finalize",
        headers=auth(contractor_token),
        json={
            "annotations": [
                {
                    "id": "a1",
                    "page": 1,
                    "x_pt": 120.5,
                    "y_pt": 640.0,
                    "text": SECRET,
                    "image_data": LONG_IMAGE,
                }
            ]
        },
    )
    assert response.status_code == 422, response.text
    _assert_no_echo(response)


def test_a_422_still_names_the_field_that_failed(api, contractor_token, contract_id):
    response = api.post(
        f"/api/contracts/{contract_id}/finalize",
        headers=auth(contractor_token),
        json={"annotations": [{"id": "a1", "page": 1, "x_pt": 1.0, "y_pt": 2.0, "text": SECRET}]},
    )
    assert response.status_code == 422, response.text
    entry = _detail(response)[0]
    assert set(entry) == {"loc", "msg", "type"}
    assert entry["loc"] == ["body", "annotations", 0, "kind"]
    assert entry["msg"] == "Field required"
    assert entry["type"] == "missing"


def test_the_frontends_older_server_detection_still_works(api, contractor_token, contract_id):
    """`isAnnotationsUnsupported` (frontend/src/api/editor.ts:50) lowercases the
    message plus details and looks for "annotation". The message it reads is
    `JSON.stringify(detail)` (client.ts:83), so the word has to survive in `loc`
    -- it is the only place left that can carry it."""
    response = api.post(
        f"/api/contracts/{contract_id}/finalize",
        headers=auth(contractor_token),
        json={"annotations": [{"id": "a1", "kind": "text", "page": 1, "x_pt": 1.0, "y_pt": 2.0}]},
    )
    assert response.status_code == 422, response.text
    haystack = json.dumps(_detail(response)).lower()
    assert "annotation" in haystack


def test_a_signature_placement_422_does_not_return_the_image(
    api, contractor_token, contract_id
):
    """The real client bug this fires on: the pad posts `image_data` with no
    `field_id` and no coordinates. The default handler answers with the image."""
    response = api.post(
        f"/api/contracts/{contract_id}/signatures",
        headers=auth(contractor_token),
        json={"image_data": LONG_IMAGE, "signer_name": f"Charles Frye {SECRET}"},
    )
    assert response.status_code == 422, response.text
    _assert_no_echo(response)
    assert _detail(response)[0]["loc"] == ["body"]


def test_a_bulk_signature_422_does_not_return_the_image(api, contractor_token, contract_id):
    response = api.post(
        f"/api/contracts/{contract_id}/signatures/bulk",
        headers=auth(contractor_token),
        json={
            "field_ids": ["p1_contractor_signature", "p1_contractor_signature"],
            "signer_name": SECRET,
            "image_data": LONG_IMAGE,
        },
    )
    assert response.status_code == 422, response.text
    _assert_no_echo(response)


def test_a_finalize_layout_422_does_not_return_the_layout_map(
    api, contractor_token, contract_id
):
    response = api.post(
        f"/api/contracts/{contract_id}/finalize",
        headers=auth(contractor_token),
        json={"layout": {"slot_offsets_pt": {"p1_contractor_signature": 9999.0}}, "notes": SECRET},
    )
    assert response.status_code == 422, response.text
    _assert_no_echo(response)
    entry = _detail(response)[0]
    assert entry["loc"] == ["body", "layout", "slot_offsets_pt"]
    assert "must be within" in entry["msg"]


def test_a_saved_signature_422_does_not_return_the_label_or_image(api, contractor_token):
    response = api.post(
        "/api/signatures",
        headers=auth(contractor_token),
        json={"label": f"{SECRET} " * 40, "image_data": LONG_IMAGE},
    )
    assert response.status_code == 422, response.text
    _assert_no_echo(response)
    assert _detail(response)[0]["loc"] == ["body", "label"]


def test_a_malformed_path_parameter_is_not_echoed(api, contractor_token):
    """Path and query parameters raise the same exception as a body; a hand-built
    URL must not come back either."""
    response = api.get(
        f"/api/contracts/not-a-uuid-{SECRET.replace(' ', '-')}", headers=auth(contractor_token)
    )
    assert response.status_code == 422, response.text
    assert SECRET not in response.text
    assert _detail(response)[0]["loc"] == ["path", "contract_id"]


def test_a_message_that_names_one_character_stays_readable(api, contractor_token, contract_id):
    """The standard PDF fonts are WinAnsi, and `AnnotationIn` refuses a character
    they cannot print by naming it. One character is the whole point of the
    message, so it is kept -- the rest of the typed text is not."""
    response = api.post(
        f"/api/contracts/{contract_id}/finalize",
        headers=auth(contractor_token),
        json={
            "annotations": [
                {
                    "id": "a1",
                    "kind": "text",
                    "page": 1,
                    "x_pt": 1.0,
                    "y_pt": 2.0,
                    "text": f"{SECRET} П",
                }
            ]
        },
    )
    assert response.status_code == 422, response.text
    message = _detail(response)[0]["msg"]
    assert "Helvetica cannot print" in message
    assert "U+041F" in message
    # The character is named; the rest of what was typed is not returned.
    assert SECRET not in response.text


def test_a_long_message_is_bounded(api, contractor_token, contract_id):
    """A validator that quotes a client-supplied field id could otherwise write
    an arbitrarily long message; `MAX_VALIDATION_MESSAGE_CHARS` caps it."""
    from app.main import MAX_VALIDATION_MESSAGE_CHARS

    field_id = "s" * 800
    response = api.post(
        f"/api/contracts/{contract_id}/signatures/bulk",
        headers=auth(contractor_token),
        json={"field_ids": [field_id, field_id], "image_data": LONG_IMAGE},
    )
    assert response.status_code == 422, response.text
    message = _detail(response)[0]["msg"]
    assert len(message) == MAX_VALIDATION_MESSAGE_CHARS
    assert message.endswith("...")
    assert LONG_IMAGE[-200:] not in response.text
