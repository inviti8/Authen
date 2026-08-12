# Authen

Paid **notarization and content provenance** over the **x402 payment protocol on
Algorand mainnet**. An agent POSTs bytes, pays, and receives a signed, timestamped
attestation it can verify offline — and that anyone else can verify for free.

## One-paragraph orientation

Authen sells a capability agents do not have: durable, verifiable memory. An LLM
agent can generate opinions cheaply; it cannot attest that a specific artifact
existed at a specific moment, signed by a key anyone can check. Phase 1 is the
notary — SHA-256 plus an Ed25519 detached signature in
`b64url(sig).b64url(payload)` form. Phase 2 adds IPFS pinning, C2PA manifest
signing for images (`c2pa-python`), and registration of the node's app-CA in the
`hvym-cert-registry` trust contract.

## Hard deadline

**2026-09-01, 11:45pm EST** — a paid x402 endpoint must be live on Algorand
mainnet, publicly reachable over HTTPS, settling through the GoPlausible
facilitator, Bazaar-registered and tagged `x402-global-challenge`, with at least
one real completed payment. This is an **eligibility check, not a judged
milestone** — nothing about product quality is assessed on that date. The four
judged criteria are evaluated later from project info submitted 09-02 → 09-29 plus
leaderboard activity through 10-08.

## History — this was Pintheon

The repo began as PintheonV2, selling comic issues per-issue over x402. That was
dropped on 2026-08-12 after measuring the live field: across **1,204 listed
resources the median price is $0.005, the p90 is $0.08, and the maximum is $0.50 —
not one resource above $1**. A $3.00 comic issue is six times the most expensive
thing on the rail. x402 is a machine-to-machine API micropayment market; no agent
shops for comics, and no creator will hand a crypto-native paywall URL to their
audience.

The comics code is parked in `shelved/`, not deleted. `BUILD_PLAN.md` and
`IMPLEMENTATION_PLAN.md` describe that product and are **superseded** — read them
for the x402 protocol findings (which still hold) and ignore the comics specifics.

## What is verified, and what is not

Verified on the wire, not assumed:

- Full settlement on Algorand testnet against the live facilitator, twice.
- The request header is **`PAYMENT-SIGNATURE`**. Not `PAYMENT`, not v1's
  `X-PAYMENT`. Send the wrong name and the server sees *no payment at all*: it
  re-challenges with a generic "Payment required" that is indistinguishable from a
  rejection, while `/verify` returns `isValid: true` for the same payload.
- The tag belongs at **`accepts[].extra.tag`**.
- The x402 SDK ships three paywall bundles and **defaults to EVM**. The EVM bundle
  parses `algorand:<genesis>` as an EVM chain id, throws `Unsupported chain ID:
  NaN`, and renders a blank page. `create_app()` must pass an `AvmPaywallHandler`.
  Pinned by `tests/test_paywall.py`.
- `merchantId` derives from payTo, so leaderboard volume aggregates on **payTo, not
  hostname**. 13 of 78 merchants already span multiple hosts.

## House rules

- **Verify before asserting.** The field data above moves weekly; re-measure it.
- **Never simulate payment volume.** Wash traffic is a disqualification risk.
- **Never overclaim what an attestation proves.** It says "this node observed bytes
  with this digest at this time" — not authorship, not ownership, not prior
  existence. The first counterexample discredits every attestation ever issued, so
  the narrow wording in `authen/notary.py` is load-bearing, not boilerplate.
- **Verification stays free.** An attestation nobody can afford to check is worth
  nothing.
- **One payTo.** No `DynamicPayTo`, no second recipient in the payment group,
  before 2026-10-08.
- **The payTo private key never touches the server.** The `exact` scheme only needs
  payTo as asset receiver (`arcv`).
- **The node's signing key is self-generated on first boot**, never injected by
  env. Record its public half in config so a silently regenerated identity fails
  loudly instead of quietly invalidating every attestation.

## Related repos

| Repo | What it holds |
|---|---|
| `D:/repos/pintheon_contracts` | `mock_c2pa/` — runnable app-CA generation, leaf issuance, chain verification. `HVYM_CERT_REGISTRY.md`. |
| `heavymeta_collective` | Shipped portal side: Soroban bindings, bundle parser, canonical payloads, token mint. |
| `D:/repos/infinipaint` | `docs/design/C2PA.md` — desktop-side plan, C++ port targets. |
| `D:/repos/heavymeta` | Flutter co-op wallet, Stellar, zero-custody. |

The `hvym-cert-registry` contract is live on **Stellar** mainnet
(`CAKBTT765YCBZDPU7RNPGC4C4TSXIRFHQCEBNPEQZNMJCLXAB3K6VE2G`) and testnet
(`CC252R637U7QXG5SSHTVHBSKB3PGKRKRP66EEI2IEVTXIQWP6EQRLH2T`). Whether Authen's
registry stays on Soroban or ports to Algorand box storage +
`ed25519verify_bare` is **open** — single-chain is a materially stronger story for
an Algorand competition, and one Ed25519 key is already both a Stellar and an
Algorand address, so existing identities survive a move.
