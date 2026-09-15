# API image, and only the API. Deliberately small: no torch, no models, no
# ffmpeg — real inference would live in a worker image built from
# requirements-worker.txt.
#
# No site either. The site is built and served by Vercel; this image used to
# build a second copy into dist/, which the app then served at the Render URL.
# That copy was half-working by construction — its chat socket came from an
# origin ALLOWED_ORIGINS does not list and was refused — so anyone who found the
# API's address met a site whose chat looked broken. Without dist/, the app mounts no
# site (backend/app.py checks for the folder) and `/` is a plain 404.

FROM python:3.12-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY alembic ./alembic
COPY scripts ./scripts
COPY worker.py alembic.ini pyproject.toml ./

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/media_uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Migrations are a deploy step, not a container start step — running them at
# start races when more than one instance boots at once. Run
# `alembic upgrade head` as a release command before rolling out where the host
# has one. MIGRATE_ON_START=true is for a host that does not (Render's free
# plan) and is only safe with exactly one instance; seeding is idempotent.
#
# --forwarded-allow-ips: the host's proxy is not on localhost, so without it
# uvicorn ignores X-Forwarded-For and every visitor has the proxy's address —
# one shared rate-limit bucket for the whole site. The container is reachable
# only through that proxy, which is what makes trusting it safe.
CMD ["sh", "-c", "if [ \"$MIGRATE_ON_START\" = true ]; then alembic upgrade head && python scripts/manage.py seed; fi && exec uvicorn backend.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
