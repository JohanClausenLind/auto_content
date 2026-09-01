"""Web Push: VAPID keygen, signed request shape (against a fake push service), payload rules."""

from __future__ import annotations

import json

from content_factory.notifications.push import (
    action_item_payload,
    generate_vapid_keys,
    load_vapid_keys,
    send_web_push,
)


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = ""
        self.headers = {}


class FakeSession:
    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.requests: list[dict] = []

    def post(self, url, data=None, headers=None, timeout=None, **kw):
        self.requests.append({"url": url, "data": data, "headers": headers})
        return FakeResponse(self.status)


SUB = {
    "endpoint": "https://push.example/send/abc123",
    "keys": {
        # A real browser subscription shape; values are a valid P-256 point + 16-byte auth secret.
        "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
        "auth": "tBHItJI5svbpez7KI4CCXg",
    },
}


def test_keygen_and_env_roundtrip(monkeypatch) -> None:
    keys = generate_vapid_keys()
    assert len(keys["private"]) in (42, 43) and "=" not in keys["private"]
    assert len(keys["public"]) > 80 and "=" not in keys["public"]
    monkeypatch.setenv("VAPID_KEYS", json.dumps(keys))
    assert load_vapid_keys() == keys
    monkeypatch.setenv("VAPID_KEYS", "not json")
    assert load_vapid_keys() is None


def test_send_web_push_signs_with_vapid_and_encrypts(monkeypatch) -> None:
    keys = generate_vapid_keys()
    session = FakeSession()
    payload = action_item_payload(
        {
            "id": "ai_x",
            "title": "Approval needed",
            "body": "Revision abc",
            "kind": "approval_waiting",
            "deep_link": "/projects/prj_1",
        },
        "https://cf.example",
    )
    result = send_web_push(
        SUB,
        payload,
        vapid={"private": keys["private"], "public": keys["public"]},
        requests_session=session,
    )
    assert result.ok and result.status == 201
    req = session.requests[0]
    assert req["url"].startswith("https://push.example/send/")
    auth = req["headers"]["Authorization"]
    assert auth.startswith("vapid t=") and "k=" in auth
    assert req["headers"]["Content-Encoding"] == "aes128gcm"
    assert isinstance(req["data"], bytes) and b"Approval needed" not in req["data"]  # encrypted


def test_dead_subscription_is_reported_gone() -> None:
    keys = generate_vapid_keys()
    from pywebpush import WebPushException

    class GoneSession(FakeSession):
        def post(self, url, **kw):
            raise WebPushException("gone", response=FakeResponse(410))

    result = send_web_push(SUB, {"title": "x"}, vapid=keys, requests_session=GoneSession())
    assert not result.ok and result.gone


def test_payload_carries_deep_link_and_never_an_action() -> None:
    payload = action_item_payload(
        {"id": "ai_1", "title": "T", "deep_link": "/projects/p"}, "http://localhost:3000"
    )
    assert payload["url"] == "http://localhost:3000/projects/p"
    assert "approve" not in json.dumps(payload).lower()
