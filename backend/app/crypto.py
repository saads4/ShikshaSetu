"""Cryptography: keyed hashes, AES-GCM vault, wrapped keys, ES256 signing, Merkle (RFC 6962)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import unicodedata
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings
from .core import canonical_json


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


# --- keyed hashes ------------------------------------------------------------
def _hmac_key(purpose: str) -> bytes:
    """Derive one sub-key per purpose from IDENTITY_HMAC_KEY so keys are domain separated."""
    root = get_settings().identity_hmac_key.encode()
    return hmac.new(root, b"purpose:" + purpose.encode(), hashlib.sha256).digest()


def keyed_hash(purpose: str, value: str) -> bytes:
    return hmac.new(_hmac_key(purpose), value.encode(), hashlib.sha256).digest()


def identity_key(apaar_or_token: str) -> bytes:
    return keyed_hash("identity", apaar_or_token)


def name_tokens(full_name: str) -> list[bytes]:
    """Blind-index tokens (DEC-22): HMAC of each normalised name token."""
    norm = unicodedata.normalize("NFKD", full_name).encode("ascii", "ignore").decode().lower()
    return sorted({keyed_hash("name-token", t) for t in norm.replace(".", " ").split() if len(t) >= 2})


# --- master key / vault --------------------------------------------------------
def master_key() -> bytes:
    raw = get_settings().master_key
    try:
        key = base64.b64decode(raw, validate=True)
        if len(key) == 32:
            return key
    except Exception:
        pass
    return hashlib.sha256(raw.encode()).digest()  # dev placeholder: derive 32 bytes


def wrap(plaintext: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(master_key()).encrypt(nonce, plaintext, b"wrap")


def unwrap(blob: bytes) -> bytes:
    blob = bytes(blob)
    return AESGCM(master_key()).decrypt(blob[:12], blob[12:], b"wrap")


def new_data_key() -> tuple[bytes, bytes]:
    key = AESGCM.generate_key(256)
    return key, wrap(key)


def encrypt(data_key: bytes, obj: Any, aad: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    return nonce, AESGCM(data_key).encrypt(nonce, canonical_json(obj), aad)


def decrypt(data_key: bytes, nonce: bytes, ct: bytes, aad: bytes) -> Any:
    return json.loads(AESGCM(data_key).decrypt(bytes(nonce), bytes(ct), aad))


# --- ES256 -----------------------------------------------------------------
def new_es256() -> tuple[bytes, dict]:
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    nums = priv.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig",
           "x": b64u(nums.x.to_bytes(32, "big")), "y": b64u(nums.y.to_bytes(32, "big"))}
    return pem, jwk


def sign_es256(private_pem: bytes, message: bytes) -> str:
    priv = serialization.load_pem_private_key(private_pem, password=None)
    der = priv.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def verify_es256(public_jwk: dict, message: bytes, signature_b64u: str) -> bool:
    try:
        sig = b64u_dec(signature_b64u)
        r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
        pub = ec.EllipticCurvePublicNumbers(
            int.from_bytes(b64u_dec(public_jwk["x"]), "big"), int.from_bytes(b64u_dec(public_jwk["y"]), "big"),
            ec.SECP256R1()).public_key()
        pub.verify(utils.encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


# --- Merkle tree, RFC 6962 (Certificate Transparency) hashing ------------------------------
def merkle_leaf(data: bytes) -> bytes:
    return sha256(b"\x00" + data)


def _node(l: bytes, r: bytes) -> bytes:
    return sha256(b"\x01" + l + r)


def _split(n: int) -> int:
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def merkle_root(leaves: list[bytes]) -> bytes:
    """MTH(D[n]) per RFC 6962 2.1: split at the largest power of two < n. `leaves` are already leaf-hashed."""
    n = len(leaves)
    if n == 0:
        return sha256(b"")
    if n == 1:
        return leaves[0]
    k = _split(n)
    return _node(merkle_root(leaves[:k]), merkle_root(leaves[k:]))


def merkle_path(leaves: list[bytes], m: int) -> list[tuple[str, bytes]]:
    """Audit path for leaf index m: list of (side, sibling_hash) from the leaf upwards; side is where the sibling sits."""
    n = len(leaves)
    if n <= 1:
        return []
    k = _split(n)
    if m < k:
        return merkle_path(leaves[:k], m) + [("R", merkle_root(leaves[k:]))]
    return merkle_path(leaves[k:], m - k) + [("L", merkle_root(leaves[:k]))]


def merkle_verify(leaf_hash: bytes, path: list[tuple[str, bytes]], root: bytes) -> bool:
    h = leaf_hash
    for side, sib in path:
        h = _node(h, sib) if side == "R" else _node(sib, h)
    return h == root
