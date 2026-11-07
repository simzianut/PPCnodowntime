"""`python -m dataset` runs every dataset step in order."""

from __future__ import annotations

import json

from dataset import dundee


def step_dundee_download() -> dict:
    cfg = dundee.load_config()
    return {"files": [p.name for p in dundee.download(cfg, dundee.data_dir() / "raw" / "dundee")]}


def step_dundee_build() -> dict:
    rep = dundee.build()
    return {k: v for k, v in rep.items() if k != "lanes_per_cp"}


# Append later steps here: county stats, mapping, signals, context.
STEPS = [
    ("dundee.download", step_dundee_download),
    ("dundee.build", step_dundee_build),
]


def main() -> None:
    summary = {}
    for name, fn in STEPS:
        print(f"== {name}")
        summary[name] = fn()
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
