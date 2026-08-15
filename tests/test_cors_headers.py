"""The app's exposed-header list and nginx's must agree.

`proxy_hide_header Access-Control-Expose-Headers` in deploy/nginx/authen.conf strips
whatever the app sets and replaces it with nginx's own literal. That is deliberate —
the 402 challenge is built by payment_middleware in the WSGI layer outside Flask, so
`after_request` never runs for the one response a browser payer most needs to read,
and only nginx can put a header on it.

The cost of that design is that **nginx's list is the only one that reaches a
browser**. Adding a header to `_EXPOSE_HEADERS` and not to the vhost publishes
nothing, and publishes it silently: the header is on the wire, the app looks correct,
and `fetch()` in a browser simply cannot see it.

That is not hypothetical. `X-Authen-Settlement` and `X-Authen-Settlement-Tx` were
added to `_EXPOSE_HEADERS` with the settlement guard and shipped unreadable, because
the vhost still carried the previous list. The comment above the `add_header` said
"keep in step with app.py" and nothing enforced it.

Same shape as tests/test_body_limits.py: parse the committed vhost rather than
assert a copy of its values, so the test tracks the config instead of duplicating it.
No network.
"""

from __future__ import annotations

import re
from pathlib import Path

from authen.web.app import _EXPOSE_HEADERS

VHOST = Path(__file__).resolve().parent.parent / "deploy" / "nginx" / "authen.conf"


def _split(value: str) -> set[str]:
    """Header names, case-folded — HTTP header names are case-insensitive."""
    return {h.strip().lower() for h in value.split(",") if h.strip()}


def _nginx_exposed() -> set[str]:
    """The single quoted literal nginx sends as Access-Control-Expose-Headers."""
    text = VHOST.read_text(encoding="utf-8")
    m = re.search(
        r'add_header\s+Access-Control-Expose-Headers\s+"([^"]+)"',
        text,
        re.IGNORECASE,
    )
    assert m, "no add_header Access-Control-Expose-Headers found in the vhost"
    return _split(m.group(1))


def test_nginx_exposes_everything_the_app_does():
    """Anything the app means to publish must survive proxy_hide_header.

    The failure this catches is silent in every other signal: the header is present
    on the wire, curl shows it, and only a browser is affected.
    """
    missing = _split(_EXPOSE_HEADERS) - _nginx_exposed()
    assert not missing, (
        f"{sorted(missing)} are in _EXPOSE_HEADERS but not in the nginx literal. "
        "proxy_hide_header drops the app's value, so a browser payer cannot read "
        "them. Add them to add_header in deploy/nginx/authen.conf."
    )


def test_nginx_exposes_nothing_the_app_does_not():
    """Drift in the other direction is a stale list, and usually a rename.

    Advertising a header nobody sends is harmless to a client but is a reliable sign
    the two lists have diverged — which means the useful direction above may be
    wrong too.
    """
    extra = _nginx_exposed() - _split(_EXPOSE_HEADERS)
    assert not extra, (
        f"nginx exposes {sorted(extra)} which the app never sets. Stale or renamed; "
        "reconcile with _EXPOSE_HEADERS in authen/web/app.py."
    )


def test_the_settlement_headers_are_actually_in_both():
    """The specific regression, named.

    A buyer whose settlement came back `unknown` has no way to learn that from a
    browser unless these two are exposed, and `unknown` is precisely the case where
    they most need to know.
    """
    from authen.x402.settlement import SETTLEMENT_HEADER, SETTLEMENT_TX_HEADER

    for header in (SETTLEMENT_HEADER, SETTLEMENT_TX_HEADER):
        assert header.lower() in _split(_EXPOSE_HEADERS), f"{header} missing from app"
        assert header.lower() in _nginx_exposed(), f"{header} missing from nginx"


def test_the_challenge_header_is_exposed():
    """PAYMENT-REQUIRED carries the 402 challenge itself.

    Without it a browser payer can see a 402 and cannot read what it is being asked
    to pay. GoPlausible's x402 Doctor flags this, and it currently passes.
    """
    assert "payment-required" in _nginx_exposed()
