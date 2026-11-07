# 0066: Whole stack in Docker Compose, one nginx entry point on Traefik's network

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS (services and ports, env vars), docker-compose.yml, Makefile, docker/, frontend/src/api/client.ts, README

## Context
The app must run on the GPU server (BENGEE-PROD-NVIDIA01) behind the server's shared Traefik, and start with a single `docker compose up`. Vlad wants Traefik to route to **one** container, with no bigger change to the main ingress. Traefik reaches its backends over a shared Docker network (labels). That server's Docker has no free subnet for a new compose network (0063). Until now only Postgres, training and figures ran in Docker; the API and the frontend ran on the host (`make api`, `make web`).

## Decision
- **Services** in `docker-compose.yml`:
  - `ppc-db` (PostGIS)
  - `ppc-migrate`: one-shot; applies `db/migrations` with the same `schema_migrations` bookkeeping as `make migrate`
  - `ppc-seed`: one-shot; runs `python -m stations`
  - `ppc-api`: FastAPI with a single uvicorn worker, because replay state lives in memory
  - `ppc-web`: nginx; serves the built SPA and proxies `/api` and `/ws` (WebSocket upgrade) to `ppc-api:8000`
  - `ppc-collector`: the live collector. It runs only under the compose profile `live`, because nothing in the app reads its output yet and it makes outbound calls from a production host. AmpWhere stays off unless a human sets `AMPWHERE_ENABLED=1` (0003).
- **Service names carry a `ppc-` prefix.** On a shared network, compose service names are DNS aliases, so a bare `db` or `api` could resolve to another project's container.
- **Server override `docker-compose.server.yml`** redefines the `default` network as Traefik's external network (`TRAEFIK_NETWORK`). No new network is created. It also removes all published ports and puts Traefik labels on `ppc-web` only (`PPC_HOST`, `TRAEFIK_ENTRYPOINT`). The other services get `traefik.enable=false`. The server's `.env` sets `COMPOSE_FILE=docker-compose.yml:docker-compose.server.yml`, so a plain `docker compose up -d` is enough there.
- **Local / normal hosts:** the base file alone uses a private compose network. `ppc-web` is published on `HTTP_PORT` (default 8080). `ppc-db` is bound to `127.0.0.1:5432` only, so `make db` and host-side tools keep working.
- **API image:** uv with Python 3.12, deps `--extra rl --extra train`, but **CPU-only torch** (`download.pytorch.org/whl/cpu`) instead of the cu124 wheel the lock pins on Linux. It does not use `UV_COMPILE_BYTECODE` (0063). `data/` is bind-mounted read-only, and `artifacts/` is mounted too, so a newly promoted model needs no rebuild. Licensed data never enters an image.
- **Frontend:** built with `VITE_API_URL=""`, which means same origin: REST goes to `/api/...`, and the WebSocket goes to `ws(s)://<page host>/ws/...`. `client.ts` changes in two lines so that an empty value means same origin. The dev default (`http://localhost:8000` when unset) is unchanged.
- `POSTGRES_PASSWORD` comes from `.env` (default `ppc` for local use). On the server it must be a real secret, because the DB shares a network with other projects' containers.
- Training stays in `docker-compose.train.yml`. It is a batch job on a GPU, not a service.

## Alternatives considered
- Host port + Traefik file provider: needs a file-provider entry in the main Traefik config, which is the ingress change Vlad wants to avoid.
- Own internal network with a hand-picked subnet: there are no free predefined pools, and picking a range on a production host risks colliding with networks we don't know about.
- `network_mode: host`: exposes every port on the host and breaks container DNS.
- Serve the SPA from FastAPI: one container fewer, but mixes static files into the API and needs API changes; nginx also handles the WebSocket upgrade and caching cleanly.

## Consequences
- Other containers on Traefik's network can reach `ppc-db` and `ppc-api` directly. That is tolerable for a demo with a real DB password; the internal-subnet option can come back if the server gets free pools.
- `data/` must exist on the server (the 0062 package) for real mode. Without it, set `API_FIXTURES=1`.
- `make db` now targets the service `ppc-db`.
- If the main Traefik needs a cert resolver on the router, add one label line in `docker-compose.server.yml`.
