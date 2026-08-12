#!/bin/bash
#
# PintheonV2 - Node Status Check
#
# Verifies the things that actually gate revenue, in the order they fail:
# service up, TLS valid, free routes answer, paid route challenges correctly,
# payTo can receive USDC. Suitable for ad-hoc inspection or cron alerting.
#
# The expensive failure this exists to catch is a node that looks completely
# healthy - 200s on every free route - while the paid route serves for free or
# every settlement bounces off an un-opted-in payTo.
#
# Usage:
#   sudo bash status.sh            # verbose
#   sudo bash status.sh --quiet    # silent on success (cron-friendly)
#   sudo bash status.sh --domain pintheon.example.art
#
# Exit codes:
#   0 - All checks passed
#   1 - Warning (cert expiring soon, placeholder content)
#   2 - Failure (service down, paid route unprotected, payTo cannot receive)
#   3 - Cannot determine (config unreadable)
#

set -uo pipefail

APP_DIR="${APP_DIR:-/opt/pintheonv2}"
VENV_DIR="${APP_DIR}/.venv"
CFG="${APP_DIR}/config/node.toml"
WARN_DAYS=14
QUIET=0
DOMAIN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quiet|-q)  QUIET=1; shift ;;
        --domain)    DOMAIN="${2:-}"; shift 2 ;;
        -h|--help)   sed -n '2,26p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1"; exit 3 ;;
    esac
done

out() { [[ $QUIET -eq 0 ]] && echo "$@" || true; }
err() { echo "$@" >&2; }

EXIT_CODE=0
ISSUES=()

note() {
    local sev="$1" msg="$2"
    ISSUES+=("[$sev] $msg")
    case "$sev" in
        FAIL) EXIT_CODE=2 ;;
        WARN) [[ $EXIT_CODE -lt 1 ]] && EXIT_CODE=1 ;;
    esac
}

ok()   { out "  OK    $*"; }
bad()  { out "  FAIL  $*"; note FAIL "$*"; }
warn() { out "  WARN  $*"; note WARN "$*"; }

out ""
out "============================================================"
out "PintheonV2 Status - $(date)"
out "============================================================"

#-----------------------------------------------------------------------------
# Configuration
#-----------------------------------------------------------------------------

out ""
out "--- Configuration ---"

if [[ ! -f "$CFG" ]]; then
    err "No config at $CFG"
    exit 3
fi

read_cfg() {
    "${VENV_DIR}/bin/python" - "$CFG" "$1" <<'PY' 2>/dev/null
import sys, tomllib, pathlib
cfg_path, dotted = sys.argv[1], sys.argv[2]
cur = tomllib.loads(pathlib.Path(cfg_path).read_text(encoding="utf-8"))
for k in dotted.split("."):
    cur = cur.get(k, "") if isinstance(cur, dict) else ""
print(cur if isinstance(cur, str) else "")
PY
}

NETWORK="$(grep -oP '^PINTHEON_NETWORK=\K.*' /etc/pintheonv2/env 2>/dev/null || echo mainnet)"
[[ -n "$DOMAIN" ]] || DOMAIN="$(read_cfg node.public_url | sed 's|https\?://||; s|/$||')"
PAYTO="$(read_cfg "treasury.${NETWORK}")"

out "  network    ${NETWORK}"
out "  domain     ${DOMAIN:-UNSET}"
out "  payTo      ${PAYTO:-UNSET}"

[[ -n "$DOMAIN" ]] || bad "public_url is not set in ${CFG}"
[[ -n "$PAYTO"  ]] || bad "[treasury].${NETWORK} is empty - the node cannot be paid"

#-----------------------------------------------------------------------------
# Services
#-----------------------------------------------------------------------------

out ""
out "--- Services ---"

for svc in pintheonv2 nginx; do
    if systemctl is-active --quiet "$svc"; then
        ok "${svc} active"
    else
        bad "${svc} is NOT running  (journalctl -u ${svc} -n 40)"
    fi
done

if ss -tlnp 2>/dev/null | grep -q '127.0.0.1:8402'; then
    ok "gunicorn listening on 127.0.0.1:8402"
else
    bad "nothing listening on 127.0.0.1:8402"
fi

#-----------------------------------------------------------------------------
# TLS
#-----------------------------------------------------------------------------

out ""
out "--- TLS ---"

CERT="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
if [[ ! -f "$CERT" ]]; then
    bad "no certificate at ${CERT}  (certbot --nginx -d ${DOMAIN} --redirect)"
