# ShikshaSetu — AICTE APAAR trust layer (functional prototype)

An end-to-end functional **prototype** built from `PRD.md` (PRD name: *APAAR Sentinel*; the UI follows `UI_ref.png` and is branded **APAAR ShikshaSetu**).
An institution *claims* a student's APAAR ID before admitting; a live seat ledger and **three independent locks** (institution · registry · student)
decide what is recorded as VERIFIED. All data is synthetic. Registry and SMS are **mock services** and are labelled `SIMULATED` wherever they appear.

> A signature proves integrity, not truth. · APAAR is never mandatory for being counted. · Aadhaar is never stored or printed.

## What is built in this delivery (and what is not)

The PRD describes a very large system. This delivery covers the whole scope in one pass, with the simplifications listed under "Honest list of simplifications and known gaps" below.
"Built and tested" means an automated test or a `verify-seed` check exercises it; "Built" means it runs but has no dedicated automated test; anything not exercised is called out.

| Area | Status |
|---|---|
| Auth: demo accounts (password `1234`), JWT HS256, argon2id, lockout, sessions, step-up, client credentials | **Built and tested** |
| RBAC: route permission registry, startup guard, generated route × role matrix test, forced RLS, regional scope | **Built and tested** |
| Claim gate: atomic seat ledger, exclusivity across institutions, idempotency, dry-run validate, seat exceptions, admit / withdraw | **Built and tested** (1,000 claims at concurrency 50 → exactly 100 succeed) |
| Identity: hygiene, Aadhaar guard, registry adapter + circuit breaker, diagnosis with fixes, provisional IDs, fix-it flow | **Built and tested** |
| Triple-Lock: OTP (mock SMS), guardian mode, assisted confirmation, dispute → case, deadline job | **Built and tested** |
| Rules engine (data-driven, 9 rule kinds; `SUM_RECONCILES` is submission-site and not yet used), findings | **Built** (claim site) |
| Hash-chained audit log, signed checkpoints, tamper demo, simulated WORM | **Built and tested** |
| Dashboards from an outbox projector (`agg.*`), AICTE views read aggregates only, small-cell suppression | **Built and tested** |
| Seed: deterministic, ~14k students, 60 institutions, story fixtures through the real gate, `verify-seed` | **Built** |
| React UI: landing, login, institution / AICTE / student portals, Dev SMS Inbox, Operations, Demo tools | **Built** (see "Known gaps") |
| Bulk upload: template, scan, encrypted staging, chunked dry-run (live claim rules + registry), diff, `errors.csv`, commit by a **different** admin with step-up, idempotent re-upload | **Built and tested** (CSV only; no XLSX, no malware scan — recorded as `NOT_PERFORMED`) |
| Submissions: declared totals, readiness check (`SUM_RECONCILES`), warning acknowledgement, window + extensions, AICTE accept / request review, **RFC 6962 Merkle cohort commitments** signed by the institution and countersigned by AICTE, inclusion proofs (verifiable in the browser), tamper detection | **Built and tested** |
| Duplicate/transfer engine: keyed blocking keys, Bloom-filter Dice scoring, timeline classes, review queue with per-field contributions, confirm/dismiss (labels stored), transfers with auto-match and orphan leads, `eval` harness | **Built and tested** |
| Trust Index (six weighted components, formulas and evidence shown, nightly + run-now), anomaly layers A (findings), B (duplicates), C (robust z-scores + scikit-learn Isolation Forest when ≥ 30 institutions), Louvain graph clusters (NetworkX) | **Built and tested** (layer D liveness feed not built) |
| Cases workspace (state machine, 14-day response window, outcome labels, SHA-256 evidence pack), appeals (15-day window, different decider, DB trigger), seat release (step-up), student disputes resolved only via a case outcome | **Built and tested** |
| Elevation grants (case-gated, max 4 h, justification ≥ 30 chars) with audited student-level reveal, volume alert > 200/hour | **Built and tested** |
| Reports: 8 aggregate types + institution-only student export; CSV, XLSX, PDF; stamped, QR → public SHA-256 check, small-cell suppression, formula-safe, regional scope | **Built and tested** |
| Credentials: SD-JWT VC (ES256) with selective disclosure, AICTE attestation, W3C-style Bitstring Status List (131,072 bits, signed), issue / issue-all / supersede / revoke, auto-revoke when an enrolment ends, PDF certificate with QR | **Built and tested** |
| Verification service (`verify-api`, separate process on :8001): purpose-bound signed YES/NO answers (5-minute TTL, nonce), identical NEUTRAL body for unknown / no-consent / expired, per-org scope, hourly limit, abuse alert, public status page (`/v/:ref`), share-link page (`/s/:token`), JWKS and key-status endpoints | **Built and tested** |
| Student wallet: credentials + QR, share links (token shown once, hash stored, ≤ 30 days, revocable), consent ledger and consent requests, APAAR-use consent (never affects counting), access log, correction requests (values encrypted; reviewer decides), data-subject requests (access export, erasure with lawful-retention rule), guardian link by OTP | **Built and tested** |
| Rule sets: draft from active, edit, publish (a **different** reviewer, step-up), immutable once published, rollback; **policy simulator** (read-only replay over a non-PII snapshot, skipped rules listed, reviewer workload) | **Built and tested** |
| Onboarding (public application, **SIMULATED** identity check, AICTE approval creates the administrator), user management (tenant-limited, immediate deactivation), API clients (secret shown once, rotate, disable) | **Built and tested** |
| Webhooks: HMAC-signed deliveries (identifiers only), retry 1 m / 5 m / 30 m / 2 h / 6 h, dead-letter after 24 h, replay, SSRF guard, local demo sink | **Built and tested** (verified end to end against the running API and sink) |
| Retention job (staged rows, idempotency keys, sessions, OTP hashes, expired grants; the audit trail is never purged); Layer D liveness anomaly from a **SIMULATED** feed (`POST /feeds/liveness`) | **Built and tested** |
| Hindi labels for the student portal navigation and key notices, language toggle | **Partial** (a small catalogue; most pages stay English) |
| Report schedules | **Not built** |

