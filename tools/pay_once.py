"""Buy one attestation on testnet, end to end, against a live facilitator.

This is the proof that the whole rail works: 402 challenge -> buyer signs an axfer ->
facilitator verifies -> facilitator settles on chain -> node signs the attestation
and returns a receipt. Everything before this is inference; this is evidence.

    python tools/pay_once.py

It boots the node in-process on 127.0.0.1:8402, so nothing needs deploying yet. The
facilitator never fetches the resource under the `exact` scheme - it only sees the
signed payment - so a loopback server is sufficient.

CATALOGUING: the facilitator auto-catalogs resources into the Bazaar on /verify. That
is why `config/node.local.toml` sets the tag to something other than the competition
tag: a localhost URL indexed under `x402-global-challenge` would be junk in the
competition's own discovery index. Do not point this script at a config carrying the
real tag until the node is on its final public hostname.

Requires: a funded, opted-in buyer holding the asset in config. Run
`tools/testnet_setup.py --provision` first.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from wsgiref.simple_server import WSGIRequestHandler, make_server

import httpx
from algosdk import encoding, mnemonic, transaction

from algo import load_testnet_accounts

HOST, PORT = "127.0.0.1", 8402
BASE = f"http://{HOST}:{PORT}"


class BuyerSigner:
    """Implements x402's ClientAvmSigner protocol over a raw algosdk key.

    A real buyer would be a wallet. The protocol wants exactly two things: an address,
    and the ability to sign only the group indexes it is asked to sign - the fee payer
    transaction in the group belongs to the facilitator and must be left alone.
    """

    def __init__(self, sk: str, addr: str) -> None:
        self._sk = sk
        self._addr = addr

    @property
    def address(self) -> str:
        return self._addr

    def sign_transactions(
        self, unsigned_txns: list[bytes], indexes_to_sign: list[int]
    ) -> list[bytes | None]:
        out: list[bytes | None] = [None] * len(unsigned_txns)
        for i in indexes_to_sign:
            txn = encoding.msgpack_decode(base64.b64encode(unsigned_txns[i]).decode())
            out[i] = base64.b64decode(encoding.msgpack_encode(txn.sign(self._sk)))
        return out


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *_args) -> None:  # noqa: ANN002
        pass


def serve_in_thread(app) -> tuple:
    srv = make_server(HOST, PORT, app, handler_class=_QuietHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    for _ in range(50):
        try:
            httpx.get(f"{BASE}/health", timeout=1.0)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    return srv, t


def _decode_header(value: str) -> dict:
    return json.loads(base64.b64decode(value))


def run() -> int:
    from authen.config import load_config
    from authen.web.app import create_app

    cfg = load_config()
    if cfg.network.name != "testnet":
        raise SystemExit(
            f"Refusing to run against {cfg.network.name}. This script spends real "
            "money on mainnet. Set AUTHEN_NETWORK=testnet."
        )

    accounts = load_testnet_accounts()
    buyer = accounts["buyer"]
    signer = BuyerSigner(mnemonic.to_private_key(buyer["mnemonic"]), buyer["address"])

    print(f"network   {cfg.network.name}  asset {cfg.network.usdc_asa}")
    print(f"buyer     {buyer['address']}")
    print(f"payTo     {cfg.pay_to}")
    print(f"amount    {cfg.price_micro_usdc} micro-USDC ({cfg.price_display})")
    print()

    srv, _t = serve_in_thread(create_app(cfg))
    try:
        url = f"{BASE}{cfg.raw['routes']['notarize']}"
        body = b"authen end-to-end settlement probe"

        # 1. Unpaid request must be challenged.
        r = httpx.post(url, content=body, timeout=30)
        if r.status_code != 402:
            raise SystemExit(
                f"Expected 402, got {r.status_code}. The route is not protected - "
                "attestations are being signed for free."
            )
        challenge = _decode_header(r.headers["PAYMENT-REQUIRED"])
        print(f"[1/4] 402 challenge   x402Version={challenge['x402Version']} "
              f"accepts={len(challenge['accepts'])}")

        # 2. Build and sign the payment.
        from x402.client import x402ClientSync
        from x402.mechanisms.avm.exact import register_exact_avm_client
        from x402.schemas.payments import PaymentRequired

        client = x402ClientSync()
        register_exact_avm_client(
            client, signer=signer, networks=cfg.network.caip2,
            algod_url=cfg.network.algod_url,
        )
        payload = client.create_payment_payload(
            PaymentRequired.model_validate(challenge)
        )
        header = base64.b64encode(
            payload.model_dump_json(by_alias=True, exclude_none=True).encode()
        ).decode()
        print(f"[2/4] payment signed  {len(header)} byte header")

        # 3. Replay with payment. The middleware verifies, serves, then settles.
        #
        # The request header is PAYMENT-SIGNATURE. Not PAYMENT, and not X-PAYMENT
        # (that is v1). Get it wrong and the server sees no payment at all: it
        # re-challenges with a plain 402 and an unhelpful "Payment required", which
        # looks exactly like a rejected payment. The facilitator will happily report
        # isValid on the same payload while this fails.
        r = httpx.post(url, content=body, headers={"PAYMENT-SIGNATURE": header}, timeout=90)
        print(f"[3/4] paid request    {r.status_code} {r.headers.get('content-type')} "
              f"{len(r.content)} bytes")
        if r.status_code != 200:
            # The rejection reason rides in the re-issued challenge header, not the
            # body - the body is typically empty. Decode it or you are guessing.
            again = r.headers.get("PAYMENT-REQUIRED")
            if again:
                print("\nRejection:", json.dumps(_decode_header(again), indent=2)[:2000])
            print("\nBody:", r.text[:600])
            raise SystemExit("Payment was rejected. See the reason above.")

        # 4. Receipt.
        raw = r.headers.get("PAYMENT-RESPONSE") or r.headers.get("X-PAYMENT-RESPONSE")
        if not raw:
            raise SystemExit(
                "Signed the attestation but returned no settlement receipt. Without "
                "it a buyer cannot prove payment."
            )
        receipt = _decode_header(raw)
        print(f"[4/4] settled         success={receipt.get('success')}")
        txid = receipt.get("transaction") or receipt.get("txHash") or ""
        if txid:
            print(f"      txid            {txid}")
            print(f"      explorer        https://testnet.explorer.perawallet.app/tx/{txid}")
        print(f"\n{json.dumps(receipt, indent=2)}")
        return 0 if receipt.get("success") else 1
    finally:
        srv.shutdown()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()
    return run()


if __name__ == "__main__":
    sys.exit(main())
