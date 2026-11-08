# 0093: No mocked inputs in the live and collected-day decision path

- Date: 2026-11-08
- Who: tudor-claude
- Status: accepted
- Supersedes: 0070 (the `LIVE_SIGNALS=mock` default only), 0090 (the "signals are mocked from the F0 rates" part only)
- Affects: CONTRACTS (`LIVE_SIGNALS`, collected-day replay), spec 02 §5, spec 04 live mode, `api/live_mode.py`, `api/collected_replay.py`

## Context
The demo has to show genuine decisions taken by the model, with nothing mocked. Two paths still fed it invented inputs:
- `LIVE_SIGNALS` defaulted to `mock`, so live runs got PPC Blue route intents and Maps EV/ICE counts sampled from `artifacts/f0_rates.parquet` (0070). The collected-day replay (0090) always sampled them the same way. There is no real PPC Blue or Maps feed (0012), and `cost_rule`'s intent override (wake a CP when an intent's eta is close) fired on those samples.
- `live_forecaster` silently fell back to `ConstantForecaster` when the GRU was missing or failed to load, so the "agent" could run on constant probabilities with nothing but a log line to show for it.

## Decision
- `LIVE_SIGNALS` defaults to `off` in `api/live_mode.py`, `docker-compose.yml` and `.env.example`, and the collected-day replay follows it too. Only the exact value `mock` opts in; anything else is `off`. With `off` the `SignalFeed` returns an empty frame every slot, so `SimState.signals` is empty: `cost_rule` sees no intents (override never fires) and the PPO observation gets zero intents, EV flow and traffic. Both run infos report `"signals": "off"`.
- `live_forecaster` raises when the model file is missing or fails to load. Live and collected run creation turn that into a 500 with the reason (`live forecaster ... is missing; no stub is used`). A missing `f2_live_calibration.json` (or one fitted for another model) is logged as a warning; the GRU still runs, uncalibrated.
- `cost_rule`'s empty-forecast fallback to `ConstantForecaster` stays. It cannot fire in live or collected runs: their engine always has the GRU forecaster, which returns one row per CP in the state, so `state.forecast` is set every slot. It remains reachable only in replays without `forecasts.parquet`.
- `typical_traffic` (missing `data/processed/traffic_typical.parquet` -> neutral index 0.5) now logs a warning. The file is not built in our data folder, so the traffic index is not real either. It feeds only the `mock` signal sampler, so with the default `off` it never reaches a policy.

## Alternatives considered
- Keep `mock` as default and label it in the UI: the decisions would still be driven by invented intents.
- Drop the mock sampler entirely: it is still useful to demo the intent override, and `model.live_calibration` uses `SignalFeed` for its with-signals replay, so it stays as an opt-in.
- Fall back to `f2_gru.pt` -> stub chain: a stub gives no genuine decision, so failing loudly is better.

## Consequences
- The live and collected-day agent (`rl_ppo` -> GRU + cost rule, 0069) decides only from real AmpWhere statuses, the calendar and the trained `f2_gru_live.pt` with its real-data calibration.
- Still not real, and documented here: (1) `live_model_path` falls back to `f2_gru.pt` when `f2_gru_live.pt` is absent; that model was trained with mocked signal features, which are then all zero. (2) `cost_rule` defaults to p15/p30/p60 = 0.05/0.10/0.20 for a CP missing from the forecast frame; the GRU never omits one. (3) If `RL_AGENT` selects the PPO policy, its observation has zero intents, EV flow and traffic, which differs from its training distribution.
- A deployment without the GRU artifact can no longer start live or collected runs; that is intended.
