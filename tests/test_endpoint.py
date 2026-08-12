"""End-to-end behaviour of the Authen endpoint, short of actual settlement.

The paid route must 402 and the free routes must not. `test_paid_route_requires_payment`
is the one that would have caught the route-pattern bug in production.

These build the app, which contacts the facilitator's /supported once.
"""

from __future__ import annotations

import base64
import json

import pytest

from .conftest import TREASURY

CHALLENGE_TAG = "x402-global-challenge"
TESTNET_CAIP2 = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="


def _challenge(client) -> dict:
    r = client.post("/api/v1/notarize", data=b"probe")
    assert r.status_code == 402
    return json.loads(base64.b64decode(r.headers["PAYMENT-REQUIRED"]))


# ---------------------------------------------------------------- free routes


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert len(body["key"]) == 64


def test_identity_is_free_and_published(client, identity):
    """The signing key must be fetchable without paying, or nobody can verify."""
    r = client.get("/api/v1/identity")
    assert r.status_code == 200
    body = r.get_json()
    assert body["publicKey"] == identity.public_hex
    assert body["algorithm"] == "ed25519"
    # One key, both ledgers - this is what lets an attestation be checked against
    # a trust registry on either chain.
    assert body["algorand"] == identity.algorand
    assert body["stellar"] == identity.stellar
    assert body["stellar"].startswith("G")


def test_verify_is_free(client):
    """Verification behind a paywall means nobody checks anything."""
    r = client.post("/api/v1/verify", json={"attestation": "not.a.real.token"})
    assert r.status_code != 402, "verification must never be paywalled"
    assert r.get_json()["valid"] is False


def test_verify_rejects_empty(client):
    r = client.post("/api/v1/verify", json={})
    assert r.status_code == 400


# ---------------------------------------------------------------- paid route


def test_paid_route_requires_payment(client):
    """The regression guard. A 200 here means attestations are being given away."""
    r = client.post("/api/v1/notarize", data=b"probe")
    assert r.status_code == 402, (
        "Paid route did not challenge. If this is 200, the x402 route pattern is not "
        "matching and the endpoint is signing attestations for free."
    )
    assert "PAYMENT-REQUIRED" in r.headers


def test_paid_route_does_not_leak_attestation_in_challenge(client):
    r = client.post("/api/v1/notarize", data=b"probe")
    assert b"attestation" not in r.data.lower()


def test_get_on_notarize_is_not_a_bypass(client):
    """The route is registered for POST. A GET must not reach an unprotected handler."""
    r = client.get("/api/v1/notarize")
    assert r.status_code != 200


# ------------------------------------------------- registration gate items


def test_challenge_declares_tag_where_the_bazaar_reads_it(client):
    """Tag must sit at accepts[].extra.tag — verified against 1200+ live resources."""
    accepts = _challenge(client)["accepts"][0]
    assert accepts["extra"]["tag"] == CHALLENGE_TAG


def test_challenge_payment_requirements(client, cfg):
    accepts = _challenge(client)["accepts"][0]
    assert accepts["payTo"] == TREASURY
    assert accepts["network"] == TESTNET_CAIP2
    assert accepts["scheme"] == "exact"
    # ASA id and amount are strings on the wire, not ints.
    assert isinstance(accepts["asset"], str)
    assert isinstance(accepts["amount"], str)
    assert accepts["amount"] == cfg.price_micro_usdc
    assert accepts["extra"]["feePayer"]


def test_challenge_version(client):
    assert _challenge(client)["x402Version"] == 2


def test_bazaar_discovery_extension_present(client):
    """No bazaar extension means the resource is never cataloged, so never discovered."""
    assert "bazaar" in json.dumps(_challenge(client))


def test_listing_copy_does_not_overclaim(client):
    """An attestation that overpromises is worse than none.

    The Bazaar description is the only thing a buying agent reads. It must not
    imply authorship, ownership, or that the content predates the timestamp.
    """
    desc = json.dumps(_challenge(client)).lower()
    for forbidden in ("proves ownership", "proves authorship", "guarantees authenticity"):
        assert forbidden not in desc


@pytest.mark.parametrize("path", ["/health", "/api/v1/identity"])
def test_payment_headers_exposed_to_browsers(client, path):
    """Without this a browser client cannot read the settlement receipt."""
    expose = client.get(path).headers.get("Access-Control-Expose-Headers", "")
    assert "PAYMENT-RESPONSE" in expose
