"""Merkle manifests — one signature over a set of items, one proof per item.

A manifest is a set of item records — bullion bars, artworks, anything with
photographs and identifying marks — combined into a single Merkle root that this
node signs. Any one item can then be proven to have been in the signed set without
disclosing the others.

That last property is the whole point rather than a nicety. A client proving one
disputed bar was in a consignment must not thereby publish the rest of their
holdings, and a manifest that has to be shown whole to be shown at all is one many
clients will simply refuse to create.

**This node never sees the items.** The paid route receives a root and a count; the
records, the photographs and the tree stay with the caller. So the node cannot learn
what was in a manifest, which is a real privacy property and also why the route is
cheap to operate — it hashes nothing and stores nothing, consistent with the rule
that no third-party bytes are persisted.

Cross-implementation agreement
------------------------------
The Android shell builds these trees and this node verifies them. Two independent
implementations will not agree by accident, so `tests/vectors_manifest.json` pins
every construction detail and both sides are tested against it. A change here that
alters any vector is a wire-format break: every manifest attestation already issued
would stop verifying.

Construction, and why each choice
---------------------------------
    leaf(record) = SHA256( 0x00 || JCS(record) )
    node(l, r)   = SHA256( 0x01 || l || r )
    odd node     = promoted unchanged to the next level

**Domain separation** (`0x00` / `0x01`): without distinct prefixes an internal node
digest can be replayed as a record, opening second-preimage attacks — an attacker
proves membership of something never in the tree.

**Promote, never duplicate** the odd node. Bitcoin duplicates the last hash of an
odd level and inherited CVE-2012-2459 for it: two distinct trees produce the same
root, so a proof stops being evidence about *which* manifest it came from. That is
precisely the claim this product rests on.

**Numbers are strings** in records. RFC 8785 canonicalisation requires ES6 number
serialisation, and float formatting is where cross-language digests diverge in
practice. The schema forbids JSON floats: a mass is `"31.103"`. Integers are
permitted only for exact counts. This matches the convention x402 already uses for
`amount` and `asset`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"

#: What a manifest attestation claims. Deliberately distinct from the notary's
#: `sha256-observed-at`: this one is about a root, not about bytes the node saw.
MANIFEST_STATEMENT = "merkle-root-observed-at"

#: Bound on items per manifest. Not a technical limit — the node only ever sees a
#: root — but a declared count that cannot be checked should not be unbounded.
MAX_ITEMS = 100_000


class CanonicalisationError(ValueError):
    """A record cannot be canonicalised identically across implementations."""


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------


def _check_canonicalisable(value: Any, path: str = "$") -> None:
    """Reject anything whose canonical form is not identical across languages.

    Floats are the important rejection. Python's repr, JavaScript's Number
    serialisation and Kotlin's Double.toString do not agree in general, and a
    disagreement is invisible until the two sides produce different digests for the
    same record — at which point every affected attestation is unverifiable by the
    other side.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        raise CanonicalisationError(
            f"{path}: float {value!r} is not permitted. Values with fractional parts "
            'must be strings ("31.103"): ES6 number serialisation does not reproduce '
            "identically across languages."
        )
    if isinstance(value, int):
        if not (-(2**53) < value < 2**53):
            raise CanonicalisationError(
                f"{path}: integer {value} is outside the exactly-representable "
                "range; express it as a string."
            )
        return
    if value is None or isinstance(value, str):
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_canonicalisable(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise CanonicalisationError(f"{path}: object key {k!r} is not a string")
            _check_canonicalisable(v, f"{path}.{k}")
        return
    raise CanonicalisationError(f"{path}: type {type(value).__name__} is not JSON")


def canonical_record(record: Any) -> bytes:
    """RFC 8785-style canonical JSON, restricted to the types this schema allows.

    Keys sorted, no insignificant whitespace, UTF-8 output, no `\\u` escaping.

    Ordering caveat: RFC 8785 sorts by UTF-16 code unit and Python sorts by code
    point. These coincide for everything in the Basic Multilingual Plane, which
    covers every field name in this schema — but a future schema with astral-plane
    keys would need explicit UTF-16 ordering.
    """
    _check_canonicalisable(record)
    return json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def leaf_hash(record: Any) -> bytes:
    return hashlib.sha256(LEAF_PREFIX + canonical_record(record)).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_inclusion(record: Any, proof: list[dict], root_hex: str) -> tuple[bool, str | None]:
    """Recompute a root from one record and its proof path.

    Returns `(ok, error)`. Deliberately small enough to reimplement from the
    published algorithm — nobody should have to call this node to check a manifest,
    which is the same reason attestation verification is free.
    """
    if not isinstance(root_hex, str) or len(root_hex) != 64:
        return False, "root must be 64 hex chars (sha256)"
    try:
        bytes.fromhex(root_hex)
    except ValueError:
        return False, "root is not hex"

    if not isinstance(proof, list):
        return False, "proof path must be a list"
    if len(proof) > 64:
        # A path longer than 64 implies more than 2^64 leaves. Bound it so a
        # malicious proof cannot make verification arbitrarily expensive.
        return False, "proof path is implausibly long"

    try:
        current = leaf_hash(record)
    except CanonicalisationError as exc:
        return False, f"item is not canonicalisable: {exc}"

    for i, step in enumerate(proof):
        if not isinstance(step, dict):
            return False, f"proof step {i} is not an object"
        side = step.get("side")
        raw = step.get("hash")
        if side not in ("L", "R"):
            return False, f"proof step {i}: side must be 'L' or 'R'"
        if not isinstance(raw, str) or len(raw) != 64:
            return False, f"proof step {i}: hash must be 64 hex chars"
        try:
            sibling = bytes.fromhex(raw)
        except ValueError:
            return False, f"proof step {i}: hash is not hex"

        current = node_hash(sibling, current) if side == "L" else node_hash(current, sibling)

    if current.hex() != root_hex.lower():
        return False, "item does not belong to this manifest root"
    return True, None


# ---------------------------------------------------------------------------
# Building — not used by the node, kept so the vectors can be regenerated
# ---------------------------------------------------------------------------


def build_levels(leaves: list[bytes]) -> list[list[bytes]]:
    """Every level of the tree, leaves first, root last."""
    if not leaves:
        raise ValueError("a manifest must contain at least one item")
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        current = levels[-1]
        nxt = [node_hash(current[i], current[i + 1]) for i in range(0, len(current) - 1, 2)]
        if len(current) % 2:
            nxt.append(current[-1])  # promote, never duplicate
        levels.append(nxt)
    return levels


def merkle_root(records: list[Any]) -> str:
    return build_levels([leaf_hash(r) for r in records])[-1][0].hex()


def inclusion_proof(records: list[Any], index: int) -> list[dict]:
    """The path proving `records[index]` is in the tree built from `records`."""
    if not 0 <= index < len(records):
        raise IndexError(f"index {index} outside 0..{len(records) - 1}")
    levels = build_levels([leaf_hash(r) for r in records])
    path: list[dict] = []
    i = index
    for level in levels[:-1]:
        if i % 2:
            path.append({"side": "L", "hash": level[i - 1].hex()})
        elif i + 1 < len(level):
            path.append({"side": "R", "hash": level[i + 1].hex()})
        # else: promoted node — no sibling at this level, so no step to record
        i //= 2
    return path
