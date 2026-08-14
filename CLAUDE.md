# Authen

Paid **notarization and content provenance** over the **x402 payment protocol on
Algorand mainnet**. An agent POSTs bytes, pays, and receives a signed, timestamped
attestation it can verify offline — and that anyone else can verify for free.

## One-paragraph orientation

Authen sells a capability agents do not have: durable, verifiable memory. An LLM
agent can generate opinions cheaply; it cannot attest that a specific artifact
existed at a specific moment, signed by a key anyone can check. Phase 1 is the
notary — SHA-256 plus an Ed25519 detached signature in
`b64url(sig).b64url(payload)` form. C2PA manifest signing for images
(`c2pa-python`) shipped alongside it. Phase 2 is paid storage and transfer plus
on-chain time anchoring — see **Phase 2** below, which supersedes the older
"Phase 2 adds IPFS pinning" framing.

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
  hostname**. 13 of 78 merchants already span multiple hosts. The id is
  `b64(payTo[:24])` — confirmed by decoding live rows.
- The SDK's default client selector is **`accepts[0]`, unconditionally**
  (`client_base.py:99`, "Default selector: return first requirement"). Servers may
  send a list (`http/types.py:169`), but no stock client will choose among them, and
  `accepts` is resolved from static route config at registration
  (`x402_http_server_base.py:144`) with no per-request price hook. **Price tiers must
  therefore be separate routes, not multiple `accepts` entries** — the latter would
  silently charge every caller the first price regardless of what they sent.

Measured and NOT true yet:

- **The node is not in the Bazaar.** As of 2026-08-13, after the first mainnet
  payment settled: all 5,963 resources on `/discovery/resources` contain neither
  `authen.hvym.link` nor our payTo; `/discovery/merchants/{b64(payTo[:24])}` and
  `/discovery/resources/{b64url("POST:<url>")}` both 404. The 402 challenge *does*
  carry `extensions.bazaar`, and the middleware registers the extension
  (`middleware/flask.py:314-316`), so the gap is between "we serve it" and "the
  facilitator catalogs it" — not a missing declaration. **This is a 2026-09-01 gate
  item.** Re-check with the derived-id lookups above; do not infer listing from the
  challenge containing the extension.

## House rules

- **Verify before asserting.** The field data above moves weekly; re-measure it.
- **Never simulate payment volume.** Wash traffic is a disqualification risk.
- **Never overclaim what an attestation proves.** It says "this node observed bytes
  with this digest at this time" — not authorship, not ownership, not prior
  existence. The first counterexample discredits every attestation ever issued, so
  the narrow wording in `authen/notary.py` is load-bearing, not boilerplate.
- **Verification stays free. Bytes are not.** Checking an attestation costs nothing
  and always will. Retrieving stored content is a paid route — decided 2026-08-13.
  The two are different products and only the first is a house rule.
- **Never announce stored content to the IPFS DHT.** If a CID is resolvable from
  `ipfs.io`, the paid download route is decoration — anyone with the CID fetches the
  bytes from a public gateway for free. This is a `Routing.Type`/reprovider setting
  that reads as a harmless default to anyone who does not know it is load-bearing.
- **One payTo.** No `DynamicPayTo`, no second recipient in the payment group,
  before 2026-10-08.
- **The payTo private key never touches the server.** The `exact` scheme only needs
  payTo as asset receiver (`arcv`).
- **The node's signing key is self-generated on first boot**, never injected by
  env. Record its public half in config so a silently regenerated identity fails
  loudly instead of quietly invalidating every attestation.

## Phase 2 — storage, transfer, anchoring (decided 2026-08-13)

The product is agent-to-agent file handoff with provenance attached: A pays to
store bytes and gets an attestation, B pays to fetch them and can verify offline,
for free, that they are exactly the bytes A registered and when. Two agents that
have never met need no shared account and no credential exchange — the payment is
the authorization. That is the part S3 presigned URLs cannot do.

