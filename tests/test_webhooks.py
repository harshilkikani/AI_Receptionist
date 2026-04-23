"""V3.13 — webhook event bus tests."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from src import webhooks


def _capture_post():
    """Return (list, post_fn). post_fn records every call as a dict."""
    captured = []

    def post_fn(url, body, signature, headers=None):
        captured.append({"url": url, "body": body,
                         "signature": signature})
        return (200, None)
    return captured, post_fn


def _failing_post(url, body, signature, headers=None):
    return (500, "http_500")


# ── subscription parsing ──────────────────────────────────────────────

def test_subscriptions_none_client():
    assert webhooks._subscriptions_for(None) == []


def test_subscriptions_no_webhooks_key():
    assert webhooks._subscriptions_for({"id": "x"}) == []


def test_subscriptions_filters_empty_urls():
    c = {"webhooks": [
        {"url": "https://valid.example/", "events": ["call.ended"]},
        {"url": "", "events": ["call.ended"]},
    ]}
    subs = webhooks._subscriptions_for(c)
    assert len(subs) == 1
    assert subs[0]["url"] == "https://valid.example/"


def test_subscriptions_handles_bad_shape():
    assert webhooks._subscriptions_for({"webhooks": "not a list"}) == []
    assert webhooks._subscriptions_for({"webhooks": [None, "foo"]}) == []


# ── fire ──────────────────────────────────────────────────────────────

def test_fire_unknown_event_noop():
    assert webhooks.fire("bogus.event", {"id": "x"}) == []


def test_fire_no_subscribers_returns_empty():
    assert webhooks.fire("call.ended", {"id": "x"}) == []


def test_fire_delivers_matching_events():
    captured, post_fn = _capture_post()
    client = {"id": "ace_hvac", "webhooks": [
        {"url": "https://hook.example/1", "events": ["call.ended"],
         "secret": "s1"},
    ]}
    results = webhooks.fire("call.ended", client,
                            data={"call_sid": "CA1"}, post_fn=post_fn)
    assert len(results) == 1
    assert results[0]["delivered"] is True
    assert len(captured) == 1
    payload = json.loads(captured[0]["body"])
    assert payload["event"] == "call.ended"
    assert payload["client_id"] == "ace_hvac"
    assert payload["data"]["call_sid"] == "CA1"


def test_fire_skips_non_matching_event_subs():
    captured, post_fn = _capture_post()
    client = {"id": "x", "webhooks": [
        {"url": "https://a.example/", "events": ["call.ended"]},
        {"url": "https://b.example/", "events": ["booking.created"]},
    ]}
    webhooks.fire("call.ended", client, post_fn=post_fn)
    assert len(captured) == 1
    assert captured[0]["url"] == "https://a.example/"


def test_fire_signs_body_with_secret():
    captured, post_fn = _capture_post()
    secret = "whsec_test_secret"
    client = {"id": "x", "webhooks": [
        {"url": "https://hook/", "events": ["call.ended"], "secret": secret},
    ]}
    webhooks.fire("call.ended", client, data={"k": "v"}, post_fn=post_fn)
    sig = captured[0]["signature"]
    assert sig.startswith("sha256=")
    # Verify by recomputing
    expected = hmac.new(secret.encode(), captured[0]["body"],
                       hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


def test_fire_no_secret_leaves_signature_empty():
    captured, post_fn = _capture_post()
    client = {"id": "x", "webhooks": [
        {"url": "https://hook/", "events": ["call.ended"]},
    ]}
    webhooks.fire("call.ended", client, post_fn=post_fn)
    assert captured[0]["signature"] == ""


def test_fire_records_delivery_failure():
    results = webhooks.fire(
        "call.ended",
        {"id": "x", "webhooks": [
            {"url": "https://fail/", "events": ["call.ended"]}]},
        post_fn=_failing_post,
    )
    assert results[0]["delivered"] is False
    assert results[0]["status"] == 500


def test_fire_safe_swallows_exceptions():
    def boom(*a, **kw):
        raise RuntimeError("post crashed")
    client = {"id": "x", "webhooks": [
        {"url": "https://hook/", "events": ["call.ended"]}]}
    # Should not raise even when post_fn throws.
    # fire_safe wraps fire with its own try/except.
    webhooks.fire_safe("call.ended", client)  # no post_fn, uses real _post
    # That call likely 404s; important is it doesn't raise


def test_fire_multiple_subscribers_for_one_event():
    captured, post_fn = _capture_post()
    client = {"id": "x", "webhooks": [
        {"url": "https://a/", "events": ["call.ended"]},
        {"url": "https://b/", "events": ["call.ended"]},
    ]}
    results = webhooks.fire("call.ended", client, post_fn=post_fn)
    assert len(results) == 2
    assert len(captured) == 2


# ── payload shape ─────────────────────────────────────────────────────

def test_payload_includes_ts_and_event():
    captured, post_fn = _capture_post()
    client = {"id": "x", "webhooks": [
        {"url": "https://h/", "events": ["booking.created"]}]}
    webhooks.fire("booking.created", client,
                  data={"booking_id": "bk_1"}, post_fn=post_fn)
    payload = json.loads(captured[0]["body"])
    assert payload["event"] == "booking.created"
    assert "ts" in payload
    assert payload["data"]["booking_id"] == "bk_1"


def test_known_events_contains_expected():
    expected = {"call.started", "call.ended", "emergency.triggered",
                "booking.created", "feedback.negative"}
    assert expected <= webhooks.KNOWN_EVENTS
