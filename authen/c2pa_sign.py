"""C2PA manifest signing — Content Credentials for images.

Ported from `pintheon_contracts/mock_c2pa/andromica/ca_generation.py`, which is the
locked reference for the certificate shape and is exercised by that repo's
`run_mock_flow --mode dry-run`. Port, don't reinvent: the extension set below is
what the C2PA spec requires and what the `hvym-cert-registry` contract fingerprints.

Two-tier, deliberately:

  app CA   long-lived, self-signed Ed25519, holds the node identity key. Its
           fingerprint is what a trust registry records.
  leaf     issued per signing run, short-lived. Compromise of a leaf expires on
           its own rather than requiring the CA to be revoked.

The node's private key never leaves the process: `c2pa.Signer.from_callback` takes
a signing function, so the c2pa native library is handed signatures, never a key.

HONEST LIMIT — read before making claims about this. Authen's CA is not on the
C2PA conformance trust list. A validator will confirm the manifest is structurally
valid and the signature is intact, and will report the signer as untrusted, because
"trusted" in C2PA means "on a list Authen is not on". What this buys is a
cryptographic, machine-checkable binding from image to signer, verifiable against
our own registry. Anything stronger is a claim we cannot support.
"""

from __future__ import annotations

import datetime
import io
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .keys import NodeIdentity

# Heavymeta's private claim-signing OID. The `hvym-cert-registry` contract
# fingerprints a CA carrying this, so the CA keeps it for registry compatibility.
HVYM_CLAIM_SIGNING_OID = x509.ObjectIdentifier("1.3.6.1.4.1.42038.1.5.0")

# What c2pa-rs actually accepts on the END-ENTITY cert. Determined empirically
# against c2pa-python 0.37.6, not read off a spec:
#
#   leaf EKU = emailProtection            -> accepted
#   leaf EKU = the private OID alone      -> "the certificate is invalid"
#   leaf EKU = emailProtection + private  -> accepted
#   CA EKU   = private OID, even critical -> accepted
#
# So the leaf carries both: emailProtection to satisfy the validator, the private
# OID to stay legible to our own tooling. Independently, SubjectKeyIdentifier on
# the CA and SubjectKeyIdentifier + AuthorityKeyIdentifier on the leaf are
# REQUIRED - without them every chain is rejected with the same opaque message.
LEAF_EKUS = [ExtendedKeyUsageOID.EMAIL_PROTECTION, HVYM_CLAIM_SIGNING_OID]

CA_VALID_DAYS = 3650
LEAF_VALID_DAYS = 30

SUPPORTED_FORMATS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tif",
    "image/avif": ".avif",
    "image/heic": ".heic",
}


@dataclass(frozen=True)
class CertChain:
    ca_cert: x509.Certificate
    leaf_cert: x509.Certificate
    leaf_key: Ed25519PrivateKey

    @property
    def pem_chain(self) -> str:
        """Leaf first, then issuer — the order c2pa expects."""
        return "".join(
            c.public_bytes(serialization.Encoding.PEM).decode()
            for c in (self.leaf_cert, self.ca_cert)
        )

    @property
    def ca_fingerprint(self) -> str:
        return self.ca_cert.fingerprint(hashes.SHA256()).hex()


def _name(cn: str, ou: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Heavymeta Cooperative"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou),
        ]
    )


def build_app_ca(identity: NodeIdentity, app_name: str = "Authen") -> x509.Certificate:
    """Self-signed CA over the node's own identity key.

    Uses the node key rather than a fresh one on purpose: the same Ed25519 key
    signs attestations, is published at /api/v1/identity, and is what a trust
    registry would record. One identity, one thing to verify.
    """
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    subject = _name(f"{app_name} Instance", "App Instances")

    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(identity.public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=CA_VALID_DAYS))
        # CA:TRUE, pathlen:0 — may issue leaves, but no further CAs.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        # Critical, so a non-C2PA verifier correctly refuses this cert.
        .add_extension(
            x509.ExtendedKeyUsage([HVYM_CLAIM_SIGNING_OID]), critical=True
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(identity.public), critical=False
        )
        # The Stellar URI is the registry lookup key. The same 32 bytes are also
        # an Algorand address, so this binding survives a registry chain move.
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.UniformResourceIdentifier(f"stellar:{identity.stellar}"),
                    x509.UniformResourceIdentifier(f"algorand:{identity.algorand}"),
                    x509.UniformResourceIdentifier(f"heavymeta:app/{app_name}"),
                ]
            ),
            critical=False,
        )
        .sign(private_key=identity.private, algorithm=None)  # Ed25519: no prehash
    )


