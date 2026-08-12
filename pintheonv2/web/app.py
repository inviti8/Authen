"""Flask application for the Phase 1 paid endpoint.

Three public surfaces:

    GET /api/v1/titles                  free   catalogue, for agents and humans
    GET /api/v1/preview/<title>/<n>     free   scrambled page — the paywall boundary
    GET /api/v1/issue/<title>           PAID   the whole issue

The paid route is the core flow, not a stub bolted onto a product that works without
it. That distinction is what judged criterion 2 measures.

Phase 1 returns the same issue to every buyer. Per-buyer encryption and PWA packaging
arrive in Phase 2 without changing the payment path or the price.
"""

from __future__ import annotations

import io
import zipfile

from flask import Flask, Response, jsonify, send_file
from x402.http.constants import (
    PAYMENT_RESPONSE_HEADER,
    X_PAYMENT_RESPONSE_HEADER,
)
from x402.http.middleware.flask import payment_middleware
from x402.http.paywall import AvmPaywallHandler, PaywallBuilder

from ..config import NodeConfig, load_config
from ..content.library import Library, UnknownTitle
from ..x402.server import build_server, routes_for

# Browser clients cannot read a response header unless it is explicitly exposed.
# Both names are listed: v2 emits PAYMENT-RESPONSE, and X-PAYMENT-RESPONSE is the v1
# alias older clients still look for.
_EXPOSE_HEADERS = f"{PAYMENT_RESPONSE_HEADER}, {X_PAYMENT_RESPONSE_HEADER}"


def _issue_zip(title) -> io.BytesIO:
    """Package an issue as a zip, pages in reading order.

    Phase 2 replaces this with the bespoke per-buyer PWA directory. The payment path
    does not change when it does.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, page in enumerate(title.pages, start=1):
            zf.write(page, arcname=f"{i:03d}{page.suffix.lower()}")
    buf.seek(0)
    return buf


def create_app(cfg: NodeConfig | None = None) -> Flask:
    cfg = cfg or load_config()
    cfg.paths.ensure_dirs()
    cfg.paths.check_key_and_db_together()

    library = Library(cfg.paths.content_dir).load()

    app = Flask(__name__)
    app.config["PINTHEON"] = cfg
    app.config["LIBRARY"] = library

    def issue_url(slug: str) -> str:
        return f"{cfg.public_url}/api/v1/issue/{slug}"

    # ---------------------------------------------------------------- free

    @app.get("/health")
    def health() -> Response:
        return jsonify(
            {
                "ok": True,
                "node": cfg.node_name,
                "network": cfg.network.name,
                "titles": len(library),
            }
        )

    @app.get("/api/v1/titles")
    def titles() -> Response:
        return jsonify(
            {
                "node": cfg.node_name,
                "network": cfg.network.caip2,
                "asset": cfg.network.usdc_asa,
                "payTo": cfg.pay_to,
                "titles": [
                    t.public_dict(cfg.issue_price_display, issue_url(t.slug))
                    for t in library.all()
                ],
            }
        )

    @app.get("/api/v1/preview/<title>/<int:n>")
    def preview(title: str, n: int):
        try:
            path = library.preview_page(title, n)
        except UnknownTitle:
            return jsonify({"error": "no such preview page"}), 404
        return send_file(path, max_age=3600)

    # ---------------------------------------------------------------- paid

    @app.get("/api/v1/issue/<title>")
    def issue(title: str):
        # Reaching this handler means the middleware already verified and will settle
        # before the body is released to the client.
        try:
            t = library.get(title)
        except UnknownTitle:
            return jsonify({"error": "no such title"}), 404
        return send_file(
            _issue_zip(t),
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{t.slug}.zip",
        )

    @app.after_request
    def expose_payment_headers(resp: Response) -> Response:
        resp.headers.setdefault("Access-Control-Expose-Headers", _EXPOSE_HEADERS)
        return resp

    # x402 middleware last: it wraps the finished WSGI app.
    #
    # slug_example feeds the Bazaar discovery schema, so agents see a real title slug
    # rather than a placeholder. Falls back only when the library is empty.
    slug_example = library.all()[0].slug if len(library) else "example-title"
    server = build_server(cfg)

    # The paywall MUST be configured explicitly.
    #
    # With no provider registered the SDK serves its EVM paywall — a wagmi/ethers
    # React bundle that tries to parse our `algorand:<genesis>` network id as an EVM
    # chain id, throws `Unsupported chain ID: NaN`, and renders a blank white page.
    # It also phones home to cca-lite.coinbase.com. Nothing in the response looks
    # wrong: status 402, correct headers, 1.9MB of HTML.
    #
    # `AvmPaywallHandler` selects the AVM bundle, which speaks Pera/Defly. And
    # `testnet` defaults to True in this SDK regardless of the network, so it has to
    # be derived from config or a mainnet node advertises itself as testnet.
    paywall = (
        PaywallBuilder()
        .with_network(AvmPaywallHandler())
        .with_config(
            app_name=cfg.node_name,
            testnet=cfg.network.name != "mainnet",
        )
        .build()
    )

    payment_middleware(
        app, routes_for(cfg, slug_example), server, paywall_provider=paywall
    )

    return app
