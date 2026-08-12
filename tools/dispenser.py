"""Programmatic testnet ALGO funding via the AlgoKit TestNet Dispenser API.

Algorand has no Friendbot, but it does have a scriptable dispenser — I previously
claimed otherwise on the strength of the browser faucets alone, which was wrong.
The API is real and public:

    https://api.dispenser.algorandfoundation.tools

Every endpoint needs a bearer JWT. The token comes from an OAuth2 *device
authorization* flow against Auth0, which is the one human step: you open a URL,
confirm a short code, and the CI-audience token that comes back is valid for 30
days. After that, funding is a single call and repeatable from CI.

    python tools/dispenser.py --login      once per 30 days; prints a URL to visit
    python tools/dispenser.py --limit      remaining daily allowance
    python tools/dispenser.py --fund       top the funder account up

The token is written to `.venv/dispenser_token.txt` (gitignored) and also read from
`ALGOKIT_DISPENSER_ACCESS_TOKEN`, which is the variable name AlgoKit itself uses —
so a token from `algokit dispenser login --ci` works here unchanged, and vice versa.

We do the device flow directly rather than depending on the `algokit` CLI: it is
~40 lines against httpx, which is already a dependency, and it avoids pulling in
keyring and a system-keychain prompt for a throwaway testnet credential. The client
ids below are public constants from algokit-cli's own source (`core/dispenser.py`).
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

from algo import REPO_ROOT, fmt_algo, load_testnet_accounts

API_BASE = "https://api.dispenser.algorandfoundation.tools"
AUTH_BASE = "https://dispenser-prod.eu.auth0.com"

# Public client identifiers, mirrored from algokit-cli src/algokit/core/dispenser.py.
CI_CLIENT_ID = "BOZkxGUiiWkaAXZebCQ20MTIYuQSqqpI"
CI_AUDIENCE = "api-prod-dispenser-ci"

TOKEN_FILE = REPO_ROOT / ".venv" / "dispenser_token.txt"
TOKEN_ENV = "ALGOKIT_DISPENSER_ACCESS_TOKEN"

ALGO_ASSET_ID = 0  # the dispenser addresses ALGO as asset 0
LOGIN_TIMEOUT = 300
TIMEOUT = 15


# ----------------------------------------------------------------- token handling


def load_token() -> str | None:
    """Environment first, so CI can inject a token without touching the filesystem."""
    tok = os.environ.get(TOKEN_ENV)
    if tok:
        return tok.strip()
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip() or None
    return None


def require_token() -> str:
    tok = load_token()
    if not tok:
        raise SystemExit(
            "No dispenser token.\n"
            "Run: python tools/dispenser.py --login\n"
            f"(or set {TOKEN_ENV} from `algokit dispenser login --ci`)"
        )
    return tok


def cmd_login() -> int:
    r = httpx.post(
        f"{AUTH_BASE}/oauth/device/code",
        data={
            "client_id": CI_CLIENT_ID,
            "scope": "openid profile email",
            "audience": CI_AUDIENCE,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    dc = r.json()

    print("\n  Open this URL in a browser:\n")
    print(f"      {dc['verification_uri_complete']}\n")
    print(f"  Confirm the code:  {dc['user_code']}\n")
    print("  Waiting for approval", end="", flush=True)

    interval = int(dc.get("interval", 5))
    deadline = time.monotonic() + LOGIN_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(interval)
        t = httpx.post(
            f"{AUTH_BASE}/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": dc["device_code"],
                "client_id": CI_CLIENT_ID,
                "audience": CI_AUDIENCE,
            },
            timeout=TIMEOUT,
        ).json()

        if "access_token" in t:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(t["access_token"])
            print(f"\n\n  Authorised. Token saved to {TOKEN_FILE}")
            print("  Valid for 30 days. Now run: python tools/dispenser.py --fund")
            return 0

        err = t.get("error")
        if err == "authorization_pending":
            print(".", end="", flush=True)
            continue
        if err == "slow_down":
            interval += 5
            continue
        print(f"\n\n  Login failed: {err}: {t.get('error_description', '')}")
        return 1

    print("\n\n  Timed out waiting for approval.")
    return 1


# --------------------------------------------------------------------- API calls


def _api(method: str, path: str, json_body: dict | None = None) -> dict:
    r = httpx.request(
        method,
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {require_token()}"},
        json=json_body,
        timeout=TIMEOUT,
    )
    if r.status_code == 401:
        raise SystemExit(
            "Dispenser rejected the token (401). It has probably expired — CI tokens "
            "last 30 days.\nRun: python tools/dispenser.py --login"
        )
    if r.status_code >= 400:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        code = body.get("code", r.status_code)
        raise SystemExit(f"Dispenser error [{code}]: {body.get('message', r.text[:200])}")
    return r.json()


def cmd_limit() -> int:
    amount = int(_api("GET", f"/fund/{ALGO_ASSET_ID}/limit")["amount"])
    print(f"Remaining daily dispenser allowance: {fmt_algo(amount)}")
    print("Limits reset at midnight UTC.")
    return 0


def cmd_fund(address: str | None, micro_algo: int) -> int:
    if address is None:
        accounts = load_testnet_accounts()
        acct = accounts.get("funder") or accounts["treasury"]
        address = acct["address"]

    limit = int(_api("GET", f"/fund/{ALGO_ASSET_ID}/limit")["amount"])
    if micro_algo > limit:
        print(f"Requested {fmt_algo(micro_algo)} but only {fmt_algo(limit)} remains today.")
        print("Funding the remainder instead; limits reset at midnight UTC.")
        micro_algo = limit
    if micro_algo <= 0:
        raise SystemExit("Daily dispenser allowance exhausted. Resets at midnight UTC.")

    res = _api(
        "POST",
        f"/fund/{ALGO_ASSET_ID}",
        {"receiver": address, "amount": micro_algo},
    )
    print(f"Funded {address}")
    print(f"  amount  {fmt_algo(int(res['amount']))}")
    print(f"  txID    {res['txID']}")
    print("\nNow run: python tools/testnet_setup.py --provision")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--login", action="store_true", help="device-code login (30-day token)")
    g.add_argument("--limit", action="store_true", help="remaining daily allowance")
    g.add_argument("--fund", action="store_true", help="fund the testnet funder account")
    ap.add_argument("--address", help="override the receiver (defaults to the funder)")
    ap.add_argument(
        "--amount",
        type=float,
        default=5.0,
        help="whole ALGO to request (default: 5)",
    )
    args = ap.parse_args()

    if args.login:
        return cmd_login()
    if args.limit:
        return cmd_limit()
    return cmd_fund(args.address, int(args.amount * 1_000_000))


if __name__ == "__main__":
    sys.exit(main())
