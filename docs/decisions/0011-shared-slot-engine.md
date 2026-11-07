# 0011: One 5-minute-slot engine for RL training, replay and live mode

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: spec 04, spec 02

## Decision
- One engine steps in 5-minute slots. Arrivals inside a slot keep second precision, so the wait can be measured.
- It is wrapped as a Gymnasium environment for PPO. The API drives the same engine for replay at speeds from 1× to 288× and for live mode.
- All policies run side by side on the same sessions and signals.

## Alternatives considered
- Event-driven simulation: more precise, but slower for RL and more code.
- Separate training and demo simulators: the two would drift apart.

## Consequences
- M4 must ship a version of the engine that works on the fixtures early, so M2 can start training.
