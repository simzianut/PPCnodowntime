# 0077: Docker is the standard way to run the app

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: — (builds on 0066)
- Affects: AGENTS.md, README.md, every module's "how to test"

## Context
The stack had several ways to run it: native (`make api` + `make web`), fixtures, and the Docker stack from T35 (0066). Demos and checks done natively hid problems: stale venvs, different data paths, a frontend falling back to demo data. The Docker stack now runs end to end with the F2 agent and live mode (T35, T38).

## Decision
- Everyone runs, demos and checks the app with `docker compose up -d --build` → http://localhost:8080. This applies to humans and agents (AGENTS.md "Running the app").
- Docker needs at least 4 GB of memory. On Windows that is `memory=4GB` in `.wslconfig`.
- The private data folder is set with `DATA_HOST_DIR` in `.env`.
- Native `make api` / `make web` stay available only for debugging a single module. Tests and lint stay native (`make test`, `make lint`).

## Alternatives considered
- Keep both as equal options: two setups drift apart, and "works on my machine" bugs come back.

## Consequences
- A task's "how to test" note should say how to check it in the Docker stack.
- After code changes, rebuild (`--build`) and reload the page.
