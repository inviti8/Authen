"""End-to-end behaviour of the Phase 1 endpoint, short of actual settlement.

The paid route must 402 and the free routes must not. `test_paid_route_requires_payment`
is the one that would have caught the route-pattern bug in production.

These build the app, which contacts the facilitator's /supported once.
"""

from __future__ import annotations

import base64
import json

import pytest

from .conftest import PAGES, PREVIEW_PAGES, SLUG, TREASURY

CHALLENGE_TAG = "x402-global-challenge"
TESTNET_CAIP2 = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="


def _challenge(client) -> dict:
    r = client.get(f"/api/v1/issue/{SLUG}")
    assert r.status_code == 402
    return json.loads(base64.b64decode(r.headers["PAYMENT-REQUIRED"]))


# ---------------------------------------------------------------- free routes


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_catalogue_is_free_and_lists_the_title(client):
    r = client.get("/api/v1/titles")
    assert r.status_code == 200
    body = r.get_json()
    assert body["payTo"] == TREASURY
    slugs = [t["slug"] for t in body["titles"]]
    assert SLUG in slugs
    entry = next(t for t in body["titles"] if t["slug"] == SLUG)
    assert entry["pages"] == PAGES
    assert entry["price"] == "$3.00"


def test_preview_pages_are_free(client):
    for n in range(1, PREVIEW_PAGES + 1):
        r = client.get(f"/api/v1/preview/{SLUG}/{n}")
        assert r.status_code == 200, f"preview page {n} should be free"
        assert r.headers["Content-Type"].startswith("image/")


def test_paywall_boundary_holds(client):
    """Pages past the preview limit are not free — that boundary IS the paywall."""
    r = client.get(f"/api/v1/preview/{SLUG}/{PREVIEW_PAGES + 1}")
    assert r.status_code == 404


def test_unknown_title_preview(client):
    assert client.get("/api/v1/preview/no-such-title/1").status_code == 404


# ---------------------------------------------------------------- paid route


def test_paid_route_requires_payment(client):
    """The regression guard. A 200 here means the issue is being given away."""
    r = client.get(f"/api/v1/issue/{SLUG}")
    assert r.status_code == 402, (
        "Paid route did not challenge. If this is 200, the x402 route pattern is not "
        "matching and the endpoint is serving paid content for free."
    )
    assert "PAYMENT-REQUIRED" in r.headers


def test_paid_route_does_not_leak_payload_in_challenge(client):
    r = client.get(f"/api/v1/issue/{SLUG}")
    assert r.content_type != "application/zip"
    assert len(r.data) < 100_000, "402 response should not carry the issue"


# ------------------------------------------------- registration gate items


def test_challenge_declares_tag_where_the_bazaar_reads_it(client):
    """Tag must sit at accepts[].extra.tag — verified against 850 live resources."""
    accepts = _challenge(client)["accepts"][0]
    assert accepts["extra"]["tag"] == CHALLENGE_TAG


def test_challenge_payment_requirements(client):
    accepts = _challenge(client)["accepts"][0]
    assert accepts["payTo"] == TREASURY
    assert accepts["network"] == TESTNET_CAIP2
    assert accepts["scheme"] == "exact"
    # ASA id and amount are strings on the wire, not ints.
    assert isinstance(accepts["asset"], str)
    assert isinstance(accepts["amount"], str)
    assert accepts["amount"] == "3000000"
    assert accepts["extra"]["feePayer"]


def test_challenge_version(client):
    assert _challenge(client)["x402Version"] == 2


def test_bazaar_discovery_extension_present(client):
    """No bazaar extension means the resource is never cataloged, so never discovered."""
    assert "bazaar" in json.dumps(_challenge(client))


@pytest.mark.parametrize("path", ["/health", "/api/v1/titles"])
def test_payment_headers_exposed_to_browsers(client, path):
    """Without this a browser client cannot read the settlement receipt."""
    expose = client.get(path).headers.get("Access-Control-Expose-Headers", "")
    assert "PAYMENT-RESPONSE" in expose
