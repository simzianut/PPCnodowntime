# Decision records

One file per decision: `NNNN-short-slug.md`. To get the next number, take the highest existing one and add 1. Rules are in [spec 00 §6](../specs/00-foundation.md#6-decision-logging-mandatory). Once merged, a decision file is never edited. To change a decision, write a new one that supersedes it.

## Template

```markdown
# NNNN: <title>

- Date: YYYY-MM-DD
- Who: <person>-<agent>
- Status: accepted | superseded by MMMM
- Supersedes: — | NNNN
- Affects: <specs / contracts / modules>

## Context
<What forced a choice? What did we know and not know?>

## Decision
<What we chose, stated plainly.>

## Alternatives considered
- <option>: <why not>

## Consequences
<What this makes easier or harder, and the risks or placeholders to come back to.>
```