## Stack

React + TypeScript (Vite, React Router, TanStack Query) · Python 3.11+, FastAPI, Pydantic v2 · SQLAlchemy Core + psycopg · Alembic migrations ·
PostgreSQL (Supabase) · pandas, scikit-learn (Isolation Forest), NetworkX (Louvain), RapidFuzz · XlsxWriter, reportlab, segno for reports · `cryptography` (AES-GCM vault, HMAC keyed hashes, ES256, Merkle) · Docker.
No Redis, no Celery: background work uses a PostgreSQL-backed queue (`ops.jobs`, `FOR UPDATE SKIP LOCKED`) with a separate worker/scheduler process.

## Database: hosted Supabase PostgreSQL

Set `DATABASE_URL` in `.env` to your Supabase project's **direct** (port 5432) or **session-pooler** connection string
(`postgresql+psycopg://postgres:<password>@db.<ref>.supabase.co:5432/postgres`). Do **not** use the transaction pooler (6543): the app uses `SET LOCAL` per transaction and Alembic needs a session.
The app uses plain PostgreSQL features only (row locks, partial unique indexes, CHECKs, RLS, grants); no Supabase Auth/Storage/Realtime are used.
Each request runs as `SET LOCAL ROLE app_runtime` (created by migration 0001) so row-level security and the append-only grants apply to application code.

For local development and for tests, `docker compose --profile localdb up -d db testdb` starts two **plain PostgreSQL 16** containers (ports 54322 and 54323).
They are stand-ins, not the product database. **Tests drop and recreate schemas — only ever point `TEST_DATABASE_URL` at a throwaway database.**

## Quick start

Prerequisites: Docker Desktop, Python 3.11+ (3.13 tested), Node 20+ (24 tested), `uv` (or `pip`).

