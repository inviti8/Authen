"""The node must never report settlement failure on settlement success.

Measured on mainnet: transaction `UB5F3X3RTQGZZG4VCIJS...`, round 64062555, $0.15
taken from the buyer, `402 {"error": "Settlement failed"}` returned, nothing served.
The facilitator timed out after the payment had already landed.

This module pins both halves of the fix, because the second half is the one that is
easy to lose in a refactor:

  1. A timeout is never reported as `failed`. The chain decides, or we say `unknown`.
  2. **The buyer receives the thing they paid for.** At settlement time the
     attestation already exists — the handler returned 2xx and the SDK is holding
     the body. The SDK's failure path discards it. Any fix that answers the first
     point but still drops the body has left the buyer paying for nothing, which was
     the more expensive half of the bug.

The asymmetry that governs every judgement call here: a false `unknown` costs us one
signature; a false `failed` takes a buyer's money and returns an error. So anything
we cannot positively classify as a rejection is indeterminate.

No network — the chain and the facilitator are both stubbed. The one thing not
stubbed is the transaction decoding, which runs against real algosdk on a real
signed transaction, because that is where a silent wrong answer would come from.
"""

from __future__ import annotations

import base64
import json

import pytest
from algosdk import account, encoding, transaction

from authen.x402 import settlement
from authen.x402.settlement import (
    REJECTED,
    SETTLED,
    SETTLEMENT_HEADER,
    SETTLEMENT_TX_HEADER,
    UNKNOWN,
    ChainVerdict,
    confirm_on_chain,
    guard_settlement,
    is_definitive_rejection,
    payment_last_valid,
    payment_txid,
)

GENESIS = base64.b64encode(b"\x01" * 32).decode()


def _signed_payment(last_valid: int = 2000):
    """A real signed axfer, and the txid the chain will know it by."""
    sk, addr = account.generate_account()
    sp = transaction.SuggestedParams(
        fee=1000, first=last_valid - 1000, last=last_valid,
        gh=GENESIS, gen="mainnet-v1.0", flat_fee=True,
    )
    txn = transaction.AssetTransferTxn(
        sender=addr, sp=sp, receiver=addr, amt=150000, index=31566704
    )
    return encoding.msgpack_encode(txn.sign(sk)), txn.get_txid()


class _Payload:
    """The shape `process_settlement` receives: a payload with an AVM group."""

    def __init__(self, group, index=0):
        self.payload = type(
            "P", (), {"payment_group": group, "payment_index": index}
        )()


class _Requirements:
    network = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="


class _Result:
    """Stand-in for ProcessSettleResult as the SDK returns it."""

    def __init__(self, success, error_reason=None):
        self.success = success
        self.error_reason = error_reason
        self.headers = {}
        self.transaction = None
        self.network = None


class _Server:
    """Stand-in for x402HTTPResourceServerSync, with a swappable verdict."""

    def __init__(self, result):
        self._result = result
        self.calls = 0

    def process_settlement(self, payment_payload, requirements):
        self.calls += 1
        return self._result


class _Middleware:
    def __init__(self, server):
        self._http_server = server


# ---------------------------------------------------------------------------
# Deriving the txid — the part that talks to real algosdk
# ---------------------------------------------------------------------------


def test_payment_txid_matches_the_real_transaction_id():
    """A txid covers transaction fields, not the signature, so ours is the chain's.

    This is what makes the chain read exact rather than a search over recent
    transfers: we can name the transaction before it is submitted.
    """
    b64, txid = _signed_payment()
    assert payment_txid(_Payload([b64])) == txid


def test_payment_txid_honours_payment_index():
    """The paying leg is not always first; the fee-payer leg sits in the group too."""
    other, _ = _signed_payment()
    b64, txid = _signed_payment()
    assert payment_txid(_Payload([other, b64], index=1)) == txid


def test_payment_last_valid_is_read_from_the_transaction():
    b64, _ = _signed_payment(last_valid=4242)
    assert payment_last_valid(_Payload([b64])) == 4242


@pytest.mark.parametrize(
    "payload",
    [_Payload([]), _Payload(["not-base64"]), _Payload([_signed_payment()[0]], index=9)],
)
def test_undecodable_payloads_degrade_to_none_not_an_exception(payload):
    """An unreadable payload must become UNKNOWN, never a claim about the chain.

    Raising here would surface as a 500 on a paid request; returning a wrong txid
    would be worse still, since it would let the chain lookup answer confidently
    about the wrong transaction.
    """
    assert payment_txid(payload) is None
    assert payment_last_valid(payload) is None


