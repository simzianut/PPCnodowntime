"""Cost-rule policy based on analytical trade-off between energy and wait (spec 02 §3).

Calculates E_save(s) - C_wait(s) per charge point and applies route intent
overrides and min_awake_per_location constraints.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from model.stubs import ConstantForecaster
from model.types import (
    Command,
    CpView,
    Policy,
    PolicyName,
    PowerClass,
    SimState,
    State,
    TargetState,
)

DEFAULT_POWER_PROFILES = {
    PowerClass.AC_22: {
        "idle_w": 10.0,
        "standby_w": 3.0,
        "deep_sleep_w": None,
        "standby_wake_s": 10.0,
        "deep_sleep_wake_s": None,
    },
    PowerClass.DC_50: {
        "idle_w": 150.0,
        "standby_w": 30.0,
        "deep_sleep_w": 5.0,
        "standby_wake_s": 10.0,
        "deep_sleep_wake_s": 180.0,
    },
    PowerClass.DC_HPC: {
        "idle_w": 150.0,
        "standby_w": 30.0,
        "deep_sleep_w": 5.0,
        "standby_wake_s": 10.0,
        "deep_sleep_wake_s": 180.0,
    },
}


class CostRulePolicy(Policy):
    """Analytical policy optimizing power state trade-offs (spec 02 §3)."""

    name = PolicyName.COST_RULE

    def __init__(
        self,
        lambda_wait: float = 0.1,
        slot_s: int = 300,
        min_awake_per_location: int = 1,
        power_profiles: dict[PowerClass, dict[str, Any]] | None = None,
        power_profiles_path: Path | str = "config/power_profiles.yaml",
        sim_config_path: Path | str = "config/sim.yaml",
    ) -> None:
        self.lambda_wait = lambda_wait
        self.slot_s = slot_s
        self.min_awake_per_location = min_awake_per_location
        self.power_profiles = power_profiles or self._load_power_profiles(power_profiles_path)
        # Explicit profiles are used verbatim, like sim.engine.Engine(profiles=...).
        self._per_cp = power_profiles is None
        self._store_cfg = Path(power_profiles_path) if Path(power_profiles_path).exists() else None
        self._cp_profiles: dict[tuple[PowerClass, float], dict[str, Any]] = {}
        self._load_sim_config(sim_config_path)
        self._fallback_forecaster = ConstantForecaster()

    def _load_power_profiles(self, path: Path | str) -> dict[PowerClass, dict[str, Any]]:
        p = Path(path)
        if not p.exists():
            return DEFAULT_POWER_PROFILES
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        profiles: dict[PowerClass, dict[str, Any]] = {}
        for pc in (PowerClass.AC_22, PowerClass.DC_50, PowerClass.DC_HPC):
            raw = data.get(pc.value, {})
            profiles[pc] = {
                "idle_w": float(raw.get("idle_w", DEFAULT_POWER_PROFILES[pc]["idle_w"])),
                "standby_w": float(raw.get("standby_w", DEFAULT_POWER_PROFILES[pc]["standby_w"])),
                "deep_sleep_w": (
                    float(raw["deep_sleep_w"]) if raw.get("deep_sleep_w") is not None else None
                ),
                "standby_wake_s": float(
                    raw.get("standby_wake_s", DEFAULT_POWER_PROFILES[pc]["standby_wake_s"])
                ),
                "deep_sleep_wake_s": (
                    float(raw["deep_sleep_wake_s"])
                    if raw.get("deep_sleep_wake_s") is not None
                    else None
                ),
            }
        return profiles

    def _profile(self, cp: CpView) -> dict[str, Any]:
        """Class profile, with per-CP watts from stations.store when max_kw is known."""
        base = self.power_profiles.get(cp.power_class, DEFAULT_POWER_PROFILES[cp.power_class])
        kw = getattr(cp, "max_kw", None)
        if not self._per_cp or kw is None or pd.isna(kw):
            return base
        key = (cp.power_class, float(kw))
        prof = self._cp_profiles.get(key)
        if prof is None:
            from stations.store import get_power_profile

            p = get_power_profile(cp.power_class, float(kw), config_path=self._store_cfg)
            deep = p["deep_sleep_w"] if base["deep_sleep_w"] is not None else None
            prof = {
                **base,
                "idle_w": p["idle_w"],
                "standby_w": p["standby_w"],
                "deep_sleep_w": deep,
            }
            self._cp_profiles[key] = prof
        return prof

    def _load_sim_config(self, path: Path | str) -> None:
        p = Path(path)
        if not p.exists():
            return
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if "slot_s" in data:
            self.slot_s = int(data["slot_s"])
        if "min_awake_per_location" in data:
            self.min_awake_per_location = int(data["min_awake_per_location"])

    def decide(self, state: SimState) -> list[Command]:
        """Choose target power states maximizing E_save - C_wait."""
        forecast_df = (
            state.forecast
            if state.forecast is not None and not state.forecast.empty
            else self._fallback_forecaster.predict(state)
        )
        forecast_by_cp = forecast_df.set_index("cp_id").to_dict(orient="index")

        # Check imminent route intents per location: eta within wake_time + 300s
        intents_by_loc = self._extract_route_intents(state)

        # First pass: calculate optimal state per CP
        proposals: dict[str, tuple[TargetState, float, str]] = {}
        cps_by_loc_class: dict[tuple[str, PowerClass], list[CpView]] = {}

        for cp in state.cps:
            loc_key = (cp.location_id, cp.power_class)
            cps_by_loc_class.setdefault(loc_key, []).append(cp)

            if cp.state in (State.CHARGING, State.OFFLINE):
                continue

            fc = forecast_by_cp.get(cp.cp_id, {})
            p15 = float(fc.get("p_arrival_15", 0.05))
            p30 = float(fc.get("p_arrival_30", 0.10))
            p60 = float(fc.get("p_arrival_60", 0.20))

            prof = self._profile(cp)
            idle_w = prof["idle_w"]

            # Candidate states
            candidates: list[TargetState] = [TargetState.IDLE, TargetState.STANDBY]
            if cp.power_class != PowerClass.AC_22 and prof["deep_sleep_w"] is not None:
                candidates.append(TargetState.DEEP_SLEEP)

            best_state = TargetState.IDLE
            best_net = -1e9
            best_reason = "idle_default"

            loc_intents = intents_by_loc.get(cp.location_id, [])

            for s in candidates:
                wake_s = (
                    0.0
                    if s == TargetState.IDLE
                    else (
                        prof["standby_wake_s"]
                        if s == TargetState.STANDBY
                        else prof["deep_sleep_wake_s"] or 0.0
                    )
                )

                # Intent override: if an arrival is expected within wake_s + 5 min, force wake
                if s != TargetState.IDLE and loc_intents:
                    if any(lead_s <= wake_s + 300.0 for lead_s in loc_intents):
                        continue

                # E_save in kWh
                power_s = (
                    idle_w
                    if s == TargetState.IDLE
                    else (
                        prof["standby_w"]
                        if s == TargetState.STANDBY
                        else (prof["deep_sleep_w"] or 0.0)
                    )
                )
                e_save_kwh = ((idle_w - power_s) * self.slot_s) / 3_600_000.0

                # Select smallest forecast horizon >= wake_s + slot_s
                needed_s = wake_s + self.slot_s
                if needed_s <= 900.0:
                    p_arr = p15
                elif needed_s <= 1800.0:
                    p_arr = p30
                else:
                    p_arr = p60

                # C_wait in kWh (λ in kWh per minute of wait, decision 0020)
                c_wait_kwh = p_arr * (wake_s / 60.0) * self.lambda_wait
                net_kwh = e_save_kwh - c_wait_kwh

                if net_kwh > best_net:
                    best_net = net_kwh
                    best_state = s
                    best_reason = f"cost_rule(state={s.value},net={net_kwh:.4f}kWh)"

            proposals[cp.cp_id] = (best_state, best_net, best_reason)

        # Second pass: enforce min_awake_per_location per power class
        for (_loc_id, _p_class), group in cps_by_loc_class.items():
            active_cps = [cp for cp in group if cp.state not in (State.CHARGING, State.OFFLINE)]
            if not active_cps:
                continue

            awake_count = sum(
                1
                for cp in active_cps
                if proposals.get(cp.cp_id, (TargetState.IDLE,))[0] == TargetState.IDLE
            )

            # Check if all would sleep and arrival in 60m is possible (p_arr_60 >= 0.02)
            if awake_count < self.min_awake_per_location:
                # Check 60m forecast: exemption only if no arrival with p > 0.98
                exempt = all(
                    float(forecast_by_cp.get(cp.cp_id, {}).get("p_arrival_60", 0.20)) < 0.02
                    for cp in active_cps
                )
                if not exempt:
                    # Sort sleeping CPs by net gain ascending (lowest gain wakes first)
                    sleeping = [
                        cp
                        for cp in active_cps
                        if proposals.get(cp.cp_id, (TargetState.IDLE,))[0] != TargetState.IDLE
                    ]
                    sleeping.sort(key=lambda cp: proposals[cp.cp_id][1])
                    needed = self.min_awake_per_location - awake_count
                    for cp in sleeping[:needed]:
                        proposals[cp.cp_id] = (
                            TargetState.IDLE,
                            0.0,
                            "min_awake_per_location_guard",
                        )

        # Emit commands
        commands: list[Command] = []
        for cp_id, (target, _, reason) in proposals.items():
            commands.append(Command(cp_id=cp_id, target_state=target, reason=reason))

        return commands

    def _extract_route_intents(self, state: SimState) -> dict[str, list[float]]:
        intents_by_loc: dict[str, list[float]] = {}
        if state.signals is None or state.signals.empty:
            return intents_by_loc

        # Filter route intent signals
        sig = state.signals
        if "signal" not in sig.columns or "location_id" not in sig.columns:
            return intents_by_loc

        intent_rows = sig[sig["signal"] == "ppcblue_route_intent"]
        if intent_rows.empty or "eta_ts" not in intent_rows.columns:
            return intents_by_loc

        for _, row in intent_rows.iterrows():
            eta = row["eta_ts"]
            loc = row["location_id"]
            if pd.notna(eta):
                lead_s = (pd.to_datetime(eta) - state.t).total_seconds()
                if lead_s >= 0:
                    intents_by_loc.setdefault(loc, []).append(lead_s)
        return intents_by_loc
