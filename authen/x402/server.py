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


def merchant_extension(cfg: NodeConfig) -> dict[str, Any]:
    """The `x402-merchant` extension — this node's public identity in the Bazaar.

    Optional, and worth declaring anyway. GoPlausible's own Flask example says it
    plainly: declare this to CONTROL name/website/logo/categories; omit it and they
    are read from the endpoint's domain metadata (OpenGraph tags, llms.txt,
    agent-card.json) instead. deploy/www/index.html carries those tags as the
    fallback, but a scrape is a guess about someone else's parser and this is not.

    `categories` is how an agent filters the catalog, so it carries what this node
    actually does plus the competition tag — the one live enriched challenge
    merchant (AlgoFile) lists `x402-global-challenge` there too.

    The v2 extension shape is `{info, schema}`: the values AND a JSON Schema that
    validates them. Same contract as the bazaar extension, and the facilitator
    validates it the same way.
    """
    return {
        "info": {
            "name": cfg.node_name,
            "website": cfg.public_url,
            "logo": f"{cfg.public_url}/logo.png",
            "categories": [
                "provenance",
                "notarization",
                "c2pa",
                "developer tools",
                "algorand",
                "x402",
                cfg.tag,
            ],
        },
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "website": {"type": "string"},
                "logo": {"type": "string"},
                "categories": {"type": "array", "items": {"type": "string"}},
            },
        },
    }


def pin_method(discovery: dict[str, Any], method: str) -> dict[str, Any]:
    """Narrow the bazaar schema's `method` enum to exactly this route's method.

    The facilitator's catalog validator is STRICTER than the SDK's. Its own
    x402 Doctor reports, against a declaration the SDK calls valid:

        Bazaar discovery extension — Present but REJECTED by the catalog
        validator (cataloging silently skips):
        bazaar.schema method enum must match the declared method

    `declare_discovery_extension` hardcodes the whole verb family —
    `["POST","PUT","PATCH"]` for a body declaration, `["GET","HEAD","DELETE"]`
    for a query one — while `enrich_declaration` injects the single real method
    into `info` at request time. `POST` is a member of that enum, so
    `validate_discovery_extension` passes and jsonschema passes. The catalog
    wants equality, not membership, so every declaration the SDK helper produces
    is dropped.

    Silent, again: challenge served, /verify valid, settlement fine, resource
    never cataloged. This is the second variant of that failure mode — see the
    `body_type` note above for the first — and it is why the Doctor is worth
    running rather than trusting the SDK's own validator.

    Narrowing to one method is also just more accurate: this route serves POST
    and nothing else.
    """
    try:
        props = discovery["bazaar"]["schema"]["properties"]["input"]["properties"]
        props["method"]["enum"] = [method]
    except (KeyError, TypeError) as exc:  # pragma: no cover - shape guard
        raise RuntimeError(
            "bazaar declaration is not the shape this function expects; the SDK "
            f"helper may have changed: {exc}"
        ) from exc
    return discovery


def with_merchant(cfg: NodeConfig, discovery: dict[str, Any]) -> dict[str, Any]:
    """Attach this node's identity to a discovery extension.

    `declare_discovery_extension()` returns `{"bazaar": {...}}`; the merchant block
    is a sibling key, not nested inside it. Every server example GoPlausible ships
    declares both together.
    """
    return {**discovery, "x402-merchant": merchant_extension(cfg)}


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
        "extensions": with_merchant(cfg, pin_method(declare_discovery_extension(
            input={
                "content_base64": "<standard base64 of the bytes to notarize>",
                "media_type": "application/pdf",
            },
            input_schema={
                "type": "object",
                "properties": {
                    "content_base64": {
                        "type": "string",
                        "description": (
                            "Standard base64 of the exact bytes to hash. The "
                            "attestation covers the DECODED bytes, so the digest "
                            "matches your original file, not this envelope. Any "
                            "content, text or binary. Max 32 MiB per request; "
                            "base64 costs ~33% in transfer, so roughly 24 MiB of "
                            "payload - to use the full 32 MiB, POST the bytes as "
                            "the raw request body with any other Content-Type, "
                            "which this route also accepts and which produces an "
                            "identical attestation. There is no digest-submission "
                            "mode: posting a hex digest attests that hex string, "
                            "not the object it came from."
                        ),
                    },
                    "media_type": {
                        "type": "string",
                        "description": (
                            "Optional. Describes the decoded bytes, not the JSON "
                            "envelope. Recorded in the attestation as `m`."
                        ),
                    },
                },
                "required": ["content_base64"],
            },
            # `json` with an object body, though the route's native form is raw
            # bytes and `text` describes that honestly. The catalog validator
            # refuses a non-object body - "body discovery body must be an object" -
            # and raw bytes have no object form. So rather than declare a shape the
            # route does not accept, notarize() genuinely takes this envelope and
            # unwraps it, and raw bytes keep working. See GoPlausible/.github#6.
            body_type="json",
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
        ), "POST")),
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
        "extensions": with_merchant(cfg, pin_method(declare_discovery_extension(
            input={
                "image_base64": "<standard base64 of the image>",
                "media_type": "image/png",
            },
            input_schema={
                "type": "object",
                "properties": {
                    "image_base64": {
                        "type": "string",
                        "description": (
                            "Standard base64 of the image. Supported: jpeg, png, "
                            "webp, tiff, avif, heic. Max 32 MiB per request; "
                            "base64 costs ~33% in transfer, so roughly 24 MiB of "
                            "image - to use the full 32 MiB, POST the raw image "
                            "bytes with an image/* Content-Type, which this route "
                            "also accepts."
                        ),
                    },
                    "media_type": {
                        "type": "string",
                        "description": (
                            "Required with image_base64. The format decides how "
                            "the manifest is embedded, and a JSON envelope leaves "
                            "no Content-Type to infer it from."
                        ),
                    },
                },
                "required": ["image_base64", "media_type"],
            },
            # See the note on notarize_route_config: object body required by the
            # catalog validator, so the route accepts this envelope for real.
            body_type="json",
            output=OutputConfig(
                example={
                    "body": "<the same image with a C2PA manifest embedded>",
                    "X-Authen-Attestation": "<b64url-sig>.<b64url-payload>",
                },
            ),
        ), "POST")),
    }


def routes_for(cfg: NodeConfig) -> dict[str, Any]:
    """Route table keyed the way the middleware expects: "<METHOD> <path>"."""
    notarize = to_x402_pattern(cfg.raw["routes"]["notarize"])
    c2pa = to_x402_pattern(cfg.raw["routes"]["c2pa_sign"])
    return {
        f"POST {notarize}": notarize_route_config(cfg),
        f"POST {c2pa}": c2pa_route_config(cfg),
    }
