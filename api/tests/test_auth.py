"""Access key (decision 0087): cookie login, 401 on /api, refused /ws handshake, open health."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.auth import COOKIE, AccessKey, access_key_from_env
from api.main import create_app

KEY = "test-key-123"


@pytest.fixture
def client():
    with TestClient(create_app(fixtures=True, access=AccessKey(KEY))) as c:
        yield c


def test_health_and_status_stay_open(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/status").json() == {"required": True, "authenticated": False}


def test_api_needs_cookie(client):
    r = client.get("/api/stations")
    assert r.status_code == 401
    assert r.json() == {"detail": "access key required"}
    assert client.put("/api/settings/autonomy", json={"mode": "auto"}).status_code == 401


def test_wrong_key_rejected(client):
    r = client.post("/api/auth/login", json={"key": "nope"})
    assert r.status_code == 401
    assert COOKIE not in r.cookies
    assert client.get("/api/stations").status_code == 401


def test_login_sets_cookie_without_the_key(client):
    r = client.post("/api/auth/login", json={"key": KEY})
    assert r.status_code == 200
    header = r.headers["set-cookie"]
    assert KEY not in header
    assert "HttpOnly" in header and "SameSite=strict" in header and "Secure" not in header
    assert client.get("/api/stations").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_secure_cookie_behind_https_proxy(client):
    r = client.post("/api/auth/login", json={"key": KEY}, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in r.headers["set-cookie"]


def test_logout_clears_cookie(client):
    client.post("/api/auth/login", json={"key": KEY})
    client.post("/api/auth/logout")
    assert client.get("/api/stations").status_code == 401


def test_ws_handshake_refused_without_cookie(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/runs/anything") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_ws_handshake_passes_with_cookie(client):
    client.post("/api/auth/login", json={"key": KEY})
    # Past the auth check the route itself answers: an unknown run closes with 4404.
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/runs/nope") as ws:
            ws.receive_text()
    assert exc.value.code == 4404


def test_auth_off_by_default(monkeypatch):
    assert access_key_from_env().required is False
    with TestClient(create_app(fixtures=True)) as c:
        assert c.get("/api/stations").status_code == 200
        assert c.get("/api/auth/status").json() == {"required": False, "authenticated": True}


def test_env_key_and_generated_key(monkeypatch):
    monkeypatch.setenv("PPC_ACCESS_KEY", "from-env")
    assert access_key_from_env().key == "from-env"
    monkeypatch.delenv("PPC_ACCESS_KEY")
    monkeypatch.setenv("PPC_AUTH", "on")
    a, b = access_key_from_env(), access_key_from_env()
    assert a.required and len(a.key) >= 40 and a.key != b.key
