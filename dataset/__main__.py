"""`python -m dataset` runs every dataset step in order."""

from __future__ import annotations

import json

from dataset import context, county_stats, dundee, mapping, signals


def step_dundee_download() -> dict:
    cfg = dundee.load_config()
    return {"files": [p.name for p in dundee.download(cfg, dundee.data_dir() / "raw" / "dundee")]}


def step_dundee_build() -> dict:
    rep = dundee.build()
    return {k: v for k, v in rep.items() if k != "lanes_per_cp"}


def step_county_stats() -> dict:
    return county_stats.build()


def step_mapping() -> dict:
    rep = mapping.build()
    keys = ["sessions_total", "sessions_by_class_split", "donor_fallback_count", "capped_count"]
    return {k: rep[k] for k in keys}


def step_context() -> dict:
    return context.build()


def step_signals() -> dict:
    return signals.build()


STEPS = [
    ("dundee.download", step_dundee_download),
    ("dundee.build", step_dundee_build),
    ("county_stats", step_county_stats),
    ("mapping", step_mapping),
    ("context", step_context),
    ("signals", step_signals),
]


def main() -> None:
    summary = {}
    for name, fn in STEPS:
        print(f"== {name}")
        summary[name] = fn()
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
