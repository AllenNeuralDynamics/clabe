"""The token in a browser: a sign-in sets a cookie, and a ?token= link is swapped for one."""

from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient

from clabe import ui
from clabe.server import create_app
from clabe.server._proxy import cookie_name
from clabe.workflow import Run, Workflow


def _client(token: str | None = "s3cret") -> TestClient:
    wf = Workflow("demo")

    @wf.step
    def notes():
        return ui.text("Notes")

    return TestClient(create_app(Run(wf), token=token, static_dir=None), base_url="http://rig-3:24070")


def test_the_cookie_is_named_after_the_port():
    assert cookie_name("rig-3:24070") == "clabe_token_24070"
    assert cookie_name("hub") == "clabe_token_80"
    assert cookie_name(None) == "clabe_token_80"


def test_signing_in_sets_a_cookie_the_api_then_accepts():
    client = _client()
    assert client.get("/api/state").status_code == 401
    refused = client.post("/api/login", json={"token": "wrong"})
    assert refused.status_code == 401 and not refused.cookies
    signed = client.post("/api/login", json={"token": " s3cret "})
    assert signed.status_code == 200 and signed.json()["required"] is True
    assert "httponly" in signed.headers["set-cookie"].lower()
    assert client.cookies.get("clabe_token_24070") == "s3cret"
    assert client.get("/api/state").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/state").status_code == 401


def test_a_link_with_the_token_is_swapped_for_the_cookie():
    client = _client()
    response = client.get("/?token=s3cret&tab=reports", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "./?tab=reports"
    assert "clabe_token_24070=s3cret" in response.headers["set-cookie"]
    # a wrong token is left alone: the page asks for it
    assert client.get("/?token=nope", follow_redirects=False).status_code != 303
    # API calls keep accepting ?token= and the header (scripts, RunClient)
    fresh = _client()
    assert fresh.get("/api/state?token=s3cret").status_code == 200
    assert fresh.get("/api/state", headers={"Authorization": "Bearer s3cret"}).status_code == 200


@pytest.mark.parametrize(
    ("asked", "app_path", "lands"),
    [
        ("https://vm/clabe/?token=s3cret", "/?token=s3cret", "https://vm/clabe/"),
        (
            "https://vm/clabe/rigs/rig-3/?token=s3cret&a=1",
            "/rigs/rig-3/?token=s3cret&a=1",
            "https://vm/clabe/rigs/rig-3/?a=1",
        ),
        (
            "https://vm/clabe/rigs/rig-3/run?token=s3cret",
            "/rigs/rig-3/run?token=s3cret",
            "https://vm/clabe/rigs/rig-3/run",
        ),
        ("http://rig-3:24070/?token=s3cret", "/?token=s3cret", "http://rig-3:24070/"),
    ],
)
def test_a_link_with_the_token_stays_under_a_proxys_prefix(asked: str, app_path: str, lands: str):
    """A proxy that strips a prefix (Traefik's StripPrefix) passes the Location back as it is: it must be relative."""
    response = _client().get(app_path, follow_redirects=False)
    assert response.status_code == 303
    assert urljoin(asked, response.headers["location"]) == lands


def test_without_a_token_signing_in_says_none_is_needed():
    client = _client(token=None)
    assert client.post("/api/login", json={}).json() == {"ok": True, "required": False}
    assert client.get("/?token=x", follow_redirects=False).status_code != 303
