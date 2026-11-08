# 0082: Impute the 03:30–04:16 UTC live-collection gap on 2026-11-08 with a labelled Markov bridge

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 01 §8 (live JSONL), the pitch evaluation on the overnight AmpWhere run

## Context
- The overnight collector run (decisions 0003, 0016) lost 46 minutes when the laptop went into lid-closed sleep on battery: no polls from 03:30:03 to 04:16:17 UTC.
- The 04:16 delta recovered every charger's final status (199 real changes, 14 PPC), but not the changes inside the window or their timing.
- A normal 46-minute window just before (02:45–03:30) had 366 real status changes (16 PPC), so roughly 170 changes, about 2–5 of them PPC, are missing.
- A delta row is not always a status change. Most rows re-report an unchanged status.

## Decision
- Fill the gap with **synthetic, labelled** rows in `data/raw/live/imputed/gap-20261108T0330-0416.jsonl`, plus a `.meta.json` with the method, seed and transition matrices.
- **Method:** a discrete-time Markov bridge on 5-minute slots (03:35 … 04:10).
  - Transition matrices over {CHARGING, AVAILABLE, OFFLINE, UNKNOWN} are fitted on consecutive 5-minute polls from 00:30 to 03:30 UTC. PPC Blue and other operators get separate matrices, with a pseudocount of 0.5 per cell.
  - Each charger's path is sampled conditioned on its observed state at 03:30 and at 04:16. Rows are emitted only on change. Seed 20261108.
- Every imputed row has `source: "imputed"`, `imputed: true`, `imputation: "markov-bridge-v1"`, and `raw_status` and `source_updated_at` set to null.
- The file sits outside the `statuses-*.jsonl` pattern, so readers must opt in to load it.
- **Evaluation:** score the model on observed data only. Imputed rows may be used for continuity in visualisations, and must be shown as imputed. The pitch must not call 03:30–04:16 observed.
- Result: 205 rows, 13 of them PPC.

## Alternatives considered
- Leave the gap (one 46-minute step): honest but leaves a hole in the replay timeline. Still the default for scoring.
- Write imputed rows into the raw `statuses-*.jsonl`: makes synthetic data look observed. Rejected.
- Linear or last-value interpolation: puts every net change at one arbitrary time and gives no excursions.

## Consequences
- The bridge under-produces short flaps (205 changes against about 366 expected), so the imputed stretch is smoother than reality.
- Matrices fitted on 00:30–03:30 assume the same night-time dynamics hold until 04:16, which is reasonable before the morning ramp.
- The script used is a one-off and is not in the repo. Its method is fully described here and in the `.meta.json`.
