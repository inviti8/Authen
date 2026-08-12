"""Configuration resolution for Authen.

Data-directory resolution is ported from Pintheon V1 (`pintheon/config.py:43`) —
same precedence order, but the env prefix is `AUTHEN_`, not V1's `PINTHEON_`. That
is a deliberate break: Authen is a different product and there are no existing
deployments to stay compatible with. If a node ever has to read a V1 layout, add an
explicit fallback rather than quietly reusing the old name.

Two further departures from V1, both from IMPLEMENTATION_PLAN.md §4.2:

  1. The DB directory and the DB file no longer share a name. V1 had `config.DB_PATH`
     (a directory) shadowed 27 lines later by `pintheon.DB_PATH` (a file).
  2. The master key has its own explicit path anchored to the data directory, rather
     than being derived from wherever the database happens to sit. In V1 the key
     followed `Path(db_path).parent`, which is why `migrate_data.sh` can move a
     database away from its key and render it permanently unreadable.

There are no filesystem side effects at import time — call `ensure_dirs()`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEBUG = os.getenv("AUTHEN_DEBUG", "false").lower() == "true"
IN_CONTAINER = os.path.exists("/.dockerenv") or bool(
    os.environ.get("APPTAINER_CONTAINER")
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTAINER_DATA_DIR = "/home/authen/data"


# --------------------------------------------------------------------------
# Data directory — ported from V1 config.py:43 _resolve_data_dir()
# --------------------------------------------------------------------------


def resolve_data_dir() -> Path:
    """Resolve the data directory: env var > debug path > container path > platformdirs.

    Same precedence and same env var as V1, so existing deployments keep their layout.
    """
    env = os.environ.get("AUTHEN_DATA_DIR")
    if env:
        return Path(env)
    if DEBUG:
        return Path("~/.local/share/AUTHEN").expanduser()
    if IN_CONTAINER:
        return Path(CONTAINER_DATA_DIR)
    try:
        from platformdirs import PlatformDirs

        return Path(PlatformDirs("AUTHEN").user_data_dir)
    except ImportError:
        return Path("~/.local/share/AUTHEN").expanduser()


@dataclass(frozen=True)
class Paths:
    data_dir: Path

    @property
    def db_dir(self) -> Path:
        return Path(os.environ.get("AUTHEN_DB_DIR", self.data_dir / "db"))

    @property
    def db_file(self) -> Path:
        """The encrypted TinyDB file. Phase 2."""
        return self.db_dir / "enc_db.json"

    @property
    def key_file(self) -> Path:
        """Fernet master key.

        Anchored to the data directory, NOT to `db_file.parent`. V1 derived this from
        the database's parent, which let the two be separated silently. Keeping it
        independent means relocating the database can never orphan its key.
        """
        return self.data_dir / "authen.ini"

    @property
    def ipfs_path(self) -> Path:
        return Path(os.environ.get("AUTHEN_IPFS_PATH", self.data_dir / "ipfs"))

    @property
    def content_dir(self) -> Path:
        return Path(os.environ.get("AUTHEN_CONTENT_DIR", self.data_dir / "content"))

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.db_dir, self.content_dir):
            d.mkdir(parents=True, exist_ok=True)

    def check_key_and_db_together(self) -> None:
        """Refuse to run with a database whose key is missing.

        Generating a fresh key beside an existing encrypted database is the moment the
        data becomes unrecoverable, and it looks like a clean first boot. Fail loudly
        instead. See IMPLEMENTATION_PLAN.md §4.2 ①.
        """
        if self.db_file.exists() and not self.key_file.exists():
            raise RuntimeError(
                f"Encrypted database at {self.db_file} has no master key at "
                f"{self.key_file}.\n"
                "Refusing to start: generating a new key would make this database "
                "permanently unreadable.\n"
                "Restore pintheon.ini from backup, or move the database aside to "
                "start a genuinely new node."
            )


# --------------------------------------------------------------------------
# node.toml
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NetworkProfile:
    name: str
    caip2: str
    slug: str
    usdc_asa: str
    usdc_decimals: int
    algod_url: str
    fee_payer: str


@dataclass(frozen=True)
class NodeConfig:
    raw: dict[str, Any]
    paths: Paths
    network: NetworkProfile
    pay_to: str
    public_url: str
    node_name: str
    facilitator_url: str
    tag: str
    scheme: str
    max_timeout_seconds: int
    price_micro_usdc: str
    c2pa_micro_usdc: str
    identity_pubkey: str

    @property
    def price_display(self) -> str:
        micro = int(self.price_micro_usdc)
        return f"${micro / 10 ** self.network.usdc_decimals:.2f}"


def _config_path() -> Path:
    override = os.environ.get("AUTHEN_CONFIG")
    if override:
        return Path(override)
    local = REPO_ROOT / "config" / "node.local.toml"
    if local.exists():
        return local
    return REPO_ROOT / "config" / "node.toml"


def load_config(path: Path | None = None) -> NodeConfig:
    cfg_path = path or _config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"No node config at {cfg_path}. Copy config/node.example.toml to "
            "config/node.toml and fill it in."
        )
    with cfg_path.open("rb") as fh:
        raw = tomllib.load(fh)

    # Env wins, so one image can serve both networks without a config edit.
    net_name = os.environ.get("AUTHEN_NETWORK") or raw["node"]["network"]
    if net_name not in raw.get("networks", {}):
        raise ValueError(
            f"Unknown network {net_name!r}. Known: {sorted(raw.get('networks', {}))}"
        )
    n = raw["networks"][net_name]
    network = NetworkProfile(
        name=net_name,
        caip2=n["caip2"],
        slug=n["slug"],
        usdc_asa=str(n["usdc_asa"]),
        usdc_decimals=int(n["usdc_decimals"]),
        algod_url=n["algod_url"],
        fee_payer=n["fee_payer"],
    )

    pay_to = (raw.get("treasury", {}).get(net_name) or "").strip()
    if not pay_to:
        raise ValueError(
            f"[treasury].{net_name} is empty in {cfg_path}.\n"
            "This is the payTo address — the leaderboard's aggregation unit and "
            "permanent for the competition. It must never be a placeholder."
        )

    x = raw.get("x402", {})
    return NodeConfig(
        raw=raw,
        paths=Paths(data_dir=resolve_data_dir()),
        network=network,
        pay_to=pay_to,
        public_url=raw["node"]["public_url"].rstrip("/"),
        node_name=raw["node"].get("name", "Pintheon"),
        facilitator_url=raw["facilitator"]["url"].rstrip("/"),
        tag=x.get("tag", "x402-global-challenge"),
        scheme=x.get("scheme", "exact"),
        max_timeout_seconds=int(x.get("max_timeout_seconds", 120)),
        price_micro_usdc=str(raw["pricing"]["notarize_micro_usdc"]),
        c2pa_micro_usdc=str(raw["pricing"].get("c2pa_micro_usdc", raw["pricing"]["notarize_micro_usdc"])),
        identity_pubkey=(raw.get("identity", {}).get("public_key") or "").strip(),
    )
