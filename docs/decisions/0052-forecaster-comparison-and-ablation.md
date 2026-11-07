# 0052: Compare three forecasters, plus a feature ablation and a signal sensitivity suite

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 02 §2, model/, config/train/

## Context
The challenge asks for our own trained model, and the pitch claims that EV-specific signals beat plain traffic. LightGBM (0032, 0050) is a single model with no baseline and no ablation. Arrivals are rare (P(arrival in 30 min) is about 1% AC, 3% DC_50, 6% HPC), so probability quality matters more than accuracy.

## Decision
- **F0 seasonal baseline:** per-CP hour-of-week arrival rate from the training period, shrunk towards the power_class × context mean. Then `p_H = 1 − exp(−rate × H)`. It is what a fixed schedule implicitly uses, so ML must beat it.
- **F1 LightGBM:** the existing model (0032, 0050), wrapped as a reproducible run. Train ≤ 2023-12-31, test 2024–2025, calibration slice = the last months of the training period, inactive CP-days excluded (data README).
- **F2 GRU:** a small recurrent net over the last 3 h of 5-minute slots. It has static embeddings (power_class, context, county EV density, CPs at location) and a discrete-time survival head over twelve 5-minute buckets. `p15 ≤ p30 ≤ p60` holds by construction. A Poisson head gives `expected_arrivals_60`.
- **Shared metrics, same test set for all:** AUC, PR-AUC, Brier score, log loss, reliability bins, per horizon and per power_class. Each run also writes `forecasts.parquet`, so the cost rule can be evaluated with any forecaster (T11).
- **Ablation** (feature steps):
  1. calendar + history
  2. \+ `maps_traffic_index`
  3. \+ EV-weighted flow
  4. \+ PPC Blue intents

  Steps 1–4 run on signals v3.
- **Sensitivity:** step 3 is rerun on `signals_evtag01.parquet` (EV tag rate 0.1) and step 4 on `signals_papp03.parquet` (intent share 0.3). Steps 1–2 stay on v3, because the variants' traffic noise differs slightly (shared RNG).
- **Pitch framing:** steps 1–2 are measured value. Steps 3–4 are value under our mock assumptions, because `maps_ev_count` and intents are drawn from future true arrivals by design (0039, data-agent check on 2026-11-07).

## Alternatives considered
- Transformer / Temporal Fusion Transformer: needs more data and tuning than the time allows, and the target is sparse.
- Spatio-temporal graph network (common on Dundee data): the links between Romanian stations come from how donors were mapped (0002, 0038), not from real drivers, so it would learn the mapping.

## Consequences
- The ablation numbers for steps 3–4 depend on `config/signals.yaml` and must be presented with those assumptions.
- F2 needs a GPU for reasonable training time. F0 and F1 run on a CPU.
