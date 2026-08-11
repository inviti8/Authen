# PintheonV2 — Build Plan

**Repo:** `git@github.com:inviti8/PintheonV2.git` → `D:/repos/PintheonV2`
**Status:** empty repo, no commits. This document is the brief.
**Author of brief:** design session 2026-08-11. All facts below were verified
against live sources or local code on that date; anything unverified is marked.

---

## 0. TL;DR for the agent picking this up

You are building **Pintheon V2**: a self-hosted node that sells comics/print media
per page over the **x402 payment protocol on Algorand mainnet**, delivering
**bespoke per-buyer encrypted PWA readers** over IPFS.

Two things drive every decision:

1. **A hard deadline of 2026-09-01, 11:45pm EST.** A paid x402 endpoint must be
   live on Algorand mainnet, publicly reachable over HTTPS, settling through the
   GoPlausible facilitator, Bazaar-registered and tagged, with at least one real
   completed payment — or the project cannot enter the Global x402 Challenge at all.
2. **Pintheon V1 works.** Do not rewrite what already works (IPFS/MFS/IPNS
   plumbing, TinyDB schema, Soroban bindings). Port it. Rewrite only the parts
   listed in §5.

**Read §7 before writing code.** Two unresolved facts change the product design,
and both are cheap to resolve.

---

## 1. Local repo map

Everything you need is on disk. Paths are absolute.

### Primary

| Repo | Path | What it is |
|---|---|---|
| **PintheonV2** | `D:/repos/PintheonV2` | This repo. Empty. |
| Pintheon V1 | `C:/Users/surfa/Documents/metavinci/pintheon` | The working predecessor. Branch `mainnet` is current. **Your main source of portable code.** |
| Pintheon image gen | `C:/Users/surfa/Documents/metavinci/pintheon_image_gen` | Docker build. `Dockerfile.pintheon` clones `inviti8/pintheon` by `GIT_BRANCH` build arg; `supervisord.conf` runs nginx + gunicorn + `ipfs daemon`. Change the clone URL for V2. |
| hvym_stellar | `C:/Users/surfa/Documents/metavinci/hvym_stellar` | The crypto package (published to PyPI as `hvym-stellar`, V1 pins `0.23.0`). Source + `CRYPTO_SPEC.md`. **Model for the Algorand variant.** |
| aiposematic | `C:/Users/surfa/Documents/metavinci/aiposematic` | v1.1 reversible key-dependent image scrambling. Cipher keys derivable from `Stellar25519KeyPair`; ECDH for artist→subscriber key agreement. **This is the encryption primitive for page content.** |
| hvym_tunnler | `C:/Users/surfa/Documents/metavinci/hvym_tunnler` | VPS tunnel server (FastAPI + WebSocket + Redis + Stellar JWT). Serves nodes at `*.tunnel.hvym.link`. Read `docs/ARCHITECTURE.md`, `CUSTOM_DOMAINS.md`. |
| metavinci | `C:/Users/surfa/Documents/metavinci/metavinci` | PyQt tray daemon. Owns the Docker lifecycle and the tunnel client (`tunnel_client.py`, 620 LOC — **port this into V2**). |

### Supporting

