"""Manifest attestations — Merkle roots, inclusion proofs, and the two routes.

What makes this module worth its length is that every failure here is silent. A
wrong tree construction still produces a valid signature over a plausible root; a
proof that verifies against the wrong manifest still returns 200. Nothing on the
wire looks wrong until someone tries to use an attestation as evidence and it does
not hold up — by which point every manifest ever issued is suspect.

Three groups of invariant:

  * **construction** — the tree must not be malleable, and two implementations in
    two repos must agree byte for byte (`vectors_manifest.json`)
  * **claim discipline** — a manifest attestation claims something different from a
    notary attestation, and the two must not be interchangeable
  * **privacy** — an inclusion proof must disclose nothing about the other items,
    which is the property this route is actually sold on
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from authen.manifest import (
    LEAF_PREFIX,
    MANIFEST_STATEMENT,
    NODE_PREFIX,
    CanonicalisationError,
    canonical_record,
    inclusion_proof,
    leaf_hash,
    merkle_root,
    node_hash,
    verify_inclusion,
)
from authen.notary import build_attestation, build_manifest_attestation, verify_attestation

VECTORS = Path(__file__).parent / "vectors_manifest.json"


def rec(n: int) -> dict:
    """A minimal item record. Numbers are strings on purpose — see authen/manifest.py."""
    return {"id": f"item-{n:04d}", "object": {"type": "bar", "mass_g": f"{n}.000"}}


# ---------------------------------------------------------------------------
# Cross-implementation agreement
# ---------------------------------------------------------------------------


def test_vectors_reproduce_exactly():
    """The Pyx capture app builds these trees; this node verifies them.

    The vectors are generated from the Pyx reference implementation and committed
    to both repos. If this fails after a change here, the change is a wire-format
    break — every manifest attestation already issued would stop verifying, and the
    Android shell would start producing roots this node rejects.
    """
    v = json.loads(VECTORS.read_text(encoding="utf-8"))

    for case in v["canonical"]:
        assert canonical_record(case["record"]).decode("utf-8") == case["canonical"], case["name"]

    for case in v["roots"]:
        assert merkle_root(case["records"]) == case["root"], case["name"]

    for case in v["proofs"]:
        path = inclusion_proof(case["records"], case["index"])
        assert path == case["proof"]["path"], case["name"]
        ok, why = verify_inclusion(case["records"][case["index"]], path, case["root"])
        assert ok, f"{case['name']}: {why}"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_canonical_is_key_order_independent():
    a = {"b": "2", "a": "1", "c": {"y": "1", "x": "2"}}
    b = {"c": {"x": "2", "y": "1"}, "a": "1", "b": "2"}
    assert canonical_record(a) == canonical_record(b)


def test_canonical_emits_utf8_not_escapes():
    """A maker or mint name may be non-ASCII; JCS requires real UTF-8."""
    out = canonical_record({"maker": "Métaux Précieux"})
    assert "Métaux".encode() in out
    assert b"\\u" not in out


def test_floats_are_rejected():
    """The cross-language divergence the schema exists to avoid."""
    with pytest.raises(CanonicalisationError) as e:
        canonical_record({"mass_g": 31.103})
    assert "string" in str(e.value)


def test_domain_separation_between_leaves_and_nodes():
    """An internal node must not be replayable as a record.

    Sharing the hash construction is the classic second-preimage hole: an attacker
    presents an internal node as a record and proves membership of an item that was
    never in the manifest.
    """
    import hashlib

    a, b = leaf_hash(rec(1)), leaf_hash(rec(2))
    assert node_hash(a, b) != hashlib.sha256(a + b).digest()
    blob = a + b
    assert (
        hashlib.sha256(LEAF_PREFIX + blob).digest()
        != hashlib.sha256(NODE_PREFIX + blob).digest()
    )


def test_odd_node_is_promoted_not_duplicated():
    """CVE-2012-2459: duplicating the last node lets distinct trees share a root.

    If [a,b,c] and [a,b,c,c] produced the same root, an inclusion proof would stop
    being evidence about WHICH manifest an item came from — which is the entire
    claim the paid route sells.
    """
    assert merkle_root([rec(1), rec(2), rec(3)]) != merkle_root(
        [rec(1), rec(2), rec(3), rec(3)]
    )


@pytest.mark.parametrize("n", [1, 2, 3, 5, 7, 8, 9, 17])
def test_every_item_proves(n):
    """Odd sizes are where promotion logic goes wrong, so cover them all."""
    records = [rec(i) for i in range(n)]
    root = merkle_root(records)
    for i in range(n):
        ok, why = verify_inclusion(records[i], inclusion_proof(records, i), root)
        assert ok, f"item {i} of {n}: {why}"


def test_tampered_item_fails():
    records = [rec(i) for i in range(8)]
    root = merkle_root(records)
    proof = inclusion_proof(records, 3)
    tampered = {**records[3], "object": {"type": "bar", "mass_g": "999.000"}}
    ok, why = verify_inclusion(tampered, proof, root)
    assert not ok and why


def test_proof_from_another_manifest_fails():
    a = [rec(i) for i in range(8)]
    b = [rec(i) for i in range(100, 108)]
    ok, _ = verify_inclusion(a[0], inclusion_proof(a, 0), merkle_root(b))
    assert not ok


def test_proof_discloses_nothing_about_other_items():
    """The privacy property the route is sold on, asserted rather than assumed.

    A client proving one disputed bar must not thereby publish the rest of their
    holdings. A proof carries sibling digests only.
    """
    records = [rec(i) for i in range(8)]
    blob = json.dumps(inclusion_proof(records, 0))
    for other in records[1:]:
        assert other["id"] not in blob
        assert other["object"]["mass_g"] not in blob


def test_absurd_proof_paths_are_refused_not_processed():
    """A malicious proof must not make verification arbitrarily expensive."""
    ok, why = verify_inclusion(rec(1), [{"side": "L", "hash": "aa" * 32}] * 200, "bb" * 32)
    assert not ok and "long" in why


# ---------------------------------------------------------------------------
# Claim discipline
# ---------------------------------------------------------------------------


def test_manifest_attestation_claims_a_root_not_bytes(identity):
    """The intent field must distinguish the two claims.

    A notary attestation says the node saw bytes. A manifest attestation says the
    node saw a root someone else computed — it never sees the items. Collapsing
    those would let "root observed" be read as "goods inspected", which is a far
    larger claim than this node can support.
    """
    att = build_manifest_attestation(identity, root_hex="ab" * 32, count=42)
    assert att.payload["i"] == MANIFEST_STATEMENT == "merkle-root-observed-at"
    assert att.payload["i"] != build_attestation(
        identity, digest_hex="ab" * 32, size=1
    ).payload["i"]


def test_manifest_attestation_verifies_and_is_canonical(identity):
    att = build_manifest_attestation(identity, root_hex="cd" * 32, count=7, label="lot 3")
    ok, payload, err = verify_attestation(att.wire)
    assert ok, err
    assert payload["h"] == "cd" * 32
    assert payload["n"] == 7
    assert payload["l"] == "lot 3"


def test_manifest_attestation_rejects_bad_roots(identity):
    for bad in ["", "abc", "z" * 64, "ab" * 31]:
        with pytest.raises(ValueError):
            build_manifest_attestation(identity, root_hex=bad, count=1)


def test_manifest_attestation_rejects_empty_manifests(identity):
    with pytest.raises(ValueError):
        build_manifest_attestation(identity, root_hex="ab" * 32, count=0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_manifest_route_is_paid(client):
    """There must be no unpaid path to a manifest attestation."""
    r = client.post("/api/v1/manifest", json={"root": "ab" * 32, "n": 1})
    assert r.status_code == 402


def test_verify_route_is_free_and_checks_the_signature(client, identity):
    att = build_manifest_attestation(identity, root_hex="ab" * 32, count=3)
    r = client.post("/api/v1/manifest/verify", json={"attestation": att.wire})
    assert r.status_code == 200
    body = r.get_json()
    assert body["valid"] is True
    assert body["root"] == "ab" * 32
    assert body["declaredItemCount"] == 3
    assert body["knownKey"] is True
    assert body["itemVerified"] is False, "no item was supplied, so none was verified"


def test_verify_route_refuses_a_notary_attestation(client, identity):
    """A valid signature over a different claim must not pass here.

    Both are correctly signed by this node, so a verifier that only checks the
    signature would accept "these bytes were seen" as "this manifest root was
    signed" — quietly upgrading the claim.
    """
    notary = build_attestation(identity, digest_hex="ab" * 32, size=10)
    r = client.post("/api/v1/manifest/verify", json={"attestation": notary.wire})
    assert r.status_code == 400
    assert "not a manifest attestation" in r.get_json()["error"]


def test_verify_route_proves_one_item_without_the_others(client, identity):
    """The end-to-end version of the property that sells the route."""
    records = [rec(i) for i in range(8)]
    root = merkle_root(records)
    att = build_manifest_attestation(identity, root_hex=root, count=len(records))

    r = client.post(
        "/api/v1/manifest/verify",
        json={
            "attestation": att.wire,
            "item": records[5],
            "proof": {"index": 5, "path": inclusion_proof(records, 5)},
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["valid"] is True and body["itemVerified"] is True
    assert body["itemIndex"] == 5

    # Nothing about the other seven items reached the node or came back.
    blob = json.dumps(body)
    for other in records[:5] + records[6:]:
        assert other["id"] not in blob


def test_verify_route_rejects_a_tampered_item(client, identity):
    records = [rec(i) for i in range(8)]
    att = build_manifest_attestation(identity, root_hex=merkle_root(records), count=8)
    r = client.post(
        "/api/v1/manifest/verify",
        json={
            "attestation": att.wire,
            "item": {**records[2], "object": {"type": "bar", "mass_g": "999.000"}},
            "proof": {"index": 2, "path": inclusion_proof(records, 2)},
        },
    )
    assert r.status_code == 400
    assert r.get_json()["itemVerified"] is False


def test_verify_route_requires_both_item_and_proof(client, identity):
    att = build_manifest_attestation(identity, root_hex="ab" * 32, count=1)
    r = client.post(
        "/api/v1/manifest/verify", json={"attestation": att.wire, "item": rec(1)}
    )
    assert r.status_code == 400
    assert "proof" in r.get_json()["error"]


def test_verify_route_rejects_junk(client):
    for body in [{}, {"attestation": ""}, {"attestation": "not.a.token"}]:
        r = client.post("/api/v1/manifest/verify", json=body)
        assert r.status_code == 400
        assert r.get_json()["valid"] is False
