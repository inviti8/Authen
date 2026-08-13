"""Buy one attestation on Algorand MAINNET. Real money, irreversible.

This is the registration-gate transaction: the competition requires at least one
real completed payment, and this is it.

    python tools/pay_mainnet.py --check           show what is needed, spend nothing
    python tools/pay_mainnet.py --new-buyer       create a throwaway buyer account
    python tools/pay_mainnet.py --pay --confirm   spend real USDC

WHY THIS IS NOT `pay_once.py` WITH A FLAG FLIPPED
-------------------------------------------------
`pay_once.py` boots the node in-process on 127.0.0.1:8402. That is correct on
testnet and catastrophic here: the facilitator auto-catalogs into the Bazaar on
/verify, so paying a loopback URL would permanently register
`http://127.0.0.1:8402/...` as this merchant's resource. Roughly 13% of the live
index is junk created exactly that way.

**This script therefore talks to the PUBLIC URL only.** It refuses to run against
anything that is not https://, because the URL in the challenge is the URL that
gets catalogued, forever, keyed to the payTo.

SAFETY RAILS
------------
  * refuses unless the active network is mainnet (the inverse of pay_once.py)
  * refuses if the resource URL is not https://
  * refuses if buyer == payTo (paying yourself is not a real payment, and it is
    the first thing an anti-wash review looks for)
  * refuses without an explicit --confirm
  * checks buyer ALGO, USDC and ASA opt-in BEFORE building anything, so a
    predictable failure costs nothing

The buyer mnemonic comes from AUTHEN_BUYER_MNEMONIC, or from
`.venv/mainnet_buyer.json` (gitignored) if you used --new-buyer.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import httpx
from algosdk import account as algo_account
from algosdk import encoding, mnemonic
from algosdk.v2client import algod

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

BUYER_FILE = REPO_ROOT / ".venv" / "mainnet_buyer.json"
ALGOD_URL = "https://mainnet-api.algonode.cloud"
USDC_ASA = 31566704

# 0.1 ALGO account minimum + 0.1 for the USDC slot, plus headroom for fees. The
# buyer pays no transaction fee under `exact` - the facilitator sponsors it - but
# the minimum balance must still be there or the account cannot hold the asset.
MIN_ALGO_MICRO = 250_000


class BuyerSigner:
    """x402's ClientAvmSigner protocol over a raw algosdk key.

    Signs only the group indexes it is asked to sign; the fee-payer transaction
    belongs to the facilitator and must be left untouched.
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


def _client() -> algod.AlgodClient:
    return algod.AlgodClient("", ALGOD_URL)


def _load_buyer() -> dict | None:
    env = os.environ.get("AUTHEN_BUYER_MNEMONIC")
    if env:
        sk = mnemonic.to_private_key(env.strip())
        return {"address": algo_account.address_from_private_key(sk), "mnemonic": env.strip()}
    if BUYER_FILE.exists():
        return json.loads(BUYER_FILE.read_text())
    return None


def _buyer_state(address: str) -> dict:
    info = _client().account_info(address)
    holding = next(
        (a for a in info.get("assets", []) if int(a.get("asset-id", -1)) == USDC_ASA),
        None,
    )
    return {
        "algo": int(info.get("amount", 0)),
        "min_balance": int(info.get("min-balance", 0)),
        "opted_in": holding is not None,
        "usdc": int((holding or {}).get("amount", 0)),
    }


def cmd_new_buyer() -> int:
    if BUYER_FILE.exists():
        print(f"Refusing to overwrite {BUYER_FILE}.")
        print("Delete it first if you really want a new buyer account.")
        return 1
    sk, addr = algo_account.generate_account()
    BUYER_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUYER_FILE.write_text(
        json.dumps({"address": addr, "mnemonic": mnemonic.from_private_key(sk)}, indent=2)
    )
    print("Created a throwaway MAINNET buyer account.\n")
    print(f"  address  {addr}")
    print(f"  saved to {BUYER_FILE}  (gitignored)\n")
    print("This key is disposable and should never hold more than a few dollars.")
    print("Fund it with:")
    print("  - about 0.3 ALGO (minimum balance for the account + the USDC slot)")
    print(f"  - a little USDC, and OPT IN to ASA {USDC_ASA}\n")
    print("Then: python tools/pay_mainnet.py --check")
    return 0


