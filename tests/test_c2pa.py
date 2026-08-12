"""C2PA manifest signing.

The certificate profile here was determined empirically against c2pa-python
0.37.6, not read off a spec, and the failure mode is a single opaque message
("the certificate is invalid") for every distinct mistake. These tests pin the
working shape so a dependency bump that breaks it fails here rather than in
production, where the only symptom is a paid endpoint returning 400.
"""

from __future__ import annotations

import io

import pytest

from authen.c2pa_sign import (
    HVYM_CLAIM_SIGNING_OID,
    SUPPORTED_FORMATS,
    build_app_ca,
    issue_leaf,
    read_manifest,
    sign_image,
)
from authen.keys import load_or_create
from authen.notary import build_attestation, sha256_hex, verify_attestation

pytest.importorskip("PIL", reason="Pillow needed to synthesise a test image")


@pytest.fixture(scope="module")
def ident(tmp_path_factory):
    return load_or_create(tmp_path_factory.mktemp("c2pakeys"))


@pytest.fixture(scope="module")
def ca(ident):
    return build_app_ca(ident)


@pytest.fixture(scope="module")
def jpeg() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 90, 160)).save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- certificates


def test_ca_binds_the_node_identity(ident, ca):
    """The CA must be over the node key, not a fresh one.

    Same key signs attestations and is published at /api/v1/identity, so a
    verifier has one thing to check rather than two.
    """
    from cryptography.hazmat.primitives import serialization

    assert ca.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ) == ident.public_bytes


def test_ca_carries_both_chain_addresses(ident, ca):
    """SANs must name the identity on both ledgers, so a registry move is free."""
    from cryptography import x509

    sans = [
        u.value
        for u in ca.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
    ]
    assert f"stellar:{ident.stellar}" in sans
    assert f"algorand:{ident.algorand}" in sans


def test_ca_is_constrained(ca):
    from cryptography import x509

    bc = ca.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is True
    assert bc.path_length == 0, "a CA that can mint sub-CAs is a bigger blast radius"


def test_leaf_cannot_issue(ident, ca):
    from cryptography import x509

    chain = issue_leaf(ident, ca)
    bc = chain.leaf_cert.extensions.get_extension_for_class(
        x509.BasicConstraints
    ).value
    assert bc.ca is False


def test_leaf_carries_email_protection_eku(ident, ca):
    """c2pa-rs rejects the chain without it — with an unrelated error message.

    The private OID alone produces "the certificate is invalid", which reads like
    a signing problem rather than a policy one. Hours were lost to this.
    """
    from cryptography import x509
    from cryptography.x509.oid import ExtendedKeyUsageOID

    chain = issue_leaf(ident, ca)
    ekus = list(
        chain.leaf_cert.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        ).value
    )
    assert ExtendedKeyUsageOID.EMAIL_PROTECTION in ekus
    assert HVYM_CLAIM_SIGNING_OID in ekus


def test_chain_has_key_identifiers(ident, ca):
    """Also required by c2pa-rs, also reported as "the certificate is invalid"."""
    from cryptography import x509

    chain = issue_leaf(ident, ca)
    ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
    chain.leaf_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
    chain.leaf_cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)


def test_pem_chain_is_leaf_first(ident, ca):
    chain = issue_leaf(ident, ca)
    assert chain.pem_chain.count("BEGIN CERTIFICATE") == 2
    first = chain.pem_chain.split("-----END CERTIFICATE-----")[0]
    from cryptography.x509 import load_pem_x509_certificate

    parsed = load_pem_x509_certificate((first + "-----END CERTIFICATE-----").encode())
    assert parsed.serial_number == chain.leaf_cert.serial_number


# ---------------------------------------------------------------- signing


def test_signs_and_reads_back(ident, ca, jpeg):
    signed, chain = sign_image(ident, ca, jpeg, "image/jpeg")
    assert len(signed) > len(jpeg)

    out = read_manifest(signed, "image/jpeg")
    assert out["embedded"] is True
    assert out["validationState"] == "Valid", (
        "manifest must validate structurally; only the trust-list check may fail"
    )
    assert chain.ca_fingerprint


def test_only_failure_is_the_trust_list(ident, ca, jpeg):
    """Every cryptographic check must pass. The signer being untrusted is expected
    and documented — Authen's CA is not on the C2PA conformance list — but any
    OTHER failure means we are shipping a broken manifest."""
    signed, _ = sign_image(ident, ca, jpeg, "image/jpeg")
    results = read_manifest(signed, "image/jpeg")["validationResults"] or {}

    codes: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "failure" and isinstance(v, list):
                    codes.extend(e.get("code", "") for e in v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(results)
    assert set(codes) <= {"signingCredential.untrusted"}, f"unexpected failures: {codes}"


def test_does_not_claim_the_content_was_captured(ident, ca, jpeg):
    """The library's CREATE default stamps digitalSourceType=digitalCapture.

    That asserts this node photographed the subject. It did not. Shipping it
    would put a false statement inside a signed manifest.
    """
    signed, _ = sign_image(ident, ca, jpeg, "image/jpeg")
    blob = str(read_manifest(signed, "image/jpeg")["manifest"]).lower()

    assert "digitalcapture" not in blob, (
        "manifest claims this node captured the image. It received bytes over HTTP. "
        "Check that sign_image still passes C2paDigitalSourceType.EMPTY."
    )
    # `.../digitalsourcetype/empty` is the explicit no-claim value and is correct —
    # the point is that SOME source type is asserted and it must be the empty one.
    assert "digitalsourcetype/empty" in blob


def test_attestation_rides_inside_the_manifest(ident, ca, jpeg):
    """The embedded token must verify on its own, and bind to this image."""
    att = build_attestation(
        ident, digest_hex=sha256_hex(jpeg), size=len(jpeg), media_type="image/jpeg"
    )
    signed, _ = sign_image(ident, ca, jpeg, "image/jpeg", attestation=att.to_dict())

    manifest = read_manifest(signed, "image/jpeg")["manifest"]
    active = list(manifest["manifests"].values())[0]
    labels = [a["label"] for a in active.get("assertions", [])]
    assert "com.heavymeta.authen.attestation" in labels

    embedded = next(
        a for a in active["assertions"] if a["label"] == "com.heavymeta.authen.attestation"
    )
    ok, payload, err = verify_attestation(embedded["data"]["attestation"])
    assert ok, err
    assert payload["h"] == sha256_hex(jpeg)


def test_rejects_unsupported_media_type(ident, ca, jpeg):
    with pytest.raises(ValueError, match="unsupported media type"):
        sign_image(ident, ca, jpeg, "application/pdf")


@pytest.mark.parametrize("mt", sorted(SUPPORTED_FORMATS))
def test_supported_formats_have_extensions(mt):
    assert SUPPORTED_FORMATS[mt].startswith(".")
