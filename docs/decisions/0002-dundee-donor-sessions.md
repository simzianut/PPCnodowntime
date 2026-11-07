# 0002: Usage history from Dundee (UK) sessions mapped onto Romanian charge points

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: spec 01, spec 02

## Context
No public usage history exists for Romanian stations. AmpWhere has no history and forbids scraping. A scraper started during the hackathon only collects a few hours of data.

## Decision
- Use Dundee City Council session data (Jul 2021 – Aug 2025, tagged AC / rapid / ultra-rapid) as the donor.
- Map it onto Romanian charge points by matching each one to a donor charger:
  - same power class
  - utilisation quantile closest to a demand index built from county EV density × context type
- Thin or top up the sessions to the target rate.
- Shift the dates onto the Romanian calendar, keeping local clock time and swapping holidays.
- Training period ends 2023-12-31; test period starts 2024-01-01.

## Alternatives considered
- Synthetic curves calibrated on several datasets: chosen first, then dropped in favour of real session timelines.
- Boulder (CC0): mostly AC Level 2, so weak for DC and highway stations.
- Copying donor timelines with no scaling: ignores how EV density differs between counties.

## Consequences
- The Dundee licence isn't stated, so raw files stay in `data/raw/` and are never committed or redistributed.
- UK behaviour stands in for Romanian behaviour. The live-status data checks how well the mapping holds.
