# 0070: Live mode runs the F2 agent in shadow on real AmpWhere statuses

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS (POST /api/runs live, live KPIs), spec 04 §6, api/engine_mode.py, model/gru_forecaster.py

## Context
Live mode (spec 04 §6) returned 400 until T12. The team wants the F2 agent acting on today's real statuses now. We do not control real chargers (PRD non-goal), and live data has no future sessions and no real PPC Blue or Maps feeds.

## Decision
- **Shadow mode:** a live run steps once per wall-clock 5-minute slot (Europe/Bucharest). Each slot it:
  1. reads the latest status per CP from the collector's JSONL (`data/raw/live/statuses-YYYYMMDD.jsonl`, matched to `cp_id` via `source_refs`; the DB `live_status` table when it is available)
  2. maps CHARGING → CHARGING, OFFLINE → OFFLINE, and AVAILABLE/UNKNOWN → IDLE unless the agent has put the CP to sleep
  3. runs the F2 forecaster online, the policies, and ACT/RECOMMEND exactly as in replay
- **Arrivals** are AVAILABLE → CHARGING transitions. An arrival at a CP the agent had asleep counts as an added wait of that state's wake time (10 s standby, 180 s deep sleep). Energy is counted against always-on with the same power profiles. KPIs and WS ticks keep the replay shapes.
- **Signals:** mocked PPC Blue intents and Maps EV counts are sampled with `model.live_signals` (T15) from the **F0 seasonal rate** (`artifacts/f0_rates.parquet`), not from F2's own forecast, so the agent does not feed on its own predictions. Traffic is the live TomTom index when available, else the Mapbox typical profile.
- **F2 online:** each CP keeps a rolling 36-slot feature history. At run start the history is empty and masked, the same as the zero-padded start of day in training. The features that need long history (`arrivals_same_slot_4w`) use what the live JSONL history has, else 0 (0059 live-mode gap).
- `oracle` and `naive_quiet_hours` with future knowledge stay replay-only.

## Alternatives considered
- Sample live signals from F2's own forecast (as T15 does with F1): a feedback loop that confirms its own predictions.
- Wait for a real PPC feed: not available during the hackathon.

## Consequences
- Live KPIs are shadow estimates ("what the agent would have saved today"), not measured savings, and must be labelled that way.
- Live quality depends on the collector running (`ppc-collector`, T9/T35).
