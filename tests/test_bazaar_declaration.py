"""Regression tests for the Bazaar discovery declaration.

Second only to `test_route_patterns.py` in value, and it exists for the same reason:
a wrong declaration fails **silently and completely**. The 402 challenge still carries
a plausible `extensions.bazaar`, /verify still returns valid, settlement still
succeeds, money still moves — and the resource is simply never cataloged. There is no
error anywhere on the wire. The only way to see it is to run the facilitator's own
validator over the declaration, which is what these tests do.

The specific bug this pins: `declare_discovery_extension` builds a *query* shape when
`body_type` is None, constraining `method` to GET/HEAD/DELETE. At request time the
server extension injects the route's real method into `info.input`, so a POST route
declares `method: "POST"` against a schema that forbids it. Live between 2026-08-12
and 2026-08-14; cost us the Bazaar listing and a 09-01 gate item.

`validate_discovery_extension` and `enrich_declaration` are the facilitator's and the
middleware's own code, imported from the SDK rather than reimplemented, so these
tests track the library instead of our assumptions about it. No network needed.
"""

from __future__ import annotations

import pytest
from x402.extensions.bazaar import validate_discovery_extension
from x402.extensions.bazaar.server import bazaar_resource_server_extension
from x402.extensions.bazaar.types import parse_discovery_extension

from authen.x402.server import routes_for


class _Ctx:
    """Stand-in for the Flask request context `enrich_declaration` reads."""

    def __init__(self, method: str) -> None:
        self.method = method


def _declarations(cfg):
    """(route_key, method, enriched_declaration) for every paid route."""
    out = []
    for key, route in routes_for(cfg).items():
        method = key.split(" ", 1)[0]
        bazaar = route["extensions"]["bazaar"]
        enriched = bazaar_resource_server_extension.enrich_declaration(
            bazaar, _Ctx(method)
        )
        out.append((key, method, enriched))
    return out


def test_every_route_declares_bazaar(cfg):
    routes = routes_for(cfg)
    assert routes, "no paid routes configured"
    for key, route in routes.items():
        assert "bazaar" in (route.get("extensions") or {}), f"{key} declares no bazaar"


def test_enriched_declaration_validates(cfg):
    """The wire form must pass the facilitator's validator, not just the declared form.

    Validating the pre-enrichment declaration is what hid the bug: it passes, because
    the offending `method` is not in it yet.
    """
    for key, _method, enriched in _declarations(cfg):
        result = validate_discovery_extension(enriched)
        assert result.valid, f"{key}: {result.errors}"


def test_declared_method_matches_route(cfg):
    for key, method, enriched in _declarations(cfg):
        assert enriched["info"]["input"]["method"] == method, key


def test_post_routes_declare_a_body_not_query_params(cfg):
    """A POST route must produce the body shape, or it is dropped at parse time.

    `parse_discovery_extension` dispatches on the presence of `bodyType` alone. Without
    it a POST declaration is parsed as a query extension and rejected by pydantic.
    """
    for key, method, enriched in _declarations(cfg):
        if method not in ("POST", "PUT", "PATCH"):
            continue
        input_ = enriched["info"]["input"]
        assert "bodyType" in input_, f"{key}: no bodyType - will parse as a query shape"
        assert "queryParams" not in input_, f"{key}: body sent as queryParams"
        parsed = parse_discovery_extension(enriched)
        assert type(parsed).__name__ == "BodyDiscoveryExtension", key


def test_query_shape_on_a_post_route_is_rejected(cfg):
    """Prove the failure mode this module exists to prevent.

    If this test ever fails, the SDK has loosened its validation and the guarantee
    above is weaker than it looks - investigate before relaxing anything.
    """
    from x402.extensions.bazaar import declare_discovery_extension

    bad = declare_discovery_extension(
        input={"body": "<raw bytes>"},
        input_schema={"type": "object", "properties": {"body": {"type": "string"}}},
    )["bazaar"]  # note: no body_type - this is the bug, verbatim

    assert validate_discovery_extension(bad).valid, "pre-enrichment it looks fine"

    enriched = bazaar_resource_server_extension.enrich_declaration(bad, _Ctx("POST"))
    result = validate_discovery_extension(enriched)
    assert not result.valid
    assert any("method" in e for e in result.errors), result.errors

    with pytest.raises(Exception):
        parse_discovery_extension(enriched)
