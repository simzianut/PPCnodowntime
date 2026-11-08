# 0089: Report a recommendation's expiry in the step that reaches its `expires_at`

- Date: 2026-11-08
- Who: teodora + teodora-claude
- Status: accepted
- Supersedes: — (refines how decision 0025's TTL is reported; the TTL itself is unchanged)
- Affects: `sim/engine.py` (`Engine.step`), `sim/tests/test_engine.py`, T52

## Context
- In a RECOMMEND replay the API pauses for new recommendations. The UI then approves what `GET /api/recommendations` lists as pending, which is the API's copy, updated from each `StepResult`.
- `Engine.step` expired recommendations only at the *start* of the next step. After the step that ends at a recommendation's `expires_at`, the engine was already at that time, so `decide_recommendation` refused it (`expires_at <= now`). The API copy still said pending until the next step.
- Driving the app on the 671-CP network in RECOMMEND mode, 1,425 of 1,435 approvals returned **409 "recommendation has expired"**. The UI showed each one as an error.

## Decision
- At the end of every step, `Engine.step` expires the recommendations whose `expires_at` is at or before the new slot boundary, and returns them in that step's `new_recommendations`. At the last slot it still expires everything, as before.
- Nothing changes in which recommendations can be approved, or until when. Only the *report* of the expiry moves one step earlier, so a pending entry on the API is always one the engine accepts.
- Tests: three engine assertions move to the new timing, a new engine test checks that a caller tracking `StepResult`s never holds a stale pending, and `api/tests/test_recommend_replay.py` approves the oldest listed recommendation at each pause and expects no 409. That API test fails on the old engine.

## Alternatives considered
- Let the API re-read `engine.recommendations()` after each step. It's O(all recommendations) per step and duplicates the engine's job.
- Make expiry strict (`expires_at < now`). That gives a one-slot-longer TTL than decision 0025 describes, and approvals at `expires_at` would apply after it.

## Consequences
- `StepResult.new_recommendations` can contain expirations caused by the step's own end time.
- ACT-mode results are unchanged, since it creates no recommendations.