# ---------------------------------------------------------------------------
# Classifying the facilitator's answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        "timeout",
        "D1_ERROR: D1 DB exceeded its CPU time limit",
        "Read timed out",
        "502 Bad Gateway",
        "Connection reset by peer",
        None,
        "",
        "something nobody has seen before",
    ],
)
def test_indeterminate_answers_are_not_rejections(reason):
    """Anything that is not a recognised adjudication is indeterminate.

    The unrecognised case matters most: the facilitator can invent a new error
    string at any time, and the default for a novel string must be `unknown`.
    """
    assert not is_definitive_rejection(reason)


@pytest.mark.parametrize(
    "reason",
    [
        "insufficient_funds",
        "ERR_INVALID_SIGNATURE",
        "recipient_mismatch",
        "network_mismatch",
        "transaction already in ledger",
    ],
)
def test_real_rejections_are_recognised(reason):
    """When the facilitator actually adjudicated, 402 is the honest answer."""
    assert is_definitive_rejection(reason)


# ---------------------------------------------------------------------------
# Reading the chain
# ---------------------------------------------------------------------------


def test_confirmed_transaction_reads_as_settled(monkeypatch):
    b64, txid = _signed_payment()
    monkeypatch.setattr(settlement, "_txid_confirmed", lambda cfg, t: t == txid)
    v = confirm_on_chain(None, _Payload([b64]))
    assert v.state == SETTLED and v.txid == txid


def test_absent_and_expired_reads_as_rejected(monkeypatch):
    """Past last-valid, an absent transaction can never confirm. That is decisive."""
    b64, txid = _signed_payment(last_valid=1000)
    monkeypatch.setattr(settlement, "_txid_confirmed", lambda cfg, t: False)
    monkeypatch.setattr(settlement, "_current_round", lambda cfg: 1001)
    v = confirm_on_chain(None, _Payload([b64]))
    assert v.state == REJECTED and v.txid == txid


def test_absent_but_still_valid_reads_as_unknown(monkeypatch):
    """Still in flight is not a failure, and must not be reported as one."""
    b64, _ = _signed_payment(last_valid=9999)
    monkeypatch.setattr(settlement, "_txid_confirmed", lambda cfg, t: False)
    monkeypatch.setattr(settlement, "_current_round", lambda cfg: 100)
    monkeypatch.setattr(settlement, "_POLL_BUDGET_SECONDS", 0.0)
    assert confirm_on_chain(None, _Payload([b64])).state == UNKNOWN


def test_unreachable_chain_reads_as_unknown_not_rejected(monkeypatch):
    """An indexer we cannot reach tells us nothing. It must not become a rejection.

    This is the same class of mistake as the original bug, one layer down: silence
    from a dependency is not a negative answer.
    """
    b64, _ = _signed_payment(last_valid=1000)
    monkeypatch.setattr(settlement, "_txid_confirmed", lambda cfg, t: False)
    monkeypatch.setattr(settlement, "_current_round", lambda cfg: None)
    monkeypatch.setattr(settlement, "_POLL_BUDGET_SECONDS", 0.0)
    assert confirm_on_chain(None, _Payload([b64])).state == UNKNOWN


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def _guarded(result, verdict, cfg=None):
    server = _Server(result)
    mw = _Middleware(server)
    guard_settlement(mw, cfg)
    return server, mw


def test_successful_settlement_is_passed_through_untouched(monkeypatch):
    """The happy path must not be reshaped; it already carries a real receipt."""
    ok = _Result(True)
    ok.headers = {"PAYMENT-RESPONSE": "real-receipt"}
    server, _ = _guarded(ok, None)
    called = []
    monkeypatch.setattr(
        settlement, "confirm_on_chain", lambda *a: called.append(1) or ChainVerdict(UNKNOWN)
    )
    out = server.process_settlement(_Payload([]), _Requirements())
    assert out is ok and not called, "the chain was read on a settlement that succeeded"


def test_definitive_rejection_is_still_a_failure(monkeypatch):
    """A real 'no' stays a 'no'. This fix must not become a way to serve for free."""
    bad = _Result(False, "insufficient_funds")
    server, _ = _guarded(bad, None)
    called = []
    monkeypatch.setattr(
        settlement, "confirm_on_chain", lambda *a: called.append(1) or ChainVerdict(UNKNOWN)
    )
    out = server.process_settlement(_Payload([]), _Requirements())
    assert out.success is False
    assert not called, "the chain was read on an answer the facilitator adjudicated"


