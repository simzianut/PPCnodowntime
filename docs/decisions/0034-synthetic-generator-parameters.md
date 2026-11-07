# 0034: Parameters of the synthetic simulation data

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: sim/synthetic.py, decision 0024

## Context
Decision 0024 adds a seeded generator so M4 and M2 can work before real data exists. Its numbers are invented and must be visible as such.

## Decision
All values are placeholders, used only by `sim.synthetic` (never by M1's pipeline):
- Default window: 14 train days from 2023-10-02, 7 test days from 2024-03-11, all 116 seed locations (~308 CPs).
- Arrival rates: DC 4–10 per day, AC 2–5, scaled by `context_multiplier` from `config/signals.yaml`. 20% of CPs are "quiet" at 0.2–1 per day.
- Day of week: urban Saturday ×0.75 and Sunday ×0.65; retail Saturday ×1.35; highway Friday and Sunday ×1.2. Romanian public holidays ×0.6–0.8.
- Daily shapes: urban peaks ~08 and ~18, retail 10–20, highway 06–22, airport mixed, other midday.
- Durations: AC 1–4 h, DC_50 20–50 min, DC_HPC 15–35 min. Power as decision 0015. Overlapping sessions on a CP are dropped.
- Signals: spec 01 §5 with `config/signals.yaml`; `background_ev` 2.0 and `ice_base` 40.0 while the config is null (as decision 0015). False-positive intents copy a true intent's location, shifted ±2–6 h.
- Weather: seasonal sinusoid with a daily cycle, rain on ~30% of days, gamma wind.
- Oracle forecast: `p_H = 0.7·truth + 0.3·(1 − e^(−rate·H)) + N(0, 0.03)`, clipped to [0.001, 0.999] and monotone across horizons; `expected_arrivals_60 = 0.7·count + 0.3·hourly rate + N(0, 0.15)`.
- Each day is 288 slots of absolute time from local midnight, the same as the engine.

## Alternatives considered
- Use M1's fixtures only: 5 invented locations × 2 days, too little for gym training or a national map.

## Consequences
- The oracle forecast is much better than a real one will be. Results on synthetic data say nothing about real savings.
- The signals frame is about 150 MB with the defaults. Pass a smaller `locations` subset in tests.
