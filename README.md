# PPCnodowntime

Energy-saving sleep scheduling for PPC Blue charge points. What and why: `docs/PRD.md`. How we work: `AGENTS.md`.

## Run locally
Needs Docker, [uv](https://docs.astral.sh/uv/) and Node 20+.

```
cp .env.example .env
make db      # postgres+postgis on :5432, apply db/migrations
make seed    # M3: load station seed into DB
make data    # M1: build processed datasets (needs data/raw/)
make train   # M2: train forecaster + PPO, write artifacts/
make api     # M4: FastAPI on :8000
make web     # M5: Vite on :5173
make test    # pytest + frontend tests
make lint    # ruff
```

Repo layout and conventions: `docs/specs/00-foundation.md`. Interfaces: `docs/CONTRACTS.md`.
