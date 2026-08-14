"""Body-limit invariants across nginx, Flask, and the published Bazaar schema.

Three layers independently decide how big a request may be, and every bug this
module pins came from two of them disagreeing:

    nginx client_max_body_size   refuses BEFORE the paywall  (deploy/nginx/authen.conf)
    Flask MAX_CONTENT_LENGTH     refuses INSIDE the paid app (authen/web/app.py)
    the Bazaar input schema      what buyers are TOLD        (authen/x402/server.py)

Two real incidents, both silent:

  * AUTHEN_API_REPORT.md §1 — the schema advertised 32 MiB while nginx enforced 4.
    Every buyer in the 4-32 MiB band was told the request was fine and refused.
    The advertised number is cataloged permanently by the facilitator, so it is
    the number agents plan against.
  * AUTHEN_API_REPORT.md §5 — the paid signing route accepted 32 MiB while the
    FREE verification route inherited 4, so any image worth paying to sign
    produced an artifact too large to verify. A C2PA-signed output is always
    larger than its input, so the free route must be the more permissive one.

And the ordering that must never invert: nginx <= Flask. Flask's check runs inside
the WSGI app that payment_middleware wraps, so if nginx is the looser layer the
size check moves BEHIND the paywall — verify, settle, then 413, and the buyer pays
for nothing. Reversing these two numbers is the expensive bug.

Parses the committed vhost rather than asserting hardcoded values, so the test
tracks the config instead of a copy of it. No network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from authen.web.app import MAX_BODY_BYTES
from authen.x402.server import c2pa_route_config, notarize_route_config

VHOST = Path(__file__).resolve().parent.parent / "deploy" / "nginx" / "authen.conf"

_SUFFIX = {"k": 1024, "m": 1024**2, "g": 1024**3}


def _to_bytes(size: str) -> int:
    size = size.strip().lower()
    if size[-1] in _SUFFIX:
        return int(size[:-1]) * _SUFFIX[size[-1]]
    return int(size)


def _caps() -> dict[str, int]:
    """Map location path -> client_max_body_size in bytes.

    The vhost default (declared at server level) is keyed as "__default__".
    Brace-depth tracking is enough here: the file has no nested locations.
    """
    caps: dict[str, int] = {}
    location: str | None = None
    depth = 0

    for raw in VHOST.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        if m := re.match(r"location\s+(?:=\s*)?(\S+)\s*\{", line):
            if not m.group(1).startswith("@"):
                location = m.group(1)

        if m := re.search(r"client_max_body_size\s+(\S+?);", line):
            caps[location or "__default__"] = _to_bytes(m.group(1))

        depth += line.count("{") - line.count("}")
        if depth <= 1:
            location = None

    return caps


def _advertised_max(route_config) -> int:
    """The 'Max NN MiB' a buyer reads in the cataloged Bazaar input schema.

    Searches the whole body schema rather than one fixed key. The body was a bare
    string while `bodyType` was "text" and is an object now that the catalog
    validator requires one ("body discovery body must be an object"). The limit
    has to stay findable across that change: it is the number a buyer plans
    against and the facilitator catalogs it permanently.
    """
    schema = route_config["extensions"]["bazaar"]["schema"]
    body = schema["properties"]["input"]["properties"]["body"]
    m = re.search(r"Max\s+(\d+)\s*MiB", json.dumps(body))
    assert m, f"no advertised size anywhere in the body schema: {body!r}"
    return int(m.group(1)) * 1024**2


def test_vhost_is_parseable():
    caps = _caps()
    assert "__default__" in caps, "no server-level client_max_body_size found"
    assert len(caps) >= 3, f"expected several per-location caps, got {caps}"


def test_no_nginx_cap_exceeds_the_flask_ceiling():
    """nginx <= Flask, or the size check moves behind the paywall.

    Flask 413s at MAX_CONTENT_LENGTH regardless of what nginx allows, so a looser
    nginx cap does not grant a bigger body - it just moves the refusal to after
    verify and settle.
    """
    for location, cap in _caps().items():
        assert cap <= MAX_BODY_BYTES, (
            f"{location} allows {cap} > MAX_BODY_BYTES {MAX_BODY_BYTES}; "
            "raise MAX_BODY_BYTES in authen/web/app.py first"
        )


@pytest.mark.parametrize(
    ("path", "config_fn"),
    [
        ("/api/v1/notarize", notarize_route_config),
        ("/api/v1/c2pa/sign", c2pa_route_config),
    ],
)
def test_paid_route_enforces_exactly_what_it_advertises(path, config_fn, cfg):
    """§1: the cataloged schema and the enforced cap must be the same number."""
    caps = _caps()
    assert path in caps, f"{path} has no explicit cap and would inherit the small default"
    assert caps[path] == _advertised_max(config_fn(cfg)), (
        f"{path} enforces {caps[path]} but advertises {_advertised_max(config_fn(cfg))} "
        "in the Bazaar schema, which is cataloged permanently"
    )


def test_verifying_signed_output_is_more_permissive_than_paying_to_sign():
    """§5: a signed artifact is always larger than the input that produced it."""
    caps = _caps()
    sign = caps["/api/v1/c2pa/sign"]
    verify = caps["/api/v1/c2pa/verify"]
    assert verify > sign, (
        f"c2pa/verify accepts {verify} but c2pa/sign accepts {sign}; the node "
        "cannot read back artifacts it sells"
    )
    # Measured 24% growth on a real TIFF. 25% is the floor worth guarding.
    assert verify >= sign * 1.25, f"{verify} leaves no headroom over {sign}"


def test_free_attestation_route_was_not_widened():
    """/api/v1/verify takes a few-hundred-byte JSON attestation.

    It must NOT be raised alongside c2pa/verify. Every byte allowed on an unpaid
    route with no paywall in front of it is an unpriced allocation any caller can
    trigger, and 4 MiB is already ~8000x what an attestation needs.
    """
    caps = _caps()
    assert caps.get("/api/v1/verify") is None, (
        "/api/v1/verify has its own cap; it should inherit the small vhost default"
    )
    assert caps["__default__"] <= 4 * 1024**2


def test_raised_free_route_is_concurrency_capped():
    """48 MiB into the heap on a free route needs a bound on how many at once.

    c2pa/verify does request.get_data(cache=False) with 2 workers x 4 threads, so
    without limit_conn eight concurrent bodies is ~384 MiB nobody paid for.
    """
    text = VHOST.read_text(encoding="utf-8")
    assert "limit_conn_zone" in text, "no limit_conn_zone declared"
    block = text.split("location = /api/v1/c2pa/verify")[1].split("}")[0]
    assert "limit_conn" in block, "c2pa/verify raised to 48m with no concurrency cap"
