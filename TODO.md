# Morning TODO

**#1 is done — testnet settlement works end to end.** The critical path for the
**2026-09-01** deadline is now #2 (VPS + hostname) and #3 (mainnet account).

Everything else is built and passing — see `IMPLEMENTATION_PLAN.md` §3 for the full
Phase 1 checklist and the end of this file for what's already done.

---

## 1. ~~Testnet settlement~~ — **DONE, and it works**  ✅

The full rail is proven end to end on Algorand testnet against the live GoPlausible
facilitator: 402 → sign → verify → **settle on chain** → issue delivered → receipt.

```
[1/4] 402 challenge   x402Version=2 accepts=1
[2/4] payment signed  3432 byte header
[3/4] paid request    200 application/zip 89285 bytes
[4/4] settled         success=True
      txid            WQZSNW67EMG4G4SUL3AQBQIY2ZVB4KMMII5N4CHTCHNTTKSRXHHQ
```

Confirmed independently on chain (round 66235006): 3.000000 units moved buyer →
treasury, **fee 0** — the facilitator sponsored it via `feePayer`, exactly as the
`exact` scheme intends. Reproduce any time with `python tools/pay_once.py`.

This ran against a **self-minted stand-in ASA (`769120200`)**, not real USDC, because
the USDC faucet was rate-limited. That is not a shortcut: the `exact` scheme takes any
ASA id, and mainnet's `31566704` differs in no other way. Swapping the asset id is a
one-line config change.

**Optional, when the faucet cooldown lifts** — resend testnet USDC here to re-run the
same test against real Circle USDC (ASA `10458941`, both accounts already opted in):

```
NJO3MQADL3UO236P75NAV4NCVFNA2SVVYH6BVUO5MFMIHBZVXNAQNNNFYI
```

Your first send never arrived, and the chain shows why: the account had no opt-in at
the time, so the transfer was rejected outright — there is no pending transaction to
wait on. Both accounts are opted in now, so a resend will land. Then:

```bash
python tools/testnet_setup.py --provision --asa 10458941
python tools/pay_once.py     # after pointing config/node.local.toml at 10458941
```

> ALGO funding is scripted now too — `tools/dispenser.py` (`--login`, `--fund`).
> The AlgoKit dispenser API *is* programmatic; my earlier claim that it wasn't was
> wrong. One device-code login, then a 30-day token.
>
> Mnemonics live in `.venv/testnet_accounts.json` (gitignored).

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
- Testnet provisioning, ALGO dispenser, opt-in verification scripts
- **Full paid purchase settled on testnet** — see #1

**Blocked on #2 + #3** for mainnet cutover. Those are now the only things between us
and a live paid endpoint.
