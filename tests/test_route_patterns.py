"""Regression tests for Flask -> x402 route pattern translation.

This is the highest-value test in the suite. The x402 middleware regex-escapes route
paths and only substitutes `[param]` and `*`; a Flask-style `<title>` survives as a
literal, matches nothing, and the paid endpoint then serves **200 with the full
payload, for free**. Silently. No warning, no log line.

These tests need no network.
"""

from __future__ import annotations

import pytest

from authen.x402.server import to_x402_pattern


@pytest.mark.parametrize(
    ("flask_rule", "expected"),
    [
        ("/api/v1/notarize/<id>", "/api/v1/notarize/[id]"),
        ("/api/v1/preview/<title>/<int:n>", "/api/v1/preview/[title]/[n]"),
        ("/a/<uuid:id>/b", "/a/[id]/b"),
        ("/a/<path:rest>", "/a/[rest]"),
        ("/no/params", "/no/params"),
        ("/<a>/<b>", "/[a]/[b]"),
    ],
)
def test_translation(flask_rule, expected):
    assert to_x402_pattern(flask_rule) == expected


def test_no_flask_syntax_survives():
    """Any leftover angle bracket means the route silently will not be protected."""
    out = to_x402_pattern("/api/v1/notarize/<id>")
    assert "<" not in out and ">" not in out


def test_translated_pattern_matches_real_path():
    """Compile through the middleware's own parser and match a concrete request path.

    Uses the SDK's `_parse_route_pattern` rather than a reimplementation, so the test
    tracks the library's behaviour instead of our assumptions about it.
    """
    from x402.http.x402_http_server_base import x402HTTPServerBase as Base

    _verb, regex = Base._parse_route_pattern(
        f"GET {to_x402_pattern('/api/v1/notarize/<id>')}"
    )
    assert regex.match("/api/v1/notarize/abc123")
    assert regex.match("/api/v1/notarize/anything")
    assert not regex.match("/api/v1/notarize/a/b"), "must not span a path separator"
    assert not regex.match("/api/v1/preview/x/1")


def test_untranslated_pattern_matches_nothing():
    """Prove the failure mode this module exists to prevent."""
    from x402.http.x402_http_server_base import x402HTTPServerBase as Base

    _verb, regex = Base._parse_route_pattern("GET /api/v1/notarize/<id>")
    assert not regex.match("/api/v1/notarize/abc123")
