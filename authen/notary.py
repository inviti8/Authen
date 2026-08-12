"""Notarization — hash a thing, stamp it, sign it.

The attestation says exactly one thing, and it is worth being precise about what:

    "At time T, this node saw bytes whose SHA-256 is H."

It does NOT say the content is true, that the submitter owns it, or that it
existed before T. Overclaiming here is how provenance products lose credibility,
so the wording in `STATEMENT` is deliberately narrow and is carried in the
attestation itself.

Wire format is `b64url(sig).b64url(payload)` over sorted-keys compact JSON — the
same shape used by `inkternity_tokens.py`, `cert_registry_tokens.py` and
infinipaint's `TokenVerifier`. Reusing it means anything in the estate that can
already verify one of those tokens can verify these with a schema change and no
new parsing.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .keys import NodeIdentity

STATEMENT = "sha256-observed-at"
ATTESTATION_VERSION = 1


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical(payload: dict) -> bytes:
    """Sorted-keys compact JSON, UTF-8. Locked - see mock_c2pa/canonical.py."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Attestation:
    payload: dict
    signature: bytes
    wire: str

    def to_dict(self) -> dict:
        return {
            "attestation": self.wire,
            "payload": self.payload,
            "signature": self.signature.hex(),
        }


def build_attestation(
    identity: NodeIdentity,
    *,
    digest_hex: str,
    size: int,
    media_type: str | None = None,
    cid: str | None = None,
    observed_at: int | None = None,
) -> Attestation:
    """Sign a statement about one blob.

    Field names are single letters to match the estate's canonical-payload style
    (`a`, `m`, `fp`, `i`, `n` in cert_registry_canonical). Integers are JSON
    numbers, hashes lowercase hex - both locked conventions.
    """
    if len(digest_hex) != 64:
        raise ValueError("digest must be 64 hex chars (sha256)")

    payload: dict = {
        "h": digest_hex.lower(),          # sha256 of the bytes
        "i": STATEMENT,                   # intent - what this attestation claims
        "k": identity.public_hex,         # signing key, raw ed25519 hex
        "s": int(size),                   # byte length observed
        "t": int(observed_at if observed_at is not None else _now()),
        "v": ATTESTATION_VERSION,
    }
    if media_type:
        payload["m"] = media_type
    if cid:
        payload["c"] = cid

    blob = canonical(payload)
    signature = identity.sign(blob)
    return Attestation(
        payload=payload,
        signature=signature,
        wire=f"{b64u(signature)}.{b64u(blob)}",
    )


def verify_attestation(wire: str) -> tuple[bool, dict | None, str | None]:
    """Verify a wire attestation against the key it names.

    Self-describing on purpose: the payload carries the signing key, so a verifier
    needs nothing but the token. That check proves internal consistency, NOT that
    the key is trustworthy - deciding whether to trust `k` is the trust-registry
    question and is deliberately a separate step.

    Returns (ok, payload, error).
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        sig_part, payload_part = wire.split(".", 1)
    except ValueError:
        return False, None, "malformed: expected '<sig>.<payload>'"

    try:
        signature = b64u_decode(sig_part)
        blob = b64u_decode(payload_part)
    except Exception:
        return False, None, "malformed: base64url decode failed"

    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return False, None, "malformed: payload is not JSON"

    if canonical(payload) != blob:
        # Re-encoding must reproduce the exact signed bytes, or a forger could
        # reorder keys or add whitespace to smuggle a different payload past a
        # verifier that only checks the parsed object.
        return False, payload, "payload is not canonical (key order or spacing)"

    key_hex = payload.get("k")
    if not isinstance(key_hex, str) or len(key_hex) != 64:
        return False, payload, "payload has no usable signing key"

    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
        pub.verify(signature, blob)
    except (InvalidSignature, ValueError):
        return False, payload, "signature does not verify"

    return True, payload, None


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())
