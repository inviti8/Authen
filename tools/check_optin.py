"""Verify an address can actually receive the asset it is about to be paid in.

Read-only. Takes an address, never a key — safe to run against the mainnet treasury.

    python tools/check_optin.py --network mainnet --address <payTo>
    python tools/check_optin.py --network testnet --all

An un-opted-in payTo is one of the two fatal Phase 1 failures: the endpoint looks
healthy, returns a well-formed 402, and every settlement fails at the facilitator's
simulate step. Verify on-chain before going live rather than assuming — this script
exists so "verify opt-in" is a command and not a good intention.
"""

from __future__ import annotations

import argparse
import sys

from algo import (
    ASA_MIN_BALANCE_MICROALGO,
    MIN_BALANCE_MICROALGO,
    USDC_ASA,
    account_info,
    algo_balance,
    asset_holding,
    fmt_algo,
    fmt_units,
    load_testnet_accounts,
)


def check(network: str, address: str, asa_id: int, label: str = "") -> bool:
    head = f"{label or address[:12] + '...'}  ({network})"
    print(f"\n{head}\n{'-' * len(head)}")
    print(f"  address   {address}")
    try:
        info = account_info(network, address)
    except Exception as exc:  # noqa: BLE001 - surface whatever the node said
        print(f"  ERROR     could not reach node or unknown account: {exc}")
        return False

    bal = algo_balance(info)
    needed = MIN_BALANCE_MICROALGO + ASA_MIN_BALANCE_MICROALGO
    print(f"  balance   {fmt_algo(bal)}")

    if bal == 0:
        print("  STATUS    UNFUNDED - account does not exist on chain yet")
        print(f"            needs >= {fmt_algo(needed)} to hold one ASA")
        return False

    holding = asset_holding(info, asa_id)
    if holding is None:
        print(f"  ASA {asa_id}  NOT OPTED IN")
        print("  STATUS    FAIL - cannot receive this asset.")
        print("            Settlement would fail at the facilitator's simulate step.")
        if bal < needed:
            print(f"            Also under-funded: opt-in needs >= {fmt_algo(needed)}")
        return False

    amount = int(holding.get("amount", 0))
    print(f"  ASA {asa_id}  opted in, holding {fmt_units(amount)}")
    print("  STATUS    OK - can receive")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--network", choices=("mainnet", "testnet"), default="testnet")
    ap.add_argument("--address", help="address to check")
    ap.add_argument("--asa", type=int, help="ASA id (defaults to USDC for the network)")
    ap.add_argument(
        "--all",
        action="store_true",
        help="check every account in .venv/testnet_accounts.json (testnet only)",
    )
    args = ap.parse_args()

    asa_id = args.asa or USDC_ASA[args.network]
    print(f"Checking opt-in for ASA {asa_id} on {args.network}")

    if args.all:
        if args.network != "testnet":
            ap.error("--all is testnet only; pass --address for mainnet")
        accounts = load_testnet_accounts()
        # The accounts file also carries bookkeeping entries such as `_asset`, which
        # have no address. Select on the shape rather than on the key name.
        results = [
            check(args.network, v["address"], asa_id, label=role)
            for role, v in accounts.items()
            if isinstance(v, dict) and "address" in v
        ]
        ok = all(results)
    elif args.address:
        ok = check(args.network, args.address, asa_id)
    else:
        ap.error("pass --address or --all")

    print()
    print("ALL OK" if ok else "NOT READY - see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
