"""CLI: python -m model.experiments {run,suite,promote}."""

from __future__ import annotations

import argparse

from model.experiments import runner, suite


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m model.experiments")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one training run")
    r.add_argument("config")
    r.add_argument("--seed", type=int)
    r.add_argument("--set", dest="sets", action="append", default=[], metavar="key.path=value")
    r.add_argument("--overwrite", action="store_true")
    s = sub.add_parser("suite", help="several runs plus summary table")
    s.add_argument("suite")
    s.add_argument("--continue-on-error", action="store_true")
    p = sub.add_parser("promote", help="copy a run's model into artifacts/")
    p.add_argument("run_dir")
    p.add_argument(
        "--as", dest="rename", default=None, help="artifact file name, e.g. f2_gru_live.pt"
    )
    e = sub.add_parser("ensemble", help="average several F2 runs into one run dir")
    e.add_argument("run_dirs", nargs="+")
    e.add_argument("--name", required=True)
    e.add_argument("--calibration", default="auto")
    from model import gru_forecaster

    q = sub.add_parser("predict", help="F2 forecasts.parquet for a range of days")
    gru_forecaster.add_predict_args(q)
    a = ap.parse_args(argv)

    if a.cmd == "run":
        overrides = dict(runner.parse_override(x) for x in a.sets)
        cfg = runner.resolve_config(a.config, a.seed, overrides)
        d = runner.run_config(cfg, overwrite=a.overwrite)
        print(f"run dir: {d}")
    elif a.cmd == "suite":
        suite.run_suite(a.suite, a.continue_on_error)
    elif a.cmd == "predict":
        gru_forecaster.run_predict(a)
    elif a.cmd == "ensemble":
        from model.experiments import ensemble

        print(f"run dir: {ensemble.build(a.run_dirs, a.name, a.calibration)}")
    else:
        for f in suite.promote(a.run_dir, a.rename):
            print(f"promoted {f}")


if __name__ == "__main__":
    main()
