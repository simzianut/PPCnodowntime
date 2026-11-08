# 0086: The collector container stays down after a deliberate stop

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: docker-compose.yml (`ppc-collector`), decision 0066

## Context
`dataset.live_collector` exits with code 2 when it must stop on purpose: `AMPWHERE_ENABLED` is not 1, or the site told us to stop (decision 0003). Exit code 1 means it hit `max_consecutive_failures`. Decision 0066 gave `ppc-collector` `restart: unless-stopped`. With the `live` profile on and AmpWhere disabled, the container restarted about once a minute: 24 restarts were seen on the local stack. With AmpWhere enabled, the same policy would keep restarting a poller that the site had told to stop, which goes against 0003.

## Decision
- The container wraps the collector in `sh -c`, which maps exit 2 to exit 0.
- The restart policy is `on-failure:5`:
  - after a deliberate stop the container stays down
  - after a crash or repeated poll failures it gets at most 5 restarts, then stays down for a human to look at

## Alternatives considered
- `restart: "no"`: a transient crash at night would end collection until someone notices.
- Change the collector to exit 0 on a deliberate stop: that's M1 code, and the exit code is part of its CLI.

## Consequences
- A stopped collector is visible in `docker compose ps -a` as `Exited (0)`. Its last log line says why.
- Turning collection on still takes an explicit human step: `AMPWHERE_ENABLED=1` plus the `live` profile, then `docker compose up -d ppc-collector`.