def _load_cfg():
    from authen.config import load_config

    cfg = load_config()
    if cfg.network.name != "mainnet":
        raise SystemExit(
            f"Active network is {cfg.network.name!r}, not mainnet.\n"
            "This script exists to spend real money; refusing to run otherwise.\n"
            "Unset AUTHEN_NETWORK, or use tools/pay_once.py for testnet."
        )
    return cfg


def cmd_check(cfg) -> int:
    url = f"{cfg.public_url}{cfg.raw['routes']['notarize']}"
    print(f"resource   {url}")
    print(f"payTo      {cfg.pay_to}")
    print(f"price      {cfg.price_micro_usdc} micro-USDC ({cfg.price_display})")
    print()

    ok = True
    if not url.startswith("https://"):
        print("  FAIL  resource URL is not https. This URL gets catalogued forever.")
        ok = False

    buyer = _load_buyer()
    if not buyer:
        print("  FAIL  no buyer account.")
        print("        Run --new-buyer, or set AUTHEN_BUYER_MNEMONIC.")
        return 1

    print(f"buyer      {buyer['address']}")
    if buyer["address"] == cfg.pay_to:
        print("  FAIL  buyer IS the payTo. That is a self-transfer, not a payment.")
        return 1

    try:
        st = _buyer_state(buyer["address"])
    except Exception as exc:
        print(f"  FAIL  cannot read buyer account: {str(exc)[:120]}")
        return 1

    need = int(cfg.price_micro_usdc)
    print(f"  ALGO     {st['algo'] / 1e6:.6f}  (min-balance {st['min_balance'] / 1e6:.6f})")
    print(f"  USDC     {st['usdc'] / 1e6:.6f}  opted-in={st['opted_in']}")
    print()

    if not st["opted_in"]:
        print(f"  FAIL  buyer is not opted into ASA {USDC_ASA}. It cannot hold USDC.")
        ok = False
    if st["algo"] < MIN_ALGO_MICRO:
        print(f"  FAIL  buyer needs at least {MIN_ALGO_MICRO / 1e6:.2f} ALGO for minimum balance.")
        ok = False
    if st["usdc"] < need:
        print(f"  FAIL  buyer holds {st['usdc'] / 1e6:.6f} USDC, needs {need / 1e6:.6f}.")
        ok = False

    # The endpoint must actually challenge before we try to pay it.
    try:
        r = httpx.post(url, content=b"gate-probe", timeout=30)
        if r.status_code == 402:
            print("  OK    endpoint returns 402")
        else:
            print(f"  FAIL  endpoint returned {r.status_code}, expected 402")
            ok = False
    except Exception as exc:
        print(f"  FAIL  endpoint unreachable: {str(exc)[:120]}")
        ok = False

    print()
    print("READY - run with --pay --confirm" if ok else "NOT READY - see above")
    return 0 if ok else 1


