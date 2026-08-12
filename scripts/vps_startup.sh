#!/bin/bash
#
# Authen - VPS Startup Script
#
# Provisions the flagship node: the one instance that carries competition uptime.
# Public HTTPS, real certificate, paid x402 endpoint settling on Algorand mainnet.
#
# Works on any Ubuntu/Debian VPS:
#   - DigitalOcean, Linode, Vultr, Hetzner, Oracle Cloud
#   - Container-based VPS (OpenVZ, LXC)
#
# Usage:
#   curl -O https://raw.githubusercontent.com/inviti8/Authen/main/scripts/vps_startup.sh
#   chmod +x vps_startup.sh
#   sudo ./vps_startup.sh
#
# Or via cloud-init: paste into "user data" / "startup script" field.
#
# Idempotent. Re-running after a reboot only ensures services are up.
#
# Logs: /var/log/authen-startup.log
#

set -uo pipefail  # not -e: we handle errors with explicit messages

#=============================================================================
# CONFIGURATION - Modify these variables for your deployment
#=============================================================================

# Public hostname for this node. Must already have an A record pointing here.
DOMAIN="authen.example.com"

# Email for Let's Encrypt expiry notices. Required for unattended cert issuance.
ADMIN_EMAIL=""

# The payTo address - PUBLIC, this is only the address, never a key or mnemonic.
# Leave empty to configure later; the script will still finish but will not
# declare the node live.
PAYTO_MAINNET=""

# Git repository
REPO_URL="https://github.com/inviti8/Authen.git"
REPO_BRANCH="main"

# GitHub Personal Access Token (only needed while the repo is private).
# Generate at: https://github.com/settings/tokens (fine-grained, Contents read-only)
GITHUB_TOKEN=""

# Active network: "mainnet" | "testnet". Prove the flow on testnet first.
NETWORK="mainnet"

# Linux user that owns and runs the service
AUTHEN_USER="authen"

#=============================================================================
# END CONFIGURATION - Do not modify below unless you know what you're doing
#=============================================================================

# Derived paths. These match deploy/systemd/authen.service - if you change
# one, change both.
APP_DIR="/opt/authen"
VENV_DIR="${APP_DIR}/.venv"
DATA_DIR="/var/lib/authen"
ENV_DIR="/etc/authen"
ENV_FILE="${ENV_DIR}/env"
MARKER_DIR="/var/lib/authen-install"
MARKER_FILE="${MARKER_DIR}/.initialized"
LOG_FILE="/var/log/authen-startup.log"

# tomllib is stdlib only from 3.11. authen/config.py imports it, so 3.10 -
# which is what Ubuntu 22.04 ships - cannot run this app at all.
MIN_PY_MINOR=11

# Ensure PATH is set (fixes container-based VPS like OpenVZ/LXC)
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

# Redirect all output to the log
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "============================================================"
echo "Authen Startup Script"
echo "Started: $(date)"
echo "============================================================"
echo ""

fail() { echo ""; echo "ERROR: $*"; echo ""; exit 1; }

# Write the full TLS vhost, adjusted for this nginx's HTTP/2 syntax.
#
# The committed vhost uses the legacy `listen 443 ssl http2;` form because it is
# the one that cannot brick a server: unknown directives make nginx refuse to
# load its entire configuration, so getting this wrong takes the site down rather
# than just disabling HTTP/2. On nginx >= 1.25.1 that form still works but is
# deprecated, so upgrade it to `http2 on;` there to keep `nginx -t` quiet.
install_full_vhost() {
    sed "s/authen\.example\.com/${DOMAIN}/g" \
        "${APP_DIR}/deploy/nginx/authen.conf" > "$VHOST"

    local ver
    ver="$(nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo 0.0.0)"
    if printf '%s\n%s\n' "1.25.1" "$ver" | sort -V -C; then
        sed -i -E 's/^([[:space:]]*)listen (\[::\]:)?443 ssl http2;/\1listen \2443 ssl;/' "$VHOST"
        sed -i -E '0,/^([[:space:]]*)listen \[::\]:443 ssl;/s//\1listen [::]:443 ssl;\n\1http2 on;/' "$VHOST"
        echo "  nginx ${ver} >= 1.25.1 - using 'http2 on;'"
    else
        echo "  nginx ${ver} < 1.25.1 - using legacy 'listen ... http2' form"
    fi
}