**No Kubo for now — blobs keyed by SHA-256 on local disk.** IPFS was considered for
durability and for scaling past the VPS's 60 GiB SSD. On durability it does not
help: a single-node pin is exactly as durable as a file on the same disk, because
nothing replicates a CID unless another operator chooses to pin it, and none will.
On scaling the argument is better — a self-run `ipfs-cluster` fleet is a real
answer — but so are a bigger block volume and an S3-compatible store, and R2's
zero egress bears directly on a paid-download product. The property that keeps all
of those open is **content addressing, not IPFS**: with blobs keyed by SHA-256 the
backing store can move to Kubo, a cluster, R2, or a larger volume without touching
the API or invalidating a single attestation. So the option is preserved and the
operational cost is not paid during the window where uptime is a judged input.
Arithmetic behind "not yet": ~45 GiB usable is ~1,440 objects at the 32 MiB
ceiling, ~46,000 at 1 MiB. If we hit that wall before 10-08, something has gone
very right and the migration is cheap. If Kubo does land, take `ipfs-cluster` for
the pinset rather than scripting Kubo directly, and see the no-announce house rule.

**Anchoring needs no contract.** The weak point in the product today is that `t` is
whatever the node's clock said, signed by the node — we could backdate an
attestation and nobody could tell. Anchoring a digest to a block round makes the
time claim checkable by someone who trusts Algorand instead of trusting us, which
is the one claim the whole product rests on. A 0-ALGO self-payment with the digest
in the note field is a permanent, indexed, timestamped record: no PyTeal, no
deployment, no upgrade path to maintain. Batch digests into a Merkle root published
on a schedule and serve inclusion proofs from a free endpoint — cost stays O(1) per
batch, and the paid path never blocks on algod being reachable.

The node's identity key is already an Algorand address
(`7UORPWQ7ZRLLJ7DOO4XETVC4Y6NOOTNQMAWFPQIHMHRZROQVMV2IYO3Q2U`), so the key that
signs attestations can sign the anchor transaction. That means **a funded hot key
on the server** — permitted, because it is not payTo and payTo stays cold, but keep
the balance minimal and say so in config.

**PyTeal belongs to the registry, not to anchoring.** Box storage plus
`ed25519verify_bare` is the right shape only where queryable state and revocation
semantics are needed — "is key K registered, by whom, still valid" — which a note
field cannot give. That is the real `hvym-cert-registry` port, it is the most
expensive item here, and it already works on Stellar. Ship with the registry still
on Soroban and explain the one-key-two-chains property rather than half-porting it.

Order, cheapest-first and highest-value-first: **anchoring → storage routes →
registry port**, with the report's streaming work (`AUTHEN_API_REPORT.md` §4)
underneath storage — it gets load-bearing the moment bodies start hitting disk.

**Still open — do not treat the numbers below as decided.** Retention is
unconfirmed; the proposal is 30 days, long enough for a handoff and short enough
that one payment covers what it costs, with `/renew` as a real route rather than an
apology. Proposed ladder, constrained by the $0.50 observed market maximum and by
tiers-must-be-separate-routes: notarize $0.05 flat at any size (hashing is O(1)
once streamed — the cost is bytes held over time, not bytes hashed);
store 1/8/32 MiB at $0.05/$0.15/$0.40; fetch $0.01; renew at tier price. Whatever
retention is chosen must be returned in the store response and stated in the Bazaar
description — an agent that does not know the object expires will build as if it
does not.

Two risks this opens that Phase 1 did not have: unbounded egress if a fetch is ever
free (one payment, unlimited retrievals), and hosting arbitrary third-party bytes on
a box tied to a real identity — retention limits, no open gateway, and a working
unpin path before the first upload, not after the first incident.

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
`ed25519verify_bare` is **still open, and deliberately last** (see Phase 2).
Single-chain is a materially stronger story for an Algorand competition, and one
Ed25519 key is already both a Stellar and an Algorand address, so existing
identities survive a move whenever it happens. What is **no longer open** is the
half of that question people conflate with it: per-notarization time anchoring goes
on Algorand via a note-field transaction and needs no contract on either chain.
