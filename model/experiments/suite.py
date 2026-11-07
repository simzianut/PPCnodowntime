"""Suites (several runs + summary table) and promote (run dir -> artifacts/)."""

from __future__ import annotations

import csv
import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from model import repro
from model.experiments import runner
from model.experiments.registry import get_trainer


def _dig(d: dict, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def run_suite(suite_path, continue_on_error: bool = False) -> Path:
    suite = yaml.safe_load(Path(suite_path).read_text())
    cols = list(suite.get("summary_metrics", []))
    sha8 = runner.config_sha256(suite)[:8]
    out_dir = repro.runs_dir() / "suites" / f"{suite['name']}-{sha8}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, run_ids, failed = [], [], []
    for entry in suite["runs"]:
        overrides = entry.get("set") or {}
        for seed in entry.get("seeds") or [None]:
            cfg = runner.resolve_config(entry["config"], seed, overrides)
            run_id = runner.make_run_id(cfg)
            run_ids.append(run_id)
            if runner.run_status(run_id) == "ok":
                print(f"skip {run_id} (already ok)")
            else:
                try:
                    runner.run_config(cfg, overwrite=True)
                except Exception as exc:
                    if not continue_on_error:
                        raise
                    failed.append(run_id)
                    print(f"FAILED {run_id}: {exc}")
                    continue
            mpath = repro.runs_dir() / run_id / "metrics.json"
            metrics = json.loads(mpath.read_text()) if mpath.exists() else {}
            rows.append(
                {
                    "run_id": run_id,
                    "config": cfg["name"],
                    "seed": cfg["seed"],
                    "overrides": json.dumps(overrides, sort_keys=True),
                    **{c: _dig(metrics, c) for c in cols},
                }
            )
    header = ["run_id", "config", "seed", "overrides", *cols]
    with (out_dir / "summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    md = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    md += ["| " + " | ".join(str(r[h]) for h in header) + " |" for r in rows]
    (out_dir / "summary.md").write_text("\n".join(md) + "\n")
    (out_dir / "suite.json").write_text(
        json.dumps({"name": suite["name"], "run_ids": run_ids, "failed": failed}, indent=2)
    )
    print(f"suite summary: {out_dir / 'summary.md'}")
    return out_dir


def promote(run_dir) -> list[Path]:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if manifest.get("status") != "ok":
        raise RuntimeError(f"{manifest.get('run_id')} has status {manifest.get('status')}, not ok")
    cfg = yaml.safe_load((run_dir / "config.yaml").read_text())
    spec = get_trainer(cfg["model"])
    dest = Path(os.environ.get("ARTIFACTS_DIR", "artifacts"))
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for src, name in spec.promote.items():
        shutil.copy2(run_dir / src, dest / name)
        written.append(dest / name)
        prov = {
            "run_id": manifest["run_id"],
            "config_sha256": manifest["config_sha256"],
            "git_sha": manifest.get("git_sha"),
        }
        (dest / f"{Path(name).stem}_provenance.json").write_text(json.dumps(prov, indent=2))
    return written