#-----------------------------------------------------------------------------
# Check if this is a restart (marker file exists)
#-----------------------------------------------------------------------------

if [[ -f "$MARKER_FILE" ]]; then
    echo "=== RESTART DETECTED ==="
    echo "Marker file exists: $MARKER_FILE"
    echo "Skipping initialization (already completed)"
    echo ""
    echo "Ensuring services are running..."

    systemctl is-active --quiet authen || systemctl start authen
    systemctl is-active --quiet nginx      || systemctl start nginx

    echo "Services verified."
    echo ""
    echo "Restart complete: $(date)"
    exit 0
fi

echo "=== FIRST BOOT - FULL INITIALIZATION ==="
echo ""

#-----------------------------------------------------------------------------
# Preflight
#-----------------------------------------------------------------------------

echo "--- Preflight ---"

[[ "$(id -u)" -eq 0 ]] || fail "This script must be run as root (use sudo)."

if [[ "$DOMAIN" == "authen.example.com" ]]; then
    fail "DOMAIN is still the placeholder. Edit the CONFIGURATION block first."
fi

if [[ -z "$ADMIN_EMAIL" ]]; then
    echo "WARNING: ADMIN_EMAIL is empty. Certificate issuance will be skipped."
fi

# DNS must already point here, or certbot's HTTP-01 challenge cannot succeed.
PUBLIC_IP="$(curl -s --max-time 10 ifconfig.me 2>/dev/null || echo '')"
RESOLVED="$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -1)"
echo "  public IP     : ${PUBLIC_IP:-unknown}"
echo "  ${DOMAIN} -> ${RESOLVED:-NXDOMAIN}"
if [[ -n "$PUBLIC_IP" && -n "$RESOLVED" && "$PUBLIC_IP" != "$RESOLVED" ]]; then
    echo "  WARNING: DNS does not point at this host. TLS issuance will fail"
    echo "           until it does. Continuing with the rest of the install."
fi

echo ""

#-----------------------------------------------------------------------------
# System packages
#-----------------------------------------------------------------------------

echo "--- Installing system packages ---"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || fail "apt-get update failed"
apt-get install -y -qq \
    git curl ca-certificates \
    nginx \
    certbot python3-certbot-nginx \
    ufw \
    || fail "package installation failed"

echo ""

#-----------------------------------------------------------------------------
# Python 3.11+
#
# This is the single most common deployment failure for this app. Ubuntu 22.04
# ships Python 3.10, which has no tomllib, and authen/config.py imports it
# at module scope. The app cannot start. Fail loudly here rather than at first
# request.
#-----------------------------------------------------------------------------

echo "--- Resolving a Python interpreter (need >= 3.${MIN_PY_MINOR}) ---"

PY=""
for cand in python3.13 python3.12 python3.11 python3; do
    command -v "$cand" >/dev/null 2>&1 || continue
    minor="$("$cand" -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)"
    major="$("$cand" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo 0)"
    if [[ "$major" -eq 3 && "$minor" -ge "$MIN_PY_MINOR" ]]; then
        PY="$(command -v "$cand")"
        echo "  found $PY (3.$minor)"
        break
    fi
done

if [[ -z "$PY" ]]; then
    echo "  No suitable interpreter. Adding deadsnakes PPA and installing 3.12..."
    apt-get install -y -qq software-properties-common || fail "cannot install software-properties-common"
    add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1 || \
        fail "could not add deadsnakes PPA. Use Ubuntu 24.04 (ships Python 3.12) instead."
    apt-get update -qq
    apt-get install -y -qq python3.12 python3.12-venv python3.12-dev || \
        fail "python3.12 install failed. Use Ubuntu 24.04 instead."
    PY="$(command -v python3.12)"
    echo "  installed $PY"
