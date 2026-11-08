# 0088: bengee gets an offline deploy tar, and routing can go through Traefik's file provider

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: docker-compose.server.yml, CONTRACTS (env vars), decision 0066

## Context
The app goes to bengee at `nodowntime.space`. The server has no git checkout and gets no build step: code and data arrive as a tar copied with scp (as for training, 0062). Vlad wants the route added to Traefik's file provider (`dynamic.yaml`) instead of Docker labels. If Traefik's Docker provider is also on, the labels from 0066 on `ppc-web` would create a second router with the same Host rule.

## Decision
**Labels switch**
- `docker-compose.server.yml` sets `traefik.enable=${PPC_TRAEFIK_LABELS:-true}` on `ppc-web`. With `PPC_TRAEFIK_LABELS=false`, only the file-provider route exists.
- The default `true` keeps 0066's label routing.

**Container name for the file route**
- `.env.server` sets `COMPOSE_PROJECT_NAME=ppcnodowntime`. The container is then `ppcnodowntime-ppc-web-1` whatever folder the tar is unpacked into.
- The file route's service URL is `http://ppcnodowntime-ppc-web-1:80`.
- That works when the Traefik container is on `TRAEFIK_NETWORK`, which is the stack's default network.

**The tar**
- Built from `main`, with the top folder `ppcnodowntime/`. It contains:
  - `docker save` of `ppc-api:latest`, `ppc-web:latest` and `postgis/postgis:16-3.4` (linux/amd64)
  - the compose files, `docker/`, `db/migrations`, `artifacts/`
  - the data real mode needs: `data/processed`, `data/seed`, `data/raw/live`, `data/raw/stations`
  - `.env.server`, without the generated secrets
- The server runs `docker compose up -d --no-build`, so nothing is built there.
- The RL policies found locally (`maskppo`, `ddqn`) are 5,000-step smoke runs that were never promoted. They ship under `extras/`, not in `artifacts/`, so the server doesn't offer them as agents.

## Alternatives considered
- Labels only (0066): Vlad wants the route in `dynamic.yaml`.
- Remove the labels outright: the label route stays useful on hosts where the Docker provider is the norm.
- Build on the server: no build step there, and the build needs internet plus the uv/npm caches.

## Consequences
- The tar holds AmpWhere-derived data and API keys. It goes only to our own server and is never published.
- If Traefik isn't a container on `TRAEFIK_NETWORK` (for example, it runs on the host), the file route can't resolve the container name. `ppc-web` would then need a port published on `127.0.0.1`.
