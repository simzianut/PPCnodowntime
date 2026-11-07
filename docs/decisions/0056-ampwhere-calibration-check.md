# 0056: Calibration check against real PPC activity: report it, no rescale yet

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 01 §8 (validation), spec 02 §2 (live validation), the pitch

## Context
- The reconstructed sessions (0002, 0038) had never been checked against real Romanian data.
- By 2026-11-07 the AmpWhere collector (0016) had about 3 h of real PPC Blue statuses: Saturday 19:20–22:40 Romanian time.
- `python -m dataset.calibration` counts unit-level charging starts, i.e. a CP going from 0 to ≥ 1 charging connector.
- It compares them with the reconstructed arrivals in the same weekday and local window, on the 17 November Saturdays in the sessions. Only active CP-days are counted, scaled up to all CPs of the class.

## Result

| Class | Real | Reconstructed mean | Reconstructed p10–p90 | Real ÷ reconstructed |
|---|---|---|---|---|
| AC_22 | 26 | 19.7 | 0–67 | 1.32 |
| DC_50 | 56 | 77.0 | 28–117 | 0.73 |
| DC_HPC | 40 | 86.6 | 22–137 | 0.46 |

Overall, the reconstruction shows about 50% more demand than this evening. Most of the gap is HPC, about 2×. The likely cause is that Dundee's 3 ultra-rapid chargers are its busiest. Every real value is inside the reconstruction's day-to-day p10–p90 range.

## Decision
Decided with the user:
- **Report it; don't rescale yet.** It's one evening, and a rescale would force everyone to retrain.
- The numbers are a validation result for the pitch: *"reconstruction is within the normal range of real PPC activity; HPC is probably overstated about 2×"*.
- Re-run `python -m dataset.calibration` as more live data accumulates. A per-class rescale (v4) is the follow-up if the gap holds over more hours and days.

## Alternatives considered
- Per-class rescale now (×1.32 / ×0.73 / ×0.46): based on one evening.
- HPC-only rescale: same objection, smaller impact.

## Consequences
- Energy-saving results for HPC may be computed on busier-than-real chargers, which makes them conservative for sleeping. The pitch should say so.
- Caveats: AmpWhere polls every 5 min, so very short sessions can be missed. The reconstruction reflects 2021–25 behaviour, while the live data is from November 2026.
- The report goes to `data/processed/reports/calibration_ampwhere.json` (local; derived from AmpWhere, so not committed).