fi

# venv module is packaged separately on Debian/Ubuntu.
PYVER="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
apt-get install -y -qq "python${PYVER}-venv" >/dev/null 2>&1 || true

echo ""

#-----------------------------------------------------------------------------
# Service user
#-----------------------------------------------------------------------------

echo "--- Creating service user '${AUTHEN_USER}' ---"

if id "$AUTHEN_USER" >/dev/null 2>&1; then
    echo "  user already exists"
else
    useradd --system --create-home --shell /usr/sbin/nologin "$AUTHEN_USER" \
        || fail "could not create user"
    echo "  created"
fi

echo ""

#-----------------------------------------------------------------------------
# Clone the repository
#-----------------------------------------------------------------------------

echo "--- Fetching the application ---"

CLONE_URL="$REPO_URL"
if [[ -n "$GITHUB_TOKEN" ]]; then
    CLONE_URL="${REPO_URL/https:\/\//https://${GITHUB_TOKEN}@}"
fi

if [[ -d "${APP_DIR}/.git" ]]; then
    echo "  repo present, pulling ${REPO_BRANCH}"
    git -C "$APP_DIR" remote set-url origin "$CLONE_URL"
    git -C "$APP_DIR" fetch --quiet origin "$REPO_BRANCH" || fail "git fetch failed"
    git -C "$APP_DIR" checkout --quiet "$REPO_BRANCH"
    git -C "$APP_DIR" reset --hard --quiet "origin/${REPO_BRANCH}"
else
    mkdir -p "$(dirname "$APP_DIR")"
    git clone --quiet --branch "$REPO_BRANCH" "$CLONE_URL" "$APP_DIR" \
        || fail "git clone failed. If the repo is private, set GITHUB_TOKEN."
    echo "  cloned into ${APP_DIR}"
fi

# Never leave the token readable in the git remote beyond what is needed.
chmod 700 "${APP_DIR}/.git"

echo ""

#-----------------------------------------------------------------------------
# Virtualenv and dependencies
#-----------------------------------------------------------------------------

echo "--- Building the virtualenv ---"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    "$PY" -m venv "$VENV_DIR" || fail "venv creation failed"
fi
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip wheel || fail "pip upgrade failed"
"${VENV_DIR}/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt" \
    || fail "dependency installation failed"
echo "  dependencies installed"

echo ""

#-----------------------------------------------------------------------------
# Configuration
#
# The node config is committed (config/node.toml) because the payTo address is
# public and version-controlling it makes an accidental change show up in a
# diff. What this step does is fill in the deployment-specific values.
#-----------------------------------------------------------------------------

echo "--- Writing configuration ---"

CFG="${APP_DIR}/config/node.toml"
if [[ ! -f "$CFG" ]]; then
    cp "${APP_DIR}/config/node.example.toml" "$CFG"
    echo "  seeded config/node.toml from the example"
fi

# public_url is what lands in the Bazaar as the resource URL. It must be the real
# public origin - a wrong value here publishes an unreachable resource.
#
# Patterns tolerate any alignment whitespace. Matching a fixed number of spaces
# would silently no-op if the file were ever reformatted, leaving the node on the
# example.art placeholder - which looks fine until it is published.
sed -i -E "s|^public_url[[:space:]]*=.*|public_url   = \"https://${DOMAIN}\"|" "$CFG"
sed -i -E "s|^network[[:space:]]*=.*|network      = \"${NETWORK}\"|" "$CFG"

if [[ -n "$PAYTO_MAINNET" ]]; then
    sed -i -E "s|^mainnet[[:space:]]*=.*|mainnet = \"${PAYTO_MAINNET}\"|" "$CFG"
    echo "  payTo (mainnet): ${PAYTO_MAINNET}"
