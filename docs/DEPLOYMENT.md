# ShikshaSetu deployment guide (Supabase + Render + Vercel)

| Piece | Where | Why |
|---|---|---|
| PostgreSQL | **Supabase** | Already the system of record (`DATABASE_URL`). |
| Core API + worker + mock services | **Render** (Docker web service) | FastAPI needs a long-running process, a background worker and writable files. Vercel cannot host this. |
| verify-api (port 8001 locally) | **Render** (second Docker web service) | It is a separate deployable by design. |
| React web app | **Vercel** | Static Vite build. |

Resulting URLs (yours will differ):

- Web: `https://shikshasetu.vercel.app`
- API: `https://shikshasetu-api.onrender.com`
- Verify: `https://shikshasetu-verify.onrender.com`

Deploy in this order: **Supabase → generate secrets → migrate and seed → Render → Vercel → fix CORS**.

---

## 0. Prerequisites

- Push the repo to GitHub. `.env` and `data/` are already git-ignored, so keep it that way.
- Python 3.11+ and Node 22 locally, for the one-time migrate and seed step.

## 1. Supabase

1. Create a project and save the database password. You cannot see it again, only reset it.
2. Open **Project Settings → Database → Connection string**.
3. Use the **Session pooler** string (port **5432**, host `aws-0-<region>.pooler.supabase.com`, user `postgres.<project-ref>`).
   - Render's network is IPv4. The "direct" host `db.<ref>.supabase.co` is IPv6-only unless you buy the IPv4 add-on, so the direct string will fail from Render.
   - Do **not** use the transaction pooler (port 6543). The app uses `SET LOCAL` per transaction and Alembic needs a session connection.
4. Convert it to the SQLAlchemy/psycopg form:

```
postgresql+psycopg://postgres.<project-ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

   If the password contains `@ : / # ?` etc., URL-encode it (`@` becomes `%40`). Easiest is to reset it to letters and digits only.

## 2. Generate production secrets (once, keep them forever)

```bash
python -c "import secrets;print(secrets.token_urlsafe(48))"
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
```

Run the first command twice, for `JWT_SECRET` and `IDENTITY_HMAC_KEY`. Use the second for `MASTER_KEY`.

> **Do not change `MASTER_KEY` or `IDENTITY_HMAC_KEY` after seeding.** Signing keys and per-institution data keys in the database are wrapped by `MASTER_KEY`. A different key makes existing data unreadable. The seed step and Render must use the **same** values.

## 3. Migrate and seed Supabase from your machine

Put the values in your local `.env` (never commit it, never paste secrets in chat):

```
DATABASE_URL=postgresql+psycopg://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres
JWT_SECRET=...
MASTER_KEY=...
IDENTITY_HMAC_KEY=...
APP_ENV=demo
DEMO_MODE=true
```

```bash
cd backend
pip install -e .
python -m app.cli migrate
python -m app.cli seed --profile standard
python -m app.cli verify-seed --profile standard
python -m app.cli rls-check
```

(`python -m app.cli setup --profile standard` runs migrate, seed and verify in one go.)

**Known risk:** this project has never been run against hosted Supabase. Migration 0001 creates an `app_runtime` role and uses `SET LOCAL ROLE`. If `migrate` fails with a permission error on `CREATE ROLE` or `SET ROLE`, stop and send me the error. It needs a small migration tweak, not a different hosting plan.

## 4. Render (backend)

You create two **Web Services** from the same repo. For both: **New → Web Service → connect GitHub repo**, then set:

- **Language/Runtime:** Docker
- **Root Directory:** `backend`
- **Dockerfile Path:** `Dockerfile`
- **Region:** the one nearest your Supabase region
- **Instance type:** Free works for a demo (see caveats below)

### 4a. Service `shikshasetu-api`

**No Docker Command needed.** The image runs `backend/start.sh`, which picks the process from the `SERVICE` variable and binds to Render's `$PORT`. Add `SERVICE` = `api` (the default). It starts the mock registry and SMS service on 127.0.0.1:8002 (matches the default `MOCK_SERVICES_URL`) and the worker as background processes in the same container, so they share its disk with the API.

**Health Check Path:** `/readyz`

**Environment variables:**

| Key | Value |
|---|---|
| `APP_ENV` | `demo` |
| `DEMO_MODE` | `true` |
| `DATABASE_URL` | the Supabase session-pooler URL from step 1 |
| `JWT_SECRET` | from step 2 |
| `MASTER_KEY` | from step 2 |
| `IDENTITY_HMAC_KEY` | from step 2 |
| `IDENTITY_HMAC_KEY_VERSION` | `1` |
| `SEED_DEMO_PASSWORD` | `1234` (same as at seed time) |
| `SMS_FIXED_OTP` | `123456` (so demos need no real SMS) |
| `CORS_ORIGINS` | `https://shikshasetu.vercel.app` (update after step 5) |
| `PUBLIC_VERIFY_URL` | `https://shikshasetu-verify.onrender.com` |
| `STORAGE_DIR` | `/app/data/objects` |
| `WORM_DIR` | `/app/data/worm` |
| `KEYS_DIR` | `/app/data/keys` |
| `LOG_LEVEL` | `INFO` |

