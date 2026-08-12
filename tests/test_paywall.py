"""The browser-facing paywall must be the Algorand one.

The x402 SDK ships three paywall bundles (EVM, SVM, AVM) and defaults to **EVM**
when no provider is registered. That default is catastrophic here and completely
silent: the EVM bundle is a wagmi/ethers React app that parses the payment
requirement's `network` as an EVM chain id. Given `algorand:<genesis-hash>` it
produces NaN, throws `Unsupported chain ID: NaN` during render, and paints a blank
white page. It also calls out to cca-lite.coinbase.com.

Nothing about the response looks wrong from the outside — 402, correct headers,
1.9MB of HTML — so no status check catches it. Only a human opening the URL does.

These tests need no network beyond app construction.
"""

from __future__ import annotations

import json
import re

import pytest


BROWSER = {"Accept": "text/html", "User-Agent": "Mozilla/5.0 Chrome/140"}


def _module_bundle(html: str) -> str:
    m = re.search(r'<script type="module">(.*?)</script>', html, re.S)
    assert m, "paywall has no module bundle"
    return m.group(1)


def _template_bundle(template: str) -> str:
    m = re.search(r'<script type="module">(.*?)</script>', template, re.S)
    assert m
    return m.group(1)


@pytest.fixture(scope="module")
def paywall_html(client) -> str:
    r = client.post("/api/v1/notarize", data=b"probe", headers=BROWSER)
    assert r.status_code == 402
    assert r.headers["Content-Type"].startswith("text/html")
    return r.get_data(as_text=True)


def test_serves_the_avm_paywall_not_evm(paywall_html):
    """The one that matters. EVM here means a blank page for every human buyer."""
    from x402.http.paywall.avm_paywall_template import AVM_PAYWALL_TEMPLATE as AVM
    from x402.http.paywall.evm_paywall_template import EVM_PAYWALL_TEMPLATE as EVM

    served = _module_bundle(paywall_html)
    assert served == _template_bundle(AVM), (
        "Paywall is not the AVM bundle. If this is the EVM bundle the page renders "
        "blank with 'Unsupported chain ID: NaN' — check that create_app() still "
        "passes paywall_provider with AvmPaywallHandler."
    )
    assert served != _template_bundle(EVM)


def test_paywall_speaks_algorand_wallets(paywall_html):
    served = _module_bundle(paywall_html)
    assert "Pera" in served, "AVM paywall should offer an Algorand wallet"
    assert "coinbase" not in served, "EVM bundle leaked in; it phones home to Coinbase"


def test_paywall_config_is_injected(paywall_html):
    cfg = json.loads(re.search(r"window\.x402\s*=\s*(\{.*?\});", paywall_html, re.S).group(1))
    accepts = cfg["paymentRequired"]["accepts"][0]
    assert accepts["network"].startswith("algorand:")
    assert cfg["appName"], "appName is blank; the wallet prompt will be unbranded"


def test_testnet_flag_tracks_the_network(paywall_html, cfg):
    """`testnet` defaults to True in the SDK no matter the network.

    Left alone, a mainnet node advertises itself as testnet in its own paywall.
    """
    conf = json.loads(re.search(r"window\.x402\s*=\s*(\{.*?\});", paywall_html, re.S).group(1))
    assert conf["testnet"] is (cfg.network.name != "mainnet")


def test_agents_still_get_json_not_html(client):
    """The paywall must not displace the machine-readable challenge."""
    r = client.post("/api/v1/notarize", data=b"probe", headers={"Accept": "application/json"})
    assert r.status_code == 402
    assert "PAYMENT-REQUIRED" in r.headers
    assert not r.headers["Content-Type"].startswith("text/html")
