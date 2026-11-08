# Reference data/processed manifest

SHA-256 of the reference `data/processed/` used for the demo on 2026-11-08 (branch tudor-claude/T54-real-data-demo). The data is private and never committed (AGENTS.md hard rules). Check a copy with `cd data/processed && shasum -a 256 -c` against the table, or compare by hand.

Files built by `ppc-seed` (`python -m stations`) from the AmpWhere snapshot: `locations`, `charge_points`, `source_refs`, `other_chargers`. Their hashes match only when built from the same snapshot and OSM cache.

| File | SHA-256 |
|---|---|
| `charge_points.parquet` | `98c3f8e34b9ecc66b8dc2bbd61d897704710db3ca0b3d1deba6797ba7f074907` |
| `context.parquet` | `c4a43cd407498231b9e2db03ffb59e07906694e997c634aa5407efb70efafe29` |
| `cp_active_days.parquet` | `f2a8023f820f5479d4c28c8bc921937d6e0ddb2d1718a673fb366111ebd37158` |
| `cp_donor_map.parquet` | `428228ecc84549f6c1161a67b16f6c25fd123172a7505ea62ea77006039f0e06` |
| `donor_chargers_cps.parquet` | `3ea119268ffef7e63a9eccaf538af1c3f9ef075fc5b298af80195c924d25eed0` |
| `donor_chargers.parquet` | `8e653fd8691f46434b90323841c122045f32294adb76004ff34d0b754a8a40da` |
| `donor_occupancy_cps.parquet` | `5ea7fec5000e79d7e8101ea1561f17b7f14d1b7ecbbeec59cf5889c7a9e7ece5` |
| `donor_occupancy.parquet` | `c4f2eb7fd69382bb2fb1a0c71b1e4675d9bd225c8ec7dc39c304fa90de8472b5` |
| `donor_sessions_cps.parquet` | `f03112129bfe907d1714d4a6db842c72a3faaaf13ffcc5d92268f4aee48dc9c2` |
| `donor_sessions.parquet` | `1028eb7bb5fc8663dc8af19c4b1094f9ddc11914011180dd88c74d5baf718297` |
| `forecasts.parquet` | `4278d385009971a6903e69d6c71918aff3f6cc4da5820318976ea87cf5fc6cde` |
| `locations.parquet` | `50bbd30d60e9870bfa25cc0bf661b23af622d3ddf4e15ab682ce79e455db95f7` |
| `other_chargers.parquet` | `fce34d59c489a870d755aa6f28fe459fd65c8092e1d720d9cbdfc0010ed987f4` |
| `sessions.parquet` | `e1b38a7fe93af0a2fc0bc805a4f453a8848391a8e4619de3bef0013de37d2483` |
| `signals.parquet` | `83e0a6cffa5b6ac3a9e00655fe172ce4c289ba47775e83f08a19d0bc6e0bf181` |
| `source_refs.parquet` | `a06900f98657414009e2e573170c4bc1bd2248607d629105d8c4bdae8c25145d` |
| `ppc_charge_points.csv` | `598b7ea38f00b2e28b66facb4560e94215313dd8be4c43f8ab14770b72ffa178` |
| `ppc_locations.csv` | `323ec098c6495204a52c6dedee69d0b68f213b0ca2997cec8fb2c9204b76aa1a` |
