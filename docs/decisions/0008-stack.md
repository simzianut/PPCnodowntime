# 0008: Stack: Python, Postgres + PostGIS, FastAPI, React + MapLibre

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: spec 00, all modules

## Decision
| Area | Choice |
|---|---|
| Python | 3.12 with uv and ruff |
| Data | pandas and Parquet |
| Forecaster | LightGBM |
| RL | Gymnasium + Stable-Baselines3 |
| Database | Postgres 16 + PostGIS via docker-compose |
| API | FastAPI with REST and WebSocket |
| Frontend | React + TypeScript + Vite, MapLibre with OpenFreeMap tiles, Recharts |

## Alternatives considered
- SQLite: simpler, but the team wanted real geo queries.
- Streamlit: a faster UI, but less polished, and it would merge M4 and M5.

## Consequences
- Everyone needs Docker for the database.
