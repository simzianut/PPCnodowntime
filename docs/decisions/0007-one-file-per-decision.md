# 0007: One file per decision in docs/decisions/; DECISIONS.md becomes an index

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: AGENTS.md, spec 00

## Context
Many agents push in parallel, and they must document every decision. A single append-only file causes a merge conflict on almost every push.

## Decision
- One ADR-style file per decision: `docs/decisions/NNNN-slug.md`, using the template in that folder's README.
- `docs/DECISIONS.md` keeps one index line per decision, plus the entries that were there before this change.
- Logging decisions is mandatory for every agent (spec 00 §6).

## Alternatives considered
- A single `DECISIONS.md`: constant conflicts.
- A decision log per module: decisions that cross modules would get lost.

## Consequences
- Two agents can pick the same number. The later one renumbers when it rebases.
