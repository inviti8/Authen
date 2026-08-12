# Authen Deployment Runbook

Deploying the **flagship node** — the one instance that carries competition uptime.
Public HTTPS, real certificate, paid x402 endpoint settling on Algorand mainnet.

For what the flagship *is* and why it is a VPS rather than creator hardware, see
[`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) §5.1. Self-hosting over the
tunnel is Phase 3 and is deliberately not on this path.

**Time:** ~20 minutes, most of it DNS propagation and cert issuance.

---

## Prerequisites

- **Ubuntu 24.04** VPS with root access, 1 vCPU / 1 GB minimum (2/4 comfortable)
- A domain you control, with an A record you can edit
- Ports 22, 80, 443 reachable
- The **payTo address** — the public Algorand address only. Never the mnemonic.

> ### Ubuntu 22.04 will not work unmodified
>
> `authen/config.py` imports `tomllib`, which is stdlib only from **Python
> 3.11**. Ubuntu 22.04 ships 3.10 and the app cannot import at all. The install
> script detects this and pulls Python 3.12 from the deadsnakes PPA, but
> **24.04 is the supported target** and avoids the whole problem.

---

## Step 1: Configure the Startup Script

Edit the `CONFIGURATION` block at the top of `scripts/vps_startup.sh`:

```bash
#=============================================================================
# CONFIGURATION - Modify these variables for your deployment
#=============================================================================

DOMAIN="authen.hvym.link"
ADMIN_EMAIL=""
PAYTO_MAINNET=""
REPO_URL="https://github.com/inviti8/Authen.git"
REPO_BRANCH="main"
GITHUB_TOKEN=""
NETWORK="mainnet"
AUTHEN_USER="authen"
```

| Variable | Description |
|----------|-------------|
| `DOMAIN` | Public hostname. Becomes `public_url`, which is what the Bazaar publishes as the resource URL. Must resolve to this VPS before TLS can be issued. |
| `ADMIN_EMAIL` | Let's Encrypt expiry notices. Required for unattended issuance; leave empty to skip TLS and do it by hand. |
| `PAYTO_MAINNET` | The payTo **address**. Public — it is published in the Bazaar. The private key never touches the server. |
| `REPO_URL` / `REPO_BRANCH` | Source to deploy. Change if using a fork. |
| `GITHUB_TOKEN` | Fine-grained PAT, Contents → Read-only. **The repo is currently public — leave this empty.** Only needed if it is made private later. |
| `NETWORK` | `mainnet` or `testnet`. Prove the flow on testnet first if this is a rehearsal. |
| `AUTHEN_USER` | Service account. Owns `/opt/authen` and `/var/lib/authen`. |

### On the payTo address

The `exact` scheme only needs payTo as the asset receiver (`arcv`) — the buyer
signs, the facilitator submits. **Nothing on this server ever needs the private
key.** Generate it in Pera or Defly, back the 25 words up offline twice, and give
the deployment only the address.

---

## Step 2: Configure DNS

Add one A record (replace `YOUR_VPS_IP`):

| Name | Type | Value |
|------|------|-------|
| `authen.yourdomain.com` | A | `YOUR_VPS_IP` |

No wildcard needed — unlike the tunnel server, this node serves a single hostname
and uses HTTP-01, so none of the acme-dns machinery applies.

Wait 1–5 minutes, then verify:

```bash
dig +short authen.yourdomain.com
```

**Do this before Step 3.** Certbot's HTTP-01 challenge fails if DNS has not
propagated, and the install script will otherwise finish with an untrusted
endpoint.

---

## Step 3: Deploy

SSH into the VPS and run:

```bash
curl -O https://raw.githubusercontent.com/inviti8/Authen/main/scripts/vps_startup.sh
chmod +x vps_startup.sh
sudo ./vps_startup.sh
```

The script will:

1. Preflight — root, placeholder domain, DNS vs. this host's public IP
2. Install nginx, certbot, ufw
3. Resolve a Python ≥ 3.11 (deadsnakes fallback on 22.04)
4. Create the `authen` service user
5. Clone the repo to `/opt/authen`
6. Build the venv and install dependencies
7. Write `config/node.toml` and `/etc/authen/env`
8. Report node identity state (the signing key is generated on first start)
9. Install and enable the systemd unit
10. Configure nginx (HTTP first, so ACME can complete)
11. Open 22/80/443
12. Start the service, then issue TLS and swap in the full vhost

It is **idempotent**. Re-running after a reboot detects the marker at
`/var/lib/authen-install/.initialized` and only ensures services are up.

---

## Step 4: Verify

```bash
sudo bash /opt/authen/scripts/status.sh
```

This checks, in the order things fail:

- `authen` and `nginx` active; gunicorn listening on `127.0.0.1:8402`
- Certificate present and not expiring within 14 days; `certbot.timer` enabled
- `/health` → 200 and `/api/v1/identity` publishes the signing key — **over the
  public URL, not loopback**, because loopback proves gunicorn is alive, not that
  a buyer can reach you
- **The published key still matches `[identity] public_key` in config.** A changed
  key means every attestation signed since is unverifiable against what you published
- **`POST /api/v1/notarize` → 402, not 200**
- **`POST /api/v1/verify` is NOT paywalled** — an attestation nobody can check is
  worth nothing
- The challenge carries `extra.tag` and the configured payTo
- payTo is opted into USDC and can actually receive

Exit codes: `0` pass, `1` warning, `2` failure, `3` config unreadable. Use
`--quiet` for cron — it is silent on success and prints issues to stderr.

### The two failures worth understanding

**A paid route returning 200 means you are signing attestations for free.** The
x402 middleware regex-escapes route patterns and only substitutes `[param]`; a
Flask `<title>` survives as a literal, matches nothing, and the route is silently
unprotected. No warning, no log line. `tests/test_route_patterns.py` guards the
translation; `status.sh` catches it in production.

**A payTo that is not opted into USDC fails every settlement while the endpoint
looks perfectly healthy.** The 402 is correct, verification passes, and settlement
dies at the facilitator's simulate step. Check it, never assume it:

```bash
/opt/authen/.venv/bin/python /opt/authen/tools/check_optin.py \
    --network mainnet --address <your-address>
```

---

## Step 5: Go Live

> ### The first paid request is not reversible
>
> The facilitator **auto-catalogs resources into the Bazaar on `/verify`**. The
> first real payment registers whatever `resourceUrl` it carries, tied to your
> payTo. There is no quiet rehearsal from a temporary URL that does not leave an
> entry behind — 13% of the live index is junk from people testing exactly that
> way. Be on the hostname you intend to keep.

Once `status.sh` is clean:

- [ ] `config/node.toml` `[treasury] mainnet` holds the real address
- [ ] payTo opted into ASA `31566704`, verified on chain
- [ ] `[identity] public_key` recorded from `GET /api/v1/identity` after first boot
- [ ] Price confirmed in `[pricing] notarize_micro_usdc`
- [ ] `POST https://<domain>/api/v1/notarize` returns 402 with `extra.tag`
- [ ] `POST https://<domain>/api/v1/verify` is free

Changing the hostname later costs the resource's discovery history but **not**
your leaderboard position — volume aggregates on payTo, not hostname. Changing
the **payTo** does split your entry. That is the one irreversible choice.

---

## Operations

### The signing key

Generated on first start into `AUTHEN_DATA_DIR/node_seed.bin` (mode 0600), never
injected by environment. Record the public half in config immediately:

```bash
curl -s https://<domain>/api/v1/identity
# put publicKey into config/node.toml under [identity] public_key
sudo systemctl restart authen
```

Startup then asserts the live key still matches and **refuses to start** if it
does not. That check exists for one failure: a state directory that did not mount.
The node generates a fresh identity, starts cleanly, serves happily — and every
attestation it signs is unverifiable against the key you published. Without the
recorded key, nothing detects it.

### Backups — the one thing that cannot be rebuilt

```bash
sudo tar czf authen-state-$(date +%F).tar.gz -C /var/lib authen
```

`/var/lib/authen` holds `authen.ini`, the Fernet master key. **Losing it
makes the encrypted database permanently unreadable**, and the failure looks like
a clean first boot rather than an error. `config.py` refuses to start if the
database is present without its key, which turns silent data loss into a loud
stop — but only if you still have a backup to restore.

### Updating

```bash
cd /opt/authen
sudo -u authen git pull
sudo /opt/authen/.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart authen
sudo bash scripts/status.sh
```

### Logs

```bash
journalctl -u authen -f          # service
tail -f /var/log/authen-startup.log
tail -f /var/log/nginx/authen.error.log
```

### Monitoring

Uptime must survive to **2026-10-08** (shortlist window). Point an external
monitor at `https://<domain>/health` and run the status check hourly:

```bash
# /etc/cron.d/authen-status
0 * * * * root bash /opt/authen/scripts/status.sh --quiet
```

---

## Troubleshooting

### PATH not found / command not found

Container-based VPS (OpenVZ, LXC) often have a minimal PATH:

```bash
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
```

The startup script sets this itself; you may need it for manual commands.

### `ModuleNotFoundError: No module named 'tomllib'`

Python 3.10. See the Ubuntu 22.04 warning above. Rebuild the venv against 3.11+:

```bash
sudo rm -rf /opt/authen/.venv
sudo python3.12 -m venv /opt/authen/.venv
sudo /opt/authen/.venv/bin/pip install -r /opt/authen/requirements.txt
sudo systemctl restart authen
```

### Service fails to start

```bash
journalctl -u authen -n 50 --no-pager
```

Common causes:

| Message | Cause |
|---|---|
| `[treasury].mainnet is empty` | payTo not configured. Edit `config/node.toml`. |
| `No node config at ...` | `config/node.toml` missing. Copy from `node.example.toml`. |
| Hangs on start | `server.initialize()` calls the facilitator's `/supported`. Check egress. |
| `has no master key at ...` | Refusing to start rather than orphan an encrypted DB. Restore `authen.ini`. |

### Certificate issuance failed

Almost always DNS. Confirm it resolves here, then retry:

```bash
dig +short <domain>
curl -s ifconfig.me
sudo certbot --nginx -d <domain> --redirect
```

If certbot reports `KeyError: 'PATH'`, set PATH as above first.

### Paid route returns 200 instead of 402

The route pattern is not matching and attestations are being signed for free.
Verify `[routes] notarize` in `config/node.toml` uses Flask syntax — the
translation to x402 syntax (`[title]`) happens in `to_x402_pattern()`. Run the
suite:

```bash
/opt/authen/.venv/bin/python -m pytest /opt/authen/tests/ -q
```

### Settlement fails but the endpoint looks healthy

payTo is not opted into USDC. See Step 4.

### Browser clients cannot read the receipt

`Access-Control-Expose-Headers` must list both `PAYMENT-RESPONSE` and
`X-PAYMENT-RESPONSE`; the app sets this in `web/app.py`. nginx forwards the
headers by default — the x402 v2 names are hyphenated, so nginx's
underscore-dropping does not apply. Verify with a live call, never by reading
config.

---

## Reference

| Path | Purpose |
|------|---------|
| `/opt/authen` | Application, owned by `authen` |
| `/opt/authen/config/node.toml` | Node config — committed, payTo is public |
| `/etc/authen/env` | Service environment. **No secrets** — the node self-generates its identity on first boot |
| `/var/lib/authen` | State: `authen.ini`, encrypted DB, content. **Back this up** |
| `/etc/systemd/system/authen.service` | Unit |
| `/etc/nginx/sites-available/authen.conf` | vhost |
| `/var/log/authen-startup.log` | Install log |

| Command | Purpose |
|---------|---------|
| `sudo bash scripts/vps_startup.sh` | Install or re-verify |
| `sudo bash scripts/status.sh` | Full health check |
| `tools/check_optin.py --network mainnet --address <a>` | Confirm payTo can receive |
| `tools/pay_once.py` | End-to-end paid purchase (**testnet only**, refuses mainnet) |