```bash
cp .env.example .env            # then generate secrets (see comments in the file) and set DATABASE_URL
docker compose --profile localdb up -d db testdb       # local stand-in databases (skip if you use hosted Supabase)
cd backend
uv venv --python 3.13 .venv && uv pip install --python .venv/Scripts/python.exe -e ".[dev]"    # Linux/macOS: .venv/bin/python
.venv/Scripts/python.exe -m app.cli setup               # migrate + seed (standard) + verify-seed
.venv/Scripts/python.exe -m app.cli dev                 # mock services :8002, verify-api :8001, API :8000, worker, and web :5173 if node_modules exists
cd ../web && npm install && npm run dev                 # if you prefer to run the UI separately
```

Open <http://localhost:5173>, choose **Sign in as…** (password `1234` for every account). `docs/demo-script.md` is generated by the seed with the synthetic APAAR IDs for demos.

| Command (`python -m app.cli …`) | Purpose |
|---|---|
| `migrate` / `seed [--profile minimal\|standard]` / `reseed` / `verify-seed` | Alembic upgrade; truncate-and-seed (standard ≈2.8 min measured: 166 s on the dev laptop, PRD target is 3 min); recompute invariants (33 checks) |
| `setup` | migrate + seed + verify |
| `dev` / `api` / `verify-api` / `mock` / `worker` | Start processes (the worker also runs webhook deliveries, simulations, retention and the projector) |
| `rls-check` / `integrity-check` | Forced RLS and grants self-check; recompute the audit chain |
| `eval` | Duplicate-engine precision/recall against the labelled seed ground truth; checks legitimate pairs are not flagged |
| `demo tamper-audit --seq 40` / `demo tamper-enrolment --id <uuid>` / `demo untamper` | Alter one audit row, or one enrolment after its cut-off (originals saved), so the integrity check / commitment verification fails; restore |

