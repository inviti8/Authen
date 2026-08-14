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

from pathlib import Path

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


def test_schema_method_enum_equals_the_declared_method(cfg):
    """The catalog validator wants equality, not membership.

    `declare_discovery_extension` emits the whole verb family - POST/PUT/PATCH for
    a body declaration - and `enrich_declaration` injects one real method. POST is
    a MEMBER of that enum, so `validate_discovery_extension` returns valid and
    jsonschema is satisfied. The facilitator's catalog validator is stricter, and
    its x402 Doctor says so directly:

        bazaar.schema method enum must match the declared method

    That mismatch silently skips cataloging while every other signal - challenge,
    /verify, settlement - stays green. Membership passing our own tools while
    equality is what actually matters is precisely why this test asserts equality.
    """
    for key, method, enriched in _declarations(cfg):
        enum = enriched["schema"]["properties"]["input"]["properties"]["method"]["enum"]
        assert enum == [method], (
            f"{key}: schema enum {enum} != [{method!r}]. The SDK's default verb "
            "family is rejected by the catalog; pin_method() must narrow it."
        )


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


def test_every_route_declares_the_merchant_identity(cfg):
    """`x402-merchant` is what CONTROLS name/website/logo/categories in the Bazaar.

    Omit it and the facilitator falls back to scraping the endpoint's domain -
    OpenGraph tags, llms.txt, agent-card.json. deploy/www/index.html carries those
    as a backstop, but a scrape is a guess about someone else's parser. Declaring
    this is the deterministic path, and every server example GoPlausible ships
    declares it alongside the bazaar extension.
    """
    for key, route in routes_for(cfg).items():
        ext = route["extensions"]
        assert "x402-merchant" in ext, f"{key} declares no merchant identity"
        assert "bazaar" in ext, f"{key} lost its bazaar extension"


def test_merchant_info_validates_against_its_own_schema(cfg):
    """v2 extensions carry both the values and a schema for them; they must agree.

    This is the same self-consistency the bazaar extension needs, and the same
    failure mode - a declaration whose info contradicts its schema is dropped at
    parse time with nothing surfaced on the wire.
    """
    import jsonschema

    for key, route in routes_for(cfg).items():
        m = route["extensions"]["x402-merchant"]
        jsonschema.validate(instance=m["info"], schema=m["schema"])


def test_merchant_urls_are_absolute_and_match_the_public_origin(cfg):
    """A relative logo is useless to a facilitator resolving it out of context."""
    for key, route in routes_for(cfg).items():
        info = route["extensions"]["x402-merchant"]["info"]
        for field in ("website", "logo"):
            assert info[field].startswith("https://"), f"{key}: {field} not absolute"
            assert info[field].startswith(cfg.public_url), (
                f"{key}: {field} does not sit under public_url {cfg.public_url}"
            )


def test_merchant_categories_carry_the_competition_tag(cfg):
    """Categories are how an agent filters the catalog.

    The one live enriched challenge merchant lists the competition tag among its
    categories, so we do too - it costs nothing and it is how a judge or an agent
    finds challenge entries.
    """
    for key, route in routes_for(cfg).items():
        cats = route["extensions"]["x402-merchant"]["info"]["categories"]
        assert cfg.tag in cats, f"{key}: {cfg.tag!r} missing from {cats}"
        assert len(set(cats)) == len(cats), f"{key}: duplicate categories"


def test_body_declaration_is_an_object(cfg):
    """The catalog validator refuses a non-object body.

    Verbatim from GoPlausible's x402 Doctor, against a declaration the SDK's own
    validator called valid:

        Bazaar discovery extension - Present but REJECTED by the catalog
        validator (cataloging silently skips): body discovery body must be an
        object

    A bare string body is what `body_type="text"` produces and is the honest
    description of a raw-bytes endpoint, but it is uncataloguable. The routes
    accept a JSON envelope for real so the object declaration is true rather than
    aspirational - see the handlers in authen/web/app.py.
    """
    for key, method, enriched in _declarations(cfg):
        if method not in ("POST", "PUT", "PATCH"):
            continue
        body = enriched["info"]["input"]["body"]
        assert isinstance(body, dict), f"{key}: body is {type(body).__name__}, not an object"
        assert body, f"{key}: body object is empty, so it documents nothing"
        body_schema = enriched["schema"]["properties"]["input"]["properties"]["body"]
        assert body_schema.get("type") == "object", (
            f"{key}: body schema declares {body_schema.get('type')!r}, not 'object'"
        )


def test_declared_body_fields_are_the_ones_the_handler_reads(cfg):
    """The declaration and the handler must name the same fields.

    A rename on one side alone is silent: the catalog would advertise a field the
    route ignores, and a caller following the published schema would get a 400 -
    or worse, an attestation over something they did not intend.
    """
    source = (
        Path(__file__).resolve().parent.parent / "authen" / "web" / "app.py"
    ).read_text(encoding="utf-8")
    for key, method, enriched in _declarations(cfg):
        if method not in ("POST", "PUT", "PATCH"):
            continue
        required = enriched["schema"]["properties"]["input"]["properties"]["body"].get(
            "required", []
        )
        assert required, f"{key}: body schema requires nothing"
        for field in required:
            assert f'"{field}"' in source, (
                f"{key}: schema requires {field!r} but app.py never reads it"
            )


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
