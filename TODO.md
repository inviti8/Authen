# TODO

Repo pivoted from PintheonV2 to **Authen** on 2026-08-12. Rail unchanged and proven;
what it sells changed. See `CLAUDE.md`.

---

## 1. Rename the GitHub repo  ⏱️ 1 minute — *do this before anything else*

Settings → rename `PintheonV2` → `Authen`. GitHub redirects the old URL, so nothing
breaks, but `scripts/vps_startup.sh` already points at
`https://github.com/inviti8/Authen.git`.

```bash
git remote set-url origin git@github.com:inviti8/Authen.git
```

---

## 2. Point DNS at the new hostname  ⏱️ 2 minutes + propagation

| Name | Type | Value |
|---|---|---|
| `authen.hvym.link` | A | `104.207.89.129` |

Same box as `pintheon.hvym.link`. Start this early — cert issuance waits on it.

> **Why now and not later:** the Bazaar keys a resource on `base64("GET:<url>")`,
> so the URL *is* the primary key. **No payment has been taken yet, so nothing is
> catalogued** and this move is free. After the first settlement it costs the
> resource's identity. Rename → repoint → *then* take the first payment.

---

## 3. Redeploy as Authen  ⏱️ ~15 minutes

Paths, service name and user all changed (`/opt/authen`, `/var/lib/authen`,
`authen.service`). Cleanest is a fresh run rather than migrating in place:

```bash
# on the VPS
sudo systemctl disable --now pintheonv2
curl -O https://raw.githubusercontent.com/inviti8/Authen/main/scripts/vps_startup.sh
# set DOMAIN=authen.hvym.link, ADMIN_EMAIL, PAYTO_MAINNET
sudo bash vps_startup.sh
sudo bash /opt/authen/scripts/status.sh
```

Then record the signing key — **this is new and it matters**:

```bash
curl -s https://authen.hvym.link/api/v1/identity
# copy publicKey into config/node.toml -> [identity] public_key
sudo systemctl restart authen
```

Without it, a state directory that fails to mount regenerates the identity
silently and every attestation signed afterwards is unverifiable against the key
you published. With it, the node refuses to start.

---

## 4. Take the first mainnet payment + register  ⏱️ ~20 minutes

The whole Sep 1 gate. Everything else is already verified.

- [ ] `status.sh` clean on `authen.hvym.link`
- [ ] One real settled payment (adapt `tools/pay_once.py`, mainnet, ~$0.05)
- [ ] Registration form submitted

---

## 5. Decide: port the trust registry to Algorand?  ⏱️ a decision, then ~a week

`hvym-cert-registry` is live on Stellar/Soroban. Authen pays on Algorand. Two
chains is rules-compliant — §6 only requires the *paid endpoint* be on Algorand
mainnet via GoPlausible — but it is a weaker story to an Algorand judging panel.

Porting is reasonable: it is a KV registry with Ed25519 auth, and Algorand has
both primitives natively (box storage, `ed25519verify_bare`). **Existing keys
survive** — the same 32-byte Ed25519 key is already both a Stellar and an
Algorand address, so only the SAN encoding changes.

Not on the Sep 1 path: the gate needs `/notarize`, which touches no registry.

---

## Already done — nothing needed from you

- Rail proven end to end on testnet against the live facilitator, twice, most
  recently against `/api/v1/notarize` (txid `M6MU2KVFEV2XKITFVLYYSGNPIMKTJQDM65XWX5TFQH5BWGSRZ66A`)
- Notary: Ed25519 attestations, canonical payloads, offline verification
- Node identity self-generated on first boot, 0600, mismatch refuses to start
- Free `/verify` and `/identity`; paid `/notarize`
- AVM paywall wired (the SDK's EVM default renders a blank page)
- 44 tests passing
- Deploy artifacts, runbook and status check updated for Authen
- Comics code parked in `shelved/`, not deleted

**Blocked on #1–#3**, which are yours, then #4 closes the deadline.
