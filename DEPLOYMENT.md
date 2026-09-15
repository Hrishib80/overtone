# Deployment

Three pieces, all on free plans:

| Piece | Host | What it serves |
|---|---|---|
| Site | **Vercel** | the built SPA (`dist/`), from `vercel.json` |
| API | **Render** (free web service, Docker) | `/api`, the chat socket `/ws`, and the job loop, from `render.yaml` |
| Data | **Supabase** | Postgres (session pooler) and the public `uploads` bucket |

The site is split from the API so that the landing page stays instant while the
free API instance is asleep. The browser talks to the API cross-origin with a
bearer token (no cookies), so CORS needs exactly one origin and nothing else.

## What the free plan costs you

- **The API sleeps after ~15 idle minutes.** The next request waits for a cold
  start, typically 30–50 seconds. The site loads immediately; sign-in and the
  first pair are what wait. Upgrading the Render service to Starter removes it.
- **There is no room for real models.** The job loop runs inside the API
  (`RUN_WORKER_IN_API=true`) with the stand-in models (`USE_REAL_MODELS=false`).
  That means pairs are drawn without face similarity — which is also what the
  pairing does anyway below 100 people per segment — and **there is no automatic
  photo screening**. Every profile waits for a person (`REQUIRE_APPROVAL=true`),
  and that is the moderation. Invited members skip that queue, so give invite
  codes only to people you trust to vouch.
- **Migrations run at start** (`MIGRATE_ON_START=true`), because the free plan
  has no pre-deploy command. That is only safe with one instance, which is all
  the free plan has.

## Environment

### Render (API)

Set by `render.yaml`: `ENVIRONMENT=production`, `MIGRATE_ON_START`,
`RUN_WORKER_IN_API`, `USE_REAL_MODELS=false`, `REQUIRE_APPROVAL`,
`STORAGE_PROVIDER=supabase`, `SUPABASE_BUCKET=uploads`, a small database pool,
and a generated `JWT_SECRET_KEY`.

Asked for when the blueprint is applied (never committed):

| Key | Value |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres.<project>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres` — percent-encode `@ # / %` in the password |
| `SUPABASE_URL` | `https://<project>.supabase.co` |
| `SUPABASE_KEY` | the **server** key (`sb_secret_…` or legacy `service_role`) — never the anon/publishable key |
| `ALLOWED_ORIGINS` | the Vercel origin, e.g. `https://overtone.vercel.app` — no trailing slash |
| `PUBLIC_WEB_URL` | the same origin; invite links point at it |

Production refuses to start with a missing value, a short JWT secret, a
wildcard origin, local storage, or real models inside the API process.

### Vercel (site)

Build-time variables (Project → Settings → Environment Variables, Production):

| Key | Value |
|---|---|
| `VITE_API_URL` | the Render URL, e.g. `https://overtone-api.onrender.com` — no `/api` |
| `VITE_ADMIN_PATH` | optional; a new random path for the admin portal |

A change to either needs a redeploy, because Vite bakes them into the bundle.

## First deploy

1. **Supabase** — the database is already migrated, seeded, and has the staff
   account. For a fresh project instead:
   ```sh
   DATABASE_URL=… alembic upgrade head
   DATABASE_URL=… python scripts/manage.py seed
   DATABASE_URL=… python scripts/manage.py staff --username <name>   # asks for the password
   ```
   Create a **public** bucket named `uploads` (Storage → New bucket).
2. **Render** — New → Blueprint → pick the GitHub repo → it reads `render.yaml`.
   Fill in the five values above (`ALLOWED_ORIGINS` and `PUBLIC_WEB_URL` can be a
   placeholder until Vercel gives you a URL). Wait for `/api/ready` to answer.
3. **Vercel** — Add New → Project → import the same repo. Framework preset
   "Other"; `vercel.json` supplies the build. Set `VITE_API_URL`, deploy.
4. Put the Vercel origin into Render's `ALLOWED_ORIGINS` and `PUBLIC_WEB_URL`
   (Render redeploys on save).
5. Open the site, sign in as the staff account — it lands on the portal.

## Operating it

- **The admin portal** is at `/<VITE_ADMIN_PATH>` (default in
  `src/services/paths.js`). Only accounts with `is_admin` see it; the API
  answers 404 to everyone else, signed in or not, and publishes no schema.
- **Staff accounts** are made from a machine with the database URL, never over
  HTTP: `python scripts/manage.py staff --username <name>`. Running it again
  resets that account's password — the only password reset there is.
- **Reviewers** (the report queue) are members: `manage.py reviewer --username`.
- **Balance**: `DATABASE_URL=… python scripts/manage.py stats`.
- **Health**: `GET /api/health` (liveness, no dependencies) and `GET /api/ready`
  (checks the database). Every response carries `X-Request-ID`, which is on
  every log line for that request.
- **Logs** are JSON. Socket tokens are redacted from them before they are
  written.

## Supabase: the data API is closed

Supabase publishes every `public` table over its REST API to the `anon` role,
whose key is designed to be public. Migration `e5a1f7c3b820` turns row-level
security on for every table and revokes `anon`/`authenticated` — including as a
default privilege, so later tables start closed. The app is unaffected: it
connects as the table owner. Supabase's security advisor should show no
"RLS disabled" warnings; if one appears after a migration, a table was created
some way that bypassed the default privileges.

## Moving to real models later

1. Build a worker image from `requirements-worker.txt` (ffmpeg, torch,
   insightface, nudenet, speechbrain, sentence-transformers) and bake the models
   in with `python scripts/fetch_models.py` (~2.6 GB).
2. Run `python worker.py` with `USE_REAL_MODELS=true` and `SARVAM_API_KEY`, on
   ~4 GB of RAM.
3. On the API, set `RUN_WORKER_IN_API=false`. Jobs are claimed with
   `FOR UPDATE SKIP LOCKED`, so the switch can overlap safely.

Photos processed by the stand-ins have stand-in embeddings; re-enqueue them
once the real worker is up if face similarity matters for existing members.

## Scaling past one instance

Chat fan-out is in-process until `REDIS_URL` is set. Before running two API
instances: set `REDIS_URL`, set `MIGRATE_ON_START=false` and run migrations as a
release step, and move the job loop to its own worker.

## Local development

```sh
pip install -r requirements-dev.txt
npm install

export DATABASE_URL="sqlite+aiosqlite:///./dev.db"   # .env may point at Supabase
alembic upgrade head
python scripts/manage.py seed
python -m uvicorn backend.app:app --port 8000        # no --reload on Windows
npx vite --port 5173 --strictPort

pytest
ruff check . && ruff format --check .
```
