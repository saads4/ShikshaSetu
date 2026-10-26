"""Software key provider (DEC-09): ES256 keys generated locally, private halves wrapped by MASTER_KEY.

Private key material is read through an owner connection; app_runtime has no SELECT on it.
SIMULATED: stands in for a cloud KMS/HSM.
"""
from __future__ import annotations

import json
from functools import lru_cache

from sqlalchemy import text

from . import crypto
from .core import uuid7
from .db import engine


def _ensure(purpose: str, institution_id: str | None) -> tuple[str, dict, bytes]:
    with engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.scope','SYSTEM',true)"))
        # serialise key creation per (purpose, institution) so concurrent workers don't create two keys
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"key:{purpose}:{institution_id}"})
        r = conn.execute(text(
            "SELECT kid, public_jwk, private_wrapped FROM cred.signing_keys WHERE purpose=:p AND status='ACTIVE' "
            "AND institution_id IS NOT DISTINCT FROM CAST(:i AS uuid) ORDER BY created_at LIMIT 1"),
            {"p": purpose, "i": institution_id}).first()
        if r:
            return r.kid, r.public_jwk, crypto.unwrap(r.private_wrapped)
        pem, jwk = crypto.new_es256()
        kid = f"{purpose.lower()}-{crypto.b64u(crypto.sha256(json.dumps(jwk, sort_keys=True).encode())[:8])}"
        jwk["kid"] = kid
        conn.execute(text(
            "INSERT INTO cred.signing_keys (id, kid, purpose, institution_id, public_jwk, private_wrapped) "
            "VALUES (:id,:kid,:p,CAST(:i AS uuid),CAST(:jwk AS jsonb),:pw)"),
            {"id": uuid7(), "kid": kid, "p": purpose, "i": institution_id, "jwk": json.dumps(jwk),
             "pw": crypto.wrap(pem)})
        return kid, jwk, pem


@lru_cache(maxsize=512)
def signing_key(purpose: str, institution_id: str | None = None) -> tuple[str, dict, bytes]:
    """(kid, public_jwk, private_pem) for the active key of this purpose/institution."""
    return _ensure(purpose, institution_id)


def clear_cache() -> None:
    signing_key.cache_clear()
    data_key.cache_clear()


@lru_cache(maxsize=512)
def data_key(institution_id: str) -> tuple[str, int, bytes]:
    """(data_key_id, version, plaintext key) - per-institution AES-256 key wrapped by MASTER_KEY."""
    with engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.scope','SYSTEM',true)"))
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"dk:{institution_id}"})
        r = conn.execute(text(
            "SELECT id, version, wrapped_key FROM pii.data_keys WHERE institution_id=CAST(:i AS uuid) AND status='ACTIVE' "
            "ORDER BY version DESC LIMIT 1"), {"i": institution_id}).first()
        if r:
            return str(r.id), r.version, crypto.unwrap(r.wrapped_key)
        key, wrapped = crypto.new_data_key()
        kid = uuid7()
        conn.execute(text("INSERT INTO pii.data_keys (id, institution_id, version, wrapped_key) "
                          "VALUES (:id, CAST(:i AS uuid), 1, :w)"), {"id": kid, "i": institution_id, "w": wrapped})
        return str(kid), 1, key


def data_key_by_id(key_id: str) -> bytes:
    with engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.scope','SYSTEM',true)"))
        r = conn.execute(text("SELECT wrapped_key FROM pii.data_keys WHERE id=CAST(:i AS uuid)"), {"i": key_id}).first()
    return crypto.unwrap(r.wrapped_key)
