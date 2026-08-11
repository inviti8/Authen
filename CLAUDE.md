# PintheonV2

**Read [`BUILD_PLAN.md`](./BUILD_PLAN.md) first.** It is the complete brief: repo
map with absolute local paths, competition requirements and deadlines, what to port
from Pintheon V1, the V2 architecture, the phase plan, and the unresolved questions
that must be settled before product code is written.

## One-paragraph orientation

PintheonV2 is a self-hosted node that sells comics and print media **per page** over
the **x402 payment protocol on Algorand mainnet**, delivering **bespoke per-buyer
encrypted PWA readers** over IPFS. It is a ground-up rebuild of Pintheon V1
(`C:/Users/surfa/Documents/metavinci/pintheon`), reusing that project's IPFS/MFS/IPNS
plumbing and encrypted TinyDB layer while replacing its Stellar-only money rail,
its Metavinci provisioning dependency, and its Onsen UI.

## Hard deadline

**2026-09-01, 11:45pm EST** — a paid x402 endpoint must be live on Algorand mainnet,
publicly reachable over HTTPS, settling through the GoPlausible facilitator,
Bazaar-registered and tagged `x402-global-challenge`, with at least one real
completed payment. Missing this excludes the project from the Global x402 Challenge
entirely. See `BUILD_PLAN.md` §6 Phase 1.

## Before writing product code

Read [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) — it is the build order.
`BUILD_PLAN.md` §7 Q1 and Q2 are **resolved** there (§1), Q3 is reframed as a
constraint, and Q4 still needs the founder. It also corrects three items in
`BUILD_PLAN.md` §8 — notably the x402 v2 header names.

Two findings change the design:

- The leaderboard ranks by **USDC volume, not settlement count**. Fractional-cent
  pricing optimises a metric nobody scores. See `IMPLEMENTATION_PLAN.md` §2.
- **The unit of sale is the issue, not the page.** Per-page pricing is dropped, and
  with it `BUILD_PLAN.md` §5's Shape A / Shape B question — no atomic groups, no
  session accounts. One `axfer`, one settlement, one bespoke PWA.
- **The VPS flagship is sequencing, not a retreat from self-hosting.** Only the mint
  endpoint needs uptime; delivered PWAs, previews and catalogue do not. Self-hosting
  on creator hardware is a dated Phase 3 deliverable — `IMPLEMENTATION_PLAN.md`
  §5.1–5.2. Keep PWA content on IPFS and the tunnel carrying JSON only; that is what
  keeps `BUILD_PLAN.md` §4.1 off the path.
- All competition volume must land on **one payTo**. No `DynamicPayTo`, no second
  recipient in the payment group, before 2026-10-08. Artist splits are a downstream
  sweep.

## House rules

- Verify before asserting — the competitive field data in §2 moves weekly.
- Never simulate payment volume; wash traffic is a disqualification risk.
- Port, don't rewrite, the V1 storage layer.
- Do not use Onsen UI.
