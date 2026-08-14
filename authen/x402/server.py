"""x402 resource server wiring.

The SDK does the protocol work. `payment_middleware` wraps the WSGI stack, buffers the
response and settles before releasing the body, so we never call /verify or /settle
ourselves (IMPLEMENTATION_PLAN.md §1.2).

Two things here are registration-gate items and worth reading carefully:

  * `extra.tag` carries `x402-global-challenge`. Verified against the live Bazaar —
    850 of 882 tagged resources put it exactly there, inside PaymentRequirements.extra.
  * `extensions["bazaar"]` is what triggers discovery registration. The middleware
    scans routes for it (`_check_if_bazaar_needed`) and attaches the extension; the
    facilitator then auto-catalogs on /verify. Drop it and the resource never appears.
  * **`body_type` is mandatory for a POST route.** `declare_discovery_extension`
    builds a *query* declaration when `body_type` is None — `method` constrained to
    `enum ["GET","HEAD","DELETE"]`, input nested under `queryParams`. At request time
    `BazaarResourceServerExtension.enrich_declaration` injects the real method into
    `info.input`, so a POST route ends up declaring `method: "POST"` against a schema
    that forbids it. The declaration is then self-invalid and the facilitator drops it
    at parse time — `parse_discovery_extension` dispatches on the presence of
    `bodyType`, routes ours to `QueryDiscoveryExtension`, and pydantic rejects
    `'POST'`. Nothing surfaces: the 402 challenge still carries a
    plausible-looking `extensions.bazaar`, /verify still returns valid, settlement
    still succeeds, and the resource is simply never cataloged.

    This cost us the Bazaar listing between 2026-08-12 and 2026-08-14. Reproduce with
    `validate_discovery_extension()` — the facilitator's own validator, which ships in
    this SDK — on the enriched declaration:

        ValidationResult(valid=False,
                         errors=["input/method: 'POST' is not one of "
                                 "['GET', 'HEAD', 'DELETE']"])

    Pinned by `tests/test_bazaar_declaration.py`. Do not infer listing from the
    challenge containing the extension; validate the enriched form.
"""

from __future__ import annotations

import re
from typing import Any

from x402.extensions.bazaar import OutputConfig, declare_discovery_extension
from x402.http import FacilitatorConfig, HTTPFacilitatorClientSync
from x402.mechanisms.avm.exact import register_exact_avm_server
from x402.server import x402ResourceServerSync

from ..config import NodeConfig


def build_server(cfg: NodeConfig) -> x402ResourceServerSync:
    """Facilitator client + AVM exact scheme, initialised against /supported."""
    facilitator = HTTPFacilitatorClientSync(
        FacilitatorConfig(url=cfg.facilitator_url, timeout=30.0)
    )
    server = x402ResourceServerSync(facilitator)
    register_exact_avm_server(server, networks=cfg.network.caip2)
    server.initialize()
    return server


def payment_extra(cfg: NodeConfig) -> dict[str, Any]:
    """The `extra` block that rides on PaymentRequirements.

    `feePayer` is pinned from config here, but the facilitator owns that address and
    can rotate it. `server.initialize()` has already fetched /supported; treat a
    mismatch as a signal to update config, not as a reason to trust the stale value.
    """
    return {
        "asset": cfg.network.usdc_asa,
        "decimals": cfg.network.usdc_decimals,
        "feePayer": cfg.network.fee_payer,
        "tag": cfg.tag,
    }


def payment_option(cfg: NodeConfig, amount: str) -> dict[str, Any]:
    """One `axfer`, one settlement, one attestation.

    payTo is static and must stay static: the leaderboard aggregates by payTo, so a
    DynamicPayTo would fragment the entry across addresses, none of which would rank.
    See IMPLEMENTATION_PLAN.md §1.3.
    """
    return {
        "scheme": cfg.scheme,
        "network": cfg.network.caip2,
        "pay_to": cfg.pay_to,
        "price": {
            "amount": amount,
            "asset": cfg.network.usdc_asa,
            "extra": payment_extra(cfg),
        },
        "max_timeout_seconds": cfg.max_timeout_seconds,
        "extra": payment_extra(cfg),
    }


