"""Hash-chained audit log with signed checkpoints (REQ-AUD-01..04, 07, 08)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import crypto, keys
from .config import ROOT, get_settings
from .core import canonical_json, dumps, now, uuid7
from .db import engine, row, rows, scalar

GENESIS = "0" * 64
CHECKPOINT_EVERY = 1000
SEALER_LOCK = 7_000_001


def log(conn: Connection, action: str, *, entity_type: str | None = None, entity_id: Any = None, actor_id: Any = None,
        actor_role: str | None = None, institution_id: Any = None, case_id: Any = None, justification: str | None = None,
        request_id: str | None = None, ip_hash: str | None = None, payload: dict | None = None, at=None) -> None:
    """Write an audit row in the caller's transaction. Payloads must not carry PII."""
    conn.execute(text(
        "INSERT INTO audit.audit_log (occurred_at, actor_id, actor_role, action, entity_type, entity_id, institution_id, "
        "case_id, justification, request_id, ip_hash, payload) VALUES (:at, CAST(:actor AS uuid), :role, :action, :et, :eid, "
        "CAST(:inst AS uuid), CAST(:case AS uuid), :just, :rid, :ip, CAST(:payload AS jsonb))"),
        {"at": at or now(), "actor": str(actor_id) if actor_id else None, "role": actor_role, "action": action,
         "et": entity_type, "eid": str(entity_id) if entity_id is not None else None,
         "inst": str(institution_id) if institution_id else None, "case": str(case_id) if case_id else None,
         "just": justification, "rid": request_id, "ip": ip_hash, "payload": dumps(payload or {})})


def _entry_material(r: dict) -> dict:
    return {"id": r["id"], "occurred_at": r["occurred_at"], "actor_id": r["actor_id"], "actor_role": r["actor_role"],
            "action": r["action"], "entity_type": r["entity_type"], "entity_id": r["entity_id"],
            "institution_id": r["institution_id"], "case_id": r["case_id"], "justification": r["justification"],
            "request_id": r["request_id"], "payload": r["payload"]}


def entry_hash(prev_hash: str, r: dict) -> str:
    return hashlib.sha256(prev_hash.encode() + canonical_json(_entry_material(r))).hexdigest()


def seal(max_rows: int = 5000) -> int:
    """Assign seq/prev_hash/entry_hash to unsealed rows, in id order. Singleton via advisory lock."""
    sealed = 0
    with engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.scope','SYSTEM',true)"))
        if not scalar(conn, "SELECT pg_try_advisory_xact_lock(:k)", k=SEALER_LOCK):
            return 0
        last = row(conn, "SELECT seq, entry_hash FROM audit.audit_log WHERE stream_id='main' AND seq IS NOT NULL "
                         "ORDER BY seq DESC LIMIT 1")
        seq, prev = (last["seq"], last["entry_hash"]) if last else (0, GENESIS)
        pending = rows(conn, "SELECT * FROM audit.audit_log WHERE seq IS NULL ORDER BY id LIMIT :n", n=max_rows)
        for r in pending:
            seq += 1
            h = entry_hash(prev, r)
            conn.execute(text("UPDATE audit.audit_log SET seq=:s, prev_hash=:p, entry_hash=:h WHERE id=:id"),
                         {"s": seq, "p": prev, "h": h, "id": r["id"]})
            prev = h
            sealed += 1
        if sealed:
            _maybe_checkpoint(conn, seq, prev)
    return sealed


def _worm_dir() -> Path:
    d = Path(get_settings().worm_dir)
    d = d if d.is_absolute() else ROOT / d
    d.mkdir(parents=True, exist_ok=True)
    return d


def _maybe_checkpoint(conn: Connection, last_seq: int, last_hash: str) -> None:
    prev_cp = row(conn, "SELECT to_seq FROM audit.audit_checkpoints WHERE stream_id='main' ORDER BY to_seq DESC LIMIT 1")
    from_seq = (prev_cp["to_seq"] + 1) if prev_cp else 1
    if last_seq - from_seq + 1 < CHECKPOINT_EVERY:
        return
    write_checkpoint(conn, from_seq, last_seq, last_hash)


