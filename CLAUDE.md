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
(`c2pa-python`) shipped alongside it. Phase 2 is on-chain time anchoring plus an
MCP client — see **Phase 2** below, which supersedes both the older "Phase 2 adds
IPFS pinning" framing and the paid-storage plan that briefly replaced it.

**Authen does not store content, and that is a product decision, not a gap.** It
hashes bytes, signs a statement about them, and discards them inside the request.

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

- **The 402 challenge travels in the `PAYMENT-REQUIRED` response header**, base64 of
  the JSON, and the *body is `{}`*. An empty body on a 402 is normal and is not
  evidence of anything. This wasted a full diagnostic pass: an empty `{}` was read as
  "the node rejected the request locally and never called the facilitator", when the
  challenge had been in the header all along. Decode the header before concluding
  anything about a 402.

- **Bazaar listing achieved 2026-08-14T13:45Z**, after three separate silent
  defects. `/discovery/merchants/RTY0QlFJT1hLVFQ0QlZNSUZZMlM1V1gz` returns name,
  website, logo and categories; `/api/v1/notarize` is cataloged; the merchant row
  reads `bazaar: true`. **The 2026-09-01 gate item is closed.**

  All three defects were invisible to the SDK's own `validate_discovery_extension`,
  which returned `valid=True` for two of them. Each one silently skips cataloging
  while challenge, /verify and settlement all behave normally:

  1. `declare_discovery_extension` with no `body_type` builds a *query* shape —
     `method` enum `["GET","HEAD","DELETE"]`, input under `queryParams` — and
     `enrich_declaration` then injects `POST` into it. Self-invalid.
  2. The catalog validator wants the schema's `method` enum to **equal** the
     declared method, not contain it. The SDK emits the whole verb family. See
     `pin_method()` in `authen/x402/server.py`.
  3. The declared `body` must be an **object**. `body_type="text"` produces a
     string body, which is the honest description of a raw-bytes endpoint and is
     uncataloguable. Both paid routes now genuinely accept a base64 JSON envelope
     so the object declaration is true rather than aspirational.

  **The x402 Doctor found 2 and 3 in one run each, after a night of inference got
  the first one right and the second wrong.** It is reachable from the merchant
  page and it is the authoritative oracle — trust it over the SDK validator, and
  over reasoning about cataloged resources, whose `discoveryInfo` does not expose
  the `schema` where these failures live. Re-run it after any declaration change.

Measured and NOT true yet:

- **`/api/v1/c2pa/sign` is not cataloged, and the declaration is not why.** Verified
  2026-08-15T00:00Z by pulling both live 402 challenges and diffing them: c2pa/sign
  passes `validate_discovery_extension`, parses as `BodyDiscoveryExtension`, and is
  structurally identical to notarize on every rule the catalog is known to enforce —
  `method` enum `["POST"]`, object body, object output. Searched the **entire**
  catalog (1352 of 1352 rows) rather than trusting a computed resource id: exactly
  one Authen row, notarize. Stop re-checking the declaration.

  What the facilitator's own transaction feed says (`/data/transactions?q=authen`)
  kills the tempting theory that this is the settlement bug below in disguise:
  **c2pa/sign has two successful settles with tx hashes**, `03:34:17Z` and
  `14:13:09Z`, the second of them *after* the declaration fix that got notarize
  cataloged at `13:45:07Z`. So it settled cleanly, with a correct declaration, and
  was never cataloged. Ten hours, so not lag.

  The one hypothesis still standing is the catalog's own instability — its total
  swung 1187 → 1636 → 4039 → 4041 → 1265 → **1352** alongside `D1_ERROR: D1 DB
  exceeded its CPU time limit`. It now reads 1352 three times running and the
  facilitator reports healthy, so the retry condition recorded here is **met**: a
  further $0.15 is now a real experiment rather than a repeat, because catalog
  stability is the one variable that changed. Land the settlement guard first (done,
  see below) so a timeout cannot corrupt the result.

- **Every free facilitator tool operates only on already-cataloged resources**, so
  none of them can diagnose an uncataloged route. This was learned the expensive
  way, by recording "run the Doctor against `/api/v1/c2pa/sign`" as a next action
  that turns out not to be runnable. The Doctor returned `endpoints: [notarize]`
  only, and Refresh reported `probed: 1`. Both derive their target list from the
  catalog. **Only a settled payment adds a resource.** Diagnose an uncataloged route
  by diffing its live challenge against a cataloged one instead.

  Both are plain HTTP and need no browser: `POST /data/merchants/{id}/doctor`
  (20/day) and `POST /data/merchants/{id}/refresh` (10/day).

