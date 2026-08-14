"""The 402 challenge must fit in nginx's response-header buffer.

x402 v2 carries the entire PaymentRequired document — accepts, description, and
both extension declarations with their JSON Schemas — as base64 in the
PAYMENT-REQUIRED **response header**. Response headers are bounded by nginx's
`proxy_buffer_size`, whose default is 4k.

On 2026-08-14 a commit that only added prose to a schema description pushed the
header to 4,396 bytes and took both paid routes down. The failure mode is
exceptionally hard to read:

  * gunicorn logs `POST /api/v1/notarize 402 2 1ms` — a clean, fast, correct
    challenge. The application is not at fault and its logs say so.
  * nginx answers 502 and never forwards the response. "upstream sent too big
    header" appears only in nginx's error log, which nobody greps when the app
    log looks healthy.
  * Free routes keep working, because their headers are small. So the service
    looks half-alive in a way that suggests a worker crash rather than a proxy
    limit.
  * `proxy_buffering off` is set on this vhost and does NOT help: that governs
    the response body. proxy_buffer_size bounds the header either way.

The header only ever grows — every route description, every extension, every
schema field adds to it. This test measures the real challenge against the real
configured buffer so that growth fails here rather than in production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

VHOST = Path(__file__).resolve().parent.parent / "deploy" / "nginx" / "authen.conf"

_SUFFIX = {"k": 1024, "m": 1024**2}


def _proxy_buffer_size() -> int:
    """The configured proxy_buffer_size in bytes, or nginx's 4k default."""
    for raw in VHOST.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        m = re.match(r"proxy_buffer_size\s+(\d+)([kKmM]?)\s*;", line)
        if m:
            return int(m.group(1)) * _SUFFIX.get(m.group(2).lower(), 1)
    return 4096


@pytest.mark.parametrize("path", ["/api/v1/notarize", "/api/v1/c2pa/sign"])
def test_challenge_headers_fit_the_proxy_buffer(client, path):
    """Total response headers on a 402 must fit, with room to spare."""
    r = client.post(path, data=b"probe")
    assert r.status_code == 402, f"{path} did not challenge"

    challenge = r.headers.get("PAYMENT-REQUIRED", "")
    assert challenge, f"{path} sent no PAYMENT-REQUIRED header"

    total = sum(len(k) + len(v) + 4 for k, v in r.headers.items())
    budget = _proxy_buffer_size()

    assert total < budget, (
        f"{path}: {total} bytes of response headers exceeds proxy_buffer_size "
        f"{budget}. nginx will answer 502 while gunicorn logs a clean 402. Raise "
        f"proxy_buffer_size in deploy/nginx/authen.conf, or shorten the route "
        f"description and extension schemas."
    )

    # Fail while there is still time to react, not at the cliff edge. A change
    # that eats the last 25% is a warning about the next one.
    assert total < budget * 0.75, (
        f"{path}: {total} bytes uses more than 75% of the {budget}-byte buffer. "
        "Still working, but the next schema addition may not be."
    )


def test_proxy_buffer_is_configured_above_the_nginx_default():
    """Relying on the 4k default is how this broke the first time."""
    assert _proxy_buffer_size() > 4096, (
        "proxy_buffer_size is not raised above nginx's 4k default; the x402 "
        "challenge header already exceeds it"
    )
