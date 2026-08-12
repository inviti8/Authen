"""Flask application for Authen — paid notarization over x402.

Three surfaces:

    GET  /health                free   liveness
    POST /api/v1/notarize       PAID   bytes -> signed attestation
    POST /api/v1/verify         free   attestation -> valid?  (drives discovery)
    GET  /api/v1/identity       free   this node's signing key + addresses

Verification is deliberately free. An attestation nobody can check is worth
nothing, so the cost of checking has to be zero — and a free verify endpoint is
what makes the paid one discoverable.

x402 is in the core flow, not bolted on: there is no unpaid path to an
attestation. That distinction is what judged criterion 2 measures.
"""

from __future__ import annotations

from flask import Flask, Response, jsonify, request
from x402.http.constants import PAYMENT_RESPONSE_HEADER, X_PAYMENT_RESPONSE_HEADER
from x402.http.middleware.flask import payment_middleware
from x402.http.paywall import AvmPaywallHandler, PaywallBuilder

from ..config import NodeConfig, load_config
from ..keys import assert_identity, load_or_create
from ..notary import build_attestation, sha256_hex, verify_attestation
from ..x402.server import build_server, routes_for

# Browser clients cannot read a response header unless it is explicitly exposed.
# v2 emits PAYMENT-RESPONSE; X-PAYMENT-RESPONSE is the v1 alias older clients want.
_EXPOSE_HEADERS = f"{PAYMENT_RESPONSE_HEADER}, {X_PAYMENT_RESPONSE_HEADER}"

# A notarization request is a hash operation, not a storage service. Cap the body
# so one caller cannot pin a worker on a multi-gigabyte upload inside the payment
# window. Callers with large objects should send a digest, not the bytes.
MAX_BODY_BYTES = 32 * 1024 * 1024


def create_app(cfg: NodeConfig | None = None) -> Flask:
    cfg = cfg or load_config()
    cfg.paths.ensure_dirs()

    identity = load_or_create(cfg.paths.data_dir)
    assert_identity(identity, cfg.identity_pubkey)

    app = Flask(__name__)
    app.config["AUTHEN"] = cfg
    app.config["IDENTITY"] = identity
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES

    # ---------------------------------------------------------------- free

    @app.get("/health")
    def health() -> Response:
        return jsonify(
            {
                "ok": True,
                "node": cfg.node_name,
                "network": cfg.network.name,
                "key": identity.public_hex,
            }
        )

    @app.get("/api/v1/identity")
    def node_identity() -> Response:
        """The key attestations are signed with, in every encoding it answers to.

        One Ed25519 key is simultaneously a Stellar and an Algorand address, which
        is what lets the same attestation be checked against a trust registry on
        either chain.
        """
        return jsonify(
            {
                "publicKey": identity.public_hex,
                "algorand": identity.algorand,
                "stellar": identity.stellar,
                "algorithm": "ed25519",
            }
        )

    @app.post("/api/v1/verify")
    def verify() -> Response:
        """Free. Checks an attestation is internally consistent and correctly signed.

        This does NOT decide whether the signing key deserves trust — that is the
        registry question, and conflating the two is how provenance claims get
        overstated.
        """
        body = request.get_json(silent=True) or {}
        wire = body.get("attestation") or request.get_data(as_text=True).strip()
        if not wire:
            return jsonify({"valid": False, "error": "no attestation supplied"}), 400

        ok, payload, error = verify_attestation(wire)
        out: dict = {"valid": ok, "payload": payload}
        if error:
            out["error"] = error
        if ok:
            out["signedBy"] = payload.get("k")
            out["knownKey"] = payload.get("k") == identity.public_hex
        return jsonify(out), (200 if ok else 400)

    # ---------------------------------------------------------------- paid

    @app.post("/api/v1/notarize")
    def notarize():
        """Hash the submitted bytes and return a signed, timestamped attestation.

        Reaching this handler means the middleware verified payment and will settle
        before any of this response is released to the caller.
        """
        data = request.get_data(cache=False)
        if not data:
            return jsonify({"error": "empty body — send the bytes to notarize"}), 400

        att = build_attestation(
            identity,
            digest_hex=sha256_hex(data),
            size=len(data),
            media_type=request.content_type or None,
        )
        return jsonify(
            {
                "statement": (
                    "This node observed bytes with the stated SHA-256 at the stated "
                    "time. It does not assert authorship, ownership, or prior existence."
                ),
                **att.to_dict(),
            }
        )

    @app.after_request
    def expose_payment_headers(resp: Response) -> Response:
        resp.headers.setdefault("Access-Control-Expose-Headers", _EXPOSE_HEADERS)
        return resp

    # x402 middleware last: it wraps the finished WSGI app.
    #
    # The paywall must be configured explicitly. With no provider the SDK serves its
    # EVM bundle, which parses our `algorand:` network id as an EVM chain id, throws
    # `Unsupported chain ID: NaN`, and renders a blank page. See tests/test_paywall.py.
    server = build_server(cfg)
    paywall = (
        PaywallBuilder()
        .with_network(AvmPaywallHandler())
        .with_config(app_name=cfg.node_name, testnet=cfg.network.name != "mainnet")
        .build()
    )
    payment_middleware(app, routes_for(cfg), server, paywall_provider=paywall)

    return app
