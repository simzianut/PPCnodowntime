# Live run 2026-11-07/08 (overnight): manifest

The AmpWhere dataset the pitch evaluation uses. The data itself is **not in the repo** (AGENTS.md hard rules, decision 0003). This file records exactly what it is, so anyone holding the archive can check they have the same data.

## Archive

| | |
|---|---|
| File | `ampwhere-live-20261107T2232Z-20261108T0925Z.tar.gz` (7.1 MB), held privately by tudor |
| SHA-256 | `6c285de9ad5b39a8d5ea5a09b806951d313a0fc185bb9f0b6b7da8b7b5d2f9cb` |
| Restore | `tar -xzf <archive> -C data/raw/` gives `data/raw/live/` |
| Contents | `statuses-*.jsonl`, `raw/` (126 raw AmpWhere responses), `ampwhere_snapshot_latest.json.gz`, `collector.log`, `imputed/` |

| File in `live/` | Rows | SHA-256 |
|---|---:|---|
| `statuses-20261107.jsonl` | 23,327 | `6fe64b17b7680f44267696be6cfad8e364f1134a67092f2a34b1d610c8ab7911` |
| `statuses-20261108.jsonl` | 93,276 | `5985fc5f2d740597bbd70421ca8303fb7cd7e248c09241839774cb87f2616602` |
| `imputed/gap-20261108T0330-0416.jsonl` | 205 (synthetic) | `ac0b9a2865a117c682011b33c56f2549a16587d8715f756cf408a7e5d4513b39` |
| `imputed/gap-20261108T0330-0416.meta.json` | — | `6cabd7728dba396f798d6692f57a64e9a29d3fa91809ed35d642d290a3a9acb0` |
| `ampwhere_snapshot_latest.json.gz` | — | `f30d197ecdc133b25764e6d2de5f0309864b72f4bc5568ab19f08f33751869a1` |

## Run

- **Window:** 2026-11-07 22:32:55 to 2026-11-08 09:25:02 UTC (00:32 to 11:25 Romanian time), about 10 h 52 min.
  - The planned window was 8 h, extended to 10 h. The stop timer was killed with its shell, so the collector ran 53 minutes longer until it was stopped by hand.
- **Polls:** 123 succeeded, 2 failed. 3 were snapshots (22:32, 05:15, 05:25), the rest deltas. Observed rows: 116,603.
- **Coverage:** every operator in Romania, 15,833 connectors at 6,007 locations. PPC Blue (provider 5) has 1,364 connectors.
- **PPC charging:** from 32 to 140 connectors over the window. Per-poll counts are in [2026-11-07-overnight-ppc-counts.csv](2026-11-07-overnight-ppc-counts.csv).

## Gaps (consecutive successful polls more than 360 s apart)

| UTC | Length | Cause | Effect |
|---|---|---|---|
| 23:25:04 → 23:31:18 | 6 min | network drop, retried | none |
| 02:55:11 → 03:05:02 | 10 min | disk full, poll at 03:00 failed | changes from 02:55 to 03:05 are timed at 03:05 |
| 03:30:03 → 04:16:17 | 46 min | laptop asleep with the lid closed; a DNS failure at 04:14 delayed the poll on waking | about 170 changes (2–5 PPC) collapsed into the 04:16 poll. Imputed separately (decision 0082). |

Score on observed rows only and treat each gap as a single coarse step ([LIVE_DATA.md §7](../LIVE_DATA.md#7-gaps-and-imputation)).
