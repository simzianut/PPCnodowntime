# 0081: Live demo clock starts per run and names itself in errors

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: —
- Affects: api/live_mode.py, api/tests/test_live_mode.py, decision 0072 (demo clock)

## Context
T45: with `LIVE_CLOCK_START`/`LIVE_CLOCK_SPEED` set, new live runs reportedly got 400 "live feed is stale" a few minutes after the API started, as if one clock were shared since startup. The code builds a new clock per run (`EngineManager.live_clock` holds `clock_from_env` uncalled; `create_live_run` calls it on every POST), so this did not reproduce. The likely cause was `ppc-api` running without `LIVE_CLOCK_START`: compose only passes it since 1ab9c0b, and a container keeps its old env until it is recreated. Without it the clock is real time, and real time is stale when the collector is off.

## Decision
- Keep the per-run clock: each live run starts at `LIVE_CLOCK_START` when it is created and runs at `LIVE_CLOCK_SPEED` from there. A regression test creates two runs 8 (fake) minutes apart and checks the second starts at START.
- Clocks describe themselves (`real time` or `demo from <START> at <SPEED>x`). The description is in the "live feed is stale" 400 detail and in the run's `info` (logged on creation), so a missing env var shows at once.

## Alternatives considered
- One shared clock from API start: every run after the first would start later in the replayed day and soon go stale. That is the reported bug.
- Logging the clock only at API startup: easy to miss; the 400 is what the user sees.

## Consequences
After changing `.env`, recreate `ppc-api` (`docker compose up -d ppc-api`) and check with `docker compose exec ppc-api env | grep LIVE`. The 400 detail now says which clock was used.
