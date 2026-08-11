"""Provision testnet accounts for the end-to-end payment test.

Algorand has no Friendbot. Stellar's is an unauthenticated GET; every Algorand testnet
faucet now funnels to https://lora.algokit.io/testnet/fund (a browser SPA) or the
AlgoKit dispenser, which needs an OAuth token. Neither can be scripted.

That gates exactly ONE step. Testnet ALGO moves freely once you hold some, so a single
browser visit unblocks everything else:

    1. HUMAN, ONCE:  fund the funder account at https://lora.algokit.io/testnet/fund
    2. python tools/testnet_setup.py --provision

Step 2 distributes ALGO, mints a stand-in USDC if needed, opts both accounts in, and
hands the buyer something to spend.

On the stand-in asset: real testnet USDC (ASA 10458941) is Circle-issued and has no
open faucet, so obtaining it is its own chase. The testnet run exists to prove the
402 -> verify -> settle -> receipt path, and the `exact` scheme takes any ASA id. So
we mint our own 6-decimal token and point config at it. Mainnet uses the real ASA
31566704 and nothing about the code path differs.

    python tools/testnet_setup.py --new         create fresh throwaway accounts
    python tools/testnet_setup.py --status      balances and opt-in state
    python tools/testnet_setup.py --provision   do everything scriptable
"""

from __future__ import annotations

import argparse
import sys

from algo import (
    USDC_ASA,
    account_info,
    algo_balance,
    asset_holding,
    client,
    fmt_algo,
    fmt_units,
    load_testnet_accounts,
    save_testnet_accounts,
)
from algosdk import account as algo_account
from algosdk import mnemonic, transaction

NETWORK = "testnet"
FAUCET_URL = "https://lora.algokit.io/testnet/fund"

# Enough to cover minimum balance, an ASA slot and a long tail of test transactions.
FUNDER_TARGET_MICROALGO = 5_000_000     # 5 ALGO — ask the faucet for this
PEER_TOPUP_MICROALGO = 1_000_000        # 1 ALGO to each other account
STANDIN_ASSET_TOTAL = 1_000_000_000_000  # 1,000,000 tokens at 6 decimals
BUYER_TOKEN_GRANT = 100_000_000          # 100 tokens — plenty of $3 issues


def _sk(acct: dict) -> str:
    return mnemonic.to_private_key(acct["mnemonic"])


def _send(c, signed) -> dict:
    txid = c.send_transaction(signed)
    return transaction.wait_for_confirmation(c, txid, 10)


def cmd_new() -> int:
    accounts = {}
    for role in ("funder", "treasury", "buyer"):
        sk, addr = algo_account.generate_account()
        accounts[role] = {"address": addr, "mnemonic": mnemonic.from_private_key(sk)}
    save_testnet_accounts(accounts)
    print("Created throwaway testnet accounts (mnemonics are gitignored):\n")
    for role, v in accounts.items():
        print(f"  {role:9} {v['address']}")
    print(f"\nNow fund the FUNDER account at {FAUCET_URL}")
    print(f"Ask for {fmt_algo(FUNDER_TARGET_MICROALGO)} or more, then run --provision")
    return 0


def cmd_status(asa_id: int) -> int:
    accounts = load_testnet_accounts()
    print(f"testnet status (asset {asa_id})\n")
    ready = True
    for role, v in accounts.items():
        info = account_info(NETWORK, v["address"])
        bal = algo_balance(info)
        h = asset_holding(info, asa_id)
        held = fmt_units(int(h["amount"])) if h else "-- not opted in --"
        print(f"  {role:9} {fmt_algo(bal):>18}   asset: {held}")
        if bal == 0:
            ready = False
    print()
    if not ready:
        print(f"Unfunded accounts present. Fund 'funder' at {FAUCET_URL}")
    return 0


