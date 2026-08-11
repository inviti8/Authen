# PintheonV2 — Implementation Plan

**Supersedes the open questions in `BUILD_PLAN.md` §7.** Read `BUILD_PLAN.md` first
for the product brief; this document is the build order.

**Written:** 2026-08-11. All facts in §1 were pulled live from the GoPlausible
facilitator and from `x402-avm` 2.0.2 installed locally on that date.
**Days to registration deadline:** 21.

---

## 0. Executive summary

- **`BUILD_PLAN.md` §7 Q1 and Q2 are both resolved.** See §1. Neither blocks code.
- **One finding inverts a core strategic premise.** The leaderboard ranks by **USDC
  volume, not settlement count**. Pay-per-page at fractional cents maximises the one
  metric nobody scores — §2.
- **Pricing is per issue, not per page.** One payment, one settlement, one bespoke
  PWA. This is simultaneously the simpler build and the better-ranking one: it deletes
  atomic-group construction, session accounts, and the pay-per-page latency problem
  outright — §2.
- **Phase 1 is smaller than feared.** `x402-avm`'s Flask middleware handles 402
  generation, verify, settle, and Bazaar registration declaratively. The registration
  endpoint is ~200 lines, not ~600 — §3.
- **The finalist gate is currently free.** The entire Algorand mainnet merchant field
  is **50 addresses**. "Top 50" is every merchant that has ever settled.
- **The VPS flagship is a sequencing decision, not a retreat from self-hosting.** Only
  the mint endpoint needs uptime at all; delivered PWAs, previews and catalogue do not.
  Self-hosting on creator hardware is a scoped Phase 3 deliverable and needs one small
  tunnel fix, not a rewrite — §5.1, §5.2.
- Three corrections to `BUILD_PLAN.md` §8's technical reference — §1.4. One of them
  (header names) silently breaks the tunnel path.

---

## 1. Resolved questions

### 1.1 Q1 — the leaderboard ranks by USDC volume ✅

The dashboard is a JS app, but it is backed by a documented JSON API. The spec lives
at `https://facilitator.goplausible.xyz/docs/openapi.json` (**not** `/openapi.json`,
which 404s), and exposes a `/data/*` analytics family. The relevant call:

```bash
curl -s "https://facilitator.goplausible.xyz/data/leaderboards\
?range=all&network=algorand-mainnet&cat=merchants&limit=50"
```

`cat` accepts `merchants | payers | resources | assets | networks | countries`.
`merchants` is the competition's aggregation unit (it keys on payTo address).

**Verified 2026-08-11:** the returned list is strictly monotonic descending by
`volume` and **not** monotonic by `settles`. The ordering is unambiguous:

| Rank | Volume USDC | Settles | Host |
|---:|---:|---:|---|
| 1 | 13,429.00 | 134,361 | x402-quant-signals.onrender.com |
| 2 | 3,229.10 | 32,293 | x402-trading-news.onrender.com |
| 3 | 1,832.84 | 17,839 | api.syraa.fun |
| **4** | **1,525.02** | **67** | x402-echo-service.vercel.app |
| 5 | 243.01 | 144 | api.algofile.io |
| **6** | **241.14** | **20,593** | api.x402node.dev |
| **16** | **17.79** | **17,790** | algate-x402.up.railway.app |
| 20 | 4.63 | 45 | api.micropay.website |
| 50 | 0.13 | 9 | api.algofile.io |

Rank 4 beats rank 6 with **67 settlements against 20,593**. Rank 16 has more
settlements than all but two entrants and ranks 16th. Settlement count does not rank.

**The sub-question — does a 16-transfer atomic group count as 1 settlement or 16 —
stops mattering.** Sixteen transfers of $0.10 contribute $1.60 of volume under either
counting rule. Group construction is now a UX decision, not a scoring decision.

**Field totals** (`/data/totals` and `/data/ecosystem`, `network=algorand-mainnet`,
`range=all`):

| Metric | Value |
|---|---:|
| Merchants (distinct payTo) | **50** |
| Resources | 823 |
| Settled volume | $22,211.26 |
| Settlements | 246,351 |
| Unique payers | 422 |
| Settle success rate | 99.91% |
| Avg settle latency | 365 ms |

Of the top 50, **34 carry `challenge: true`** and 33 carry `bazaar: true`. The
leaderboard response exposes both flags per merchant — useful for tracking the real
competitive field rather than the whole Bazaar.