def test_timeout_with_payment_on_chain_serves_the_buyer(monkeypatch):
    """The exact mainnet incident. Money landed, so the buyer gets their attestation."""
    b64, txid = _signed_payment()
    server, _ = _guarded(_Result(False, "timeout"), None)
    monkeypatch.setattr(
        settlement, "confirm_on_chain", lambda *a: ChainVerdict(SETTLED, txid, "")
    )
    out = server.process_settlement(_Payload([b64]), _Requirements())

    assert out.success is True, (
        "settlement succeeded on chain but the node still refused to serve"
    )
    assert out.headers[SETTLEMENT_HEADER] == SETTLED
    assert out.headers[SETTLEMENT_TX_HEADER] == txid
    assert out.transaction == txid


def test_chain_confirmed_settlement_emits_a_real_decodable_receipt(monkeypatch):
    """A confirmed settlement owes the buyer a PAYMENT-RESPONSE, not just our word.

    Written after the receipt-building block was found sitting behind a try/except
    with the import path wrong: it raised ImportError on every call, was swallowed,
    and served a chain-confirmed settlement with no receipt and no trace. Assert the
    header exists AND decodes back to the transaction we claim, so a moved SDK
    symbol fails here instead of silently downgrading real settlements.
    """
    from x402.http.constants import PAYMENT_RESPONSE_HEADER

    b64, txid = _signed_payment()
    server, _ = _guarded(_Result(False, "timeout"), None)
    monkeypatch.setattr(
        settlement, "confirm_on_chain", lambda *a: ChainVerdict(SETTLED, txid, "")
    )
    out = server.process_settlement(_Payload([b64]), _Requirements())

    receipt = out.headers.get(PAYMENT_RESPONSE_HEADER)
    assert receipt, "chain-confirmed settlement served without a PAYMENT-RESPONSE"

    decoded = json.loads(base64.b64decode(receipt + "=" * (-len(receipt) % 4)))
    assert decoded["success"] is True
    assert decoded["transaction"] == txid, "the receipt names a different transaction"


def test_timeout_with_expired_payment_stays_a_failure(monkeypatch):
    """The chain agreeing with the facilitator is the one case 402 is correct."""
    server, _ = _guarded(_Result(False, "timeout"), None)
    monkeypatch.setattr(
        settlement, "confirm_on_chain", lambda *a: ChainVerdict(REJECTED, "TX", "")
    )
    assert server.process_settlement(_Payload([]), _Requirements()).success is False


def test_unknown_serves_the_buyer_and_says_unknown(monkeypatch):
    """`unknown` is the required word. Never `failed`, and never a fake receipt."""
    server, _ = _guarded(_Result(False, "timeout"), None)
    monkeypatch.setattr(
        settlement, "confirm_on_chain", lambda *a: ChainVerdict(UNKNOWN, "TX", "")
    )
    out = server.process_settlement(_Payload([]), _Requirements())

    assert out.success is True, "the buyer was refused service on an unknown verdict"
    assert out.headers[SETTLEMENT_HEADER] == UNKNOWN

    from x402.http.constants import PAYMENT_RESPONSE_HEADER

    assert PAYMENT_RESPONSE_HEADER not in out.headers, (
        "an unknown settlement emitted a PAYMENT-RESPONSE receipt it cannot back"
    )


def test_no_verdict_ever_reports_the_word_failed(monkeypatch):
    """CLAUDE.md, verbatim: it must never report `failed` for a timeout."""
    for state in (SETTLED, UNKNOWN):
        server, _ = _guarded(_Result(False, "timeout"), None)
        monkeypatch.setattr(
            settlement, "confirm_on_chain", lambda *a, s=state: ChainVerdict(s, "TX", "")
        )
        out = server.process_settlement(_Payload([]), _Requirements())
        assert out.success is True
        assert "fail" not in str(out.headers).lower()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_the_live_app_installs_the_guard(cfg):
    """A fix nobody wired in is not a fix.

    The guard is installed by create_app after payment_middleware. If a refactor
    drops that call, every test above keeps passing and production regresses to
    taking money for nothing.
    """
    from authen.web.app import create_app

    app = create_app(cfg)
    mw = app.wsgi_app.__self__  # the PaymentMiddleware instance owning _wsgi_middleware
    assert mw._http_server.process_settlement.__name__ == "process_settlement"
    assert mw._http_server.process_settlement.__qualname__.startswith("guard_settlement"), (
        "create_app did not install the settlement guard; a facilitator timeout "
        "will again report failure on a payment that landed"
    )