else
    echo "  WARNING: PAYTO_MAINNET not set. Edit ${CFG} before going live."
fi
echo "  public_url: https://${DOMAIN}"

mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<EOF
# Authen service environment.
#
# Node secrets are NOT here. The node generates its identity seed on first boot
# and persists it under AUTHEN_DATA_DIR, the same way V1 self-generates
# master_key into authen.ini. Do not inject one except to restore a backup.
AUTHEN_DATA_DIR=${DATA_DIR}
AUTHEN_NETWORK=${NETWORK}
EOF
    chmod 640 "$ENV_FILE"
    echo "  wrote ${ENV_FILE}"
fi

# Data directory holds authen.ini and the encrypted DB. Losing authen.ini
# makes the database permanently unreadable - this path must be backed up.
mkdir -p "$DATA_DIR"
chown -R "${AUTHEN_USER}:${AUTHEN_USER}" "$DATA_DIR" "$APP_DIR"
chown root:"${AUTHEN_USER}" "$ENV_FILE"

echo ""

#-----------------------------------------------------------------------------
# Node identity
#
# Generated on first boot into DATA_DIR, never injected by env. Record the public
# key in config afterwards: startup then asserts it still matches, which catches a
# state volume that failed to mount. A silently regenerated key looks like a clean
# first boot and invalidates every attestation the node has ever signed.
#-----------------------------------------------------------------------------

echo "--- Node identity ---"

if [[ -f "${DATA_DIR}/node_seed.bin" ]]; then
    echo "  existing identity found (seed present)"
else
    echo "  none yet - generated on first start"
fi

echo ""

#-----------------------------------------------------------------------------
# systemd
#-----------------------------------------------------------------------------

echo "--- Installing the systemd unit ---"

install -m 644 "${APP_DIR}/deploy/systemd/authen.service" \
    /etc/systemd/system/authen.service || fail "could not install unit"
systemctl daemon-reload
systemctl enable --quiet authen
echo "  enabled authen.service"

echo ""

#-----------------------------------------------------------------------------
# nginx
#-----------------------------------------------------------------------------

echo "--- Configuring nginx ---"

VHOST="/etc/nginx/sites-available/authen.conf"
install_full_vhost

