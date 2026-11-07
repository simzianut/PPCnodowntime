# 0010: Train on donor weather and holidays, run on Romanian weather and holidays

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: spec 01, spec 02

## Context
The donor sessions happened in Dundee's weather and around UK holidays, but the agent runs in Romania.

## Decision
- **Training features:** Open-Meteo historical weather at Dundee, and GB-SCT holidays. This keeps the real cause and effect.
- **Inference and simulation:** Open-Meteo weather at each Romanian county centroid, and Romanian holidays.

## Alternatives considered
- Romanian weather attached to the remapped sessions: the learned weather effect becomes noise.
- No weather: loses a realistic signal.

## Consequences
- Romanian weather is more extreme than Dundee's, so the model is extrapolating there. This is a known limitation.
