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


def issue_payment_option(cfg: NodeConfig) -> dict[str, Any]:
    """One `axfer`, one settlement, one issue.

    payTo is static and must stay static: the leaderboard aggregates by payTo, so a
    DynamicPayTo would fragment the entry across addresses, none of which would rank.
    Artist splits are a downstream sweep, not a second recipient in the group.
    See IMPLEMENTATION_PLAN.md §1.3.
    """
    return {
        "scheme": cfg.scheme,
        "network": cfg.network.caip2,
        "pay_to": cfg.pay_to,
        "price": {
            "amount": cfg.issue_micro_usdc,
            "asset": cfg.network.usdc_asa,
            "extra": payment_extra(cfg),
        },
        "max_timeout_seconds": cfg.max_timeout_seconds,
        "extra": payment_extra(cfg),
    }


def issue_route_config(cfg: NodeConfig, slug_example: str) -> dict[str, Any]:
    """Route config for the paid issue endpoint.

    The description is Bazaar listing copy — an agent deciding whether to buy reads
    this and nothing else. Write it for that reader.
    """
    return {
        "accepts": issue_payment_option(cfg),
        "description": (
            f"Buy a complete comic issue from {cfg.node_name}. One payment delivers "
            "the full issue as a self-contained encrypted reader the buyer keeps: it "
            "works offline and does not depend on this node staying online. Free "
            "scrambled page previews are available without payment at "
            "/api/v1/preview/{title}/{n}, and /api/v1/titles lists what is for sale."
        ),
        "mime_type": "application/zip",
        "extensions": declare_discovery_extension(
            input={"title": slug_example},
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "Title slug, as listed by GET /api/v1/titles."
                        ),
                    }
                },
                "required": ["title"],
            },
            output=OutputConfig(
                example={
                    "format": "zip",
                    "contents": "one image per page, in reading order",
                },
            ),
        ),
    }


def to_x402_pattern(flask_rule: str) -> str:
    """Translate a Flask route rule into an x402 route pattern.

    They use different syntax for path parameters and it fails silently:

        Flask   /api/v1/issue/<title>       or  <int:n>
        x402    /api/v1/issue/[title]

    `_parse_route_pattern` (x402_http_server_base) regex-escapes the path and only
    substitutes `[param]` and `*`. A Flask-style `<title>` survives escaping as a
    literal, so the pattern matches nothing, no route is protected, and **the paid
    endpoint serves for free with a 200**. There is no warning. Keep the Flask rule as
    the single source of truth in config and derive the x402 pattern from it.
    """
    return re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"[\1]", flask_rule)


def routes_for(cfg: NodeConfig, slug_example: str) -> dict[str, Any]:
    """Route table keyed the way the middleware expects: "<METHOD> <path>"."""
    pattern = to_x402_pattern(cfg.raw["routes"]["issue"])
    return {f"GET {pattern}": issue_route_config(cfg, slug_example)}