> **Note on `BUILD_PLAN.md` §2's numbers.** The brief's field data (1,003 resources,
> 48 payTos, rank #1 = 33,074 settles at api.syraa.fun, rank #20 = 14 settles) came
> from summing `settleCount` across `/discovery/resources`. That is a different
> counter from the `/data/*` event store, and it produces a different ordering — the
> brief's rank #1 is actually rank #3 by volume. The merchant count (~48 → 50) is the
> one figure that reconciles. **Use `/data/leaderboards` for all future field checks**;
> it is what the competition dashboard renders.

### 1.2 Q2 — `x402-avm` 2.0.2's Flask middleware is sufficient ✅

Installed clean into a Python 3.13 venv: `pip install "x402-avm[flask,avm,extensions]"`.
Pulls `py-algorand-sdk` 2.11.1, `pydantic` 2.13.4, `PyNaCl`, `msgpack`. No native
build steps, no version pins fought.

**The middleware handles verify and settle.** The entry point:

```python
payment_middleware(
    app,                              # Flask
    routes,                           # RoutesConfig
    server,                           # x402ResourceServerSync
    paywall_config=None,
    paywall_provider=None,
    sync_facilitator_on_start=True,   # fetches /supported on first request
) -> PaymentMiddleware
```

It wraps the WSGI stack, buffers the response, and settles **before** releasing the
body to the client (`ResponseWrapper` in `x402/http/middleware/flask.py`). We do not
call `/verify` or `/settle` ourselves.

**Bazaar registration is automatic and declarative.** `_check_if_bazaar_needed()`
scans the route config for `extensions["bazaar"]`; if present,
`_register_bazaar_extension()` attaches `bazaar_resource_server_extension` to the
server. Cataloguing then happens facilitator-side — `/verify`'s own OpenAPI
description reads *"Auto-catalogs resources via Bazaar extension."* There is no
separate registration call to make.

Routes are a plain TypedDict:

```python
class RouteConfig(TypedDict):
    accepts: PaymentOption | list[PaymentOption]
    resource: str | None
    description: str | None
    mime_type: str | None
    custom_paywall_html: str | None
    unpaid_response_body: UnpaidResponseBody | None
    extensions: dict[str, Any] | None
    hook_timeout_seconds: float | None

class PaymentOption(TypedDict):
    scheme: str
    pay_to: str | DynamicPayTo
    price: Price | DynamicPrice
    network: Network
    max_timeout_seconds: int | None
    extra: dict[str, Any] | None
```

`price` and `pay_to` both accept **dynamic** variants — per-title and per-page pricing
is supported natively, no custom middleware. Build the discovery block with
`declare_discovery_extension(input=..., input_schema=..., body_type=..., output=...)`.

**Verdict: the Phase 1 endpoint is ~200 lines.**

### 1.3 Q3 — reframed, and it is now a *warning*, not a question

Q3 asked whether the facilitator can settle a group with a second recipient (artist
cut + co-op fee). Two things now bear on it:

1. `PaymentOption.pay_to` accepts `DynamicPayTo`, so the SDK would let us route
   per-artist.
2. **The leaderboard aggregates by payTo address.** `BUILD_PLAN.md` §2 already flags
   that changing payTo splits your history into two non-ranking entries.

**Therefore: do not use `DynamicPayTo`, and do not add a second recipient to the
payment group, before 2026-10-08.** Every cent of competition volume must land on the
single registered payTo or it fragments across leaderboard entries. Artist splits are
a **downstream sweep** — the node receives to one address and pays artists out on its
own schedule, off the x402 path.

This is a better design anyway: it decouples payout policy from payment protocol, and
it removes the group-construction risk from the critical path. Revisit multi-recipient
groups after the shortlist window closes.

### 1.4 Corrections to `BUILD_PLAN.md` §8

| # | Brief says | Actually | Impact |
|---|---|---|---|
| 1 | Headers are `X-PAYMENT` / `X-PAYMENT-RESPONSE` | x402 **v2** uses `PAYMENT-SIGNATURE` (request), `PAYMENT-REQUIRED` (402 challenge), `PAYMENT-RESPONSE` (receipt). The `X-`-prefixed names are **v1 legacy aliases**, still present in `x402.http.constants` for back-compat. | Real. `BUILD_PLAN.md` §4 bug #2 says the tunnel's `_ALLOWED_RESPONSE_HEADERS` strips `X-PAYMENT-RESPONSE`; the header that actually gets stripped is `PAYMENT-RESPONSE`. Whitelist **both**. |
| 2 | Tag `x402-global-challenge` — location unspecified | It lives at **`accepts[].extra.tag`** — i.e. inside `PaymentOption.extra`, next to `feePayer`. Verified across the live Bazaar: 850 of 882 tagged resources use `extra.tag`; a handful also mirror it into `resource.tags[]` and `extensions.bazaar.info.tags[]`. | Registration gate item. Get this wrong and the entry is untagged. |
| 3 | — | `/discovery/resources?network=` requires the **CAIP-2 id**; the `algorand-mainnet` slug silently returns `total: 0`. `/data/*` endpoints take the **slug**. | Silent-empty-result trap when scripting field checks. |

Also worth knowing: `/supported` reports `"extensions": []` even though the Bazaar
extension demonstrably works. Do not gate on that field.

---

## 2. Pricing — per issue

`BUILD_PLAN.md` §5 and V1's `X402_DESIGN.md` §6 both assert: *"Pay-per-page is the
volume engine. A 24-page issue at fractional-cent pricing is 24 paid calls instead of
1."* Given §1.1, that is backwards. **Every scored dimension measures USDC value:**

- Leaderboard pool (500,000 ALGO, top 20) — volume.
- Judged criterion 1 of 4 — "USDC processed through the endpoint".

Fractional-cent pricing maximises settlement count, which appears on no scorecard.
`algate-x402` is the cautionary case: 17,790 settlements, rank 16, $17.79.

**Decision: the unit of sale is the issue.** One payment, one settlement, one bespoke
PWA. Per-page pricing is dropped.

| Product | Price | Unit |
|---|---|---|
| Full issue — bespoke encrypted PWA | **$3.00** | one `axfer`, one settlement |
| Page preview (scrambled) | free | marketing surface / paywall boundary |
| Catalogue | free | agent discovery |

**What that buys, against today's board:** top 20 costs $4.63 — two issues. Top 10
costs ~$46, about 15 issues. Top 5 costs ~$243, roughly 80 issues — the first target
that needs genuine readership rather than a demo. Re-run the leaderboard query before
fixing the price; the board will move.

### Why this is strictly better, not merely simpler

Per-issue was already the higher-volume option ($3.00 against $1.20 for a 24-page
issue at $0.05/page). Collapsing to it also deletes a large amount of the hardest
work in the brief:

| Deleted | Was needed for |
|---|---|
| Atomic 16-transaction group construction | Shape A pay-per-page |
| In-browser session account + funding step | Shape B pay-per-page |
| Session-account ASA opt-in friction | Shape B — the friction that landed on a comics reader who had never touched Algorand |
| Page prefetch to hide ~3s finality | Both shapes |
| `BUILD_PLAN.md` §5's entire "Pay-per-page UX" problem | — |

The payment becomes a single `axfer`: one wallet interaction, one signature, no group
at all. The 16-transaction group limit stops being a design constraint.

**The unit of sale now matches the unit of delivery.** `BUILD_PLAN.md` §5 already
defines the bespoke PWA as one UnixFS directory per buyer containing the shared shell,
a per-buyer index, and that buyer's encrypted pages. That directory *is* an issue.
One payment produces exactly one artifact with one root CID.

### What this costs

**The innovation narrative loses "the reader is an autonomous paying agent."** That
was Shape B's contribution. What carries criterion 4 instead — and this is the
stronger claim anyway — is the bespoke artifact: every buyer receives a distinct root
CID, a genuinely separate application they own, while IPFS block-level dedup stores
the shared shell exactly once. Novel infrastructure pattern, and it is the thesis §5.1
commitment made real rather than a payment-frequency trick.

Agentic commerce still appears: the issue is a Bazaar-discoverable resource an agent
can buy unattended, with the sealing target defaulting to the payer's own key (§4.5).

**One new technical consequence — see §4.6.** Per-issue means the node must render
~24 pages inside the window of a single request, where per-page meant one. That moves
render latency onto the critical path and is the main thing this simplification makes
harder.

> **Anti-gaming, precisely.** Rules §14 prohibits "artificial volume, wash
> transactions, **repeated** self-payments". The registration gate simultaneously
> *requires* ≥1 real settled payment. These are consistent: a small number of genuine
> functional test payments to prove the endpoint settles is required and fine. A loop
> that manufactures volume is disqualifying. Do the former, never the latter.

---

## 3. Phase 1 — clear the registration gate

**Target 2026-08-20. Hard stop 2026-09-01 23:45 EST.** Nothing in Phase 2 begins
until this is signed off.

### 3.1 Scope decision

Build the Phase 1 endpoint **inside this repo**, as a self-contained
`pintheonv2/x402/` module with its own `wsgi.py` — not as a throwaway service in a
separate repo. Rationale: `x402-avm` does the heavy lifting, so the "narrow standalone
thing" and "the real module" are the same ~200 lines. A separate repo would be thrown
away and re-verified against mainnet later, which is pure risk.

**Ship a real product surface, not a stub.** The Phase 1 endpoint is
**pay-per-issue delivery for one real title**: free scrambled page previews, paid
full-resolution issue. That is the core payment flow — which is exactly what judged
criterion 2 ("x402 in the *core* flow, not bolted on") measures — and it upgrades to
the bespoke PWA in Phase 2 without changing the payment path or the price. Per-buyer
encryption and PWA packaging are the parts that slip to Phase 2; Phase 1 returns the
same issue to every buyer.

### 3.2 Task list

**A. The irreversible step — do this first, carefully.**

- [ ] Generate the Algorand mainnet account. **Back up the 25-word mnemonic offline,
      twice, before it touches anything.**
- [ ] Fund with ALGO for minimum balance (~0.2 ALGO covers the account + one ASA).
- [ ] Opt into USDC, ASA `31566704`. *A payTo that is not opted in cannot receive —
      settlement fails at the facilitator's simulate step.*
- [ ] Record the address in `deployments.mainnet.json`. **This address is permanent
      for the life of the competition.** Never substitute a placeholder.
- [ ] Verify opt-in on-chain before proceeding.

**B. Infrastructure.**

- [ ] VPS provisioned (2 vCPU / 4 GB is ample for Phase 1). **This is a sequencing
      decision — it keeps the tunnel off the critical path. It does not weaken the
      self-host premise; see §5.1.** The same image runs on creator hardware in §5.2.
- [ ] DNS A record → public hostname.
- [ ] TLS via certbot. **Public HTTPS is a gate item; no localhost, no self-signed.**
- [ ] nginx → gunicorn. Do **not** reuse V1's `setup_pintheon.sh` vhost as-is —
      `BUILD_PLAN.md` §4 bug #3: `location /` does `try_files ... =404` with no `@app`
      fallback, so a new Flask route is unreachable. Write a clean vhost.
- [ ] nginx must pass through `PAYMENT-SIGNATURE` inbound and expose
      `PAYMENT-RESPONSE` + `Access-Control-Expose-Headers` outbound.
- [ ] systemd unit + restart-on-failure. Uptime must survive to 2026-10-08.

**C. The endpoint.**

- [ ] `pintheonv2/x402/server.py` — `x402ResourceServerSync` +
      `HTTPFacilitatorClientSync` pointed at `https://facilitator.goplausible.xyz/`.
- [ ] Route config for `GET /api/v1/issue/<title>`:
      - `accepts.network` = `algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=`
      - `accepts.pay_to` = the address from step A (static — see §1.3)
      - `accepts.price` = `3000000` micro-USDC ($3.00, 6 decimals)
      - `accepts.extra` = `{"asset": "31566704", "decimals": 6,
        "feePayer": "ZMFK2OI7ZBD2U27ISERZC4S6LKM6WMFJPZQ4MYNJDZ2VNBNMBA67RA22AA",
        "tag": "x402-global-challenge"}`
      - `extensions` = `declare_discovery_extension(...)` — **required**, this is what
        triggers Bazaar registration
      - `max_timeout_seconds` — set generously (≥120). The per-buyer render window
        lives inside it once Phase 2 lands; see §4.6.
      - `description` — write it for an agent audience; it is the Bazaar listing copy
- [ ] Free preview route `GET /api/v1/preview/<title>/<n>` — scrambled page, no
      payment. Markets the work and defines the paywall boundary.
- [ ] `GET /api/v1/titles` — free catalogue, so agents can discover what is purchasable.
- [ ] Phase 1 issue payload format. A zip of page images is adequate and becomes the
      PWA directory in Phase 2 without changing the payment path. Keep `mime_type` honest.

**D. Content.**

- [ ] One title staged from `D:/repos/comics.heavymeta.art/content/titles`
      (**pending rights confirmation — see §7**).
- [ ] Scrambled previews generated via `aiposematic` (Phase 1 uses a single fixed
      scramble key; per-buyer keying is Phase 2).
- [ ] Pages served from local disk. IPFS is Phase 2 — do not put Kubo on the Phase 1
      critical path.

**E. Verify on testnet, then go live.**

- [ ] Full flow against Algorand **testnet** first: CAIP-2
      `algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=`.
      Same facilitator, same fee payer. See §3.4 for funding — it is not like Stellar.
- [ ] Cut over to mainnet.
- [ ] **One real mainnet payment**, end to end, from a separate funded account.
      Confirm `PAYMENT-RESPONSE` returns `success: true` with a txid.
- [ ] Confirm the txid resolves at `/api/receipt/{txId}`.
- [ ] Confirm the resource appears in `/discovery/resources` with
      `extra.tag == "x402-global-challenge"`.
- [ ] Confirm the payTo appears in `/data/leaderboards?...&cat=merchants` with
      `challenge: true`.
- [ ] **Submit the registration form.**

### 3.4 Testnet funding — Algorand has no Friendbot

Verified 2026-08-11. Unlike Stellar, where Friendbot is an unauthenticated `GET`,
**every Algorand testnet faucet is now gated against automation**:

| Endpoint | Result |
|---|---|
| `bank.testnet.algorand.network` | 301 → `lora.algokit.io/testnet/fund`, a browser SPA |
| `dispenser.testnet.aws.algodev.network/dispense` | 301 → same SPA; `405` on POST |
| `api.dispenser.algorandfoundation.tools` | `404` unauthenticated — needs an OAuth bearer token (`algokit dispenser login`) |

**This gates exactly one step.** Testnet ALGO moves freely once held, so a single
browser visit unblocks the rest. `tools/testnet_setup.py` scripts everything
downstream — peer funding, asset creation, opt-ins, and seeding the buyer:

```bash
python tools/testnet_setup.py --new         # throwaway accounts
#   HUMAN, ONCE: fund the funder at https://lora.algokit.io/testnet/fund
python tools/testnet_setup.py --provision
python tools/check_optin.py --network testnet --all
```

**Testnet USDC is worked around, not chased.** ASA `10458941` is genuinely
Circle-issued (verified on chain: creator `VETIGP3I…`, 6 decimals, `centre.io`) but
has no open faucet. The testnet run exists to prove the
402 → verify → settle → receipt path, and the `exact` scheme accepts any ASA id, so
`--provision` mints a 6-decimal stand-in (`TUSDC`) and points config at it. **Mainnet
uses the real ASA `31566704` and no code path differs.** If the facilitator turns out
to reject a non-USDC asset, that is itself worth learning on testnet.

`tools/check_optin.py` is read-only and takes an address, never a key — safe to point
at the mainnet treasury for the §3.2 step A verification.

### 3.3 Definition of done

All eight `BUILD_PLAN.md` §2 checkboxes green, **verified from the facilitator's own
API rather than from local logs**. The last three checks in D above are the real
proof — they confirm GoPlausible sees the endpoint the way the competition does.

---

## 4. Phase 2 — the product (2026-09-01 → 09-29)

Project info is due **2026-09-29**. Treat that as a second hard deadline; the finalist
gate requires it alongside top-50.

### 4.1 Module layout

Follows `BUILD_PLAN.md` §5, adjusted for what Phase 1 establishes.

```
pintheonv2/
├── keys/
│   ├── identity.py        # one seed → Algorand + Stellar + X25519
│   └── hvym_algorand/     # port of hvym_stellar
├── ledger/
│   ├── base.py            # Ledger protocol
│   ├── algorand.py        # py-algorand-sdk (already present via x402-avm)
│   └── stellar.py         # port from V1 — provenance/registry only
├── x402/
│   ├── server.py          # ← exists from Phase 1
│   ├── pricing.py         # DynamicPrice callbacks
│   └── sealing.py         # sealing-target resolution
├── content/
│   ├── scramble.py        # aiposematic wrapper
│   ├── fingerprint.py     # signal-space DCT watermark
│   └── pwa.py             # bespoke reader build → IPFS
├── storage/
│   ├── ipfs.py            # PORT FROM V1
│   └── db.py              # PORT FROM V1
├── web/                   # Flask app, routes, admin (no Onsen)
└── reader/                # PWA shell (separate build)
```

### 4.2 `keys/identity.py` — build this first

Everything else depends on it. One 32-byte seed derives all three identities:

- **ed25519** — `nacl.signing.SigningKey(seed)`
- **Algorand address** — base32(pubkey ‖ checksum) where checksum = last 4 bytes of
  SHA-512/256(pubkey). This is why the `hvym_stellar` port is thin: *Algorand
  addresses are ed25519 public keys in different clothing.*
- **Stellar** — `Keypair.from_raw_ed25519_seed(seed)`
- **X25519** — `SigningKey.to_curve25519_private_key()` /
  `verify_key.to_curve25519_public_key()`

Round-trip test against `algosdk.account` and `stellar_sdk.Keypair` independently.

#### Secret lifecycle — self-generated on first boot, never injected

V1 already does this for one of the two secrets. Port the mechanism; do not replace
it with deploy-time injection.

| Secret | V1 | V2 |
|---|---|---|
| `master_key` — Fernet key for the encrypted TinyDB | **Self-generated on first boot.** `pintheonMachine/__init__.py:91-96` writes `pintheon.ini` beside the DB with a `uuid4` and `base64(Fernet.generate_key())` if absent; read back at `:110`, used at `:1555`. | **Port verbatim.** |
| Node seed — 32 bytes → ed25519 → Algorand + Stellar + X25519 | **Provisioned by Metavinci.** `set_seed()` (`:329`) is called from `pintheon.py:566` with `verifier.secret()` off the launch macaroon. Self-generation never existed. | **New.** Generate on first boot, persist in the encrypted DB's existing `node_keys` table. |
| payTo private key | n/a | **Never on the node.** Wallet-held — the `exact` scheme only needs payTo as asset receiver. |

**Do not inject either secret via environment variables in normal operation.** Doing
so reintroduces exactly the external provisioning step `BUILD_PLAN.md` §5 decision 1
removes, and puts a setup chore in front of artists who are not crypto-natives
(thesis §9.2). A fresh node must come up with zero configuration. Env overrides exist
only for restore-from-backup and ephemeral/CI runs; when set and the state directory
already holds secrets, **refuse to start on mismatch** rather than silently
overwriting an identity.

Splitting the two across `pintheon.ini` (master key) and the encrypted DB (seed) is
V1's existing split and worth keeping — one file's compromise is not both.

#### Persistence — V1 already solves this; port it, then make it self-enforcing

V1 persists correctly today. The chain:

| Step | Location |
|---|---|
| `-v {volume_path}:/home/pintheon/data` | `metavinci/metavinci.py:767` |
| `ENV PINTHEON_DATA_DIR=/home/pintheon/data` | `pintheon_image_gen/Dockerfile.pintheon:93` |
| `DATA_DIR = _resolve_data_dir()` — env > debug > container > platformdirs | `pintheon/config.py:43,66` |
| `DB_PATH = os.path.join(DATA_DIR, 'db')` — a **directory** | `pintheon/config.py:68` |
| `DB_PATH = os.path.join(config.DB_PATH, "enc_db.json")` — shadowed, now a **file** | `pintheon/pintheon.py:41` |
| `config_path = Path(db_path).parent / 'pintheon.ini'` | `pintheonMachine/__init__.py:92` |

Resolved layout on the host, under the mounted volume:

```
~/pintheon_data/                 <- PINTHEON_DATA_DIR, the mount
├── db/
│   ├── enc_db.json              <- TinyDB, Fernet-encrypted
│   └── pintheon.ini             <- master_key. NOT at the volume root.
├── ipfs/
├── custom_homepage/
└── ssl/
```

Both secrets sit under the mount, so the master key survives container restarts.

**Port `config.py`'s `_resolve_data_dir()` and the `PINTHEON_DATA_DIR` convention
verbatim.** Keep V1's env var name; do not invent a new one.

**The residual risk is that the mount is enforced externally, and that enforcer is
going away.** Nothing in the image requires the volume — `Dockerfile.pintheon` has no
`VOLUME` declaration, and it is *metavinci* that supplies `-v`. `BUILD_PLAN.md` §5
decision 1 drops metavinci as a hard requirement, so V2 must make persistence
self-enforcing rather than caller-dependent:

- [ ] Declare `VOLUME /home/pintheon/data` in the V2 Dockerfile, so the requirement is
      visible in the image rather than implied by the caller.
- [ ] Record derived addresses in `config/node.toml` `[identity]` and **assert them at
      startup**. A regenerated identity must fail loudly, not come up clean-looking
      while orphaning the previous encrypted DB.
- [ ] Refuse to start if `PINTHEON_DATA_DIR` resolves inside the container's writable
      layer when running containerized — that is the failure, caught early.
- [ ] Document the mount in the self-host quickstart (§5.2). Artists running the image
      themselves are the population that will get this wrong.

#### Port cleanup — findings from tracing `DB_PATH`

The directory-vs-file question resolves clean: `pintheon.py:41` shadows
`config.DB_PATH` with `os.path.join(config.DB_PATH, "enc_db.json")`, so TinyDB does
receive a file. **Not a bug.** But tracing it turned up one real defect and two things
worth fixing on the way past.

**① `migrate_data.sh` moves the database without its key — the migrated DB is
unreadable.** This is a genuine data-loss path.

`migrate_data.sh:36` copies `enc_db.json` into `$PINTHEON_DB_PATH/`, and `:46` copies
the IPFS repo. **It never copies `pintheon.ini`.** Because the master key's location is
derived from the DB file's parent (`__init__.py:92`), the new directory has no ini —
so `__init__.py:93-96` generates a *fresh* `master_key` and the migrated ciphertext
can never be decrypted.

There is no recovery. `master_key` is also written into the DB's own `node_data` table
(`__init__.py:1483`) and returned by `establish_data()` (`:549`), but that copy lives
*inside* the encrypted database — circular, and useless once the key is lost.

*Confidence: reasoned from the code, not reproduced. The failure would surface at
`_open_db()` (`:1554`). Likely never hit in anger, which is why it survives.* Verify
before relying on the migration path; do not port the script as-is.

**② `PINTHEON_DB_PATH` is a footgun for the same reason.** Pointing it at a new
location silently relocates where the master key is *expected*, so an existing node
regenerates its key and orphans its own database. The env var reads like a harmless
path override and is not one.

**③ Naming.** `config.DB_PATH` (directory) and `pintheon.DB_PATH` (file) are different
values under the same name in two modules, with the shadowing 27 lines apart. Anyone
reading `config.DB_PATH` reasonably assumes it is the database path. It is not.

**Port decisions:**

- [ ] Rename to `DB_DIR` and `DB_FILE`. No shadowing.
- [ ] **Make the master key's location explicit, not derived from the DB's parent.**
      This is the root cause of both ① and ②. Give it its own resolved path anchored
      to `DATA_DIR`, independent of wherever the DB happens to live.
- [ ] On startup, if a database exists but its key file does not, **refuse to start**
      with a clear error. Never silently generate a fresh key next to an existing
      encrypted DB — that is the moment the data becomes unrecoverable.
- [ ] Move `os.makedirs` out of `config.py` import time (`:77-81`) into explicit
      initialisation. Import-time filesystem side effects make the module hard to test
      and hard to reason about.
- [ ] If V2 ships a migration path, it moves key and database **together, or not at
      all**, and verifies it can decrypt before declaring success.

### 4.3 `hvym_algorand`

`BUILD_PLAN.md` §3 establishes that `hvym_stellar` touches `stellar_sdk.Keypair` at
exactly two points — `raw_secret_key()` and the base64 pub encoding. Swap those for
the algosdk equivalents; every class above them (`StellarSharedKey`,
`StellarSharedKeyTokenBuilder`, `FileCaveatVerifier`, `HVYMDataToken`) carries over
unchanged. Port with tests, keep the class shapes, rename in place.

### 4.4 Storage — port, do not rewrite

Per `BUILD_PLAN.md` house rules. From `pintheonMachine/__init__.py`:

- `add_file_to_ipfs` — upload → pin → MFS → IPNS → `file_book`
- `_auto_publish_directory_to_ipns`, `publish_mfs_to_ipns`
- TinyDB + Fernet layer and the `file_book` row shape

Carry the row shape as-is even where fields are Stellar-specific; V2 adds columns
rather than reshaping. Drop only the Soroban token columns' *usage*, not the columns.

### 4.5 Key delivery

Per `BUILD_PLAN.md` §5, one parameter with two paths:

- **Human buyers (primary).** Checkout generates an X25519 keypair in-browser. The
  public key is passed as a `sealTo` query param — declared in the route's
  `input_schema` via `declare_discovery_extension`, so it is self-describing in the
  Bazaar. Private key lives in IndexedDB and becomes the PWA's decryption key.
  Recovery by passphrase-wrapped export.
- **Agent buyers.** `sealTo` omitted → default to the payer's address-derived X25519
  key. The payer address arrives in the settle response.

Single code path, one branch on `sealTo` presence.

### 4.6 Render latency — the one thing per-issue makes harder

`BUILD_PLAN.md` §5's Shape A / Shape B question is **closed by the per-issue
decision** (§2). Neither is built. The payment is a single `axfer`; there is no group,
no session account, no prefetch.

What replaces it as the hard problem: **the node must scramble, watermark, and package
~24 pages for one buyer inside a single request.** Per-page rendering hid this by
doing one page at a time. Per-issue puts the whole issue on the critical path.

Resolve it in this order:

1. **Benchmark `aiposematic` first, before designing around it.** Do this early in
   Phase 2 — it is a half-day task and it decides the architecture. Measure per-page
   scramble + DCT watermark at real page resolution on the target VPS.
2. **If ~24 pages render in a comfortable margin under `max_timeout_seconds`** (set
   ≥120 in §3.2), render inline. Simplest possible thing; prefer it if it fits.
3. **If it does not fit, decouple settlement from delivery.** Settle → return a
   receipt with a job id immediately → PWA polls until the root CID is ready. The
   buyer's payment is confirmed in ~3s regardless; only the artifact lags. This is
   also the more robust shape under load, and it is a small amount of extra code.
4. **Parallelise across pages** — the work is embarrassingly parallel per page, and
   the shared shell is rendered once and deduped, not per buyer.

Do not pre-render per-buyer content speculatively. Thesis §9.6's scaling objection —
100,000 encryptions, most never downloaded — is dissolved precisely because the buyer
arrives at purchase time. Rendering ahead of payment reintroduces the problem x402
solved.

### 4.7 Remaining Phase 2 tasks

- [ ] **Benchmark `aiposematic` per page on the target VPS** — do this first, it
      decides §4.6's architecture
- [ ] `content/scramble.py` — per-buyer keyed aiposematic scrambling
- [ ] `content/fingerprint.py` — signal-space DCT watermark (v1 scope per
      `BUILD_PLAN.md` §5 decision 5; no CLIP/VAE, it cannot fit in a synchronous 402)
- [ ] `content/pwa.py` — three-part UnixFS directory per buyer (shared shell,
      per-buyer index, per-buyer pages); block-level dedup stores the shell once
- [ ] Reader PWA shell — no Onsen
- [ ] Swap the Phase 1 flat-file response for the bespoke per-buyer pipeline —
      **payment path and price are unchanged**, only what gets returned
- [ ] Load the full title set
- [ ] **Submit project info by 09-29**

---

## 5. Phase 3 — self-hosting and usage (09-29 → 10-08)

### 5.1 Hosting posture — the VPS is a sequencing decision, not a product statement

`BUILD_PLAN.md` §5 decision 6 puts the flagship node on a VPS. That stands for
Phase 1, but the rationale needs restating, because "the flagship runs on a VPS" reads
as a retreat from the premise that Pintheon runs on the creator's own hardware. It
isn't one.

**The axis that matters is creator-controlled vs platform-controlled, not rented
hardware vs residential hardware.** §5 decision 3 justifies node key custody with *"the
node is the artist's own machine, so this is not third-party custody."* A VPS the
artist rents — running their node, holding their keys, with no intermediary in the
payment path — satisfies that in full. The flagship is *a* node, not *the platform*.
Same image, same software, same keys as any self-hosted instance.

**The uptime requirement is narrower than it looks.** Of the four surfaces, only one
needs the node to be up:

| Surface | Depends on node uptime? |
|---|---|
| Delivered PWA (buyer already owns it) | **No** — pages and key are on the buyer's device |
| Page previews | **No** — IPFS |
| Catalogue | **No** — can be static / IPFS |
| **Mint endpoint (402 → settle → render)** | **Yes** |

So a creator's node going offline pauses *new sales*. It does not touch anyone's
existing library. This is a materially better availability story than a centralized
platform, where deplatforming destroys access retroactively — and it should be said
out loud in the submission (§6).

**Why the VPS anyway, for Phase 1.** Moving the flagship to creator hardware for
Sep 1 means routing it through `hvym_tunnler`, which pulls `BUILD_PLAN.md` §4's bugs
onto the critical path with three weeks left. §4.2 is the pointed one: the
`_ALLOWED_RESPONSE_HEADERS` whitelist strips the settlement receipt. Payment still
settles — that happens server-side — but the buyer never receives `PAYMENT-RESPONSE`.
Degraded for a human; **broken for an agent** that needs the receipt to confirm.

Spending the final three weeks debugging a WebSocket proxy in a dependency repo
instead of shipping the product is the single worst trade available. VPS for Phase 1.

### 5.2 Self-hosting is closer than `BUILD_PLAN.md` §4 implies

§4.1 (binary corruption — `body.decode("utf-8", errors="replace")` inbound,
`response.text` outbound) only bites if **page images travel through the tunnel**. They
need not. If the PWA pulls content from IPFS and the tunnel carries only x402 API
traffic, that traffic is all JSON — UTF-8-safe by construction, and the corruption bug
never fires.

That leaves §4.2, the header whitelist, as the one genuinely required fix, and it is a
small well-understood change.

**Phase 3 deliverable — demonstrate the self-host path live:**

- [ ] Fix `hvym_tunnler` `_ALLOWED_RESPONSE_HEADERS` (`app/api/routes.py:24`) —
      whitelist **both** `PAYMENT-RESPONSE` and `X-PAYMENT-RESPONSE` (§1.4).
- [ ] Route PWA content fetches to IPFS, not through the tunnel. This is what keeps
      §4.1 off the path; make it an explicit architectural rule, not an accident.
- [ ] Absorb `metavinci/tunnel_client.py` into the node so it self-tunnels with its
      own keypair (`BUILD_PLAN.md` §5 decision 1 — also closes the §4.5 identity gap).
- [ ] Stand up a **second node on actual creator hardware**, tunnelled, live alongside
      the VPS flagship.
- [ ] Fix §4.1 properly (base64-armour non-UTF-8 bodies both ends) — no longer
      urgent once content goes via IPFS, but required before the tunnel is a
      general-purpose product surface.

Two live nodes running identical software — one rented, one residential — is a far
better demonstration of the thesis than either alone, and it is precisely the evidence
"sustained potential" asks for.

### 5.3 Remaining Phase 3 tasks

- [ ] Drive real readers to the flagship node. This is the only lever left on volume.
- [ ] Admin dashboard rebuild (replaces Onsen).
- [ ] Docker image via `pintheon_image_gen`, clone URL repointed to this repo. This is
      the artefact that makes §5.2 reproducible by someone who is not us.

## 6. Phase 4 — finals (→ 2026-11-02)

Lead with use case quality, sustained potential, innovation — 75% of the judged score,
and the three criteria where a bespoke-encrypted comics rail is differentiated. Volume
is the fourth quarter and the gate, not the pitch.

Two framing points that are load-bearing and easy to leave unsaid:

**The buyer's copy outlives the seller.** Once minted, the PWA is on the buyer's
device: pages, key, reader, all of it. It keeps working if the creator's node goes
offline, if the creator quits, if we disappear. No platform can say this — on a
centralized service, losing the platform loses the library retroactively. Say it
plainly; it is the strongest single claim in the product and it costs nothing to make.

**Self-hosting is demonstrated, not asserted.** Point at the second node (§5.2) and
the Docker image. "Sustained potential" is a claim about what happens after the prize
money stops, and a rail that only we can operate has a weak answer.

**Honesty note carries forward.** Per `BUILD_PLAN.md` §5, the node knows each buyer's
key at render time. "Only the buyer can decrypt" is really "only the buyer, plus the
node at the moment of minting." Do not repeat thesis §5.1's stronger framing.

---

## 7. Decisions needed from you

Everything else in this plan can proceed on stated assumptions. These four cannot.

| # | Decision | Blocks | Why it needs you |
|---|---|---|---|
| 1 | **Which comics ship on the flagship node.** `BUILD_PLAN.md` §7 Q4 — needs rights confirmation from the founder. | Phase 1 step D | Real books score materially better on use case quality. I will not stage third-party content on a paid mainnet endpoint without explicit confirmation. |
| 2 | **Issue price.** §2 assumes **$3.00**. | Phase 1 step C | Revenue policy is yours. Per-issue is decided; the number is a judgement call. At $3.00, top-20 is two issues and top-5 is ~80. |
| 3 | **VPS host and region.** | Phase 1 step B | Cost and account ownership are yours. Needs multi-week uptime through 2026-10-08. |
| 4 | **Who holds the mainnet mnemonic backup.** | Phase 1 step A | Irreversible and unrecoverable. The payTo address cannot change after registration. |

Decisions 1 and 4 are the two that can genuinely damage the entry. The rest are
schedule risk only.

---

## 8. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| payTo lost or changed after registration | **Fatal** | Offline mnemonic backup ×2 before the account is used. §3.2 step A first. |
| payTo not opted into USDC | **Fatal** | Explicit on-chain verification before the endpoint goes live. Settlement fails at simulate otherwise. |
| Endpoint down during 09-30 → 10-08 shortlist window | **High** | systemd restart, uptime monitor, VPS not laptop. |
| Node state not on a mounted volume → identity regenerated, encrypted DB orphaned | Medium | **Not a V1 defect — V1 persists correctly** (`metavinci.py:767` + `config.py:43-68`). The risk is that metavinci enforces the mount and V2 drops metavinci. Port `_resolve_data_dir()`, add `VOLUME` to the Dockerfile, assert `[identity]` addresses at startup. §4.2. |
| **Master key separated from its database → permanent data loss** | **High (inherited from V1)** | Live defect in `migrate_data.sh` (moves `enc_db.json`, not `pintheon.ini`), and latent in any `PINTHEON_DB_PATH` change. Fix at the root during the port: give the key its own explicit path, and refuse to start when a DB exists without its key. §4.2 ①②. |
| Project info missed on 09-29 | **High** | Second hard deadline. Calendar it now. |
| Tag omitted or misplaced | **High** | `extra.tag`, verified from `/discovery/resources` post-launch — §3.2 step E. |
| nginx strips `PAYMENT-RESPONSE` | Medium | Explicit pass-through + expose-headers; verified by live call, not config review. |
| Field inflates before 09-01 | Medium | Re-run `/data/leaderboards` weekly; pricing is the lever. |
| Volume optimised for count not value | Medium | Resolved by §2 — but re-check if pricing is revisited. |
| **Whole-issue render exceeds the request window** | **Medium — raised by the per-issue decision** | The main cost of §2. Benchmark first (§4.6 step 1). Previews are pre-scrambled; only per-buyer render is on the request path. Generous `max_timeout_seconds`, parallel page render, async job + poll as fallback. |
| Tunnel header whitelist strips `PAYMENT-RESPONSE` on self-hosted nodes | Medium (product, not competition) | The one required fix for §5.2. Breaks agent buyers, degrades human ones. Flagship is unaffected — it has a real IP and cert. |
| Tunnel binary corruption blocks self-host media | Low | Structurally avoided: PWA content goes via IPFS, tunnel carries JSON only (§5.2). Fix properly before the tunnel is a general product surface. |
| Self-host path slips and "sustained potential" rests on assertion | Medium | §5.2 is a dated deliverable with a second live node, not a roadmap item. If it slips, the Docker image alone is a weaker but real answer. |

---

## 9. Reproducible field check

Re-run before any pricing decision. Note the slug-vs-CAIP-2 asymmetry (§1.4 #3).

```bash
FAC=https://facilitator.goplausible.xyz

# Ranking — this is what the competition dashboard renders. Ranks by USDC volume.
curl -s "$FAC/data/leaderboards?range=all&network=algorand-mainnet&cat=merchants&limit=50"

# Field totals
curl -s "$FAC/data/totals?range=all&network=algorand-mainnet"
curl -s "$FAC/data/ecosystem?range=all&network=algorand-mainnet"

# Our own entry, post-registration
curl -s "$FAC/discovery/merchants/<id>"
curl -s "$FAC/api/receipt/<txId>"

# Bazaar catalogue — needs the CAIP-2 id, NOT the slug
curl -s "$FAC/discovery/resources?limit=1000&offset=0"

# Facilitator capabilities
curl -s "$FAC/supported"
```

Full API: `https://facilitator.goplausible.xyz/docs/openapi.json`.