# The vhost references certificate paths that do not exist until certbot runs.
# Serve plain HTTP first so the ACME challenge can complete, then let certbot
# rewrite the TLS block.
mkdir -p /var/www/certbot
if [[ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
    echo "  no certificate yet - installing an HTTP-only vhost for the ACME challenge"
    cat > "$VHOST" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        proxy_pass http://127.0.0.1:8402;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
fi

rm -f /etc/nginx/sites-enabled/default
ln -sf "$VHOST" /etc/nginx/sites-enabled/authen.conf
nginx -t || fail "nginx configuration is invalid"
systemctl enable --quiet nginx
systemctl restart nginx
echo "  nginx configured and restarted"

echo ""

#-----------------------------------------------------------------------------
# Firewall
#-----------------------------------------------------------------------------

echo "--- Firewall ---"

if command -v ufw >/dev/null 2>&1; then
    ufw allow 22/tcp   >/dev/null 2>&1 || true
    ufw allow 80/tcp   >/dev/null 2>&1 || true
    ufw allow 443/tcp  >/dev/null 2>&1 || true
    ufw --force enable >/dev/null 2>&1 || true
    echo "  22, 80, 443 allowed"
    echo "  NOTE: your VPS provider may have a separate firewall to open too"
fi

echo ""

#-----------------------------------------------------------------------------
# Start the application
#-----------------------------------------------------------------------------

echo "--- Starting authen ---"

systemctl restart authen
sleep 4
if systemctl is-active --quiet authen; then
    echo "  service is running"
else
    echo "  SERVICE FAILED TO START. Recent log:"
    journalctl -u authen -n 30 --no-pager
fi

echo ""

#-----------------------------------------------------------------------------
# TLS
#
# Single-domain HTTP-01. No wildcard needed here, so none of the acme-dns
# machinery the tunnel server requires applies. certbot.timer handles renewal.
#-----------------------------------------------------------------------------

echo "--- TLS certificate ---"

if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
    echo "  certificate already present"
    # Ensure the full vhost (with TLS + x402 header handling) is in place.
    install_full_vhost
    nginx -t && systemctl reload nginx
elif [[ -n "$ADMIN_EMAIL" ]]; then
    if certbot --nginx --non-interactive --agree-tos \
        --email "$ADMIN_EMAIL" -d "$DOMAIN" --redirect; then
        echo "  certificate issued"
        # Swap in the real vhost now that the cert paths resolve.
        install_full_vhost
        if nginx -t; then
            systemctl reload nginx
        else
            # Never leave an invalid config on disk: nginx is still serving the
            # old one from memory, but the next restart or reboot would fail and
            # take the endpoint down silently.
            echo "  VHOST INVALID - reverting to the ACME-time config so nginx stays bootable"
            certbot --nginx --non-interactive --agree-tos \
                --email "$ADMIN_EMAIL" -d "$DOMAIN" --redirect --reinstall
            nginx -t && systemctl reload nginx
        fi
    else
        echo "  CERTIFICATE ISSUANCE FAILED."
        echo "  Most common cause: DNS is not yet pointing at this host."
        echo "  Fix DNS, then re-run:  certbot --nginx -d ${DOMAIN} --redirect"
    fi
else
    echo "  skipped (ADMIN_EMAIL not set)"
    echo "  Run manually: certbot --nginx -d ${DOMAIN} --redirect"
fi

echo ""

#-----------------------------------------------------------------------------
# Mark initialization complete
#-----------------------------------------------------------------------------

mkdir -p "$MARKER_DIR"
date > "$MARKER_FILE"

#-----------------------------------------------------------------------------
# Summary
#-----------------------------------------------------------------------------

echo "============================================================"
echo "Authen Initialization Complete"
echo "============================================================"
echo ""
echo "Endpoints:"
echo "  Health:    https://${DOMAIN}/health"
echo "  Identity:  https://${DOMAIN}/api/v1/identity   (free - the signing key)"
echo "  Verify:    https://${DOMAIN}/api/v1/verify     (free)"
echo "  Notarize:  https://${DOMAIN}/api/v1/notarize   (paid, 402)"
echo ""
echo "Paths:"
echo "  App:     ${APP_DIR}"
echo "  Config:  ${APP_DIR}/config/node.toml"
echo "  Env:     ${ENV_FILE}"
echo "  Data:    ${DATA_DIR}   <-- BACK THIS UP (holds authen.ini)"
echo ""
echo "Next Steps:"
echo ""
echo "  1. Verify the node answers and challenges correctly:"
echo "       sudo bash ${APP_DIR}/scripts/status.sh"
echo ""
if [[ -z "$PAYTO_MAINNET" ]]; then
echo "  2. Set the payTo address (REQUIRED before going live):"
echo "       sudo -u ${AUTHEN_USER} nano ${APP_DIR}/config/node.toml"
echo "       # [treasury] mainnet = \"<your-address>\""
echo "       sudo systemctl restart authen"
echo ""
fi
echo "  3. Confirm payTo is opted into USDC (ASA 31566704). Without this every"
echo "     settlement fails at the facilitator's simulate step while the endpoint"
echo "     still looks healthy:"
echo "       ${VENV_DIR}/bin/python ${APP_DIR}/tools/check_optin.py \\"
echo "           --network mainnet --address <your-address>"
echo ""
echo "  4. The FIRST paid request auto-catalogs this URL into the Bazaar, tied to"
echo "     your payTo. Make sure https://${DOMAIN} is the URL you intend to keep"
echo "     before taking a real payment."
echo ""
echo "Logs:"
echo "  Startup:  ${LOG_FILE}"
echo "  Service:  journalctl -u authen -f"
echo ""
echo "Completed: $(date)"
echo "============================================================"