| Repo | Path | Relevance |
|---|---|---|
| pintheon_contracts | `D:/repos/pintheon_contracts` | Soroban contract sources + bindings + `deployments.*.json`. Source of truth for contract IDs. |
| heavymeta_collective | `C:/Users/surfa/Documents/metavinci/heavymeta_collective` | Membership/enrollment web app. See `ACCOUNT_ABSTRACTION.md` for the non-custodial direction and `NFC_AUTH.md` for why browser-extension signing was rejected. |
| comics.heavymeta.art | `D:/repos/comics.heavymeta.art` | **Existing comics site with real content** (`content/titles`, founder's AQS titles). Has `PAYMENT_RAIL_PLAN.md`, a Cloudflare Worker federation server, and a Vite `pay/` app. Candidate content source for the flagship node. |
| hvym-market-muscle | `D:/repos/hvym-market-muscle` | Private strategy repo. `HEAVYMETA_THESIS.md` is the product thesis — read §5.1, §9.2, §9.3, §9.6, §9.9. `RAIL_CONCEPT.md` is **out of scope** (Stellar mobile wallet, not Pintheon). |
| metavinci_desktop | `C:/Users/surfa/Documents/metavinci/metavinci_desktop` | Desktop build wrapper. |
| hvym_pinner | `C:/Users/surfa/Documents/metavinci/hvym_pinner` | CID Hunter — consumes V1's `/api/pinned_files`. |
| lepus | `D:/repos/lepus` | Freenet fork for metadata datapods (thesis §5.2). Not in V2 scope. |
| heavymeta_docs | `C:/Users/surfa/Documents/metavinci/heavymeta_docs` | Docs site. |
| heavymeta-cli-dev | `C:/Users/surfa/Documents/metavinci/heavymeta-cli-dev` | CLI toolset. |

Also present under `C:/Users/surfa/Documents/metavinci/`: `carf`, `color_collapse`,
`freenet-lepus`, `glasswing`, `hvym-t-bot`, `river`, `vapor-merch`,
`pintheon_image_gen`. Under `D:/repos/`: `heavymeta`, `kenter`, `keycard-shell`,
`infinipaint`, `thespis`, `pelt`, `Nami`, `lottese`, `lupus`, `digenius`,
`DiffMorph`, `hvym_art_src`, `hvym-research-lab`, `aiposematic`, `inkternity-server`,
`status-keycard`.

---

## 2. The competition (facts, verified 2026-08-11)

Source: [Official Rules PDF](https://algorand.co/hubfs/x402%20competition%20Official%20Rules.pdf),
[challenge page](https://algorand.co/global-x402-challenge).

### Registration gate — the only hard deadline

This is an **eligibility check, not a judged milestone.** Rules §6: *"To be eligible
to participate in the Program, each Entrant must have a paid x402 endpoint that is
deployed and reachable on Algorand Mainnet, and such endpoint must use the
GoPlausible facilitator so that transactions and volume are automatically tracked
in a public dashboard for the Competition."* Nothing about product quality is
assessed on this date — all four judged criteria are evaluated later, from the
project info submitted 09-02 → 09-29 plus leaderboard activity through 10-08.

**The endpoint's code may evolve freely after registration. The payTo address may
not.** It is the key the leaderboard aggregates volume under; changing it splits
your history into two entries, neither of which ranks. Choosing and opting in that
mainnet account is the one irreversible decision in Phase 1 — treat it as such, and
never use a placeholder.

**Go live early.** Volume accrues from first settlement, and the shortlist window
runs to 10-08. With rank #20 currently at 14 settled payments, being live in
mid-August rather than 08-31 is close to free leaderboard position.

By **2026-09-01, 11:45pm EST** you must have:

- [ ] Paid x402 endpoint deployed and reachable on **Algorand Mainnet**
- [ ] Using the **GoPlausible facilitator** (so volume auto-tracks on the public dashboard)
- [ ] Publicly hosted over **HTTPS** — no localhost
- [ ] payTo address **opted into USDC, ASA `31566704`**
- [ ] **Bazaar** discovery extension enabled
- [ ] Tagged `x402-global-challenge`
- [ ] **≥1 real mainnet payment that fully settles** — from Algorand's how-to-submit
      blog guidance rather than rules §6 verbatim; do it regardless
- [ ] Registration form submitted

### Prizes

| Pool | Size | Decided by |
|---|---|---|
| Judged | $100K — 1st $25K, 2nd $22.5K, 3rd $20K, 4th $17.5K, 5th $15K | Panel scores 10 finalists on 4 **evenly weighted** criteria |
| Leaderboard | 500,000 ALGO across **top 20** | Volume, subject to anti-wash review |

### The four judged criteria — 25% each

1. **Volume** — USDC processed through the endpoint
2. **Use case quality** — x402 in the **core** payment flow, not bolted onto a
   product that works without it
3. **Sustained potential** — credible path to usage continuing after the prize period
4. **Innovation** — novel payment flow, agentic commerce use case, or infra pattern

**Finalist gate:** top **50** on the leaderboard **and** all project info submitted.
Top-50 is a threshold, not a ranking.

### Timeline

| Date | Milestone |
|---|---|
| **2026-09-01 23:45 EST** | Registration closes. Live mainnet endpoint required. |
| 2026-09-02 → 09-29 | Final Presentation registration (submit project info) |
| 2026-09-30 → 10-08 | Shortlist window |
| 2026-10-09 | Finalists notified |
| 2026-11-02 | Final Presentation, Devcon 8 India |
| by 2026-11-12 | Winners announced |

### Anti-gaming

Rules §14 explicitly reserve the right to exclude "artificial volume, wash
transactions, repeated self-payments, or other activity intended to manipulate
leaderboard results." **All volume must be real.** Do not simulate traffic.

### Field size — why this is winnable

Pulled live from `https://facilitator.goplausible.xyz/discovery/resources` on
2026-08-11 (1,148 resources total, paginate with `?limit=1000&offset=N`):

- **1,003** resources on Algorand mainnet, but only **48 distinct payTo addresses**.
  payTo is the leaderboard's aggregation unit.
- Only **42** payTos have ≥1 settlement. Six have zero.
- **Rank #20 = 14 settled payments.** Rank #30 = 3. Ranks 43–48 = 0.
- Rank #1 (`api.syraa.fun`, 33,074 settles) is **79% of all mainnet volume**.
- Growth: 14 resources first seen June, 673 July, 316 in the first 11 days of August.
- The Bazaar contains junk — `localhost:3000` and `127.0.0.1:4021` are registered
  and cannot satisfy the public-HTTPS rule.

**Implication:** top-50 is currently free; top-20 currently costs ~15 real settled
payments. With pay-per-page, one reader finishing one 24-page issue clears rank #20
as the board stands today. The field will grow before October, but from 48 payTos.

Re-run this query before making pricing decisions — the numbers will have moved.

---

## 3. Pintheon V1 — what exists

Read the code at `C:/Users/surfa/Documents/metavinci/pintheon`. Summary so you know
what to look for.

### Shape

Flask app (`pintheon.py`, ~1,400 lines) + `PintheonMachine` (`pintheonMachine/__init__.py`,
~2,545 lines) + Kubo IPFS + Stellar/Soroban.

- **State machine** (`transitions`): `spawned → initialized → establishing → idle`,
  plus `handling_file`, `redeeming`. Constructed at `__init__.py:233`.
- **DB:** TinyDB with Fernet-encrypted JSON storage (`enc_db.json`). Master key in
  `pintheon.ini` next to the DB. Tables: `file_book`, `node_data`, `customization`,
  `stellar_book`, `token_book`, `access_tokens`, `peer_book`, `namespaces`,
  `state_data`, `node_keys`.
- **`file_book` row shape** (`__init__.py:2311`): `Name`, `Type`, `Encrypted`,
  `Hash`, `CID`, `ContractID`, `Size`, `IsLogo`, `IsBgImg`, `Balance`,
  `ReceiverPub`, `Directory`, `IPNSHash`, `Pinned`, `PinSlotId`, `PinQty`,
  `PinsRemaining`, `PinOfferPrice`.

### Key call sites

| What | Where |
|---|---|
| App wiring / `PINTHEON` singleton | `pintheon.py:45` |
| Upload → IPFS → pin → MFS → IPNS → DB | `__init__.py:2259` `add_file_to_ipfs` |
| MFS dir → IPNS auto-publish | `__init__.py:1931` `_auto_publish_directory_to_ipns` |
| IPNS publish | `__init__.py:1861` `publish_mfs_to_ipns` |
| Encrypted share (7z + ECDH password) | `__init__.py:1360` `stellar_shared_archive` |
| Access token mint | `__init__.py:2148` `add_access_token` |
| Access token verify | `__init__.py:2177` `authorize_access_token` |
| Seed → Stellar keypair | `__init__.py:1262` `_create_stellar_keypair_from_seed` |
| Node bootstrap | `__init__.py:1387` `create_new_node` |
| Contract IDs from on-chain registry | `__init__.py:293` `_load_contract_ids_from_registry` |
| Dashboard payload | `__init__.py:2434` `get_dashboard_data` |

### Identity today

One Stellar ed25519 keypair drives everything: blockchain account, X25519
encryption (via `Stellar25519KeyPair`), macaroon signing, access tokens. The seed
is **provisioned by Metavinci** — a launch macaroon posted to `/new_node`, then
`set_seed()` → `Keypair.from_secret(seed)`. **V2 must generate its own seed.**

### Serving topology

```
nginx :9999 (private) → /admin + all mutation routes → gunicorn
nginx :9998 (public)  → homepage, /static/, /ipfs|/ipns → Kubo :8082
```

Pintheon is **not** in the data path for file retrieval.

### hvym_stellar API (`hvym_stellar/__init__.py`)

| Class | Line | Purpose |
|---|---|---|
| `Stellar25519KeyPair` | 183 | `SigningKey(raw_secret)` → `.to_curve25519_private_key()` / `verify_key.to_curve25519_public_key()` |
| `StellarSharedKey` | 283 | X25519 ECDH box; `encrypt`, `asymmetric_encrypt`, `hash_of_shared_secret` |
| `StellarSharedDecryption` | 453 | Counterpart |
| `StellarSharedKeyTokenBuilder` | 636 | Macaroon tokens signed from the ECDH secret |
| `StellarSharedKeyTokenVerifier` | 843 | Verification + caveats |
| `FileCaveatVerifier` | 1141 | Size / type / hash caveats |
| `HVYMDataToken` | 1240 | Biscuit-based file tokens |

**Critical fact:** the package touches `stellar_sdk.Keypair` at exactly two points —
`raw_secret_key()` and the base64 pub encoding. Algorand addresses **are** ed25519
public keys (base32 + 4-byte checksum), so an `hvym_algorand` sibling is a thin
port and every class above carries over unchanged.

---

## 4. Known bugs and traps in the existing system

Do not rediscover these.

| # | Issue | Location | Consequence |
|---|---|---|---|
| 1 | **Tunnel corrupts binary.** `body.decode("utf-8", errors="replace")` inbound; `response.text` outbound | `hvym_tunnler/app/api/routes.py:132`; `metavinci/tunnel_client.py` `_forward_to_local` | Images/video cannot be served through the tunnel. Fix by base64-armoring non-UTF-8 bodies on both ends. |
| 2 | **Response header whitelist** `_ALLOWED_RESPONSE_HEADERS` (15 entries) | `hvym_tunnler/app/api/routes.py:24` | `X-PAYMENT-RESPONSE` is silently stripped. Inbound `X-PAYMENT` is fine (`dict(request.headers)` forwards everything). |
| 3 | **`location @app` is dead code.** Public vhost `location /` does `try_files $uri $uri.html $uri/ =404` with no `@app` fallback | `pintheon/setup_pintheon.sh` | Any new public Flask route is unreachable on :9998 until the nginx config is fixed. |
| 4 | 30s httpx timeout, full buffering, no range support in the tunnel | `metavinci/tunnel_client.py` | Fine for JSON, bad for media. |
| 5 | Tunnel identity ≠ collective membership key | see `hvym_tunnler/TUNNLER_MEMBER_AUTH.md` | Known gap. V2 self-tunnels with the node keypair, which fixes it. |

**Note:** #1–#4 are **off the critical path for the competition**, because the
flagship node runs on a VPS with a real IP and certificate and does not need the
tunnel. They matter for the artist self-host product.

---

## 5. V2 architecture

### Decisions already locked

| # | Decision | Rationale |
|---|---|---|
| 1 | New repo, Algorand-native, **tunnel client absorbed into the node** | Drops Metavinci as a hard requirement; node self-tunnels with its own keypair |
| 2 | Product is **comics / print media**, delivered as a bespoke per-buyer **PWA** | Buyer owns their copy; installs offline |
| 3 | **Node holds all keys and acts on behalf of the app** | Target users are artists, not crypto-natives (thesis §9.2). The node is the artist's own machine, so this is not third-party custody |
| 4 | Sealing target **defaults to the payer's address-derived X25519 key** | Algorand addresses are ed25519 pubkeys → convert to X25519. The payment itself delivers the buyer's key; zero registration |
| 5 | **Signal-space fingerprinting only** for v1 | Full multi-domain (CLIP/VAE) is minutes per image and cannot live inside a synchronous 402. Thesis §9.9 concedes signal-space is the practical tier |
| 6 | **Flagship node on a VPS**, separate from the self-host container | Competition needs multi-week uptime through the Oct 8 shortlist window |
| 7 | **`aiposematic` scramble is the free public preview** | One artifact markets the work, poisons scrapers, and defines the paywall boundary |
| 8 | **Do not use Onsen UI.** V1's admin dashboard stays behind; rebuild it in the September window | Reader PWA is new code regardless; admin UI is not judged |

### Module layout (proposed — adjust as you learn)

```
pintheonv2/
├── ledger/           # chain abstraction
│   ├── base.py       #   Ledger protocol: address, sign, balance, opt-in
│   ├── algorand.py   #   algosdk — primary money rail
│   └── stellar.py    #   port from V1 — provenance/registry ledger
├── keys/
│   ├── identity.py   #   one seed → ed25519 → Algorand + Stellar + X25519
│   └── hvym_algorand #   sibling of hvym_stellar (may graduate to its own package)
├── x402/
│   ├── server.py     #   402 responses, X-PAYMENT parsing, verify/settle
│   ├── facilitator.py#   GoPlausible client
│   └── bazaar.py     #   discovery extension + registration
├── content/
│   ├── scramble.py   #   aiposematic wrapper
│   ├── fingerprint.py#   signal-space DCT watermark
│   └── pwa.py        #   bespoke reader build → IPFS
├── storage/
│   ├── ipfs.py       #   PORT FROM V1 — add/pin/MFS/IPNS
│   └── db.py         #   PORT FROM V1 — TinyDB encrypted
├── tunnel/           # PORT from metavinci/tunnel_client.py
├── web/              # Flask app, routes, admin (no Onsen)
└── reader/           # the PWA shell (separate build)
```

### The crypto design

Content encryption uses `aiposematic`'s reversible scrambling, keyed per buyer:

1. Creator uploads pages. Node stores originals encrypted at rest.
2. Public preview = one heavily-scrambled version per page, on IPFS, free. This is
   the marketing surface: shareable, crawlable, and it degrades scraper training data.
3. On payment, node scrambles that page with a key derived for **that buyer**, adds
   a signal-space watermark, uploads, returns the CID + the sealed key.

### The bespoke PWA

Three parts, added to **one UnixFS directory per buyer**:

| Part | Scope | Content |
|---|---|---|
| Shell | Shared | Reader UI, service worker, manifest, decryption code. Not copyrighted. |
| Index | Per buyer | Page order, title, metadata, wrapped key blob |
| Pages | Per buyer | Each page scrambled with that buyer's key |

Each buyer gets a distinct root CID — a genuinely separate app they own — while
IPFS **block-level dedup stores the shared shell exactly once**. Bespoke ownership
and storage efficiency fall out of the same operation.

Service workers need a real origin for install-to-homescreen; path-based gateway
URLs (`/ipfs/<cid>/`) scope awkwardly. Serve from the node's own origin.

### Key delivery — the crux

Sealing to the payer's address-derived X25519 key requires the decrypting client to
hold the matching **private** key, i.e. the Algorand seed. An agent has that.
**A human using Pera or Defly does not** — wallets don't export seeds and don't do
X25519 ECDH.

One parameter (sealing target), two paths:

- **Human buyers (primary):** checkout generates an X25519 keypair **in-browser**.
  The public key is the sealing target sent with the payment; the private key lives
  in IndexedDB and becomes the PWA's decryption key. Payment identity and decryption
  identity decouple — better for privacy, works with every wallet. Recovery via
  passphrase-wrapped export.
- **Agent buyers:** default the sealing target to the payer's address-derived key.

**Honesty note for the submission:** because the node performs the encryption, it
necessarily knows each buyer's key at render time. "Only the buyer can decrypt" is
really "only the buyer, plus the node at the moment of minting." Do not repeat
thesis §5.1's stronger framing in public materials.

### Pay-per-page UX

The buyer must not wait on 24 confirmations. Two viable shapes:

**Shape A — one signature, atomic group.** The `exact` spec allows up to 16
top-level transactions per group, each signed individually, submitted atomically.
The reader pays once for a 16-page block: one wallet interaction, sixteen `axfer`
transfers land together. Simple; no funding step.

**Shape B — session account, silent micropayments.** The PWA generates an ed25519
keypair in-browser; the buyer funds it once; the app then signs each page payment
itself with zero interaction, prefetching a page or two ahead so ~3s finality never
lands in front of the reader. **The reader becomes an autonomous paying agent** —
strong on the Innovation criterion.

Shape B's cost: the session account needs a small ALGO balance to exist and to
opt into USDC. The facilitator sponsors *fees* (`extra.feePayer`), but ASA opt-in
minimum-balance is separate, and that friction lands on a comics reader who may
never have touched Algorand.

The same in-browser ed25519 key can serve as both payer and decryption identity —
collapsing the two problems into one, echoing thesis §5.2's unified-keypair idea.

**Which shape to build depends on §7 Q1.** Do not guess.

---

## 6. Phase plan

### Phase 0 — resolve the unknowns (do this first, ~1 day)

See §7. Both questions are cheap and both change the design.

### Phase 1 — clear the registration gate (target: 2026-08-20, hard stop 09-01)

**The gate does not require Pintheon.** It requires a paid endpoint. Ship the
narrowest thing that satisfies §2, on a VPS, as a standalone service if that is
faster. The rules do not require the endpoint to stay unchanged.

- [ ] VPS provisioned, DNS, TLS cert
- [ ] **Algorand mainnet account generated, funded, opted into USDC ASA `31566704`.
      This address is permanent — see §2. Not a placeholder. Back up the seed.**
- [ ] Minimal paid endpoint returning 402 → verify → settle via GoPlausible
- [ ] Bazaar discovery extension declared, tagged `x402-global-challenge`
- [ ] One real mainnet payment settled end to end
- [ ] Registration form submitted

**Do not proceed to Phase 2 until this is done.** Everything else is recoverable;
this deadline is not.

### Phase 2 — the product (2026-09-01 → 09-29)

- [ ] `keys/identity.py` — one seed → Algorand + Stellar + X25519
- [ ] `hvym_algorand` port
- [ ] Port `storage/ipfs.py` and `storage/db.py` from V1
- [ ] `content/scramble.py` — aiposematic integration
- [ ] `content/pwa.py` — bespoke reader build → IPFS
- [ ] Reader PWA shell (no Onsen)
- [ ] Pay-per-page endpoint replacing the Phase 1 stub
- [ ] Real comics loaded from `D:/repos/comics.heavymeta.art/content/titles`
- [ ] Project info submitted by **09-29**

### Phase 3 — usage + polish (09-29 → 10-08 shortlist window)

- [ ] Drive real readers to the flagship node
- [ ] Admin dashboard rebuild (replaces Onsen)
- [ ] Absorb tunnel client; fix tunnel bugs §4.1/§4.2 for the self-host story
- [ ] Docker image via `pintheon_image_gen` pointed at this repo

### Phase 4 — finals (→ 2026-11-02)

- [ ] Presentation. Lead with use case quality, sustained potential, innovation.

---

## 7. Unresolved — resolve before building product code

**Q1. How does the leaderboard rank — USDC value, or settlement count? And is a
16-transfer atomic group counted as 1 settlement or 16?**

This decides pricing and which pay-per-page shape to build. If ranked by value,
fractional-cent pages are the wrong optimization and Shape A is strictly simpler.
If ranked by count, Shape B's per-page settlements matter. The facilitator
dashboard at `https://facilitator.goplausible.xyz/dashboard/leaderboards` is a JS
app and was not machine-readable in the design session; probe the API under
`/docs` (OpenAPI) or ask GoPlausible directly.

**Q2. Does `x402-avm` 2.0.2's Flask middleware handle group construction and Bazaar
registration, or must we call `/verify` and `/settle` ourselves?**

Determines whether the Phase 1 endpoint is ~200 lines or ~600.
`pip install x402-avm[flask]`. Known API surface: `x402ResourceServerSync`,
`HTTPFacilitatorClientSync`, `FacilitatorConfig`, `ExactAvmServerScheme`,
`x402.extensions` (Bazaar discovery).

**Q3. Can the facilitator verify/settle a group with a second recipient** (artist
cut + co-op fee in one atomic group)? The spec's `paymentIndex` names a single
transaction as the one paying the resource server. Needs a testnet probe. Not
blocking for Phase 1.

**Q4. Which comics go on the flagship node?** `D:/repos/comics.heavymeta.art/content/titles`
holds real content; `PAYMENT_RAIL_PLAN.md` names the founder's AQS titles as the
pilot. Real books score materially better on use case quality than placeholder art.
Confirm rights and selection with the founder.

---

## 8. Technical reference

### Constants

| Thing | Value |
|---|---|
| Facilitator | `https://facilitator.goplausible.xyz/` (OpenAPI at `/docs`) |
| Algorand mainnet CAIP-2 | `algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=` |
| Algorand testnet CAIP-2 | `algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=` |
| USDC mainnet ASA | `31566704` (6 decimals) |
| USDC testnet ASA | `10458941` |
| Facilitator fee payer | `ZMFK2OI7ZBD2U27ISERZC4S6LKM6WMFJPZQ4MYNJDZ2VNBNMBA67RA22AA` |
| x402Version | `2` |
| Required tag | `x402-global-challenge` |
| Python SDK | `x402-avm` 2.0.2 — extras `flask`, `fastapi`, `httpx`, `requests`, `avm`, `extensions` |
| Scheme spec | `x402-foundation/x402` → `specs/schemes/exact/scheme_exact_algo.md` |
| Legacy net ids | `algorand-mainnet` / `algorand-testnet` map automatically |

Legacy Stellar constants worth carrying: registry contract
`CA6KQ5GYGI33VZB5IGWW7XXLLHR2MPEBWVDREU4P5ZGCSKRGHXBCRKXV` (mainnet), from which V1
resolves `hvym_collective`, `opus_token`, `hvym_pin_service` at boot
(`__init__.py:293`).

### x402 wire format

Client sends `X-PAYMENT`:

```json
{
  "x402Version": 2,
  "scheme": "exact",
  "network": "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=",
  "payload": {
    "paymentGroup": ["<base64 msgpack txn>", "..."],
    "paymentIndex": 1
  }
}
```

Server returns `X-PAYMENT-RESPONSE` on success:

```json
{
  "success": true,
  "errorReason": null,
  "payer": "<address>",
  "transaction": "<txid>",
  "network": "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="
}
```

`PaymentRequirements` fields: `scheme`, `network` (CAIP-2), `amount` (string,
microunits), `payTo` (58-char base32), `maxTimeoutSeconds`, `asset` (ASA ID as
string, **not** an ERC20 address), optional `extra.feePayer`.

Payment transaction must be `axfer` with `aamt` = amount, `arcv` = payTo,
`xaid` = ASA ID, `snd` = payer. Max **16** top-level transactions per group; each
signed individually (Ed25519, k-of-n multisig, or LogicSig). Facilitator simulates
the group, then submits via `v2/transactions`. Receiver **must be opted into the
ASA** before it can receive.

Verification order per spec: x402Version → scheme match → network match → group
size ≤16 → msgpack decode → validate `paymentGroup[paymentIndex]` → identify
facilitator `pay` txns → simulate.

### Useful probes

```bash
# What the facilitator supports (schemes, networks, fee payers)
curl -s https://facilitator.goplausible.xyz/supported

# The Bazaar — live competitive intel, paginate with limit/offset
curl -s "https://facilitator.goplausible.xyz/discovery/resources?limit=1000&offset=0"
# fields per item: id, resourceUrl, method, description, mimeType, merchantId,
#                  accepts[], discoveryInfo, verifyCount, settleCount,
#                  firstSeen, lastSeen
```

---

## 9. Thesis alignment (why this product, not another one)

From `D:/repos/hvym-market-muscle/HEAVYMETA_THESIS.md`:

- **§5.1 Bespoke one-to-one model** — every piece of content uniquely encrypted per
  subscriber via per-subscriber ECDH. This is the core commitment; V2 honours it.
- **§9.6 (MEDIUM):** per-subscriber encryption doesn't scale — 10,000 subscribers ×
  10 images/week = 100,000 encryptions, most never downloaded. **x402 dissolves
  this**: the buyer arrives at purchase time, so you encrypt once, for one buyer,
  after they've paid. Bespoke encryption becomes a per-transaction cost with
  revenue attached.
- **§9.3 (HIGH):** leaving Web2 loses algorithmic discovery. **The Bazaar is a
  partial answer** — a machine-readable, non-gatekept discovery index for paid
  resources. It won't bring human fans; it makes creator content discoverable to
  the agent economy.
- **§9.2 (HIGH):** target users are artists, not crypto-natives. Hide the crypto.
  Satisfied by node-custodied keys acting on behalf of the application.
- **§9.9:** signal-space fingerprint detection is practical; model-level detection
  against foundation training is not. Set expectations accordingly.

`RAIL_CONCEPT.md` in that repo describes a **Stellar mobile wallet**, not Pintheon.
Its no-custody constraints do **not** bind this design. Confirmed by the founder.

---

## 10. Working agreements

- **Verify before asserting.** Every number in §2 came from a live source on
  2026-08-11. Re-check anything load-bearing; the field data moves weekly.
- **Do not simulate payment volume.** Rules §14 makes wash traffic a
  disqualification risk.
- **Port, don't rewrite,** the IPFS/MFS/IPNS layer and the TinyDB schema. They work.
- **Phase 1 beats elegance.** A live endpoint on 2026-09-01 is worth more than any
  amount of architecture that misses it.