No trailing slashes on any URL. `CORS_ORIGINS` may hold several origins separated by commas (add your custom domain later).

### 4b. Service `shikshasetu-verify`

**No Docker Command needed.** Add the variable `SERVICE` = `verify`, and `start.sh` runs verify-api only. (Without it the service would start the core API instead.)

**Health Check Path:** `/docs` in demo mode, or leave blank.

**Environment variables:** the same `APP_ENV`, `DEMO_MODE`, `DATABASE_URL`, `JWT_SECRET`, `MASTER_KEY`, `IDENTITY_HMAC_KEY` and `IDENTITY_HMAC_KEY_VERSION` as the API, plus:

| Key | Value |
|---|---|
| `CORS_ORIGINS` | `https://shikshasetu.vercel.app` |
| `PUBLIC_VERIFY_URL` | `https://shikshasetu-verify.onrender.com` |

Both services must share the **same** secrets, because verify-api validates tokens and credentials the core API issued.

### Check

```bash
curl https://shikshasetu-api.onrender.com/readyz
curl https://shikshasetu-api.onrender.com/healthz
```

`/docs` is available only while `DEMO_MODE=true`.

### Render caveats

- **Free instances sleep after about 15 minutes idle.** The first request takes about 30-60 seconds, and the in-container worker is also asleep, so queued jobs (webhooks, reports) run only while the service is awake. Warm the service up before presenting, or use the Starter plan.
- **Filesystem is ephemeral** unless you add a Render **Disk** (paid plans only, mounted at `/app/data`). Without one, uploaded documents, generated reports and WORM audit checkpoint files are lost on each redeploy or restart. Everything else (users, ledger, keys, credentials, the audit log) lives in Supabase and is safe. A Render Disk attaches to one service, which is why the worker runs inside the API container.
- Render Disks are not shared across services, so do not split the worker into a separate service while the app still writes to local files.

## 5. Vercel (frontend)

1. **Add New → Project →** import the GitHub repo.
2. **Root Directory:** `web`
3. **Framework Preset:** Vite (build `npm run build`, output `dist`; both are auto-detected).
4. **Environment Variables** (these are baked in at build time, so changing them needs a redeploy):

| Key | Value |
|---|---|
| `VITE_API_URL` | `https://shikshasetu-api.onrender.com/api/v1` |
| `VITE_VERIFY_URL` | `https://shikshasetu-verify.onrender.com` |

5. Deploy. `web/vercel.json` already rewrites every path to `index.html`, so direct links and refreshes on React Router routes work.
6. Copy the final Vercel URL. If it differs from what you set in `CORS_ORIGINS`, update that variable on **both** Render services and let them redeploy.

## 6. Smoke test

1. Open the Vercel URL. The demo accounts list on the login page confirms the web app can reach the API (this endpoint exists only in demo mode).
2. Sign in with a demo account (password `1234`).
3. Upload a file and open an audit or integrity page, to exercise the database and the worker.
4. Open the verifier portal, to confirm the web app can reach verify-api.

If the browser console shows CORS errors, `CORS_ORIGINS` doesn't exactly match the page origin (scheme, host, no trailing slash). If you see network errors instead, check that `VITE_*` values were set before the build.

## 7. Going to real production later

Set `APP_ENV=production` and `DEMO_MODE=false`. The API then refuses to start with placeholder or short secrets. This also:

- disables `/docs` and the demo-accounts endpoint, and the web login's demo list goes away;
- generates random seed passwords instead of `1234`, so you need a plan to hand out credentials;
- blocks private/loopback webhook targets.

Replace the mock registry and SMS with real services before doing this, and add a Render Disk or object storage for files.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Network is unreachable` or timeout to the DB from Render | You used the direct IPv6 host. Use the session pooler. |
| `prepared statement ... already exists` or `SET LOCAL` has no effect | You used port 6543. Switch to 5432. |
| API exits with "Refusing to start in production" | Secrets are placeholders or under 32 characters, or `DEMO_MODE` is true with `APP_ENV=production`. |
| Login works locally but fails after deploy | Different `MASTER_KEY`/`IDENTITY_HMAC_KEY` from the ones used at seed time. |
| Frontend still calls `localhost:8000` | `VITE_API_URL` missing at build time. Set it and redeploy. |
| Webhooks/reports never complete | Worker is asleep (free tier) or not running. Check the start command. |