def write_checkpoint(conn: Connection, from_seq: int, to_seq: int, root_hash: str) -> str:
    kid, _, pem = keys.signing_key("SEALER")
    header = crypto.b64u(json.dumps({"alg": "ES256", "kid": kid, "typ": "audit-checkpoint"}).encode())
    body = crypto.b64u(canonical_json({"stream": "main", "from_seq": from_seq, "to_seq": to_seq, "root": root_hash}))
    jwt = f"{header}.{body}.{crypto.sign_es256(pem, f'{header}.{body}'.encode())}"
    cid = uuid7()
    path = _worm_dir() / f"checkpoint-main-{from_seq}-{to_seq}.jwt"
    path.write_text(jwt, encoding="utf-8")  # SIMULATED WORM: local append-only directory
    conn.execute(text(
        "INSERT INTO audit.audit_checkpoints (id, stream_id, from_seq, to_seq, root_hash, signed_jwt, kid, worm_path) "
        "VALUES (:id,'main',:f,:t,:r,:j,:k,:p)"), {"id": cid, "f": from_seq, "t": to_seq, "r": root_hash, "j": jwt, "k": kid,
                                                    "p": str(path)})
    return str(cid)


def checkpoint_now() -> str | None:
    """Force a checkpoint over everything sealed since the last one (used after seeding)."""
    seal()
    with engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.scope','SYSTEM',true)"))
        last = row(conn, "SELECT seq, entry_hash FROM audit.audit_log WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1")
        if not last:
            return None
        prev_cp = row(conn, "SELECT to_seq FROM audit.audit_checkpoints WHERE stream_id='main' ORDER BY to_seq DESC LIMIT 1")
        from_seq = (prev_cp["to_seq"] + 1) if prev_cp else 1
        if last["seq"] < from_seq:
            return None
        return write_checkpoint(conn, from_seq, last["seq"], last["entry_hash"])


def integrity_check() -> dict:
    """Recompute the whole chain, verify checkpoint signatures and WORM copies. Reports the first broken seq."""
    result: dict = {"ok": True, "checked": 0, "first_broken_seq": None, "problems": [], "checkpoints_verified": 0,
                    "worm": "SIMULATED (local append-only directory)"}
    with engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.scope','SYSTEM',true)"))
        prev = GENESIS
        expected = 1
        for r in conn.execute(text("SELECT * FROM audit.audit_log WHERE seq IS NOT NULL ORDER BY seq")).mappings():
            r = dict(r)
            if r["seq"] != expected:
                result.update(ok=False, first_broken_seq=r["seq"])
                result["problems"].append(f"sequence gap before seq {r['seq']}")
                return result
            if r["prev_hash"] != prev or entry_hash(prev, r) != r["entry_hash"]:
                result.update(ok=False, first_broken_seq=r["seq"])
                result["problems"].append(f"hash mismatch at seq {r['seq']} (action {r['action']})")
                return result
            prev, expected = r["entry_hash"], expected + 1
            result["checked"] += 1
        cps = rows(conn, "SELECT * FROM audit.audit_checkpoints ORDER BY to_seq")
        pub = {k["kid"]: k["public_jwk"] for k in rows(conn, "SELECT kid, public_jwk FROM cred.signing_keys")}
        for cp in cps:
            head, body, sig = cp["signed_jwt"].split(".")
            if not crypto.verify_es256(pub.get(cp["kid"], {"x": "AA", "y": "AA"}), f"{head}.{body}".encode(), sig):
                result.update(ok=False, first_broken_seq=cp["to_seq"])
                result["problems"].append(f"checkpoint {cp['id']} signature invalid")
                return result
            at = row(conn, "SELECT entry_hash FROM audit.audit_log WHERE stream_id='main' AND seq=:s", s=cp["to_seq"])
            if not at or at["entry_hash"] != cp["root_hash"]:
                result.update(ok=False, first_broken_seq=cp["to_seq"])
                result["problems"].append(f"checkpoint {cp['id']} root does not match chain at seq {cp['to_seq']}")
                return result
            worm = Path(cp["worm_path"]) if cp["worm_path"] else None
            if not worm or not worm.exists() or worm.read_text(encoding="utf-8") != cp["signed_jwt"]:
                result.update(ok=False, first_broken_seq=cp["to_seq"])
                result["problems"].append(f"WORM copy of checkpoint {cp['id']} missing or different")
                return result
            result["checkpoints_verified"] += 1
    return result