def issue_leaf(identity: NodeIdentity, ca_cert: x509.Certificate) -> CertChain:
    """Issue a short-lived claim-signing leaf under the app CA."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    leaf_key = Ed25519PrivateKey.generate()

    leaf = (
        x509.CertificateBuilder()
        .subject_name(_name("Authen Claim Signer", "Claim Signers"))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=LEAF_VALID_DAYS))
        # End entity: must not be able to issue anything further.
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage(LEAF_EKUS), critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(identity.public),
            critical=False,
        )
        .sign(private_key=identity.private, algorithm=None)
    )
    return CertChain(ca_cert=ca_cert, leaf_cert=leaf, leaf_key=leaf_key)


def sign_image(
    identity: NodeIdentity,
    ca_cert: x509.Certificate,
    data: bytes,
    media_type: str,
    *,
    claim_generator: str = "Authen",
    attestation: dict | None = None,
) -> tuple[bytes, CertChain]:
    """Embed a signed C2PA manifest into an image. Returns (signed bytes, chain).

    Carries Authen's own attestation as a custom assertion, so the C2PA manifest
    and the standalone token say the same thing and either can be checked.

    ON THE `c2pa.created` ACTION — this is a modelling compromise, not an oversight.
    C2PA requires the first action to be `created` or `opened`. `opened` additionally
    requires an ingredient reference which c2pa-python 0.37.6 cannot satisfy from a
    stream (`UPDATE` intent raises "ingredient file not found" even with an ingredient
    attached). So the manifest says `c2pa.created`, which here means *this signed
    asset version was created by Authen* - not that Authen authored the depicted
    content.

    Two things keep that from becoming an overclaim:

      * `digitalSourceType` is left EMPTY. The library's default for CREATE is
        `digitalCapture`, which would assert Authen photographed the subject. It
        does not, and shipping that would be a lie in the manifest.
      * The embedded attestation states the narrow claim in words.

    Revisit if a later c2pa-python fixes the UPDATE path; `opened` is the honest
    action for a notary.
    """
    from c2pa import (
        Builder,
        C2paBuilderIntent,
        C2paDigitalSourceType,
        C2paSigningAlg,
        Signer,
    )

    if media_type not in SUPPORTED_FORMATS:
        raise ValueError(
            f"unsupported media type {media_type!r}; "
            f"supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
        )

    chain = issue_leaf(identity, ca_cert)

    manifest = {
        "claim_generator": claim_generator,
        "claim_generator_info": [{"name": claim_generator, "version": "1"}],
        "format": media_type,
        "title": "Authen-signed asset",
        "assertions": [
            {
                "label": "com.heavymeta.authen.attestation",
                "data": {
                    "statement": (
                        "Authen observed these bytes at the stated time and signed "
                        "them. This asserts observation and time only - not "
                        "authorship, ownership, or prior existence of the content."
                    ),
                    **(attestation or {}),
                },
            }
        ],
    }

    # The leaf's key signs the claim; the node key signed the leaf. Neither ever
    # crosses the FFI boundary - c2pa gets a callback and receives signatures.
    signer = Signer.from_callback(
        callback=lambda payload: chain.leaf_key.sign(payload),
        alg=C2paSigningAlg.ED25519,
        certs=chain.pem_chain,
    )
    try:
        builder = Builder(manifest)
        try:
            builder.set_intent(C2paBuilderIntent.CREATE, C2paDigitalSourceType.EMPTY)
            src, dst = io.BytesIO(data), io.BytesIO()
            builder.sign(signer, media_type, src, dst)
            return dst.getvalue(), chain
        finally:
            builder.close()
    finally:
        signer.close()


def read_manifest(data: bytes, media_type: str) -> dict:
    """Read and validate an embedded manifest. Free — verification always is."""
    from c2pa import Reader

    reader = Reader(media_type, stream=io.BytesIO(data))
    try:
        import json as _json

        return {
            "manifest": _json.loads(reader.json()),
            "validationState": reader.get_validation_state(),
            "validationResults": reader.get_validation_results(),
            "embedded": reader.is_embedded(),
        }
    finally:
        reader.close()
