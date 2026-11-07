# 0003: Collect live statuses from AmpWhere for the whole hackathon; no PPC Blue bot-wall bypass

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: spec 01, spec 03, spec 04

## Context
Live statuses let us check the donor mapping and power a live demo mode.
- AmpWhere's Terms of Service §4 forbid scraping or bulk downloading. Its `robots.txt` disallows AI crawlers.
- ppcblue.ro is behind an Imperva bot wall.

## Decision
- The team runs an AmpWhere poller every 5 minutes for the whole hackathon, knowingly accepting the ToS risk.
  - It uses a minimal request rate and stops on HTTP 403 or 429.
  - Raw data stays in `data/raw/` and is never committed.
  - AI coding agents whose operators are disallowed by AmpWhere's `robots.txt` must not run or fetch it themselves. A human runs it.
- For PPC Blue, we do **not** bypass the bot wall. We ask PPC for its official AFIR Art. 20 static and dynamic feed, and plug it in through `PpcOfficialAdapter`.

## Alternatives considered
- No live data: loses the live demo and the mapping check.
- Bypassing PPC's bot protection: that is circumventing an access control, not just breaching terms. Rejected.

## Consequences
- Legal and reputational risk in front of PPC if this is noticed. Mitigated by keeping the data out of the repo and switching to PPC's feed as soon as it is offered.
