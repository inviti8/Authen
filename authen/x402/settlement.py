"""Never report settlement failure on settlement success.

The bug this module exists to fix was found by the Obolus agent and confirmed on
chain: transaction `UB5F3X3RTQGZZG4VCIJS...`, round 64062555, $0.15 taken from the
buyer, nothing served. The facilitator timed out *after* the payment landed, and
Authen answered `402 {"error": "Settlement failed"}`.

Two separate harms, and the second is the one that is easy to miss:

  1. We told the buyer their payment failed when it had succeeded. Today that costs
     them a duplicate payment. If anything is ever added that retries settlement on
     a `failed` verdict, it becomes a double-settle.
  2. **We threw away goods the buyer had already paid for.** By the time settlement
     runs, the handler has returned 2xx and the attestation is sitting in
     `body_chunks` in the SDK middleware. The failure path discards it. We held a
     signed attestation for a payment that was on chain, and dropped it on the
     floor.

Where the SDK does it, exactly:

    x402/http/middleware/flask.py:428-439   settle_result.success is False
    x402/http/middleware/flask.py:441-453   process_settlement raised

Note that `process_settlement` (x402_http_server_base.py:433) already catches every
exception and converts it to `success=False`, so a facilitator timeout arrives at
the *first* branch, not the second. Both discard the body and both send a 402.

CLAUDE.md sketched the fix as "an outer WSGI layer wrapping payment_middleware".
That does not work: an outer layer sees only the finished 402 and cannot recover
the attestation the SDK already discarded. The interception has to happen where the
verdict is produced, before the middleware acts on it — which is the return value of
`process_settlement`. Wrapping that one method leaves every other SDK control path
untouched and is the smallest change that can serve the buyer what they bought.

The rule, from CLAUDE.md: **it must never report `failed` for a timeout — read the
chain, or say `unknown`.**

Reading the chain is exact here, and does not require searching for anything. The
payment payload carries the buyer's *signed* payment transaction, and an Algorand
transaction id is a hash of the transaction fields, not of the signature. So we can
derive the txid locally and ask the chain a direct question about it. Better still,
the transaction carries `last_valid`: once the chain is past that round and the txid
is absent, the transaction can never confirm. That turns the usual "not found yet"
ambiguity into a decisive answer:

    txid on chain                    -> SETTLED     serve the goods, real receipt
    absent and past last_valid       -> REJECTED    402 is honest, pass it through
    anything else                    -> UNKNOWN     serve the goods, say `unknown`

Serving on UNKNOWN is deliberate. The node stores nothing, so re-serving an
attestation we already computed costs us a signature and no more. Weigh that against
charging a buyer and returning an error, and it is not close. The worst case is that
we give away $0.05–$0.15 of signing during a facilitator outage; the alternative
worst case is taking money for nothing, which is the behaviour we are here to
delete.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..config import NodeConfig

# How the chain answered about a payment we could not get a verdict on.
SETTLED = "settled"
REJECTED = "rejected"
UNKNOWN = "unknown"

# Header carrying our own verdict, always present when this guard intervened. A
# buyer that wants certainty can take the txid to any Algorand explorer.
SETTLEMENT_HEADER = "X-Authen-Settlement"
SETTLEMENT_TX_HEADER = "X-Authen-Settlement-Tx"

# Total extra latency this guard may add to a request that has ALREADY spent the
# facilitator's timeout. Algorand blocks land in under three seconds, so a window of
# roughly two blocks is enough to catch a payment that was in flight when the
# facilitator gave up, without leaving the buyer's connection hanging.
_POLL_BUDGET_SECONDS = 6.0
_POLL_INTERVAL_SECONDS = 2.0
_HTTP_TIMEOUT_SECONDS = 4.0

# Substrings that mean the facilitator reached a real verdict and said no. Anything
# NOT matching one of these is treated as indeterminate, which is the safe default:
# mistaking a genuine rejection for `unknown` costs us one signature, while mistaking
# a timeout for a rejection is the bug we are fixing.
_DEFINITIVE_REJECTIONS = (
    "insufficient",
    "invalid_signature",
    "invalid signature",
    "invalid_payment",
    "recipient_mismatch",
    "network_mismatch",
    "genesis_hash_mismatch",
    "unsupported_scheme",
    "invalid_asset",
    "amount_insufficient",
    "expired",
    "already",
)


@dataclass(frozen=True)
class ChainVerdict:
    """What the chain says about a payment the facilitator would not confirm."""

    state: str
    txid: str | None = None
    detail: str = ""


# ---------------------------------------------------------------------------
# Deriving the payment transaction id
# ---------------------------------------------------------------------------


def payment_txid(payment_payload: Any) -> str | None:
    """The Algorand txid of the buyer's payment transaction, or None.

    The `exact` AVM payload is an atomic group of base64 msgpack transactions plus a
    `paymentIndex` naming which one pays us. The buyer signs their leg before we ever
    see it, and a txid covers the transaction fields rather than the signature, so
    this id is final and identical to the one the facilitator would submit.

    Returns None rather than raising: a payload shape we cannot read must degrade to
    UNKNOWN, never to a claim about the chain.
    """
    try:
        from algosdk import encoding

        payload = getattr(payment_payload, "payload", None) or payment_payload
        group = _field(payload, "payment_group", "paymentGroup")
        index = _field(payload, "payment_index", "paymentIndex") or 0

        if not group or int(index) >= len(group):
            return None

        decoded = encoding.msgpack_decode(group[int(index)])
        txn = getattr(decoded, "transaction", decoded)
        return str(txn.get_txid())
    except Exception:
        return None


def payment_last_valid(payment_payload: Any) -> int | None:
    """The last round in which the payment transaction can confirm, or None.

    This is what makes a negative answer decisive instead of merely "not yet".
    """
    try:
        from algosdk import encoding

        payload = getattr(payment_payload, "payload", None) or payment_payload
        group = _field(payload, "payment_group", "paymentGroup")
        index = _field(payload, "payment_index", "paymentIndex") or 0

        if not group or int(index) >= len(group):
            return None

        decoded = encoding.msgpack_decode(group[int(index)])
        txn = getattr(decoded, "transaction", decoded)
        return int(txn.last_valid_round)
    except Exception:
        return None


def _field(obj: Any, snake: str, camel: str) -> Any:
    """Read a field that may be an attribute or a dict key, in either casing."""
    if isinstance(obj, dict):
        return obj.get(snake, obj.get(camel))
    return getattr(obj, snake, None) or getattr(obj, camel, None)


# ---------------------------------------------------------------------------
# Asking the chain
# ---------------------------------------------------------------------------


def _get_json(url: str) -> dict[str, Any] | None:
    """GET and parse JSON. None on 404, on transport error, or on garbage.

    Deliberately does not distinguish "absent" from "unreachable" in its return
    value; the caller decides what absence means using the round height, which is the
    only signal that can make absence decisive.
    """
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return None


def _current_round(cfg: NodeConfig) -> int | None:
    status = _get_json(f"{cfg.network.algod_url}/v2/status")
    if not status:
        return None
    try:
        return int(status["last-round"])
    except (KeyError, TypeError, ValueError):
        return None


def _txid_confirmed(cfg: NodeConfig, txid: str) -> bool:
    """True if the indexer has the transaction in a confirmed block.

    The indexer rather than algod, because algod only remembers a transaction for a
    short window after it confirms and this can run seconds-to-minutes late.
    """
    found = _get_json(f"{cfg.network.indexer_url}/v2/transactions/{txid}")
    if not found:
        return False
    txn = found.get("transaction") or {}
    return bool(txn.get("confirmed-round"))


def confirm_on_chain(cfg: NodeConfig, payment_payload: Any) -> ChainVerdict:
    """Decide what really happened to a payment the facilitator did not confirm.

    Polls for up to `_POLL_BUDGET_SECONDS`, because a settlement that timed out may
    still be in flight — the facilitator gave up waiting, not the network.
    """
    txid = payment_txid(payment_payload)
    if not txid:
        return ChainVerdict(UNKNOWN, None, "payment payload could not be decoded")

    last_valid = payment_last_valid(payment_payload)
    deadline = time.monotonic() + _POLL_BUDGET_SECONDS

    while True:
        if _txid_confirmed(cfg, txid):
            return ChainVerdict(SETTLED, txid, "confirmed on chain")

        # Absent AND unable to confirm ever again is the one safe negative.
        if last_valid is not None:
            head = _current_round(cfg)
            if head is not None and head > last_valid:
                return ChainVerdict(
                    REJECTED,
                    txid,
                    f"absent at round {head}, past last-valid {last_valid}",
                )

        if time.monotonic() >= deadline:
            return ChainVerdict(UNKNOWN, txid, "not on chain yet, still valid")

        time.sleep(_POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def is_definitive_rejection(error_reason: str | None) -> bool:
    """Did the facilitator actually adjudicate, or did it just fail to answer?

    Unmatched reasons are treated as indeterminate on purpose. The asymmetry is the
    whole point: a false `unknown` costs one signature, a false `failed` takes a
    buyer's money and gives nothing back.
    """
    if not error_reason:
        return False
    reason = error_reason.lower()
    return any(marker in reason for marker in _DEFINITIVE_REJECTIONS)


def guard_settlement(middleware: Any, cfg: NodeConfig) -> None:
    """Wrap the middleware's `process_settlement` so a timeout can never read as failure.

    Wrapping this one bound method, rather than the WSGI app, is what lets the buyer
    keep the response they paid for: the SDK's success path still runs, so the
    buffered 2xx body is released instead of discarded.
    """
    http_server = middleware._http_server  # noqa: SLF001 - the SDK exposes no hook
    inner = http_server.process_settlement

    def process_settlement(payment_payload: Any, requirements: Any) -> Any:
        result = inner(payment_payload, requirements)

        if result.success or is_definitive_rejection(result.error_reason):
            return result

        verdict = confirm_on_chain(cfg, payment_payload)

        if verdict.state == REJECTED:
            # The chain agrees with the facilitator. 402 is the honest answer.
            return result

        return _serve_anyway(result, verdict, requirements, cfg)

    http_server.process_settlement = process_settlement


def _serve_anyway(result: Any, verdict: ChainVerdict, requirements: Any, cfg: NodeConfig) -> Any:
    """Turn a non-verdict into a served response, labelled with what we actually know.

    `success=True` here means "release the buffered response", which is the only
    lever the SDK offers. It is not a claim that settlement completed — the claim we
    make is in the headers, and on UNKNOWN it says `unknown` rather than asserting a
    receipt we cannot back.
    """
    from x402.http.types import ProcessSettleResult

    headers = {
        SETTLEMENT_HEADER: verdict.state,
        SETTLEMENT_TX_HEADER: verdict.txid or "",
    }

    if verdict.state == SETTLED:
        # We have a real, chain-confirmed settlement, so emit a genuine receipt.
        #
        # Deliberately NOT wrapped in a try/except. An earlier draft was, with the
        # import path wrong, and the effect was a chain-confirmed settlement served
        # with no PAYMENT-RESPONSE and nothing logged anywhere — the same silent
        # class of defect as the three Bazaar declaration bugs. If the SDK moves
        # this symbol, fail loudly at import time in tests, not quietly in
        # production on the one path that matters.
        from x402.http.constants import PAYMENT_RESPONSE_HEADER
        from x402.http.utils import encode_payment_response_header
        from x402.schemas import SettleResponse

        headers[PAYMENT_RESPONSE_HEADER] = encode_payment_response_header(
            SettleResponse(
                success=True,
                transaction=verdict.txid or "",
                network=requirements.network,
            )
        )

    return ProcessSettleResult(
        success=True,
        headers=headers,
        transaction=verdict.txid,
        network=getattr(requirements, "network", None),
    )
