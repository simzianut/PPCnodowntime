# 0020: λ is kWh per minute of added wait; the reward is normalised by always-on overhead

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS, spec 02 §3–4, config/sim.yaml

## Context
Spec 02's cost rule compared energy in joules with λ × wait minutes, so λ had no unit and the λ grid had no meaning.

## Decision
- λ is **kWh per minute of added wait**. The cost rule computes `E_save` in kWh.
- RL reward per slot and CP: `−(overhead_kwh + λ · wait_min_caused) / always_on_slot_kwh(power_class)`, where `always_on_slot_kwh = idle_w × slot_s / 3.6e6`.
- With the placeholder power numbers, a DC CP is worth deep-sleeping for one slot when P(arrival) < about 4% at λ = 0.1. The grid `[0.01 … 1.0]` stays.

## Alternatives considered
- Unitless λ on normalised terms: harder to explain, and the grid would need re-tuning.

## Consequences
Changing the power profiles shifts the break-even points. The λ grid may need a new decision after real power numbers arrive.