- **The `/data/*` and `/discovery/*` namespaces key merchants by different ids.**
  Ours is `RTY0QlFJT1hLVFQ0QlZNSUZZMlM1V1gz` under `/discovery/*` and
  `85e5f1fc3935dd01` under `/data/*`; using the wrong one returns a bare
  `404 {"error":"not found"}` that reads exactly like "no such merchant". Same trap
  as the caip2-vs-slug asymmetry already noted above. Find the `/data` id by
  scanning `/data/merchants?limit=200` for the name.

- **The facilitator disagrees with itself about our volume**, which is a sharper
  report than the undercount noted before because it needs no reference to our chain
  data. Three sources, three numbers, measured together:

  | Source | Settles | Volume |
  |---|---|---|
  | Algorand chain | 7 | $0.65 |
  | `/data/transactions` | 6 | $0.50 |
  | `/data/merchants` | 5 | $0.45 |

  The chain-to-facilitator gap is the timed-out settle (`UB5F3X3RTQGZZG4VCIJS…`,
  14:09:15Z) which never reached them at all. But their **own two endpoints differ by
  one $0.05 settle**, reproducible against their API alone. Immaterial at these
  amounts, material to anyone ranked on volume, and it is our money going uncounted
  on a leaderboard we are judged on. Lead with the self-inconsistency; unlike the
  50-row cap this one is self-interested and specific.

Fixed since:

- **Authen no longer reports settlement failure on settlement success.** Found by the
  Obolus agent, verified on chain: `UB5F3X3RTQGZZG4VCIJS…`, round 64062555, $0.15
  taken, nothing served. Fixed in `authen/x402/settlement.py`, pinned by
  `tests/test_settlement_guard.py` (36 tests, verified non-vacuous by disabling the
  guard and confirming 7 fail).

  **Two corrections to what was recorded here before.** First, the sketched fix —
  "an outer WSGI layer wrapping `payment_middleware`" — does not work: an outer layer
  sees only the finished 402 and cannot recover what the SDK already discarded. The
  interception has to be on the *return value of* `process_settlement`, which is the
  last point where the buffered 2xx body is still alive. Second, the SDK's
  `process_settlement` already catches every exception itself
  (`x402_http_server_base.py:433`), so a timeout arrives at the `success=False`
  branch (`middleware/flask.py:428`), not the `except` at 441.

  The harm was also worse than recorded. Beyond the false verdict, **the SDK discards
  the attestation it is already holding** — by settlement time the handler has
  returned 2xx and the signed attestation sits in `body_chunks`. We took the money
  and threw away the goods in the same code path.

  Reading the chain is exact and needs no search: the payment payload carries the
  buyer's *signed* transaction, and an Algorand txid hashes the transaction fields,
  not the signature, so the id can be derived locally. `last_valid` then makes a
  negative answer decisive — absent past that round, it can never confirm.

      txid on chain               -> settled    serve the goods, real receipt
      absent and past last_valid  -> rejected    402 is honest, pass it through
      anything else               -> unknown     serve the goods, say `unknown`

  Serving on `unknown` is deliberate: the node stores nothing, so re-serving costs
  one signature, against the alternative of charging a buyer and returning an error.
  Unrecognised facilitator error strings default to indeterminate for the same
  asymmetry. This needs `indexer_url` per network profile — algod is not a
  substitute, it forgets confirmed transactions quickly.

## House rules

- **Verify before asserting.** The field data above moves weekly; re-measure it.
- **Never simulate payment volume.** Wash traffic is a disqualification risk.
- **Never overclaim what an attestation proves.** It says "this node observed bytes
  with this digest at this time" — not authorship, not ownership, not prior
  existence. The first counterexample discredits every attestation ever issued, so
  the narrow wording in `authen/notary.py` is load-bearing, not boilerplate.
- **Verification stays free.** Checking an attestation costs nothing and always
  will. An attestation nobody can afford to check is worth nothing, and the free
  verify route is also what makes the paid one discoverable.
- **The node persists no third-party bytes.** Content arrives, is hashed, and is
  gone when the request ends. Nothing is written to disk, nothing is retained,
  nothing is served back. This is what keeps Authen out of the intermediary-liability
  business entirely — no retention policy, no takedown queue, no abuse process, and
  no path by which someone else's content can get the node suspended during a judged
  uptime window. Reversing it is a Phase 3 conversation with a threat model attached,
  not a feature addition. See **Phase 2** for why storage was dropped.