def notarize_route_config(cfg: NodeConfig) -> dict[str, Any]:
    """Route config for the paid notarize endpoint.

    The description is Bazaar listing copy - an agent deciding whether to buy reads
    this and nothing else. It states the claim narrowly on purpose: an attestation
    that overpromises is worse than none, because the first counterexample discredits
    every one we ever issued.
    """
    return {
        "accepts": payment_option(cfg, cfg.price_micro_usdc),
        "description": (
            "Notarize any bytes. POST the content and receive a signed, timestamped "
            "attestation that this node observed data with that SHA-256 digest at "
            "that moment. Ed25519 detached signature in "
            "b64url(sig).b64url(payload) form, verifiable offline by anyone. "
            "Verification is free at POST /api/v1/verify; the signing key is "
            "published at GET /api/v1/identity. The attestation asserts observation "
            "and time only - not authorship, ownership, or prior existence."
        ),
        "mime_type": "application/json",
        "extensions": declare_discovery_extension(
            input="<raw bytes of the content to notarize>",
            input_schema={
                "type": "string",
                "description": (
                    "Raw request body - the exact bytes to hash. Any content "
                    "type. Max 32 MiB. The digest is computed here from the "
                    "bytes you send; there is no digest-submission mode, so "
                    "posting a hex digest attests that hex string and not the "
                    "object it was derived from."
                ),
            },
            # The body is raw bytes, not a JSON document. "text" is the closest of the
            # three permitted values (json | form-data | text) and, unlike "json",
            # does not tell a caller to wrap the payload in an envelope.
            body_type="text",
            output=OutputConfig(
                example={
                    "attestation": "<b64url-sig>.<b64url-payload>",
                    "payload": {
                        "h": "<sha256 hex>",
                        "i": "sha256-observed-at",
                        "k": "<ed25519 public key hex>",
                        "s": 1024,
                        "t": 1786000000,
                        "v": 1,
                    },
                },
            ),
        ),
    }


def to_x402_pattern(flask_rule: str) -> str:
    """Translate a Flask route rule into an x402 route pattern.

    They use different syntax for path parameters and it fails silently:

        Flask   /api/v1/notarize/<id>       or  <int:n>
        x402    /api/v1/notarize/[id]

    `_parse_route_pattern` (x402_http_server_base) regex-escapes the path and only
    substitutes `[param]` and `*`. A Flask-style `<title>` survives escaping as a
    literal, so the pattern matches nothing, no route is protected, and **the paid
    endpoint serves for free with a 200**. There is no warning. Keep the Flask rule as
    the single source of truth in config and derive the x402 pattern from it.
    """
    return re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"[\1]", flask_rule)


def c2pa_route_config(cfg: NodeConfig) -> dict[str, Any]:
    """Route config for the paid C2PA signing endpoint."""
    return {
        "accepts": payment_option(cfg, cfg.c2pa_micro_usdc),
        "description": (
            "Embed C2PA Content Credentials into an image. POST the image bytes with "
            "an image/* Content-Type and receive the same image with a signed C2PA "
            "manifest embedded, plus an Authen attestation carried inside the "
            "manifest and in the X-Authen-Attestation header. Signed by a short-lived "
            "leaf under this node's app CA; the CA fingerprint is returned in "
            "X-Authen-CA-Fingerprint. Reading a manifest back is free at "
            "POST /api/v1/c2pa/verify. NOTE: this node's CA is not on the C2PA "
            "conformance trust list, so conformant validators will report the "
            "signature as cryptographically valid but the signer as untrusted."
        ),
        "mime_type": "image/jpeg",
        "extensions": declare_discovery_extension(
            input="<raw image bytes; Content-Type must be image/*>",
            input_schema={
                "type": "string",
                "description": (
                    "Raw image bytes. Supported: jpeg, png, webp, tiff, "
                    "avif, heic. Max 32 MiB."
                ),
            },
            # See the note on notarize_route_config: raw bytes, not a JSON envelope.
            body_type="text",
            output=OutputConfig(
                example={
                    "body": "<the same image with a C2PA manifest embedded>",
                    "X-Authen-Attestation": "<b64url-sig>.<b64url-payload>",
                },
            ),
        ),
    }


def routes_for(cfg: NodeConfig) -> dict[str, Any]:
    """Route table keyed the way the middleware expects: "<METHOD> <path>"."""
    notarize = to_x402_pattern(cfg.raw["routes"]["notarize"])
    c2pa = to_x402_pattern(cfg.raw["routes"]["c2pa_sign"])
    return {
        f"POST {notarize}": notarize_route_config(cfg),
        f"POST {c2pa}": c2pa_route_config(cfg),
    }
