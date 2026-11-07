# 0001: RL agent (PPO) driven by a demand forecaster, with a cost-rule fallback

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: spec 02, spec 04, PRD

## Context
The pitch is "we train an agent". The options were:
- a forecaster plus a cost rule
- an RL agent
- an LLM agent
- pure heuristics

We have under 24h. RL is risky in that time but tells the strongest story.

## Decision
- A LightGBM forecaster predicts EV arrivals per charge point.
- A PPO agent (Stable-Baselines3) observes those forecasts plus signals and state, and picks IDLE, STANDBY or DEEP_SLEEP for each charge point. One shared policy is used for all charge points.
- A cost rule using the same forecaster is the fallback and the comparison.
- The forecaster and the agent both live in Module 2.

## Alternatives considered
- Forecaster + cost rule only: safe, but the ML story is weaker.
- RL on raw signals with no forecaster: slower to learn, and the pipeline is harder to debug.
- One global agent controlling every charge point: the action space is too large.
- LLM agent: doesn't improve the energy results. It stays a stretch explainer.

## Consequences
- M4 must deliver the Gymnasium environment early.
- During training, a charge point's siblings follow the cost rule. This is an approximation.
- If PPO is dominated by the cost rule, the PRD claims are based on the better policy, and a decision file records which one.
