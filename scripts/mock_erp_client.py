"""Reference clients for the system-to-system API. SIMULATED: they stand in for an institution ERP and for the ABC / exam-registration feed.

Create a client first (Institution portal > Admin > API clients, or AICTE > Users and feeds > Feed clients), then:

  python scripts/mock_erp_client.py claim    --client-id cl-... --client-secret ... --apaar 99xxxxxxxxxx --name "A B" --dob 2007-03-15 --programme CSE
  python scripts/mock_erp_client.py liveness --client-id cl-... --client-secret ... --apaar 99xxxxxxxxxx --abc --exam
  python scripts/mock_erp_client.py verify-webhook --secret whsec_... --signature "t=...,v1=..." --body-file body.json

Uses only the standard library plus httpx (already a backend dependency).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import uuid

import httpx


def token(api: str, cid: str, secret: str) -> str:
    r = httpx.post(f"{api}/auth/token", json={"client_id": cid, "client_secret": secret}, timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("claim", "liveness"):
        p = sub.add_parser(name)
        p.add_argument("--client-id", required=True); p.add_argument("--client-secret", required=True); p.add_argument("--apaar", required=True)
        if name == "claim":
            p.add_argument("--name", required=True); p.add_argument("--dob", required=True); p.add_argument("--programme", required=True)
            p.add_argument("--student-id", default="ERP" + uuid.uuid4().hex[:6]); p.add_argument("--gender", default="M"); p.add_argument("--parent-name", default="Parent")
        else:
            p.add_argument("--abc", action="store_true"); p.add_argument("--exam", action="store_true")
    w = sub.add_parser("verify-webhook")
    w.add_argument("--secret", required=True); w.add_argument("--signature", required=True); w.add_argument("--body-file", required=True)
    a = ap.parse_args()

    if a.cmd == "verify-webhook":
        body = open(a.body_file, encoding="utf-8").read()
        parts = dict(x.split("=", 1) for x in a.signature.split(","))
        want = "t=%s,v1=%s" % (parts["t"], hmac.new(a.secret.encode(), f"{parts['t']}.{body}".encode(), hashlib.sha256).hexdigest())
        ok = hmac.compare_digest(want, a.signature)
        print("signature valid" if ok else "signature INVALID")
        return 0 if ok else 1

    h = {"Authorization": f"Bearer {token(a.api, a.client_id, a.client_secret)}"}
    if a.cmd == "claim":
        body = {"apaar_id": a.apaar, "name": a.name, "dob": a.dob, "institution_student_id": a.student_id, "programme_code": a.programme, "gender": a.gender, "parent_name": a.parent_name}
        r = httpx.post(f"{a.api}/claims", headers={**h, "Idempotency-Key": uuid.uuid4().hex}, json=body, timeout=30)
    else:
        r = httpx.post(f"{a.api}/feeds/liveness", headers=h, json={"items": [{"apaar_id": a.apaar, "has_abc_credit": a.abc, "has_exam_registration": a.exam}]}, timeout=30)
    print(r.status_code)
    print(json.dumps(r.json(), indent=2))
    return 0 if r.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
