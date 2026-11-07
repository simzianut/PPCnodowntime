# 0013: Repo scaffold: packaging, entry points, tests, migrations

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 00 §1 and §4, CONTRACTS (make targets), all modules

## Context
T2 builds the skeleton from spec 00. The spec fixes the stack but not how packages are built, how `make` reaches each module, where tests live or how migrations run.

## Decision
- **One uv project, hatchling build.** The five packages (`dataset`, `model`, `stations`, `sim`, `api`) sit at the repo root and are built as one wheel. `uv.lock` is committed. Python is pinned to 3.12 (`.python-version`).
- **Dependencies.** Everything in the stack is a base dependency, except Stable-Baselines3 (it pulls in PyTorch, ~1 GB). That is the `rl` extra, used only by `make train`.
- **Entry points.** `make seed|data|train` run `python -m stations|dataset|model`. Until a module adds its `__main__.py`, the target prints "not implemented yet" and exits 0. `make api` and `make web` do the same until `api/main.py` and `frontend/package.json` exist.
- **Tests.** Each module keeps its tests in `<package>/tests/`, so modules don't edit a shared folder. Cross-module checks (config keys) live in top-level `tests/`.
- **Migrations.** Plain SQL in `db/migrations/NNNN_slug.sql`, applied by `make db` in name order, each in one transaction, recorded in `schema_migrations`. No Alembic.
- **Frontend package manager:** npm.
- **DB healthcheck uses TCP** (`pg_isready -h 127.0.0.1`). The postgis image runs a socket-only temporary server during first init, and a socket check reported healthy before that server restarted.
- **Config files** carry the placeholder values already written in specs 01, 03 and 04. `background_ev` and `ice_base` in `config/signals.yaml` have no value in any spec, so they are `null` until T8 sets them (with a decision).

## Alternatives considered
- Alembic: autogenerate is useful, but M3 writes DDL by hand (spec 03 §3) and SQL files are easier for every agent to read.
- One package per module with its own pyproject: cleaner isolation, but five lockfiles and cross-imports through path deps for a 24h project.
- SB3 as a base dependency: simpler, but every `uv sync` would download PyTorch.
- `make` targets that fail when the entry point is missing: stricter, but `make test`/CI would be red until every module lands.

## Consequences
- Modules plug in by adding `__main__.py`; the Makefile doesn't need editing.
- `background_ev` and `ice_base` must be set before the signal generator runs.
- Migrations can't be rolled back automatically; fix forward with a new file.
