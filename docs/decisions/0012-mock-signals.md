# 0012: Mocked PPC Blue and Maps signals come from ground truth in replay, from the forecast in live mode

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: spec 01, spec 02

## Context
- PPC Blue has no public API.
- Waze for Cities is only open to government agencies and road operators.
- Google's terms forbid training models on its data.

We mock these signals, but they must stay consistent with arrivals.

## Decision
**Replay.** Signals are derived from the ground-truth sessions:
- **PPC Blue route intent:** for 60% of sessions, 10–40 min before arrival, plus 10% false positives.
- **Maps EV count:** partial (25% of EVs are tagged) and noisy.
- **Combustion-engine count:** weight 0.05.
- **Traffic index:** a general traffic curve.

**Live mode.** Signals are sampled from the forecaster's expected arrivals, then checked against the statuses actually observed.

All parameters are in `config/signals.yaml`.

## Alternatives considered
- Independent feeds: the signals would barely correlate with arrivals, and the comparison would be unfair.

## Consequences
- The value of the PPC Blue signal depends on the parameters we assume. The pitch must state them.
