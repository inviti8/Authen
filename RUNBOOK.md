# PintheonV2 Deployment Runbook

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
> `pintheonv2/config.py` imports `tomllib`, which is stdlib only from **Python
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

DOMAIN="pintheon.example.art"
ADMIN_EMAIL=""
PAYTO_MAINNET=""
REPO_URL="https://github.com/inviti8/PintheonV2.git"
REPO_BRANCH="main"
GITHUB_TOKEN=""
NETWORK="mainnet"
PINTHEON_USER="pintheon"
```

| Variable | Description |
|----------|-------------|
| `DOMAIN` | Public hostname. Becomes `public_url`, which is what the Bazaar publishes as the resource URL. Must resolve to this VPS before TLS can be issued. |
| `ADMIN_EMAIL` | Let's Encrypt expiry notices. Required for unattended issuance; leave empty to skip TLS and do it by hand. |
| `PAYTO_MAINNET` | The payTo **address**. Public — it is published in the Bazaar. The private key never touches the server. |
| `REPO_URL` / `REPO_BRANCH` | Source to deploy. Change if using a fork. |
| `GITHUB_TOKEN` | Fine-grained PAT, Contents → Read-only. **The repo is currently public — leave this empty.** Only needed if it is made private later. |
| `NETWORK` | `mainnet` or `testnet`. Prove the flow on testnet first if this is a rehearsal. |
| `PINTHEON_USER` | Service account. Owns `/opt/pintheonv2` and `/var/lib/pintheonv2`. |

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
| `pintheon.yourdomain.com` | A | `YOUR_VPS_IP` |

No wildcard needed — unlike the tunnel server, this node serves a single hostname
and uses HTTP-01, so none of the acme-dns machinery applies.

Wait 1–5 minutes, then verify:

```bash
dig +short pintheon.yourdomain.com
```

**Do this before Step 3.** Certbot's HTTP-01 challenge fails if DNS has not
propagated, and the install script will otherwise finish with an untrusted
endpoint.

---

## Step 3: Deploy

SSH into the VPS and run:

```bash
curl -O https://raw.githubusercontent.com/inviti8/PintheonV2/main/scripts/vps_startup.sh
chmod +x vps_startup.sh
sudo ./vps_startup.sh
```

The script will:

1. Preflight — root, placeholder domain, DNS vs. this host's public IP
2. Install nginx, certbot, ufw
3. Resolve a Python ≥ 3.11 (deadsnakes fallback on 22.04)
4. Create the `pintheon` service user
5. Clone the repo to `/opt/pintheonv2`
6. Build the venv and install dependencies
7. Write `config/node.toml` and `/etc/pintheonv2/env`
8. Generate placeholder content if no titles are staged
9. Install and enable the systemd unit
10. Configure nginx (HTTP first, so ACME can complete)
11. Open 22/80/443
12. Start the service, then issue TLS and swap in the full vhost

It is **idempotent**. Re-running after a reboot detects the marker at
`/var/lib/pintheonv2-install/.initialized` and only ensures services are up.

---

## Step 4: Verify

```bash
sudo bash /opt/pintheonv2/scripts/status.sh
```

This checks, in the order things fail:

- `pintheonv2` and `nginx` active; gunicorn listening on `127.0.0.1:8402`
- Certificate present and not expiring within 14 days; `certbot.timer` enabled
- `/health` → 200 and `/api/v1/titles` returns a catalogue — **over the public
  URL, not loopback**, because loopback proves gunicorn is alive, not that a
  buyer can reach you
- **`/api/v1/issue/<slug>` → 402, not 200**
- The challenge carries `extra.tag` and the configured payTo
- payTo is opted into USDC and can actually receive

Exit codes: `0` pass, `1` warning, `2` failure, `3` config unreadable. Use
`--quiet` for cron — it is silent on success and prints issues to stderr.

### The two failures worth understanding

**A paid route returning 200 means you are giving the issue away.** The x402
middleware regex-escapes route patterns and only substitutes `[param]`; a Flask
`<title>` survives as a literal, matches nothing, and the route is silently
unprotected. No warning, no log line. `tests/test_route_patterns.py` guards the
translation; `status.sh` catches it in production.

**A payTo that is not opted into USDC fails every settlement while the endpoint
looks perfectly healthy.** The 402 is correct, verification passes, and settlement
dies at the facilitator's simulate step. Check it, never assume it:

```bash
/opt/pintheonv2/.venv/bin/python /opt/pintheonv2/tools/check_optin.py \
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
- [ ] Real content staged (see Content below), not placeholder art
- [ ] Price confirmed in `[pricing] issue_micro_usdc`
- [ ] `https://<domain>/api/v1/issue/<slug>` returns 402 with `extra.tag`

