# 0024: Seeded synthetic data in sim/ until M1 and M3 deliver; provisional location IDs in fixtures

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 04, fixtures/api, T4, T6

## Context
No real sessions, signals or charge points exist yet. T4/T5 are unclaimed; M1's fixtures (decision 0015) cover only 5 invented locations over 2 days, too little for gym training or a national map. M4 must ship the engine, gym env and API fixture mode first.

## Decision
- `sim/synthetic.py` generates sessions, signals, context and a noisy-oracle forecast table in the exact contract schemas, from a seed, in memory. Nothing is written under M1's fixture names.
- Charge points come from a minimal reader of `data/seed/stations_open.csv`: real coordinates, CP count from the source or 2, power class from `current_types`/`max_kw`, context type and county code from simple rules.
- Location IDs are provisional: `L1` + the 1-based row number in `stations_open.csv`, zero-padded to 4 digits (`L10001`…). This avoids M1's fixture placeholders `L00001`–`L00005` (decision 0015) and M3's future numbering. `fixtures/api/` uses them.
- `sim.data` can also load M1's committed fixtures (`fixtures/*_sample.parquet` + `locations_sample_m1.csv`) as a second, smaller data bundle.

## Alternatives considered
- Wait for T4/T6: blocks M2 and M5.
- Commit generated fixtures under M1's names: writes into another module's contract.

## Consequences
When T4 assigns real location IDs, M4 regenerates `fixtures/api/` in one commit (noted on T4). Swap the loaders to `data/processed/` and `stations.store` once they exist.
