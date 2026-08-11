"""Test fixtures — a self-contained node with a tiny synthetic library."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent

TREASURY = "NJO3MQADL3UO236P75NAV4NCVFNA2SVVYH6BVUO5MFMIHBZVXNAQNNNFYI"
SLUG = "test-issue"
PAGES = 4
PREVIEW_PAGES = 2


@pytest.fixture(scope="session")
def node_dir(tmp_path_factory) -> Path:
    """A data directory with a small library: 4 pages, 2 of them previewable."""
    d = tmp_path_factory.mktemp("nodedata")
    pages = d / "content" / "titles" / SLUG / "pages"
    previews = d / "content" / "previews" / SLUG
    pages.mkdir(parents=True)
    previews.mkdir(parents=True)

    for n in range(1, PAGES + 1):
        Image.new("RGB", (40, 60), (n * 40 % 255, 120, 200)).save(pages / f"{n:03d}.png")
        if n <= PREVIEW_PAGES:
            Image.new("RGB", (40, 60), (10, 10, 10)).save(previews / f"{n:03d}.png")

    (d / "content" / "titles" / SLUG / "meta.json").write_text(
        json.dumps(
            {
                "title": "Test Issue",
                "creator": "fixture",
                "blurb": "synthetic",
                "preview_pages": PREVIEW_PAGES,
            }
        ),
        encoding="utf-8",
    )
    return d


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
    from pintheonv2.config import load_config

    monkeypatch_session.setenv("PINTHEON_DATA_DIR", str(node_dir))
    monkeypatch_session.setenv("PINTHEON_NETWORK", "testnet")
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
    server.initialize(). That is a deliberate integration point: a config that the
    facilitator rejects should fail here rather than in production.
    """
    from pintheonv2.web.app import create_app

    return create_app(cfg).test_client()