def _verified_payment_client(cfg, monkeypatch, settle_result):
    """A live app whose payment check passes and whose facilitator returns `settle_result`.

    Stubs exactly two seams — the payment verdict and the facilitator's settle call —
    and leaves the entire WSGI path, the route handler, the response buffering and
    the guard running for real. That is what makes this test able to observe the
    thing unit tests cannot: whether the body survives.
    """
    from x402.http.types import HTTPProcessResult

    from authen.web.app import create_app

    app = create_app(cfg)
    mw = app.wsgi_app.__self__
    server = mw._http_server

    real_process = server.process_http_request

    def fake_process(context, paywall_config=None):
        out = real_process(context, paywall_config)
        if out.type != "payment-error" or out.response is None:
            return out
        # Reuse the challenge the server just built to recover real requirements.
        import base64 as _b64
        import json as _json

        from x402.http.constants import PAYMENT_REQUIRED_HEADER

        raw = out.response.headers.get(PAYMENT_REQUIRED_HEADER)
        challenge = _json.loads(_b64.b64decode(raw + "=" * (-len(raw) % 4)))
        from x402.schemas import PaymentRequirements

        return HTTPProcessResult(
            type="payment-verified",
            payment_payload=_Payload([]),
            payment_requirements=PaymentRequirements(**challenge["accepts"][0]),
        )

    monkeypatch.setattr(server, "process_http_request", fake_process)
    monkeypatch.setattr(server, "process_settlement", lambda *a: settle_result)
    # Reinstall the guard over the stubbed facilitator call.
    guard_settlement(mw, cfg)
    return app.test_client()


def test_end_to_end_a_timeout_still_delivers_the_attestation(cfg, monkeypatch):
    """The whole point, exercised through the real WSGI stack.

    The mainnet incident in one assertion: the facilitator times out, the payment is
    on chain, and the buyer must receive the attestation their money bought — not
    `402 {"error": "Settlement failed"}`.

    A unit test on `process_settlement` cannot catch the regression that matters
    here, because the body is discarded one layer up in the SDK's WSGI middleware.
    Only a request that goes all the way through can prove the attestation survives.
    """
    b64, txid = _signed_payment()
    monkeypatch.setattr(
        settlement, "confirm_on_chain", lambda *a: ChainVerdict(SETTLED, txid, "")
    )
    client = _verified_payment_client(cfg, monkeypatch, _Result(False, "timeout"))

    r = client.post(
        "/api/v1/notarize",
        json={"content_base64": base64.b64encode(b"paid for this").decode()},
    )

    assert r.status_code == 200, (
        f"buyer paid and got {r.status_code} {r.get_data(as_text=True)[:200]} — "
        "the settlement guard did not release the response"
    )
    body = r.get_json()
    assert body and body.get("attestation"), "200 returned with no attestation in it"
    assert r.headers.get(SETTLEMENT_HEADER) == SETTLED
    assert r.headers.get(SETTLEMENT_TX_HEADER) == txid


def test_end_to_end_an_unknown_verdict_still_delivers_the_attestation(cfg, monkeypatch):
    """Serving on `unknown` is a deliberate choice, so pin it.

    The node stores nothing, so re-serving an attestation costs one signature. That
    is the cheaper mistake by a wide margin: the alternative is charging a buyer and
    handing back an error, which is the behaviour this whole module deletes.
    """
    monkeypatch.setattr(
        settlement, "confirm_on_chain", lambda *a: ChainVerdict(UNKNOWN, "TX", "")
    )
    client = _verified_payment_client(cfg, monkeypatch, _Result(False, "read timed out"))

    r = client.post(
        "/api/v1/notarize", json={"content_base64": base64.b64encode(b"x").decode()}
    )
    assert r.status_code == 200
    assert r.get_json().get("attestation")
    assert r.headers.get(SETTLEMENT_HEADER) == UNKNOWN


def test_end_to_end_a_real_rejection_still_refuses(cfg, monkeypatch):
    """The guard must not become a way to get attestations without paying."""
    client = _verified_payment_client(
        cfg, monkeypatch, _Result(False, "insufficient_funds")
    )
    r = client.post(
        "/api/v1/notarize", json={"content_base64": base64.b64encode(b"x").decode()}
    )
    assert r.status_code == 402, "a genuinely rejected payment was served anyway"


def test_settlement_headers_are_exposed_to_browsers():
    """A header a browser payer cannot read cannot tell them anything."""
    from authen.web.app import _EXPOSE_HEADERS

    assert SETTLEMENT_HEADER in _EXPOSE_HEADERS
    assert SETTLEMENT_TX_HEADER in _EXPOSE_HEADERS


def test_indexer_is_configured_and_distinct_from_algod(cfg):
    """algod is not a substitute: it forgets confirmed transactions quickly."""
    assert cfg.network.indexer_url
    assert cfg.network.indexer_url != cfg.network.algod_url
    assert cfg.network.indexer_url.startswith("https://")
