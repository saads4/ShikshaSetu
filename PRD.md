# APAAR Sentinel — Prototype Product Requirements Document (PRD)

| | |
|---|---|
| **Product** | APAAR Sentinel — a trust layer placed in front of the AICTE portal |
| **Document type** | Implementation-ready PRD for an end-to-end functional **prototype** (not a production deployment) |
| **Version / date** | 1.0 · 26 October 2026 |
| **Source baseline** | *APAAR Sentinel: System Information Document* (the single uploaded file), which itself reconciles (1) the *Production Architecture and Design Report* of 25 Oct 2026 and (2) the six-slide SIH 2026 deck |
| **Audience** | An AI coding agent (and human reviewers) who will build, test, seed and run the prototype |
| **Event context** | Smart India Hackathon 2026 · Theme: Smart Automation · Category: Software · Team Artificial_Unintelligence |

---

## 0. How to read and use this PRD

### 0.1 Provenance labels (used throughout)

Every non-trivial statement carries one of these labels so nothing is silently invented.

| Label | Meaning |
|---|---|
| **[SRC]** / `FR-xx`, `C-xx`, `G-xx`, `AS-xx`, `R-xx`, `F#`, `M##`, `P#` | Taken from the uploaded source document, with its own IDs kept for traceability |
| **[DEC-nn]** | An **implementation decision** made in this PRD to make the prototype practical (register in §2.6). Never presented as a source requirement |
| **[SIM]** | A **mocked or simulated** capability. The UI and the API must visibly label it as simulated (see §0.3) |
| **REQ-xxx-nn** | A requirement defined by this PRD (§3). Each cites its source reference |
| **MVP / Deferred / Future** | Feature classes defined in §0.2 and applied in Appendix A |

### 0.2 Feature classes

| Class | Definition |
|---|---|
| **MVP** | Built in this prototype. Required for a complete, realistic, demonstrable end-to-end system |
| **Deferred** | Deliberately excluded from this implementation (includes **Redis, Grafana** and everything that depends on them). Module boundaries and interfaces keep a seam so it can be added without refactoring |
| **Future Production Enhancement** | Needed only when scaling toward the national production target described in the source (HSM, WAF, DR, Kubernetes, real national integrations, and so on) |

### 0.3 The honesty rule (binding on the whole build)

1. Simulated data and mock services are always labelled in the UI (a `SIMULATED` or `MOCK` badge and a tooltip naming the real system it stands in for) and in API responses (`"source": "MOCK_APAAR_REGISTRY"`).
2. The prototype never claims a capability it does not implement. Deferred features are either absent from the UI or shown as a disabled item with the text "Not available in this prototype".
3. All data is synthetic (AS-09). No real person, real APAAR ID, real phone number or real Aadhaar number appears anywhere. Institution names are fictional.
4. Demo credentials (`1234`) are for local demonstration only (§9.1.4).
5. Statements the source says must be stated plainly stay stated plainly in the product: *a signature proves integrity, not truth; APAAR is never mandatory for being counted; Aadhaar is never stored or printed* (source §9.5).

### 0.4 Requirement keywords
**MUST** = mandatory for MVP acceptance. **SHOULD** = expected unless a documented reason exists. **MAY** = optional.

### 0.5 Document map
§1 Summary · §2 Source analysis · §3 Requirements · §4 Roles · §5 Workflows · §6 Architecture · §7 Database · §8 API · §9 Security · §10 UI/UX · §11 Seed data · §12 User stories · §13 Edge cases · §14 Testing · §15 Setup and deployment · §16 Roadmap · Appendix A Feature classification and simplification register · Appendix B Traceability matrix · Appendix C Glossary.

---

## 1. Executive Summary, Objectives, Scope and Success Criteria

### 1.1 Executive summary

AICTE today receives enrolment as category totals **after** admission has happened. Nothing at the moment of admission stops an institution from exceeding approved intake, enrolling one student in two places, or filling seats with students who do not exist. APAAR Sentinel is a **trust layer in front of the AICTE portal**: an institution *claims* a student's APAAR ID before admitting; a **live seat ledger** and **three independent confirmations (Triple-Lock: institution, APAAR registry, student)** decide what AICTE records; verifiers receive a **signed minimal yes or no**, never the student file. Intelligence (Trust Index, explainable anomaly leads, policy simulator) gives AICTE leads, never automatic verdicts. [SRC §1]

This PRD specifies a **large, realistic, end-to-end functional prototype** of that system: real persistence, real business rules and state machines, real atomic seat/claim enforcement in PostgreSQL, real cryptographic signing and hash-chained audit, a genuine multi-role web application, and a large interconnected synthetic dataset. External systems that do not exist yet (APAAR registry, SMS gateway, ABC/NAD feeds, AICTE master) are replaced by **clearly labelled mock services that behave realistically** (latency, outages, mismatches). **Redis, Grafana and all infrastructure that depends on them are deferred** and replaced with database-backed equivalents behind stable interfaces.

### 1.2 Problem statement and pain points [SRC §1.2]

| # | Pain point | Prototype demonstration |
|---|---|---|
| 1 | Admissions beyond approved intake | Live seat ledger: warning at 90%, hard stop at 100%, reviewer-approved exceptions |
| 2 | Same student at two institutions | Atomic claim on a keyed hash of the APAAR ID; second claim returns `CONFLICT` |
| 3 | Ghost students on borrowed IDs | Student confirmation (OTP); unconfirmed share and liveness signal feed the Trust Index and alerts |
| 4 | Invalid or missing APAAR ID | Indic-aware name/DOB diagnosis with field-level fixes; provisional IDs; APAAR is never a hard gate |
| 5 | Records nobody can verify | Signed credential, QR, verifier portal returning a signed yes/no with only permitted fields |

### 1.3 Objectives [SRC §1.3] and prototype interpretation

| # | Source objective | Prototype must show |
|---|---|---|
| O1 | Stop admissions beyond approved intake at admission | 1,000 attempts at concurrency 50 on a bucket with cap 100 yield exactly 100 successes |
| O2 | One student cannot hold two exclusive seats in a cycle | Simultaneous claims of one ID by two institutions yield exactly one success |
| O3 | Ghost enrolments hard via three confirmations | Seat is `VERIFIED` only when all three locks agree; Pending/Unconfirmed/Disputed shown honestly |
| O4 | Never penalise a genuine student for an identity problem | NOGEN / CONSENT_REFUSED / FOREIGN students get provisional IDs and are counted and checked |
| O5 | Signed minimal answer for verifiers; student sees and revokes access | Verifier flow with disclosure meter; student access log and consent withdrawal |
| O6 | Explainable leads for AICTE, never automatic verdicts | Trust Index components link to evidence rows; cases, appeals, no automatic penalty |
| O7 | Minimal, private data; no Aadhaar anywhere | Encrypted PII vault, keyed hashes, aggregate-by-default dashboards, Aadhaar guard, redacted logs |
| O8 | Tamper-evident records without a blockchain | Hash-chained audit with signed checkpoints; Merkle cohort commitments, countersigned |
| O9 | Test a rule change on past cycles before go-live | Policy simulator replays a draft rule set over the seeded 2025-26 cycle |

### 1.4 Scope

**In scope (MVP)** — all of the following, working end to end:
claim gate and seat ledger · identity validation with provisional IDs · rules engine and findings · bulk upload (dry-run then commit) · ERP API channel with signed webhooks · submission lifecycle with windows and extensions · Triple-Lock verification (OTP via mock SMS, guardian, assisted) · duplicate and transfer engine · Trust Index, anomaly layers, graph clusters, liveness (mock feed), cases and appeals · AICTE and institution dashboards · reports (CSV, Excel, PDF) · signed credentials, certificates and QR · verifier portal and public verify service · student wallet (consents, access log, share links, corrections, data requests, guardian mode) · hash-chained audit and Merkle cohort commitments · policy simulator · RBAC/ABAC with RLS and case-gated drill-down · mock services · seeded dataset · demo tooling.

**Out of scope (Deferred or Future)** — listed with reasons in Appendix A: Redis and Celery, Grafana/Prometheus/Loki/Tempo/Sentry, Supabase platform services, TOTP MFA/SSO, KMS/HSM, WORM object-lock storage, DigiLocker/ABC/NAD production integration, natural-language query, three-way match, read replica/partitioning/Kubernetes/DR, BYOK, private set intersection and zero-knowledge proofs, native mobile apps, payment handling, Aadhaar in any form. [SRC §1.7, §4.3]

### 1.5 Success criteria (prototype acceptance)

The prototype is accepted when **all** of the following hold. Each maps to a test in §14.

| ID | Criterion | Measure |
|---|---|---|
| SC-01 | Fresh-machine setup produces a usable system | On a machine with Docker, Python and Node, `setup` + `seed` + `start` completes in ≤ 15 minutes (standard profile) and the login page lists all demo accounts |
| SC-02 | No oversold seat | Concurrency test: ≥ 1,000 claim attempts, concurrency ≥ 50, one hot bucket (cap 100): exactly 100 `CLAIMED`, rest `SEAT_LIMIT`; `occupied ≤ cap + approved_extra` always |
| SC-03 | One exclusive claim per student per cycle | Two institutions claim the same ID in parallel: exactly one `201`, one `409 CLAIM_CONFLICT` |
| SC-04 | Idempotent replay | Same `Idempotency-Key` + body replays the stored response (`Idempotent-Replay: true`); different body gives `422 IDEMPOTENCY_MISMATCH` |
| SC-05 | Every demo role completes its full workflow | All 15 demo scenarios in §11.7 pass as automated E2E tests |
| SC-06 | Dashboards are computed, not hardcoded | A test inserts a claim via API and the dashboard figure changes within 10 s (projector interval); `verify-seed` recomputes every aggregate from base tables with zero mismatches |
| SC-07 | Triple-Lock honesty | An enrolment becomes `VERIFIED` only after Lock 2 `PASSED/PROVISIONAL_OK` and Lock 3 `CONFIRMED`; unconfirmed claims become `UNCONFIRMED` at the deadline |
| SC-08 | Privacy | No Aadhaar-like value in any column or log; student-level data reachable by AICTE only via a case-gated, time-limited grant; every access audited |
| SC-09 | Tamper evidence | Modifying one audit row (demo script) makes `integrity-check` fail and report the first broken `seq`; changing an enrolment changes the Merkle root |
| SC-10 | Credentials | Tampered credential rejected; revoked credential returns `REVOKED`; verifier receives only purpose-allowed fields; replay after 5 minutes rejected |
| SC-11 | Authorization | Generated route×role test matrix passes: every denied combination returns 403/404 and is audited; no route without a declared permission |
| SC-12 | Resilience | Registry "down" mode makes claims `PENDING_VERIFICATION` (never silent pass); restoring it resolves them via the revalidation job; killing the worker loses no jobs |
| SC-13 | Honest labelling | A UI scan finds a `SIMULATED/MOCK` badge on every view showing registry, SMS, liveness, or feed-derived data |
| SC-14 | Seeded scale | Standard profile loads ≥ 60 institutions, ≥ 13,500 students, and the evaluation harness reports duplicate precision/recall against injected ground truth |

---

## 2. Analysis of the Uploaded Documents

### 2.1 Documents analysed

| # | Document | Used as |
|---|---|---|
| U1 | *APAAR Sentinel System Information Document (1).docx* (uploaded) | The single baseline. 9 sections: overview, features and NFRs, architecture, stack, database, rules/business logic, security, implementation, consistency review |
| (S1) | *Production Architecture and Design Report* (25 Oct 2026) | Referenced **by** U1 (governs technical detail). **Not supplied** as a separate file; used only through U1's content |
| (S2) | SIH submission deck (six slides) | Referenced **by** U1 (governs headline positioning). Not supplied separately |
| (S3) | Earlier documents D1 problem analysis, D2 solution plan, D3 platform specification | Referenced by U1/S1. Not supplied. The wording of PS-1 to PS-10 is therefore **not available**; themes inferred by U1 are used |

> The prompt says "all uploaded documents" but only **U1** was attached. The cross-check in this PRD is against U1 (and the source decisions it logs). Other APAAR files may exist on the author's machine but were not provided to this analysis and were not used. If they contain requirements not reflected in U1, they should be added via the change process in §2.7.

U1 notes that the original wording of FR-01…FR-70 is only available as numbers grouped under features F1–F18 and NFR rows. This PRD therefore defines its own testable requirements (§3) that cite those FR numbers by group (Appendix B maps all 70).

### 2.2 Consolidated requirement baseline

The source defines **18 functional features (F1–F18), 15 modules (M01–M15), 14 database schemas, 70 requirements, 10 problem-statement themes (PS-1…PS-10), 19 resolved conflicts (C-01…C-19), 20 information gaps (G-01…G-20), 12 assumptions (AS-01…AS-12) and 22 risks (R-01…R-22)**. The PRD consolidates them as follows.

| Source feature | PRD module(s) | Class in this PRD |
|---|---|---|
| F1 Multi-channel ingestion | Ingestion (REQ-ING), Integration (REQ-INTG) | MVP (TUS resumable upload, ClamAV, scheduled ERP sync, separate sandbox DB → Deferred/simulated) |
| F2 Onboarding and user admin | REQ-ONB | MVP-lite (MFA, bulk onboarding, recertification → Deferred) |
| F3 Seat ledger and Admission Gate | REQ-GATE, REQ-REF | MVP (full) |
| F4 APAAR validation, provisional IDs | REQ-IDN | MVP (registry is a labelled mock; Splink training → Deferred) |
| F5 Triple-Lock | REQ-TL | MVP (OTP via mock SMS; DigiLocker → Deferred) |
| F6 Rules engine and findings | REQ-RUL | MVP (RATIO_THRESHOLD kind → Deferred) |
| F7 Policy simulator | REQ-RUL-09 | MVP |
| F8 Event-sourced records, submission lifecycle | REQ-GATE, REQ-SUB | MVP (closure/merger continuity UI → Deferred, data link kept) |
| F9 Duplicate and transfer engine | REQ-DUP | MVP |
| F10 Intelligence and case management | REQ-INT | MVP (SHAP, three-way match, faculty ratio → Deferred) |
| F11 Dashboards and live updates | REQ-DASH | MVP (Realtime Broadcast → polling; MapLibre map → ranked state view) |
| F12 Reports and NLQ | REQ-RPT | Reports MVP; NLQ/briefs Deferred; report schedules Deferred |
| F13 Signed credentials | REQ-CRD | MVP (Enrolment Proof; other credential types data-ready, Deferred) |
| F14 Privacy-preserving verification | REQ-VER | MVP |
| F15 Student wallet, consent, corrections | REQ-STU | MVP |
| F16 Tamper evidence | REQ-AUD | MVP (WORM copy simulated) |
| F17 Access control and data protection | REQ-AUTH, REQ-ACL, REQ-SEC | MVP (KMS/HSM, MFA, BYOK → simulated/Deferred) |
| F18 Integration APIs and adapters | REQ-INTG | MVP with mocks (real adapters → Future) |

### 2.3 Conflicts, ambiguities and how this PRD handles them

**A. Conflicts already logged in the source (§9.1, C-01…C-19)** are adopted as resolved there. Those that most shape this PRD:

| Source ID | Resolution carried into this PRD |
|---|---|
| C-01 | Logical federation: central tables hold keyed hashes and statuses; personal data sits in a tenant-keyed encrypted vault |
| C-02 | One rule engine, three call sites (claim path, bulk/dry-run, submission aggregates) |
| C-03 | Exact duplicates blocked at claim; fuzzy matching asynchronous |
| C-04 | Operator dry-runs, institution admin commits (see P-C3 below for how this PRD applies it) |
| C-05 | Two state machines: Enrolment (event-sourced) and Submission; Locked = signed cut-off commitment |
| C-06/07 | Per-bucket gate with approved exceptions; gate on occupied seats, also keep cumulative admitted |
| C-09 | Statutory basis for counting; consent only for sharing |
| C-10 | Safe query builder only; no text-to-SQL (NLQ itself is Deferred here) |
| C-17 | Trust Index prioritises reviews only; no automatic penalty |
| C-19 | Shared phones are a weighted review signal, never an automatic flag |
| Source §9.1 #1–#10 | Supabase Auth/Storage; Broadcast not SSE; Supavisor not PgBouncer; four concurrency classes; six verifier tiers; M10 issues credentials; report phase order P0–P6; OTP is the working channel; **sequence diagrams, DDL and API examples were absent from the source and are rebuilt here from prose** (§5, §7, §8) |

**B. New conflicts and tensions created by the prototype scope (this PRD).** None of these are source requirements; each is resolved by a decision.

| ID | Tension | Resolution |
|---|---|---|
| P-C1 | Source scope stack is Supabase + FastAPI + Celery + Redis + Grafana; the brief defers Redis and Grafana and asks for simple setup | [DEC-01..03] Plain PostgreSQL 16 + FastAPI; Postgres-backed job queue and scheduler; Redis/Celery/Grafana-family deferred behind interfaces |
| P-C2 | Source login = Supabase Auth with TOTP MFA mandatory; brief demands `1234` demo accounts and easy role switching | [DEC-05, DEC-08] Local JWT auth with demo accounts; MFA Deferred; signing actions use a password step-up |
| P-C3 | C-04 says an operator uploads and an institution admin commits, but the permission matrix also lets IA upload | [DEC-24] IA may upload, but the committer must differ from the uploader (DB CHECK); a second IA is seeded for one institution to demonstrate it |
| P-C4 | AS-07 says only new admissions from go-live are in scope, but the policy simulator and trends need "past cycles" | [DEC-11] The seed includes a **synthetic back-filled 2025-26 cycle** (first-year admissions only), labelled as seed history, so the simulator, trends and cohort proofs have data. No pre-go-live student rights are implied |
| P-C5 | Source: erasure by crypto-shredding the **per-institution** data key; a single student's erasure cannot destroy a key shared by thousands | [DEC-15] Record-level erasure: ciphertext and nonce nulled and `erased_at` set on that student's vault record; key-version destruction stays a Future capability |
| P-C6 | Mock APAAR IDs would be 12 digits, but the source forbids any Aadhaar-like value anywhere (also checked by an automated scan) | [DEC-17] Mock IDs have a recognisable synthetic format (`99` prefix, Luhn check); the scan allows only that format in the APAAR field and, in the hashed form, nowhere else |
| P-C7 | Source "India state/district heat map" uses MapLibre and official GeoJSON | [DEC-26] MVP renders a state/district heat **table and treemap** (ECharts); the geographic map is Deferred |
| P-C8 | Source: 100,000-row file validated; 50,000 students over 200 institutions | [DEC-11, DEC-18] Three seed profiles; default `standard` (60 institutions, ~14,000 students) resets in minutes; `full` reproduces the source scale. Upload limit is configurable (default 20,000 rows) |
| P-C9 | Source retains a separate **Sandbox** deployment with its own database | [DEC-23] Dry-run (`/claims:validate` and upload dry-run) provides the same "live rules, no writes" behaviour; a separate sandbox DB is Deferred |
| P-C10 | The deck lists Grafana among the twelve highlighted technologies | [DEC-03] Deferred by instruction; replaced by an in-app Operations page and structured logs; documented as a known deviation from the deck |
| P-C11 | Source says the OTP should go to the registry-held mobile; the mock registry must therefore hold mobiles | [DEC-19] Mock registry stores a mobile per APAAR ID; the institution-supplied number is the LOW-trust fallback |

### 2.4 Assumptions

**Carried from the source (AS-01…AS-12):** AS-01 registry returns existence, status and name/DOB match (mock honours this) · AS-02 bucket types and caps are dated reference data · AS-03 statutory basis for counting · AS-04 India-region hosting (not applicable to local prototype) · AS-05 OTP preferably to registry mobile; institution number is LOW trust · AS-06 hundreds of institutions / millions of rows with 10× headroom (design target; prototype is smaller) · AS-07 new admissions only · AS-08 generic certificate issuer · AS-09 synthetic data only · AS-10 delegated institution signing keys with MFA step-up (simulated) · AS-11 NLQ optional and last (Deferred) · AS-12 proposed SLOs (prototype targets in NFR §3.22).

**Added by this PRD:**

| ID | Assumption |
|---|---|
| PA-01 | The prototype runs on one developer machine (Windows, macOS or Linux) with Docker available for PostgreSQL |
| PA-02 | The prototype's "today" is the seed anchor date (default: the day of seeding); all seeded dates are relative to it so deadlines stay coherent |
| PA-03 | Seat-bucket caps, bucket types and rule thresholds in the seed are **illustrative synthetic values**, not Approval Process Handbook values (G-02) |
| PA-04 | AICTE regions are modelled as six illustrative regions (North, South, East, West, Central, North-East) |
| PA-05 | A single academic cycle is "current" at a time (2026-27); 2025-26 is the closed prior cycle |
| PA-06 | Browser support: current Chrome, Edge, Firefox, Safari; viewport ≥ 360 px |

### 2.5 Unresolved questions (carried and new)

Carried from the source (§9.2) with the **default used in the prototype**: G-01 real APAAR format/API → pluggable validator + mock · G-02 Handbook seat rules → illustrative data · G-03 definition of "actual enrolment" → gate on occupied, report both · G-04 foreign nationals → passport/OCI keyed hash + last four · G-05 certificate issuer → generic issuer · G-06 DPDP basis/retention/minors → statutory basis for counting, consent for sharing, guardian flow · G-07/09 pre-go-live students, PGDM → new admissions only, PGDM same model · G-08 central records vs hybrid → logical federation · G-10 volumes/SLOs → prototype targets · G-11 feeds → mock · G-12/13 deadline/phoneless/minors/unconfirmed policy → 30 days, flag only, seat stays occupied until a reviewer acts · G-14 what CONFLICT discloses → resolution reference + public contact only · G-15 verifier onboarding → consent request through student, QR, share link · G-17/18 hosting/SSO, SMS channel → not applicable / mock · G-19/20 report formats, closure/merger → generic templates, name history + `merged_into`.

New open questions raised by this PRD (must be answered before production, not before the prototype):

| ID | Question | Prototype default |
|---|---|---|
| PQ-01 | Should an unadmitted `CLAIMED` seat expire automatically? | No expiry. AICTE reviewer or the institution can release it. A "stale claims" widget (claimed > 45 days, not admitted) is shown |
| PQ-02 | Who may confirm assisted (phoneless) Lock 3 — IA only, or a university facilitator role? | IA only, with reason code and audit; flagged with `channel_trust = LOW` |
| PQ-03 | Does a CONCURRENT_OK enrolment (ODL/online/part-time) count toward the exclusive-seat check at another institution? | No; recorded and eligible for the duplicate engine as `FT_PLUS_ODL` |
| PQ-04 | Should AICTE be able to release a seat without institution consent? | Yes (reviewer, with reason; opens a case) per the "gate abuse" threat control |
| PQ-05 | Is a student found with two APAAR IDs auto-merged? | Never (source). Deferred: flagging to the registry owner (needs the real registry) |

### 2.6 Implementation decisions register

All decisions below are **[DEC-nn]**, taken for prototype practicality. Each states the source behaviour it replaces and how the original is restored later (full register of simplifications in Appendix A).

| ID | Decision | Replaces (source) | Reason | Restore later by |
|---|---|---|---|---|
| DEC-01 | Single PostgreSQL 16 (Docker) accessed by FastAPI; **no Supabase platform services** (Auth, Storage, Realtime, Supavisor, PostgREST) | Supabase PostgreSQL stack | One-command setup; all integrity features (row locks, partial unique index, CHECK, RLS, grants) are plain PostgreSQL | Point `DATABASE_URL` at Supabase; swap `AuthProvider`/`ObjectStore` adapters |
| DEC-02 | **Postgres-backed job queue** (`ops.jobs`, `SELECT … FOR UPDATE SKIP LOCKED`, leases, retries) with a worker process and a scheduler loop | Celery 5 + Redis 7 + Celery Beat | Redis deferred. The source already says PostgreSQL job tables are the truth and handlers must be idempotent | Add a Celery/Redis `JobQueue` implementation; same handler registry |
| DEC-03 | **Grafana, Prometheus, Loki, Tempo, Sentry, Alertmanager deferred**. Replaced by structured JSON logs (PII-redacted), `/healthz` `/readyz`, in-memory request timing, and an in-app **Operations** page reading DB-derived health (queue depth, oldest job, outbox lag, pending registry checks, sealer lag) | Observability stack (FR-65) | Deferred by instruction | Add OpenTelemetry exporter and `/metrics`; dashboards in Grafana |
| DEC-04 | **One React SPA with four role-based portals** (Institution, AICTE, Student, Verifier) as lazy-loaded route groups; public verifier page at `/v/:ref` | Four separately built portals | Simpler build; same UX separation | Split builds per portal |
| DEC-05 | **Local JWT authentication** (HS256, 60 min), `iam.sessions` for revoke/idle timeout, argon2id password hashes, seeded demo accounts with password `1234`, lockout after 5 failures | Supabase Auth (PKCE, ES256 JWT) | Brief requires `1234` accounts and simple auth | Swap `AuthProvider` for Supabase/OIDC |
| DEC-06 | **Role set reduced** to IA, IO, AR, AS, ST (with guardian mode), VU, CL + public (PUB). **AA merged into AR** (read-only analyst duties become a subset of AR). **AU deferred** (audit read access given to AR and AS, read-only) | 10 roles in source §7.1 | Essential roles only | Re-split roles in `iam.roles` (data change) |
| DEC-07 | **Polling with `as_of`** (TanStack Query `refetchInterval` 15 s, manual refresh) | Supabase Realtime Broadcast | Realtime is a Supabase service; no student data over push anyway | Add SSE/Broadcast `EventBus` implementation |
| DEC-08 | **MFA (TOTP) deferred**; **step-up** = re-enter password within the session, yielding a 5-minute `step_up` token required for signing actions (issue credential, sign commitment, publish rule set, countersign) | Mandatory TOTP MFA (aal2); MFA step-up | Brief requires simple auth | Enable TOTP; replace the step-up check with `aal2` |
| DEC-09 | **Software key provider**: ES256 keys and per-institution data keys are generated locally, wrapped (AES-256-GCM) by `MASTER_KEY` from env; the "WORM bucket" is an append-only local directory (`data/worm/`) labelled `SIMULATED` | Cloud KMS/HSM, WORM bucket in a separate account | No cloud account in a prototype | `KeyProvider` implementation for KMS/HSM; object-lock bucket |
| DEC-10 | **Mock services app** (separate FastAPI process on :8002) provides: APAAR registry, SMS gateway (+dev inbox), AICTE master export, ABC/exam activity feed. Core API talks to it over HTTP with timeouts and a circuit breaker | Real external systems | Genuine adapter behaviour (latency, outage) without credentials | Replace adapter base URL/implementation |
| DEC-11 | **Seed profiles**: `minimal` (tests), `standard` (default), `full` (source scale). Includes a back-filled 2025-26 cycle | Source "50,000 students / ~200 institutions" | Fast resets; simulator needs a past cycle | Use `full` |
| DEC-12 | **Static match weights**; Fellegi–Sunter/Splink training and DuckDB are Deferred. Blocking keys + Bloom-filter Dice scoring are MVP | Splink batch training | Needs labelled data volume and ML ops | Train Splink on reviewer labels |
| DEC-13 | **reportlab** for PDFs (pure Python) instead of WeasyPrint; XlsxWriter and segno kept | WeasyPrint | WeasyPrint needs native GTK/Pango libraries that are painful on Windows | Switch back to WeasyPrint in Docker |
| DEC-14 | Institution risk uses **robust z-scores + Isolation Forest** (scikit-learn, enabled when ≥ 30 institutions exist); explanations are **per-feature contribution tables**, not SHAP | SHAP explanations | Dependency weight; contribution tables are explainable and honest | Add SHAP TreeExplainer |
| DEC-15 | **Record-level erasure** for data-subject erasure requests (see P-C5) | Crypto-shred of key version | See P-C5 | Per-subject keys or key-version shredding |
| DEC-16 | **Clock service**: all business time comes from `Clock.now()` with an optional demo offset in `ops.system_config` (`demo_clock_offset_seconds`), editable only in demo mode | Authoritative cloud time source (FR-67) | Needed to demo 30-day deadlines in minutes | Remove the offset (config) |
| DEC-17 | **Mock APAAR format**: 12 digits, prefix `99`, last digit Luhn-valid, and **not** Verhoeff-valid (so it cannot be confused with an Aadhaar number); validators are pluggable [SRC G-01] | Real APAAR format (unknown) | Real spec not public | New `ApaarFormatValidator` plug-in |
| DEC-18 | Upload via **single `PUT` of the file body** (≤ 25 MB, ≤ 20,000 rows by default) to the local `ObjectStore`; scanning runs magic-byte, size, zip-bomb, row-count and CSV-formula checks; **malware scanning is simulated and recorded as `NOT_PERFORMED`** | TUS resumable upload, ClamAV | Infra weight | TUS server + ClamAV sidecar |
| DEC-19 | Mock registry holds a mobile per ID; **OTP delivery goes to the mock SMS gateway** and is displayed in a **Dev SMS Inbox** page. OTP: 6 digits, 10-minute validity, 5 attempts, 3 resends/hour; optional fixed OTP via `SMS_FIXED_OTP` | DLT-registered SMS gateway | No real SMS | Real gateway adapter |
| DEC-20 | **Webhooks** delivered by queue jobs (HMAC-SHA256 signed, retried with backoff for 24 h then dead-lettered with replay). A local **webhook sink** endpoint exists for demos | Same, plus real ERP endpoints | Demo without external ERP | Real ERP endpoints |
| DEC-21 | Database roles: `app_migrator` (DDL, seed) and `app_runtime` (RLS applies). Roles `app_agg`, `app_drilldown`, `app_worker`, `app_verifier`, `app_sealer`, `app_auditor_ro` are **enforced in the code layer** by separate repository modules and, where cheap, by `SECURITY DEFINER` functions; full DB-role split is Future | Eight DB roles | Setup simplicity | Create the roles; one `DATABASE_URL_*` each |
| DEC-22 | **Name search uses blind-index tokens** (HMAC of normalised name tokens, stored on `core.enrolments.name_tokens`) so institutions can search by whole name token without decrypting everything | Not specified | Search must work over encrypted PII | n/a |
| DEC-23 | **Dry-run replaces a separate sandbox environment** (see P-C9) | Sandbox DB (FR-06) | Simplicity | Sandbox deployment |
| DEC-24 | **Commit ≠ upload**: `committed_by ≠ uploaded_by` enforced by CHECK (see P-C3) | C-04 | Strict separation of duties | n/a |
| DEC-25 | **Bulk template v1** columns fixed in §8.5; client pre-validation uses Papa Parse + SheetJS in a Web Worker with Zod generated from the same template JSON that the server serves | Same (SheetJS, Papa Parse, Zod) | n/a | n/a |
| DEC-26 | **State/district heat table + treemap** instead of a MapLibre geographic map | MapLibre + GeoJSON | No official GeoJSON bundled; table is functionally equivalent | Add MapLibre layer |
| DEC-27 | **Pagination**: keyset cursors on all unbounded lists; `sort` limited to an allow-list of indexed columns (always with `id` tiebreak); optional `include_total=true` for counts | Keyset pagination | Matches source; UI shows page controls using cursors and total | n/a |
| DEC-28 | **Rate limiting** is an in-process token bucket behind a `RateLimiter` interface (per-user, per-client, per-IP for public) | Redis token buckets | Redis deferred | Redis `RateLimiter` |
| DEC-29 | **i18n**: message catalogues in English and Hindi for student-facing screens, SMS templates and error messages; other regional languages Deferred | English, Hindi, configured regional languages (FR-66) | Prototype scope | Add catalogues |
| DEC-30 | **Credential types**: only *Enrolment Proof* is issued; *Year Completion, Degree/Diploma, Transcript Summary* exist as enum values and templates but need academic-result data that does not exist in this system | Four credential types | No result data in scope | Add issuers/data feeds |
| DEC-31 | **Guardian** is not a separate role: a student-role user may have `guardian_links` and act on behalf of linked minors ("guardian mode") | Guardian OTP flow | Role minimisation | n/a |
| DEC-32 | **Demo tools** (registry mode, clock, run-job-now, reset, SMS inbox) exist only when `DEMO_MODE=true` and are restricted to the AICTE system admin; absent otherwise | n/a | Brief requires realistic demos of outages and deadlines | Remove via config |
| DEC-33 | **Operational defaults not stated in the source** (all stored in `ops.system_config`, changeable): identity-fix deadline 14 days; case response window 14 days; appeal window 15 days; elevation default 30 min, maximum 4 h; confirmation reminders at day 7 and 21; stale-claim marker at 45 days; JWT 60 min, idle timeout 30 min, lockout 5 failures / 5 min; OTP limits (DEC-19); orphan-lead period 30 days; upload limits (DEC-18); API rate-limit defaults (§8.1) | Source says "configurable" or is silent | A usable prototype needs concrete numbers | Re-baseline with AICTE after pilot data |

### 2.7 Change control for this PRD
Any requirement discovered in an unsupplied source document is added as `REQ-…` with its source reference, classified MVP/Deferred/Future, and its test added to §14. Silent additions are not permitted.

---

## 3. Functional and Non-Functional Requirements

Every requirement below is **MVP** unless Appendix A says otherwise. "Src" cites the source (FR-xx groups per Appendix B, F#, C-xx, §). Each module lists its **dependencies** and a **Done when** line (module-level acceptance); story-level acceptance is in §12.

### 3.1 Authentication and sessions (REQ-AUTH) — F17

*Depends on:* `iam` schema, argon2 hashing, Clock. *Done when:* every demo account can sign in, switch role, sign out, and a revoked/expired session is rejected on the next request.

| ID | Requirement | Src |
|---|---|---|
| REQ-AUTH-01 | `POST /auth/login {username,password}` MUST return a bearer JWT and profile on success; on failure a generic `401 INVALID_CREDENTIALS` (no hint whether the user exists). After 5 consecutive failures the account locks for 5 minutes (`423 ACCOUNT_LOCKED` with `retry_after`); success resets the counter | §7.3, DEC-05 |
| REQ-AUTH-02 | JWTs MUST be HS256 signed with `JWT_SECRET`; verification MUST accept only HS256 (reject `none` and any other `alg`). Claims: `iss, aud ("core-api"\|"verify-api"), sub, sid, utype, roles[], scope{type,value}, inst, iat, exp`. Lifetime `JWT_TTL_MINUTES` (default 60) | §7.4 (1), DEC-05 |
| REQ-AUTH-03 | Each login MUST create an `iam.sessions` row. Each authenticated request MUST update `last_seen_at` (at most once per minute). Idle time > 30 min or `revoked_at` set MUST yield `401 SESSION_EXPIRED`. Users MUST be able to list their sessions and sign out any of them | §7.3 |
| REQ-AUTH-04 | When `DEMO_MODE=true`, `GET /auth/demo-accounts` MUST list demo accounts (username, display name, role, institution, one-line description, **no password**) plus a `password_hint`. The login page MUST show them as "Sign in as" cards, and the app header MUST offer a role switcher that signs out and signs in as another demo account **through the normal `/auth/login`** (no server-side bypass) | Brief §2 |
| REQ-AUTH-05 | Signing actions (issue credential, sign cohort commitment, countersign, publish rule set, revoke credential, release seat) MUST require `X-Step-Up-Token` obtained from `POST /auth/step-up {password}` (valid 5 min, bound to the session). Missing token → `401 STEP_UP_REQUIRED`; the UI shows a re-enter-password dialog | §7.3, AS-10, DEC-08 [SIM of MFA step-up] |
| REQ-AUTH-06 | `POST /auth/token` (OAuth2 `client_credentials`) MUST issue a 15-minute bearer token carrying the client's scopes (e.g. `claims:write`, `claims:validate`, `upload:write`, `feeds:write`). Client secrets are stored hashed, shown once at creation, rotatable; `last_used_at` tracked | §7.3, FR-59 |
| REQ-AUTH-07 | **Production guard:** the API MUST refuse to start when `APP_ENV=production` and any of: `DEMO_MODE=true`, any `iam.users.is_demo = true`, default/placeholder `JWT_SECRET`/`MASTER_KEY`/`IDENTITY_HMAC_KEY`. The message names the problem | Brief §2 |
| REQ-AUTH-08 | Passwords MUST be stored as argon2id hashes (including demo accounts); never logged, returned, or placed in URLs. Demo password = `SEED_DEMO_PASSWORD` (default `1234`) | Brief §2 |
| REQ-AUTH-09 | Students and guardians sign in with username + password like other users [DEC-05]. The OTP in this system is used for **Lock 3 confirmation**, not for sign-in (a deliberate difference from source §7.3 which uses phone OTP login) | DEC-05 |

### 3.2 Access control (REQ-ACL) — F17, FR-51…54, FR-57, FR-58

*Depends on:* AUTH, `iam.user_roles`, RLS migrations. *Done when:* the generated route×role matrix test (SC-11) and cross-tenant tests pass.

| ID | Requirement | Src |
|---|---|---|
| REQ-ACL-01 | Every route MUST declare a permission (or `public`). The app MUST fail at startup if a route lacks one. A CI test compares OpenAPI paths and scopes to the §4.3 matrix | §8.10 |
| REQ-ACL-02 | Each request MUST pass four checks in order: (1) JWT valid; (2) role/scope — sensitive permissions re-checked against `iam.user_roles` on every request so a stale token cannot widen access; (3) tenant check (`inst` claim equals the resource's institution); (4) PostgreSQL RLS using `SET LOCAL app.institution_id / app.scope / app.actor_id` taken from the verified token, never from client input | §7.4 |
| REQ-ACL-03 | Denials MUST return `403` with a stable code and be audited. Where the existence of a resource must not be revealed (other tenants' records, unknown or unconsented credentials/students), return `404` (or the neutral verifier response) | §7.4 |
| REQ-ACL-04 | RLS MUST be **enabled and forced** on every table having `institution_id`; a startup self-check (`rls_check`) fails the build/start if any such table lacks it | §5.1, §8.12 |
| REQ-ACL-05 | **Aggregate by default:** AICTE dashboards MUST read only `agg.*` through a repository module that has no access to `core.*`/`pii.*` code paths. Student-level rows MUST NOT appear in `agg` | §7.5, DEC-21 |
| REQ-ACL-06 | **Elevation:** `POST /me/elevations {case_id, justification ≥ 30 chars, scope, duration_minutes ≤ 240 (default 30)}` creates a `pii.access_grants` row. Student-level endpoints for AICTE MUST verify an unexpired grant matching case and scope. Every search/view/export under a grant is audited with case and justification. More than 200 revealed records per user per hour raises an access-volume alert | §7.5 |
| REQ-ACL-07 | **Regional scope:** an AICTE user whose role scope is `REGION` or `STATE` MUST see only institutions within it in every list, aggregate, alert and case view | §7.1, F2 |
| REQ-ACL-08 | **Separation of duties** enforced in the database (CHECK/trigger), not only the UI: upload committer ≠ uploader; submission `approved_by` ≠ `submitted_by`; rule set `published_by` ≠ `drafted_by`; seat-exception decider ≠ requester | C-04, §6.7, §6.9 |
| REQ-ACL-09 | An institution admin MUST NOT grant AICTE roles or access another institution. Cross-tenant and cross-role tests are part of CI | §7.2 |

### 3.3 Onboarding and user administration (REQ-ONB) — F2, FR-08, FR-70

*Depends on:* REF, AUTH. *Done when:* a new institution can register, be approved, and its admin can create an operator who can sign in.

| ID | Requirement | Src |
|---|---|---|
| REQ-ONB-01 | `POST /onboarding/institutions` (public, rate-limited): `aicte_id` MUST exist in `ref.institutions` with status `PENDING_APPROVAL` or `ACTIVE` without an admin; collects nodal officer name, designation, official email, phone | F2 |
| REQ-ONB-02 | Nodal-officer identity check is **[SIM]**: compare the submitted email domain and name with the master record; store `MATCH/MISMATCH` plus the label "Simulated check — production uses verified officer attestation" | F2 |
| REQ-ONB-03 | AICTE reviewer approves/rejects with reason; approval creates the institution admin user (`ia.<slug>`, demo password in demo mode), sets the institution `ACTIVE`, audited | F2 |
| REQ-ONB-04 | Institution admin creates/deactivates IO and IA users **within own institution only** | FR-70 |
| REQ-ONB-05 | AICTE system admin creates/deactivates AICTE users (AR/AS) with scope; cannot change own role | F2 |
| REQ-ONB-06 | Deactivated users' sessions are revoked immediately | §7.3 |

### 3.4 Reference data and configuration (REQ-REF) — M02

*Depends on:* mock AICTE master. *Done when:* `sync_master` is idempotent, the gate reads only local tables, and changing an intake version updates ledger caps with a `CAP_CHANGE` entry.

| ID | Requirement | Src |
|---|---|---|
| REQ-REF-01 | `AicteMasterAdapter.sync()` MUST load institutions, programmes, dated intake versions, bucket types and caps from the mock master export. It is idempotent (upsert by `aicte_id`, programme code, `(programme, year, version)`). The gate MUST never call the master synchronously | §3.5 |
| REQ-REF-02 | Seat bucket types MUST be data (`ref.seat_bucket_types`); no regulatory percentage is hard-coded (G-02) | §6.3, AS-02 |
| REQ-REF-03 | Intake versions are dated (`effective_from/to`); a new version creates `CAP_CHANGE` ledger entries. A cap lowered below current `occupied` MUST be accepted but flagged as a Warning finding and block further claims (no eviction) | §6.3 |
| REQ-REF-04 | Submission windows per academic year: `opens_at`, `closes_at`, `cutoff_at`, evaluated server-side in UTC and displayed in IST | FR-67, F8 |
| REQ-REF-05 | Window extensions: institution admin requests (reason, new close); AICTE reviewer decides; approved extension raises the institution-specific close | F8, FR-05 |
| REQ-REF-06 | `ref.enrolment_exception_rules` lists approved dual-programme pairs that map to `EXCEPTION_APPROVED` | §5.4, F9 |
| REQ-REF-07 | `ops.system_config` holds thresholds with defaults: `seat_warn_pct=90`, `seat_stop_pct=100`, `confirm_deadline_days=30`, `orphan_lead_days=30`, `small_cell_min=5`, `verifier_answer_ttl_seconds=300`, `registry_timeout_ms=2000`, `otp_ttl_minutes=10`, `trust_weights{…}`, `late_submission_flag=true` | §6.2, §6.8 |
| REQ-REF-08 | Programme `successor_programme_id`, institution `merged_into_id` and `former_names` are stored and displayed read-only (continuity UI is Deferred) | F8, FR-68 |
| REQ-REF-09 | Lookups for gender, category (`GEN, OBC, SC, ST, EWS`), nationality codes, states, districts, regions are reference data | §6.1 |

### 3.5 Claim gate, seat ledger and enrolment events (REQ-GATE) — F3, F8, FR-14…19

*Depends on:* REF, IDN (format/registry adapter), RUL (claim-path rules), AUD hook, outbox. *Done when:* SC-02, SC-03, SC-04 pass and the claim outcomes table below is covered by tests.

| ID | Requirement | Src |
|---|---|---|
| REQ-GATE-01 | `POST /claims` MUST execute: authenticate/authorise; require `Idempotency-Key`; run claim-path rules; normalise the identifier; call the registry adapter **outside any DB transaction**; open one bounded transaction (statement and lock timeouts) that upserts student and identity key, locks the seat-bucket row, inserts the enrolment, checks cap, writes ledger entry, enrolment event, verification row, confirmation request, encrypted PII record, audit entry and outbox row; commit; return claim ID and warnings | §6.2, §8.2 |
| REQ-GATE-02 | Outcomes MUST match the table in §5.1.2 (CLAIMED, CONFLICT, SEAT_LIMIT, DUPLICATE_SAME_INSTITUTION, INVALID, PENDING_VERIFICATION, CONCURRENT_OK, EXCEPTION_APPROVED) with the stated HTTP codes | §6.2 |
| REQ-GATE-03 | **Exclusivity** MUST be enforced by the partial unique index `ux_enrol_exclusive` on `(academic_year, key_version, identity_key)` where `concurrency_class='EXCLUSIVE' AND status IN ('CLAIMED','ADMITTED')`. Overlap with an earlier cycle is a warning and review candidate, never a block | §5.4 |
| REQ-GATE-04 | **No oversell:** the seat-bucket row MUST be locked (`SELECT … FOR UPDATE`) during the claim; `occupied` updated in the same transaction; table CHECK `occupied BETWEEN 0 AND cap + approved_extra` MUST never trip in the concurrency test | §5.4, FR-16 |
| REQ-GATE-05 | When `occupied/(cap+approved_extra)` reaches `seat_warn_pct` the response MUST carry warning `SEAT_90_PERCENT` and an alert is created for the institution and AICTE (notification + dashboard alert, once per bucket per threshold crossing) | §6.2 |
| REQ-GATE-06 | At 100% a claim without an approved exception MUST return `409 SEAT_LIMIT` with bucket, cap, occupied and whether an exception can be requested | §6.2 |
| REQ-GATE-07 | **Seat exceptions:** institution admin requests (extra seats, justification, evidence note); AICTE reviewer approves/rejects with reason; approval increases `approved_extra`, writes an `EXCEPTION_GRANT` ledger entry, expires at `expires_at`. Overflow in a regular bucket requires an approved exception; supernumerary buckets have their own caps | C-06, §6.2 |
| REQ-GATE-08 | **Withdrawal** writes a `RELEASE` ledger entry (effective date, reason) and decrements `occupied`; `cumulative_admitted` is never decremented. A re-fill does not double count | §6.3, C-07 |
| REQ-GATE-09 | AICTE reviewer MAY release a `CLAIMED`/`ADMITTED` seat (reason mandatory, step-up, case opened, institution notified) — the "gate abuse" control | §7.9, PQ-04 |
| REQ-GATE-10 | `Idempotency-Key` (16–64 chars): same key+body within 7 days replays the stored response with `Idempotent-Replay: true`; same key different body → `422 IDEMPOTENCY_MISMATCH`; same key in flight → `409 REQUEST_IN_PROGRESS` | §8.10 |
| REQ-GATE-11 | Every attempt MUST be written to `core.claim_attempts` (outcome, institution, programme, source, request id; the conflicting institution is stored but never exposed to the caller) | §5.3 |
| REQ-GATE-12 | `CONFLICT` MUST return only a `resolution_ref` and the other institution's **public** nodal contact (or an AICTE resolution reference) — never student data | G-14, §6.2 |
| REQ-GATE-13 | `POST /claims:validate` MUST run all claim-path logic and registry diagnosis with **no write and no seat consumed**, returning the outcome the real claim would produce | FR-06, [DEC-23] |
| REQ-GATE-14 | Concurrency classes: `EXCLUSIVE` (full-time), `CONCURRENT_OK` (ODL, online, part-time), `EXCEPTION_APPROVED`, `TRANSFER_PENDING`; only `EXCLUSIVE` rows fall under the unique index | §5.4, S9.1 #5 |
| REQ-GATE-15 | A claim creates an enrolment in status `CLAIMED`. `POST /enrolments/{id}/admit` (institution admin) moves it to `ADMITTED` (sets `admission_date`, event `ADMIT`, increments `cumulative_admitted`). The claim body MAY set `admit_now=true` to do both in one request | §1.4, F8 |
| REQ-GATE-16 | Same student, same institution, live exclusive claim already exists → `DUPLICATE_SAME_INSTITUTION` (`200` with existing claim ID, `duplicate=true`), never a second row. A second unique index `(student_id, programme_id, academic_year)` over live statuses covers concurrent-class programmes | §6.2 |
| REQ-GATE-17 | Every claim and decision records `rule_set_id` of the version that evaluated it | §6.1 |
| REQ-GATE-18 | History is event-sourced: `core.enrolment_events` is append-only (no UPDATE/DELETE grants); `core.enrolments` holds current state; corrections are new `CORRECT` events with a reason | F8, §5.1 |
| REQ-GATE-19 | Event types: `CLAIM, ADMIT, WITHDRAW, TRANSFER_OUT, TRANSFER_IN, CORRECT, SEAT_RELEASE, IDENTITY_UPGRADE, VERIFICATION_CHANGE`; payloads contain no PII | §5.3 |
| REQ-GATE-20 | A claim whose registry result is unavailable MUST succeed as `PENDING_VERIFICATION` (consumes the seat), be retried with jittered backoff by the revalidation job, appear in a dashboard counter, and be bounded by the deadline — never a silent pass | §6.2, §8.11 |

### 3.6 Identity validation and provisional IDs (REQ-IDN) — M04, F4, FR-09…13

*Depends on:* mock registry, PII vault. *Done when:* injected mismatch types in the seed receive the right diagnosis and NOGEN/REFUSED/FOREIGN students are counted and checked.

| ID | Requirement | Src |
|---|---|---|
| REQ-IDN-01 | **Hygiene:** strip spaces/hyphens, keep leading zeros, detect scientific notation from spreadsheet conversion (e.g. `9.9E+11`), uppercase, then validate format and checksum with the pluggable validator ([DEC-17] for the mock format) | §6.4 |
| REQ-IDN-02 | **Aadhaar guard:** reject any 12-digit, Verhoeff-valid number supplied in a field not meant for it (and in the APAAR field if it fails the mock format), with message code `AADHAAR_SUSPECTED` advising removal. Aadhaar is never stored, logged, or printed | §7.6, FR-51 |
| REQ-IDN-03 | **Registry adapter:** HTTP call to the mock registry with 2 s timeout and a circuit breaker (opens after 5 consecutive failures, half-open after 30 s). Response: existence, status, name and DOB (for comparison only), mobile (for OTP routing) | §3.5, §8.2 |
| REQ-IDN-04 | **Name normalisation (Indic-aware):** expand initials against the registry name, collapse variants (Mohd/Md/Mohammed), transliterate Devanagari and Tamil to canonical Latin (`indic-transliteration`), generate phonetic keys (`jellyfish`) | §6.4 |
| REQ-IDN-05 | **Field comparison:** name similarity (RapidFuzz token set + phonetic), DOB exact / day-month swap / two-digit-year error, mobile where supplied. Defaults: name ≥ 0.92 pass, 0.80–0.92 probable, < 0.80 mismatch | §6.4 |
| REQ-IDN-06 | **Diagnosis** with field-level message and suggested fix, e.g. "Name matches at 0.94; DOB is a day-month swap; correct the institution record" | §6.4, FR-10 |
| REQ-IDN-07 | **Status taxonomy:** `VERIFIED, PROBABLE_MATCH, PENDING_FIX, PENDING_VERIFICATION, INVALID, NOGEN, CONSENT_REFUSED` with the treatments in source §6.4 (INVALID is rejected with fixes; PENDING_FIX gives the institution a fix-it list and deadline) | §6.4 |
| REQ-IDN-08 | **Provisional IDs** for `NOGEN`, `REFUSED`, `FOREIGN`: random token, expiry, compliance deadline; passport/OCI numbers stored only as keyed hash + last four characters; the student is still counted and checked | C-08, §5.3 |
| REQ-IDN-09 | **Upgrade path:** `POST /provisional-ids/{id}/upgrade` adds a new identity key (real APAAR) to the same student record, writes `IDENTITY_UPGRADE`, preserves history; if the real ID is already exclusively claimed elsewhere the upgrade yields `CONFLICT` | §6.4 |
| REQ-IDN-10 | **Revalidation job:** re-checks `PENDING_VERIFICATION` and `PENDING_FIX` enrolments (registry recovered or institution corrected); no long negative caching | §8.11, FR-13 |
| REQ-IDN-11 | `POST /identity/check` returns a diagnosis and stores nothing (but logs a `registry_checks` row of source `CHECK_ONLY` without PII) | §8.10 |
| REQ-IDN-12 | APAAR is never a hard gate: records lacking an APAAR ID enter via provisional ID or `PENDING_FIX` and are never rejected solely for that | O4, C-08 |

### 3.7 Ingestion: bulk upload and ERP API (REQ-ING) — F1, FR-01…07, FR-61

*Depends on:* GATE, RUL, IDN, job queue, ObjectStore. *Done when:* the S3 demo scenario (§11.7) passes and a re-upload of the same file creates no duplicates.

| ID | Requirement | Src |
|---|---|---|
| REQ-ING-01 | A versioned **template** (JSON schema + CSV download) is served at `GET /templates/enrolment/latest`; both the browser (Zod) and server (Pydantic) validate from that one definition | FR-01, DEC-25 |
| REQ-ING-02 | **Pre-validation in the browser** (Papa Parse for CSV, SheetJS for XLSX, in a Web Worker) reports row/column errors before upload; this never replaces server validation | F1 |
| REQ-ING-03 | **Job lifecycle:** `UPLOADING → SCANNING → QUEUED → PARSING → VALIDATING → READY_TO_COMMIT → COMMITTING → COMPLETED/PARTIAL` (+ `FAILED`, `CANCELLED`); progress via `GET /upload-jobs/{id}` (UI polls every 2 s) | §8.3 |
| REQ-ING-04 | **File safety:** extension and magic-byte check (CSV or XLSX only), size ≤ 25 MB, rows ≤ `MAX_UPLOAD_ROWS` (20,000), XLSX: reject macros (`vbaProject.bin`), zip-bomb guard (uncompressed ≤ 200 MB, ≤ 200 entries), CSV-formula neutralisation (cells beginning `= + - @` are flagged Warning and prefixed `'` before staging). Malware scan is **[SIM]**: recorded as `NOT_PERFORMED` with the label shown in the UI | §7.10, DEC-18 |
| REQ-ING-05 | **Chunked parse:** the file is split into chunks (default 500 rows) in `ingest.upload_chunks`, each with a lease; a crashed worker's chunk is retried alone | §8.3, §8.11 |
| REQ-ING-06 | **Dry-run (automatic after parse):** all rules run, no seat consumed; each staged row gets outcome `ACCEPTED, HELD, REJECTED, NOOP_DUPLICATE`; staged payloads are encrypted with the tenant data key | §8.3 |
| REQ-ING-07 | Row-level errors MUST include field, rule code, message and suggested fix; downloadable `errors.csv` (formula-safe) | FR-02, FR-03 |
| REQ-ING-08 | **Partial acceptance & diff:** the diff view classifies rows as `NEW`, `CHANGED` (field-level before/after, no PII in diff payload — fields shown only to the tenant), `UNCHANGED` | F1 |
| REQ-ING-09 | **Commit** (`POST /upload-jobs/{id}/commit`, IA only, `committed_by ≠ uploaded_by`) runs the same claim logic row by row in chunked workers over `ACCEPTED` rows; per-row results `COMMITTED` or `COMMIT_FAILED(outcome)`. Final job status `COMPLETED` or `PARTIAL` | C-04, §8.3 |
| REQ-ING-10 | **Idempotency by stable row key** (`row_key` or hash of `institution_student_id + programme_code + academic_year`): re-processing never creates duplicate claims; identical committed rows are `NOOP_DUPLICATE` | FR-04, §8.3 |
| REQ-ING-11 | Per-institution concurrency quota for jobs (default 2 running) so one institution cannot starve others | §8.3 |
| REQ-ING-12 | Completion emits webhook `job.completed` (REQ-INTG-07) | §8.3 |
| REQ-ING-13 | **Channels:** (a) portal single form, (b) bulk file, (c) REST API `POST /claims` by an API client with `claims:write` — all use the same rule set and gate. A runnable **mock ERP client** (`scripts/mock_erp_client.py`) exercises (c) and receives webhooks via the local sink | FR-01, F1 |
| REQ-ING-14 | Staged rows are purged `staged_row_retention_days` (default 7) after job completion by the retention job | §5.5 |

### 3.8 Rules engine, findings and policy simulator (REQ-RUL) — M06, F6, F7, FR-21…25

*Depends on:* REF. *Done when:* the v1 seed rule set evaluates at all three call sites and the simulator reproduces a replay (§12 stories RUL-*).

| ID | Requirement | Src |
|---|---|---|
| REQ-RUL-01 | Rules are **data**: `rule_code, category, kind, params(JSON), severity, scope, effective_from/to, regulatory_ref, owner` with kinds `REQUIRED_FIELD, REGEX_FORMAT, COMPARE, IMPLIES, MUTUALLY_EXCLUSIVE, DATE_WITHIN, LOOKUP_IN_REFERENCE, BUCKET_CAP, SUM_RECONCILES` (`RATIO_THRESHOLD` is Deferred). New rule or parameter = data change; new kind = release | §6.1 |
| REQ-RUL-02 | Severities `Blocker` (stops acceptance), `Error` (fix or approval before submission), `Warning` (institution admin acknowledges; reviewer may waive with reason code), `Info` | FR-23 |
| REQ-RUL-03 | Rule sets are **immutable once published**; a change creates a new version; every claim and submission stores the `rule_set_id` that evaluated it | §6.1 |
| REQ-RUL-04 | One engine, **three call sites**: claim path, bulk/dry-run validation, submission-time aggregate checks | C-02 |
| REQ-RUL-05 | Categories: identity, same-institution duplicates, intake, category consistency, data integrity, cross-field, aggregate reconciliation | FR-22 |
| REQ-RUL-06 | **Findings** store evidence, rule version, suggested fix and status `OPEN, ACKNOWLEDGED, RESOLVED, WAIVED`. Institution admin acknowledges Warnings; AICTE reviewer waives with `reason_code`; a finding auto-resolves when re-evaluation no longer produces it | FR-25, §6.1 |
| REQ-RUL-07 | **Draft ≠ publish:** AICTE system admin drafts a rule set; a different AICTE reviewer publishes (step-up). Publishing activates it (single active set) and retires the previous; **rollback = re-activate the previous published set** | §6.9 |
| REQ-RUL-08 | Aggregate reconciliation (`SUM_RECONCILES`): student rows equal declared enrolment per programme; total ≤ approved intake + approved extras; bucket sums ≤ caps | FR-24/25, F6 |
| REQ-RUL-09 | **Policy simulator:** `POST /simulations {draft_rule_set_id, cycles[]}` runs an async job replaying the draft over event-store snapshots of the chosen past cycle with the same engine in read-only mode; reports institutions that would be flagged, programmes affected, findings by severity, and estimated reviewer workload (findings × minutes-per-finding config). Existing decisions keep their rule version unless the set declares `retroactive` | F7, FR-24, §6.9 |

### 3.9 Submission lifecycle (REQ-SUB) — F8, FR-04/05, §6.7

*Depends on:* ING, RUL, AUD (commitments). *Done when:* an institution takes a submission Draft → Locked with the separation of duties enforced.

| ID | Requirement | Src |
|---|---|---|
| REQ-SUB-01 | Submission per `(institution, academic_year, window)` with states `DRAFT, VALIDATED, SUBMITTED, ACCEPTED, UNDER_REVIEW, CORRECTED, LOCKED` and transitions per §5.4.2 | C-05 |
| REQ-SUB-02 | **Readiness check** (`POST /submissions/{id}/validate`): rules at submission call site; declared totals vs rows vs approved intake; open Blocker/Error findings block; Warnings listed for acknowledgement | F8, FR-05 |
| REQ-SUB-03 | Submission requires an **open window** (or approved extension); submissions after close carry `late_flag=true` (allowed only if late submission is permitted by an extension or reviewer override) | F8 |
| REQ-SUB-04 | Institution admin submits after acknowledging all Warnings; DB CHECK `approved_by <> submitted_by` | §6.7 |
| REQ-SUB-05 | AICTE reviewer decides: `ACCEPT`, `REQUEST_REVIEW` (→ UNDER_REVIEW with notes), or returns it for correction (→ CORRECTED path) | §6.7 |
| REQ-SUB-06 | **Lock = signed cut-off commitment** (REQ-AUD-05): institution signs the Merkle root (step-up), AICTE countersigns; only then `LOCKED`. Post-lock corrections are approved events producing a new commitment version | C-05 |
| REQ-SUB-07 | Students cannot edit official fields; corrections are requests (REQ-STU-05) decided by a reviewer; approved corrections write `CORRECT` events | §6.7 |

### 3.10 Triple-Lock verification (REQ-TL) — M08, F5, FR-31/32

*Depends on:* GATE, IDN, notifications, consent. *Done when:* SC-07 passes for adult, minor-with-guardian, assisted and disputed paths.

| ID | Requirement | Src |
|---|---|---|
| REQ-TL-01 | Each claim creates `core.enrolment_verification`: Lock 1 institution signature (signed with the institution's delegated software key after step-up for bulk, per-claim for single), Lock 2 from the registry result (`PENDING, PASSED, FAILED, PROVISIONAL_OK`), Lock 3 `PENDING` with `deadline_at = claimed_at + confirm_deadline_days` | §6.5, AS-10 |
| REQ-TL-02 | Overall state: `PENDING` (locks outstanding), `VERIFIED` (all agree), `UNCONFIRMED` (deadline passed without Lock 3), `DISPUTED`, `PENDING_FIX` (Lock 2 failed with a diagnosed fix) | §6.5 |
| REQ-TL-03 | **OTP confirmation:** the OTP goes preferably to the mobile held by the (mock) registry; an institution-supplied number is accepted as a lower-trust channel recorded `channel_trust=LOW`. OTP is 6 digits, stored hashed, 10-minute TTL, 5 attempts, 3 resends/hour; sent through the mock SMS gateway and visible in the Dev SMS Inbox [SIM] | §8.5, AS-05, DEC-19 |
| REQ-TL-04 | **Minors** (age < 18 at admission): a verified `guardian_link` receives the guardian OTP; the guardian confirms via guardian mode | §8.5, §7.11 |
| REQ-TL-05 | **Assisted confirmation** for students without phones: institution admin records an audited confirmation (reason code, witness note); `channel=ASSISTED`, `channel_trust=LOW` | §8.5, PQ-02 |
| REQ-TL-06 | **Student dispute** sets Lock 3 `DISPUTED`, overall `DISPUTED`, opens a case automatically (type `STUDENT_DISPUTE`), notifies AICTE | §6.5 |
| REQ-TL-07 | **Deadline job** (hourly): reminders at day 7 and day 21; at the deadline set `UNCONFIRMED`, write `VERIFICATION_CHANGE`, feed the Trust Index; if the institution's unconfirmed share ≥ 30% (config) open a case. Default policy **flag only; seat stays occupied until a reviewer acts** (G-13) | §8.5, G-13 |
| REQ-TL-08 | Lock 3 attests enrolment; it is **not** consent to APAAR use. Refusing APAAR consent never blocks counting. DigiLocker channel is Deferred and not shown as available | C-09, §6.5 |
| REQ-TL-09 | Every confirmation records channel and trust level; the Trust Index and dashboards report LOW-trust share separately | AS-05, R-04 |

### 3.11 Duplicate and transfer engine (REQ-DUP) — M07, F9, FR-26…30

*Depends on:* GATE events, PII vault, job queue. *Done when:* the seeded labelled duplicates are found with the target metrics (§14.6) and legitimate transfers/ODL pairs are not flagged.

| ID | Requirement | Src |
|---|---|---|
| REQ-DUP-01 | **Exact** duplicates are blocked at claim (REQ-GATE-03) | C-03 |
| REQ-DUP-02 | **Features at claim time** (worker, from the claim outbox event): K1 = HMAC(identity key); K2 = HMAC(phonetic first + last name tokens + DOB); K3 = HMAC(DOB + phonetic parent-name key); Bloom-filter encodings (keyed, bigram) of name, DOB, parent name; keyed hashes of phone, email, address as graph edges. Raw values never leave the tenant vault except inside the worker's memory | §8.4 |
| REQ-DUP-03 | **Candidates** are generated only by joining on blocking keys; blocks larger than `max_block_size` (default 50) are logged and routed to review rather than compared pairwise | §8.4 |
| REQ-DUP-04 | **Scoring:** Dice similarity on Bloom encodings plus timeline and programme agreement → score with per-field contributions (e.g. `0.91: name 0.95, DOB exact, father 0.90, different institutions, overlapping dates`). Defaults: ≥ 0.80 stored as `REVIEW`; ≥ 0.97 with `SEQUENTIAL` timeline stored `AUTO_LINKED` | §8.4, DEC-12 |
| REQ-DUP-05 | **Timeline classes:** `OVERLAP_FT, SEQUENTIAL, FT_PLUS_ODL, EXCEPTION, SAME_INST_READMIT` with outcomes per source §6.6 | §6.6 |
| REQ-DUP-06 | **Pair outcomes** `AUTO_LINKED, REVIEW, DISMISSED (never re-flagged), CONFIRMED_DUP (routed to both institutions as findings)`; reviewer decisions are stored as labels | §6.6, FR-27 |
| REQ-DUP-07 | **Reviewing** shows scores and per-field contributions without PII; "Reveal identity" requires an elevation grant tied to the case (REQ-ACL-06) | §7.5 |
| REQ-DUP-08 | **Transfers:** institution A records `TRANSFER_OUT` with effective date; B records `TRANSFER_IN` referencing it (class `TRANSFER_PENDING` until matched); a missing counterpart after `orphan_lead_days` creates an `ORPHAN_LEAD` (a lead, not a violation) | §6.6, FR-28 |
| REQ-DUP-09 | `EXCEPTION` pairs matching `ref.enrolment_exception_rules` are allowed and recorded | F9 |
| REQ-DUP-10 | Privacy review of Bloom encodings is a **documented pre-production requirement** (R-08); the prototype uses keyed HMAC salts and shows the notice in the admin docs | R-08 |

### 3.12 Intelligence, Trust Index and case management (REQ-INT) — M09, F10, FR-20, 33…36

*Depends on:* TL, DUP, claim_attempts, feeds, job queue. *Done when:* the seeded ghost ring, over-admission institution and stolen-identity disputes surface as ranked alerts with evidence, and an appeal closes with a stored label.

| ID | Requirement | Src |
|---|---|---|
| REQ-INT-01 | **Institution Trust Index** (0–100) is a transparent weighted sum; each component links to evidence rows. Weights (config): Triple-Lock confirmation 30, open anomaly alerts 20, claim conflicts per 1,000 claims 15, submission timeliness 15, intake gate hits/exceptions 10, correction rate 10 (sum 100). Formulas in §5.6.1 | §6.8, C-17 |
| REQ-INT-02 | Bands: ≥ 80 `LOW_PRIORITY`, 60–79 `WATCH`, < 60 `REVIEW_PRIORITY`. The score **only prioritises reviews**; no automatic penalty or restriction is derived from it, and the UI says so | C-17, R-07 |
| REQ-INT-03 | **Layer A (deterministic):** rule findings of impossible/invalid combinations surface as alerts when OPEN Blocker/Error findings exceed a per-institution threshold | §6.8 |
| REQ-INT-04 | **Layer B (cross-institution duplicates):** `CONFIRMED_DUP` and high-score `REVIEW` candidates create alerts | §6.8 |
| REQ-INT-05 | **Layer C (institution risk):** features per institution/year — growth vs own history, share of uploads in the 48 h before a deadline, NOGEN+PENDING ratio vs peers, shared-contact cluster size, foreign-national/OCI share spike, category-mix drift (total-variation distance vs prior year), zero attrition (withdrawn share = 0 with ≥ 50 enrolments), unconfirmed share. Method: peer groups by (state, programme level, institution type), min 5 peers else national peers; robust z-score `0.6745·(x−median)/MAD`, alert at \|z\| ≥ 3.5; Isolation Forest (scikit-learn) scored when ≥ 30 institutions have a closed prior cycle, alert at ≥ 97th percentile | §6.8, DEC-14 |
| REQ-INT-06 | **Explainability:** every alert stores baseline, deviation, evidence references, suggested action, model/config version and a per-feature contribution table (SHAP Deferred) | §6.8 |
| REQ-INT-07 | **Graph clusters (Louvain via NetworkX):** nodes = students, phone hash, email hash, address hash; clusters with ≥ 8 students sharing ≥ 1 contact node across ≥ 1 institution are scored; shared phones are a **weighted review signal only**, never an automatic flag (guardians legitimately share phones) | C-19, §8.7 |
| REQ-INT-08 | **Layer D (academic liveness) [SIM]:** an `ADMITTED` enrolment older than `liveness_semesters × semester_days` (2 × 180 days) with no ABC credit and no exam registration in the mock feed becomes a ghost-candidate lead. Feed data arrives through `POST /feeds/liveness` (scope `feeds:write`) from the mock feed pusher; every liveness view carries the `SIMULATED` badge and the value claim is withheld (R-06) | C-15, G-11, R-06 |
| REQ-INT-09 | **Cases:** created from alerts, confirmed duplicates, student disputes, high unconfirmed share; fields: case number (`CASE-2026-0001`), institution, type, owner, status, response due (default 14 days), outcome code, notes, SHA-256 **evidence pack**, timeline | §6.8, §8.7 |
| REQ-INT-10 | Case states: `OPEN → AWAITING_RESPONSE → UNDER_REVIEW → CLOSED`, and `CLOSED → APPEALED → CLOSED` (§5.4.7). Outcome codes (also training labels): `CONFIRMED_VIOLATION, NOT_SUBSTANTIATED, DATA_CORRECTED, REFERRED_FOR_INSPECTION, FALSE_POSITIVE`. No outcome triggers an automatic penalty | C-17 |
| REQ-INT-11 | **Appeals:** institution admin files with grounds within 15 days of closure; a **different** reviewer than the case owner decides; decision recorded and label updated | §8.7, R-07 |
| REQ-INT-12 | Trust Index and anomaly pipeline run nightly and on demand (`run-now`), idempotent per `(institution, year, date)` | §8.7 |

### 3.13 Dashboards and live updates (REQ-DASH) — M13, F11, FR-37…39, 41

*Depends on:* outbox, projector, `agg` schema. *Done when:* SC-06 passes and every widget shows `as_of`.

| ID | Requirement | Src |
|---|---|---|
| REQ-DASH-01 | The **outbox projector** (scheduler loop, 2 s) claims outbox rows with `FOR UPDATE SKIP LOCKED`, updates `agg.*` tables idempotently (`agg.projector_state.last_outbox_id`), creates webhook deliveries and emits no PII | §8.6 |
| REQ-DASH-02 | Every widget response carries `as_of` (time of the newest projected outbox row). The UI shows a freshness chip, turning amber when > 60 s old | FR-39, AS-12 |
| REQ-DASH-03 | **AICTE dashboard:** national KPIs; seat-utilisation explorer (filters: state, region, programme level, institution type, utilisation band; sort; pagination); state/district heat table and treemap; alert inbox; duplicate queue; compliance tracker (submission state per institution); scorecards (Trust Index); trends (claims per day, cumulative, current vs 2025-26); exports | §8.6, F11 |
| REQ-DASH-04 | **Institution dashboard:** seat ledger by programme × bucket (progress bars with 90/100% markers), claims awaiting confirmation with days remaining, PENDING_FIX list with deadlines, open findings, submission status, own Trust Index components, notification delivery status, stale-claims widget | §8.6 |
| REQ-DASH-05 | **Small-cell suppression:** any group with fewer than `small_cell_min` (5) is returned as `{"suppressed": true}` by the API layer | §6.10, social impact |
| REQ-DASH-06 | One governed definition of enrolment (shown in tooltips and report footers): **Occupied** = status `CLAIMED`/`ADMITTED`; **Cumulative admitted** = ever `ADMITTED`; **Verified** = occupied and overall state `VERIFIED` | C-07, G-03 |
| REQ-DASH-07 | All list widgets support search, filter, sort and keyset pagination per DEC-27 | Brief §3 |
| REQ-DASH-08 | Auto-refresh by polling every 15 s with a manual refresh button; no student-level data in any push/poll payload for AICTE users | DEC-07, §3.2 flow 7 |

### 3.14 Reports (REQ-RPT) — M13, F12, FR-40

*Depends on:* DASH, job queue, ObjectStore. *Done when:* each report type downloads in CSV, XLSX and PDF with stamp and suppression.

| ID | Requirement | Src |
|---|---|---|
| REQ-RPT-01 | Report types: `SEAT_UTILISATION, ENROLMENT_SUMMARY, VERIFICATION_STATUS, FINDINGS, COMPLIANCE, TRUST_INDEX, CLAIM_ACTIVITY, INSTITUTION_PACK` (summary pack for accreditation use; formats for NBA/NAAC/AISHE are G-19 and Deferred) | F12 |
| REQ-RPT-02 | Formats CSV, XLSX (XlsxWriter), PDF (reportlab, DEC-13). Requested with `POST /reports` → `202`, polled, downloaded; files in the `ObjectStore` with `sha256` | §8.10 |
| REQ-RPT-03 | Every report is **stamped** with rule-set version, cut-off, generator, generated-at, the definitions of §3.13 DASH-06 and a verification QR to `GET /public/reports/{ref}` (returns title, generated-at, SHA-256 and rule version only) | F12 |
| REQ-RPT-04 | Aggregate reports apply small-cell suppression; CSV/XLSX cells beginning `= + - @` are prefixed `'` | §7.10 |
| REQ-RPT-05 | Institutions can export their own enrolment list (student-level, decrypted, audited, formula-safe). AICTE cannot export student-level data except under an elevation grant tied to a case | §7.5 |
| REQ-RPT-06 | Deferred: NLQ, briefs, scheduled reports (Appendix A) | AS-11 |

### 3.15 Credentials and certificates (REQ-CRD) — M10, F13, FR-44…46

*Depends on:* TL (VERIFIED only), KeyProvider, consent. *Done when:* SC-10 passes and the certificate PDF's QR resolves to the live status page.

| ID | Requirement | Src |
|---|---|---|
| REQ-CRD-01 | A credential is issued only for an enrolment with overall state `VERIFIED` and status `ADMITTED`, by an institution admin (step-up), individually or via a "issue for all verified" job | §6.10 |
| REQ-CRD-02 | Format: **SD-JWT VC** signed ES256 by the issuer key. Selectively disclosable: programme, institution, years, approved-seat flag, status. Always visible: `credential_ref`, issuer, `kid`, validity. No student name or ID inside the credential. An **AICTE attestation** (ES256 signature over the SHA-256 digest of the issuer-signed part) is stored with it. Use an SD-JWT library if one installs cleanly; else implement per the SD-JWT specification with `cryptography` | §8.8 |
| REQ-CRD-03 | `credential_ref` is a 128-bit random, non-sequential value (base64url) | §8.8 |
| REQ-CRD-04 | **Bitstring Status List** (W3C): gzip+base64url bitstring, 131,072 bits, signed JWT, served by verify-api with a 60 s cache TTL; revocation sets a bit with a reason code; a name correction supersedes the credential | §8.8 |
| REQ-CRD-05 | **Certificate PDF** (platform template) prints name, APAAR ID, student ID, programme, institution, credential reference and a QR; the QR holds only `credential_ref`, issuer, `kid`, signature digest and the short HTTPS URL — no personal data. **Aadhaar is never printed** | §8.8, FR-46 |
| REQ-CRD-06 | Revocation by AICTE reviewer (reason code, step-up), supersession by institution admin or reviewer; both emit audit entries and the webhook `credential.revoked`. A withdrawn/transferred-out enrolment auto-revokes its credential (`ENROLMENT_ENDED`) via an outbox consumer | §8.8 |
| REQ-CRD-07 | Key registry: `GET /public/jwks` (public keys by `kid`) and `GET /public/key-status` (`ACTIVE, ROTATING, RETIRED, COMPROMISED`). Rotation/compromise workflow is Deferred; the seed includes one `RETIRED` sample key to exercise key-status display | §7.7 |
| REQ-CRD-08 | Types `YEAR_COMPLETION, DEGREE_DIPLOMA, TRANSCRIPT_SUMMARY` are enum values only (DEC-30) | §8.8 |

### 3.16 Privacy-preserving verification (REQ-VER) — F14, FR-47

*Depends on:* CRD, consent, verify-api. *Done when:* scenario S7 passes and the abuse defences are covered by tests.

| ID | Requirement | Src |
|---|---|---|
| REQ-VER-01 | Verifier organisations have a tier (`REGULATOR, INSTITUTION, UNIVERSITY, EMPLOYER, BANK, EMBASSY`), status `ACTIVE/SUSPENDED`, and purpose scopes (allowed fields, hourly limit). Purposes: `EMPLOYMENT_VERIFICATION, LOAN_ELIGIBILITY, VISA_SUPPORT, ADMISSION_TRANSFER, REGULATORY_CHECK` | §7.1, C |
| REQ-VER-02 | A request names a **purpose** and a **question**: `IS_ENROLLED`, `PROGRAMME_MATCH(programme_code)`, `ENROLLED_IN_YEAR(academic_year)`, and presents a credential reference (from a certificate/QR) or a student-issued share token | F14, G-15 |
| REQ-VER-03 | The response is a **signed yes or no** (ES256 JWT) with only the fields the purpose allows, a `nonce` (verifier-supplied or server-generated), `iat`, and `exp = iat + 300 s` | §8.8, §7.8 |
| REQ-VER-04 | Live checks before answering: status-list bit, credential validity period, enrolment still `VERIFIED` and not withdrawn, **consent** granted/unexpired/in-scope, organisation `ACTIVE`, rate limit | §8.8 |
| REQ-VER-05 | **Neutral response:** unknown ref, unconsented, revoked-with-no-consent and similar all return the identical body `{"result":"NEUTRAL","message":"No verifiable answer is available for this request."}`. For an existing but unconsented credential the system silently creates a consent request in the student's wallet | §7.8, G-15 |
| REQ-VER-06 | The UI shows a **disclosure meter** (`n of ~40 record fields shared`) for the verifier and the student, from a field catalogue of 40 fields in `core/field_catalogue` | §1.8 |
| REQ-VER-07 | Abuse defences: per-org hourly limits (429); alert when requests exceed 3× the org's 7-day hourly average or > 30 distinct unknown refs in 10 min; AICTE system admin can suspend an org | §7.8 |
| REQ-VER-08 | The verifier UI states plainly: *a signature proves integrity and key control, not that the underlying academic fact is true* | §7.8, §9.5 |
| REQ-VER-09 | `verify-api` is a **separate deployable** (own process/port, narrow read access, own rate limits, no staff routes). `GET /v/{ref}` public page shows live status and the boundary statement | §3.4 |
| REQ-VER-10 | `POST /public/verify` accepts a student-issued **share token** from anyone (rate-limited per IP) and returns the same signed minimal answer limited to the share link's fields and purpose | F14 |
| REQ-VER-11 | **Offline behaviour:** the public status page and verifier portal remember the last successful status check per credential reference (browser storage, wrapped in try/catch). If the API is unreachable they show that last-known status with its timestamp and an amber warning ("Offline — last known status as of …, may be out of date"); an unknown reference is never shown as valid | §8.8 |

### 3.17 Student wallet, consent and corrections (REQ-STU) — M11, F15, FR-48, 55, 56, 69

*Depends on:* TL, CRD, VER, consent. *Done when:* scenarios S4, S5, S7, S15 pass.

| ID | Requirement | Src |
|---|---|---|
| REQ-STU-01 | Student sees own enrolments with the three locks, identity status, fix requests, deadlines and credentials | F15 |
| REQ-STU-02 | **Access log:** every verification request (org, purpose, fields, result, time) and every staff elevation touching the record (shown as "AICTE case access", case ref, role, time) | §1.8, FR-48 |
| REQ-STU-03 | **Consent ledger:** grant/refuse/withdraw per verifier org and purpose, with notice version, method, expiry; explicit `REFUSED` state for APAAR-use consent. Withdrawal blocks new verifications; past events remain in the audit log. Refusal never affects counting | §7.11, C-09 |
| REQ-STU-04 | **Share links:** choose credential, purpose, field subset, expiry (≤ 30 days); token shown once; revoke any time; use count shown | F15 |
| REQ-STU-05 | **Correction requests:** field (name, DOB, parent names, phone, email), proposed value (stored encrypted), note; AICTE reviewer decides; approval writes a `CORRECT` event, updates the vault record version and supersedes affected credentials. Students cannot edit official fields directly | §6.7, FR-55 |
| REQ-STU-06 | **Data-subject requests (DPDP-aligned):** `ACCESS` (generates a JSON export of the student's own record), `CORRECTION`, `ERASURE`, `GRIEVANCE`. Erasure on an active enrolment ends `REJECTED_LAWFUL_RETENTION` with restricted processing; otherwise record-level erasure (DEC-15). The portal footer shows a named grievance-officer path (placeholder role holder) | §7.11, FR-69 |
| REQ-STU-07 | **Guardian mode:** a student-role user with verified `guardian_links` can switch to a linked minor and confirm, consent, and view on their behalf. Link verification is a guardian OTP via the mock SMS gateway | DEC-31, §7.11 |
| REQ-STU-08 | **Inclusion proof:** for any committed cohort the student can fetch a Merkle inclusion proof for their enrolment and verify it in the browser | §8.9 |
| REQ-STU-09 | English and Hindi catalogues (DEC-29); language toggle persists per user | FR-66 |

### 3.18 Audit and tamper evidence (REQ-AUD) — M12, F16, FR-49/50

*Depends on:* KeyProvider, job queue. *Done when:* SC-09 passes.

| ID | Requirement | Src |
|---|---|---|
| REQ-AUD-01 | Every mutation, every sensitive read (student-level view, export, elevation, verification request), login, logout, failed login and authorisation denial MUST write `audit.audit_log` (actor, role, action, entity, institution, case, justification, request id, IP, payload **without PII**) in the same transaction as the change | §7, §8.12 |
| REQ-AUD-02 | **Hash chain:** the sealer (scheduler task, singleton via PostgreSQL advisory lock, every 5 s) assigns `seq`, `prev_hash`, `entry_hash = SHA-256(prev_hash ‖ canonical_json(entry))` in `id` order. Unsealed entries are shown with a lag indicator | §8.9, DEC-02 |
| REQ-AUD-03 | **Checkpoints** every 1,000 entries or hourly: ES256-signed root over the latest link, written to `audit.audit_checkpoints` and copied to `data/worm/` (append-only directory, **simulated WORM**, labelled in the UI) | C-16, DEC-09 |
| REQ-AUD-04 | **Integrity check** (`POST /audit/integrity-check`, async): recomputes the chain, verifies checkpoint signatures, compares WORM copies, reports the first broken `seq` and entry. Runs after seed/restore and daily | §8.9, §8.12 |
| REQ-AUD-05 | **Cohort commitments:** at each cut-off a Merkle tree (RFC 6962 hashing: `0x00` leaf prefix, `0x01` node prefix) is built over a canonical serialisation (`serialisation_version=1`, sorted by enrolment id) of an institution's enrolments (no PII: ids, programme, bucket, status, admission date, verification state, identity-key hash, rule-set id). The institution signs the root (step-up), AICTE countersigns | §8.9, FR-50 |
| REQ-AUD-06 | `commitment_leaves` are stored so an **inclusion proof** can be produced after later events; editing an enrolment after the cut-off is provable because the recomputed root differs | §8.9 |
| REQ-AUD-07 | History tables (`enrolment_events`, `seat_ledger_entries`, `audit_log`, `audit_checkpoints`, `commitment_leaves`) have **no UPDATE/DELETE grants** for `app_runtime`; a trigger permits setting the seal columns on `audit_log` exactly once | §5.4 |
| REQ-AUD-08 | The AICTE audit explorer filters by actor, action, entity, institution, case, date and shows chain status | M12 |
| REQ-AUD-09 | Demo CLI (migrator role): `sentinel demo tamper-audit --seq N` alters one audit entry and `sentinel demo tamper-enrolment --id …` alters one enrolment after a cut-off (originals saved) so detection can be shown; `sentinel demo untamper` restores both | SC-09 |

### 3.19 Integration APIs, webhooks and adapters (REQ-INTG) — M14, F18, FR-59/60

| ID | Requirement | Src |
|---|---|---|
| REQ-INTG-01 | Versioned REST under `/api/v1`, OpenAPI 3.1 at `/api/v1/openapi.json`, interactive docs at `/docs` (demo mode) | FR-59 |
| REQ-INTG-02 | Standard headers: `Authorization`, `X-Request-Id` (generated if absent, always echoed), `Idempotency-Key`, `If-Match` (ETag on PATCH and decisions), `Accept-Language` | §8.10 |
| REQ-INTG-03 | Errors are **RFC 9457** `application/problem+json` with `type, title, status, code, detail, request_id, retryable, errors[]` (each: `field, code, message, fix_suggestion`) and localised messages | §8.10 |
| REQ-INTG-04 | Keyset pagination: `limit` ≤ 500 (default 50), opaque `cursor`, `next_cursor`; broad extracts go through report jobs | §8.10, DEC-27 |
| REQ-INTG-05 | Rate limits per client/user/IP via token bucket (DEC-28); `RateLimit-Limit/Remaining/Reset` headers; `429` with `Retry-After`; stricter on public endpoints | §8.10 |
| REQ-INTG-06 | Long operations return `202` with a job resource; progress by GET; completion by webhook | §8.10 |
| REQ-INTG-07 | **Webhooks:** endpoints registered per institution/client; payloads carry identifiers only; `X-Sentinel-Signature: t=<unix>,v1=<HMAC-SHA256(secret, t.body)>`; retries with backoff (1 m, 5 m, 30 m, 2 h, 6 h, then every 6 h) for 24 h then dead-letter with replay in the portal. Events: `job.completed, claim.created, claim.pending_verification_resolved, finding.created, confirmation.completed, credential.revoked, case.updated`. URL validation blocks private ranges (SSRF) unless `ALLOW_PRIVATE_WEBHOOK_URLS=true` (demo sink) | §8.10, §7.10 |
| REQ-INTG-08 | Adapter interfaces (each with a mock implementation): `RegistryAdapter`, `AicteMasterAdapter`, `SmsGateway`, `EmailGateway`, `LivenessFeedAdapter`; stubs for `DigiLockerAdapter`, `AbcAdapter`, `NadAdapter` raise `NotAvailableInPrototype` and are not exposed in the UI | §3.5, F18 |
| REQ-INTG-09 | `POST /feeds/liveness` (client scope `feeds:write`) ingests activity (APAAR ID → hashed on receipt, never stored raw) | §8.7 |
| REQ-INTG-10 | Mock services are visibly separate (`mock-services` process, own schema `mockext`, banner in responses) | Brief §3 |

### 3.20 Operations, jobs and notifications (REQ-OPS)

| ID | Requirement | Src |
|---|---|---|
| REQ-OPS-01 | **Job queue (DEC-02):** table `ops.jobs` (`queue, type, payload, status QUEUED/RUNNING/SUCCEEDED/FAILED/DEAD, run_at, attempts, max_attempts, lease_expires_at, locked_by, institution_id`). Workers poll every 1 s with `FOR UPDATE SKIP LOCKED`; a lease (default 60 s) is extended by a heartbeat; an expired lease returns the job to `QUEUED`; retries use exponential backoff with jitter; after `max_attempts` the job is `DEAD` and visible on the Operations page. Queues: `ingest, bulk_claim, identity, matching, intel, credential, report, notify, webhook, maintenance`. Handlers MUST be idempotent. `WORKER_MODE=inline` executes synchronously for tests | §8.11, F |
| REQ-OPS-02 | **Scheduler:** `ops.schedules` with a single leader (advisory lock) enqueueing: outbox projector 2 s, audit sealer 5 s, registry revalidation 60 s, notification dispatch 5 s, confirmation reminders/expiry hourly, Trust Index + anomaly pipeline nightly, orphan-lead scan daily, liveness evaluation daily, retention daily, integrity check daily, status-list republish on change | §3.4 |
| REQ-OPS-03 | **Outbox:** every transaction that changes business state writes `ops.outbox_events` (topic, aggregate, payload without PII) in the same commit | §8.6 |
| REQ-OPS-04 | `GET /healthz` (process alive) and `GET /readyz` (DB reachable, migrations at head, RLS self-check passed; mock-service reachability reported but non-fatal) | §8.10 |
| REQ-OPS-05 | **Operations page** (AICTE reviewer and system admin): queue depth and oldest job age per queue, dead jobs with replay, outbox lag, sealer lag, pending registry checks, circuit-breaker state, rate-limit rejections, request latency percentiles (in-memory), last scheduler runs [Grafana substitute, DEC-03] | FR-65 |
| REQ-OPS-06 | **Demo tools** (only if `DEMO_MODE=true`, AICTE system admin only): set registry mode (`UP/DOWN/SLOW`), clock offset / advance N days, run a scheduled job now, open Dev SMS Inbox, trigger reset-and-reseed (typed confirmation), view webhook sink | DEC-16, DEC-32 |
| REQ-OPS-07 | **Retention job** driven by `ops.retention_policies`: staged rows (7 days), idempotency keys (7 days), expired sessions (30 days), expired OTP hashes (1 day), expired elevation grants (kept, marked) | §5.5 |
| REQ-OPS-08 | **Logging:** structured JSON with `request_id`; a redaction processor drops/hashes `name, dob, phone, email, apaar, otp, password, token, authorization` keys and scrubs 10- and 12-digit sequences; HMAC keys never logged. A CI check greps logs of the test run for Aadhaar-like patterns | §7.6, §8.12 |
| REQ-OPS-09 | **Notifications:** `ops.notifications` with channel `SMS/EMAIL/INAPP`, templates in English and Hindi, delivery status visible to institutions; failures retry with backoff, then fall back (SMS → EMAIL → INAPP) | §3.5, F, M15 |

### 3.21 Seeding and reset (REQ-SEED)

| ID | Requirement | Src |
|---|---|---|
| REQ-SEED-01 | `sentinel db seed --profile {minimal\|standard\|full}` MUST be deterministic for a given `SEED_RNG` and `SEED_ANCHOR_DATE` and create all data of §11 | Brief §4 |
| REQ-SEED-02 | `sentinel db reset` drops and recreates the app schemas (or the database), runs migrations and seeds; `sentinel db reseed` truncates data tables and reseeds without re-migrating | Brief §4 |
| REQ-SEED-03 | Seeding MUST go through the real domain rules for the "story" records (§11.7) and through validated bulk loaders for volume data; both MUST produce consistent ledgers, events, audit chain and aggregates | Brief §4 |
| REQ-SEED-04 | `sentinel db verify-seed` MUST recompute and assert the invariants listed in §11.10 | Brief §4 |
| REQ-SEED-05 | A fresh setup (`sentinel setup`) MUST leave the system immediately usable: migrations applied, seed loaded, demo accounts working | Brief §4 |
| REQ-SEED-06 | `sentinel eval` runs the evaluation harness (§14.6) against seeded ground truth stored in schema `seed` (inaccessible to the app role) | §8.13 |

### 3.22 Non-functional requirements (NFR)

Targets marked **(proposed)** are source design targets (AS-12); the *Prototype target* is what the build is tested against on a developer laptop (4 cores, 16 GB, SSD) with the `standard` profile.

| ID | Area | Source target | Prototype target and mechanism |
|---|---|---|---|
| NFR-01 | Claim latency | p95 < 500 ms excluding registry (proposed), FR-62 | p95 < 500 ms at 50 concurrent clients (registry mock set to fast); registry called outside the transaction |
| NFR-02 | Upload | ack < 2 s; large file within agreed time | Ack < 2 s; a 5,000-row file reaches `READY_TO_COMMIT` in < 60 s; 20,000 rows in < 4 min |
| NFR-03 | Public verification | p95 < 300 ms (proposed) | p95 < 300 ms for `POST /public/verify` (excluding jitter used for neutral-response equalisation) |
| NFR-04 | Dashboard freshness | < 60 s (proposed) | Projected within 10 s of commit; `as_of` shown |
| NFR-05 | Dashboard query time | n/a | < 500 ms per widget on `standard`; < 1.5 s on `full` |
| NFR-06 | Availability/recovery | 99.9%, RPO 5 min, RTO 1 h (proposed) | Not an SLO in the prototype. A documented `pg_dump` backup/restore script restores the demo in < 10 min, followed by the integrity check |
| NFR-07 | Scalability | Hundreds of institutions, millions of rows, 10× headroom (AS-06) | Schema/indexing designed accordingly (keyset pagination, no unbounded scans); `full` profile (200 institutions, ~50,000 students) must meet NFR-05 |
| NFR-08 | Correctness | No oversold seat; one exclusive claim | SC-02/SC-03; property tests on ledger invariants (Hypothesis) |
| NFR-09 | Integrity | Tamper-evident history | REQ-AUD-*, no UPDATE/DELETE grants |
| NFR-10 | Security/privacy | Least privilege, minimisation | Section 9 |
| NFR-11 | Interoperability | OpenAPI 3.1, RFC 9457, webhooks | REQ-INTG-* |
| NFR-12 | Usability and inclusion | WCAG, low-bandwidth, English+Hindi | WCAG 2.1 AA best effort; `axe-core` finds 0 critical/serious issues on key pages; code-split bundles < 500 kB gzip initial; English + Hindi |
| NFR-13 | Time | UTC storage, IST cut-offs, authoritative clock | `timestamptz` UTC; Clock service; cut-offs configured in IST |
| NFR-14 | Observability | OTel, Prometheus, Grafana… | **Deferred** (DEC-03); structured logs + Operations page |
| NFR-15 | Maintainability | Modular monolith, import-linter, tests tagged by FR | `import-linter` contracts enforced in CI; each requirement has a test tagged `@req("REQ-…")`; backend coverage ≥ 80% for gate, rules, identity, audit |
| NFR-16 | Portability | Docker, standard servers | Runs on Windows 11, macOS, Linux with Docker (DB) and local Python/Node, or fully in Docker Compose |
| NFR-17 | Reliability | §8.11 failure handling | Behaviours in §13 verified by tests (registry down, worker killed mid-chunk, duplicate requests, clock skew) |
| NFR-18 | Documentation | OpenAPI, runbooks | README quick-start, `docs/runbooks/` (registry outage, queue backlog, rule rollback, restore, tamper detection) |

---

## 4. Final User Roles, Permissions and Access-Control Matrix

### 4.1 Retained roles (and what was merged or deferred)

| Code | Role | Class | Description (this prototype) | Source |
|---|---|---|---|---|
| **IA** | Institution admin | MVP | Claims seats, admits, withdraws, transfers, commits uploads, submits, signs commitments, issues credentials, manages own institution's users/clients/webhooks, responds to cases and appeals | §7.1 |
| **IO** | Institution data operator | MVP | Uploads and dry-runs data, validates claims, reads own institution's records; cannot commit or submit | §7.1 |
| **AR** | AICTE reviewer / officer (**includes AA analyst duties**) | MVP, merged [DEC-06] | Reads national/regional aggregates and alerts; resolves duplicates; approves corrections and exceptions; waives findings; accepts/locks submissions; publishes rule sets; revokes credentials; runs cases; releases seats; decides onboarding, DSRs | §7.1 |
| **AS** | AICTE system admin | MVP | Drafts rule sets; manages AICTE users and verifier organisations; runs demo tools; no default access to student data or aggregates dashboards | §7.1 |
| **ST** | Student or guardian | MVP | Own record, consents, access log, credentials, share links; requests corrections and data actions; guardian mode for linked minors | §7.1, DEC-31 |
| **VU** | Verifier user | MVP | Purpose-bound verification requests only | §7.1 |
| **CL** | System client | MVP (minimal) | ERP/feed client with OAuth2 scopes (`claims:write`, `claims:validate`, `upload:write`, `feeds:write`) | §7.1 |
| **PUB** | Public | MVP | Public verifier endpoints, rate-limited; onboarding form | §7.1 |
| ~~AA~~ | AICTE analyst | **Merged into AR** | Read-only aggregate/alert viewing | DEC-06 |
| ~~AU~~ | Auditor | **Deferred** | Independent read-only audit role; AR and AS get read-only audit access meanwhile | DEC-06 |

Scopes (`iam.user_roles.scope_type`): `NATIONAL`, `REGION`, `STATE`, `INSTITUTION`. Verifier organisations carry a tier and purpose scopes. All permission checks use the permission codes in §4.3.

### 4.2 Role-to-DB-context mapping
IA, IO, ST, CL → RLS with `app.institution_id` (ST/guardian: own `student_id` policy). AR/AS → `app.scope` of NATIONAL/REGION/STATE for aggregates through the `agg_repo`; student-level only through `drilldown` functions with a grant. VU and PUB → `verify-api` narrow views.

### 4.3 Access-control matrix (route permission registry)

✔ allowed · ✖ denied · *own* = own institution/record only · *grant* = only with an unexpired case-linked elevation · *step* = also requires step-up token · *reg* = within the user's regional scope.

| Capability (permission code) | IA | IO | AR | AS | ST | VU | CL |
|---|---|---|---|---|---|---|---|
| Sign in, `GET /me`, sessions (`auth:self`) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | token |
| Create claim (`claims:write`) | ✔ own | ✖ | ✖ | ✖ | ✖ | ✖ | ✔ own (scope) |
| Validate claim / dry-run (`claims:validate`) | ✔ own | ✔ own | ✖ | ✖ | ✖ | ✖ | ✔ own |
| View institution enrolments (`enrolments:read`) | ✔ own | ✔ own | ✖ (aggregate/grant only) | ✖ | own record | ✖ | ✖ |
| Admit / withdraw / transfer-out/in (`enrolments:manage`) | ✔ own | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ |
| Release a seat (`seats:release`) | ✖ | ✖ | ✔ step, reg | ✖ | ✖ | ✖ | ✖ |
| Upload and dry-run files (`uploads:write`) | ✔ own | ✔ own | ✖ | ✖ | ✖ | ✖ | ✔ own |
| Commit upload (`uploads:commit`; committer ≠ uploader) | ✔ own, step | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ |
| Validate submission (`submissions:validate`) | ✔ own | ✔ own | ✖ | ✖ | ✖ | ✖ | ✖ |
| Submit (`submissions:submit`) | ✔ own | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ |
| Decide / accept / return submission (`submissions:decide`) | ✖ | ✖ | ✔ reg | ✖ | ✖ | ✖ | ✖ |
| Sign cohort commitment (`commitments:sign`) | ✔ own, step | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ |
| Countersign / lock (`commitments:countersign`) | ✖ | ✖ | ✔ step, reg | ✖ | ✖ | ✖ | ✖ |
| Request / decide seat exception (`seat_exceptions:request` / `:decide`) | req own | ✖ | decide reg | ✖ | ✖ | ✖ | ✖ |
| Request / decide window extension (`extensions:request` / `:decide`) | req own | ✖ | decide reg | ✖ | ✖ | ✖ | ✖ |
| Read findings (`findings:read`) | ✔ own | ✔ own | ✔ reg (aggregate; detail on grant) | ✖ | ✖ | ✖ | ✖ |
| Acknowledge finding (`findings:ack`) / waive (`findings:waive`) | ack own | ✖ | waive reg | ✖ | ✖ | ✖ | ✖ |
| Draft rule set (`rulesets:draft`) | ✖ | ✖ | ✖ | ✔ | ✖ | ✖ | ✖ |
| Publish / roll back rule set (`rulesets:publish`; publisher ≠ drafter) | ✖ | ✖ | ✔ step | ✖ | ✖ | ✖ | ✖ |
| Read rule sets (`rulesets:read`) | active only | active only | ✔ | ✔ | ✖ | ✖ | ✖ |
| Run policy simulation (`simulations:run`) | ✖ | ✖ | ✔ | ✔ | ✖ | ✖ | ✖ |
| AICTE dashboards, alerts, scorecards, trends (`dashboards:aicte`) | ✖ | ✖ | ✔ reg | ✖ | ✖ | ✖ | ✖ |
| Institution dashboard (`dashboards:institution`) | ✔ own | ✔ own | ✔ reg | ✖ | ✖ | ✖ | ✖ |
| Trust Index read (`trust:read`) | ✔ own | ✖ | ✔ reg | ✖ | ✖ | ✖ | ✖ |
| Review duplicates (`duplicates:review`) | ✖ | ✖ | ✔ reg (reveal on grant) | ✖ | ✖ | ✖ | ✖ |
| Open / run / close cases (`cases:manage`) | ✖ | ✖ | ✔ reg | ✖ | ✖ | ✖ | ✖ |
| Read own cases, respond (`cases:respond`) | ✔ own | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ |
| File appeal (`appeals:file`) / decide (`appeals:decide`; decider ≠ case owner) | file own | ✖ | decide | ✖ | ✖ | ✖ | ✖ |
| Request elevation (`elevations:request`) | ✖ | ✖ | ✔ (case required) | ✖ | ✖ | ✖ | ✖ |
| Student-level view (`students:read`) | ✔ own | ✔ own | grant | ✖ | own | ✖ | ✖ |
| Record transfers (`transfers:write`) | ✔ own | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ |
| Request reports (`reports:request`) | ✔ own | ✖ | ✔ reg (aggregate) | ✖ | ✖ | ✖ | ✖ |
| Issue credential (`credentials:issue`) | ✔ own, step | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ |
| Revoke / supersede credential (`credentials:revoke`) | supersede own | ✖ | revoke, step | ✖ | ✖ | ✖ | ✖ |
| Own wallet: consents, access log, share links, credentials (`student:wallet`) | ✖ | ✖ | ✖ | ✖ | ✔ own | ✖ | ✖ |
| Confirm / dispute enrolment (`student:confirm`) | assisted only | ✖ | ✖ | ✖ | ✔ own/guardian | ✖ | ✖ |
| Correction request (`corrections:request`) / decide (`corrections:decide`) | ✖ | ✖ | decide reg | ✖ | request own | ✖ | ✖ |
| Data-subject request (`dsr:request` / `dsr:decide`) | ✖ | ✖ | decide | ✖ | request own | ✖ | ✖ |
| Purpose-bound verification (`verifier:request`) | ✖ | ✖ | ✖ | ✖ | ✖ | ✔ own org | ✖ |
| Manage verifier orgs, suspend (`verifier_orgs:manage`) | ✖ | ✖ | read | ✔ | ✖ | ✖ | ✖ |
| Manage users (`users:manage`) | own institution | ✖ | ✖ | AICTE scope | ✖ | ✖ | ✖ |
| Manage API clients & webhooks (`clients:manage`) | own | ✖ | ✖ | AICTE-level clients | ✖ | ✖ | ✖ |
| Onboarding decision (`onboarding:decide`) | ✖ | ✖ | ✔ reg | ✖ | ✖ | ✖ | ✖ |
| Read audit trail, run integrity check (`audit:read`) | ✖ | ✖ | ✔ reg | ✔ | ✖ | ✖ | ✖ |
| Feeds (`feeds:write`) | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ | ✔ (scope) |
| Operations page (`ops:read`) | ✖ | ✖ | ✔ | ✔ | ✖ | ✖ | ✖ |
| Demo tools (`demo:tools`, `DEMO_MODE` only) | ✖ | ✖ | ✖ | ✔ | ✖ | ✖ | ✖ |
| Public status, `POST /public/verify` (share token), onboarding form, public JWKS | public (rate-limited) |||||||

Rules: an IA cannot grant AICTE roles or access another institution; AS has no default access to student data or institution dashboards; AR student-level access is always via grant; every denial is audited; unknown or cross-tenant resource IDs return `404`.

---

## 5. User Journeys, Workflows, Business Rules and State Transitions

### 5.1 Core workflow: the claim (Admission Gate)

#### 5.1.1 Sequence (single claim, also used per row by bulk commit)

| Step | Actor / component | Action | Tx? |
|---|---|---|---|
| 1 | Browser / ERP | `POST /claims` with `Idempotency-Key`. Body: programme, bucket, APAAR ID **or** provisional type + document number, student attributes, optional `admit_now`, `admission_date`, `row_key` | – |
| 2 | core-api | Verify JWT; permission `claims:write`; tenant check; idempotency lookup (replay / mismatch / in-flight) | – |
| 3 | core-api | Load institution, programme, intake version, bucket (read-only, local reference tables); reject inactive institution (`403 INSTITUTION_NOT_ACTIVE`) or unapproved programme (`422 PROGRAMME_NOT_APPROVED`) | – |
| 4 | Rules engine | Evaluate claim-path rules on the raw request. Any **Blocker** finding → `422 RULE_BLOCKER` (attempt logged, no seat) | – |
| 5 | Identity module | Hygiene → format/checksum → Aadhaar guard. Failure → `422 IDENTITY_INVALID` with fix suggestions. Provisional-ID path skips the registry and creates/uses a provisional token | – |
| 6 | Registry adapter | HTTP call to mock registry (2 s timeout, circuit breaker). Result: `FOUND(match scores)`, `NOT_FOUND`, or `UNAVAILABLE`. **Outside any DB transaction** | – |
| 7 | Identity module | Diagnose: `VERIFIED`, `PROBABLE_MATCH`, `PENDING_FIX` (with fix), `INVALID` (`NOT_FOUND` → `422 IDENTITY_INVALID` with suggestion "use a provisional ID if the APAAR was not generated"), `PENDING_VERIFICATION` (unavailable) | – |
| 8 | core-api | **Begin transaction** with `statement_timeout=3s`, `lock_timeout=2s`; `SET LOCAL app.institution_id/app.scope/app.actor_id` | **T** |
| 9 | Claims | Upsert `core.students` + `student_identity_keys` (`INSERT … ON CONFLICT`); reuse existing student if the identity key exists | T |
| 10 | Claims | `SELECT … FROM core.seat_buckets WHERE … FOR UPDATE` (the **only** contended lock) | T |
| 11 | Claims | Insert `core.enrolments`. A unique violation on `ux_enrol_exclusive` is resolved: holder is the same institution → `DUPLICATE_SAME_INSTITUTION`; otherwise `CONFLICT` (holder recorded internally only) → rollback | T |
| 12 | Claims | If `occupied + 1 > cap + approved_extra` → `SEAT_LIMIT` → rollback. Else `occupied += 1` | T |
| 13 | Claims | Insert `seat_ledger_entries(DEBIT, occupied_after)`, `enrolment_events(CLAIM)`, `enrolment_verification` (Lock 1 signed; Lock 2 from step 7; Lock 3 PENDING; deadline), `confirmation_requests` (+ OTP hash), encrypted `pii.pii_records`, `match name_tokens`, audit entry, outbox row (`claim.created`) | T |
| 14 | core-api | **Commit.** Store idempotent response. Respond `201` with claim id, `outcome`, verification state, seat snapshot and warnings | – |
| 15 | Async (jobs) | Notify queue sends OTP/SMS; projector updates `agg.seat_utilisation` and `agg.verification_funnel`; matching queue computes blocking keys and fuzzy candidates; sealer extends the audit chain; webhook `claim.created` delivered | – |

**Lock order and deadlock safety:** a claim holds at most one row lock (the bucket) before inserting into the unique index. Two claims for the same identity in different buckets cannot deadlock: the later one waits on the earlier uncommitted unique-index entry and, after the first commits, receives the unique violation.

**Lock 1 signature [DEC-09, AS-10]:** an ES256 signature by the institution's delegated key (`cred.signing_keys`, purpose `INSTITUTION_CLAIM`) over the SHA-256 digest of `{claim_id, institution_id, programme_id, bucket, identity_key_hash, claimed_at, actor_id}`. For single claims the authenticated IA session authorises the signature. **Bulk commit requires one step-up token** covering all rows of that job.

#### 5.1.2 Claim outcomes

| Situation | Outcome code | HTTP | Seat consumed | Notes / response content |
|---|---|---|---|---|
| Valid ID, seat free, no live exclusive claim, registry `VERIFIED` | `CLAIMED` | 201 | Yes | Claim id; verification `PENDING` (Lock 3 outstanding); warning `SEAT_90_PERCENT` if occupied ≥ 90% |
| Registry mismatch with diagnosed fix | `CLAIMED` (identity `PENDING_FIX`) | 201 | Yes | Overall state `PENDING_FIX`; fix-it list with deadline `fix_deadline_days` (default 14); never rejected |
| Registry "close but not certain" | `CLAIMED` (identity `PROBABLE_MATCH`) | 201 | Yes | Lock 2 `PENDING`; Warning finding `IDN-PROBABLE`; reviewer can waive (→ Lock 2 `PASSED`) |
| Registry unavailable (timeout / breaker open) | `CLAIMED` (identity `PENDING_VERIFICATION`) | 201 | Yes | Dashboard counter; retried by revalidation job; bounded by deadline; weighted in risk scoring |
| NOGEN / CONSENT_REFUSED / FOREIGN provisional path | `CLAIMED` (identity `NOGEN`/`CONSENT_REFUSED`/provisional) | 201 | Yes | Lock 2 `PROVISIONAL_OK`; provisional token and compliance deadline returned |
| Programme mode ODL / ONLINE / PART_TIME | `CONCURRENT_OK` | 201 | Yes | Outside the exclusivity index |
| Approved dual-programme exception pair | `EXCEPTION_APPROVED` | 201 | Yes | Outside the exclusivity index |
| Live exclusive claim at **another** institution this cycle | `CONFLICT` | 409 `CLAIM_CONFLICT` | No | `resolution_ref` + other institution's public nodal contact; never student data |
| Bucket at 100% and no approved exception | `SEAT_LIMIT` | 409 `SEAT_LIMIT` | No | Bucket, cap, occupied, `exception_requestable` |
| Same student already claimed at the same institution | `DUPLICATE_SAME_INSTITUTION` | 200 | No | Existing claim id, `duplicate=true` |
| Format/checksum failure, Aadhaar-like value, or ID not found | `INVALID` | 422 `IDENTITY_INVALID` | No | Field-level fix suggestions |
| Blocker rule finding | `RULE_BLOCKED` | 422 `RULE_BLOCKER` | No | Findings with fixes |
| Institution not active / programme not approved | – | 403 / 422 | No | Codes `INSTITUTION_NOT_ACTIVE`, `PROGRAMME_NOT_APPROVED` |

#### 5.1.3 After the claim: admit, withdraw, release

| Action | Actor | Rules | Effects |
|---|---|---|---|
| **Admit** | IA | Status must be `CLAIMED`; `admission_date` ≤ today and within window (rule FLD-004/005); rules re-evaluated | Status `ADMITTED`; event `ADMIT`; `cumulative_admitted += 1`; credential becomes eligible once `VERIFIED` |
| **Withdraw** | IA | Status `CLAIMED`/`ADMITTED`; reason code (`STUDENT_WITHDREW, ADMISSION_CANCELLED, FEE_NOT_PAID, DATA_ERROR, OTHER`); effective date ≥ claim date | Status `WITHDRAWN`; `RELEASE` ledger entry; `occupied -= 1`; pending confirmation closed; credential auto-revoked `ENROLMENT_ENDED`; webhook; excluded from `ux_enrol_exclusive` so the student can be claimed elsewhere |
| **Transfer out** | IA | Effective date; destination institution optional | Status `TRANSFERRED_OUT`; `RELEASE`; transfer row `OUT_RECORDED` |
| **Transfer in** | IA (receiving) | References the out-record (or none → `TRANSFER_PENDING` class) | New claim with class `TRANSFER_PENDING` until matched, then `EXCLUSIVE` |
| **Release (AICTE)** | AR (step-up) | Reason mandatory; opens a case | Status `WITHDRAWN` with `reason=AICTE_RELEASE`; ledger `RELEASE`; institution notified |

#### 5.1.4 Seat exception workflow
IA requests (extra seats, justification, evidence note) when a bucket is ≥ 90% or `SEAT_LIMIT` was hit → status `PENDING` → AR (regional scope) approves (with `expires_at`, optional fewer seats) or rejects (reason) → on approval: `approved_extra += n`, ledger `EXCEPTION_GRANT`, notification, outbox. Expiry job returns `approved_extra` to 0 only for seats not yet used (never evicts). The Trust Index counts requested exceptions.

### 5.2 Bulk upload workflow

| Step | Who | What |
|---|---|---|
| 1 | IO/IA | Downloads template v1, fills in; browser validates in a Web Worker (required fields, formats, enumerations, row-level) and shows errors before upload |
| 2 | IO/IA | `POST /upload-jobs` → job `UPLOADING`; `PUT /upload-jobs/{id}/content` (file body); `POST …/complete` |
| 3 | worker `ingest` | `SCANNING` (extension, magic bytes, size, XLSX macro + zip-bomb checks, row cap, CSV-formula flags; malware scan **NOT_PERFORMED [SIM]**) |
| 4 | worker | `PARSING` into chunks (500 rows, leased) → staged rows encrypted with the tenant data key |
| 5 | worker | `VALIDATING` = **dry-run**: per row identity hygiene + registry **diagnosis** (rate-limited to 20 calls/s, circuit breaker), rules, in-file and in-institution duplicates, seat headroom projection; **no seat consumed**. Outcomes per row: `ACCEPTED` (no Blocker/Error), `HELD` (Errors with fix), `REJECTED` (Blockers), `NOOP_DUPLICATE` (identical committed row key) |
| 6 | IO/IA | Reviews row table, `errors.csv`, diff; fixes the file and re-uploads (a new job; stable `row_key`s keep it idempotent) |
| 7 | IA (≠ uploader) | `POST /upload-jobs/{id}/commit` (step-up) when status is `READY_TO_COMMIT`. Job `COMMITTING` |
| 8 | workers `bulk_claim` | For each `ACCEPTED` row run §5.1.1 steps 4–14 with source `BULK`; results `COMMITTED` or `COMMIT_FAILED(outcome)` (`CONFLICT`, `SEAT_LIMIT`, `IDENTITY_INVALID` …) |
| 9 | system | Job `COMPLETED` (all committed) or `PARTIAL`; webhook `job.completed`; findings created for Warnings; notification to uploader and committer |
| 10 | IA | Retries failed rows after fixes via a new job; `retry-failed-chunks` re-runs chunks that crashed (not failed rows) |

Seat headroom projection in the dry-run is advisory: the real check always happens in the claim transaction at commit time, so a dry-run `ACCEPTED` row may still fail with `SEAT_LIMIT` or `CONFLICT` if the world changed.

### 5.3 Triple-Lock workflow

```
claim committed ──► Lock1 SIGNED (institution)
                 ──► Lock2: registry result  → PASSED | PROVISIONAL_OK | PENDING | FAILED(fix)
                 ──► Lock3: confirmation_request(OTP) sent via mock SMS
Student path (adult):   portal "Confirm" → OTP entered → CONFIRMED
Minor path:             guardian_link verified → guardian OTP → guardian mode confirm → CONFIRMED (channel GUARDIAN)
No-phone path:          IA records ASSISTED confirmation (reason, witness) → CONFIRMED (channel_trust LOW)
Dispute:                student "This is not me / I did not enrol" → DISPUTED → case opened
Day 7, Day 21:          reminders (SMS → email → in-app)
Day 30 (deadline):      no Lock3 → UNCONFIRMED (flag only; seat stays occupied) → Trust Index input
All three agree         → overall VERIFIED → credential eligible
```

OTP rules: 6 digits; stored as salted hash; TTL 10 min; max 5 verification attempts per OTP; max 3 sends per hour per request (`429 OTP_RESEND_LIMIT`); resend by student or IA (`confirmation:resend`). Delivery goes to the **registry-held mobile** (channel `REGISTRY_MOBILE`, trust `HIGH`) when the mock registry returns one, otherwise the institution-supplied mobile (`INSTITUTION_MOBILE`, trust `LOW`); provisional-ID students always use `INSTITUTION_MOBILE` or `ASSISTED`. The message is localised (en/hi) and masked in all UIs (`+91 ••••• ••123`). In the prototype the SMS appears in the **Dev SMS Inbox [SIM]**.

### 5.4 State machines

#### 5.4.1 Enrolment and verification (Enrolment machine is event-sourced)

| From | Event | To | Actor | Guard / notes |
|---|---|---|---|---|
| – | `CLAIM` | `CLAIMED` | IA / CL | §5.1.1; creates verification row |
| `CLAIMED` | `ADMIT` | `ADMITTED` | IA | `admission_date` valid |
| `CLAIMED`, `ADMITTED` | `WITHDRAW` | `WITHDRAWN` | IA | releases seat |
| `CLAIMED`, `ADMITTED` | `SEAT_RELEASE` | `WITHDRAWN` | AR | case opened |
| `ADMITTED` | `TRANSFER_OUT` | `TRANSFERRED_OUT` | IA | releases seat |
| any live | `CORRECT` | unchanged | AR (approved request) / IA (data fix) | new vault version; reason mandatory |
| any live | `IDENTITY_UPGRADE` | unchanged | IA / AR | provisional → APAAR; may raise `CONFLICT` |
| any live | `VERIFICATION_CHANGE` | unchanged | system | records lock transitions |

Verification overall state: `PENDING → VERIFIED` (Lock 2 PASSED/PROVISIONAL_OK and Lock 3 CONFIRMED) · `PENDING → UNCONFIRMED` (deadline) · `PENDING/VERIFIED/UNCONFIRMED → DISPUTED` (student dispute) · `PENDING → PENDING_FIX` (Lock 2 FAILED with fix) · `PENDING_FIX → PENDING` (institution corrected, revalidation) · `UNCONFIRMED → VERIFIED` (late confirmation allowed; event recorded) · `DISPUTED → VERIFIED|WITHDRAWN` only through a case outcome decided by AR.

Lock 2: `PENDING → PASSED | FAILED | PROVISIONAL_OK`; `PENDING_VERIFICATION` identities stay `PENDING` until revalidation. Lock 3: `PENDING → CONFIRMED | EXPIRED | DISPUTED`.

#### 5.4.2 Submission (institution, academic year, window)

| From | Action | To | Actor | Guard |
|---|---|---|---|---|
| (none) | create | `DRAFT` | IA/IO | one per (institution, year, window) |
| `DRAFT`, `CORRECTED` | validate | `VALIDATED` | IA/IO | readiness check passes (no open Blocker/Error); declared totals present |
| `VALIDATED` | submit | `SUBMITTED` | IA | Warnings acknowledged; window open or extension; `late_flag` set if after close |
| `SUBMITTED` | accept | `ACCEPTED` | AR | `approved_by ≠ submitted_by` |
| `SUBMITTED` | request review | `UNDER_REVIEW` | AR | notes mandatory |
| `UNDER_REVIEW` | institution corrects | `CORRECTED` | IA | events/data fixes recorded; revalidate then resubmit (`CORRECTED → VALIDATED → SUBMITTED`) |
| `ACCEPTED` | sign + countersign commitment | `LOCKED` | IA signs root (step-up), AR countersigns (step-up) | Locked **means** a signed cut-off commitment, not frozen data |
| `LOCKED` | approved post-lock correction | `LOCKED` (new commitment version) | AR approves, IA re-signs | prior commitment `SUPERSEDED` |

#### 5.4.3 Upload job
`UPLOADING → SCANNING → QUEUED → PARSING → VALIDATING → READY_TO_COMMIT → COMMITTING → COMPLETED | PARTIAL`; any state may go to `FAILED` (reason code) or `CANCELLED` (by uploader before commit). Re-upload creates a new job.

#### 5.4.4 Seat exception and window extension
`PENDING → APPROVED | REJECTED` (AR); `APPROVED → EXPIRED` (time). Window extension: `PENDING → APPROVED | REJECTED`.

#### 5.4.5 Duplicate candidate and transfer
Candidate: `(created) → AUTO_LINKED | REVIEW`; `REVIEW → DISMISSED | CONFIRMED_DUP | (link as transfer) AUTO_LINKED`; `DISMISSED` never re-created for the same pair. `CONFIRMED_DUP` creates a Finding for both institutions and a case. Transfer: `OUT_RECORDED → MATCHED` (IN recorded referencing it) · `OUT_RECORDED → ORPHAN_LEAD` (after `orphan_lead_days`) · `ORPHAN_LEAD → MATCHED | DISMISSED`.

#### 5.4.6 Credential
`VALID → REVOKED` (AR, or system `ENROLMENT_ENDED`) · `VALID → SUPERSEDED` (IA/AR; e.g. name correction) · no transitions out of `REVOKED`/`SUPERSEDED`; the status-list bit is set for both.

#### 5.4.7 Case and appeal
`OPEN → AWAITING_RESPONSE` (AR requests institution response, due in 14 days) `→ UNDER_REVIEW` (response received or overdue) `→ CLOSED` (outcome code). `CLOSED → APPEALED` (IA within 15 days) `→ CLOSED` (different AR decides: `UPHELD` or `OVERTURNED`; label updated). Student disputes and auto-opened cases start at `OPEN`.

#### 5.4.8 Rule set
`DRAFT` (AS, editable) → `PUBLISHED/ACTIVE` (AR ≠ drafter, step-up; previous → `RETIRED`) → `RETIRED`. A published set is immutable; rollback re-activates a `RETIRED` set (new activation event). Only one set is ACTIVE.

#### 5.4.9 Supporting machines
Consent: `GRANTED | REFUSED → WITHDRAWN | EXPIRED`. Correction request: `PENDING → APPROVED | REJECTED`. Data-subject request: `OPEN → IN_REVIEW → FULFILLED | REJECTED_LAWFUL_RETENTION | REJECTED_OTHER`. Onboarding: `PENDING → APPROVED | REJECTED`. Confirmation request: `SENT → CONFIRMED | EXPIRED | FAILED`. Guardian link: `PENDING → VERIFIED | REVOKED`.

#### 5.4.10 Job and webhook delivery
Job: `QUEUED → RUNNING → SUCCEEDED | FAILED(retry → QUEUED) | DEAD`; expired lease `RUNNING → QUEUED`. Webhook delivery: `PENDING → DELIVERED | RETRYING → DELIVERED | DEAD_LETTERED → (replay) PENDING`.

### 5.5 User journeys by role

**IA — Institution admin (e.g. `ia.kit`).** Sign in → dashboard shows seats by programme × bucket (amber ≥ 90%, red 100%), claims awaiting confirmation, PENDING_FIX list, findings, submission status → *Claim a seat*: choose programme and bucket, enter APAAR ID and details; sees the verdict instantly (warnings, fix-it, or conflict/limit) → admits the student → resends OTP or records assisted confirmation → handles transfers → commits a bulk job prepared by the operator → requests a seat exception when a bucket is full → validates and submits the year's submission → signs the cohort commitment (step-up) → issues credentials for verified students → responds to AICTE cases and may appeal → manages operators, API clients and webhooks.

**IO — Data operator (e.g. `io.kit`).** Download template → pre-validate → upload → read row-level errors with fixes → fix and re-upload → run claim validation (dry-run) for individual records → run submission readiness check. Cannot commit or submit; the UI shows "Waiting for institution admin to commit".

**AR — AICTE reviewer (e.g. `ar.national`, `ar.south`).** Dashboard: national KPIs, utilisation, state/district heat table, alert inbox sorted by severity, Trust Index scoreboard (explainable components) → open an alert → creates a case (response window) → requests an elevation (case + justification) to reveal student-level evidence → reviews duplicate candidates (scores and per-field contributions; reveal under grant) → decides seat exceptions, corrections, DSRs, onboarding, window extensions → waives Warnings with reason codes → accepts or reviews submissions, countersigns commitments → publishes rule sets drafted by AS after viewing the simulation → revokes credentials → releases abusive claims → reads audit and runs the integrity check.

**AS — System admin (e.g. `as.admin`).** Drafts rule sets, runs the policy simulator, manages AICTE users and verifier organisations (suspend/activate), uses Demo Tools (registry down/slow, clock advance, run job now, reset), reads the Operations page and audit.

**ST — Student (e.g. `st.diya`).** Sign in → sees enrolment at the institution with three locks → receives OTP in the Dev SMS Inbox → confirms (or disputes) → sees credential and certificate → reviews the access log ("who has looked at my record") → handles a verifier's consent request (grant/refuse) → creates/revokes share links → raises a correction request → files a data-subject request → verifies an inclusion proof. **Guardian (`gd.meera`):** switches to the linked minor and confirms/consents on their behalf.

**VU — Verifier (e.g. `vu.employer`).** Pick purpose and question, enter a credential reference (or scan the QR) → receives either a signed YES/NO with the disclosed fields and a disclosure meter, or the neutral response → sees usage and limits.

**CL — ERP client.** Obtain token (client credentials) → `POST /claims` with idempotency keys → receive webhooks (`claim.created`, `confirmation.completed`, `job.completed`) → push liveness feeds (feeds scope).

**PUB.** Scans QR → live status page; registers an institution via the onboarding form; verifies a share token.

### 5.6 Business rules, formulas and algorithms

#### 5.6.1 Trust Index (all weights in `ops.system_config.trust_weights`)

For institution *i*, academic year *y*, computed on all non-withdrawn enrolments:

| Component (max pts) | Score formula | Evidence link |
|---|---|---|
| Triple-Lock confirmation (30) | `30 × VERIFIED / (VERIFIED + UNCONFIRMED + DISPUTED + PENDING_older_than_14d)`; denominator 0 → 30 | enrolment_verification rows |
| Open anomaly alerts (20) | `20 × (1 − min(1, Σ w / 5))`, `w` = 1.0 HIGH, 0.5 MEDIUM, 0.2 LOW per OPEN/IN_CASE alert | anomaly_alerts |
| Claim conflicts (15) | `15 × (1 − min(1, rate / 50))`, `rate` = conflict attempts per 1,000 attempts | claim_attempts |
| Submission timeliness (15) | on time 15; late flag 7.5; window closed with no submission 0; window still open 15 | submissions |
| Intake gate hits / exceptions (10) | `10 × (1 − min(1, (SEAT_LIMIT attempts + exceptions requested) / 10))` | claim_attempts, seat_exceptions |
| Correction rate (10) | `10 × (1 − min(1, corrections ÷ enrolments / 0.15))` | enrolment_events |

Total = sum, rounded to 1 decimal. Bands per REQ-INT-02. Config version is stored with each score. The UI shows each component with points, the formula inputs and a link to the evidence list.

#### 5.6.2 Identity comparison (registry response vs institution data)

* **Name similarity** = max(RapidFuzz `token_set_ratio`/100 on normalised Latin forms, phonetic-key agreement score). Normalisation order: Unicode NFKC → transliterate Indic scripts → lowercase → expand `Md/Mohd → Mohammed`, `Kum/Km → Kumari` → expand initials against the registry name → remove honorifics (Shri, Smt, Mr, Ms, Dr).
* **DOB**: `EXACT`; `DAY_MONTH_SWAP` when swapping day and month (day ≤ 12) equals the registry DOB; `YEAR_TYPO` when only the year differs and the last two digits agree or the years differ by exactly 1, 10 or 100; else `MISMATCH`.
* **Verdict:** name ≥ 0.92 and DOB `EXACT` → `VERIFIED`. Name ≥ 0.80 and DOB `EXACT` → `PROBABLE_MATCH`. Name ≥ 0.80 with DOB `DAY_MONTH_SWAP`/`YEAR_TYPO` → `PENDING_FIX` (fix: correct the DOB). Name < 0.80 with DOB `EXACT` → `PENDING_FIX` (fix: correct the name; message shows similarity). Name < 0.80 and DOB mismatch → `INVALID` with "ID belongs to a different person" **without** disclosing the registry's values. Registry `DEACTIVATED/SUSPENDED` status → `PENDING_FIX` with message to contact the registry.
* Fix suggestions never reveal registry personal data beyond what the institution already supplied; they state the *type* of mismatch ("DOB appears to have day and month swapped").

#### 5.6.3 Duplicate scoring and timeline

* Bloom filters: 1,024 bits, 20 keyed hash functions over character bigrams (padded), keyed by a salt in `MASTER_KEY`-derived material. Score = `0.5·dice(name) + 0.3·dob_match + 0.2·dice(father_name)` where `dob_match` = 1 for exact (via K-key equality or Bloom Dice = 1), 0 otherwise.
* **Timeline class** for a pair: same institution and different year → `SAME_INST_READMIT`; either enrolment's class is `CONCURRENT_OK` and the other `EXCLUSIVE` → `FT_PLUS_ODL`; pair matches an `enrolment_exception_rules` entry → `EXCEPTION`; intervals `[admission_date or claimed_at, end or open]` overlap → `OVERLAP_FT`; else `SEQUENTIAL`.
* Storage: `REVIEW` if score ≥ 0.80 and class ∈ {`OVERLAP_FT`, `SEQUENTIAL`}; `AUTO_LINKED` if score ≥ 0.97 and `SEQUENTIAL` (probable transfer, requests Transferred Out/In); `FT_PLUS_ODL`, `EXCEPTION`, `SAME_INST_READMIT` are stored for the record with status `AUTO_LINKED` and not queued.
* Blocks bigger than `max_block_size` (50) → one `REVIEW` item per block flagged `BLOCK_TOO_LARGE`.

#### 5.6.4 Seeded rule set v1 (illustrative; thresholds are data)

Call sites: **C** claim, **B** bulk/dry-run, **S** submission.

| Code | Category | Kind | Sev. | Check | Sites |
|---|---|---|---|---|---|
| IDN-001 | identity | REQUIRED_FIELD | Blocker | `apaar_id` or `provisional_type` present | C B |
| IDN-002 | identity | REGEX_FORMAT | Blocker | APAAR passes `mock_v1` format + checksum | C B |
| IDN-003 | identity | REGEX_FORMAT (forbid) | Blocker | no Aadhaar-like 12-digit Verhoeff-valid value in any non-ID field | C B |
| IDN-004 | same-institution duplicates | LOOKUP_IN_REFERENCE (must not exist) | Blocker | identity key not already live at this institution (same year) | C B |
| IDN-005 | identity | IMPLIES | Error | `citizenship_type ∈ {FOREIGN, OCI}` ⇒ `document_number` present | C B |
| IDN-PROBABLE | identity | (engine) | Warning | registry `PROBABLE_MATCH` | C B |
| FLD-001 | data integrity | REQUIRED_FIELD | Error | `student_name, dob, gender, category, institution_student_id, programme_code, bucket_type` present | C B |
| FLD-002 | data integrity | LOOKUP_IN_REFERENCE | Blocker | `programme_code` exists, active, approved for this year | C B |
| FLD-003 | data integrity | COMPARE | Warning | age at admission between 15 and 35 (DIPLOMA 14–35) | C B |
| FLD-004 | cross-field | DATE_WITHIN | Error | `admission_date` inside the submission window (or approved extension) | B S, admit |
| FLD-005 | cross-field | COMPARE | Error | `admission_date ≤ counselling_cutoff_date` (config) | B S, admit |
| CAT-001 | category consistency | IMPLIES | Error | `citizenship_type = FOREIGN` ⇒ `nationality_code ≠ IN` | C B |
| CAT-002 | category consistency | MUTUALLY_EXCLUSIVE | Error | `citizenship_type = FOREIGN` with `domicile_quota = Y` | C B |
| CAT-003 | category consistency | LOOKUP_IN_REFERENCE | Error | `category ∈ {GEN, OBC, SC, ST, EWS}` | C B |
| CAT-004 | category consistency | IMPLIES | Error | `bucket_type = SN_FN` ⇒ `citizenship_type = FOREIGN` | C B |
| CAT-005 | category consistency | IMPLIES | Error | `bucket_type = SN_EWS` ⇒ `category = EWS` | C B |
| XFD-001 | cross-field | IMPLIES | Error | minor (age < 18 at admission) ⇒ `guardian_name` and `guardian_phone` present | C B |
| XFD-002 | cross-field | IMPLIES | Info | programme mode ODL/ONLINE/PART_TIME ⇒ class `CONCURRENT_OK` | C B |
| INT-001 | intake | BUCKET_CAP | Blocker | `occupied + 1 ≤ cap + approved_extra` | C |
| INT-002 | intake | BUCKET_CAP | Warning | occupied ≥ `seat_warn_pct` of cap | C |
| AGG-001 | aggregate reconciliation | SUM_RECONCILES | Error | rows per programme = declared enrolment | S |
| AGG-002 | aggregate reconciliation | SUM_RECONCILES | Blocker | total occupied ≤ approved intake + approved extra | S |
| AGG-003 | aggregate reconciliation | SUM_RECONCILES | Blocker | every bucket occupied ≤ cap + approved extra | S |
| AGG-004 | aggregate reconciliation | SUM_RECONCILES | Warning | PENDING_VERIFICATION + PENDING_FIX share ≤ 10% | S |

**Draft v2 (seeded as `DRAFT`, used for the simulator demo):** FLD-003 raised to **Error** and narrowed to 16–30; XFD-001 widened to age < 19; new **AGG-005** (Warning): total supernumerary occupied ≤ 10% of regular cap. The simulator reports only rules whose inputs exist in the non-PII enrolment snapshot (age is approximated by `admission_year − birth_year`); skipped rules are listed as "not simulated".

#### 5.6.5 Cohort commitment (Merkle)
Leaf = `SHA-256(0x00 ‖ canonical_json(enrolment_leaf))` where `enrolment_leaf = {enrolment_id, programme_code, bucket_type, status, admission_date, verification_state, identity_key_hash, rule_set_id}`; node = `SHA-256(0x01 ‖ left ‖ right)`; odd node promoted (RFC 6962). Leaves sorted by `enrolment_id`. Institution signature and AICTE countersignature are ES256 over `{institution_id, academic_year, cutoff_at, version, merkle_root, leaf_count}`.

#### 5.6.6 Signed verification answer
ES256 JWT, header `kid` = verifier-attestation key: `{iss, aud: <verifier org id>, jti: request id, nonce, iat, exp = iat+300, credential_ref, question, answer: "YES"|"NO", fields: {…allowed ∩ requested…}, status_list_checked_at}`. A verifier must reject answers after `exp` and with a nonce they did not supply.

#### 5.6.7 Consent semantics
Statutory enrolment recording does not depend on consent (AS-03); consent governs **sharing** with verifier organisations by purpose and field scope. A share link is itself a consent, scoped to its fields, purpose and expiry. Refusing APAAR consent records `REFUSED` and never blocks counting (C-09). Withdrawal takes effect on the next request.

---

## 6. System Architecture

### 6.1 Architecture decision (retained from the source)

**Modular monolith.** The claim gate must change identity, seat ledger, enrolment, event store and outbox in **one ACID transaction**, so the platform is one codebase deployed as several process types, not a set of services. [SRC §3.1] Each module owns its database schemas and exposes a Python service interface; other modules call that interface or consume outbox events and **never read another module's tables** (enforced by `import-linter` in CI). Matching, reports and credential signing are the first candidates to split later. Microservices, serverless, Kafka, Kubernetes, graph DB, warehouse, search engine, blockchain, ZK proofs and PSI are not used. [SRC §4.3]

### 6.2 Prototype topology

```
                       ┌───────────────────────── Browser ─────────────────────────┐
                       │  React SPA  (Institution · AICTE · Student · Verifier)     │
                       │  Web Worker: Papa Parse / SheetJS / Zod pre-validation     │
                       └───────┬───────────────────────────────┬────────────────────┘
                 HTTPS/JSON    │ Bearer JWT                    │ public pages /v/:ref, /api verify
                               ▼                               ▼
   ┌───────────────────────────────────────┐        ┌────────────────────────┐
   │ core-api  :8000  (FastAPI)            │        │ verify-api  :8001      │  separate process,
   │  M01–M15 modules · RBAC/ABAC · RLS ctx │        │  status, JWKS, status  │  narrow read access,
   │  idempotency · problem+json · audit    │        │  lists, verify answers │  own rate limits
   └───────┬───────────────┬────────────────┘        └───────────┬────────────┘
           │ SQL (psycopg3) │ enqueue jobs / outbox                │ SQL (narrow views)
           ▼                ▼                                      ▼
   ┌───────────────────────────────────────────────────────────────────────────┐
   │ PostgreSQL 16  :5432   schemas: ref iam pii core identity ingest rules    │
   │  matching intel cred consent audit ops agg  (+ mockext, seed)             │
   │  single source of truth · row locks · partial unique index · RLS · CHECKs │
   └───────▲────────────────────────▲──────────────────────────▲───────────────┘
           │ jobs (SKIP LOCKED)      │ outbox                    │
   ┌───────┴────────────┐   ┌────────┴───────────────┐   ┌──────┴───────────────┐
   │ worker (+scheduler)│   │ projector / sealer     │   │ mock-services :8002  │
   │ queues: ingest,    │   │ (scheduler tasks,      │   │ [SIMULATED] registry,│
   │ bulk_claim, …      │   │  advisory-lock single) │   │ SMS+inbox, master,   │
   └─────────┬──────────┘   └────────────────────────┘   │ ABC/exam feed        │
             │ HTTP (timeouts, breaker)                   └──────────────────────┘
             └───────────────────────────────────────────────▲
   Local filesystem: data/objects (uploads, reports), data/worm (simulated WORM), data/keys (wrapped keys)
```

| Process | Command | Port | Purpose |
|---|---|---|---|
| `postgres` | `docker compose up -d db` | 5432 | System of record |
| `core-api` | `sentinel api` | 8000 | All authenticated routes |
| `verify-api` | `sentinel verify-api` | 8001 | Public credential status, JWKS, status lists, verify |
| `worker` | `sentinel worker` | – | Job queue consumers **and** scheduler (leader-elected) |
| `mock-services` | `sentinel mock` | 8002 | Simulated registry, SMS/email, AICTE master, ABC/exam feed |
| `web` | `npm run dev` (or nginx static in Compose) | 5173 / 8080 | SPA |

### 6.3 Layers and responsibilities

| Layer | Components | Technology (prototype) | Responsibility |
|---|---|---|---|
| 1 Users and frontend | Four portals in one SPA; public verifier page | React 18, TypeScript, Vite, Ant Design 5, TanStack Query, Zustand, React Router, Apache ECharts, Papa Parse, SheetJS, Zod, i18next | UI, sign-in, in-browser upload pre-validation, polling refresh |
| 2 Application | core-api, verify-api | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (explicit SQL on claim path), Alembic, psycopg 3 | Business logic in modules M01–M15; auth, RBAC/ABAC; problem+json; idempotency |
| 3 Data and storage | PostgreSQL, local ObjectStore | PostgreSQL 16, filesystem | Transactional truth; files (uploads, reports); keys |
| 4 Asynchronous | worker, scheduler, projector, sealer | Postgres-backed queue (DEC-02), threading + asyncio | Bulk jobs, matching, intelligence, reports, notifications, webhooks, projections |
| 5 External integrations | Adapters + mock-services | httpx, FastAPI (mock) | See §6.7 |
| 6 Cross-cutting | Access control, data protection, evidence, ops | RBAC/ABAC, RLS, `cryptography`, structlog | See §9 |

### 6.4 Module map (M01–M15)

Package root `backend/app/modules/<name>/` with `service.py` (public interface), `repo.py` (SQL), `models.py` (SQLAlchemy/Pydantic), `events.py` (outbox topics), `api.py` (router). A module imports only other modules' `service.py`/`events.py`.

| Mod | Package | Schemas | Responsibilities | Collaborates with |
|---|---|---|---|---|
| M01 | `iam` | `iam`, `pii.access_grants` | Users, roles, scopes, sessions, API clients, verifier orgs, idempotency store, elevation grants | all (permission checks) |
| M02 | `refdata` | `ref` | Institutions, programmes, intake versions, buckets, windows, exception rules; master sync; system config | M05 (read) |
| M03 | `ingest` | `ingest` | Templates, upload jobs, chunks, staged rows, dry-run, diff, submissions, correction requests | M04, M05, M06 |
| M04 | `identity` | `identity`, `pii` | Hygiene, format/checksum, registry adapter use, normalisation, diagnosis, provisional IDs, keyed hashing, PII vault | M05, M07 |
| M05 | `claims` | `core` | Gate, seat buckets/ledger, exceptions, enrolments, events, withdrawals, transfers | M04, M06, M08 |
| M06 | `rules` | `rules` | Rule sets, evaluation, findings, waivers, simulator | M03, M05, M13 |
| M07 | `matching` | `matching` | Features, blocking, scoring, timeline, review queue | M05 events, M09 |
| M08 | `trust` | `core.enrolment_verification`, `core.confirmation_requests` | Triple-Lock state, confirmations, deadlines, disputes | M11, M15 |
| M09 | `intel` | `intel` | Trust Index, alerts, graph clusters, liveness, cases, appeals | M07, M08 |
| M10 | `credentials` | `cred` | Issuers, keys, credentials, status lists, certificates, share links, verification requests | M08, M11 |
| M11 | `consent` | `consent` | Notices, consent ledger, guardian links, DSRs | M08, M10 |
| M12 | `audit` | `audit` | Audit log, sealer, checkpoints, commitments, integrity check | all |
| M13 | `analytics` | `agg`, `ops.report_jobs` | Projector, aggregate tables, dashboards API, reports, retention | outbox, M06 |
| M14 | `integration` | `ops.webhook_deliveries`, `iam.webhook_endpoints` | Idempotency, webhooks, adapter framework, feed endpoints | external |
| M15 | `notify` | `ops.notifications` | Templates, localisation, OTP/SMS/email dispatch, retries, fallbacks | M08, M13 |

`import-linter` contracts: (1) *layers*: `api → service → repo`; (2) *independence*: modules do not import each other's `repo`/`models`; (3) *forbidden*: `analytics.agg_repo` must not import `claims.repo`; `verify_api` may import only `common`, `credentials.public_views`. CI fails on violation.

### 6.5 Outbox topics and consumers

Topics (payloads carry ids only): `claim.created`, `claim.pending_verification_resolved`, `enrolment.admitted`, `enrolment.withdrawn`, `enrolment.transferred`, `verification.changed`, `confirmation.completed`, `finding.created`, `seat.threshold_crossed`, `exception.decided`, `submission.state_changed`, `upload.completed`, `credential.issued`, `credential.revoked`, `case.updated`, `consent.changed`, `commitment.completed`, `ruleset.published`.

| Consumer (scheduler task / job) | Reads topics | Effect |
|---|---|---|
| Projector | all business topics | Updates `agg.*` |
| Webhook fan-out | `claim.*`, `confirmation.completed`, `finding.created`, `credential.revoked`, `case.updated`, `upload.completed` | Creates `webhook_deliveries` |
| Match-feature builder | `claim.created`, `enrolment.*` | Computes features, candidates |
| Credential guard | `enrolment.withdrawn/transferred` | Auto-revokes credential |
| Notification triggers | threshold, verification, exception, case topics | Enqueues notifications |
| Trust recompute trigger | `submission.state_changed`, `case.updated`, `verification.changed` | Marks institution dirty for the nightly/on-demand Trust Index run |

### 6.6 Repository layout

```
apaar-sentinel/
├── README.md                    quick start, demo accounts, scenario script
├── docker-compose.yml           db (default) + optional app profile (api, verify, worker, mock, web)
├── .env.example
├── scripts/                     setup.ps1 · setup.sh · dev.ps1 · dev.sh · mock_erp_client.py · load/claim_hammer.py
├── docs/                        runbooks/ · demo-script.md · security-notes.md
├── backend/
│   ├── pyproject.toml · alembic.ini · importlinter.ini
│   ├── app/
│   │   ├── main.py  config.py  cli.py  clock.py  errors.py  logging.py
│   │   ├── common/              db.py rls.py ids.py(uuidv7) crypto/ keys.py objectstore.py
│   │   │                        idempotency.py pagination.py permissions.py ratelimit.py
│   │   ├── modules/             iam refdata ingest identity claims rules matching trust
│   │   │                        intel credentials consent audit analytics integration notify
│   │   ├── adapters/            registry.py sms.py email.py master.py feeds.py stubs.py
│   │   └── worker/              runner.py scheduler.py handlers.py
│   ├── verify_api/              main.py views.py
│   ├── mock_services/           main.py registry.py sms.py master.py feeds.py
│   ├── migrations/versions/     Alembic (expand-then-contract)
│   ├── seed/                    profiles.py generators/ data/ loaders.py stories.py verify.py ground_truth.py
│   └── tests/                   unit/ integration/ workflow/ authz/ concurrency/ property/ seed/ api_contract/
└── web/
    ├── package.json · vite.config.ts
    ├── src/ portals/{institution,aicte,student,verifier}/  shared/  i18n/{en,hi}/  api/(generated client)
    └── tests/ (vitest) · e2e/ (playwright)
```

### 6.7 Integration design (adapters and mock services)

All external systems are accessed through adapter interfaces. In the prototype every adapter points at **mock-services** (process on :8002), which behaves like a real HTTP service. Every mock response includes `"source": "MOCK_<SYSTEM>"` and every UI view that shows it carries the `SIMULATED` badge.

| Adapter (interface) | Real system | Prototype implementation | Endpoints (mock) | Behaviour on failure (core) |
|---|---|---|---|---|
| `RegistryAdapter` | APAAR registry | `HttpRegistryAdapter` → mock | `GET /registry/v1/persons/{apaar_id}` → `{apaar_id, status(ACTIVE/DEACTIVATED/SUSPENDED), name, dob, mobile, source}`; 404 unknown | 2 s timeout; circuit breaker (5 failures → open 30 s); claim `PENDING_VERIFICATION`; jittered retries |
| `AicteMasterAdapter` | AICTE intake master | `HttpMasterAdapter` → mock | `GET /aicte-master/v1/export` | Sync job retries; gate unaffected (local tables) |
| `SmsGateway` | DLT SMS gateway | `HttpSmsGateway` → mock | `POST /sms/v1/messages`, `GET /sms/v1/messages?to=` (inbox) | Retries with backoff → fallback EMAIL → INAPP |
| `EmailGateway` | SMTP | `HttpEmailGateway` → mock | `POST /email/v1/messages` | Retries, INAPP fallback |
| `LivenessFeedAdapter` | ABC / exam / NAD feeds | Mock feed pusher → core `POST /feeds/liveness` | `POST /control/feeds/push` | Feed errors logged; no value claims |
| `DigiLockerAdapter`, `AbcAdapter`, `NadAdapter` | National systems | Stub raising `NotAvailableInPrototype` | – | Not exposed in UI |
| `KeyProvider` | KMS/HSM | `LocalKeyProvider` (wrapped by `MASTER_KEY`) | – | Fails closed for signing/decrypt |
| `ObjectStore` | Supabase Storage/S3 | `LocalFsObjectStore` | – | Fails fast |
| `Notifier`/`WebhookSender` | ERP endpoints | `httpx` + local sink | `POST /api/v1/dev/webhook-sink` | Retries 24 h then dead-letter |

**Mock-services scenario controls** (also exposed to the AICTE system admin through Demo Tools): `POST /control/registry {mode: UP|DOWN|SLOW|FLAKY, latency_ms, error_rate}`; `POST /control/sms {fail_rate}`. The mock registry holds `mockext.registry_people`: every seeded APAAR ID **plus ~300 unclaimed "spare" people** so demos can type new claims; mismatch cases are pre-seeded (day-month swap, initials, `Mohd/Mohammed`, Devanagari/Tamil name forms, deactivated ID).

### 6.8 Cross-cutting design patterns

* **Request context:** a dependency opens a DB transaction, runs `SET LOCAL app.institution_id/app.scope/app.actor_id/app.student_id`, and exposes a `RequestContext(actor, scope, request_id, clock)`. Repositories never accept a tenant id from client input.
* **Transactions:** claim path uses explicit SQL with `statement_timeout`/`lock_timeout`; elsewhere SQLAlchemy ORM. All business writes also write audit and outbox rows in the same transaction (`UnitOfWork`).
* **Errors:** domain exceptions carry a stable `code`; a single handler renders RFC 9457 with `request_id`, localisation and `retryable`.
* **Idempotency:** middleware-level for mutating POST routes flagged `idempotent` (everything except `/auth/*` and OTP verify); stores `(actor_id, key)` → request hash + response.
* **Clock:** every time-dependent decision reads `Clock.now()`; tests inject a fake clock; demo offset in `ops.system_config`.
* **Crypto:** `common/crypto` wraps AES-256-GCM (vault), HMAC-SHA256 (identity keys, blind-index tokens, Bloom salts, OTP salt), ECDSA P-256 / ES256 (JWTs, credentials, commitments, checkpoints). No custom primitives. Identity HMAC key versioned (`key_version`).
* **PII vault:** `pii.pii_records.ciphertext` = AES-256-GCM(JSON{name, name_native, dob, father_name, mother_name, guardian_name, guardian_phone, phone, email, district}); nonce per record; the per-institution data key is generated at institution creation, wrapped by `MASTER_KEY`, cached in process memory for the request lifetime. Decryption happens only inside tenant requests, worker jobs, or `drilldown` functions with a valid grant.
* **No caches** hold authority data or seat counts. (Redis-backed caches/rate limits are Deferred; in-process TTL cache is allowed only for reference data and public JWKS.)
* **Pagination/search:** repository helpers implement keyset pagination and allow-listed sorting; name search through blind-index tokens.

### 6.9 Seams for deferred capabilities

| Deferred capability | Seam in the prototype | How it is added without refactoring |
|---|---|---|
| Redis / Celery | `JobQueue` interface (`enqueue`, `claim`, `heartbeat`, `complete`) + handler registry; `RateLimiter` interface; `EventBus` interface | New implementations selected by `JOB_QUEUE=celery`, `RATE_LIMITER=redis`; handlers unchanged |
| Grafana / Prometheus / OTel | Structured logging + `MetricsSink` interface (in-memory impl) | Add `PrometheusSink`, OTel exporter, `/metrics`; keep Operations page |
| Supabase Auth / Storage / Realtime | `AuthProvider`, `ObjectStore`, `EventBus` | Supabase adapters; JWT verification against Supabase keys |
| KMS / HSM / Object Lock | `KeyProvider`, `WormSink` | Cloud KMS provider, S3 Object Lock sink |
| TOTP MFA / SSO | Step-up dependency `require_step_up()` | Replace its check with `aal2` claim |
| DigiLocker / ABC / NAD | Adapter stubs + `channel` enum value `DIGILOCKER` | Real adapters, enable channel |
| NLQ / briefs | `analytics.semantic_layer` allow-list module (empty) | Add structured-JSON LLM adapter returning validated queries |
| Read replica / partitioning | Repositories take a `read_only` flag; tables keyed for partitioning (`occurred_at`, `academic_year`) | Route reads to replica; `pg_partman` migrations |
| Separate DB roles | `db_role` argument on session factory | Create roles and DSNs (DEC-21) |
| Sandbox environment | `ENVIRONMENT` setting and dry-run flows | Deploy second stack with separate DB |

### 6.10 Environments

| Environment | Purpose | Data | Notes |
|---|---|---|---|
| Local dev | Development | Seed `standard` | Docker DB + local processes |
| CI | Automated tests | Seed `minimal` | Ephemeral PostgreSQL service container; mock-services started in-process |
| Demo (Compose) | Evaluation and training | Seed `standard`/`full`, resettable | `docker compose --profile app up`; `DEMO_MODE=true` |
| Staging / Production / DR | **Future** | Real or synthetic at scale | Out of prototype scope (Appendix A) |

---

## 7. Database Schema Requirements

PostgreSQL 16, one database `sentinel`, **14 schemas mapped to the modules** (source §5) plus two prototype-only schemas: `mockext` (mock services) and `seed` (evaluation ground truth). Migrations are Alembic, **expand-then-contract**. Table-level DDL is derived from this section (the source states DDL is part of the build, not the document).

### 7.1 Conventions [SRC §5.1]

* **Keys:** application-generated **UUIDv7** (`uuid` type; library `uuid-utils`). Public identifiers (`credential_ref`, provisional tokens, `case_no`, `enrolment_ref`) are separate random/sequence values.
* **Enumerations** are `text` with `CHECK (col IN (…))`, so allowed values travel with migrations.
* **Time:** `timestamptz` in UTC; dates as `date`. Cut-offs configured in IST, stored UTC.
* **No Aadhaar column** anywhere. Identifiers only as HMAC-SHA256 `identity_key` (+ `key_version`).
* **PII separation:** names, DOB, parents, phone, email, district live only encrypted in `pii.pii_records`. Non-identifying attributes needed for rules and reports (`gender, category, citizenship_type, nationality_code, birth_year, is_minor`) stay on `core.enrolments`.
* **Append-only:** `core.enrolment_events`, `core.seat_ledger_entries`, `audit.*` history tables have no UPDATE/DELETE grants for `app_runtime`.
* **Tenant isolation:** every table with `institution_id` has RLS **enabled and forced**. Policy template: `USING (institution_id = nullif(current_setting('app.institution_id', true),'')::uuid OR current_setting('app.scope', true) IN ('NATIONAL','REGION','STATE','SYSTEM'))`; REGION/STATE narrowing is applied by the repositories (and a policy on `ref.institutions.region/state` through a helper function). Student tables add `student_id = current_setting('app.student_id')` policies.
* **Roles:** `app_migrator` (owner; DDL; seed) and `app_runtime` (no BYPASSRLS; DML only where granted) [DEC-21].
* **Soft delete only**; `created_at`, `updated_at` where mutable; `row_version int` for ETag/If-Match on decision tables.
* Notation below: `*` primary key, `→` foreign key, `U` unique, `?` nullable, `[]` array, `{}` jsonb.

### 7.2 Schema: `ref` (M02)

| Table | Columns and constraints |
|---|---|
| `institutions` | `*id`, `aicte_id` U, `name`, `type` ∈ ENGINEERING, POLYTECHNIC, MANAGEMENT, PHARMACY, UNIVERSITY_DEPT; `state`, `district`, `region` ∈ NORTH, SOUTH, EAST, WEST, CENTRAL, NORTH_EAST; `nodal_officer_name`, `nodal_officer_email`, `public_contact_name`, `public_contact_email`, `public_contact_phone` (used by `CONFLICT`), `status` ∈ ACTIVE, PENDING_APPROVAL, SUSPENDED, CLOSED; `merged_into_id?→`, `former_names{}`, `data_key_current?`, timestamps |
| `programmes` | `*id`, `institution_id→`, `programme_code` (U per institution), `name`, `level` ∈ UG, PG, DIPLOMA, PGDM; `discipline`, `mode` ∈ FULL_TIME, PART_TIME, ODL, ONLINE; `lateral_entry bool`, `duration_years`, `shift`, `status` ∈ ACTIVE, DISCONTINUED; `successor_programme_id?→` |
| `seat_bucket_types` | `*code` (REGULAR, LATERAL, SN_FN, SN_OCI, SN_TFW, SN_EWS), `label`, `is_supernumerary bool`, `description` |
| `intake_versions` | `*id`, `programme_id→`, `academic_year`, `version_no`, `approved_intake int`, `effective_from`, `effective_to?`, `source`; U `(programme_id, academic_year, version_no)` |
| `intake_bucket_caps` | `*id`, `intake_version_id→`, `bucket_type→`, `cap int ≥ 0`; U `(intake_version_id, bucket_type)` |
| `submission_windows` | `*id`, `academic_year`, `label`, `opens_at`, `closes_at`, `cutoff_at`, CHECK `opens_at < closes_at` |
| `window_extensions` | `*id`, `institution_id→`, `window_id→`, `requested_by→iam.users`, `new_closes_at`, `reason`, `status` ∈ PENDING, APPROVED, REJECTED; `decided_by?`, `decided_at?`, `decision_reason?`, `row_version` |
| `enrolment_exception_rules` | `*id`, `name`, `description`, `level_a`, `level_b`, `mode_a?`, `mode_b?`, `active bool` |
| `lookups` | `*(kind, code)`, `label_en`, `label_hi`, `parent?` for gender, category, nationality, state, district |

### 7.3 Schema: `iam` (M01, M14)

| Table | Columns and constraints |
|---|---|
| `users` | `*id`, `username` U (citext), `display_name`, `email`, `utype` ∈ INSTITUTION, AICTE, STUDENT, VERIFIER; `password_hash`, `is_demo bool`, `status` ∈ ACTIVE, DISABLED; `institution_id?→`, `student_id?→core.students`, `verifier_org_id?→`, `locale` ∈ en, hi; `failed_attempts`, `locked_until?`, `last_login_at?`, timestamps |
| `roles` | `*code` ∈ IA, IO, AR, AS, ST, VU, CL |
| `role_permissions` | `*(role_code, permission)` |
| `user_roles` | `*id`, `user_id→`, `role_code→`, `scope_type` ∈ NATIONAL, REGION, STATE, INSTITUTION; `scope_value?`; U `(user_id, role_code, scope_type, scope_value)` |
| `sessions` | `*id`, `user_id→`, `issued_at`, `last_seen_at`, `expires_at`, `revoked_at?`, `user_agent`, `ip_hash` |
| `api_clients` | `*id`, `institution_id?→`, `name`, `client_id` U, `secret_hash`, `scopes text[]`, `status`, `last_used_at?`, `created_by→` |
| `webhook_endpoints` | `*id`, `institution_id?→`, `client_id?→`, `url`, `secret_enc`, `events text[]`, `status` ∈ ACTIVE, PAUSED, DISABLED |
| `verifier_orgs` | `*id`, `name`, `tier` ∈ REGULATOR, INSTITUTION, UNIVERSITY, EMPLOYER, BANK, EMBASSY; `status` ∈ ACTIVE, SUSPENDED; `contact_email`, timestamps |
| `verifier_scopes` | `*id`, `verifier_org_id→`, `purpose_code`, `allowed_fields text[]`, `allowed_questions text[]`, `hourly_limit int` |
| `idempotency_keys` | `*(actor_id, key)`, `route`, `request_hash`, `state` ∈ IN_PROGRESS, DONE; `response_status?`, `response_body{}?`, `created_at`, `expires_at` |
| `onboarding_requests` | `*id`, `aicte_id`, `officer_name`, `designation`, `email`, `phone`, `identity_check` ∈ MATCH, MISMATCH (simulated), `status`, `decided_by?`, `decided_at?`, `reason?` |
| `step_up_tokens` | `*id`, `session_id→`, `issued_at`, `expires_at`, `used_for text[]` |

### 7.4 Schema: `pii` (M01, M04)

| Table | Columns and constraints |
|---|---|
| `data_keys` | `*id`, `institution_id→`, `version int`, `wrapped_key bytea`, `status` ∈ ACTIVE, RETIRED, DESTROYED, `created_at`; U `(institution_id, version)` |
| `pii_records` | `*id`, `student_id→`, `institution_id→`, `data_key_id→`, `version int`, `nonce bytea`, `ciphertext bytea?`, `erased_at?`, `created_at`; U `(student_id, institution_id, version)`; CHECK `(ciphertext IS NULL) = (erased_at IS NOT NULL)` |
| `access_grants` | `*id`, `user_id→`, `case_id→intel.cases`, `justification`, `scope_type` ∈ CASE, INSTITUTION, ENROLMENT_SET; `scope_ref{}`, `granted_at`, `expires_at`, `revoked_at?`, `records_revealed int default 0`; CHECK `expires_at > granted_at AND expires_at ≤ granted_at + interval '4 hours'` |

### 7.5 Schema: `core` (M04, M05, M08)

| Table | Columns and constraints |
|---|---|
| `students` | `*id`, `student_ref` U (`STU-<base32>`), `identity_type` ∈ APAAR, PROVISIONAL; `identity_status` ∈ VERIFIED, PROBABLE_MATCH, PENDING_FIX, PENDING_VERIFICATION, NOGEN, CONSENT_REFUSED, INVALID; `registry_status?`, `merged_into_id?→students`, `created_at` |
| `student_identity_keys` | `*id`, `student_id→`, `key_version`, `identity_key bytea`, `kind` ∈ APAAR, PROVISIONAL; `status` ∈ ACTIVE, SUPERSEDED; U `(key_version, identity_key)` |
| `provisional_ids` | `*id`, `student_id→`, `token` U, `type` ∈ NOGEN, REFUSED, FOREIGN; `document_hash?`, `document_last4?`, `expires_at`, `compliance_deadline?`, `upgraded_key_id?→`, `status` ∈ OPEN, UPGRADED, EXPIRED |
| `enrolments` | `*id`, `enrolment_ref` U, `student_id→`, `institution_id→`, `programme_id→`, `academic_year`, `seat_bucket_id→`, `bucket_type→`, `concurrency_class` ∈ EXCLUSIVE, CONCURRENT_OK, EXCEPTION_APPROVED, TRANSFER_PENDING; `status` ∈ CLAIMED, ADMITTED, WITHDRAWN, TRANSFERRED_OUT; `institution_student_id`, `admission_date?`, `claimed_at`, `claimed_by→iam.users?`, `claim_source` ∈ PORTAL, BULK, API; `row_key` , `rule_set_id→`, `identity_key bytea`, `key_version`, `gender`, `category`, `citizenship_type`, `nationality_code`, `birth_year`, `is_minor bool`, `exception_id?→`, `name_tokens bytea[]`, `withdrawn_reason?`, `row_version`, timestamps. **Indexes:** `ux_enrol_exclusive` UNIQUE `(academic_year, key_version, identity_key)` WHERE `concurrency_class='EXCLUSIVE' AND status IN ('CLAIMED','ADMITTED')`; `ux_enrol_programme` UNIQUE `(student_id, programme_id, academic_year)` WHERE `status IN ('CLAIMED','ADMITTED')`; U `(institution_id, academic_year, row_key)`; `(institution_id, academic_year, status)`; `(programme_id, academic_year)`; `(student_id)`; GIN `(name_tokens)`; keyset index `(institution_id, id)` |
| `enrolment_events` | `*id`, `enrolment_id→`, `seq int` (U with enrolment_id), `event_type` ∈ CLAIM, ADMIT, WITHDRAW, TRANSFER_OUT, TRANSFER_IN, CORRECT, SEAT_RELEASE, IDENTITY_UPGRADE, VERIFICATION_CHANGE; `reason_code?`, `reason_text?`, `payload{}` (no PII), `actor_id?`, `rule_set_id?`, `occurred_at`, `effective_date?` — **append-only** |
| `seat_buckets` | `*id`, `programme_id→`, `academic_year`, `bucket_type→`, `cap int`, `approved_extra int default 0`, `occupied int default 0`, `cumulative_admitted int default 0`, `intake_version_id→`, `warn_alerted bool`; U `(programme_id, academic_year, bucket_type)`; **CHECK `occupied BETWEEN 0 AND cap + approved_extra`**, `cumulative_admitted ≥ 0` |
| `seat_ledger_entries` | `*id bigserial`, `seat_bucket_id→`, `entry_type` ∈ DEBIT, RELEASE, EXCEPTION_GRANT, CAP_CHANGE; `delta int`, `occupied_after int`, `enrolment_id?`, `exception_id?`, `reason?`, `actor_id?`, `effective_date`, `created_at` — **append-only**; invariant `occupied = Σ(DEBIT) − Σ(RELEASE)` |
| `seat_exceptions` | `*id`, `seat_bucket_id→`, `institution_id→`, `requested_by→`, `extra_seats int > 0`, `justification`, `evidence_note`, `status` ∈ PENDING, APPROVED, REJECTED, EXPIRED; `approved_seats?`, `decided_by?`, `decided_at?`, `decision_reason?`, `expires_at?`, `row_version`; CHECK `decided_by IS NULL OR decided_by <> requested_by` |
| `enrolment_verification` | `*enrolment_id→`, `lock1_status` ∈ SIGNED, `lock1_signature`, `lock1_kid`, `lock2_status` ∈ PENDING, PASSED, FAILED, PROVISIONAL_OK; `lock3_status` ∈ PENDING, CONFIRMED, EXPIRED, DISPUTED; `overall_state` ∈ PENDING, VERIFIED, UNCONFIRMED, DISPUTED, PENDING_FIX; `channel?` ∈ REGISTRY_MOBILE, INSTITUTION_MOBILE, GUARDIAN, ASSISTED, DIGILOCKER; `channel_trust?` ∈ HIGH, LOW; `deadline_at`, `fix_deadline_at?`, `confirmed_at?`, `reminders_sent int`, `updated_at` |
| `confirmation_requests` | `*id`, `enrolment_id→`, `channel`, `target_masked`, `otp_hash`, `otp_expires_at`, `attempts int`, `resend_count int`, `last_sent_at`, `status` ∈ SENT, CONFIRMED, EXPIRED, FAILED; `for_guardian bool`, `guardian_link_id?→`, `created_at` |
| `transfers` | `*id`, `student_id→`, `out_enrolment_id?→`, `in_enrolment_id?→`, `out_institution_id?→`, `in_institution_id?→`, `effective_date_out?`, `status` ∈ OUT_RECORDED, MATCHED, ORPHAN_LEAD, DISMISSED; `created_at`, `orphan_after?` |
| `claim_attempts` | `*id bigserial`, `institution_id→`, `programme_id?`, `bucket_type?`, `source`, `outcome` ∈ CLAIMED, CLAIMED_PENDING_VERIFICATION, CONFLICT, SEAT_LIMIT, DUPLICATE_SAME_INSTITUTION, INVALID, RULE_BLOCKED; `http_status`, `conflict_holder_institution_id?` (internal), `identity_key_hash?` (first 8 bytes only), `request_id`, `created_at`. Index `(institution_id, created_at)` |

### 7.6 Schemas: `identity`, `ingest`, `rules`, `matching`

| Table | Columns and constraints |
|---|---|
| `identity.registry_checks` | `*id`, `enrolment_id?→`, `student_id?`, `source` ∈ CLAIM, REVALIDATION, CHECK_ONLY, UPLOAD_DRYRUN; `status` ∈ FOUND, NOT_FOUND, UNAVAILABLE; `name_score?`, `dob_match?` ∈ EXACT, DAY_MONTH_SWAP, YEAR_TYPO, MISMATCH; `verdict`, `latency_ms`, `breaker_state`, `checked_at` (no PII) |
| `identity.diagnoses` | `*id`, `registry_check_id→`, `enrolment_id?→`, `status`, `field`, `message_code`, `suggested_fix{}`, `deadline_at?`, `resolved_at?` |
| `ingest.templates` | `*version`, `schema{}`, `effective_from`, `csv_header` |
| `ingest.upload_jobs` | `*id`, `institution_id→`, `academic_year`, `created_by→`, `filename`, `storage_key`, `size_bytes`, `template_version`, `status` (per §5.4.3), `scan_result{}` (incl. `malware: NOT_PERFORMED`), `total_rows`, `accepted int`, `held int`, `rejected int`, `noop int`, `committed int`, `failed int`, `committed_by?→`, `committed_at?`, `failure_reason?`, timestamps; **CHECK `committed_by IS NULL OR committed_by <> created_by`** |
| `ingest.upload_chunks` | `*id`, `job_id→`, `chunk_no`, `row_start`, `row_end`, `phase` ∈ PARSE, VALIDATE, COMMIT; `status`, `lease_expires_at?`, `attempts`; U `(job_id, phase, chunk_no)` |
| `ingest.staged_rows` | `*id`, `job_id→`, `row_no`, `row_key`, `outcome` ∈ ACCEPTED, HELD, REJECTED, NOOP_DUPLICATE, COMMITTED, COMMIT_FAILED; `payload_nonce`, `payload_ct bytea` (encrypted), `errors{}`, `diff{}`, `claim_outcome?`, `enrolment_id?`; index `(job_id, outcome)` |
| `ingest.submissions` | `*id`, `institution_id→`, `academic_year`, `window_id→`, `status` ∈ DRAFT, VALIDATED, SUBMITTED, ACCEPTED, UNDER_REVIEW, CORRECTED, LOCKED; `declared{}` (per programme), `submitted_by?`, `submitted_at?`, `approved_by?`, `approved_at?`, `late_flag bool`, `rule_set_id?`, `review_notes?`, `locked_at?`, `row_version`; U `(institution_id, academic_year, window_id)`; **CHECK `approved_by IS NULL OR approved_by <> submitted_by`** |
| `ingest.correction_requests` | `*id`, `enrolment_id→`, `institution_id→`, `requested_by→`, `requester_kind` ∈ STUDENT, INSTITUTION; `field`, `proposed_enc bytea`, `proposed_nonce`, `note`, `status` ∈ PENDING, APPROVED, REJECTED; `decided_by?`, `decided_at?`, `reason?` |
| `rules.rule_sets` | `*id`, `name`, `version_no` U, `status` ∈ DRAFT, ACTIVE, RETIRED; `drafted_by→`, `published_by?→`, `published_at?`, `effective_from?`, `retroactive bool`, `notes`; **CHECK `published_by IS NULL OR published_by <> drafted_by`**; partial unique index on `status='ACTIVE'` (one active) |
| `rules.rules` | `*id`, `rule_set_id→`, `rule_code`, `category`, `kind` ∈ (§3.8), `params{}`, `severity` ∈ Blocker, Error, Warning, Info; `scope{}`, `sites text[]` (C, B, S), `effective_from?`, `effective_to?`, `regulatory_ref?`, `owner`, `message_template`, `fix_template`; U `(rule_set_id, rule_code)` |
| `rules.findings` | `*id`, `institution_id→`, `academic_year`, `rule_id→`, `rule_set_id→`, `enrolment_id?→`, `upload_job_id?→`, `submission_id?→`, `severity`, `status` ∈ OPEN, ACKNOWLEDGED, RESOLVED, WAIVED; `evidence{}`, `suggested_fix{}`, `acknowledged_by?`, `waived_by?`, `waiver_reason_code?`, `created_at`, `resolved_at?`; index `(institution_id, academic_year, status)` |
| `rules.simulation_runs` | `*id`, `draft_rule_set_id→`, `baseline_rule_set_id→`, `cycles text[]`, `status` ∈ QUEUED, RUNNING, DONE, FAILED; `requested_by→`, `result{}`, `skipped_rules text[]`, `started_at`, `finished_at?` |
| `matching.match_features` | `*enrolment_id→`, `k1 bytea`, `k2 bytea`, `k3 bytea`, `bloom_name bytea`, `bloom_dob bytea`, `bloom_parent bytea`, `contact_hashes{}` (phone/email/address HMACs), `built_at`; indexes on `k1`, `k2`, `k3` |
| `matching.match_candidates` | `*id`, `enrolment_a→`, `enrolment_b→`, `score numeric`, `field_scores{}`, `timeline_class`, `status` ∈ AUTO_LINKED, REVIEW, DISMISSED, CONFIRMED_DUP; `flags text[]`, `decided_by?`, `decided_at?`, `decision_reason?`, `case_id?→`; U `(least(a,b), greatest(a,b))` |

### 7.7 Schemas: `intel`, `cred`, `consent`, `audit`

| Table | Columns and constraints |
|---|---|
| `intel.trust_scores` | `*id`, `institution_id→`, `academic_year`, `score numeric(4,1)`, `band`, `components{}` (points + inputs + evidence refs), `config_version`, `computed_at`; U `(institution_id, academic_year, computed_date)` |
| `intel.anomaly_alerts` | `*id`, `institution_id→`, `layer` ∈ A, B, C, D; `type`, `severity` ∈ HIGH, MEDIUM, LOW; `status` ∈ OPEN, IN_CASE, DISMISSED, CLOSED; `baseline{}`, `deviation numeric`, `evidence{}`, `explanation{}` (contribution table), `suggested_action`, `model_version`, `case_id?→`, `created_at` |
| `intel.graph_clusters` | `*id`, `run_id`, `cluster_key`, `institution_ids uuid[]`, `student_count`, `shared_contact_types text[]`, `score`, `evidence{}`, `created_at` |
| `intel.liveness_signals` | `*enrolment_id→`, `has_abc_credit bool`, `has_exam_registration bool`, `last_activity_at?`, `evaluated_at`, `status` ∈ ACTIVE, GHOST_CANDIDATE, NO_DATA; `source` default `MOCK_FEED` |
| `intel.cases` | `*id`, `case_no` U, `institution_id→`, `type` ∈ ANOMALY, DUPLICATE, STUDENT_DISPUTE, UNCONFIRMED_SHARE, SEAT_RELEASE, OTHER; `title`, `owner_id→`, `status` ∈ OPEN, AWAITING_RESPONSE, UNDER_REVIEW, CLOSED, APPEALED; `response_due_at?`, `outcome_code?`, `outcome_notes?`, `evidence_pack{}`, `evidence_sha256?`, `opened_by→`, `created_at`, `closed_at?`, `row_version` |
| `intel.case_events` | `*id`, `case_id→`, `actor_id→`, `event_type`, `body`, `created_at` |
| `intel.appeals` | `*id`, `case_id→`, `filed_by→`, `grounds`, `status` ∈ FILED, UPHELD, OVERTURNED; `decided_by?`, `decided_at?`, `decision_notes?`; app-level + trigger check `decided_by <> cases.owner_id` |
| `cred.issuers` | `*id`, `name`, `kind` ∈ AICTE, UNIVERSITY, INSTITUTION; `institution_id?→`, `status` |
| `cred.signing_keys` | `*id`, `kid` U, `purpose` ∈ ISSUER, AICTE_ATTEST, AICTE_COMMIT, SEALER, VERIFIER_ANSWER, STATUS_LIST, INSTITUTION_CLAIM; `issuer_id?→`, `institution_id?→`, `alg='ES256'`, `public_jwk{}`, `private_wrapped bytea`, `status` ∈ ACTIVE, ROTATING, RETIRED, COMPROMISED; `created_at` |
| `cred.credentials` | `*id`, `credential_ref` U (22-char base64url), `enrolment_id→`, `student_id→`, `type` ∈ ENROLMENT_PROOF (+ reserved values), `issuer_id→`, `issuer_kid`, `aicte_kid`, `status_list_id→`, `status_list_index int`, `payload_digest`, `sd_jwt text`, `attestation text`, `issued_by→`, `issued_at`, `valid_until`, `status` ∈ VALID, REVOKED, SUPERSEDED; `revoked_reason?`, `revoked_at?`, `superseded_by?→`; indexes `(credential_ref)`, `(enrolment_id)` |
| `cred.status_lists` | `*id`, `purpose='revocation'`, `size int`, `bitstring bytea`, `next_index int`, `version int`, `signed_jwt text`, `updated_at` |
| `cred.share_links` | `*id`, `credential_id→`, `token_hash`, `purpose_code`, `fields text[]`, `expires_at`, `revoked_at?`, `use_count`, `created_by→` |
| `cred.verification_requests` | `*id`, `verifier_org_id→`, `verifier_user_id→`, `credential_id?→` (null when unknown), `via` ∈ CREDENTIAL_REF, SHARE_LINK, PUBLIC; `purpose_code`, `question{}`, `outcome` ∈ YES, NO, NEUTRAL; `fields_disclosed text[]`, `nonce`, `issued_at`, `expires_at?`, `consent_id?→`, `ip_hash`, `created_at` |
| `consent.consent_notices` | `*id`, `code`, `version`, `language` ∈ en, hi; `text`, `effective_from`; U `(code, version, language)` |
| `consent.consents` | `*id`, `student_id→`, `purpose_code`, `verifier_org_id?→`, `scope{}` (fields), `notice_id→`, `status` ∈ GRANTED, REFUSED, WITHDRAWN, EXPIRED; `method` ∈ PORTAL, SHARE_LINK, GUARDIAN; `granted_at`, `expires_at?`, `withdrawn_at?` |
| `consent.guardian_links` | `*id`, `student_id→` (minor), `guardian_user_id→iam.users`, `relationship`, `status` ∈ PENDING, VERIFIED, REVOKED; `verified_at?` |
| `consent.consent_requests` | `*id`, `student_id→`, `verifier_org_id→`, `purpose_code`, `question{}`, `status` ∈ PENDING, GRANTED, REFUSED, EXPIRED; `created_at` |
| `consent.data_subject_requests` | `*id`, `student_id→`, `type` ∈ ACCESS, CORRECTION, ERASURE, GRIEVANCE; `details`, `status` ∈ OPEN, IN_REVIEW, FULFILLED, REJECTED_LAWFUL_RETENTION, REJECTED_OTHER; `decided_by?`, `created_at`, `resolved_at?`, `result_ref?` |
| `audit.audit_log` | `*id bigserial`, `stream_id default 'main'`, `seq?` (assigned by sealer; U with `stream_id`), `occurred_at`, `actor_id?`, `actor_role?`, `action`, `entity_type`, `entity_id?`, `institution_id?`, `case_id?`, `justification?`, `request_id`, `ip_hash?`, `payload{}` (no PII), `prev_hash?`, `entry_hash?`, `checkpoint_id?`. Trigger: UPDATE allowed only to set `seq, prev_hash, entry_hash` when currently NULL; no DELETE |
| `audit.audit_checkpoints` | `*id`, `stream_id`, `from_seq`, `to_seq`, `root_hash`, `signed_jwt`, `kid`, `worm_path`, `created_at` |
| `audit.cohort_commitments` | `*id`, `institution_id→`, `academic_year`, `submission_id→`, `cutoff_at`, `version int`, `merkle_root`, `leaf_count`, `serialisation_version`, `institution_signature`, `institution_kid`, `aicte_countersignature?`, `aicte_kid?`, `status` ∈ PENDING_COUNTERSIGN, COMPLETE, SUPERSEDED; `created_by→`, `countersigned_by?→`, `created_at`; U `(institution_id, academic_year, version)` |
| `audit.commitment_leaves` | `*(commitment_id→, leaf_index)`, `enrolment_id`, `leaf_json{}`, `leaf_hash` |

### 7.8 Schemas: `ops`, `agg`, `mockext`, `seed`

| Table | Columns and constraints |
|---|---|
| `ops.outbox_events` | `*id bigserial`, `topic`, `aggregate_type`, `aggregate_id`, `institution_id?`, `payload{}` (no PII), `created_at`, `published_at?`, `attempts`; partial index on `published_at IS NULL` |
| `ops.jobs` | `*id`, `queue`, `type`, `payload{}`, `status` ∈ QUEUED, RUNNING, SUCCEEDED, FAILED, DEAD; `priority`, `run_at`, `attempts`, `max_attempts`, `lease_expires_at?`, `locked_by?`, `result{}?`, `last_error?`, `institution_id?`, `dedupe_key?` (U where active), `created_at`, `finished_at?`; index `(queue, status, run_at)` |
| `ops.schedules` | `*name`, `interval_seconds`, `next_run_at`, `last_run_at?`, `enabled bool`, `last_status?` |
| `ops.notifications` | `*id`, `recipient_user_id?`, `recipient_kind`, `channel` ∈ SMS, EMAIL, INAPP; `template_code`, `language`, `params{}` (masked), `status` ∈ QUEUED, SENT, FAILED, FALLBACK; `attempts`, `provider_ref?`, `institution_id?`, `enrolment_id?`, `created_at`, `sent_at?`, `read_at?` |
| `ops.webhook_deliveries` | `*id`, `endpoint_id→`, `event_type`, `payload{}`, `status` ∈ PENDING, DELIVERED, RETRYING, DEAD_LETTERED; `attempts`, `next_attempt_at?`, `last_status_code?`, `created_at` |
| `ops.report_jobs` | `*id`, `requested_by→`, `institution_id?`, `type`, `params{}`, `format`, `status`, `file_key?`, `sha256?`, `rule_set_id?`, `cutoff_at?`, `created_at`, `finished_at?` |
| `ops.system_config` | `*key`, `value{}`, `updated_by?`, `updated_at` |
| `ops.retention_policies` | `*id`, `data_class`, `retention_days`, `action` ∈ PURGE, ANONYMISE |
| `agg.seat_utilisation` | `*(programme_id, academic_year, bucket_type)`, `institution_id`, `state`, `district`, `region`, `institution_type`, `level`, `cap`, `approved_extra`, `occupied`, `cumulative_admitted`, `pct numeric`, `as_of` |
| `agg.verification_funnel` | `*(institution_id, academic_year)`, `total`, `pending`, `verified`, `unconfirmed`, `disputed`, `pending_fix`, `pending_registry`, `provisional`, `low_trust_channel`, `as_of` |
| `agg.findings_summary` | `*(institution_id, academic_year, severity, status)`, `count` |
| `agg.data_quality` | `*(institution_id, academic_year)`, `pending_fix`, `pending_verification`, `provisional_open`, `overdue_fix`, `as_of` |
| `agg.claim_daily` | `*(day, institution_id)`, `claims`, `conflicts`, `seat_limit`, `invalid`, `duplicates_same_inst` |
| `agg.projector_state` | `*name`, `last_outbox_id` |
| `mockext.registry_people` | `*apaar_id`, `status`, `name`, `name_native?`, `dob`, `mobile`, `is_spare bool` |
| `mockext.registry_control`, `sms_messages`, `email_messages`, `feed_activity` | Control row (`mode, latency_ms, error_rate`), message stores for inboxes, ABC/exam activity per APAAR ID |
| `seed.ground_truth` | `*id`, `kind` ∈ DUPLICATE_TRUE, TRANSFER, FT_PLUS_ODL, EXCEPTION, MISMATCH_TYPE, GHOST_RING, OVER_ADMISSION, STOLEN_IDENTITY; `ref_a`, `ref_b?`, `details{}` — readable only by `app_migrator` / evaluation CLI |

### 7.9 Integrity guarantees enforced by the database [SRC §5.4]

| Guarantee | Mechanism | Test |
|---|---|---|
| No seat oversold | Bucket row lock + `CHECK (occupied BETWEEN 0 AND cap + approved_extra)` | concurrency test (SC-02) |
| One exclusive claim per student per cycle | Partial unique `ux_enrol_exclusive` | SC-03 |
| Ledger arithmetic | `occupied = Σ(DEBIT) − Σ(RELEASE)`; verified by `verify-seed` and a nightly check | property test |
| History cannot be edited | No UPDATE/DELETE grants on event, ledger, audit tables; seal trigger | grants test |
| Tenant isolation | Forced RLS | cross-tenant tests |
| Separation of duties | CHECK constraints (upload commit, submission approval, rule publish, exception decision) | constraint tests |
| Replays harmless | Idempotency keys, `row_key` uniqueness, exclusivity index, ETags | replay tests |
| No PII outside vault | Migration lint rejects columns named like `name\|dob\|phone\|email\|aadhaar` outside `pii` and `mockext` | CI lint |

### 7.10 Indexing and growth considerations [SRC §5.6]

* Required indexes are listed per table above; additionally `findings (institution_id, academic_year, status)`, `audit_log (stream_id, seq)`, `claim_attempts (institution_id, created_at)`, `outbox_events` partial unpublished, `staged_rows (job_id, outcome)`, `credentials (credential_ref)`, `match_features (k1)`, `(k2)`, `(k3)`. **Validate with `EXPLAIN` against the `full` profile** (keyset queries must use index scans).
* Keyset pagination always sorts on an indexed column plus `id`.
* **Partitioning is Deferred** (no `pg_partman` in the prototype): `enrolment_events` and `findings` would partition by academic year; `audit_log`, `claim_attempts`, `verification_requests` by month. Tables are shaped so this needs no application change.
* Sizing heuristic (source): ~1.2 KB per enrolment row, ten events at ~0.5 KB, ~1 KB PII, ~0.6 KB match features per enrolment → the `full` profile is well under 1 GB.

---

## 8. API Specifications

Base URLs: core-api `http://localhost:8000/api/v1` · verify-api `http://localhost:8001` · mock-services `http://localhost:8002`. A breaking change creates `/api/v2`. OpenAPI 3.1 is generated by FastAPI (`/api/v1/openapi.json`); the tables below are the **contract** the generated spec must match (CI compares paths, methods and permissions).

### 8.1 Conventions

| Topic | Rule |
|---|---|
| Auth | `Authorization: Bearer <jwt>`; step-up actions add `X-Step-Up-Token` |
| Request id | `X-Request-Id` generated if absent; echoed on every response and in every error |
| Idempotency | `Idempotency-Key` (16–64 chars) **required on every mutating POST** except `/auth/*`, OTP verify and `/public/*`. Same key+body ≤ 7 days → stored response with `Idempotent-Replay: true`; same key different body → `422 IDEMPOTENCY_MISMATCH`; in flight → `409 REQUEST_IN_PROGRESS` |
| Concurrency | Decision/PATCH endpoints return `ETag`; clients send `If-Match`; mismatch `412 PRECONDITION_FAILED`; missing `428 PRECONDITION_REQUIRED` |
| Pagination | `limit` (≤ 500, default 50), `cursor` (opaque), `sort` (allow-listed, e.g. `-created_at`), `include_total=true` optional. Response `{ "items": [], "next_cursor": null, "total": 123?, "as_of": "…" }` |
| Filtering/search | Documented per endpoint as query params; unknown params → `422` |
| Language | `Accept-Language: en\|hi` selects localised messages |
| Errors | `application/problem+json` per RFC 9457 (below) |
| Rate limits | `RateLimit-Limit/Remaining/Reset`; `429` + `Retry-After`. Defaults: 300 req/min per user, 600/min per client, 30/min per IP on `/public/*` and `/onboarding/*` |
| Async | `202 Accepted` + `{ "job_id": "…", "status_url": "…" }` |
| Dates | ISO 8601 UTC with `Z`; calendar dates `YYYY-MM-DD` |
| Unknown fields | Rejected (`422 VALIDATION_ERROR`, `code: "UNKNOWN_FIELD"`) |

**Problem+json shape**
```json
{
  "type": "https://sentinel.example/problems/seat-limit",
  "title": "Seat limit reached",
  "status": 409,
  "code": "SEAT_LIMIT",
  "detail": "Bucket REGULAR for B.E. Computer Science is full (120 of 120).",
  "request_id": "01JA…",
  "retryable": false,
  "errors": [ { "field": "bucket_type", "code": "SEAT_LIMIT", "message": "…", "fix_suggestion": "Request a seat exception." } ]
}
```

### 8.2 Error code catalogue

| Code | HTTP | Meaning |
|---|---|---|
| `INVALID_CREDENTIALS` / `ACCOUNT_LOCKED` / `SESSION_EXPIRED` / `TOKEN_INVALID` / `STEP_UP_REQUIRED` | 401 (423 for lock) | Authentication problems |
| `FORBIDDEN` / `SEPARATION_OF_DUTIES` / `GRANT_REQUIRED` / `GRANT_EXPIRED` / `VERIFIER_SUSPENDED` / `PURPOSE_NOT_ALLOWED` / `INSTITUTION_NOT_ACTIVE` | 403 | Authorisation problems (all audited) |
| `NOT_FOUND` | 404 | Unknown or not visible (cross-tenant) |
| `VALIDATION_ERROR` (+ field codes `REQUIRED`, `INVALID_FORMAT`, `AADHAAR_SUSPECTED`, `UNKNOWN_FIELD`, …) | 422 | Request invalid |
| `IDENTITY_INVALID` / `RULE_BLOCKER` / `PROGRAMME_NOT_APPROVED` / `FILE_REJECTED` / `IDEMPOTENCY_MISMATCH` / `OTP_INVALID` | 422 | Business validation |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | Missing key |
| `CLAIM_CONFLICT` / `SEAT_LIMIT` / `INVALID_STATE_TRANSITION` / `WINDOW_CLOSED` / `REQUEST_IN_PROGRESS` / `UPLOAD_NOT_READY` | 409 | State conflicts |
| `OTP_EXPIRED` | 410 | OTP expired |
| `PRECONDITION_FAILED` / `PRECONDITION_REQUIRED` | 412 / 428 | ETag problems |
| `RATE_LIMITED` / `OTP_ATTEMPTS_EXCEEDED` / `OTP_RESEND_LIMIT` | 429 | Throttling |
| `SERVICE_UNAVAILABLE` / `KEY_UNAVAILABLE` | 503 | Retryable (`retryable: true`) |

### 8.3 Session, identity and token endpoints

| Method | Path | Permission | Request | Response / notes |
|---|---|---|---|---|
| POST | `/auth/login` | public | `{username, password}` | `200 {access_token, token_type:"Bearer", expires_at, session_id, user:{id, username, display_name, roles[], scope, institution?, locale}}`; `401 INVALID_CREDENTIALS`; `423 ACCOUNT_LOCKED` |
| POST | `/auth/logout` | `auth:self` | – | `204`; revokes session |
| GET | `/auth/sessions` | `auth:self` | – | List of own sessions |
| DELETE | `/auth/sessions/{id}` | `auth:self` | – | Remote sign-out |
| POST | `/auth/step-up` | `auth:self` | `{password}` | `200 {step_up_token, expires_at}` |
| POST | `/auth/token` | public (client creds) | `{grant_type:"client_credentials", client_id, client_secret}` | `200 {access_token, expires_in:900, scope}` |
| GET | `/auth/demo-accounts` | public, `DEMO_MODE` | – | `{password_hint:"1234", accounts:[{username, display_name, role, institution?, description}]}`; `404` when demo mode off |
| GET | `/me` | `auth:self` | – | Profile, roles, scope, permissions[], active elevations, locale |
| PATCH | `/me` | `auth:self` | `{locale}` | Update preferences |
| POST | `/me/elevations` | `elevations:request` | `{case_id, justification, scope_type, scope_ref, duration_minutes}` | `201 {grant_id, expires_at}`; `422` justification < 30 chars |
| DELETE | `/me/elevations/{id}` | `elevations:request` | – | Revoke early |

### 8.4 Claims, enrolments, seat ledger

**`POST /claims`** — permission `claims:write` (IA, or CL with scope), idempotent.

Request:
```json
{
  "programme_id": "0198…",
  "bucket_type": "REGULAR",
  "apaar_id": "991234567890",
  "provisional": null,
  "student": {
    "institution_student_id": "KIT26CS0412",
    "name": "Priya Raghunathan", "dob": "2008-03-14", "gender": "F", "category": "GEN",
    "citizenship_type": "INDIAN", "nationality_code": "IN", "domicile_quota": "N",
    "father_name": "R. Raghunathan", "mother_name": "Lakshmi R.",
    "guardian_name": null, "guardian_phone": null,
    "phone": "9XXXXXXXXX", "email": "priya@example.test", "district": "Coimbatore"
  },
  "admit_now": false, "admission_date": null, "row_key": null
}
```
(`provisional` is `{ "type": "NOGEN|REFUSED|FOREIGN", "document_number": "…" }` when no APAAR ID.)

`201 Created`:
```json
{
  "claim_id": "0198…", "enrolment_ref": "ENR-2026-27-0004127", "outcome": "CLAIMED",
  "status": "CLAIMED",
  "identity": { "status": "VERIFIED", "source": "MOCK_APAAR_REGISTRY", "diagnosis": [] },
  "verification": { "overall_state": "PENDING", "lock1": "SIGNED", "lock2": "PASSED", "lock3": "PENDING",
                    "deadline_at": "2026-11-25T18:29:59Z", "channel": "REGISTRY_MOBILE", "channel_trust": "HIGH" },
  "seat": { "bucket_type": "REGULAR", "cap": 120, "approved_extra": 0, "occupied": 109, "pct": 90.8 },
  "warnings": [ { "code": "SEAT_90_PERCENT", "message": "Bucket is at 90.8% of cap." } ],
  "findings": [], "idempotent_replay": false
}
```
`409 CLAIM_CONFLICT`: `{ "code":"CLAIM_CONFLICT", "resolution_ref":"RES-2026-001873", "contact":{"institution_name":"…","name":"…","email":"…","phone":"…"} }` (public nodal contact only).
`409 SEAT_LIMIT`: `{ "code":"SEAT_LIMIT", "bucket_type":"REGULAR", "cap":120, "occupied":120, "exception_requestable":true }`.
`422 IDENTITY_INVALID`: `errors:[{"field":"apaar_id","code":"CHECKSUM_FAILED","message":"…","fix_suggestion":"Re-check the last digit"}]` or `AADHAAR_SUSPECTED`.
`200` (same institution): `{ "outcome":"DUPLICATE_SAME_INSTITUTION", "claim_id":"…", "duplicate":true }`.

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/claims:validate` | `claims:validate` | Same body; returns `{would_outcome, identity, findings, seat_projection}`; writes nothing |
| GET | `/enrolments` | `enrolments:read` | Filters: `academic_year, programme_id, status, verification_state, identity_status, channel_trust, q` (student ref, enrolment ref, institution student id, APAAR ID exact, or whole name token), `sort`, keyset |
| GET | `/enrolments/{id}` | `enrolments:read` | Detail with decrypted PII (tenant request), locks, findings, credentials; audited |
| GET | `/enrolments/{id}/history` | `enrolments:read` | Event list |
| POST | `/enrolments/{id}/admit` | `enrolments:manage` | `{admission_date}` |
| POST | `/enrolments/{id}/withdraw` | `enrolments:manage` | `{reason_code, reason_text?, effective_date}` |
| POST | `/enrolments/{id}/transfer-out` | `enrolments:manage` | `{effective_date, destination_institution_id?}` |
| POST | `/transfers` | `transfers:write` | Transfer-in claim `{out_enrolment_id? , claim:{…}}` |
| GET | `/transfers` | `transfers:write` / `duplicates:review` | Filters `status` |
| POST | `/enrolments/{id}/confirmation:resend` | `enrolments:manage` | `429 OTP_RESEND_LIMIT` |
| POST | `/enrolments/{id}/assisted-confirmation` | `enrolments:manage`, step-up | `{reason_code, witness_note}` |
| POST | `/enrolments/{id}/correction-requests` | `enrolments:manage` | Institution data fix request `{field, proposed_value, note}` |
| POST | `/enrolments/{id}/release` | `seats:release`, step-up | `{reason}` (AR) |
| GET | `/programmes` | scoped | Programmes with live seat summary; filters `level, mode, q` |
| GET | `/programmes/{id}/intake` | scoped | Intake versions and bucket caps |
| GET | `/programmes/{id}/seat-ledger` | scoped | Buckets + ledger entries (keyset), invariant check flag |
| POST | `/seat-exceptions` | `seat_exceptions:request` | `{seat_bucket_id, extra_seats, justification, evidence_note}` |
| GET | `/seat-exceptions` | scoped | Filters `status` |
| POST | `/seat-exceptions/{id}/decision` | `seat_exceptions:decide` | `{decision: APPROVE\|REJECT, approved_seats?, expires_at?, reason}` + `If-Match` |
| POST | `/identity/check` | IA, IO, AR | Diagnosis only (nothing stored): `{apaar_id, name, dob, phone?}` → verdict + diagnosis |
| POST | `/provisional-ids` | `claims:write` | `{type, document_number, student}` → token (used in a claim) |
| POST | `/provisional-ids/{id}/upgrade` | IA / AR | `{apaar_id}` → `IDENTITY_UPGRADE` or `409 CLAIM_CONFLICT` |

### 8.5 Bulk upload

**Template v1** (`GET /templates/enrolment/latest` → JSON schema; `GET /templates/enrolment/v1/download?format=csv|xlsx`). Header names (case-insensitive, order-independent): 

| # | Column | Req. | Validation |
|---|---|---|---|
| 1 | `row_key` | no | ≤ 64 chars; default hash(`institution_student_id\|programme_code\|year`) |
| 2 | `institution_student_id` | yes | ≤ 40 chars |
| 3 | `apaar_id` | yes unless `provisional_type` | text; spreadsheet scientific notation detected; mock format |
| 4 | `provisional_type` | no | `NOGEN`, `REFUSED`, `FOREIGN` |
| 5 | `document_number` | if `citizenship_type ∈ {FOREIGN, OCI}` or provisional FOREIGN | stored only as keyed hash + last 4 |
| 6 | `student_name` | yes | Unicode text, ≤ 120 |
| 7 | `dob` | yes | `YYYY-MM-DD` or `DD/MM/YYYY` (ambiguous formats flagged) |
| 8 | `gender` | yes | `M`, `F`, `O` |
| 9 | `category` | yes | `GEN, OBC, SC, ST, EWS` |
| 10 | `citizenship_type` | no (default `INDIAN`) | `INDIAN, FOREIGN, OCI` |
| 11 | `nationality_code` | no (default `IN`) | ISO 3166-1 alpha-2 |
| 12 | `domicile_quota` | no (default `N`) | `Y`/`N` |
| 13 | `father_name` | recommended (Warning) | used for duplicate scoring |
| 14 | `mother_name` | no | |
| 15 | `guardian_name` | if minor | |
| 16 | `guardian_phone` | if minor | 10-digit mobile |
| 17 | `phone` | yes | 10-digit mobile |
| 18 | `email` | no | RFC 5322 basic |
| 19 | `district` | no | |
| 20 | `programme_code` | yes | exists for institution |
| 21 | `bucket_type` | no (default `REGULAR`) | `ref.seat_bucket_types` |
| 22 | `admission_date` | no | if present and ≤ today, the row is admitted on commit |

| Method | Path | Permission | Request / response |
|---|---|---|---|
| POST | `/upload-jobs` | `uploads:write` | `{filename, size_bytes, template_version, academic_year}` → `201 {id, status:"UPLOADING", upload_url:"/api/v1/upload-jobs/{id}/content", limits}` |
| PUT | `/upload-jobs/{id}/content` | `uploads:write` | Raw body (CSV/XLSX), `Content-Type: application/octet-stream`; `422 FILE_REJECTED` with reason |
| POST | `/upload-jobs/{id}/complete` | `uploads:write` | → `202`; pipeline starts |
| GET | `/upload-jobs` / `/upload-jobs/{id}` | `uploads:write` | Status, counts, scan result, phase progress `{phase, done, total}` |
| GET | `/upload-jobs/{id}/rows` | `uploads:write` | Filters `outcome, severity, q`; keyset; each row: outcome, errors[] with `fix_suggestion` |
| GET | `/upload-jobs/{id}/errors.csv` | `uploads:write` | Formula-safe CSV |
| GET | `/upload-jobs/{id}/diff` | `uploads:write` | `{new:n, changed:n, unchanged:n, items[]}` |
| POST | `/upload-jobs/{id}/commit` | `uploads:commit`, step-up | `202`; `403 SEPARATION_OF_DUTIES` if committer = uploader; `409 UPLOAD_NOT_READY` |
| POST | `/upload-jobs/{id}/retry-failed-chunks` | `uploads:write` | Re-runs crashed chunks only |
| POST | `/upload-jobs/{id}/cancel` | `uploads:write` | Before commit |

### 8.6 Submissions, windows, findings, rules

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/windows` | scoped | Windows with institution-specific effective close |
| POST | `/window-extensions` | `extensions:request` | `{window_id, new_closes_at, reason}` |
| POST | `/window-extensions/{id}/decision` | `extensions:decide` | `{decision, reason}` + `If-Match` |
| GET/POST | `/submissions` | `submissions:validate` | List; create (get-or-create Draft) `{academic_year, window_id, declared:{programme_id: n}}` |
| POST | `/submissions/{id}/validate` | `submissions:validate` | Readiness check → `{ready, blockers[], errors[], warnings[], reconciliation}` |
| POST | `/submissions/{id}/submit` | `submissions:submit` | `{acknowledged_finding_ids[]}`; `409 WINDOW_CLOSED`, `409 INVALID_STATE_TRANSITION` |
| POST | `/submissions/{id}/decision` | `submissions:decide` | `{decision: ACCEPT\|REQUEST_REVIEW, notes}` + `If-Match`; `403 SEPARATION_OF_DUTIES` |
| POST | `/submissions/{id}/corrected` | `submissions:submit` | Moves `UNDER_REVIEW → CORRECTED` |
| POST | `/commitments` | `commitments:sign`, step-up | `{submission_id}` → builds tree, signs → `201 {id, merkle_root, leaf_count, status:"PENDING_COUNTERSIGN"}` |
| POST | `/commitments/{id}/countersign` | `commitments:countersign`, step-up | → `COMPLETE`; submission `LOCKED` |
| GET | `/commitments/{id}` / `/commitments?institution_id=` | scoped | Detail incl. signatures |
| GET | `/commitments/{id}/proof?enrolment_id=` | IA, AR, ST (own) | `{leaf, leaf_hash, path[], root, signatures}` |
| GET | `/findings` | `findings:read` | Filters `institution_id, academic_year, severity, status, rule_code, q`; keyset |
| POST | `/findings/{id}/acknowledge` | `findings:ack` | → `ACKNOWLEDGED` |
| POST | `/findings/{id}/waive` | `findings:waive` | `{reason_code, note}` + `If-Match`; for `IDN-PROBABLE` also sets Lock 2 `PASSED` |
| GET | `/rule-sets` / `/rule-sets/{id}` | `rulesets:read` | Rules included |
| POST | `/rule-sets` | `rulesets:draft` | `{name, base_rule_set_id}` → new DRAFT |
| PUT | `/rule-sets/{id}/rules/{rule_code}` | `rulesets:draft` | Edit a rule in a DRAFT only |
| POST | `/rule-sets/{id}/publish` | `rulesets:publish`, step-up | `403 SEPARATION_OF_DUTIES` if publisher = drafter |
| POST | `/rule-sets/{id}/activate` | `rulesets:publish`, step-up | Rollback (re-activate a retired set) |
| POST | `/simulations` | `simulations:run` | `{draft_rule_set_id, cycles:["2025-26"]}` → `202` |
| GET | `/simulations/{id}` | `simulations:run` | `{status, institutions_flagged, programmes_affected, findings_by_severity, reviewer_workload_hours, skipped_rules[]}` |

### 8.7 Duplicates, intelligence, cases

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/duplicate-candidates` | `duplicates:review` | Filters `status, timeline_class, min_score, state`; per-field contributions, **no PII** |
| GET | `/duplicate-candidates/{id}` | `duplicates:review` | With `reveal=true` requires `GRANT_REQUIRED` unless grant for linked case |
| POST | `/duplicate-candidates/{id}/decision` | `duplicates:review` | `{decision: DISMISS\|CONFIRM_DUP\|LINK_AS_TRANSFER, reason}` + `If-Match` |
| GET | `/trust-index` | `trust:read` | Keyset; filters `state, band, institution_type`; `?institution_id=` detail with components and evidence refs |
| GET | `/alerts` | `dashboards:aicte` | Filters `layer, severity, status, institution_id` |
| GET | `/alerts/{id}` | `dashboards:aicte` | Baseline, deviation, contributions, evidence, model version |
| POST | `/alerts/{id}/open-case` | `cases:manage` | `{title, response_days}` → case |
| GET | `/cases` / `/cases/{id}` | `cases:manage` (AR), `cases:respond` (IA own) | Timeline, evidence pack, outcome |
| POST | `/cases/{id}/request-response` | `cases:manage` | `{questions, due_at}` |
| POST | `/cases/{id}/respond` | `cases:respond` | `{response_text, attachments_note}` |
| POST | `/cases/{id}/outcome` | `cases:manage` | `{outcome_code, notes}` → `CLOSED` |
| POST | `/cases/{id}/appeals` | `appeals:file` | `{grounds}` |
| POST | `/appeals/{id}/decision` | `appeals:decide` | `{decision: UPHELD\|OVERTURNED, notes}`; `403 SEPARATION_OF_DUTIES` for case owner |
| GET | `/cases/{id}/evidence-pack` | `cases:manage` | JSON + `sha256`; student-level appendix only under grant |
| GET | `/cases/{id}/students` | `students:read` (grant) | Decrypted rows; counted against the grant; audited |
| POST | `/feeds/liveness` | `feeds:write` | `{records:[{apaar_id, has_abc_credit, has_exam_registration, last_activity_at}]}` → hashed on receipt; `202` |
| POST | `/intel/run` | `demo:tools` or AR | `{job: trust_index\|anomaly\|liveness}` → `202` |

### 8.8 Student and guardian

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/student/enrolments` | `student:wallet` | Own enrolments, locks, deadlines; `?on_behalf_of=<student_id>` in guardian mode |
| GET | `/student/dependants` | `student:wallet` | Linked minors (verified guardian links) |
| POST | `/student/guardian-links` | `student:wallet` | `{student_ref, relationship}` → OTP sent to guardian phone on file (mock SMS); `POST …/{id}/verify {otp}` |
| POST | `/student/enrolments/{id}/otp:request` | `student:confirm` | Sends/re-sends OTP (`429 OTP_RESEND_LIMIT`) |
| POST | `/student/enrolments/{id}/confirm` | `student:confirm` | `{otp}` → `200 {lock3:"CONFIRMED", overall_state}`; `422 OTP_INVALID`, `410 OTP_EXPIRED`, `429 OTP_ATTEMPTS_EXCEEDED` |
| POST | `/student/enrolments/{id}/dispute` | `student:confirm` | `{reason}` → `DISPUTED`, case opened |
| POST | `/student/apaar-consent` | `student:wallet` | `{decision: GRANTED\|REFUSED}` (never blocks counting) |
| GET/POST | `/student/consents` | `student:wallet` | List; grant `{verifier_org_id, purpose_code, fields[], expires_at}` |
| POST | `/student/consents/{id}/withdraw` | `student:wallet` | Effective immediately |
| GET | `/student/consent-requests` | `student:wallet` | Pending verifier requests; `POST …/{id}/decision {GRANT\|REFUSE}` |
| GET | `/student/access-log` | `student:wallet` | Verification requests and staff elevations, keyset |
| GET | `/student/credentials` | `student:wallet` | With status; `GET …/{id}/certificate.pdf` |
| POST | `/student/share-links` | `student:wallet` | `{credential_id, purpose_code, fields[], expires_in_days ≤ 30}` → `{token, url}` once |
| DELETE | `/student/share-links/{id}` | `student:wallet` | Revoke |
| POST | `/student/correction-requests` | `corrections:request` | `{enrolment_id, field, proposed_value, note}` |
| POST | `/student/data-requests` | `dsr:request` | `{type, details}`; `GET` list; `GET …/{id}/export.json` for fulfilled ACCESS |
| GET | `/student/inclusion-proof?commitment_id=` | `student:wallet` | Merkle proof for own enrolment |
| AR side | `/corrections` (GET), `/corrections/{id}/decision` (POST, `corrections:decide`); `/data-requests` (GET), `/data-requests/{id}/decision` (POST, `dsr:decide`) | | `If-Match` |

### 8.9 Dashboards and reports

| Method | Path | Permission | Notes (every response has `as_of`) |
|---|---|---|---|
| GET | `/dashboards/aicte/summary` | `dashboards:aicte` | National KPIs; filters `academic_year, state, region` |
| GET | `/dashboards/aicte/utilisation` | same | Seat explorer: filters `state, region, level, institution_type, band(<50\|50-89\|90-99\|100), institution_id`, `sort`, keyset |
| GET | `/dashboards/aicte/geo` | same | State/district heat data (counts, utilisation, verified %) |
| GET | `/dashboards/aicte/alerts`, `/duplicates`, `/compliance`, `/scorecards`, `/trends` | same | Compliance = submission state per institution; trends = daily series incl. prior-year comparison |
| GET | `/dashboards/institution/{id}/summary` | `dashboards:institution` | Seat ledger, awaiting confirmation, fix list, findings, submission, Trust Index, notification delivery |
| POST | `/reports` | `reports:request` | `{type, params, format: csv\|xlsx\|pdf}` → `202 {job_id}` |
| GET | `/reports` / `/reports/{id}` | `reports:request` | Status, `sha256` |
| GET | `/reports/{id}/download` | `reports:request` | File |
| GET | `/public/reports/{ref}` | public | `{title, generated_at, sha256, rule_set_version}` for the QR |

### 8.10 Credentials, verifier and public verification

| Method | Path | Service | Permission | Notes |
|---|---|---|---|---|
| POST | `/credentials` | core | `credentials:issue`, step-up | `{enrolment_id, type:"ENROLMENT_PROOF"}` → `201 {credential_ref, status_url, qr_payload}`; `409` unless enrolment `VERIFIED` and `ADMITTED` |
| POST | `/credentials:issue-all-verified` | core | `credentials:issue`, step-up | `202` job |
| GET | `/credentials` | core | IA own / AR (aggregate+lookup by ref) | Filters `status, q` |
| POST | `/credentials/{id}/revoke` | core | `credentials:revoke`, step-up | `{reason_code, note}` |
| POST | `/credentials/{id}/supersede` | core | `credentials:revoke` / IA | `{reason_code}` |
| GET | `/credentials/{id}/certificate.pdf` | core | IA own, ST own | QR + printed fields |
| POST | `/verifier/requests` | verify-api | `verifier:request` | `{credential_ref\|share_token, purpose_code, question:{type, params}, nonce?}` |
| GET | `/verifier/org/usage` | verify-api | `verifier:request` | Counts by purpose/outcome, remaining hourly quota |
| GET | `/public/credentials/{ref}/status` | verify-api | public | Known: `{status: VALID\|REVOKED\|SUPERSEDED, issuer, kid, checked_at}`; unknown: `{status:"UNVERIFIABLE"}`; no personal data |
| POST | `/public/verify` | verify-api | public (rate-limited) | `{share_token, nonce?}` → signed answer limited to link scope |
| GET | `/public/status-lists/{id}` | verify-api | public | Signed bitstring status list JWT, `Cache-Control: max-age=60` |
| GET | `/public/jwks` / `/public/key-status` | verify-api | public | Public keys; key states |
| GET | `/v/{ref}` | web | public | Human page for the QR (live status + boundary statement) |

`POST /verifier/requests` success:
```json
{ "result": "ANSWER", "answer": "YES", "fields": { "institution": "Kaveri Institute of Technology", "programme": "B.E. Computer Science", "status": "VERIFIED_ENROLMENT" },
  "disclosure": { "shared": 3, "of": 40 },
  "signed_answer": "eyJhbGciOiJFUzI1NiIsImtpZCI6Imt2LTAxIn0…", "nonce": "n-8f2a…", "expires_at": "2026-10-26T10:05:00Z",
  "signature_note": "A signature proves integrity and key control, not that the underlying academic fact is true." }
```
Neutral (identical for unknown, unconsented, non-existent):
`{ "result": "NEUTRAL", "message": "No verifiable answer is available for this request." }` with a constant-time-style random delay of 50–150 ms.

### 8.11 Administration, onboarding, integration

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/onboarding/institutions` | public | `{aicte_id, officer_name, designation, email, phone}` → `202`; rate-limited |
| GET | `/onboarding/requests` | `onboarding:decide` | |
| POST | `/onboarding/requests/{id}/decision` | `onboarding:decide` | `{decision, reason}` |
| GET/POST | `/institutions/{id}/users` | `users:manage` | Create IO/IA `{username, display_name, role}`; `PATCH …/{user_id}` `{status}` |
| GET/POST | `/admin/users` | `users:manage` (AS) | AICTE users `{username, role, scope}` |
| GET/PATCH | `/admin/verifier-orgs` | `verifier_orgs:manage` | Suspend/activate; scopes |
| GET/POST | `/clients` | `clients:manage` | Create returns `client_secret` once; `POST /clients/{id}/rotate-secret` |
| GET/POST | `/webhooks` | `clients:manage` | `{url, events[]}`; `GET /webhooks/{id}/deliveries`; `POST /webhooks/{id}/deliveries/{did}/replay` |
| GET | `/audit/entries` | `audit:read` | Filters `actor_id, action, entity_type, institution_id, case_id, from, to`; keyset |
| POST | `/audit/integrity-check` | `audit:read` | `202`; result `{status: OK\|BROKEN, checked, first_broken_seq?, checkpoints_verified, worm_matches}` |
| GET | `/audit/checkpoints` | `audit:read` | |
| GET | `/ops/status` | `ops:read` | Queues, dead jobs, outbox/sealer lag, breaker, rate-limit rejections, latency percentiles |
| POST | `/ops/jobs/{id}/replay` | `ops:read` | Replay a dead job |
| GET | `/healthz`, `/readyz` | internal | |

### 8.12 Demo tools (`DEMO_MODE=true`, AS only; `404 DEMO_ONLY` otherwise)

| Method | Path | Notes |
|---|---|---|
| POST | `/demo/registry-mode` | `{mode: UP\|DOWN\|SLOW\|FLAKY, latency_ms?, error_rate?}` → proxies mock control |
| POST | `/demo/clock` | `{advance_days}` or `{offset_seconds}`; `{reset:true}` |
| POST | `/demo/jobs/run-now` | `{schedule_name}` |
| GET | `/demo/sms-inbox` | `?to=&limit=` — proxies mock SMS inbox (labelled `SIMULATED`) |
| GET | `/dev/webhook-sink` / `POST /dev/webhook-sink` | Local webhook receiver and viewer |
| POST | `/demo/reset` | `{confirm:"RESET", profile}` → `202` job runs `reseed` |

### 8.13 Webhook payloads (identifiers only)
```json
{ "id": "evt_0198…", "type": "claim.created", "created_at": "2026-10-26T09:41:07Z",
  "data": { "claim_id": "0198…", "institution_id": "0198…", "programme_id": "0198…", "outcome": "CLAIMED" } }
```
Headers: `X-Sentinel-Event`, `X-Sentinel-Signature: t=1766742067,v1=<hex HMAC-SHA256(secret, "t.body")>`, `X-Sentinel-Delivery`. Receivers MUST reject timestamps older than 5 minutes.

### 8.14 Mock-services API (not part of the product contract)
`GET /registry/v1/persons/{apaar_id}` · `POST /sms/v1/messages` · `GET /sms/v1/messages` · `POST /email/v1/messages` · `GET /aicte-master/v1/export` · `POST /control/registry` · `POST /control/sms` · `POST /control/feeds/push` · `GET /` (status page listing mode and counters). All responses include `"source":"MOCK_…"`.

---

## 9. Authentication, Session Management, Security, Audit Logging and Data Protection

Principles carried from the source: **least privilege; separation of duties; defence in depth** (service authorisation → database RLS → encryption); **aggregate by default**; **tamper evidence**; **every sensitive action audited**. [SRC §7]

### 9.1 Authentication and sessions

#### 9.1.1 Mechanisms (prototype)

| Principal | Mechanism | Source behaviour replaced |
|---|---|---|
| Institution, AICTE, student, verifier users | `POST /auth/login` (username + argon2id-verified password); bearer JWT HS256, 60 min; `iam.sessions` row; 30 min idle timeout; remote sign-out; lockout after 5 failures for 5 min | Supabase Auth PKCE + mandatory TOTP MFA + refresh rotation (DEC-05, DEC-08) |
| Signing actions | Step-up: re-enter password → 5-minute `X-Step-Up-Token` bound to the session | MFA step-up |
| ERP/feed clients | OAuth2 client credentials → 15-min bearer with scopes; secrets hashed, shown once, rotatable; HMAC-signed webhooks | private_key_jwt/mTLS (Future) |
| Students/guardians | Same username/password login; OTP only for Lock 3 and guardian-link verification | Phone-OTP login |
| Public verifier | No token; per-IP rate limits | same |

#### 9.1.2 Session rules
JWT is held in memory (and `sessionStorage` for reload); never in `localStorage` or cookies (no CSRF surface, source §7.10). Logout, deactivation, password change, or revocation make the next request fail with `401 SESSION_EXPIRED`. Role/scope for **sensitive permissions** is re-read from `iam.user_roles` per request. Expired sessions are purged after 30 days.

#### 9.1.3 Demo accounts and role switching
All demo accounts are defined in §11.3, are marked `is_demo=true`, and use the password from `SEED_DEMO_PASSWORD` (default `1234`). The login page lists them as one-click "Sign in as" cards that pre-fill the form; the header "Switch role" menu performs a normal logout/login. There is **no** passwordless backdoor.

#### 9.1.4 ⚠ Demo-credential notice (must appear in README, login page footer, and this PRD)

> **Local demonstration only.** Every demo account uses the same trivial password `1234` so evaluators can switch roles quickly. These credentials, the `SEED_DEMO_PASSWORD` setting, the `/auth/demo-accounts` endpoint, the Demo Tools, the mock services and the `DEMO_MODE` flag **must not exist in any production deployment**. Before any non-local deployment: set `APP_ENV=production`, remove demo users (`sentinel demo purge-accounts`), set strong secrets, enable real authentication (Supabase/OIDC with TOTP MFA or a government IdP), and replace the software `KeyProvider`. The API refuses to start in production mode if any of these remain (REQ-AUTH-07).

### 9.2 Authorisation layers [SRC §7.4]
Four ordered checks on every request (REQ-ACL-02): (1) JWT verification, HS256 only; (2) role and scope with sensitive-permission re-check; (3) tenant check; (4) PostgreSQL RLS with `SET LOCAL` GUCs from the verified token. Permissions come from the §4.3 registry; **an unprotected route fails the build**. `404` is used where existence must not be revealed. Database access patterns follow DEC-21 (`app_migrator` vs `app_runtime`; code-level `agg`, `drilldown`, `verifier` repositories).

### 9.3 Data classification and protection [SRC §7.6]

| Class | Examples | Prototype protection |
|---|---|---|
| C0 public | Public JWKS, status lists, rule descriptions, report verification metadata | Integrity by signature |
| C1 internal | Aggregates, institution master data | RBAC; HTTPS in non-local deployments |
| C2 pseudonymous | `identity_key`, match encodings, name tokens, phone/email hashes | Keyed HMAC with versioned keys; never returned by any API; used for indexes only |
| C3 personal | Name, DOB, parents/guardian, phone, email, district, passport/OCI digits | Per-institution AES-256-GCM envelope encryption in `pii.pii_records`; decrypted only inside the tenant's own request, a worker job, or a `drilldown` call with a valid grant; never logged; passport/OCI numbers only as keyed hash + last four |
| C4 secrets and keys | `MASTER_KEY`, `JWT_SECRET`, `IDENTITY_HMAC_KEY`, wrapped data keys, signing keys, client secrets | Env vars / `data/keys` (wrapped by `MASTER_KEY`); not in git; `.env.example` has placeholders only |

* **Aadhaar** is never collected, stored, logged or printed. Validators reject 12-digit Verhoeff-valid numbers in fields not meant to hold them and warn the user (REQ-IDN-02). A schema lint and a log-scan test enforce it.
* **Logs and metrics** pass the redaction processor (REQ-OPS-08).
* **TLS** is expected from any non-local reverse proxy (Compose demo may add Caddy/nginx); local HTTP is acceptable in the prototype.
* **Logical federation (C-01):** AICTE roles cannot decrypt personal data without a case-gated, time-limited grant.

### 9.4 Key management (prototype)

| Key | Where | Rotation / compromise (prototype) |
|---|---|---|
| `MASTER_KEY` (32 bytes, base64) | Env | Wraps data keys and private signing keys; rotation Future |
| Per-institution data key (`pii.data_keys`) | DB, wrapped by `MASTER_KEY` | Versioned; new version supported; re-encryption job Future |
| Identity HMAC key (`IDENTITY_HMAC_KEY`, `key_version`) | Env | Dual-key period designed (`key_version` column); rotation job Future |
| Issuer, institution-claim, AICTE attestation/commit, sealer, verifier-answer, status-list ES256 keys | `cred.signing_keys` (private part wrapped) | One ACTIVE key each + one RETIRED sample key; rotation and two-approver workflow **Deferred**; `/public/key-status` publishes states |
| Webhook secrets, client secrets | DB (encrypted / hashed) | Rotate endpoints; last-used tracked |

If the `KeyProvider` is unavailable, signing and decrypt-dependent actions **fail closed** with `503 KEY_UNAVAILABLE`; claims needing only HMAC continue with the in-memory key.

### 9.5 Audit logging [SRC §7, §8.9]
Scope, shape, hash chain, checkpoints, integrity check and tamper demo are defined in REQ-AUD-01…09. Additionally: audit entries for denials include the requested permission; entries never contain PII (identifiers and counts only); elevation reads include `case_id` and `justification`; the audit explorer is read-only. **Alerts** (shown in Operations and AS inbox) fire on: unusual staff access volume (> 200 revealed records/hour), repeated lookups across institutions, large or repeated exports, verifier anomalies, and repeated failed logins.

### 9.6 Common vulnerability controls [SRC §7.10]

| Risk | Control in the prototype |
|---|---|
| Broken access control / BOLA | Permission registry + RLS + object-level checks in every handler; UUIDv7 ids (non-sequential); generated cross-role and cross-tenant tests |
| Cryptographic failures | AES-256-GCM, HMAC-SHA256, ES256; no custom crypto; JWT alg allow-list |
| Injection | Parameterised SQL/ORM; Pydantic strict models (unknown fields rejected); CSV formula neutralisation on import and export |
| Authentication failures | Lockout, throttling, OTP attempt/resend limits, session binding, step-up |
| Integrity failures | Hash-chained audit, signed credentials, verified webhooks |
| SSRF | Webhook URL validation against private ranges (demo override flag), adapter base URLs from config only |
| Resource consumption | Per-client/user/IP rate limits, request-size limits, bounded pages, per-institution job quota, row and file caps |
| Upload attacks | Size/type/magic-byte checks, macro rejection, zip-bomb limits, row cap; **malware scan simulated** (labelled) |
| CSRF / XSS | Bearer tokens only (no cookie auth); strict CSP (`default-src 'self'`), React escaping, no HTML in user text |
| DoS | Rate limits, bounded transactions (`statement_timeout`), circuit breakers on adapters |

### 9.7 Privacy, consent and DPDP alignment [SRC §7.11] (design alignment; confirm with counsel — G-06)

| Area | Design response in the prototype |
|---|---|
| Notice | Versioned consent notices (en, hi) in `consent.consent_notices`; version stored with each consent |
| Purpose limitation, minimisation | Purpose-coded scopes; central store holds hashes and statuses; verifier answers disclose minimal fields (disclosure meter) |
| Consent and withdrawal | Consent ledger with purpose, scope, expiry, method, withdrawal; explicit `REFUSED` for APAAR consent |
| Children's data | `is_minor` flag; verified guardian links; guardian OTP; no behavioural tracking |
| Statutory reporting | Counting rests on a statutory basis (AS-03); consent never gates counting |
| Accuracy and correction | Correction requests, reviewer workflow, event history |
| Retention and erasure | `ops.retention_policies`; record-level erasure or `REJECTED_LAWFUL_RETENTION` (DEC-15) |
| Breach notification | Runbook `docs/runbooks/breach.md`: detect, contain, assess, notify within the statutory window (72-hour norm in the source analysis), review (documentation only in the prototype) |
| Grievance | Data-subject request endpoint and grievance-officer path in the student portal |
| Residency | N/A locally; documented requirement for any deployment |

### 9.8 Threat model mapping [SRC §7.9]

| Threat | Prototype control | Demonstrated by |
|---|---|---|
| Fake institution account | Onboarding against master, simulated officer check, reviewer approval | S13 |
| Colluding college inflates enrolment with ghost students | Atomic claim, seat ledger, Triple-Lock, cross-institution matching, anomaly layers, liveness (mock) | S9 |
| Institution supplies its own phone number to fake Lock 3 | Registry-mobile preferred; institution number recorded `LOW` trust and measured; shared-phone clusters | S9 |
| Stolen identities used for fake enrolment | Student confirmation, dispute, liveness, student-visible access log | S5, S9 |
| Forged certificate | Signed credential, live status via QR | S7 |
| Verifier over-collects/scrapes | Purpose scopes, minimal answers, neutral responses, rate limits, suspension | S7 |
| Insider alters history | Append-only tables, hash-chained audit, separation of duties | S12 |
| Insider snooping | Aggregate default, case-gated grants, access alerts | S10 |
| Replay of an old verification answer | Nonce, timestamp, 5-minute validity | S7 |
| Key compromise | Key IDs, status registry, status-list revocation (rotation workflow Deferred) | doc only |
| Database leak | Per-institution encryption, hashed identifiers | security tests |
| Gate abuse (claiming students never admitted) | Claims must be confirmed; unconfirmed rate feeds Trust Index; AICTE seat release | S9 |
| Registry-outage abuse | Pending counts visible, revalidated, bounded by deadline | S11 |

### 9.9 Known prototype security limits (be honest in the README)
HS256 shared secret instead of asymmetric JWT; no MFA; software key storage; simulated WORM and malware scan; HTTP locally; single DB role pair; no WAF/CDN; no penetration test; secrets in `.env`. Production hardening checklist is Appendix A (Future column).

---

## 10. UI/UX Requirements

### 10.1 Principles
1. **Role clarity:** each role sees only its own portal and navigation; the header shows the signed-in user, role chip, scope (institution/region), and the demo role switcher.
2. **Honest data:** every figure shows its definition on hover and an `as_of` chip; every mock/simulated source carries a visible `SIMULATED` badge; statuses are never softened (Pending, Unconfirmed, Disputed shown as such).
3. **Fix, don't reject:** error messages are specific, field-level, and carry a suggested fix; no dead-end errors.
4. **Dense but legible:** data-heavy tables with search, filters, sorting, keyset pagination (page-size selector, "Load more"/next-prev, total when requested), saved filter state in the URL.
5. **Safe actions:** irreversible or signing actions use a confirmation dialog stating consequences; signing actions show the step-up dialog.
6. **Polish:** consistent spacing, skeleton loaders, empty states with a next action, inline validation, keyboard operability, responsive down to 360 px.

### 10.2 Visual system
Ant Design 5 with a restrained theme (primary deep blue `#1F4E8C`; success `#2E7D32`; warning `#B7791F`; error `#C62828`; info `#0B6E99`); light and dark themes via CSS tokens (`prefers-color-scheme` honoured, manual toggle). Font: system UI stack (Noto Sans Devanagari fallback for Hindi). A persistent slim banner: **"Demo environment — synthetic data. Demo credentials for local use only."** Charts: Apache ECharts with colour-blind-safe palette and text/value labels (never colour alone).

**Status colours (consistent everywhere):** Verified = green · Pending = blue · Pending fix = orange · Pending verification = purple · Unconfirmed = amber · Disputed = red · Withdrawn/Transferred = grey · Seat bands: < 50% neutral, 50–89% green, 90–99% amber (warning icon), 100% red (stop icon). Findings: Blocker red, Error orange, Warning amber, Info blue. Trust bands: Low priority green, Watch amber, Review priority red (with wording that it is a *review aid, not a verdict*).

### 10.3 Shared components and behaviours

| Component | Requirement |
|---|---|
| Data table | Column sort (allow-listed), search box, filter drawer, chips for active filters, density toggle, CSV export where permitted, sticky header, keyset pagination controls, row count/total, row-level actions, selectable rows for bulk actions |
| `as_of` chip | Shows relative time; amber if > 60 s; click to refresh |
| `SIMULATED` badge | Outlined tag with tooltip naming the real system it stands in for |
| Status tag | Single component maps domain enum → colour, icon, localised label |
| Lock timeline | Three-step visual (Institution · Registry · Student) with state, channel, trust, deadline countdown |
| Forms | Inline validation from the same schema as the API; server errors map to fields via `errors[].field`; unsaved-changes guard |
| Dialogs | Consequence text; reason fields where mandatory (withdraw, waive, release, decisions); step-up dialog for signing actions |
| States | **Loading** (skeletons), **empty** (illustration-free text + primary action), **error** (problem title, detail, `request_id` copy button, Retry if `retryable`), **forbidden** (403 page), **not found**, **offline/API down banner** |
| Toasts | Success/failure with undo only where reversible |
| i18n | English and Hindi switch in the header (student portal fully localised; staff portals localise shared labels, status names and error messages) |
| Accessibility | Semantic landmarks, labelled controls, focus management in dialogs, ≥ 4.5:1 contrast, `aria-live` for async job progress, skip link, no colour-only meaning; axe checks in CI |
| Polling | TanStack Query `refetchInterval` 15 s on dashboards, 2 s on job progress; paused when the tab is hidden |
| Upload | Drag-and-drop zone, template download, client pre-validation summary, stepper (Upload → Scan → Parse → Validate → Ready → Commit), live row counters, error table with fix suggestions |

### 10.4 Navigation and screens

**Public / shared**

| Screen | Route | Content |
|---|---|---|
| Login | `/login` | Username/password; **Demo accounts** cards grouped by role with description; password hint `1234`; demo-credential notice |
| Public verifier status | `/v/:ref` | Live status, issuer, key id, checked-at, boundary statement; neutral wording for unknown refs |
| Dev SMS Inbox `[SIMULATED]` | `/dev/sms-inbox` | Messages by recipient (masked), OTP visible, template, language; auto-refresh |
| Onboarding | `/onboarding` | Institution registration form and status lookup |
| Share-link verify | `/s/:token` | Shows signed minimal answer for a student's share link |

**Institution portal (IA, IO)** — `/institution/…`

| Screen | Route | Roles | Key content |
|---|---|---|---|
| Dashboard | `/institution` | IA, IO | Seat tiles by programme × bucket; claims awaiting confirmation; PENDING_FIX list; findings; submission status; own Trust Index; notifications |
| Seat ledger | `/institution/programmes/:id` | IA, IO | Buckets with progress and thresholds, ledger entries, exception requests, invariant badge |
| Claims | `/institution/claims` | IA, IO(view) | Enrolments table (filters per §8.4); **New claim** drawer; IO sees **Validate** only |
| Claim detail | `/institution/claims/:id` | IA, IO | Student card (decrypted), identity diagnosis, lock timeline, events history, findings, actions: admit, withdraw, transfer out, resend OTP, assisted confirmation, issue credential |
| Uploads | `/institution/uploads` | IA, IO | Jobs list; **New upload** wizard; job detail (rows, errors, diff, commit) |
| Findings | `/institution/findings` | IA, IO | Table; acknowledge (IA) |
| Fix-it list | `/institution/fixes` | IA, IO | PENDING_FIX / PENDING_VERIFICATION items with deadlines and one-click "Edit & revalidate" |
| Transfers | `/institution/transfers` | IA | Out/in records, orphan leads |
| Submissions | `/institution/submissions` | IA, IO | Year/window card, readiness check, declared totals, submit, commitment signing, history |
| Seat exceptions & extensions | `/institution/exceptions` | IA | Requests and decisions |
| Cases | `/institution/cases` | IA | Respond; appeal |
| Credentials | `/institution/credentials` | IA | Issue (single/all verified), certificate PDF, status, supersede |
| Reports | `/institution/reports` | IA | Request and download own reports; enrolment export |
| Admin | `/institution/admin` | IA | Users, API clients, webhooks (+ delivery log, replay), notification delivery |

**AICTE portal (AR, AS)** — `/aicte/…`

| Screen | Route | Roles | Key content |
|---|---|---|---|
| National dashboard | `/aicte` | AR | KPI cards, trend chart (current vs 2025-26), state/district heat table + treemap, alert inbox preview, compliance summary |
| Seat utilisation | `/aicte/utilisation` | AR | Filterable explorer, band chips, export |
| Alerts | `/aicte/alerts` | AR | Inbox sorted by severity; alert detail (baseline, deviation, contributions, evidence, model version, suggested action); **Open case** |
| Trust Index | `/aicte/trust` | AR | Scoreboard; institution detail with component bars and evidence links; "review aid, not a verdict" |
| Duplicate queue | `/aicte/duplicates` | AR | Candidates with score breakdown; decision; **Reveal identity** requires elevation |
| Cases | `/aicte/cases`, `/aicte/cases/:id` | AR | Workspace: timeline, evidence pack, request response, elevation panel, outcome, appeals |
| Reviews | `/aicte/reviews` | AR | Tabs: Seat exceptions · Window extensions · Corrections · Data requests · Onboarding · Probable identity matches |
| Submissions | `/aicte/submissions` | AR | Review queue; accept/review; countersign commitments |
| Rule sets | `/aicte/rules` | AR, AS | List; editor (AS, DRAFT); **Simulator** results; publish (AR), rollback |
| Credentials | `/aicte/credentials` | AR | Lookup by reference; revoke |
| Reports | `/aicte/reports` | AR | Types, formats, download |
| Audit | `/aicte/audit` | AR, AS | Explorer; chain status; **Run integrity check** |
| Verifier orgs | `/aicte/verifiers` | AS | Tiers, scopes, usage, suspend |
| Users | `/aicte/users` | AS | AICTE users and scopes |
| Operations | `/aicte/ops` | AR, AS | Queues, dead jobs, lags, breaker, latency |
| Demo tools | `/aicte/demo` | AS (demo) | Registry mode, clock, run job, SMS inbox link, webhook sink, reset |

**Student portal (ST)** — `/student/…`: *My enrolments* (lock timeline, OTP confirm, dispute) · *Credentials* (QR, certificate, share links) · *Consents & requests* · *Access log* · *Corrections* · *Data requests* · *Guardian* (link, switch to minor) · *Proof* (inclusion proof verifier) · language toggle · grievance officer footer.

**Verifier portal (VU)** — `/verifier/…`: *Verify* (purpose, question, credential ref/QR text input; result card with YES/NO or neutral, fields, disclosure meter, signature note, "copy signed answer") · *Usage* (quota, history).

### 10.5 Key screen specifications

**New claim form (IA).** Programme and bucket selectors show live seat headroom (`109/120`, amber at 90%). APAAR ID field validates format as typed and shows `Aadhaar-like value — do not enter` if applicable; toggle "No APAAR ID" reveals provisional type + document number. Under the form, a **live validate** panel (`/claims:validate`, debounced) previews the outcome (will claim / conflict / full / needs fix). Submit shows the verdict dialog: success with lock timeline and warnings; conflict with resolution reference and public contact; seat limit with **Request exception** button; invalid with field fixes.

**Seat ledger tile.** Programme, level, mode; one bar per bucket with cap, occupied, approved extra, cumulative admitted; markers at 90% and 100%; tooltip with the governed definitions.

**Upload wizard.** Step 1 template + pre-validation summary; step 2 progress; step 3 results with tabs *All / Accepted / Held / Rejected / Duplicates*, expandable rows showing field fixes; step 4 diff; step 5 commit (IA, different user) with progress and per-row outcomes; footer shows the malware-scan line "Not performed in this prototype".

**Duplicate review.** Two columns (anonymised A/B): institution, programme, dates, timeline class, per-field score bars (`name 0.95 · DOB exact · father 0.90`); action buttons; "Reveal identity" opens the elevation dialog (case, justification ≥ 30 chars, duration) and then shows decrypted names with a countdown to grant expiry.

**Trust Index detail.** Radial or stacked bar of six components with points, inputs and links; trend of score; list of open alerts; text "This score only prioritises reviews; it triggers no penalty."

**Verifier result card.** Big YES/NO or NEUTRAL; disclosed field list; disclosure meter `3 of ~40 fields`; expiry countdown (5 min); signature box; the boundary statement; button "Verify signature in browser" (uses JWKS).

**Student lock timeline.** Three steps with plain-language explanation ("Your institution claimed this seat", "The APAAR registry confirmed your details", "You confirm that you are enrolled"), OTP entry with resend/attempts left, dispute link ("I did not enrol here"), channel and trust shown, and a note that refusing APAAR consent never affects being counted.

### 10.6 Responsive, performance and quality bars
Responsive breakpoints 360 / 768 / 1280; tables collapse to cards on phones; initial bundle < 500 kB gzip with route-level code-splitting (portals lazy-loaded; ECharts, SheetJS and Papa Parse loaded on demand); Lighthouse accessibility ≥ 90 on login, institution dashboard, student portal; no console errors; every list has a tested empty, loading and error state.

### 10.7 Copy standards
Plain English (and Hindi for student-facing text); no jargon in student screens; error titles state what happened and the next step; the phrases required by source §9.5 appear on the verifier result, student portal footer and README: *a signature proves integrity, not truth · APAAR is never mandatory for being counted · Aadhaar is never stored or printed*.

---

## 11. Seed-Data Specification, Demo Accounts, Initialisation and Reset

### 11.1 Principles
1. **Everything is synthetic** (AS-09): fictional institutions (named after rivers and ranges), invented people, invented APAAR IDs in the mock format (DEC-17), invented phone numbers that can never reach a real gateway (only the mock SMS service exists), `@example.test` emails. No real Aadhaar-like value appears anywhere.
2. **Deterministic and repeatable:** the same `SEED_RNG` (default `20261026`) and `SEED_ANCHOR_DATE` (default today) yield identical content (ids, names, relationships, dates). Values that depend on secret keys (hashes, ciphertext, signatures) differ per installation.
3. **Relative time:** every seeded date is an offset from the anchor date (e.g. window opened anchor−86 d, closes anchor+20 d) so deadlines, reminders, "days left" and stale-claim logic stay coherent whenever the seed is run.
4. **Real rules, real invariants:** story records (§11.7) are created through the domain services with the clock back-dated; volume data uses validated bulk loaders that write the same rows, events, ledgers, audit entries and outbox rows. Dashboards, scores and reports are **computed from base tables** — nothing is hardcoded (SC-06).
5. **Enrolment-first sizing:** the generator decides each programme's enrolments first, then derives intake and caps from a target utilisation band, so seat ledgers are always consistent.
6. **Labelled ground truth:** injected anomalies are recorded in `seed.ground_truth` for the evaluation harness; the application cannot read that schema.

### 11.2 Profiles and volumes

| Item | `minimal` (CI/tests) | **`standard` (default)** | `full` (source scale) |
|---|---|---|---|
| Institutions (states covered) | 6 (4) | **60 (14)** | 200 (24) |
| Programmes | ~20 | **~210** | ~700 |
| Students (distinct persons) | ~400 | **~14,000** | ~50,000 |
| Enrolments 2026-27 (current cycle) | ~250 | **~8,500** | ~30,500 |
| Enrolments 2025-26 (prior cycle, back-filled) | ~150 | **~5,500** | ~19,500 |
| Claim attempts 2026-27 (all outcomes) | ~330 | **~10,800** | ~39,000 |
| Registry people (mock; incl. ~300 spare) | ~700 | **~14,600** | ~51,000 |
| Upload jobs | 4 | **90** | 300 |
| Findings | ~120 | **~3,000** | ~10,500 |
| Duplicate candidates | 12 | **~160** | ~560 |
| Anomaly alerts / cases | 6 / 3 | **70 / 45** | 240 / 150 |
| Credentials issued | 40 | **~1,400** | ~5,000 |
| Verification requests (last 90 days) | 30 | **~1,800** | ~6,000 |
| Consents / share links | 40 / 8 | **~900 / 120** | ~3,200 / 420 |
| Notifications | ~600 | **~14,000** | ~50,000 |
| Webhook deliveries | 50 | **~6,000** | ~20,000 |
| Audit entries (sealed) | ~1.5 k | **~45 k** | ~160 k |
| Seed time (target, laptop) | < 20 s | **< 3 min** | < 10 min |

Counts are targets with ±5% tolerance except for the **fixed story fixtures**, which are exact.

### 11.3 Demo accounts (all passwords: `1234`)

> **Local demonstration only — see §9.1.4.** The login page lists these as one-click cards; the header role switcher uses the same accounts.

| Username | Role (scope) | Institution / org | Purpose in the demo |
|---|---|---|---|
| `ia.kit` | IA | Kaveri Institute of Technology, Coimbatore (Tamil Nadu · SOUTH) | Main institution admin: claims, seat exceptions, submission, credentials |
| `ia2.kit` | IA | Kaveri Institute of Technology | Second admin so a different IA can commit an IA-uploaded file (DEC-24) |
| `io.kit` | IO | Kaveri Institute of Technology | Operator: uploads/dry-runs, cannot commit |
| `ia.bvec`, `io.bvec` | IA, IO | Bharatiya Vidya Engineering College, Pune (Maharashtra · WEST) | Cross-institution conflict; bulk upload of 300 rows |
| `ia.mpb`, `io.mpb` | IA, IO | Mahanadi Polytechnic, Bhubaneswar (Odisha · EAST) | Minors, NOGEN students, guardian flow |
| `ia.gce` | IA | Gangotri College of Engineering, Hardoi (Uttar Pradesh · NORTH) — *fictional* | Ghost-ring institution: case response and appeal |
| `ia.asm`, `io.asm` | IA, IO | Aravalli School of Management, Jaipur (Rajasthan · NORTH) | PGDM; late submission and window extension |
| `ar.national` | AR (NATIONAL) | AICTE | Reviewer/case owner, countersign, publish rules |
| `ar.national2` | AR (NATIONAL) | AICTE | Second reviewer: decides appeals (≠ case owner) |
| `ar.south` | AR (REGION=SOUTH) | AICTE | Regional scope demonstration (sees only SOUTH institutions) |
| `as.admin` | AS | AICTE | Drafts rules, simulator, verifier orgs, Demo Tools |
| `st.aarav` | ST | Student at KIT (B.E. CSE) | `VERIFIED` enrolment with credential: verifier demo |
| `st.diya` | ST | Student at KIT (B.E. CSE) | `PENDING` Lock 3: OTP confirmation |
| `st.priya` | ST | Student at KIT (B.E. CSE) | Her APAAR ID triggers the double-claim `CONFLICT` at BVEC |
| `st.rohan` | ST | Minor (16) at Mahanadi Polytechnic | Guardian confirmation |
| `gd.meera` | ST (guardian mode) | Guardian of `st.rohan` | Guardian OTP and consent |
| `st.ishaan` | ST | Student at Mahanadi Polytechnic with NOGEN provisional ID | Provisional ID and upgrade |
| `st.fatima` | ST | Real student at BVEC whose APAAR was claimed by the ghost ring at GCE | Dispute, seat release, access log |
| `st.vikram` | ST | Student with a `WITHDRAWN` enrolment only | Erasure request (record-level) |
| `vu.employer` | VU | Meridian Software Services Pvt Ltd (EMPLOYER) | Verification flow |
| `vu.bank` | VU | Sagarmatha Co-operative Bank (BANK) | Different purpose scope and fields |
| `vu.embassy` | VU | Visa Section, Republic of Ardenia (EMBASSY, fictional) | Strict field scope |
| `erp-kit` (client id, secret `1234`) | CL | KIT | `claims:write`, `claims:validate`, `upload:write` — used by `mock_erp_client.py` |
| `feeds-mock` (client id, secret `1234`) | CL | AICTE | `feeds:write` — used by the mock liveness feed pusher |

**Background accounts:** every other seeded institution has one IA (`ia.<slug>`, e.g. `ia.sahyadri01`) and one IO (`io.<slug>`) with the same password, listed under "Other institutions" on the login page (paged, searchable). Total user accounts: standard ≈ 24 featured + 2×54 background.

**Pending onboarding application (fixture):** *Sahyadri Institute of Technology, Belagavi* (fictional) awaits `ar.national` approval (scenario S13).

### 11.4 Reference and configuration seed

| Data | Content |
|---|---|
| Geography | 14 states (standard): Tamil Nadu, Karnataka, Kerala, Telangana, Andhra Pradesh (SOUTH); Maharashtra, Gujarat (WEST); Uttar Pradesh, Rajasthan, Punjab (NORTH); Madhya Pradesh (CENTRAL); West Bengal, Odisha (EAST); Assam (NORTH_EAST); 3–5 districts each (`seed/data/geography.json`) |
| Institution types (standard, 60) | Engineering 28 · Polytechnic 14 · Management 9 · Pharmacy 5 · University department 4 |
| Programme catalogue | UG (B.E./B.Tech: CSE, ECE, Mechanical, Civil, EEE, IT, AI & DS …; B.Pharm; B.Arch), PG (M.Tech, M.Pharm, MBA), DIPLOMA (Mechanical, Civil, Electrical, Computer, D.Pharm), PGDM. Modes: 90% FULL_TIME, 6% PART_TIME (diploma/PGDM executive), 2% ODL, 2% ONLINE |
| Bucket types | `REGULAR`; `LATERAL` (programmes with lateral entry; cap ≈ 10% of regular); supernumerary `SN_FN`, `SN_OCI`, `SN_TFW`, `SN_EWS` (caps ≈ 5–10% on a subset of programmes). **Illustrative values, not Handbook values (G-02, PA-03)** |
| Windows | 2025-26: closed (opened anchor−456 d, closed anchor−346 d); 2026-27: opens anchor−86 d, closes/cut-off anchor+20 d |
| Rule sets | v1 ACTIVE (§5.6.4); v2 DRAFT (by `as.admin`) for scenario S14 |
| Exception rules | 2 approved dual-programme pairs (e.g. B.Tech + BS-data-science online; diploma + part-time certificate) |
| Consent notices | `VERIFICATION_SHARE` v1 and `APAAR_USE` v1 in en and hi |
| Verifier orgs (8) | Meridian Software Services (EMPLOYER), Tamarind Analytics (EMPLOYER), Sagarmatha Co-operative Bank (BANK), Visa Section Ardenia (EMBASSY), Northern Ridge University Admissions (UNIVERSITY), State Technical Education Board (REGULATOR), Kaveri Institute verifier desk (INSTITUTION), QuickHire Staffing (EMPLOYER, **SUSPENDED**) with purpose scopes (§3.16) |
| Keys | One ACTIVE of each purpose + a per-institution claim key + one RETIRED sample issuer key |
| `system_config` | All defaults of REQ-REF-07, Trust Index weights, `liveness_semesters=2`, `semester_days=180`, `fix_deadline_days=14`, `max_block_size=50`, `staged_row_retention_days=7`, `min_minutes_per_finding=6` |
| Schedules & retention | All schedules of REQ-OPS-02; policies of REQ-OPS-07 |

### 11.5 Entity distributions (standard profile; the generator must reproduce these within tolerance, `verify-seed` asserts ±3 percentage points)

**Programme utilisation bands (2026-27, REGULAR buckets):** 25% < 50% · 52% 50–89% · 15% 90–99% · 8% = 100% (of which ¾ have an approved exception making `occupied = cap + approved_extra`).

**Enrolment status (current cycle, 8,500):** ADMITTED 84.5% · CLAIMED 8% (1% of all stale > 45 days) · WITHDRAWN 7% · TRANSFERRED_OUT 0.5%.

**Overall verification state (non-withdrawn):** VERIFIED 55.0% · PENDING 28.0% · UNCONFIRMED 9.0% · PENDING_FIX 6.0% · DISPUTED 2.0%.

**Identity status (non-withdrawn):** VERIFIED 86.0% · PROBABLE_MATCH 2.5% · PENDING_FIX 6.0% · PENDING_VERIFICATION 2.5% · NOGEN 2.0% · CONSENT_REFUSED 0.7% · FOREIGN (provisional) 0.3%.

**Other attributes:** concurrency class EXCLUSIVE 96% / CONCURRENT_OK 3.5% / EXCEPTION_APPROVED 0.3% / TRANSFER_PENDING 0.2% · claim source BULK 74% / PORTAL 18% / API 8% · confirmation channel trust HIGH 78% / LOW 22% (LOW concentrated at the ghost-ring institution and a few weak-IT institutions) · gender M 58 / F 41 / O 1 · category GEN 42 / OBC 31 / SC 14 / ST 6 / EWS 7 · citizenship INDIAN 98 / FOREIGN 1.2 / OCI 0.8 · bucket REGULAR 87 / LATERAL 8 / SN_* 5 · minors 6% overall (22% in diploma programmes) · 6% of students have a native-script name (Devanagari, Tamil, Bengali, Telugu, Gujarati, Kannada, Malayalam).

**Time profile:** claims spread over the 86 days since the window opened with counselling-round spikes at days 10, 24, 38, 52, 66, 80 (each 2.5× baseline) and weekday seasonality; 6% of claims land in the last 48 h of the day before the anchor for the "recent surge" widget.

**Prior cycle 2025-26 (5,500):** ADMITTED 90% · WITHDRAWN 9% · TRANSFERRED_OUT 1%; all submissions LOCKED with COMPLETE commitments; 80% VERIFIED, 12% UNCONFIRMED, 1% DISPUTED; liveness feed: 97% active, 3% no ABC credit/exam registration (ghost candidates, mostly the GCE ring). About 40 persons appear in both cycles (re-admission) to exercise the per-cycle uniqueness rule and cross-cycle overlap warning.

**Claim-attempt outcomes (10,800):** CLAIMED incl. pending-verification 8,500 · INVALID 820 · SEAT_LIMIT 430 · RULE_BLOCKED 380 · CONFLICT 360 · DUPLICATE_SAME_INSTITUTION 270 (sums to ≈ 10,760).

**Findings (3,000):** Blocker 3% · Error 22% · Warning 65% · Info 10% — status OPEN 48 / ACKNOWLEDGED 22 / RESOLVED 24 / WAIVED 6.

**Submissions:** 2026-27 → DRAFT 15 · VALIDATED 9 · SUBMITTED 12 · ACCEPTED 8 (2 with an institution-signed commitment awaiting countersign) · UNDER_REVIEW 6 · CORRECTED 3 · LOCKED 7; 2025-26 → LOCKED 60. Window extensions 6 (PENDING 2, APPROVED 3, REJECTED 1). Late flags: 7 institutions in 2025-26, 3 in 2026-27.

**Seat exceptions (24):** PENDING 6 · APPROVED 12 (2 expired) · REJECTED 6.

**Upload jobs (90):** COMPLETED 52 · PARTIAL 14 · READY_TO_COMMIT 8 · FAILED 6 · CANCELLED 4 · (6 others COMPLETED with 100% NOOP re-uploads); no job is left in-flight.

**Cases (45):** ANOMALY 18 · DUPLICATE 10 · STUDENT_DISPUTE 12 · UNCONFIRMED_SHARE 3 · SEAT_RELEASE 2; status OPEN 12 / AWAITING_RESPONSE 9 / UNDER_REVIEW 8 / CLOSED 14 / APPEALED 2.

**Alerts (70):** layer A 14 / B 20 / C 28 / D 8; severity HIGH 12 / MEDIUM 30 / LOW 28; status OPEN 40 / IN_CASE 18 / DISMISSED 6 / CLOSED 6.

**Trust Index:** computed for all institutions for both years; bands ≈ 70% LOW_PRIORITY, 22% WATCH, 8% REVIEW_PRIORITY (the injected-anomaly institutions).

**Credentials (~1,400):** VALID ≈ 1,310 · REVOKED 60 · SUPERSEDED 30. **Consents (~900):** GRANTED 760 · WITHDRAWN 90 · REFUSED 30 · EXPIRED 20; 45 pending consent requests. **Verification requests (~1,800):** YES 1,100 / NO 150 / NEUTRAL 550. **Webhook deliveries (~6,000):** 12 dead-lettered, 80 awaiting retry. **Notifications:** 92% SENT, 5% FALLBACK, 3% FAILED.

### 11.6 Injected scenarios and labelled ground truth (standard profile; `full` scales ×3.5)

| Injection | Quantity | Ground-truth label |
|---|---|---|
| **Over-admission pressure:** institutions repeatedly hitting 100% | 4 institutions with ≥ 3 full buckets and ≥ 25 `SEAT_LIMIT` attempts; 8 with exception requests | `OVER_ADMISSION` |
| **Exact cross-institution double claims** | 360 `CONFLICT` attempts (~300 distinct students) | – (blocked by index) |
| **Fuzzy true duplicates** (spelling/transliteration/initials variants 20; DOB day-month swap 8; father-name variants 10) | 38 pairs | `DUPLICATE_TRUE` |
| **Hard negatives:** same name different DOB or parents; twins (same DOB and parents) | 70 near-miss pairs incl. 6 twin pairs | (must not be flagged `CONFIRMED_DUP`) |
| **Legitimate transfers:** 14 matched, 5 awaiting counterpart, 3 orphan leads | 22 | `TRANSFER` |
| **Full-time + ODL / online** | 14 | `FT_PLUS_ODL` |
| **Approved exception pairs** | 4 | `EXCEPTION` |
| **Same-institution re-admission** | 12 | – |
| **Identity mismatches at claim:** day-month swap 120, year typo 40, wrong name 100, deactivated ID 25, probable match 210, claims made during an earlier registry outage 240 | 735 | `MISMATCH_TYPE` |
| **Ghost ring (GCE, Hardoi):** 150 ring enrolments in 2026-27 (180 total) and 120 in 2025-26 sharing 12 phone hashes (clusters ≥ 8); 85% UNCONFIRMED/PENDING with `INSTITUTION_MOBILE` (LOW trust); 62% of 2025-26 claims in the last 48 h before window close; zero withdrawals; 2025-26 ring cohort has no ABC credits | 1 institution | `GHOST_RING` |
| **Stolen identities:** 12 DISPUTED at GCE and 2 elsewhere; `st.fatima`'s APAAR claimed at GCE while her genuine BVEC claim got `CONFLICT` | 14 | `STOLEN_IDENTITY` |
| **Zero-attrition suspicious** (≥ 50 enrolments, 0 withdrawals) | 3 institutions | – |
| **FN/OCI share spike**; **category-mix drift** | 2; 2 institutions | – |
| **Benign shared phones** (siblings/guardians) | 30 phones shared by 2–3 students at one institution | (must not auto-flag) |
| **Stale claims** (CLAIMED > 45 days, not admitted) | ~85 | – |

### 11.7 Demo scenarios (acceptance scripts; automated as Playwright/API tests)

Fixed fixtures: programme `KIT-BE-CSE` (REGULAR cap 120, occupied 108 = 90.0%); `KIT-BE-ECE` (cap 60, occupied 59); `BVEC-BE-MECH` (cap 60, occupied 57); spare people `SPARE-001…SPARE-300` with APAAR IDs printed in `docs/demo-script.md` (generated at seed); file `data/demo/bvec_bulk_300.csv`.

| # | Scenario | Actors | Steps → expected result |
|---|---|---|---|
| S1 | **Seat ledger: warn, stop, exception** | `ia.kit`, `ar.south` | Claim `SPARE-001` in KIT-BE-ECE → `201`, occupied 60/60, warning `SEAT_90_PERCENT`; claim `SPARE-002` → `409 SEAT_LIMIT`; request exception (3 seats) → `ar.south` approves 2 → claim `SPARE-002` → `201`, occupied 61/62; ledger shows DEBIT, DEBIT, EXCEPTION_GRANT, DEBIT; AICTE utilisation row updates within 10 s |
| S2 | **Double claim blocked** | `ia.bvec`, `ia.kit` | `ia.bvec` claims `st.priya`'s APAAR ID → `409 CLAIM_CONFLICT` with resolution ref and KIT public contact, no student data; `ia.kit` withdraws her → `ia.bvec` claim now `201` (partial index excludes WITHDRAWN) |
| S3 | **Bulk upload (300 rows)** | `io.bvec`, `ia.bvec` | `io.bvec` uploads the file → dry-run oracle: **ACCEPTED 251, HELD 17, REJECTED 32, NOOP 0**; `errors.csv` has 49 rows with fixes; `io.bvec` cannot commit; before commit `ia.bvec` claims 2 of the last 3 seats in BVEC-BE-MECH; `ia.bvec` commits (step-up) → job `PARTIAL`, 249 committed, 2 `COMMIT_FAILED(SEAT_LIMIT)`; re-uploading the same file → all committed rows `NOOP_DUPLICATE`. *Variant:* `scripts/mock_erp_client.py` submits 20 claims as `erp-kit`; webhooks appear in the sink |
| S4 | **Triple-Lock to credential** | `st.diya`, `ia.kit` | Student sees locks (1 signed, 2 passed, 3 pending); requests OTP → appears in Dev SMS Inbox (to registry mobile, `HIGH` trust); wrong OTP ×5 → `429`; correct OTP → `VERIFIED`; `ia.kit` issues credential (step-up); certificate PDF has QR; `/v/<ref>` shows `VALID` |
| S5 | **Minor + guardian; dispute** | `gd.meera`, `st.rohan`, `st.fatima` | Guardian switches to `st.rohan`, receives guardian OTP, confirms (`channel=GUARDIAN`). `st.fatima` sees an enrolment at GCE she never made → **Dispute** → `DISPUTED`, case `STUDENT_DISPUTE` opened |
| S6 | **Provisional ID and upgrade** | `ia.mpb`, `st.ishaan` | NOGEN student counted and checked, compliance deadline +30 d; `ia.mpb` upgrades with `SPARE-007`'s APAAR ID → `IDENTITY_UPGRADE`; if the ID were claimed elsewhere → `CONFLICT` |
| S7 | **Verification, consent, abuse defences** | `vu.employer`, `st.aarav`, `ar.national` | Verifier requests `IS_ENROLLED` for `st.aarav`'s ref → `NEUTRAL` (no consent), student sees a consent request → grants `EMPLOYMENT_VERIFICATION` (fields: programme, institution) → repeat → signed `YES`, disclosure `2 of ~40`; verify signature in browser; replay after 5 min rejected; share link works via `/s/:token`; `ar.national` revokes the credential → status `REVOKED`; student withdraws consent → `NEUTRAL`; `vu.bank` (different scope) is denied fields it may not see |
| S8 | **Duplicates and transfers** | `ar.national`, `ia.bvec` | Seeded pair "Mohd Imran Khan" / "Mohammad Imran Khan" appears at score ≈ 0.93 `OVERLAP_FT` → review without PII → **Confirm duplicate** → findings to both institutions and a case; a SEQUENTIAL pair is `AUTO_LINKED`; an orphan transfer lead is visible; hard-negative twins are **not** in the queue |
| S9 | **Ghost ring → case → appeal** | `ar.national`, `ia.gce`, `ar.national2` | Alert inbox ranks GCE first (Trust Index < 40, Layer C robust z, graph cluster, LOW-trust share, zero attrition); open case (response due +14 d); elevation (case + justification) reveals ring evidence; request response; `ia.gce` responds; outcome `REFERRED_FOR_INSPECTION` (label stored, **no automatic penalty**); `ia.gce` appeals within 15 d; `ar.national` cannot decide (`SEPARATION_OF_DUTIES`), `ar.national2` decides. `ar.national` releases `st.fatima`'s GCE seat; `ia.bvec` can now claim her genuine enrolment |
| S10 | **Drill-down audit** | `ar.national`, `st.fatima`, `as.admin` | Without grant: `403 GRANT_REQUIRED`; with grant: records revealed, counter increments, audit entry has case and justification; `st.fatima`'s access log shows "AICTE case access"; > 200 reveals/hour raises an alert |
| S11 | **Registry outage** | `as.admin`, `ia.kit` | Set registry `DOWN` → claims succeed as `PENDING_VERIFICATION` (seat consumed, dashboard counter increments, breaker opens); restore `UP` → run revalidation now → statuses resolve to `VERIFIED`/`PENDING_FIX`; `SLOW` mode (3 s) times out at 2 s identically |
| S12 | **Tamper evidence** | `as.admin` / CLI | Integrity check `OK`; `sentinel demo tamper-audit --seq N` → check `BROKEN`, first broken seq reported; `untamper` restores; `sentinel demo tamper-enrolment --id …` → recomputed Merkle root differs from the countersigned root of 2025-26 |
| S13 | **Onboarding and submission → Locked** | `ar.national`, `ia.asm`, `ia.kit`, `ar.south` | Approve Sahyadri application → new IA signs in and creates an IO; `ia.asm` requests window extension → approved; KIT's `ACCEPTED` submission with institution-signed commitment awaiting countersign → `ar.south` countersigns (step-up) → `LOCKED`; `ia.kit` as submitter cannot accept own submission (`SEPARATION_OF_DUTIES`) |
| S14 | **Policy simulator and rule publication** | `as.admin`, `ar.national` | Run the simulator for draft v2 over 2025-26 → result lists institutions flagged, programmes, findings by severity, reviewer workload and skipped rules; `as.admin` cannot publish (`SEPARATION_OF_DUTIES`); `ar.national` publishes (step-up) → new claims carry v2; rollback re-activates v1 |
| S15 | **Student corrections and data rights** | `st.aarav`, `ar.national`, `st.vikram` | Correction of father's name → reviewer approves → `CORRECT` event, vault version bump, credential superseded only if a credential field changed; ACCESS request yields JSON export; `st.aarav` ERASURE → `REJECTED_LAWFUL_RETENTION`; `st.vikram` ERASURE → record-level erase (`erased_at` set, ciphertext null, hashes and events intact) |

Each scenario has an automated test with the exact expected values above (the oracle counts come from the generator, not from hand-typed constants).

### 11.8 Generator design
`backend/seed/`: `profiles.py` (volumes) · `generators/` (`geography`, `institutions`, `programmes`, `people` — Indian names by region/community with variant generator and native-script forms, `apaar` mock IDs with Luhn, `registry` truth, `enrolments`, `anomalies`) · `data/` (curated lists: ≥ 400 first names, ≥ 300 surnames, river/range words for institution names, programme catalogue, geography) · `loaders.py` (COPY/batched inserts, 1,000-row batches, FK-ordered) · `stories.py` (domain-service driven fixtures with back-dated `Clock`) · `derived.py` (runs rules in batch, matching job, intel jobs, projector until empty, credential issuance, commitments) · `verify.py` · `ground_truth.py`.

Order of phases: (1) preflight (DB empty/migrated, keys present) → (2) reference data and keys → (3) institutions, programmes, intake, buckets, data keys, institution claim keys → (4) accounts and roles → (5) mock registry truth + spare people → (6) background population (students, keys, enrolments, events, ledger, verification, confirmations, PII, match features, claim attempts) → (7) injected scenarios → (8) story fixtures via services → (9) derived data (findings, duplicate candidates, liveness feed push + evaluation, Trust Index, alerts/cases, credentials, consents, verification requests, notifications, webhook deliveries) → (10) submissions and commitments (real signatures) → (11) audit seal + checkpoint → (12) projector drains outbox → (13) `verify-seed` → (14) write `docs/demo-script.md` and `data/demo/*.csv` with the generated identifiers.

### 11.9 Initialisation, reset and reseeding

| Command | Effect |
|---|---|
| `sentinel setup` | Generates `.env` secrets if missing, starts DB (Compose), runs migrations, seeds `standard`, runs `verify-seed`, prints demo account summary and URLs. **Idempotent** |
| `sentinel db migrate` | Alembic upgrade to head |
| `sentinel db seed [--profile minimal\|standard\|full]` | Seeds an **empty** database; refuses (exit 2) if data exists unless `--force` |
| `sentinel db reseed [--profile …]` | Truncates all data tables (keeps migrations), clears `data/objects`, `data/worm`, resets mock-services state, regenerates keys, reseeds |
| `sentinel db reset [--profile …]` | Drops and recreates the application schemas, migrates, seeds (full clean slate) |
| `sentinel db verify-seed` | Runs the checks of §11.10; exit 0/1 with a report |
| `sentinel demo purge-accounts` | Deletes all `is_demo` users (pre-production step) |
| UI: Demo Tools → **Reset demo data** | AS only, `DEMO_MODE` only, typed confirmation `RESET`, runs `reseed` as a job and shows progress |

Safeguards: all destructive commands refuse when `APP_ENV=production`; `reset`/`reseed` require `--yes` (CLI) or typed confirmation (UI); a reseed takes the standard-profile target time of § 11.2; after reseed all users are signed out (sessions truncated).

### 11.10 `verify-seed` invariants (each is a test)
1. For every seat bucket: `occupied` = live enrolments in that bucket = `Σ DEBIT − Σ RELEASE` in the ledger; CHECK holds.
2. No student has more than one live `EXCLUSIVE` enrolment per academic year.
3. `enrolment_events.seq` is contiguous per enrolment and starts with `CLAIM`.
4. Every enrolment has a verification row; `deadline_at = claimed_at + confirm_deadline_days`; `overall_state` equals the derivation function of the three locks; `UNCONFIRMED` only where the deadline has passed relative to the clock.
5. Every enrolment has a decryptable PII record (or `erased_at`); passport/OCI values exist only as hash + last four.
6. No Aadhaar-like (12-digit, Verhoeff-valid) value in any text column or generated file.
7. Every `agg.*` table equals a from-scratch recomputation from base tables.
8. Audit chain verifies end-to-end; checkpoints verify; WORM copies match.
9. Every `COMPLETE` commitment's root recomputes from `commitment_leaves`; signatures verify.
10. Credentials: signatures verify; status-list bit matches status; each was issued for an enrolment that was `VERIFIED` and `ADMITTED` at issuance.
11. Submission CHECK constraints hold; locked submissions have complete commitments.
12. Trust Index weights sum to 100 and each stored component recomputes from base tables.
13. `seed.ground_truth` counts equal §11.6 quantities.
14. RLS is forced on all tenant tables; no UPDATE/DELETE grants on history tables.
15. Every demo account logs in with the demo password and reaches its portal.
16. Distribution checks within ±3 pp of §11.5; totals within ±5% of §11.2.
17. Every APAAR ID used by an enrolment exists in the mock registry (except those intentionally injected as `NOT_FOUND`).
18. No orphan rows; all foreign keys valid.

---

## 12. User Stories with Testable Acceptance Criteria

Format: **ID** — *As a [role] I want … so that …* — acceptance criteria are numbered, written as observable outcomes, and each maps to an automated test tagged with the story ID. All stories are MVP. "Dep." lists prerequisite stories/modules.

### 12.1 Institution admin (IA)

| ID | Story | Acceptance criteria | Dep. |
|---|---|---|---|
| IA-01 | Claim a seat for a student before admitting | 1. With `KIT-BE-CSE` at 108/120, a valid claim returns `201`, `outcome=CLAIMED`, `occupied=109`, warning `SEAT_90_PERCENT`. 2. One row each in `enrolments`, `enrolment_events(CLAIM)`, `seat_ledger_entries(DEBIT, occupied_after=109)`, `enrolment_verification`, `confirmation_requests`, `pii_records`, `audit_log`, `outbox_events`. 3. A notification is queued for the student's OTP. 4. The AICTE utilisation row shows 109 within 10 s | GATE, IDN |
| IA-02 | Be certain no seat is oversold | 1. 1,000 parallel claims (concurrency ≥ 50) on a bucket with cap 100 and distinct students → exactly 100 `201`, 900 `409 SEAT_LIMIT`. 2. `occupied` never exceeds `cap + approved_extra` at any sampled moment. 3. Ledger sum equals `occupied` | GATE |
| IA-03 | Learn when a student is already claimed elsewhere | 1. Claiming an ID with a live EXCLUSIVE enrolment at another institution → `409 CLAIM_CONFLICT`. 2. Body has `resolution_ref` and the holder's public contact only — no name, DOB, institution student id, enrolment or student ids. 3. `claim_attempts` has the row with the holder stored internally. 4. No seat consumed | GATE |
| IA-04 | Retry safely | 1. Same `Idempotency-Key` + body → identical response with `Idempotent-Replay: true`, no new rows. 2. Same key, different body → `422 IDEMPOTENCY_MISMATCH`. 3. Concurrent same key → one `201`, other `409 REQUEST_IN_PROGRESS` or replay. 4. Missing key → `400 IDEMPOTENCY_KEY_REQUIRED` | GATE |
| IA-05 | Not create duplicates within my own institution | Claiming the same student twice (same institution, same year) → second returns `200 DUPLICATE_SAME_INSTITUTION` with the first claim id; one enrolment row only | GATE |
| IA-06 | Get precise feedback on a bad ID | 1. Bad checksum → `422 IDENTITY_INVALID` with `CHECKSUM_FAILED` and a fix. 2. A Verhoeff-valid 12-digit value → `AADHAAR_SUSPECTED`; the value appears in no log or table. 3. `9.91E+11` → `SCIENTIFIC_NOTATION` with "format the column as text". 4. Valid format but unknown to registry → `IDENTITY_INVALID` suggesting a provisional ID | IDN |
| IA-07 | Fix, not lose, a student whose record mismatches | 1. A claim whose DOB is a day-month swap → `201`, identity `PENDING_FIX`, overall `PENDING_FIX`, diagnosis `DOB_DAY_MONTH_SWAP` with `fix_deadline_at = claim + 14 d`. 2. Fix-it list shows it. 3. After correcting DOB and revalidating, Lock 2 → `PASSED` and state returns to `PENDING`. 4. The response never reveals registry personal data | IDN |
| IA-08 | Keep admitting when the registry is down | With registry `DOWN`: claim returns `201` with identity `PENDING_VERIFICATION`, seat consumed, dashboard counter +1, circuit breaker `OPEN` after 5 failures; after `UP` + revalidation the claim resolves; never marked `VERIFIED` without a registry response | GATE, IDN |
| IA-09 | Admit students who have no APAAR ID | NOGEN/REFUSED/FOREIGN provisional claims succeed (`PROVISIONAL_OK`); token and compliance deadline returned; passport/OCI number stored only as hash + last 4; student appears in counts and checks | IDN |
| IA-10 | Confirm admission | `admit` on `CLAIMED` → `ADMITTED`, `cumulative_admitted+1`, event `ADMIT`; admission date after the window or after the counselling cut-off is rejected with `FLD-004/005` fix text | GATE, RUL |
| IA-11 | Release a seat when a student withdraws | `withdraw` → status `WITHDRAWN`, `RELEASE` ledger entry, `occupied-1`, credential auto-revoked, the same APAAR can now be claimed at another institution | GATE, CRD |
| IA-12 | Request a seat exception | 1. Allowed when the bucket is ≥ 90%; request `PENDING`. 2. After `ar.south` approves 2 seats: `approved_extra=2`, `EXCEPTION_GRANT` entry, claims succeed until `occupied = cap + 2`. 3. Rejected request leaves the cap unchanged | GATE |
| IA-13 | Commit a validated bulk file | 1. Only the uploader's different IA can commit (`403 SEPARATION_OF_DUTIES` if same user). 2. Commit processes `ACCEPTED` rows only; job ends `COMPLETED` or `PARTIAL`. 3. Rows failing at commit show `COMMIT_FAILED(SEAT_LIMIT/CONFLICT)`. 4. Step-up token required | ING |
| IA-14 | Submit my annual submission | 1. `validate` returns blockers/errors/warnings and reconciliation (declared vs rows vs approved intake). 2. Submit refused with open Blocker/Error (`INVALID_STATE_TRANSITION`) or unacknowledged Warnings. 3. After window close without extension: `late_flag=true`. 4. Status `SUBMITTED` | SUB |
| IA-15 | Sign the cohort commitment | After `ACCEPTED`, `POST /commitments` (step-up) creates root, leaf count, institution signature; status `PENDING_COUNTERSIGN`; after AICTE countersign, submission is `LOCKED` | AUD |
| IA-16 | Issue credentials only for genuinely verified students | `POST /credentials` for a `PENDING` enrolment → `409`; for `VERIFIED` + `ADMITTED` → `201` with `credential_ref`; certificate PDF prints name, APAAR ID, student ID, programme, institution, ref, QR; no Aadhaar | CRD |
| IA-17 | Manage my institution's users | Create/deactivate IO or IA users in own institution; attempt to create an AR/AS or user in another institution → `403`/`404`; deactivated user's session fails next request | ONB |
| IA-18 | Respond to AICTE cases and appeal | Respond within the window; appeal within 15 days of closure; late appeal → `409 WINDOW_CLOSED`; appeal decided by a different reviewer than the case owner | INT |
| IA-19 | Receive signed webhooks | Endpoint receives `claim.created` with valid HMAC (`t.body`) and no personal data; failing endpoint retried on schedule then `DEAD_LETTERED`; replay works | INTG |
| IA-20 | Record transfers | `transfer-out` releases the seat; receiving institution records `transfer-in` referencing it → status `MATCHED`; no counterpart after 30 days → `ORPHAN_LEAD` visible to AICTE | DUP |
| IA-21 | See my institution's live position | Dashboard widgets (seat tiles, awaiting confirmation with days left, fix list, findings, submission, Trust Index, notification delivery) are computed from live data and carry `as_of` | DASH |
| IA-22 | Search my records safely | Search by student ref, enrolment ref, institution id, exact APAAR ID, or whole name token returns only my institution's rows; partial-token search returns none; other tenants' ids return `404` | GATE |

### 12.2 Institution data operator (IO)

| ID | Story | Acceptance criteria | Dep. |
|---|---|---|---|
| IO-01 | Prepare a clean file before uploading | Downloaded template v1 matches the server schema; in-browser pre-validation flags missing required columns, bad enumerations, scientific-notation APAAR values, and blocks upload only for structural errors | ING |
| IO-02 | See exactly what is wrong with each row | After dry-run: counts match the oracle (S3: 251/17/32/0); each rejected/held row shows field, rule code, message and fix; `errors.csv` downloads with formula-safe cells; diff view classifies NEW/CHANGED/UNCHANGED | ING |
| IO-03 | Not be able to commit or submit | `commit`, `submit`, `admit`, `claims:write` → `403 FORBIDDEN` (audited); UI shows "Waiting for institution admin" instead of the button | ACL |
| IO-04 | Check a single record without consuming a seat | `claims:validate` returns the would-be outcome; no rows written; seat count unchanged | GATE |
| IO-05 | Re-upload a corrected file safely | Re-uploading with the same `row_key`s → previously committed identical rows `NOOP_DUPLICATE`; corrected rows `CHANGED` in diff | ING |

### 12.3 AICTE reviewer (AR)

| ID | Story | Acceptance criteria | Dep. |
|---|---|---|---|
| AR-01 | See where intake is exceeded or close | National dashboard KPIs and utilisation explorer filter by state/region/level/type/band; bands match `agg.seat_utilisation`; values equal recomputation from base tables; small groups (< 5) shown as suppressed | DASH |
| AR-02 | Only see my region | `ar.south` lists only SOUTH institutions in dashboards, alerts, cases, queues; direct access to a NORTH institution id → `404`; `ar.national` sees all | ACL |
| AR-03 | Decide seat exceptions | Approve with seats and expiry or reject with reason; `If-Match` required; institution notified; decision audited; decider ≠ requester | GATE |
| AR-04 | Review duplicate candidates without seeing identities | Queue shows score, per-field contributions, timeline class; no PII in payload; `reveal=true` → `403 GRANT_REQUIRED` until an elevation exists | DUP, ACL |
| AR-05 | Investigate with a case and time-limited access | `elevations` needs a case and ≥ 30-char justification; grant expires (≤ 4 h); every reveal increments `records_revealed` and writes an audit entry with case + justification; after expiry `403 GRANT_EXPIRED` | INT, ACL |
| AR-06 | Keep appeals impartial | The case owner calling `appeals/{id}/decision` → `403 SEPARATION_OF_DUTIES`; a different AR can decide; label updated to UPHELD/OVERTURNED | INT |
| AR-07 | Waive warnings and probable matches with a reason | Waive requires `reason_code`; for `IDN-PROBABLE` Lock 2 → `PASSED`; waived findings stay visible with who/why | RUL |
| AR-08 | Review submissions | Accept (`approved_by ≠ submitted_by`) or request review with notes; countersign commitment (step-up) → `LOCKED`; cannot accept without institution signature | SUB, AUD |
| AR-09 | Publish rules safely | Publisher ≠ drafter enforced in DB; publishing requires step-up; previous set retired; rollback re-activates the previous set; claims record the rule-set id used | RUL |
| AR-10 | Revoke a credential | Revoke with reason (step-up) → status `REVOKED`, status-list bit set, public status `REVOKED` within the cache TTL, webhook `credential.revoked`, audit entry | CRD |
| AR-11 | Release a seat in a gate-abuse case | Release requires reason + step-up, status `WITHDRAWN(AICTE_RELEASE)`, `RELEASE` entry, institution notified, case opened | GATE |
| AR-12 | Trust the evidence trail | Audit explorer filters work; `integrity-check` returns `OK` on a clean seed and `BROKEN` with the first bad `seq` after tampering | AUD |
| AR-13 | Approve new institutions | Onboarding decision creates the IA user and sets the institution `ACTIVE`; mismatch from the simulated officer check is shown prominently | ONB |
| AR-14 | Decide correction and data-subject requests | Approved correction → `CORRECT` event and vault version; ERASURE on active enrolment → `REJECTED_LAWFUL_RETENTION`; otherwise record-level erasure | STU |
| AR-15 | Explain a Trust Index score | Detail shows six components with points, inputs and evidence links; total equals the sum; band label states it is a review aid, not a verdict | INT |
| AR-16 | Generate reports | CSV/XLSX/PDF for each type; stamped with rule version and cut-off; small cells suppressed; QR resolves to `/public/reports/{ref}` with matching SHA-256 | RPT |

### 12.4 AICTE system admin (AS)

| ID | Story | Acceptance criteria | Dep. |
|---|---|---|---|
| AS-01 | Draft a rule change | Create a DRAFT from the active set, edit a rule, cannot edit a published set; draft cannot be published by the same user | RUL |
| AS-02 | Test the change on a past cycle | `POST /simulations` returns `202`; result lists institutions flagged, programmes affected, findings by severity, reviewer workload, and `skipped_rules`; running twice yields identical numbers; no production data changes | RUL |
| AS-03 | Suspend a misbehaving verifier | Suspended org's requests → `403 VERIFIER_SUSPENDED`; reactivation restores; both audited | VER |
| AS-04 | Demonstrate outages and deadlines | Demo Tools set registry mode, advance the clock, run schedules now; unavailable when `DEMO_MODE=false` (`404 DEMO_ONLY`) | OPS |
| AS-05 | Manage AICTE users | Create AR/AS with scope; cannot change own role; deactivation revokes sessions | ONB |
| AS-06 | Not see student data by default | AS calling enrolment/student or institution dashboard endpoints → `403`; audit explorer and ops are readable | ACL |

### 12.5 Student and guardian (ST)

| ID | Story | Acceptance criteria | Dep. |
|---|---|---|---|
| ST-01 | See my enrolment honestly | Locks shown with exact states and channel/trust; `PENDING`, `UNCONFIRMED` and `DISPUTED` displayed as such with plain-language text; days left to deadline | TL |
| ST-02 | Confirm with an OTP | OTP appears in the Dev SMS Inbox (registry mobile when available); correct OTP → Lock 3 `CONFIRMED`; if Lock 2 passed → `VERIFIED`; OTP stored hashed | TL |
| ST-03 | Be protected from OTP abuse | 6th wrong attempt → `429 OTP_ATTEMPTS_EXCEEDED`; 4th send within an hour → `429 OTP_RESEND_LIMIT`; expired OTP → `410 OTP_EXPIRED`; new OTP invalidates the previous | TL |
| ST-04 | Say "this is not me" | Dispute → `DISPUTED`, case `STUDENT_DISPUTE` opened, AICTE notified, institution sees the status but not my reason text | TL, INT |
| ST-05 | Let my guardian act for me (minor) | Guardian link verified via guardian OTP; in guardian mode `gd.meera` can confirm/consent for the linked minor only; accessing an unlinked student → `404` | STU |
| ST-06 | Control who can verify me | Verifier request without consent → neutral + consent request in my wallet; grant → subsequent request answered; withdraw → neutral again immediately; past events remain in access log | VER |
| ST-07 | See who looked at my record | Access log lists each verification (org, purpose, fields, result, time) and staff elevations as "AICTE case access" with case reference and role | VER, ACL |
| ST-08 | Share only what I choose | Share link has fields, purpose, expiry ≤ 30 days; token shown once; revoked link → neutral; use count increments | CRD |
| ST-09 | Ask for a correction | Request stores the proposed value encrypted; status visible; students cannot edit official fields directly (`PATCH` not offered) | STU |
| ST-10 | Exercise data rights | ACCESS → JSON export of own data only; ERASURE outcomes per DEC-15; GRIEVANCE visible with the officer path | STU |
| ST-11 | Verify that my record was committed | Inclusion proof verifies in the browser against the countersigned root; editing the enrolment makes verification fail | AUD |
| ST-12 | Use Hindi | Language toggle switches student-portal labels, SMS templates and error messages; preference persists | UX |
| ST-13 | Refuse APAAR consent without penalty | `REFUSED` recorded; enrolment still counted; no service denied; copy states this plainly | TL |

### 12.6 Verifier (VU), client (CL), public (PUB)

| ID | Story | Acceptance criteria | Dep. |
|---|---|---|---|
| VU-01 | Get a signed yes or no | Consented valid credential → `ANSWER` with ES256 JWT (`aud`=org, `nonce` echoed, `exp = iat+300`); signature verifies against `/public/jwks` | VER |
| VU-02 | See only what my purpose allows | `vu.bank` and `vu.employer` get different fields for different purposes; requesting a disallowed field returns it omitted, not an error; `fields_disclosed` recorded; disclosure meter shown | VER |
| VU-03 | Not learn anything from failures | Unknown ref, no consent, withdrawn consent, revoked-with-no-consent → byte-identical NEUTRAL body; latency within 150 ms jitter band | VER |
| VU-04 | Be unable to replay | Answer used after 5 minutes is rejected by the verify helper; nonce mismatch rejected | VER |
| VU-05 | Be rate-limited | Beyond `hourly_limit` → `429` with `Retry-After`; > 30 unknown refs in 10 minutes raises an alert | VER |
| CL-01 | Integrate my ERP | `POST /auth/token` → bearer; `POST /claims` with idempotency; wrong scope → `403`; token expires in 15 min | AUTH |
| CL-02 | Receive events | Webhook signature verifiable with the documented algorithm; timestamp skew > 5 min rejected by the sample receiver | INTG |
| CL-03 | Push a liveness feed | `POST /feeds/liveness` with `feeds:write` → `202`; APAAR IDs are hashed on receipt, raw never stored; without scope `403` | INT |
| PUB-01 | Check a certificate | `/v/<ref>` shows `VALID/REVOKED/SUPERSEDED` and issuer/kid with the boundary statement; unknown refs show `UNVERIFIABLE`; no personal data | CRD |
| PUB-02 | Register an institution | Onboarding form validates `aicte_id` against the master; rate-limited; status lookup | ONB |

### 12.7 System behaviours

| ID | Behaviour | Acceptance criteria |
|---|---|---|
| SYS-01 | Outbox projector | Every committed business transaction has an outbox row; projector updates `agg.*` within 10 s; restarting the worker mid-batch causes no double counting (idempotent via `last_outbox_id`) |
| SYS-02 | Confirmation deadlines | Clock advanced past day 30 + run job → unconfirmed `PENDING` become `UNCONFIRMED`; seats unchanged; Trust Index recomputed; reminders sent on days 7 and 21 once each |
| SYS-03 | Revalidation | After registry recovery, all `PENDING_VERIFICATION` resolve within 2 scheduler runs; failures back off with jitter; items past deadline are flagged |
| SYS-04 | Session security | Revoked/idle/deactivated session → `401 SESSION_EXPIRED`; `alg=none` or tampered token → `401 TOKEN_INVALID` |
| SYS-05 | Audit chain | Sealer assigns sequential seq and hashes in id order; no gaps; checkpoint every 1,000 entries/hour; integrity check passes after restart |
| SYS-06 | Reset | `sentinel db reseed` returns to the seeded state; `verify-seed` passes; all sessions invalid; mock-services reset |
| SYS-07 | Tenant isolation | With `app.institution_id` = A, SQL against any tenant table returns zero rows of institution B even for a deliberately broken query without a WHERE |
| SYS-08 | Fail-closed keys | `KeyProvider` forced to fail → issuing/commitment/decrypt endpoints return `503 KEY_UNAVAILABLE`; HMAC-only claim continues |
| SYS-09 | Idempotent jobs | Killing the worker mid-chunk and restarting: the lease expires, the chunk re-runs, no duplicate enrolments or ledger entries |
| SYS-10 | Lockout | 5 failed logins lock for 5 minutes; success after lock expiry; attempts are audited |
| SYS-11 | Time rules | Cut-offs evaluated server-side in UTC from IST config; a claim at 23:59:59 IST on the close date is on time, 00:00:01 is late |
| SYS-12 | Webhook failure | Receiver returns 500 → retries at 1 m, 5 m, 30 m, 2 h, 6 h …; after 24 h `DEAD_LETTERED`; no PII in payloads |

---

## 13. Edge Cases, Failure Scenarios and Expected Behaviour

### 13.1 Claim and seat edge cases

| Condition | Expected behaviour |
|---|---|
| Two claims for the same ID at two institutions at the same millisecond | Exactly one `201`; the other `409 CLAIM_CONFLICT`; neither deadlocks; losing transaction fully rolled back (no ledger/event rows) |
| Claim when bucket is at 99/100 and two requests race for the last seat | One succeeds, other `SEAT_LIMIT`; no negative or over-cap values |
| Cap lowered below `occupied` by a new intake version | Accepted; Warning finding raised; further claims `SEAT_LIMIT`; no eviction; ledger `CAP_CHANGE` |
| Exception approved then expires with seats already used | `approved_extra` reduces only by unused seats; `occupied` may exceed `cap` until withdrawals; Warning finding; no CHECK violation because expiry reduces only unused extra |
| Withdrawal and a new claim for the same student at another institution race | Claim succeeds only after the withdrawal commits (index excludes `WITHDRAWN`); otherwise `CONFLICT`; client may retry |
| Same student claimed in 2025-26 and 2026-27 | Allowed (per-cycle index); overlap warning + review candidate |
| Claim for a programme not approved for the year or institution suspended | `422 PROGRAMME_NOT_APPROVED` / `403 INSTITUTION_NOT_ACTIVE`; attempt logged |
| `admit_now` with admission date after the window or after the counselling cut-off | Claim succeeds, admit fails with `FLD-004/005` fix; status stays `CLAIMED` |
| Claim body contains unknown field or an Aadhaar-like value in `name`/`phone` | `422 VALIDATION_ERROR` (`UNKNOWN_FIELD` / `AADHAAR_SUSPECTED`); value is not logged |
| Request sent twice because the client timed out after the server committed | Replay returns the stored `201`; no second row |
| Server crashes after commit before the idempotent response is stored | Next retry hits the unique constraints: returns `DUPLICATE_SAME_INSTITUTION` with the original claim id (safe) |
| Statement or lock timeout in claim transaction | Transaction rolled back; `503 SERVICE_UNAVAILABLE retryable=true`; attempt logged as error; no partial state |

### 13.2 Identity and registry

| Condition | Expected behaviour |
|---|---|
| Registry returns 404 for a well-formed ID | `INVALID` with suggestion to use a provisional ID if APAAR not generated |
| Registry returns `DEACTIVATED/SUSPENDED` | `PENDING_FIX` with message to contact the registry owner; no auto-merge |
| Two APAAR IDs for one person | Not detected in the prototype (needs the real registry); documented as Deferred (PQ-05) |
| Registry slow (3 s) | Times out at 2 s; claim `PENDING_VERIFICATION`; breaker counts a failure |
| Breaker half-open probe fails | Stays open another 30 s; a successful probe closes it |
| Name has initials, honorifics, `Md/Mohd`, Devanagari form | Normalisation yields ≥ 0.92 for the same person (tested with seeded variants) |
| Day ≤ 12 and day-month swapped, DOB otherwise identical | `DAY_MONTH_SWAP` fix; if day > 12 the swap isn't possible and falls to `MISMATCH` |
| Provisional upgrade to an ID already exclusively claimed elsewhere | `409 CLAIM_CONFLICT`; provisional enrolment unchanged |
| Foreign student with passport number | Hashed + last four only; shown masked (`••••1234`); no raw value after the request |

### 13.3 Upload and ingestion

| Condition | Expected behaviour |
|---|---|
| Wrong extension or magic bytes mismatch | `422 FILE_REJECTED reason=BAD_TYPE`; job `FAILED` |
| XLSX with macros, zip-bomb ratio, > 200 entries | `FILE_REJECTED` with reason; nothing parsed |
| File over 25 MB or over `MAX_UPLOAD_ROWS` | `413`/`422 ROW_LIMIT_EXCEEDED` with limits in the message |
| Cell starting with `=`, `+`, `-`, `@` | Warning finding; value prefixed `'`; never executed; exports re-neutralise |
| Duplicate APAAR within file | First occurrence continues; later rows `REJECTED` (`IDN-004`) |
| Row with same `row_key` and identical content already committed | `NOOP_DUPLICATE` |
| Row with same `row_key` but changed data after commit | `CHANGED`; commit applies a `CORRECT` event or rejects with reason if a locked field changed |
| Worker killed during parsing/commit | Lease expires; chunk retried alone; no duplicate claims |
| Registry down during dry-run | Rows get `PENDING_VERIFICATION` diagnosis warnings, not rejection |
| Window closed at commit time | Rows needing `FLD-004` fail as `COMMIT_FAILED(WINDOW_CLOSED)` unless extension exists |
| Uploader tries to commit own job | `403 SEPARATION_OF_DUTIES` |
| Two commits requested for the same job | Second `409 INVALID_STATE_TRANSITION` |
| Job cancelled mid-validation | Staged rows purged by retention; no claims created |

### 13.4 Triple-Lock and student flows

| Condition | Expected behaviour |
|---|---|
| OTP SMS fails (mock `fail_rate`) | Retries with backoff, falls back to e-mail then in-app; institution sees delivery status |
| Student has no phone | Assisted confirmation by IA (reason, witness); `LOW` trust; counted |
| Minor with no verified guardian link | Confirmation request waits; IA sees "guardian link required"; deadline still applies |
| Confirm after deadline (`UNCONFIRMED`) | Allowed; state → `VERIFIED` if Lock 2 passed; event recorded; Trust Index recomputed |
| Dispute after `VERIFIED` | Allowed; credential revoked (`DISPUTED`) pending case |
| Student confirms but Lock 2 `FAILED` | Overall stays `PENDING_FIX` until fixed |
| Guardian tries to act for a non-linked student | `404` |
| Consent withdrawn between request and answer | Neutral response; no data returned |
| Share link used after expiry/revocation | Neutral response |

### 13.5 Matching, intelligence and cases

| Condition | Expected behaviour |
|---|---|
| Block with > 50 members | One `REVIEW` item flagged `BLOCK_TOO_LARGE`; no pairwise explosion |
| Twins (same DOB, parents) | Name Dice low enough to remain below 0.80 or flagged with `TWIN_PATTERN` hint; must not be `CONFIRMED_DUP` automatically |
| Reviewer dismisses a pair | Never re-flagged; label stored |
| Fewer than 30 institutions with history | Isolation Forest skipped; robust z-scores only; alert text says "model not used" |
| Peer group < 5 institutions | Falls back to national peers by type; noted in explanation |
| Trust Index inputs empty | Components use defined defaults (full points) and say "no data" |
| Elevation grant expires mid-session | Next reveal `403 GRANT_EXPIRED`; UI prompts to re-request |
| Appeal after 15 days | `409 WINDOW_CLOSED` |
| Case response overdue | Case moves to `UNDER_REVIEW`; flagged overdue; no automatic penalty |

### 13.6 Credentials, verification, audit, platform

| Condition | Expected behaviour |
|---|---|
| Status list cache stale after revocation | Verify-api reads the live DB bit for answers; public status page may lag ≤ 60 s and shows `checked_at` |
| Withdrawn student's credential | Auto-revoked `ENROLMENT_ENDED`; verifier gets NEUTRAL/`NO` per purpose rules |
| Signing key unavailable | `503 KEY_UNAVAILABLE`; nothing partially signed |
| Audit sealer behind | Entries show unsealed; lag indicator; integrity check reports "N unsealed" without failing |
| Hash-chain break | `BROKEN`, first bad seq; Operations page raises a persistent alert |
| WORM copy missing | Integrity check `WORM_MISMATCH` |
| Clock offset set backwards | Rejected unless `DEMO_MODE`; audit sealer uses real time for chain ordering, business logic uses `Clock` |
| Postgres restart | API reconnects; in-flight claims retry by idempotency key; workers resume; leases expire cleanly |
| Redis or Grafana absent | N/A by design; no code path references them (`grep` check in CI) |
| mock-services down | Registry → `PENDING_VERIFICATION`; SMS → notification retries/fallback; Operations page shows adapter DOWN; core stays up |
| API receives clock-skewed webhook receiver | Receiver samples reject > 5 min skew; sender unaffected |
| User opens a record from another tenant by guessing an id | `404`; denial audited |
| Browser offline / API down | Banner "Cannot reach the server"; queries retry with backoff; forms keep unsaved input |
| Very large result sets | Never unbounded: `limit` ≤ 500, report job for extracts |
| Locale `hi` message missing | Falls back to English |

---

## 14. Testing Strategy

### 14.1 Principles and tooling
Every requirement (`REQ-…`) and story has at least one test tagged `@req("REQ-GATE-04")` / `@story("IA-02")`; CI fails if a requirement has no tagged test (traceability report in Appendix B is generated from the tags). Tests run against a real PostgreSQL (CI service container / local Docker) — never SQLite, because correctness depends on row locks, partial indexes, RLS and grants.

| Layer | Tools | Scope |
|---|---|---|
| Unit | `pytest`, `hypothesis`; `vitest` + Testing Library (web) | Pure functions: hygiene/checksum, normalisation, diagnosis, rule evaluation per kind, Trust Index formulas, Bloom/Dice, Merkle, SD-JWT/status-list, rate limiter, idempotency hashing, Clock |
| Integration | `pytest` + Postgres | Repositories, claim transaction, RLS, grants, constraints, outbox/projector, job queue leases, scheduler leader lock, adapters against mock-services |
| Workflow | `pytest` with API client (`httpx`) in `WORKER_MODE=inline` | Each state machine (§5.4) and cross-module flow (claim → verify → credential → verify) with a fake Clock |
| Authorization | Generated from the §4.3 registry | Route × role × tenant matrix; cross-tenant ID guessing; elevation/grant rules; separation of duties |
| Contract | `schemathesis` against `/openapi.json`; OpenAPI diff vs the committed baseline | Response schemas, problem+json shape, no undeclared routes |
| Property | `hypothesis` stateful tests | Ledger invariants: any sequence of claim/withdraw/exception/cap-change keeps `occupied = Σ DEBIT − Σ RELEASE`, `0 ≤ occupied ≤ cap + extra`, one live exclusive claim per identity per cycle |
| Concurrency | `pytest-asyncio` + `httpx` (and `scripts/load/claim_hammer.py`) | SC-02, SC-03; worker kill/restart (SYS-09) |
| End-to-end | Playwright (Chromium, + WebKit smoke) | The 15 scenarios S1–S15 through the real UI against a seeded `minimal`/`standard` stack |
| Accessibility | `axe-core` via Playwright | 0 critical/serious violations on login, dashboards, claim form, student portal, verifier result |
| Security | `bandit`, `semgrep`, `pip-audit`, `gitleaks`, `npm audit`, OWASP ZAP baseline (optional, staging-style) | Static/dependency/secret scans; log scan for Aadhaar-like and PII patterns |
| Load (light) | `claim_hammer.py` (asyncio) or `k6` | Claim concurrency, dashboard queries on `full`, upload of 20,000 rows |
| Seed | `pytest` calling `verify-seed` | §11.10 invariants on `minimal` in CI and `standard` nightly |

### 14.2 Mandatory test suites (with source traceability)

| Suite | Must prove | Source |
|---|---|---|
| T-GATE | Outcome table §5.1.2 row by row; idempotency; exclusivity; no oversell (SC-02, SC-03, SC-04) | F3, FR-14…19, §6.2 |
| T-IDN | Hygiene, scientific notation, Aadhaar guard, normalisation variants, DOB patterns, provisional IDs, upgrade conflicts, circuit breaker | F4, FR-09…13 |
| T-RUL | Each rule kind; each seeded rule v1; three call sites; immutability; draft ≠ publish; rollback; simulator determinism | F6, F7 |
| T-ING | File safety, formula neutralisation, S3 oracle (251/17/32/0), partial commit, idempotent re-upload, lease retry | F1 |
| T-SUB | Submission machine, windows, late flag, extension, SoD CHECKs, commitment lock | F8 |
| T-TL | Locks, OTP rules, guardian, assisted, dispute, deadline job, reminders | F5 |
| T-DUP | Labelled pairs → precision/recall thresholds; hard negatives; timeline classes; blocks; transfers/orphans | F9 |
| T-INT | Trust Index recompute; robust z; Isolation Forest gate; graph clusters; liveness (mock); cases; appeals; no-penalty assertion | F10 |
| T-DASH | Projection correctness vs recomputation; `as_of`; suppression; filters/sort/pagination (keyset stability under inserts) | F11 |
| T-CRD/VER | Credential verify; tamper; revoke; neutral identical bodies; replay; consent; scope; rate limit; suspension | F13, F14 |
| T-STU | Wallet, consent ledger, share links, corrections, DSR outcomes, guardian | F15 |
| T-AUD | Chain, checkpoints, tamper detection, WORM compare, Merkle root change, inclusion proof | F16 |
| T-AUTHZ | Matrix; step-up; lockout; JWT alg; session revocation; production guard | F17 |
| T-RLS | Forced RLS on all tenant tables; broken-query test; grants on history tables | F17, §5.4 |
| T-INTG | Problem+json, headers, webhooks signatures/retries/dead-letter, adapters timeouts, SSRF rejection | F18 |
| T-OPS | Job queue (leases, retries, dead), scheduler singleton, projector idempotency, retention | §8.11 |
| T-SEED | `verify-seed` all invariants; reseed idempotency; account logins | Brief §4 |
| T-PRIV | No PII outside vault (schema lint), log redaction, no Aadhaar-like anywhere, small-cell suppression | §7.6 |

### 14.3 End-to-end test list
One Playwright spec per scenario S1–S15 (§11.7), each asserting the listed outcomes through the UI **and** the database/API. A global smoke spec signs in as every demo account (SC-01) and checks that each portal's landing page renders data with `as_of` and the demo banner.

### 14.4 Concurrency and resilience tests
(a) 1,000 claims / 50 concurrent / cap 100. (b) Same identity from two institutions ×200 pairs. (c) Kill the worker mid-chunk and mid-outbox batch; restart; assert no duplicates and no lost events. (d) Registry `DOWN`/`SLOW`/`FLAKY`: claims pending, breaker states, recovery. (e) Postgres restart during load: clients retry via idempotency keys. (f) `mock-services` down: core keeps serving.

### 14.5 Test data
Unit tests use builders; integration/workflow tests use the `minimal` seed loaded once per session with transaction-per-test isolation where possible, or per-test schemas for concurrency tests; E2E uses `standard` (nightly) or `minimal` (CI). A fake Clock fixture controls time.

### 14.6 Evaluation harness (`sentinel eval`) [SRC §8.13]
Reads `seed.ground_truth` and the application's outputs, then reports: duplicate **precision and recall** (targets: recall ≥ 0.90 and precision ≥ 0.85 on the 38 injected true duplicates against 70 hard negatives — **proposed targets**, to be tuned); **mismatch auto-diagnosis rate** (share of injected identity mismatches resolved or correctly diagnosed; initial target **≥ 78%** per source, to be re-baselined); **intake violations caught** (100% of over-limit attempts blocked); **tamper detection** (100% of injected audit/enrolment edits detected); **fields disclosed per verification** (mean ≤ 3 of ~40); **ghost ring and stolen identities surfaced in the top 5 alerts**; and latency percentiles at `minimal/standard/full`. The harness prints a table and exits non-zero if a hard target is missed (hard: intake, tamper, ghost-ring surfacing; soft: precision/recall/auto-diagnosis, which warn).

### 14.7 Quality gates

| Gate | Check |
|---|---|
| Pull request | `ruff`, `mypy --strict` on core modules, `eslint`, `tsc --noEmit`, unit + property tests, `import-linter`, `bandit`, `semgrep`, `gitleaks`, OpenAPI diff, migration lint (no PII columns outside vault, no destructive change without contract phase), RLS self-check, requirement-tag coverage |
| Main | Integration + workflow + authorization + contract (`schemathesis`) tests, seed `minimal` verify, Playwright smoke |
| Nightly | Seed `standard` verify, full E2E S1–S15, axe, concurrency suite, `sentinel eval`, `pip-audit` |
| Release (demo) | `docker compose --profile app up` from a clean checkout passes the smoke spec; README quick-start followed literally by a clean-machine run (SC-01) |

Coverage targets: ≥ 80% line coverage on `claims`, `rules`, `identity`, `audit`, `trust`, `credentials`; ≥ 70% overall backend; web: all shared components and every portal route covered by at least one test.

---

## 15. Local Development Setup, Environment Variables, Migrations, Seeding and Deployment

### 15.1 Prerequisites
Docker Desktop (PostgreSQL 16 container), **Python 3.12** (3.11+ acceptable) with `uv` or `pip`, **Node.js 20+**, Git. Windows 11 (PowerShell), macOS or Linux. 8 GB RAM minimum, 16 GB recommended for `full`. Ports 5432, 8000, 8001, 8002, 5173 free.

### 15.2 Quick start (target ≤ 15 minutes)

```bash
git clone <repo> apaar-sentinel && cd apaar-sentinel
cp .env.example .env            # setup also generates secrets if left blank
docker compose up -d db         # PostgreSQL 16
cd backend && uv sync           # or: python -m venv .venv && pip install -e ".[dev]"
cd ../web && npm ci && cd ..
python -m app.cli setup         # migrate + seed (standard) + verify-seed + print URLs/accounts
python -m app.cli dev           # starts core-api :8000, verify-api :8001, mock-services :8002, worker, web :5173
```

PowerShell users may use `scripts/setup.ps1` and `scripts/dev.ps1`, which wrap the same commands. After `dev` starts, open `http://localhost:5173`, choose **Sign in as → Institution admin (KIT)** (password `1234`), and follow `docs/demo-script.md` (generated by the seed). Run the checks:

```bash
python -m app.cli db verify-seed
pytest -q                        # backend suites
cd web && npm test && npx playwright test    # frontend + e2e
```

### 15.3 CLI reference (`python -m app.cli …` / console script `sentinel`)

| Command | Purpose |
|---|---|
| `setup` | Idempotent first-run: secrets → DB up → migrate → seed → verify |
| `dev` / `api` / `verify-api` / `worker` / `mock` | Start all or individual processes |
| `db migrate` / `db seed` / `db reseed` / `db reset` / `db verify-seed` | See §11.9 |
| `demo purge-accounts` / `demo tamper-audit` / `demo untamper` / `demo tamper-enrolment` | Demo and pre-production utilities |
| `eval` | Evaluation harness (§14.6) |
| `rls-check` | Fails if any tenant table lacks forced RLS or any history table has UPDATE/DELETE grants |
| `backup` / `restore <file>` | `pg_dump`/`pg_restore` wrappers followed by the integrity check |
| `openapi:export` | Writes the OpenAPI spec for contract tests and the generated TypeScript client |

### 15.4 Environment variables (`.env`)

| Variable | Default (dev) | Description |
|---|---|---|
| `APP_ENV` | `local` | `local` · `demo` · `production` (production triggers the guard of REQ-AUTH-07) |
| `DEMO_MODE` | `true` | Enables demo accounts endpoint, Demo Tools, `/docs`, dev SMS inbox |
| `DATABASE_URL` | `postgresql+psycopg://app_runtime:…@localhost:5432/sentinel` | Runtime connection (RLS applies) |
| `DATABASE_URL_MIGRATOR` | `postgresql+psycopg://app_migrator:…@localhost:5432/sentinel` | Migrations, seed, tamper demos |
| `JWT_SECRET` | generated | ≥ 32 random bytes |
| `JWT_TTL_MINUTES` / `SESSION_IDLE_MINUTES` | `60` / `30` | |
| `MASTER_KEY` | generated | 32-byte base64; wraps data keys and private signing keys |
| `IDENTITY_HMAC_KEY` / `IDENTITY_HMAC_KEY_VERSION` | generated / `1` | Identity keys, blind-index tokens, Bloom salts |
| `SEED_PROFILE` / `SEED_RNG` / `SEED_ANCHOR_DATE` | `standard` / `20261026` / today | |
| `SEED_DEMO_PASSWORD` | `1234` | **Demo only** |
| `STORAGE_DIR` / `WORM_DIR` / `KEYS_DIR` | `./data/objects` / `./data/worm` / `./data/keys` | Local ObjectStore, simulated WORM sink, wrapped keys |
| `MOCK_SERVICES_URL` | `http://localhost:8002` | Registry, SMS, master, feed |
| `REGISTRY_TIMEOUT_MS` / `REGISTRY_BREAKER_THRESHOLD` / `REGISTRY_BREAKER_RESET_S` | `2000` / `5` / `30` | |
| `SMS_FIXED_OTP` | *(empty)* | If set (e.g. `123456`) all OTPs equal it — demo convenience, never in production |
| `WORKER_MODE` | `process` | `process` · `inline` (tests) |
| `JOB_QUEUE` / `RATE_LIMITER` / `EVENT_BUS` | `postgres` / `memory` / `polling` | Seams for Deferred implementations |
| `MAX_UPLOAD_MB` / `MAX_UPLOAD_ROWS` | `25` / `20000` | |
| `ALLOW_PRIVATE_WEBHOOK_URLS` | `true` (demo) | SSRF override for the local sink |
| `PUBLIC_VERIFY_URL` | `http://localhost:8001` | Base URL printed in QR codes |
| `CORS_ORIGINS` | `http://localhost:5173` | |
| `LOG_LEVEL` | `INFO` | JSON logs with redaction |
| `VITE_API_URL` / `VITE_VERIFY_URL` | `http://localhost:8000/api/v1` / `http://localhost:8001` | Web build-time settings |

### 15.5 Docker Compose

`docker-compose.yml` defines service `db` (always) and, under profile `app`, `api`, `verify`, `worker`, `mock`, `web` (nginx serving the built SPA with `/api` proxied). Volumes: `pgdata`, `appdata` (objects, worm, keys). A `migrate` one-shot service runs `db migrate` and `db seed` when the volume is empty. `docker compose --profile app up --build` starts the entire stack; `docker compose down -v` removes all data.

### 15.6 Migrations
Alembic, one linear history; **expand-then-contract**: a release adds nullable/new objects first, a later one removes old ones. Every migration is idempotent-safe on fresh and upgraded DBs; the first migration creates schemas, roles, grants, RLS policies and the history-table triggers; a migration lint enforces conventions (no PII columns outside `pii`/`mockext`, enums as CHECKs, `timestamptz`). Rollback of application code = redeploy previous image; DB rollback = restore from `backup`.

### 15.7 Deployment of the prototype
* **Developer machine:** §15.2.
* **Shared demo host (single VM):** `docker compose --profile app up -d` behind a reverse proxy with TLS (Caddy/nginx); set `APP_ENV=demo`, keep `DEMO_MODE=true`, generate fresh secrets, restrict access (VPN/IP allow-list) because credentials are `1234`; schedule `sentinel db reseed` nightly if desired; keep mock-services private.
* **Not supported by this PRD:** internet-facing or real-data deployment. Production deployment requires the Future items in Appendix A (real auth + MFA, KMS/HSM, object-lock WORM, managed DB with PITR, WAF/CDN, monitoring, penetration test, legal review).

### 15.8 Operations runbooks to include (`docs/runbooks/`)
Registry outage and recovery · queue backlog / dead jobs · rule rollback · restore from backup + integrity check · tamper detection response · mistaken fraud flag and appeal · key compromise (documentation only; rotation workflow is Deferred) · reset demo data · rotating local secrets (dev only) · breach-response outline (documentation only).

### 15.9 Scale path to production (informational, from source §8.14)

| Stage | Topology (source) | This PRD |
|---|---|---|
| Prototype and hackathon demo | Supabase local stack or dev project, FastAPI, Celery, Redis, mock adapters, 50,000 synthetic students over ~200 institutions | **This document**, with Redis/Celery/Grafana/Supabase replaced per §2.6 and `full` profile = source scale |
| Pilot | Production Supabase with PITR, managed containers in two zones, managed Redis, KMS, WAF and CDN, observability stack, tenant-isolation tests | Future (Appendix A) |
| National rollout | Managed Kubernetes with autoscaling, separate worker pools, partition tuning, DR region, deadline-burst tests, HSM for AICTE keys | Future |

Measured triggers to add components (source): read replica when reporting reads degrade claim latency · Citus evaluation only if write saturation persists after partitioning · RabbitMQ when Redis broker loss becomes operational pain (Kafka only for external replayable streams) · Kubernetes at national rollout · Parquet/DuckDB then a warehouse when historical queries exceed what views can serve · OpenSearch for free-text over very large case notes · microservices only when a module needs independent release, isolation or scaling (order: matching, reports, credentials) · active-active region not planned.

---

## 16. Phased Implementation Roadmap

Sequencing follows source C-12 (claim gate and ledger first; trust before heavy intelligence; credentials and matching before intelligence; proof and hardening last). Each phase ends with **exit criteria that are automated tests**; a phase starts only when its dependencies' exit criteria are green. The estimate is indicative for one AI coding agent working iteratively with human review (phases may overlap where noted).

| Phase | Scope | Depends on | Exit criteria (testable) |
|---|---|---|---|
| **P0 Foundations** | Repo, Compose, `.env`, CLI skeleton, migrations with schemas/roles/grants/RLS/triggers, config, Clock, logging with redaction, errors (problem+json), UUIDv7, crypto wrappers, `KeyProvider`, `ObjectStore`, job queue + worker + scheduler skeleton, auth (login, sessions, JWT, lockout, step-up), permission registry + route guard, audit hook (insert), outbox writer, CI pipeline, import-linter, `rls-check` | – | `setup` creates empty migrated DB; login for a seeded admin works; route without permission fails startup; `rls-check` and grants test pass; CI green |
| **P1 Gate and ledger** | Reference data + mock AICTE master sync; institutions/programmes/buckets; mock-services (registry, SMS); identity hygiene/format/registry adapter (basic); rules engine (claim-path kinds, seeded v1 subset); claim transaction; seat buckets/ledger/exceptions; enrolments/events; withdraw/admit; idempotency; PII vault; blind index; audit sealer + checkpoints; outbox projector + `agg.seat_utilisation`; institution claim UI + seat tiles; minimal AICTE utilisation table; seed `minimal` v0 | P0 | T-GATE complete: SC-02, SC-03, SC-04 pass; IA-01…IA-05, IA-10…IA-12 pass; ledger property tests pass; dashboard updates ≤ 10 s; audit chain verifies |
| **P2 Ingestion and submissions** | Template + client pre-validation; upload pipeline (safety checks, chunks, dry-run, diff, commit); idempotent row keys; findings workflow (ack/waive); all rule kinds and call sites; windows, extensions, submission machine, readiness check; ERP client credentials + `mock_erp_client.py`; webhooks with retries and sink; `full` rule set v1; notification service (templates, retries, fallbacks) | P1 | S3 oracle exact (251/17/32/0); SoD tests; IO-01…IO-05, IA-13, IA-14, IA-19; webhook retry/dead-letter tests |
| **P3 Identity and Triple-Lock** | Normalisation (Indic), diagnosis, fix-it lists, provisional IDs and upgrade, revalidation job and breaker; Triple-Lock (OTP, guardian, assisted, dispute, deadline, reminders); consent ledger and notices; student portal (locks, OTP, dispute, guardian mode, English/Hindi); Dev SMS Inbox | P1, P2 | S4, S5, S6, S11 pass; IA-06…IA-09; ST-01…ST-05, ST-12, ST-13; SYS-02, SYS-03 |
| **P4 Matching, credentials and verification** | Match features, blocking, scoring, timeline classes, review queue, transfers/orphan leads; credential issuance (SD-JWT, attestation), status list, certificate PDF/QR; verify-api, verifier portal, public status page; consent requests, share links, access log; key registry endpoints | P1, P3 | S2, S7, S8 pass; T-DUP precision/recall thresholds; SC-10; VU-01…VU-05, ST-06…ST-08, AR-04, AR-10, IA-16, IA-20 |
| **P5 Intelligence and operations** | Trust Index, anomaly layers A–D (robust z, Isolation Forest, Louvain, liveness on mock feed), alerts, cases, appeals, elevation/grants, full AICTE dashboards (heat table/treemap, compliance, scorecards, trends), AICTE portal screens, Operations page, Demo Tools, regional scoping | P3, P4 | S9, S10 pass; ghost ring and stolen identities rank in top 5 alerts; AR-01, AR-02, AR-05, AR-06, AR-11, AR-15, AS-03, AS-04; SC-08, SC-12 |
| **P6 Proof and reporting** | Cohort commitments and countersign (Locked), inclusion proofs, tamper CLI + integrity check UI, policy simulator + rule publish/rollback, reports (CSV/XLSX/PDF + QR), DSR/correction/erasure flows, retention jobs, onboarding workflow, audit explorer | P2, P4, P5 | S12, S13, S14, S15 pass; SC-09; AR-07…AR-09, AR-12…AR-16, AS-01, AS-02, ST-09…ST-11, IA-15 |
| **P7 Seed completion, hardening, polish** | `standard` and `full` profiles with all distributions and injections; `verify-seed` complete; `eval` harness; load/concurrency suites; axe fixes; empty/loading/error states audit; README, demo script, runbooks; Compose `app` profile; production guard tests; log/Aadhaar scans | all | SC-01…SC-14 pass; nightly suite green; clean-machine run ≤ 15 min; `sentinel eval` hard targets met |

**Parallelism guidance:** after P1, P2 and P3 can proceed in parallel by separate agents (disjoint modules: `ingest` vs `trust/identity/consent`); P4's credentials/verification and matching are independent of each other; P5 needs P3+P4 outputs; P6 sub-items (reports, simulator, commitments) are independent. The seed generator grows every phase (each phase adds its entities and its `verify-seed` invariants); P7 completes volume and injections.

**Definition of done (any feature):** requirement implemented; API contract and permission registered; audit + outbox written; RLS/authz tests; unit + workflow tests tagged; UI with loading/empty/error states and `as_of`/`SIMULATED` labels where relevant; seed data covers it; runbook/README updated; deferred seams respected (no Redis/Grafana references).

---

## Appendix A. Feature Classification and Simplification Register

### A.1 Feature classification (every major feature)

Legend: **MVP** = built; **Deferred** = excluded from this implementation, with a seam; **Future** = production-scaling enhancement. "MVP*" = built in a simplified or simulated form (details in A.2).

| # | Feature / capability | Source | Class | Prototype realisation |
|---|---|---|---|---|
| 1 | Claim-then-admit gate, atomic exclusive claim, seat-bucket row lock, CHECKs | F3, §5.4 | **MVP** | PostgreSQL partial unique index + row lock + CHECK (REQ-GATE) |
| 2 | Seat ledger of typed buckets, 90%/100% thresholds, exceptions, dated intake versions | F3, C-06/07 | **MVP** | Reference data + ledger + exception workflow |
| 3 | Event-sourced enrolments, withdrawal/transfer events | F8 | **MVP** | `enrolment_events` append-only |
| 4 | Idempotency keys and stable row keys | F18, F1 | **MVP** | `iam.idempotency_keys`, `row_key` |
| 5 | APAAR format/checksum validation (pluggable) | F4 | **MVP*** | Mock format DEC-17 |
| 6 | Registry validation adapter, timeout, circuit breaker, `PENDING_VERIFICATION` | F4, §3.5 | **MVP*** | HTTP adapter → mock registry [SIM] |
| 7 | Indic-aware name/DOB diagnosis, fix-it lists | F4 | **MVP** | RapidFuzz, jellyfish, indic-transliteration |
| 8 | Provisional IDs (NOGEN, CONSENT_REFUSED, FOREIGN) and upgrade | F4, C-08 | **MVP** | `provisional_ids` |
| 9 | Periodic revalidation | F4 | **MVP** | Scheduler job |
| 10 | Two-APAAR-ID detection flagged to registry owner | F4 | **Deferred** | Needs the real registry |
| 11 | Triple-Lock (institution, registry, student) with honest states | F5 | **MVP** | OTP via mock SMS [SIM], guardian, assisted, dispute, deadline |
| 12 | DigiLocker confirmation channel | F5 | **Deferred** | Enum value + stub only |
| 13 | Rules as data, typed kinds, severities, versioned immutable rule sets | F6 | **MVP** | 9 of 10 kinds |
| 14 | `RATIO_THRESHOLD` (student-faculty ratio) | F6 | **Deferred** | Needs faculty feed |
| 15 | Findings (evidence, fix, ack, waive) | F6 | **MVP** | |
| 16 | Aggregate reconciliation at submission | F6, FR-25 | **MVP** | `SUM_RECONCILES` |
| 17 | Policy simulator | F7 | **MVP** | Async replay on non-PII snapshot |
| 18 | Submission machine, windows, late flag, extensions | F8 | **MVP** | |
| 19 | Closure/merger/rename continuity (UI, workflow) | F8, FR-68 | **Deferred** | Data links stored |
| 20 | Bulk upload with pre-validation, dry-run, diff, commit by IA | F1 | **MVP*** | Single-PUT upload; simulated malware scan |
| 21 | TUS resumable chunked upload | F1 | **Deferred** | |
| 22 | REST API channel, OAuth2 client credentials, webhooks | F1, F18 | **MVP** | Mock ERP client |
| 23 | Scheduled ERP sync | F1 | **Deferred** | |
| 24 | Separate sandbox environment | F1, FR-06 | **Deferred** (dry-run provides equivalent) | |
| 25 | Institution onboarding, nodal-officer check | F2 | **MVP*** | Check simulated |
| 26 | Bulk onboarding; quarterly access recertification | F2, FR-70 | **Deferred** | |
| 27 | Exact duplicate blocking at claim | F9 | **MVP** | |
| 28 | Fuzzy duplicates: blocking keys, Bloom-filter Dice, timeline classes, review, labels | F9 | **MVP** | Static weights |
| 29 | Splink / Fellegi–Sunter trained weights | F9 | **Deferred** | |
| 30 | Transfer lifecycle and orphan leads | F9 | **MVP** | |
| 31 | Trust Index, anomaly layers A–C, graph clusters | F10 | **MVP** | Contribution tables instead of SHAP |
| 32 | Layer D academic liveness | F10 | **MVP*** | Mock feed [SIM], value claims withheld |
| 33 | SHAP explanations | F10 | **Deferred** | |
| 34 | Three-way match (allotment/enrolment/disbursement) | F10 | **Deferred** | Needs counselling/fee feeds |
| 35 | Case management, evidence pack, appeals, labels | F10 | **MVP** | |
| 36 | Aggregate-by-default access, case-gated elevation | F17 | **MVP** | |
| 37 | AICTE and institution dashboards with `as_of` | F11 | **MVP*** | Polling; heat table not map |
| 38 | Realtime Broadcast push | F11 | **Deferred** | |
| 39 | Reports CSV/XLSX/PDF with suppression and stamp | F12 | **MVP*** | reportlab |
| 40 | Report schedules; NBA/NAAC/AISHE/Parliament formats | F12, G-19 | **Deferred** | |
| 41 | Natural-language query and briefs | F12, AS-11 | **Deferred** | |
| 42 | SD-JWT VC credentials, AICTE attestation, status list, certificate with QR | F13 | **MVP*** | Enrolment Proof only; software keys |
| 43 | Year Completion / Degree / Transcript credentials | F13 | **Deferred** | |
| 44 | Privacy-preserving verification (tiers, scopes, signed answer, neutral response) | F14 | **MVP** | |
| 45 | Student wallet: consents, access log, share links, corrections, DSR, guardian | F15 | **MVP*** | Record-level erasure |
| 46 | Hash-chained audit, signed checkpoints | F16 | **MVP*** | WORM simulated |
| 47 | Merkle cohort commitments, countersign, inclusion proofs | F16 | **MVP** | |
| 48 | RBAC/ABAC, forced RLS, tenant boundary | F17 | **MVP** | |
| 49 | TOTP MFA; SSO; impossible-travel and new-device sign-in alerts | F17, §7.3 | **Deferred** / **Future** | Step-up simulated; session list and remote sign-out are built |
| 50 | KMS/HSM envelope encryption and signing | F17 | **MVP*** (software) / **Future** (real) | |
| 51 | Key rotation, two-approver, compromise handling | §7.7 | **Deferred** | Key-status registry published |
| 52 | Institution-held keys (BYOK) | §7.6 | **Future** | |
| 53 | Eight database roles | §7.4 | **MVP*** (two roles + code-level) / **Future** | |
| 54 | English and Hindi message catalogues | FR-66 | **MVP** | |
| 55 | Other regional languages; explicit low-bandwidth mode | FR-66 | **Deferred** | Code-splitting only |
| 56 | Redis (broker, cache, rate limits) and Celery/Beat | §3.3 | **Deferred** | Postgres queue, in-process limiter |
| 57 | Grafana, Prometheus, Loki, Tempo, Alertmanager, OpenTelemetry, Sentry | §4.1, FR-65 | **Deferred** | Logs + Operations page |
| 58 | Supabase Auth, Storage, Realtime, Supavisor | §3.7 | **Deferred** | Plain Postgres + local adapters |
| 59 | pg_partman partitioning, pg_cron | §5.6 | **Deferred** | Partition-ready schema |
| 60 | Read replica; Citus evaluation; RabbitMQ/Kafka; OpenSearch; Superset; Parquet/DuckDB | §4.1, §8.14 | **Future** | Triggers documented in source |
| 61 | CDN, WAF, load balancer, autoscaling | §3.3 | **Future** | |
| 62 | Kubernetes, Argo CD, Terraform, blue-green deploy | §4.1, §8.14 | **Future** | Compose only |
| 63 | DR region, PITR, quarterly restore drills, RPO/RTO proof | NFR | **Future** | `backup`/`restore` scripts |
| 64 | Staging/production separation; break-glass access | §3.8 | **Future** | |
| 65 | Container signing, SBOM, Trivy, DAST, annual pen test, disclosure process | §7.12 | **Future** (some scans optional in CI) | |
| 66 | DPDP legal review, privacy impact assessment, Bloom-encoding privacy review, breach drills | §7.11, R-08 | **Future** | Documentation only |
| 67 | Real integrations: APAAR registry, AICTE master, DLT SMS, ABC, NAD, DigiLocker, counselling, fee/scholarship, faculty | §3.5 | **Future** | Adapters + contract tests ready |
| 68 | Private set intersection; zero-knowledge proofs; intake forecasting | §1.7 | **Out of scope** (source) | |
| 69 | Aadhaar storage/printing/authentication; blockchain; native mobile apps; payment handling | §1.7 | **Out of scope** (source) | |

### A.2 Register of simplified, deferred, mocked and simulated capabilities

| Capability | Class | Reason | Impact on the prototype | How to implement properly later |
|---|---|---|---|---|
| **Redis / Celery / Beat** | Deferred | Brief defers Redis; Postgres already source-of-truth for jobs | One DB-polling worker (≈1 s latency), no broker throughput headroom, in-process rate limits not shared across processes | Add `CeleryJobQueue` and `RedisRateLimiter` behind existing interfaces; keep `ops.jobs` as truth; sweeper re-enqueues |
| **Grafana / Prometheus / Loki / Tempo / Alertmanager / OTel / Sentry** | Deferred | Brief defers Grafana; heavy to run locally | No time-series dashboards or alert routing; metrics in-memory only | Add `/metrics`, OTel exporter, Grafana dashboards from source §8.12; keep Operations page |
| **Supabase Auth/Storage/Realtime/Supavisor** | Deferred | Not needed for a local prototype | Local JWT auth; local files; polling | Swap adapters; verify JWT against Supabase keys; use Supavisor in transaction mode with `SET LOCAL` already used |
| **Realtime Broadcast** | Deferred | Supabase service | Dashboards refresh every 15 s, not instantly | `EventBus` broadcast of `dashboard-updated` with `as_of`; clients refetch |
| **MFA / step-up** | Deferred / Simulated | Brief wants simple auth | Password re-entry stands in; no second factor | Supabase TOTP, `aal2` claim check in `require_step_up()` |
| **SSO to government IdP** | Future | Mandate unknown (G-17) | None | OIDC provider integration |
| **HS256 local JWT** | Simulated | Simplicity | Shared secret; no external IdP | Asymmetric (ES256) tokens from an IdP; JWKS verification |
| **KMS / HSM** | Simulated | No cloud account | Keys wrapped by an env key on disk | `KmsKeyProvider`; non-exportable signing keys; HSM for AICTE keys |
| **WORM storage** | Simulated | No object-lock store | A local directory proves the flow, not immutability | Separate-account bucket with Object Lock; optional RFC 3161 timestamps |
| **Key rotation / compromise workflow** | Deferred | Needs two-person ceremony UI | One active key per purpose; status registry static | Rotation endpoints, dual-key periods, re-signing jobs, public notice |
| **Malware scanning (ClamAV)** | Simulated | Heavy sidecar | Scan result recorded as `NOT_PERFORMED` and shown in the UI | ClamAV service in the `ingest` queue; quarantine bucket |
| **TUS resumable upload** | Deferred | Single PUT adequate at ≤ 25 MB | Interrupted uploads restart | TUS server with signed upload URLs to object storage |
| **Separate sandbox environment** | Deferred | Dry-run covers the use case | Institutions test via dry-run, not a separate base URL | Second stack with its own DB at `sandbox.api…` |
| **APAAR registry** | Mocked | Real API not public (G-01) | Results come from `mockext.registry_people`; mismatch types are scripted | Real adapter + contract tests (R-01); pluggable validator for real format |
| **AICTE intake master** | Mocked | No access | Reference data from a mock export | Real sync adapter and schedule |
| **SMS / e-mail gateway** | Mocked | No DLT gateway | OTPs readable in Dev SMS Inbox (never in production) | DLT-registered gateway; delivery receipts; remove fixed OTP and inbox |
| **ABC / exam / NAD liveness feed** | Mocked | No access (G-11, R-06) | Layer D leads are illustrative; value claims withheld | Real feeds via `POST /feeds/liveness` with legal route |
| **DigiLocker, NAD, ABC integrations** | Deferred / Future | Access not granted | Only OTP channel works | Production adapters; enable `DIGILOCKER` channel |
| **Counselling, fee/scholarship feeds; three-way match** | Deferred | Feeds do not exist | No three-way mismatch alerts | Feed endpoints + match job (source §8.7) |
| **Faculty feed / `RATIO_THRESHOLD`** | Deferred | Feed does not exist | No student-faculty ratio anomaly | Add rule kind and feature once feed available |
| **Scheduled ERP sync** | Deferred | Needs real ERPs | ERPs push via API only | Beat-scheduled pull adapters |
| **Roles AA, AU** | Merged / Deferred | Essential roles only | AR does analyst duties; no independent auditor | Split roles in `iam.roles`; add `audit:read`-only role |
| **Bulk onboarding, access recertification** | Deferred | Not essential to core flows | Users created individually | Recertification jobs and campaigns |
| **Closure/merger/rename continuity UI** | Deferred | Data model suffices | Links exist, no workflow | Admin workflow over `merged_into_id`/successor links |
| **Report schedules, accreditation formats, NLQ, briefs** | Deferred | Formats unspecified (G-19); LLM optional (AS-11) | On-demand reports only | Schedules via Beat; templates once agreed; India-hosted LLM returning validated structured queries over the semantic layer |
| **Splink-trained matching weights** | Deferred | Needs volume of labels | Static weights from config | Batch training on reviewer labels (stored already) |
| **SHAP** | Deferred | Dependency weight | Contribution tables explain alerts | `shap.TreeExplainer` for Isolation Forest |
| **Geographic map** | Deferred | No GeoJSON bundled | Heat table + treemap | MapLibre with official state/district GeoJSON |
| **Credential types beyond Enrolment Proof** | Deferred | No academic result data | Only enrolment proofs | Add issuers/feeds and templates |
| **Two-APAAR detection** | Deferred | Needs real registry | Not detected | Registry flagging API |
| **Record-level erasure instead of key shredding** | Simplified | Per-institution keys (P-C5) | Ciphertext nulled; key retained | Per-subject keys or scheduled re-encryption + key destruction |
| **Partitioning, pg_cron** | Deferred | Not needed at prototype volume | None at ≤ 50 k students | `pg_partman` per source §5.6 |
| **Eight DB roles** | Simplified | Setup simplicity | Boundaries enforced in code + two roles | Create roles; separate DSNs; `SECURITY DEFINER` drill-down functions |
| **Regional languages, low-bandwidth mode** | Deferred | Prototype scope | English + Hindi only | Message catalogues; image-less lite theme |
| **Observability SLOs / DR / RPO-RTO proof** | Future | Prototype | No SLO guarantees | PITR, replica, restore drills (R-21) |
| **CDN/WAF/Kubernetes/IaC** | Future | Not a production deployment | Single-host Compose | Terraform + managed Kubernetes per source §8.14 |

---

## Appendix B. Traceability Matrix and Cross-Check

### B.1 Functional requirements FR-01…FR-70 → PRD

The source lists FR numbers grouped under features and NFR rows but **does not reproduce each requirement's wording**; rows therefore map by group. Class shows the worst-case class within the row.

| FR | Source feature / NFR | PRD requirement(s) | Class |
|---|---|---|---|
| 01 | F1 multi-channel ingestion / versioned template | REQ-ING-01, REQ-ING-13 | MVP |
| 02 | F1 pre-validation, row-level fixes | REQ-ING-02, REQ-ING-07 | MVP |
| 03 | F1 partial acceptance, diff | REQ-ING-06, -07, -08 | MVP |
| 04 | F8 idempotent re-upload / history | REQ-ING-10, REQ-GATE-18 | MVP |
| 05 | F8 windows, late flags, extensions, readiness | REQ-SUB-01…04, REQ-REF-04/05 | MVP |
| 06 | F1 sandbox / live rules without writes | REQ-GATE-13, DEC-23 | MVP* (dry-run in place of sandbox DB) |
| 07 | F1 upload performance and chunking | REQ-ING-03…05, NFR-02 | MVP |
| 08 | F2 onboarding against master | REQ-ONB-01…03 | MVP* (MFA Deferred) |
| 09–13 | F4 identity validation, diagnosis, statuses, provisional IDs, revalidation | REQ-IDN-01…12 | MVP* (mock registry) |
| 14–19 | F3 seat ledger and gate | REQ-GATE-01…20, REQ-REF-02/03 | MVP |
| 20 | F10 intelligence layers / Trust Index inputs | REQ-INT-03…05 | MVP |
| 21 | F6 rules as data, versioning | REQ-RUL-01, -03, -04 | MVP |
| 22 | F6 rule categories | REQ-RUL-05 | MVP |
| 23 | F6 severities | REQ-RUL-02 | MVP |
| 24 | F7 policy simulator | REQ-RUL-09 | MVP |
| 25 | F6 findings, reconciliation | REQ-RUL-06, -08 | MVP |
| 26–30 | F9 duplicates and transfers | REQ-DUP-01…10 | MVP* (static weights) |
| 31–32 | F5 Triple-Lock | REQ-TL-01…09 | MVP* (DigiLocker Deferred) |
| 33–36 | F10 anomaly, Trust Index, cases | REQ-INT-01, -02, -06…-12 | MVP* (SHAP, three-way match Deferred) |
| 37–39, 41 | F11 dashboards, freshness, one definition | REQ-DASH-01…08 | MVP* (polling; table not map) |
| 40 | F12 reports | REQ-RPT-01…05 | MVP |
| 42–43 | F12 NLQ and briefs | – | Deferred (AS-11) |
| 44–46 | F13 credentials, status list, certificate/QR | REQ-CRD-01…08 | MVP* (Enrolment Proof only) |
| 47 | F14 verification | REQ-VER-01…10 | MVP |
| 48 | F15 student access log | REQ-STU-02 | MVP |
| 49–50 | F16 audit chain, commitments | REQ-AUD-01…09 | MVP* (WORM simulated) |
| 51–54, 57, 58 | F17 access control, encryption, secure SDLC | REQ-AUTH-*, REQ-ACL-*, §9 | MVP* (MFA, KMS/HSM, BYOK Deferred/simulated) |
| 55–56 | F15 consent ledger, corrections | REQ-STU-03…05 | MVP |
| 59 | F18 REST/OpenAPI/webhooks | REQ-INTG-01…07 | MVP |
| 60 | F18 adapters and mocks | REQ-INTG-08…10 | MVP* (mocks; real Future) |
| 61 | Per-institution quotas, scale | REQ-ING-11, NFR-07 | MVP |
| 62 | Performance targets | NFR-01…03 | MVP |
| 63 | Maintainability / testability | NFR-15, §14 | MVP |
| 64 | Availability and recovery | NFR-06 | Deferred / Future |
| 65 | Observability | NFR-14, REQ-OPS-05 | Deferred (Operations page substitute) |
| 66 | Usability and inclusion | NFR-12, DEC-29 | MVP* (en/hi; regional + low-bandwidth Deferred) |
| 67 | Time handling | REQ-REF-04, DEC-16, SYS-11 | MVP |
| 68 | F8 continuity (closure, merger, rename) | REQ-REF-08 | Data only; UI Deferred |
| 69 | F15 data-subject requests | REQ-STU-06 | MVP |
| 70 | F2 institution user admin; recertification | REQ-ONB-04/05 | MVP*; recertification Deferred |

### B.2 Problem-statement themes (inferred in the source)

| PS | Theme | PRD coverage |
|---|---|---|
| 1 | Secure upload/update keyed on APAAR | REQ-ING-*, REQ-GATE-* |
| 2 | Intake, enrolment, supernumerary, category data | REQ-REF-*, REQ-GATE-07/14, seed buckets |
| 3 | Validation, duplicates, cross-institution checks | REQ-RUL-*, REQ-DUP-* |
| 4 | Dashboards for AICTE and institutions | REQ-DASH-* |
| 5 | Privacy-preserving third-party verification | REQ-VER-* |
| 6 | Digitally signed credentials | REQ-CRD-* |
| 7 | Integration APIs and adapters | REQ-INTG-* |
| 8 | Security, privacy, access control | §9, REQ-AUTH/ACL |
| 9 | Reporting | REQ-RPT-* |
| 10 | Certificates with APAAR ID and QR | REQ-CRD-05 |

### B.3 Resolved conflicts C-01…C-19 → PRD

C-01 → §6.8 vault, REQ-ACL-05/06 · C-02 → REQ-RUL-04 · C-03 → REQ-DUP-01 · C-04 → REQ-ING-09, DEC-24 · C-05 → §5.4.2, REQ-SUB-06 · C-06 → REQ-GATE-07 · C-07 → REQ-DASH-06, REQ-GATE-08 · C-08 → REQ-IDN-08 · C-09 → REQ-TL-08, §5.6.7 · C-10 → Deferred (NLQ), semantic-layer seam · C-11 → PostgreSQL layers, no warehouse · C-12 → §16 phase order · C-13/14 → FastAPI + custom React · C-15 → REQ-INT-08 (mock feed) · C-16 → REQ-AUD-03 · C-17 → REQ-INT-02/10 · C-18 → Lock 1 delegated key (§5.1.1, AS-10 simulated) · C-19 → REQ-INT-07.

### B.4 Information gaps G-01…G-20 → PRD

| Gap | PRD handling |
|---|---|
| G-01 | DEC-17 mock format, pluggable validator, §2.5 |
| G-02 | PA-03 illustrative buckets/caps as dated data |
| G-03 | REQ-DASH-06 (occupied + cumulative reported) |
| G-04 | REQ-IDN-08 (hash + last four) |
| G-05 | REQ-CRD generic issuer |
| G-06 | §9.7 (alignment, counsel review Future) |
| G-07, G-09 | Scope: new admissions; PGDM same model (programme level `PGDM`) |
| G-08 | Logical federation (§6.8) |
| G-10 | NFR prototype targets |
| G-11 | Mock feeds, R-06 value claims withheld |
| G-12, G-13 | REQ-TL-07: 30 days, flag only, seat stays occupied |
| G-14 | REQ-GATE-12 |
| G-15 | REQ-VER-02/05 consent via student, QR, share link |
| G-16 | **Not present in the source numbering** (the source lists G-01…G-15 and G-17…G-20) — flagged as a gap in the source |
| G-17, G-18 | Not applicable locally; DEC-01/19 |
| G-19, G-20 | Deferred report formats; continuity via name history and `merged_into` |

### B.5 Assumptions AS-01…AS-12 and risks R-01…R-22

Assumptions are carried in §2.4. Risk handling in the prototype: R-01 adapter + breaker + `PENDING_VERIFICATION` (REQ-IDN-03, GATE-20) · R-02 rules as data + simulator (REQ-RUL) · R-03 exceptions + 90% warning (REQ-GATE-05/07) · R-04 LOW-trust channel measured (REQ-TL-03/09) · R-05 guardian + assisted paths (REQ-TL-04/05) · R-06 mock feeds, value claims withheld (REQ-INT-08) · R-07 leads not verdicts, appeals (REQ-INT-02/10/11) · R-08 keyed salts, privacy review documented (REQ-DUP-10) · R-09 step-up simulated, audit of signing (REQ-AUTH-05) · R-10 pending counts and deadlines (REQ-GATE-20) · R-11 short transactions + hammer test (REQ-GATE-04, §14.4) · R-12 in-memory keys on hot paths (§9.4) · R-13 configuration-driven notices/retention (REQ-OPS-07) · R-14 phase gates (§16) · R-15 portable containers (Compose) · R-16 template, pre-validation, fix-its (REQ-ING, REQ-IDN-06) · R-17 generic issuer · R-18 partition-ready schema (Deferred) · R-19…R-22 Supabase platform limits avoided by DEC-01 (Future for managed hosting).

### B.6 Source sections → PRD sections

| Source (U1) | PRD |
|---|---|
| §1 Overview (problem, objectives, solution, scope, stakeholders) | §1, §2.2, Appendix B.2 |
| §2 Features and requirements (F1–F18, NFR, dependencies) | §3, Appendix A, B.1 |
| §3 Architecture (monolith, layers, flows, integrations, Supabase, environments) | §6, §5.1, §2.6 |
| §4 Technical stack | §6.3, §6.9, Appendix A |
| §5 Database overview | §7 |
| §6 Rules engine and business logic | §3.5–§3.12, §5 |
| §7 Permissions and security | §4, §9 |
| §8 Implementation details (modules, claim, bulk, matching, Triple-Lock, projection, intelligence, credentials, audit, API, reliability, CI/CD, evaluation, roadmap) | §5, §6, §8, §13, §14, §16 |
| §9 Consistency review, gaps, assumptions, risks, recommendations | §2.3–§2.5, Appendix B |

### B.7 Cross-check findings (missing, conflicting or unsupported items)

| # | Finding | Resolution |
|---|---|---|
| 1 | Only one of the "uploaded documents" was attached; the report, deck and D1–D3 are referenced but absent | PRD built on U1 only; change control in §2.7 |
| 2 | Original FR wording and PS-1…PS-10 wording are absent | Mapped by group; requirements restated as testable `REQ-*` |
| 3 | Source's sequence diagrams, DDL, claim-transaction listing and API request examples are absent | Rebuilt: §5.1.1, §7, §8 (flagged for check against the report) |
| 4 | G-16 missing from the source numbering | Noted in B.4 |
| 5 | Source lets IA upload but says "the person who uploads cannot approve" | DEC-24 |
| 6 | Source states student sign-in by phone OTP; brief demands `1234` accounts | DEC-05, REQ-AUTH-09 |
| 7 | Source's per-institution key vs per-subject erasure | DEC-15 |
| 8 | Source requires "no Aadhaar-like patterns in logs/columns" while real APAAR IDs are 12 digits | DEC-17 and REQ-IDN-02 |
| 9 | Source permission matrix omits the CL column and the AA/AU roles retained in §7.1 | §4 restates matrix for retained roles with CL |
| 10 | Source puts Locked = signed commitment but the submission machine lists `Locked` as a state | §5.4.2 defines the transition via sign + countersign |
| 11 | Deck lists Grafana and Redis as highlighted technologies | Deferred by instruction (DEC-02/03); deviation recorded |
| 12 | Source evidence figures are unverified citations | Not reproduced as facts in product copy; keep in the pitch with source numbers |
| 13 | Trust Index weights, SLOs, 78% auto-resolution are proposals | Labelled **proposed**; prototype thresholds in §14.6 |
| 14 | Source defines `confirm_deadline` default but not whether deadline extends for pending-fix identities | PRD keeps 30 days for Lock 3 and 14 days for fixes (DEC-level default, flagged in §2.5) |
| 15 | Source does not specify claim expiry for never-admitted claims | PQ-01 |

---

## Appendix C. Glossary

| Term | Meaning |
|---|---|
| **APAAR ID** | National student identity ("Automated Permanent Academic Account Registry"); in this prototype a **mock** ID in a synthetic format (DEC-17) |
| **ABC / NAD** | Academic Bank of Credits / National Academic Depository; mocked or deferred |
| **Claim** | An institution's request to hold a seat for a student before admitting; the unit decided atomically by the gate |
| **Occupied / Cumulative admitted / Verified** | Seats currently held (CLAIMED or ADMITTED) / ever admitted / occupied and Triple-Lock `VERIFIED` (REQ-DASH-06) |
| **Bucket** | Seat pool per programme, year and type (REGULAR, LATERAL, supernumerary types) |
| **Supernumerary** | Seats outside the regular intake with their own caps (illustrative here) |
| **Triple-Lock** | Institution claim + registry check + student confirmation |
| **NOGEN / CONSENT_REFUSED / FOREIGN** | Provisional-ID reasons: APAAR not generated / family refused consent / foreign student |
| **Trust Index** | 0–100 weighted review-priority score per institution; never a penalty |
| **Elevation / grant** | Case-linked, justified, time-limited permission to see student-level data |
| **Outbox / projector** | Same-transaction event rows consumed to update dashboards, webhooks and other modules |
| **RLS** | PostgreSQL row-level security, forced on tenant tables |
| **SD-JWT VC** | Selective-disclosure signed credential format |
| **Bitstring Status List** | Compact signed revocation list |
| **Merkle cohort commitment** | Signed root over an institution's enrolments at a cut-off |
| **Neutral response** | Identical reply to unknown/unconsented verifier requests |
| **[SIM] / [DEC-nn]** | Simulated capability / implementation decision (§0.1) |
| **Dry-run** | Full validation with no seat consumed or data committed |
| **Spare people** | Mock-registry persons not yet enrolled, used to try new claims in demos |
