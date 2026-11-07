"""Hosted-checkout contract. This module never charges a card.

A sandbox entitlement changes only after a signed test event. commercial_paid
cannot be set here.
"""

import hashlib
import hmac
import json
import os

from customer_platform.errors import PaymentRejected
from customer_platform.files import read_json, sha256_bytes, write_json
from customer_platform.paperbook import new_id

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def sign(secret, body):
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


class Payments:
    def __init__(self, tenant_dir, secret):
        self.path = os.path.join(tenant_dir, "payments.json")
        self.events_path = os.path.join(tenant_dir, "payment_events.json")
        self.secret = secret

    def load(self):
        return read_json(self.path, {"sessions": []})

    def start_checkout(self, tenant_id):
        body = self.load()
        session = {
            "charges": False,
            "hosted_checkout": False,
            "mode": "test_only",
            "notice": "No payment provider is configured. This is not a checkout and cannot charge a card.",
            "provider": "not_configured",
            "session_id": new_id("chk_"),
            "status": "checkout_open",
            "tenant_id": tenant_id,
        }
        body["sessions"].append(session)
        write_json(self.path, body)
        session["contract_path"] = "/membership/sandbox/" + session["session_id"]
        return session

    def apply_webhook(self, raw_body, signature, entitlements_for, persist):
        if not self.secret:
            raise PaymentRejected("webhook_secret_unconfigured")
        if not isinstance(raw_body, (bytes, bytearray)):
            raise PaymentRejected("body_must_be_bytes")
        expected = sign(self.secret, bytes(raw_body))
        provided = str(signature or "")
        if not provided.startswith("sha256=") or not hmac.compare_digest(expected, provided):
            raise PaymentRejected("bad_signature")
        try:
            event = json.loads(bytes(raw_body).decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise PaymentRejected("bad_event") from exc
        if event.get("test_only") is not True or event.get("livemode") is not False:
            raise PaymentRejected("live_mode_rejected")
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        tenant_id = str(event.get("tenant_id") or "")
        if not event_id or not tenant_id or event_type not in {
            "checkout.completed", "charge.refunded", "checkout.canceled",
        }:
            raise PaymentRejected("bad_event")
        body_hash = sha256_bytes(bytes(raw_body))
        events = read_json(self.events_path, {})
        prior = events.get(event_id)
        if prior:
            if prior["body_sha256"] != body_hash:
                raise PaymentRejected("idempotency_conflict")
            return prior["result"]
        entitlements = entitlements_for(tenant_id)
        result = _transition(entitlements, event_type, event_id)
        persist(tenant_id, entitlements)
        events[event_id] = {"body_sha256": body_hash, "result": result}
        write_json(self.events_path, events)
        return result


def _transition(entitlements, event_type, event_id):
    state = entitlements.get("sandbox_payment_state") or "none"
    if event_type == "checkout.completed":
        state = "sandbox_paid"
    elif event_type == "charge.refunded":
        state = "refunded"
    elif event_type == "checkout.canceled":
        state = "canceled"
    entitlements["sandbox_payment_state"] = state
    entitlements["commercial_paid"] = False
    entitlements["last_test_event_id"] = event_id
    entitlements["last_test_event_type"] = event_type
    return {
        "commercial_paid": False,
        "event_id": event_id,
        "event_type": event_type,
        "sandbox_payment_state": state,
        "test_only": True,
    }


def load_fixture(name):
    path = os.path.join(FIXTURE_DIR, name)
    with open(path, "rb") as handle:
        raw = handle.read()
    if b"TEST ONLY" not in raw:
        raise PaymentRejected("fixture_not_marked")
    return raw