Tests (use the throwaway DB on port 54323; 139 tests passed on the last full run, including a 1,000-claim concurrency test, the route × role matrix with a verifier user, and the final-phase suite):

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests
```

### Docker (everything)

`docker compose --profile app --profile localdb up --build` builds `api`, `mock`, `worker` and `web`. Run migrations/seed first (`docker compose run --rm api python -m app.cli setup`).
What was verified: the backend image builds and `app`/`verify_api` import inside it, and the compose file validates. **Not exercised:** the web image build and a full `compose up` with migrations and seed; the local (non-Docker) run is the verified path.

## Demo accounts (password `1234`)

`ia.kit`, `ia2.kit`, `io.kit` (Kaveri Institute of Technology) · `ia.bvec`, `io.bvec` · `ia.mpb`, `io.mpb` · `ia.gce` · `ia.asm`, `io.asm` ·
`ar.national`, `ar.national2`, `ar.south` (regional: sees South only) · `as.admin` (Demo tools) ·
`st.aarav` (VERIFIED), `st.diya` (Lock 3 pending), `st.priya`, `st.rohan` (minor) with guardian `gd.meera`, `st.ishaan` (provisional ID), `st.fatima` (APAAR claimed by a ghost-ring claim at GCE), `st.vikram` (withdrawn) ·
60 more institutions have `ia.<slug>` / `io.<slug>` accounts, listed under "Other institutions" on the login page. API clients: `erp-kit` / `feeds-mock` (secret `1234`).
Verifier accounts: `vu.employer` (Meridian Software, employment verification), `vu.bank` (Sagarmatha Co-operative Bank, loan eligibility), `vu.embassy` (visa support, narrowest field scope).
Try this: sign in as `st.aarav` (Credentials) and copy the credential reference, then as `vu.employer` ask "is this person enrolled?" (consent already granted for three fields);
ask the same as `vu.bank` and get a neutral reply, then see the pending consent request in `st.aarav` > Consents. `ia.sahyadri-belagavi` does not exist until `ar.national` approves the pending application (Reviews > Onboarding).
`as.admin` has a draft rule set (v2) and the policy simulator. `ar.national` publishes it (a different person than the drafter).

## Honest list of simplifications and known gaps

* **SIMULATED**: APAAR registry, SMS gateway, simulated WORM (local directory), step-up (password re-entry instead of TOTP), software key provider instead of KMS/HSM, the nodal-officer identity check in onboarding, and the ABC / exam-registration liveness feed (`scripts/mock_erp_client.py liveness`). Each is labelled in the UI.
* Name matching tolerates initials, Mohd/Mohammed-style variants, typos and phonetic similarity. **Devanagari/Tamil transliteration is not implemented** (the library is not in the approved stack).
* Bulk upload accepts **CSV only**; the browser pre-check is a small header/row-count check (no Papa Parse/SheetJS/Zod, which are not approved). The `job.completed` webhook is sent when an upload finishes; the uploader and committer also get an in-app notification.
* Duplicate matching uses 256-bit Bloom filters with 3 keyed hashes and weights 0.45/0.30/0.25 (the PRD specifies 1,024 bits, 20 hashes and 0.5/0.3/0.2) — a documented deviation. Duplicate matching uses static weights (no Splink training). The Bloom-filter privacy review is a documented pre-production requirement.
* Dashboards poll every 15 s (no push). The geographic map is replaced by a state table + bars. Dark mode is not built, and the Hindi catalogue covers navigation and key notices only (the PRD asks for full coverage).
* `agg.claim_daily` and the "claims trend" derive from `core.claim_attempts` — the seeded history is synthetic.
* The seed produces ~8.8k current-cycle / ~5.5k prior-cycle enrolments, ~2.9k findings, ~38k audit entries; these are within a few percent of the PRD standard profile but are not identical counts.
* `ops.jobs` retry/dead-letter, webhook delivery, simulations, uploads, reports and the retention job all run in the worker process (`app.cli worker`); without it uploads, webhooks and simulations stay queued. Report schedules (recurring reports) are not built; reports are generated on request.
* The Playwright E2E scripts from PRD §14 are not part of this delivery; UI flows were exercised by hand in a browser against the running servers. The `full` seed profile was not run (only `minimal` and `standard`).
* Credentials use one revocation status list (131,072 entries); a second list is not supported. The `YEAR_COMPLETION`, `DEGREE_DIPLOMA` and `TRANSCRIPT_SUMMARY` credential types are in the schema but only `ENROLMENT_PROOF` is issued. Verifier answers cover three yes/no questions (enrolled, programme, year).
* The hosted Supabase connection was never exercised (no connection string was available while building); everything was run and tested against local PostgreSQL 16. Migration 0001 creates the `app_runtime` role and relies on `SET LOCAL ROLE`; whether the Supabase `postgres` user may do that was not checked, so expect to verify it on first connect (`python -m app.cli migrate`, then `rls-check`).
* Demo credentials (`1234`) are for local demonstration only; the API refuses to start with `APP_ENV=production` and `DEMO_MODE=true` or placeholder secrets.

## Repository layout

```
backend/app/        FastAPI core API (claims, identity, verification, rules_engine, audit, ops, projector, seed*, routes_*)
backend/mock_services/   MOCK APAAR registry + SMS gateway (own process, schema mockext)
backend/verify_api/ Separate verification service (public status, share links, verifier portal API) on :8001
scripts/            mock_erp_client.py: system-client demo (claims, liveness feed, webhook signature check)
backend/migrations/ Alembic (SQL files under migrations/sql)
backend/tests/      pytest suites (units, auth/ACL matrix, gate + concurrency, Triple-Lock, audit tamper, RLS, uploads/Merkle/duplicates, cases/reports, credentials/verifier/wallet/rule sets/webhooks)
web/                React SPA (landing, login, institution / AICTE / student / verifier portals, public status + share pages, onboarding)
docs/demo-script.md Generated by the seed
```
