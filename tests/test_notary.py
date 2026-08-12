"""Attestation signing and verification.

These are the claims the product rests on. If signing is wrong, every attestation
Authen ever issued is worthless — and unlike a serving bug, nobody finds out until
someone tries to verify one.

No network required.
"""

from __future__ import annotations

import json

import pytest

from authen.keys import algorand_address, load_or_create, stellar_address
from authen.notary import (
    b64u,
    b64u_decode,
    build_attestation,
    canonical,
    sha256_hex,
    verify_attestation,
)


@pytest.fixture(scope="module")
def ident(tmp_path_factory):
    return load_or_create(tmp_path_factory.mktemp("keys"))


def test_roundtrip(ident):
    att = build_attestation(ident, digest_hex=sha256_hex(b"hello"), size=5)
    ok, payload, err = verify_attestation(att.wire)
    assert ok, err
    assert payload["h"] == sha256_hex(b"hello")
    assert payload["k"] == ident.public_hex
    assert payload["s"] == 5


def test_signature_is_over_canonical_bytes(ident):
    """Re-encoding the payload must reproduce exactly what was signed."""
    att = build_attestation(ident, digest_hex=sha256_hex(b"x"), size=1)
    _sig, payload_part = att.wire.split(".", 1)
    assert canonical(att.payload) == b64u_decode(payload_part)


def test_tampering_with_the_digest_is_caught(ident):
    att = build_attestation(ident, digest_hex=sha256_hex(b"real"), size=4)
    forged = dict(att.payload)
    forged["h"] = sha256_hex(b"fake")
    wire = f"{b64u(att.signature)}.{b64u(canonical(forged))}"
    ok, _p, err = verify_attestation(wire)
    assert not ok and err


def test_non_canonical_payload_is_rejected(ident):
    """A forger must not be able to smuggle a payload past a lenient parser.

    Same keys, different byte encoding (spaces). If we verified the *parsed*
    object rather than the exact signed bytes, this would slip through.
    """
    att = build_attestation(ident, digest_hex=sha256_hex(b"y"), size=1)
    loose = json.dumps(att.payload, sort_keys=True).encode()  # default separators
    assert loose != canonical(att.payload)
    ok, _p, err = verify_attestation(f"{b64u(att.signature)}.{b64u(loose)}")
    assert not ok
    assert "canonical" in (err or "")


def test_swapped_key_is_rejected(ident, tmp_path):
    """Claiming a different signer must not verify."""
    other = load_or_create(tmp_path / "other")
    att = build_attestation(ident, digest_hex=sha256_hex(b"z"), size=1)
    forged = dict(att.payload)
    forged["k"] = other.public_hex
    ok, _p, _err = verify_attestation(f"{b64u(att.signature)}.{b64u(canonical(forged))}")
    assert not ok


@pytest.mark.parametrize("junk", ["", "nodot", "a.b.c.d", "!!!.???", "."])
def test_malformed_input_fails_closed(junk):
    ok, _p, err = verify_attestation(junk)
    assert not ok and err


def test_attestation_claims_only_observation(ident):
    """The intent field is the claim. Widening it is a product decision, not a typo."""
    att = build_attestation(ident, digest_hex=sha256_hex(b"q"), size=1)
    assert att.payload["i"] == "sha256-observed-at"


def test_digest_must_be_sha256_shaped(ident):
    with pytest.raises(ValueError):
        build_attestation(ident, digest_hex="tooshort", size=1)


# ---------------------------------------------------------------- identity


def test_identity_persists_across_loads(tmp_path):
    """A regenerated key invalidates every attestation already issued."""
    first = load_or_create(tmp_path)
    second = load_or_create(tmp_path)
    assert first.public_hex == second.public_hex


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="Windows uses ACLs, not POSIX mode bits; os.open's mode arg is ignored there. "
    "The node runs on Linux, where this check is meaningful.",
)
def test_seed_is_not_world_readable(tmp_path):
    import os
    import stat

    load_or_create(tmp_path)
    mode = os.stat(tmp_path / "node_seed.bin").st_mode
    assert not (mode & stat.S_IROTH), "node seed must not be world readable"


def test_one_key_is_both_addresses(ident):
    """Stellar and Algorand encode the same 32 bytes differently.

    This is what allows a single signing identity to be registered in a trust
    registry on either chain without re-keying.
    """
    assert stellar_address(ident.public_bytes).startswith("G")
    assert len(stellar_address(ident.public_bytes)) == 56
    assert len(algorand_address(ident.public_bytes)) == 58


def test_identity_mismatch_refuses_to_start(ident):
    from authen.keys import assert_identity

    assert_identity(ident, None)                 # unset: allowed
    assert_identity(ident, ident.public_hex)     # matching: allowed
    with pytest.raises(RuntimeError, match="does not match"):
        assert_identity(ident, "00" * 32)


def test_truncated_seed_refuses_to_start(tmp_path):
    """A malformed seed must fail loudly, never be silently replaced.

    Regenerating here would mint a new identity and invalidate every attestation
    already issued — the exact failure assert_identity exists to catch.
    """
    load_or_create(tmp_path)
    (tmp_path / "node_seed.bin").write_bytes(b"\x00" * 8)
    with pytest.raises(RuntimeError, match="expected 32"):
        load_or_create(tmp_path)


def test_seed_write_leaves_no_temp_file(tmp_path):
    """The atomic write must clean up after itself."""
    load_or_create(tmp_path)
    assert (tmp_path / "node_seed.bin").exists()
    assert not list(tmp_path.glob("*.tmp")), "temp file left behind"


def test_seed_survives_newline_bytes(tmp_path):
    """Regression: os.open without O_BINARY corrupts seeds on Windows.

    Text mode expands 0x0A to 0x0D 0x0A, so the seed reads back 33+ bytes and the
    node refuses to start. ~12% of random 32-byte seeds contain a 0x0A, which made
    this look like flaky tests rather than a platform bug.
    """
    import os
    from unittest.mock import patch

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    # A seed guaranteed to contain newline and carriage-return bytes.
    hostile = bytes([0x0A, 0x0D, 0x1A, 0x0A] + [0x41] * 28)
    key = Ed25519PrivateKey.from_private_bytes(hostile)
    with patch("authen.keys.Ed25519PrivateKey.generate", return_value=key):
        created = load_or_create(tmp_path)

    assert (tmp_path / "node_seed.bin").stat().st_size == 32
    assert load_or_create(tmp_path).public_hex == created.public_hex