- **One payTo.** No `DynamicPayTo`, no second recipient in the payment group,
  before 2026-10-08.
- **The payTo private key never touches the server.** The `exact` scheme only needs
  payTo as asset receiver (`arcv`).
- **The node's signing key is self-generated on first boot**, never injected by
  env. Record its public half in config so a silently regenerated identity fails
  loudly instead of quietly invalidating every attestation.

## Phase 2 — anchoring and an MCP client (decided 2026-08-13)

**Paid storage was designed and dropped the same day, and never committed.** What
follows is why, so nobody re-derives it from scratch in three weeks.

The pitch was agent-to-agent file handoff with provenance attached — A pays to store
bytes, B pays to fetch them and can verify offline for free that they are exactly
what A registered. Three things killed it:

- **The liability is operational, not criminal.** The realistic bad day is not
  prosecution; it is a VPS provider or registrar acting on an abuse report in hours,
  unilaterally, with no appeal and no interest in whether we were negligent. Uptime
  through 2026-10-08 is a judged input and the same box serves notarize, C2PA,
  verify and identity, so there is no blast-radius separation. That trade would be
  worth making for real money.
- **There is no real money.** On this rail the median resource is $0.005 and the p90
  is $0.08 — the same field data that killed the comics product. Storage during the
  competition window plausibly earns lunch money while consuming the scarcest
  resource we have, which is days before the gate.
- **Content inspection cannot be the control.** Any classifier is defeated by
  `openssl enc` before upload, and the portal's gate
  (`heavymeta_collective/image_moderation.py`) detects *nudity*, which is legal,
  rather than CSAM, which is the actual liability and which it has no age signal for.
  Real coverage means PhotoDNA or NCMEC hash lists, and both have registration lead
  times measured in weeks.

**What replaces it: transfer without hosting.** A puts bytes wherever it likes — R2,
S3, its own node, a plain URL — and pays Authen to attest the digest. B fetches from
wherever and verifies free and offline. We supply the trust, they supply the bytes.
The earlier claim that presigned URLs cannot do agent-to-agent handoff was wrong:
they handle the no-shared-account part fine. What they cannot do is prove the bytes
are what A said they were and when. That was always the valuable half; storage was
the commodity half bolted on.

**An MCP server is the distribution channel, and it is a client, not a product.**
A free, open-source MCP server holding a wallet that pays per tool call is the most
on-thesis demo this competition can have: `notarize(path)` reads a local file, pays,
and writes the attestation beside it. Do **not** mistake it for a replacement for the
paid endpoint — that idea was considered and does not work. x402 monetizes an HTTP
resource *you* serve, so client-side code has nothing to gate and contributes zero
leaderboard volume unless it calls paid routes; and client-side IPFS breaks the
handoff premise outright, because B can only fetch while A is online and reachable,
and agents are ephemeral. Keep the free routes (verify, identity, anchor proofs)
working with no wallet configured, so the server is useful before it costs anything.
The honest cost of this path is that a funded wallet on the caller's machine is a
real adoption hurdle.

**The storage option stays cheap to revisit.** The reason SHA-256 content addressing
was chosen over Kubo was that the backing store can move to Kubo, `ipfs-cluster`, R2
or a bigger volume without touching the API or invalidating a single attestation.
That property is why dropping storage costs nothing later — it can come back on a box
that is not carrying a judged uptime window. If it ever does, the entry cost is the
whole trust-and-safety apparatus: hash-list matching, quarantine-not-delete (a
takedown that shreds bytes destroys evidence there may be a duty to preserve),
payer-address binding, and an abuse contact. That is the price, and it is why this is
Phase 3 at the earliest.

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

Order, cheapest-first and highest-value-first: **anchoring → MCP client → registry
port**. Anchoring goes first because it fixes the one genuine weakness in the product
as shipped, and because it is the only item on the list with no external dependency.

Pricing is settled and unchanged: notarize $0.05, C2PA signing $0.15, verification
and identity free. Both paid prices are live on mainnet and there is no reason to
churn them. The size-tier ladder drafted for storage is void along with storage, and
so is the constraint that forced it — with no per-size pricing, the
`accepts[0]`-selector finding above stops binding on us, though it stays true.

One consequence worth noting: the streaming work in `AUTHEN_API_REPORT.md` §4 drops
from load-bearing to optional. It was going to matter the moment bodies started
hitting disk, and now they never do. `MAX_BODY_BYTES` at 32 MiB and the matching
nginx cap still bound worst-case memory, so the report's items 3 and 4 are a
robustness improvement rather than a prerequisite.

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
