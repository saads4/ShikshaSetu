"""`python -m app.cli <command>` (PRD 15.3, subset implemented in this phase)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _migrate() -> None:
    subprocess.check_call([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND)


def _seed(profile: str | None) -> dict:
    from . import seed_main
    return seed_main.run(profile)


def _verify(profile: str | None) -> int:
    from . import verify
    from .config import get_settings
    results = verify.run(profile or get_settings().seed_profile)
    failed = [r for r in results if not r["ok"]]
    for r in results:
        print(("PASS " if r["ok"] else "FAIL ") + r["check"] + (f"  [{r['detail']}]" if r["detail"] else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sentinel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sp = sub.add_parser("seed"); sp.add_argument("--profile", choices=["minimal", "standard"])
    sp = sub.add_parser("reseed"); sp.add_argument("--profile", choices=["minimal", "standard"])
    sp = sub.add_parser("verify-seed"); sp.add_argument("--profile", choices=["minimal", "standard"])
    sp = sub.add_parser("setup"); sp.add_argument("--profile", choices=["minimal", "standard"])
    sub.add_parser("rls-check")
    sub.add_parser("worker")
    sub.add_parser("api")
    sub.add_parser("mock")
    sub.add_parser("verify-api")
    sub.add_parser("dev")
    sub.add_parser("integrity-check")
    sub.add_parser("eval")
    t = sub.add_parser("demo"); t.add_argument("action", choices=["tamper-audit", "tamper-enrolment", "untamper"]); t.add_argument("--seq", type=int, default=5); t.add_argument("--id", default=None)
    a = ap.parse_args(argv)

    if a.cmd == "migrate":
        _migrate()
    elif a.cmd in ("seed", "reseed"):
        print(json.dumps(_seed(a.profile), indent=2, default=str))
    elif a.cmd == "verify-seed":
        return _verify(a.profile)
    elif a.cmd == "setup":
        _migrate()
        print(json.dumps(_seed(a.profile), indent=2, default=str))
        rc = _verify(a.profile)
        print("\nOpen http://localhost:5173 (after `dev`) and sign in with any demo account; the password is 1234.")
        return rc
    elif a.cmd == "rls-check":
        from .rlscheck import check
        problems = check()
        print("OK" if not problems else "\n".join(problems))
        return 1 if problems else 0
    elif a.cmd == "integrity-check":
        from . import audit
        audit.seal()
        r = audit.integrity_check()
        print(json.dumps(r, indent=2))
        return 0 if r["ok"] else 1
    elif a.cmd == "eval":
        from . import evalharness
        print(json.dumps(evalharness.run(), indent=2))
    elif a.cmd == "worker":
        from .ops import worker_main
        from .main import configure_logging
        configure_logging()
        worker_main()
    elif a.cmd == "api":
        subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000"], cwd=BACKEND)
    elif a.cmd == "mock":
        subprocess.call([sys.executable, "-m", "uvicorn", "mock_services.main:app", "--port", "8002"], cwd=BACKEND)
    elif a.cmd == "verify-api":
        subprocess.call([sys.executable, "-m", "uvicorn", "verify_api.main:app", "--port", "8001"], cwd=BACKEND)
    elif a.cmd == "dev":
        procs = [subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_services.main:app", "--port", "8002"], cwd=BACKEND),
                 subprocess.Popen([sys.executable, "-m", "uvicorn", "verify_api.main:app", "--port", "8001"], cwd=BACKEND),
                 subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000", "--reload"], cwd=BACKEND),
                 subprocess.Popen([sys.executable, "-m", "app.cli", "worker"], cwd=BACKEND)]
        web = BACKEND.parent / "web"
        if (web / "node_modules").exists():
            procs.append(subprocess.Popen("npm run dev", cwd=web, shell=True))
        try:
            for p in procs:
                p.wait()
        except KeyboardInterrupt:
            for p in procs:
                p.terminate()
    elif a.cmd == "demo":
        from sqlalchemy import text
        from .db import engine
        with engine().begin() as conn:
            conn.execute(text("SELECT set_config('app.scope','SYSTEM',true)"))
            if a.action == "tamper-audit":
                conn.execute(text("ALTER TABLE audit.audit_log DISABLE TRIGGER trg_audit_guard"))
                conn.execute(text("CREATE TABLE IF NOT EXISTS audit.tamper_backup AS SELECT * FROM audit.audit_log WHERE false"))
                conn.execute(text("INSERT INTO audit.tamper_backup SELECT * FROM audit.audit_log WHERE seq=:s"), {"s": a.seq})
                conn.execute(text("UPDATE audit.audit_log SET payload = payload || '{\"tampered\": true}'::jsonb WHERE seq=:s"), {"s": a.seq})
                conn.execute(text("ALTER TABLE audit.audit_log ENABLE TRIGGER trg_audit_guard"))
                print(f"Altered audit entry seq={a.seq} (original saved). Run `integrity-check` to see it detected.")
            elif a.action == "tamper-enrolment":
                if not a.id:
                    print("--id <enrolment uuid> is required"); return 2
                r = conn.execute(text("SELECT status, admission_date FROM core.enrolments WHERE id=CAST(:i AS uuid)"), {"i": a.id}).first()
                if not r:
                    print("No such enrolment"); return 2
                conn.execute(text("INSERT INTO ops.system_config (key, value) VALUES ('tamper_enrolment', CAST(:v AS jsonb)) ON CONFLICT (key) DO UPDATE SET value=CAST(:v AS jsonb)"),
                             {"v": json.dumps({"id": a.id, "status": r.status, "admission_date": str(r.admission_date) if r.admission_date else None})})
                conn.execute(text("UPDATE core.enrolments SET admission_date = coalesce(admission_date, current_date) - 3, status = CASE WHEN status='CLAIMED' THEN 'ADMITTED' ELSE status END WHERE id=CAST(:i AS uuid)"), {"i": a.id})
                print("Altered one enrolment after its cut-off (original saved). Open the submission and press 'Verify commitment'.")
            else:
                conn.execute(text("ALTER TABLE audit.audit_log DISABLE TRIGGER trg_audit_guard"))
                if conn.execute(text("SELECT to_regclass('audit.tamper_backup')")).scalar():
                    conn.execute(text("UPDATE audit.audit_log a SET payload=b.payload FROM audit.tamper_backup b WHERE a.id=b.id"))
                    conn.execute(text("DROP TABLE IF EXISTS audit.tamper_backup"))
                conn.execute(text("ALTER TABLE audit.audit_log ENABLE TRIGGER trg_audit_guard"))
                saved = conn.execute(text("SELECT value FROM ops.system_config WHERE key='tamper_enrolment'")).scalar()
                if saved:
                    conn.execute(text("UPDATE core.enrolments SET status=:s, admission_date=CAST(:d AS date) WHERE id=CAST(:i AS uuid)"), {"s": saved["status"], "d": saved["admission_date"], "i": saved["id"]})
                    conn.execute(text("DELETE FROM ops.system_config WHERE key='tamper_enrolment'"))
                print("Restored original audit entries and enrolment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
