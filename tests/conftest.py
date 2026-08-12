"""Test fixtures — a self-contained Authen node."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

TREASURY = "E64BQIOXKTT4BVMIFY2S5WX337FT6MLF66UPZEUPDYAKT4QIFOXXQCR24Q"


@pytest.fixture(scope="session")
def node_dir(tmp_path_factory) -> Path:
    """An empty data directory. The node generates its own identity into it."""
    return tmp_path_factory.mktemp("nodedata")


@pytest.fixture(scope="session")
def config_file(tmp_path_factory) -> Path:
    """Copy the sample config and fill in a testnet treasury."""
    dst = tmp_path_factory.mktemp("cfg") / "node.toml"
    src = (REPO_ROOT / "config" / "node.example.toml").read_text(encoding="utf-8")
    shutil.copy(REPO_ROOT / "config" / "node.example.toml", dst)
    dst.write_text(
        src.replace('testnet = ""', f'testnet = "{TREASURY}"', 1),
        encoding="utf-8",
    )
    return dst


@pytest.fixture(scope="session")
def cfg(node_dir, config_file, monkeypatch_session):
    from authen.config import load_config

    monkeypatch_session.setenv("AUTHEN_DATA_DIR", str(node_dir))
    monkeypatch_session.setenv("AUTHEN_NETWORK", "testnet")
    return load_config(config_file)


@pytest.fixture(scope="session")
def monkeypatch_session():
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="session")
def client(cfg):
    """Test client.

    Building the app contacts the facilitator's /supported once, via
    server.initialize(). That is a deliberate integration point: a config the
    facilitator rejects should fail here rather than in production.
    """
    from authen.web.app import create_app

    return create_app(cfg).test_client()


@pytest.fixture(scope="session")
def identity(cfg):
    from authen.keys import load_or_create

    return load_or_create(cfg.paths.data_dir)
