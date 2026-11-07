# 0030: County EV density from DRPCIV end-2025 + INS 2026; Bucharest and Ilfov share one pooled rate

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 01 §7, CONTRACTS (`county_ev_stats.csv` extra columns)

## Context
- `county_ev_density` drives the demand index in the donor mapping (spec 01 §4).
- **Source:** the data.gov.ro "parc-auto-romania" fuel file at 31.12.2025 (OGL-ROU-1.0).
  - Electric cars counted as M1 + `ELECTRIC`: **61,417** nationally.
  - Population: INS TEMPO POP105A, resident population at 1 Jan 2026 (19,041,322).
- Bucharest has 13.2 EVs per 1,000 people: 37% of all EVs and 2.6× Cluj. Leasing and fleet companies likely register cars at Bucharest addresses (an assumption, not verified).

## Decision
- **Fleet counts:** the parc-auto file is downloaded by `python -m dataset.county_stats`. Rows are summed (`TOTAL_VEHICULE`) for `CATEGORIE_COMUNITARA = M1` and `VALUE_NAME = ELECTRIC`. Hybrids (`HIBRID 01–06`) and M1G are excluded.
- **Population:** committed as `data/seed/ro_county_population.csv` (public statistics).
- **Pooling:** Bucharest (B) and Ilfov (IF) share one pooled rate, (ev_B + ev_IF) / (pop_B + pop_IF) × 1000 = **11.041**. Ilfov is Bucharest's commuter belt.
- **Columns:**
  - `ev_count` and `population` stay each county's own values.
  - `ev_per_1000` is the pooled rate for B and IF, and the raw rate for every other county.
  - Extra columns: `ev_per_1000_raw` and `pool_group`.

## Alternatives considered
- Cap Bucharest at 2 × the next county: the factor would be a guess.
- Leave it at 13.2: Bucharest chargers would get the busiest donors, partly because of leasing registrations.

## Consequences
- B and IF are still about 2× Cluj, the next highest at 5.11. The lowest are Călărași 0.69, Ialomița 0.70 and Vaslui 0.73.
- The count is by registration county, not where cars are driven.
