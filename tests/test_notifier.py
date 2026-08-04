import pytest
import requests

from amao.notifier import Notifier


def test_rejects_non_https_url():
    with pytest.raises(ValueError):
        Notifier("http://example.com/webhook")


def test_notify_without_webhook_does_not_post(monkeypatch):
    calls = []
    monkeypatch.setattr("amao.notifier.requests.post", lambda *a, **k: calls.append((a, k)))
    Notifier().notify("Title", "message")
    assert calls == []


def test_notify_posts_payload(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))

    monkeypatch.setattr("amao.notifier.requests.post", fake_post)
    Notifier("https://hooks.example.com/x").notify("Title", "message", requires_human=True)

    assert len(calls) == 1
    url, payload, timeout = calls[0]
    assert url == "https://hooks.example.com/x"
    assert "Title" in payload["text"]
    assert "STUCK" in payload["text"]


def test_notify_swallows_request_exceptions(monkeypatch):
    def raiser(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr("amao.notifier.requests.post", raiser)
    Notifier("https://hooks.example.com/x").notify("t", "m")  # must not raise
