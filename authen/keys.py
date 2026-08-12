"""Node identity — one Ed25519 keypair, self-generated on first boot.

Ported from Pintheon V1's mechanism, not reinvented: V1 self-generates its Fernet
`master_key` into `pintheon.ini` on first start rather than receiving it from the
environment (`pintheonMachine/__init__.py:91-96`). The same rule holds here — a
fresh node needs no provisioning step, and no secret is ever injected by env in
normal operation.

Why Ed25519 specifically: the same 32-byte public key is simultaneously a Stellar
address (strkey `G...`) and an Algorand address (base32+checksum). One key, both
ledgers, no derivation. That is what lets an attestation signed here be verified
against an on-chain trust registry on either chain.

The seed is written 0600 and never logged, transmitted, or serialised into an
attestation. Only the public key leaves this module.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SEED_FILENAME = "node_seed.bin"


def _b32_nopad(b: bytes) -> str:
    return base64.b32encode(b).decode("ascii").rstrip("=")


def algorand_address(pub32: bytes) -> str:
    """Algorand address: base32(pubkey || last 4 bytes of sha512/256(pubkey))."""
    import hashlib

    checksum = hashlib.new("sha512_256", pub32).digest()[-4:]
    return _b32_nopad(pub32 + checksum)


def stellar_address(pub32: bytes) -> str:
    """Stellar strkey: base32(version || pubkey || crc16-xmodem), version 6<<3."""
    payload = bytes([6 << 3]) + pub32
    crc = 0
    for byte in payload:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return _b32_nopad(payload + crc.to_bytes(2, "little"))


@dataclass(frozen=True)
class NodeIdentity:
    """This node's signing identity. `private` never leaves the process."""

    private: Ed25519PrivateKey
    public_bytes: bytes

    @property
    def public(self) -> Ed25519PublicKey:
        return self.private.public_key()

    @property
    def public_hex(self) -> str:
        return self.public_bytes.hex()

    @property
    def algorand(self) -> str:
        return algorand_address(self.public_bytes)

    @property
    def stellar(self) -> str:
        return stellar_address(self.public_bytes)

    def sign(self, message: bytes) -> bytes:
        return self.private.sign(message)


def load_or_create(data_dir: Path) -> NodeIdentity:
    """Load the node seed, generating it on first boot.

    Generation is the normal path for a new node, exactly as in V1. It is also the
    dangerous path for an *existing* node whose state volume failed to mount: a
    silently regenerated identity invalidates every attestation this node has ever
    issued, and looks like a clean first boot. Callers that have recorded a public
    key should assert it still matches — see `assert_identity` below.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    seed_file = data_dir / SEED_FILENAME

    if seed_file.exists():
        seed = seed_file.read_bytes()
        if len(seed) != 32:
            raise RuntimeError(
                f"Node seed at {seed_file} is {len(seed)} bytes, expected 32. "
                "Refusing to start rather than sign with a malformed key."
            )
        private = Ed25519PrivateKey.from_private_bytes(seed)
    else:
        private = Ed25519PrivateKey.generate()
        seed = private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        # Write 0600 before any content lands, so the seed is never briefly world
        # readable. os.open with the mode set at creation, not a later chmod.
        fd = os.open(seed_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, seed)
        finally:
            os.close(fd)

    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return NodeIdentity(private=private, public_bytes=public_bytes)


def assert_identity(identity: NodeIdentity, expected_hex: str | None) -> None:
    """Fail loudly if the node's key is not the one the operator recorded.

    The failure this catches is a Docker/systemd state volume that did not mount:
    the node generates a brand new identity, starts cleanly, serves happily, and
    every attestation it signs is unverifiable against the recorded public key.
    """
    if not expected_hex:
        return
    if identity.public_hex.lower() != expected_hex.strip().lower():
        raise RuntimeError(
            "Node identity does not match the configured public key.\n"
            f"  configured: {expected_hex}\n"
            f"  actual:     {identity.public_hex}\n"
            "This usually means the state directory did not mount and a fresh "
            "identity was generated. Refusing to start: attestations signed with "
            "this key would not verify."
        )