Changing the hostname later costs the resource's discovery history but **not**
your leaderboard position — volume aggregates on payTo, not hostname. Changing
the **payTo** does split your entry. That is the one irreversible choice.

---

## Operations

### Content

Titles live under `PINTHEON_DATA_DIR`:

```
/var/lib/pintheonv2/content/titles/<slug>/meta.json
/var/lib/pintheonv2/content/titles/<slug>/pages/001.png
/var/lib/pintheonv2/content/previews/<slug>/001.png
```

`meta.json` sets `preview_pages`, which **is** the paywall boundary — pages past
it are never served free. After staging:

```bash
sudo chown -R pintheon:pintheon /var/lib/pintheonv2/content
sudo systemctl restart pintheonv2
```

### Backups — the one thing that cannot be rebuilt

```bash
sudo tar czf pintheonv2-state-$(date +%F).tar.gz -C /var/lib pintheonv2
```

`/var/lib/pintheonv2` holds `pintheon.ini`, the Fernet master key. **Losing it
makes the encrypted database permanently unreadable**, and the failure looks like
a clean first boot rather than an error. `config.py` refuses to start if the
database is present without its key, which turns silent data loss into a loud
stop — but only if you still have a backup to restore.

### Updating

```bash
cd /opt/pintheonv2
sudo -u pintheon git pull
sudo /opt/pintheonv2/.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart pintheonv2
sudo bash scripts/status.sh
```

### Logs

```bash
journalctl -u pintheonv2 -f          # service
tail -f /var/log/pintheonv2-startup.log
tail -f /var/log/nginx/pintheonv2.error.log
```

### Monitoring

Uptime must survive to **2026-10-08** (shortlist window). Point an external
monitor at `https://<domain>/health` and run the status check hourly:

```bash
# /etc/cron.d/pintheonv2-status
0 * * * * root bash /opt/pintheonv2/scripts/status.sh --quiet
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
sudo rm -rf /opt/pintheonv2/.venv
sudo python3.12 -m venv /opt/pintheonv2/.venv
sudo /opt/pintheonv2/.venv/bin/pip install -r /opt/pintheonv2/requirements.txt
sudo systemctl restart pintheonv2
```

### Service fails to start

```bash
journalctl -u pintheonv2 -n 50 --no-pager
```

Common causes:

| Message | Cause |
|---|---|
| `[treasury].mainnet is empty` | payTo not configured. Edit `config/node.toml`. |
| `No node config at ...` | `config/node.toml` missing. Copy from `node.example.toml`. |
| Hangs on start | `server.initialize()` calls the facilitator's `/supported`. Check egress. |
| `has no master key at ...` | Refusing to start rather than orphan an encrypted DB. Restore `pintheon.ini`. |

### Certificate issuance failed

Almost always DNS. Confirm it resolves here, then retry:

```bash
dig +short <domain>
curl -s ifconfig.me
sudo certbot --nginx -d <domain> --redirect
```

If certbot reports `KeyError: 'PATH'`, set PATH as above first.

### Paid route returns 200 instead of 402

The route pattern is not matching and the issue is being served for free. Verify
`[routes] issue` in `config/node.toml` uses Flask syntax (`<title>`) — the
translation to x402 syntax (`[title]`) happens in `to_x402_pattern()`. Run the
suite:

```bash
/opt/pintheonv2/.venv/bin/python -m pytest /opt/pintheonv2/tests/ -q
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
| `/opt/pintheonv2` | Application, owned by `pintheon` |
| `/opt/pintheonv2/config/node.toml` | Node config — committed, payTo is public |
| `/etc/pintheonv2/env` | Service environment. **No secrets** — the node self-generates its identity on first boot |
| `/var/lib/pintheonv2` | State: `pintheon.ini`, encrypted DB, content. **Back this up** |
| `/etc/systemd/system/pintheonv2.service` | Unit |
| `/etc/nginx/sites-available/pintheonv2.conf` | vhost |
| `/var/log/pintheonv2-startup.log` | Install log |

| Command | Purpose |
|---------|---------|
| `sudo bash scripts/vps_startup.sh` | Install or re-verify |
| `sudo bash scripts/status.sh` | Full health check |
| `tools/check_optin.py --network mainnet --address <a>` | Confirm payTo can receive |
| `tools/pay_once.py` | End-to-end paid purchase (**testnet only**, refuses mainnet) |