def cmd_provision(asa_id: int | None) -> int:
    accounts = load_testnet_accounts()
    c = client(NETWORK)

    funder = accounts.get("funder") or accounts["treasury"]
    finfo = account_info(NETWORK, funder["address"])
    if algo_balance(finfo) < PEER_TOPUP_MICROALGO * 2:
        print(f"Funder {funder['address']} holds {fmt_algo(algo_balance(finfo))}.")
        print(f"This is the one manual step. Fund it at:\n\n    {FAUCET_URL}\n")
        print(f"Ask for {fmt_algo(FUNDER_TARGET_MICROALGO)}, then re-run --provision.")
        return 1

    fsk = _sk(funder)

    # 1. Top up the peers so they can exist and hold an ASA.
    for role in ("treasury", "buyer"):
        acct = accounts[role]
        if acct["address"] == funder["address"]:
            continue
        bal = algo_balance(account_info(NETWORK, acct["address"]))
        if bal >= PEER_TOPUP_MICROALGO:
            print(f"  {role:9} already funded ({fmt_algo(bal)})")
            continue
        sp = c.suggested_params()
        _send(
            c,
            transaction.PaymentTxn(
                funder["address"], sp, acct["address"], PEER_TOPUP_MICROALGO
            ).sign(fsk),
        )
        print(f"  {role:9} funded {fmt_algo(PEER_TOPUP_MICROALGO)}")

    # 2. Stand-in asset, unless one was supplied.
    if asa_id is None:
        asa_id = accounts.get("_asset", {}).get("id")
    if asa_id is None:
        sp = c.suggested_params()
        res = _send(
            c,
            transaction.AssetConfigTxn(
                sender=funder["address"],
                sp=sp,
                total=STANDIN_ASSET_TOTAL,
                decimals=6,
                default_frozen=False,
                unit_name="TUSDC",
                asset_name="Test USDC (PintheonV2)",
                manager=funder["address"],
                reserve=funder["address"],
                strict_empty_address_check=False,
            ).sign(fsk),
        )
        asa_id = res["asset-index"]
        accounts["_asset"] = {"id": asa_id, "unit": "TUSDC", "decimals": 6}
        save_testnet_accounts(accounts)
        print(f"  asset     minted stand-in ASA {asa_id} (TUSDC, 6 decimals)")

    # 3. Opt both peers in. Receiving without opt-in is impossible; this is the step
    #    whose absence makes settlement fail at simulate.
    for role in ("treasury", "buyer"):
        acct = accounts[role]
        if asset_holding(account_info(NETWORK, acct["address"]), asa_id):
            print(f"  {role:9} already opted in to {asa_id}")
            continue
        sp = c.suggested_params()
        _send(
            c,
            transaction.AssetTransferTxn(
                sender=acct["address"], sp=sp, receiver=acct["address"], amt=0,
                index=asa_id,
            ).sign(_sk(acct)),
        )
        print(f"  {role:9} opted in to {asa_id}")

    # 4. Give the buyer something to spend.
    buyer = accounts["buyer"]
    held = asset_holding(account_info(NETWORK, buyer["address"]), asa_id)
    if int((held or {}).get("amount", 0)) < BUYER_TOKEN_GRANT:
        sp = c.suggested_params()
        _send(
            c,
            transaction.AssetTransferTxn(
                sender=funder["address"], sp=sp, receiver=buyer["address"],
                amt=BUYER_TOKEN_GRANT, index=asa_id,
            ).sign(fsk),
        )
        print(f"  buyer     granted {fmt_units(BUYER_TOKEN_GRANT)} TUSDC")

    print("\nProvisioned. Point config/node.local.toml at the stand-in asset:\n")
    print(f'    [networks.testnet]\n    usdc_asa = "{asa_id}"')
    print(f'\n    [treasury]\n    testnet = "{accounts["treasury"]["address"]}"')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--new", action="store_true", help="create throwaway accounts")
    g.add_argument("--status", action="store_true", help="balances and opt-in state")
    g.add_argument("--provision", action="store_true", help="do everything scriptable")
    ap.add_argument(
        "--asa",
        type=int,
        default=None,
        help=f"use an existing ASA (e.g. {USDC_ASA['testnet']}) instead of minting",
    )
    args = ap.parse_args()

    if args.new:
        return cmd_new()
    if args.status:
        accounts = load_testnet_accounts()
        asa = args.asa or accounts.get("_asset", {}).get("id") or USDC_ASA[NETWORK]
        return cmd_status(asa)
    return cmd_provision(args.asa)


if __name__ == "__main__":
    sys.exit(main())
