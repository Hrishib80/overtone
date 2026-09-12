# Production deployment

This project is deployed as one web service: FastAPI serves the built website, API, media route, and WebSocket route from the same HTTPS address. That avoids cross-origin login failures and keeps live chat on the same connection.

## Required environment values

Set these on the hosting provider, not in source control:

- `ENVIRONMENT=production`
- `DATABASE_URL`: PostgreSQL connection URL using `postgresql+asyncpg://`
- `JWT_SECRET_KEY`: a long random secret (at least 32 bytes)
- `SUPABASE_URL`, `SUPABASE_KEY`, and `SUPABASE_BUCKET`
- `PINECONE_API_KEY`, `INDEX_HOST`, and optional `PINECONE_NAMESPACE`
- `ALLOWED_ORIGINS=https://your-domain.example`

`ALLOWED_ORIGINS` must be the exact public site origin. A wildcard is intentionally rejected in production because authenticated browser requests use credentials.

## Build and start

Build and run the included Docker image. The application listens on the platform-provided `PORT` value and exposes `GET /api/health` for health checks.

Use one application worker for this version. Live chat and call signaling are held in process, so multiple workers require a shared message broker before horizontal scaling.

## Frontend and calls

No `VITE_API_URL` or `VITE_WS_URL` value is needed when using this single-service deployment. The website calls its own `/api` and `/ws` paths.

For a separately hosted frontend, configure `VITE_API_URL` as the API origin only, for example `https://api.example.com`, and `VITE_WS_URL` as `wss://api.example.com/ws` before the frontend build.

WebRTC uses public STUN by default. For dependable voice and video calls across mobile and restrictive networks, set `VITE_TURN_URL`, `VITE_TURN_USERNAME`, and `VITE_TURN_CREDENTIAL` at frontend build time using a managed TURN provider.