else
    END="$(openssl x509 -enddate -noout -in "$CERT" | cut -d= -f2)"
    END_EPOCH="$(date -d "$END" +%s 2>/dev/null || echo 0)"
    NOW="$(date +%s)"
    DAYS=$(( (END_EPOCH - NOW) / 86400 ))
    if   [[ $DAYS -lt 0          ]]; then bad  "certificate EXPIRED ${DAYS#-} days ago"
    elif [[ $DAYS -lt $WARN_DAYS ]]; then warn "certificate expires in ${DAYS} days"
    else ok "certificate valid for ${DAYS} more days"
    fi
fi

if systemctl is-enabled --quiet certbot.timer 2>/dev/null; then
    ok "certbot.timer enabled (unattended renewal)"
else
    warn "certbot.timer is not enabled - the cert will expire silently"
fi

#-----------------------------------------------------------------------------
# Endpoint behaviour
#
# Checked over the PUBLIC url, not loopback. Loopback proves gunicorn is alive;
# it does not prove a buyer can reach the node, which is what matters.
#-----------------------------------------------------------------------------

out ""
out "--- Endpoints (public) ---"

BASE="https://${DOMAIN}"

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@" 2>/dev/null || echo 000; }

HEALTH="$(code "${BASE}/health")"
[[ "$HEALTH" == "200" ]] && ok "/health -> 200" || bad "/health -> ${HEALTH}"

TITLES="$(curl -s --max-time 20 "${BASE}/api/v1/titles" 2>/dev/null || echo '')"
if echo "$TITLES" | grep -q '"slug"'; then
    N="$(echo "$TITLES" | grep -o '"slug"' | wc -l)"
    ok "/api/v1/titles lists ${N} title(s)"
    if echo "$TITLES" | grep -q 'placeholder-issue'; then
        warn "catalogue is still placeholder art - stage real content before launch"
    fi
else
    bad "/api/v1/titles returned no catalogue"
fi

SLUG="$(echo "$TITLES" | grep -oP '"slug"\s*:\s*"\K[^"]+' | head -1)"
if [[ -n "$SLUG" ]]; then
    ISSUE="$(code "${BASE}/api/v1/issue/${SLUG}")"
    case "$ISSUE" in
        402) ok "/api/v1/issue/${SLUG} -> 402 (paywall active)" ;;
        200) bad "PAID ROUTE IS SERVING FOR FREE (200). Route pattern is not matching." ;;
        *)   bad "/api/v1/issue/${SLUG} -> ${ISSUE} (expected 402)" ;;
    esac

    # The challenge must carry the competition tag where the Bazaar reads it, or
    # the resource is cataloged untagged and never counts.
    CHAL="$(curl -s -D - -o /dev/null --max-time 20 "${BASE}/api/v1/issue/${SLUG}" 2>/dev/null \
            | grep -i '^payment-required:' | cut -d' ' -f2- | tr -d '\r')"
    if [[ -n "$CHAL" ]]; then
        DEC="$(echo "$CHAL" | base64 -d 2>/dev/null || echo '')"
        echo "$DEC" | grep -q '"tag"' \
            && ok "402 challenge carries a tag in accepts[].extra" \
            || warn "402 challenge has no extra.tag - it will be cataloged untagged"
        echo "$DEC" | grep -q "$PAYTO" \
            && ok "402 challenge advertises the configured payTo" \
            || bad "402 challenge payTo does not match ${CFG}"
    else
        bad "402 response carried no PAYMENT-REQUIRED header"
    fi
fi

#-----------------------------------------------------------------------------
# payTo can actually receive
#
# Read-only, address-only. Safe against mainnet - no key is involved.
#-----------------------------------------------------------------------------

out ""
out "--- payTo opt-in ---"

if [[ -n "$PAYTO" && -x "${VENV_DIR}/bin/python" ]]; then
    if "${VENV_DIR}/bin/python" "${APP_DIR}/tools/check_optin.py" \
        --network "$NETWORK" --address "$PAYTO" >/dev/null 2>&1; then
        ok "payTo is opted into USDC and can receive"
    else
        bad "payTo CANNOT receive USDC - every settlement will fail at simulate"
    fi
else
    warn "skipped (payTo unset or venv missing)"
fi

#-----------------------------------------------------------------------------
# Summary
#-----------------------------------------------------------------------------

out ""
out "============================================================"
if [[ ${#ISSUES[@]} -eq 0 ]]; then
    out "ALL CHECKS PASSED - node is live and sellable"
else
    if [[ $QUIET -eq 1 ]]; then
        err "PintheonV2 status: ${#ISSUES[@]} issue(s) on ${DOMAIN}"
        for i in "${ISSUES[@]}"; do err "  $i"; done
    else
        out "${#ISSUES[@]} issue(s):"
        for i in "${ISSUES[@]}"; do out "  $i"; done
    fi
fi
out "============================================================"
out ""

exit $EXIT_CODE