def cmd_pay(cfg, confirmed: bool) -> int:
    if not confirmed:
        raise SystemExit(
            "Refusing to spend without --confirm.\n"
            "This sends real USDC on Algorand mainnet and permanently registers\n"
            f"{cfg.public_url} in the Bazaar, keyed to your payTo."
        )

    url = f"{cfg.public_url}{cfg.raw['routes']['notarize']}"
    if not url.startswith("https://"):
        raise SystemExit(f"Resource URL is not https: {url}")

    buyer = _load_buyer()
    if not buyer:
        raise SystemExit("No buyer account. Run --new-buyer first.")
    if buyer["address"] == cfg.pay_to:
        raise SystemExit("Buyer is the payTo. Self-transfers are not payments.")

    signer = BuyerSigner(mnemonic.to_private_key(buyer["mnemonic"]), buyer["address"])
    body = b"Authen mainnet registration payment"

    print(f"resource   {url}")
    print(f"buyer      {buyer['address']}")
    print(f"payTo      {cfg.pay_to}")
    print(f"amount     {cfg.price_micro_usdc} micro-USDC ({cfg.price_display})\n")

    # 1. Challenge.
    r = httpx.post(url, content=body, timeout=30)
    if r.status_code != 402:
        raise SystemExit(f"Expected 402, got {r.status_code}. Not paying.")
    challenge = json.loads(base64.b64decode(r.headers["PAYMENT-REQUIRED"]))
    accepts = challenge["accepts"][0]
    print(f"[1/4] 402 challenge   amount={accepts['amount']} payTo={accepts['payTo'][:12]}...")

    # Never pay a challenge that names an address we did not configure. A wrong
    # payTo here would send money to a stranger and register their merchant id.
    if accepts["payTo"] != cfg.pay_to:
        raise SystemExit(
            f"Challenge payTo {accepts['payTo']} does not match config {cfg.pay_to}. "
            "Refusing to pay."
        )

    # 2. Sign.
    from x402.client import x402ClientSync
    from x402.mechanisms.avm.exact import register_exact_avm_client
    from x402.schemas.payments import PaymentRequired

    client = x402ClientSync()
    register_exact_avm_client(
        client, signer=signer, networks=cfg.network.caip2, algod_url=ALGOD_URL
    )
    payload = client.create_payment_payload(PaymentRequired.model_validate(challenge))
    header = base64.b64encode(
        payload.model_dump_json(by_alias=True, exclude_none=True).encode()
    ).decode()
    print(f"[2/4] payment signed  {len(header)} byte header")

    # 3. Replay with payment. Header name is PAYMENT-SIGNATURE - anything else and
    #    the server sees no payment at all and re-challenges indistinguishably.
    r = httpx.post(
        url, content=body, headers={"PAYMENT-SIGNATURE": header}, timeout=120
    )
    print(f"[3/4] paid request    {r.status_code} {len(r.content)} bytes")
    if r.status_code != 200:
        again = r.headers.get("PAYMENT-REQUIRED")
        if again:
            print("\nRejection:", json.dumps(json.loads(base64.b64decode(again)), indent=2)[:1500])
        print("\nBody:", r.text[:600])
        raise SystemExit("Payment rejected. Nothing settled.")

    # 4. Receipt.
    raw = r.headers.get("PAYMENT-RESPONSE") or r.headers.get("X-PAYMENT-RESPONSE")
    if not raw:
        raise SystemExit("Served without a settlement receipt - cannot prove payment.")
    receipt = json.loads(base64.b64decode(raw))
    txid = receipt.get("transaction") or ""
    print(f"[4/4] settled         success={receipt.get('success')}")
    if txid:
        print(f"      txid            {txid}")
        print(f"      explorer        https://explorer.perawallet.app/tx/{txid}")
    print()
    print(json.dumps(receipt, indent=2))
    print()
    print("Attestation returned by the node:")
    print(json.dumps(r.json(), indent=2)[:900])
    return 0 if receipt.get("success") else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="preflight, spends nothing")
    g.add_argument("--new-buyer", action="store_true", help="create a throwaway buyer")
    g.add_argument("--pay", action="store_true", help="spend real USDC")
    ap.add_argument("--confirm", action="store_true", help="required with --pay")
    args = ap.parse_args()

    if args.new_buyer:
        return cmd_new_buyer()
    cfg = _load_cfg()
    if args.check:
        return cmd_check(cfg)
    return cmd_pay(cfg, args.confirm)


if __name__ == "__main__":
    sys.exit(main())
