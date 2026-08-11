"""Shared Algorand helpers for the tooling scripts.

Read-only helpers take an address and never a key, so they are safe to point at
mainnet. Anything that signs is confined to `tools/testnet_setup.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from algosdk.v2client import algod

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

TESTNET_ACCOUNTS = REPO_ROOT / ".venv" / "testnet_accounts.json"

ALGOD = {
    "mainnet": "https://mainnet-api.algonode.cloud",
    "testnet": "https://testnet-api.algonode.cloud",
}
USDC_ASA = {"mainnet": 31566704, "testnet": 10458941}

# Algorand minimum balance: 0.1 ALGO for the account, plus 0.1 per ASA held.
MIN_BALANCE_MICROALGO = 100_000
ASA_MIN_BALANCE_MICROALGO = 100_000


def client(network: str) -> algod.AlgodClient:
    return algod.AlgodClient("", ALGOD[network])


def account_info(network: str, address: str) -> dict[str, Any]:
    return client(network).account_info(address)


def algo_balance(info: dict[str, Any]) -> int:
    return int(info.get("amount", 0))


def asset_holding(info: dict[str, Any], asa_id: int) -> dict[str, Any] | None:
    """The account's holding of `asa_id`, or None if it is not opted in.

    Opt-in is what matters, not balance: a payTo that is not opted in cannot receive,
    and settlement fails at the facilitator's simulate step.
    """
    for h in info.get("assets", []):
        if int(h.get("asset-id", -1)) == asa_id:
            return h
    return None


def fmt_algo(micro: int) -> str:
    return f"{micro / 1e6:.6f} ALGO"


def fmt_units(micro: int, decimals: int = 6) -> str:
    return f"{micro / 10 ** decimals:.{decimals}f}"


def load_testnet_accounts() -> dict[str, dict[str, str]]:
    if not TESTNET_ACCOUNTS.exists():
        raise SystemExit(
            f"No testnet accounts at {TESTNET_ACCOUNTS}.\n"
            "Run: python tools/testnet_setup.py --new"
        )
    return json.loads(TESTNET_ACCOUNTS.read_text())


def save_testnet_accounts(d: dict[str, Any]) -> None:
    TESTNET_ACCOUNTS.parent.mkdir(parents=True, exist_ok=True)
    TESTNET_ACCOUNTS.write_text(json.dumps(d, indent=2))
