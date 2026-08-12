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


def notarize_payment_option(cfg: NodeConfig) -> dict[str, Any]:
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
            "amount": cfg.price_micro_usdc,
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
        "accepts": notarize_payment_option(cfg),
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
            input={"body": "<raw bytes of the content to notarize>"},
            input_schema={
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": (
                            "Raw request body - the exact bytes to hash. Any content "
                            "type. Max 32 MiB; send a digest instead for larger objects."
                        ),
                    }
                },
                "required": ["body"],
            },
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


def routes_for(cfg: NodeConfig) -> dict[str, Any]:
    """Route table keyed the way the middleware expects: "<METHOD> <path>"."""
    pattern = to_x402_pattern(cfg.raw["routes"]["notarize"])
    return {f"POST {pattern}": notarize_route_config(cfg)}
