# 0080: E2E integration: merge T44, real model + live statuses in the Docker app, API errors don't fall back to demo

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: —
- Affects: TASKS (T44, T38, T34), frontend `api/client.ts`, `state/useDashboard.ts`, `App.tsx`, README run notes, local `data/processed/` (private, not in git)

## Context
Tudor asked to merge everything into one end-to-end app now that the trained F2 GRU is on main. Open branches: T44 (Vlad, in progress, merges cleanly), T38 (Teodora, conflicts, superseded by T39), T34-deslop-b/c (versions not picked). Vlad's model zip has `forecasts.parquet`, but replay on real sessions also needs `sessions/signals/context.parquet` from the `ppc_data_v1` + `ppc_signals_v3` zips, which Tudor's laptop doesn't have. Without them `sim.data.load_default()` silently uses synthetic sessions, the first eval day (2025-03-10) isn't a replay day, `POST /api/runs` returns 400, and the frontend treated that 400 as "API down" and showed demo data.

## Decision
- Merge T44 now (old `f2_gru.pt` loads unchanged, agent unchanged). T44 stays In progress for Vlad's suite and promotion.
- Don't merge T38 or T34-deslop-b/c.
- Real data in the app for now = live mode on real AmpWhere statuses with the F2 GRU online forecaster. Station tables are built locally with `python -m stations` from the collected snapshot (445 locations / 671 CPs; ids match `forecasts.parquet`).
- Frontend: an HTTP error from the API keeps the current run and shows the backend's `detail`; only a network failure (no response) falls back to demo. `?mode=live` starts on a live run; if that fails with an HTTP error the app starts the normal replay instead.

## Alternatives considered
- Wait for the data zips before running anything: blocks the demo on a file transfer.
- Rebuild sessions locally: needs the raw Dundee data, not on this laptop.

## Consequences
- Live mode shows the trained model on real statuses; replay needs the private data zips unpacked into `data/processed/`.
- `traffic_typical.parquet` is missing locally, so live signals use the neutral 0.5 traffic index.
- 5xx no longer drops to demo either; a broken backend now shows its error instead of fake data.
