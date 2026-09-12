# API image. Deliberately small: no torch, no models, no ffmpeg — inference
# lives in the worker image (phase 02), which is built from requirements-worker.txt.

FROM node:22-bookworm-slim AS frontend-build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
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
COPY alembic.ini pyproject.toml ./
COPY --from=frontend-build /app/dist ./dist

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/media_uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Migrations are a deploy step, not a container start step — running them here
# would race when more than one instance starts at once. Run
# `alembic upgrade head` as a release command before rolling out.
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
