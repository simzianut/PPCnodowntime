"""Evaluator: runs policies over test days and lambda grid, outputs eval_results.json (spec 04 §5)."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd

from model import rl_select
from model.cost_rule import CostRulePolicy
from model.forecaster import DemandForecaster
from model.oracle import HindsightOraclePolicy
from model.types import Forecaster, Policy, SimState
from sim.baselines import AlwaysOn, NaiveQuietHours
from sim.config import CONFIG_DIR, ROOT, sim_config
from sim.data import SimData, load_default
from sim.engine import ALWAYS_ON, Engine
from sim.forecast import PrecomputedForecaster
from sim.metrics import PolicyMetrics

log = logging.getLogger("sim.evaluate")
DEFAULT_OUTPUT = ROOT / "artifacts" / "eval_results.json"


class _SlotCachedForecaster(Forecaster):
    """Caches predictions within the same timestamp slot across sibling policies."""

    def __init__(self, inner: Forecaster) -> None:
        self.inner = inner
        self._last_t: object = None
        self._cached: pd.DataFrame | None = None

    def predict(self, state: SimState) -> pd.DataFrame:
        if state.t == self._last_t and self._cached is not None:
            return self._cached
        pred = self.inner.predict(state)
        self._last_t = state.t
        self._cached = pred
        return pred


class _PolicyWrapper:
    """Wraps policy with unique name for side-by-side execution in a shared Engine."""

    def __init__(self, inner: Policy, name: str) -> None:
        self.inner = inner
        self.name = name

    def decide(self, state: SimState) -> list:
        return self.inner.decide(state)


def _resolve_test_days(data: SimData, days_arg: str | None, cfg_eval: str | list[str]) -> list[date]:
    all_test = data.days("test")
    if not all_test:
        all_test = data.days()
    if days_arg:
        if days_arg.lower() == "all":
            return all_test
        try:
            n = int(days_arg)
            return all_test[:n]
        except ValueError:
            wanted = set(days_arg.split(","))
            return [d for d in all_test if d.isoformat() in wanted]
    if isinstance(cfg_eval, list):
        wanted = set(cfg_eval)
        matching = [d for d in all_test if d.isoformat() in wanted]
        if matching:
            return matching
    # Default to first 5 test days for fast, reliable evaluation
    return all_test[:5]


def _build_forecaster(data: SimData) -> Forecaster:
    if data.forecasts is not None and not data.forecasts.empty:
        return _SlotCachedForecaster(PrecomputedForecaster(data.forecasts))
    art_path = ROOT / "artifacts"
    if (art_path / "forecaster.lgb").exists() and (art_path / "forecaster_meta.json").exists():
        return _SlotCachedForecaster(DemandForecaster.load(art_path))
    from sim.forecast import ZeroForecaster
    return ZeroForecaster()


def evaluate(
    data: SimData | None = None,
    days: Sequence[date] | None = None,
    lambdas: Sequence[float] | None = None,
    output_path: Path | str | None = None,
) -> dict:
    """Runs all policies across test days and lambdas, returning the eval_results dict."""
    cfg = sim_config()
    data = data or load_default()
    test_days = list(days) if days is not None else _resolve_test_days(data, None, cfg.eval_days)
    grid = list(lambdas) if lambdas is not None else list(cfg.lambda_grid)
    log.info("rl_ppo served by %s", rl_select.resolve_rl_agent())  # bad RL_AGENT raises
    fc = _build_forecaster(data)
    naive = NaiveQuietHours(data.sessions, data.cps)

    runs: list[tuple[str, str, float | None, Policy]] = [
        ("always_on", "always_on", None, AlwaysOn()),
        ("naive_quiet_hours", "naive_quiet_hours", None, naive),
    ]
    for lam in grid:
        cr = CostRulePolicy(
            lambda_wait=lam,
            power_profiles_path=CONFIG_DIR / "power_profiles.yaml",
            sim_config_path=CONFIG_DIR / "sim.yaml",
        )
        runs.append((f"cost_rule_{lam}", "cost_rule", lam, cr))
        try:
            runs.append((f"rl_ppo_{lam}", "rl_ppo", lam, rl_select.load_policy(
                "rl_ppo", forecaster=fc, lam=lam)))  # fmt: skip
        except rl_select.PolicyUnavailable as e:
            log.info("rl_ppo skipped: %s", e)
    for extra in ("rl_maskppo", "rl_ddqn"):
        try:
            runs.append((extra, extra, None, rl_select.load_policy(extra)))
        except rl_select.PolicyUnavailable as e:
            log.info("%s skipped: %s", extra, e)
    runs.append(("oracle", "oracle", None, HindsightOraclePolicy(data.sessions, cfg.slot_s)))

    engine_policies = [_PolicyWrapper(p, key) for key, _, _, p in runs]
    acc: dict[str, PolicyMetrics] = {key: PolicyMetrics() for key, _, _, _ in runs}
    base_acc = PolicyMetrics()

    for d in test_days:
        eng = Engine(data, d, engine_policies, forecaster=fc)
        while not eng.done:
            eng.step()
        base_acc.merge(eng._sims[ALWAYS_ON].metrics)
        for key in acc:
            acc[key].merge(eng._sims[key].metrics)

    base_wh = base_acc.overhead_wh
    results = []
    for key, p_name, lam, _ in runs:
        kpis = acc[key].kpis(base_wh)
        results.append(
            {
                "policy": p_name,
                "lambda": lam,
                "overhead_kwh": kpis["overhead_kwh"],
                "saved_kwh": kpis["saved_kwh"],
                "saved_pct": kpis["saved_pct"],
                "wait_min_total": kpis["wait_min_total"],
                "wait_min_mean": kpis["wait_min_mean"],
                "wait_min_p95": kpis["wait_min_p95"],
                "sessions": kpis["sessions"],
                "waited_sessions": kpis["waited_sessions"],
                "sleep_share": kpis["sleep_share"],
            }
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_days": [d.isoformat() for d in test_days],
        "results": results,
    }

    if output_path is not None:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate policies on test days")
    parser.add_argument("--days", "-d", default=None, help="Number of days, comma-separated dates, or 'all'")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output path for eval_results.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    data = load_default()
    cfg = sim_config()
    test_days = _resolve_test_days(data, args.days, cfg.eval_days)
    log.info("Evaluating on %d test days: %s", len(test_days), [d.isoformat() for d in test_days])
    evaluate(data=data, days=test_days, output_path=args.output)
    log.info("Saved evaluation to %s", args.output)


if __name__ == "__main__":
    main()
