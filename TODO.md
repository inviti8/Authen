# Morning TODO

Five items. Only #1 and #2 are on the critical path for the **2026-09-01** registration
deadline; #1 is the cheapest and unblocks the most.

Everything else is built and passing — see `IMPLEMENTATION_PLAN.md` §3 for the full
Phase 1 checklist and the end of this file for what's already done.

---

## 1. Fund the testnet account  ⏱️ ~2 minutes

Algorand has no Friendbot. Every faucet is browser-gated, so this one step needs a
human; everything downstream is scripted (`IMPLEMENTATION_PLAN.md` §3.4).

**Go to:** https://lora.algokit.io/testnet/fund

**Fund this address** (testnet, throwaway, zero real value):

```
NJO3MQADL3UO236P75NAV4NCVFNA2SVVYH6BVUO5MFMIHBZVXNAQNNNFYI
```

Ask for **5 ALGO** or more. Then tell me, or run it yourself:

```bash
python tools/testnet_setup.py --provision      # funds peers, mints stand-in asset, opts in
python tools/check_optin.py --network testnet --all
```

That unblocks the full 402 → verify → settle → receipt test.

> The buyer account is `GSSX5NVBWLAEDI32KU7EBHF2CBN4SIUWXNDCYGFFPCVF6Z4SNADRUKXJTM`
> — it gets funded automatically by `--provision`, no need to touch it.
> Mnemonics for both are in `.venv/testnet_accounts.json` (gitignored).

---

## 2. VPS + hostname + TLS  ⏱️ start early, it's wall-clock

DNS propagation and cert issuance can't be rushed by working harder, so kick this off
before anything else that takes thought.

- [ ] Provision a VPS (2 vCPU / 4 GB is ample)
- [ ] Point a hostname at it — **this hostname is semi-permanent**, see the warning below
- [ ] certbot for TLS. Public HTTPS is a gate item: no localhost, no self-signed.
- [ ] Deploy configs are ready in `deploy/` — nginx vhost, systemd unit, gunicorn conf.
      Replace `pintheon.example.art` throughout.

> ⚠️ **Pick the hostname deliberately.** The facilitator auto-catalogs on `/verify`
> ("Auto-catalogs resources via Bazaar extension" — its own OpenAPI). The first paid
> request registers whatever `resourceUrl` it carries, tied to your payTo. There is no
> quiet test from a temporary URL that doesn't leave a junk Bazaar entry.

---

## 3. Mainnet account + USDC opt-in  ⏱️ ~15 minutes

**Do not let me see the mnemonic** — it would land in the conversation transcript.
Generate it yourself in Pera/Defly and give me only the address.

- [ ] Create the account (Pera Wallet, mobile or web)
- [ ] **Back up the 25 words offline, twice, before the account is used**
- [ ] Fund with ~1 ALGO (covers the 0.1 minimum + 0.1 ASA slot with headroom)
- [ ] **Opt into USDC, asset ID `31566704`** — without this every settlement fails at
      the facilitator's simulate step, while the endpoint still looks healthy
- [ ] Verify it, key never required:

```bash
python tools/check_optin.py --network mainnet --address <your-address>
```

- [ ] Put the address in `config/node.toml` under `[treasury] mainnet`

> This address is **permanent for the competition**. Changing it after registration
> splits your volume across two leaderboard entries, neither of which ranks. It is the
> one irreversible decision in Phase 1.
>
> Keep it separate from the node identity key — the payTo private key never touches
> the server (the `exact` scheme only needs payTo as asset receiver).

---

## 4. Comics rights  ⏱️ a conversation

- [ ] Confirm with the founder which titles can ship on a paid mainnet endpoint
- [ ] Candidates are in `D:/repos/comics.heavymeta.art/content/titles`

Real books score materially better on "use case quality" than placeholder art. The
pipeline runs on synthetic pages meanwhile, so this blocks nothing technical — but it
should land before launch.

---

## 5. Confirm the price  ⏱️ 1 minute

Currently **$3.00 per issue** (`3000000` micro-USDC in `config/node.toml`).

Per-issue is decided; the number is yours. For calibration against today's board:
top-20 costs $4.63 total volume (two issues), top-10 ~$46, top-5 ~$243.

---

## Already done — nothing needed from you

- Phase 1 endpoint built; paid route returns a correct 402 with every registration
  gate item verified on the wire (tag at `accepts[].extra.tag`, ASA/amount as strings,
  CAIP-2 network, `x402Version: 2`, Bazaar discovery extension attached)
- Free catalogue, free scrambled previews, paywall boundary enforced
- 22 tests passing (`python -m pytest tests/ -q`)
- Content pipeline running on placeholder art
- nginx / systemd / gunicorn configs written
- Testnet provisioning + opt-in verification scripts

**Blocked only on #1** for the settlement test, and on **#2 + #3** for mainnet cutover.
