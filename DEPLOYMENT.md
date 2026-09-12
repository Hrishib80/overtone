# Deployment

One web service: FastAPI serves the built SPA, the API, media and the chat
WebSocket from a single HTTPS origin. Same-origin keeps login and live chat
simple and avoids cross-origin credential problems.

A second **worker** service runs model inference — face embedding, speaker
embedding, transcription, text embedding — off the request path. See *The
worker* below.

## Environment

Set these on the host, never in source control:

- `ENVIRONMENT=production`
- `DATABASE_URL` — PostgreSQL, `postgresql+asyncpg://…`
- `JWT_SECRET_KEY` — at least 32 characters; production refuses to start below that
- `ALLOWED_ORIGINS` — the exact public origin, no trailing slash. A wildcard is
  rejected in production because browser requests carry credentials
- `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_BUCKET`
- `SARVAM_API_KEY` — speech-to-text; worker only
- `SENTRY_DSN` — optional
- `LOG_LEVEL` — defaults to `INFO`; logs are JSON in production, console elsewhere

Configuration is validated at import. A production process with a missing or
weak value fails immediately rather than at the first request.

## Migrations

The schema is owned by Alembic. `create_all` is gone.

```sh
alembic upgrade head
```

Run this as a **release command, before** the new instances roll out — not from
the container's start command, which would race when several instances boot at
once. `alembic check` fails CI if the models and migrations have drifted apart.

To adopt an existing database that already has the tables:

```sh
alembic stamp head     # records the baseline without re-running it
```

## Reference data and campuses

After migrating, load the seeded reference rows (81 prompts, 38 gender
identities, 30 sexualities). It upserts, so it is safe to re-run on every
deploy and picks up edits to the JSON seed files:

```sh
python scripts/manage.py seed
```

Then create the campus. Caps are **per segment** — one headline number fills
one side in a week and starves the other:

```sh
python scripts/manage.py scope-create \
    --slug iitm --name "IIT Madras" \
    --domain smail.iitm.ac.in --domain iitm.ac.in \
    --cap man=600 --cap woman=600
```

A segment with no cap row is uncapped. Signups whose email domain matches no
scope are refused, so the domain list *is* the access control.

```sh
python scripts/manage.py scope-list        # occupancy and waitlist depth
python scripts/manage.py waitlist-sweep    # expire stale invites, invite next
```

`waitlist-sweep` should run on a schedule (hourly is plenty). Without it,
invitations that nobody claims hold their slot forever.

## Build and run

```sh
docker build -t overtone .
docker run -p 8000:8000 --env-file .env overtone
```

The image is deliberately small — no torch, no models, no ffmpeg. The container
listens on `PORT`.

## The worker

A second process, from the same repo:

```sh
pip install -r requirements-worker.txt
python scripts/fetch_models.py       # ~2.6 GB, cache it in the image layer
USE_REAL_MODELS=true python worker.py
```

It owns every model; the API loads none, which is what keeps the API image
small. `python worker.py --status` prints which implementation each role would
use and touches nothing — safe to run anywhere.

**Real models are opt-in outside production.** Without `USE_REAL_MODELS=true`
the worker runs deterministic stubs, so a development machine never downloads
gigabytes by surprise. In production they are always on, and a missing model is
fatal at startup rather than silently degrading.

Speech-to-text needs `SARVAM_API_KEY`. Nothing else needs a credential — face,
voice and text models are local.

Jobs live in the `jobs` table, so the worker needs only `DATABASE_URL`. A job
survives a restart, retries with exponential backoff, and is parked as `failed`
after five attempts with its payload and traceback intact. Anything left
`running` by a killed worker is requeued after fifteen minutes.

## Health checks

- `GET /api/health` — liveness. Touches no dependency, so a database blip does
  not cause a restart loop.
- `GET /api/ready` — readiness. Checks the database; returns 503 when it cannot
  serve. Point the load balancer at this one.

Every response carries `X-Request-ID`, which also appears on every log line for
that request. A user reporting a failure can hand you an id you can grep.

## Scaling

Chat connections are held in process, so **run one web instance** for now.
Phase 03 moves fanout to Redis pub/sub, after which the tier scales normally.

Workers scale freely: claiming uses `FOR UPDATE SKIP LOCKED`, so several can
pull from the same table without blocking each other.

The database URL points at Supabase's transaction pooler, which cannot hold
prepared statements between checkouts. The engine disables asyncpg's statement
cache automatically when it sees a pooler host — don't remove that, or every
query after the first fails with a duplicate-prepared-statement error.

## Frontend

No `VITE_API_URL` or `VITE_WS_URL` is needed in this single-service setup — the
site calls its own `/api` and `/ws` paths. For a separately hosted frontend, set
`VITE_API_URL` to the API origin and `VITE_WS_URL` to `wss://…/ws` at build time.

Voice and video calling has been removed, so no TURN or STUN configuration is
required. The microphone permission remains for recording the voice prompt.

## Local development

```sh
pip install -r requirements-dev.txt
npm install

alembic upgrade head
python -m uvicorn backend.app:app --reload    # API on :8000
npm run dev                                   # Vite on :5173, proxying to :8000

pytest
ruff check . && ruff format --check .
```
