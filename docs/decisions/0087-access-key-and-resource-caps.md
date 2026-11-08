# 0087: Access key for the hosted app, and CPU/memory caps on the containers

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: — (replaces the nginx rate-limit / basic-auth proposal discussed for 0066, which was never adopted)
- Affects: CONTRACTS (access-key routes, env vars), api/main.py, api/auth.py, frontend (AuthGate, client.ts), docker-compose.yml, .env.example, README

## Context
The app will be hosted on bengee. That production host also runs vLLM and LiteLLM for production AI calls, and the compose services had no resource caps. The API is open: `POST /api/runs` is CPU-heavy and evicts other users' runs (MAX_RUNS = 10), and the autonomy setting and recommendations can be changed by anyone. Vlad chose access control by an API key, generated when the site starts, with a prompt for it in the frontend on the first visit.

## Decision
**Access key**
- `ppc-api` reads `PPC_ACCESS_KEY`. If it is empty and `PPC_AUTH=on`, the API generates a key with `secrets.token_urlsafe(32)` and logs it once at start (`docker compose logs ppc-api | grep "access key"`).
- Compose sets `PPC_AUTH=on`. In code the default is off, so host-side `make api` / `make web` and the tests work unchanged unless a key is set.
- A pure ASGI middleware, added inside CORS, checks every `/api` route and the `/ws` handshake in both real and fixtures mode:
  - `/api` returns 401
  - the `/ws` handshake is closed with 4401 before accept
  - `/api/health` (healthcheck) and `/api/auth/*` stay open
- `POST /api/auth/login` compares the key with `secrets.compare_digest` and sets the cookie `ppc_access`:
  - the value is an HMAC-SHA256 of a fixed label under the key; the key itself never sits in the browser
  - HttpOnly, SameSite=Strict, Path=/, 30 days
  - `Secure` when `X-Forwarded-Proto` is https (nginx passes on Traefik's header)
- A cookie is used because browsers can't set custom headers on a WebSocket. The key never goes in a URL, because URLs end up in access logs.
- Frontend:
  - `AuthGate` wraps `<App/>`. It asks `GET /api/auth/status` and shows the key prompt instead of the app when needed.
  - Any later 401 shows the prompt again; after login the page reloads.
  - The static SPA shell stays public, while all data needs the key.
  - The gate fails open when the status route is missing (an older API), because the API still enforces the check.

**Resource caps**
- The API-image services (`ppc-api`, `ppc-seed`, `ppc-collector`) get `API_CPUS` (default 4) and `API_MEM_LIMIT` (default 8g). `OMP_NUM_THREADS` and `MKL_NUM_THREADS` are set to `API_CPUS`, so torch and BLAS don't start one thread per host core.
- `ppc-db`: 1 CPU and 1g. `ppc-web`: 0.5 CPU and 256m. All of these can be overridden from `.env`.

## Alternatives considered
- nginx `limit_req` + basic auth: proposed earlier. Basic auth gives no in-app prompt and is awkward to rotate; Vlad chose the key flow.
- Key in a header via `localStorage`: doesn't work for the WebSocket, and would expose the key to any XSS.
- Key as a query parameter on `/ws`: ends up in nginx and Traefik logs.

## Consequences
- Without a fixed `PPC_ACCESS_KEY`, each API restart creates a new key and logs everyone out. The server `.env` should set one.
- There is no login rate limit. A 32-byte random key can't realistically be brute-forced, and `/api/auth/login` does no heavy work. A short or weak hand-picked key would weaken this.
- One key is shared by everyone. There are no per-user accounts and no audit trail.
- With `API_CPUS=4`, a real-mode replay can be slower than on an unlimited laptop run.
