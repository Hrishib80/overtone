# API image. Deliberately small: no torch, no models, no ffmpeg — inference
# lives in the worker image (phase 02), which is built from requirements-worker.txt.

FROM node:22-bookworm-slim AS frontend-build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
# The icons and manifest. Without this the build succeeds and every tab icon
# is a 404, which nothing in the build output mentions.
COPY public ./public
# Only needed when this image serves the site as well; a split deploy builds
# the site on its own host with its own values.
ARG VITE_API_URL=""
ARG VITE_ADMIN_PATH=""
RUN npm run build


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
COPY --from=frontend-build /app/dist ./dist

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
