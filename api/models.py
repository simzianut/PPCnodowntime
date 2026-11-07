"""Pydantic models for every API payload in docs/CONTRACTS.md."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from sim.types import (
    Autonomy,
    CommandStatus,
    ContextType,
    PolicyName,
    PowerClass,
    State,
    TargetState,
)

Confidence = Literal["high", "medium", "low"]
RunMode = Literal["replay", "live"]
RunStatusName = Literal["running", "paused", "done"]
PausedReason = Literal["user", "recommendations"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChargePoint(_Strict):
    cp_id: str
    power_class: PowerClass
    max_kw: float | None
    connectors: list[str]
    current_type: Literal["AC", "DC"]
    state: State | None = None


class Location(_Strict):
    location_id: str
    name: str
    partner: str | None
    address: str | None
    county_code: str
    lat: float
    lon: float
    context_type: ContextType
    primary_source: str
    confidence: Confidence
    charge_points: list[ChargePoint]


class Kpis(_Strict):
    overhead_kwh: float
    saved_kwh: float
    saved_pct: float = Field(description="0-100, share of always_on overhead")
    wait_min_total: float
    wait_min_mean: float
    wait_min_p95: float
    sessions: int
    waited_sessions: int
    sleep_share: float = Field(ge=0, le=1)


class Recommendation(_Strict):
    id: int
    ts: datetime
    cp_id: str
    policy: PolicyName
    from_state: State
    target_state: TargetState
    reason: str
    status: CommandStatus
    expires_at: datetime


class Health(_Strict):
    ok: bool


class PolicyInfo(_Strict):
    name: PolicyName
    lambdas: list[float]
    available: bool


class AutonomyBody(_Strict):
    mode: Autonomy


class RunCreate(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: RunMode
    date: Date | None = None
    policies: list[PolicyName] = Field(default_factory=lambda: list(PolicyName), min_length=1)
    focus_policy: PolicyName | None = None
    lambda_: float | None = Field(default=None, alias="lambda", gt=0)
    speed: int = Field(default=60, ge=1, le=288)


class RunCreated(_Strict):
    run_id: str


class RunStatus(_Strict):
    run_id: str
    mode: RunMode
    status: RunStatusName
    paused_reason: PausedReason | None
    sim_time: datetime
    slot: int
    speed: int
    focus_policy: PolicyName
    kpis: dict[PolicyName, Kpis]


class RunControl(_Strict):
    action: Literal["play", "pause", "speed", "seek", "focus"]
    value: int | float | str | None = None


class RecommendationDecision(_Strict):
    decision: Literal["approve", "reject"]


class StateChange(_Strict):
    cp_id: str
    state: State


class ArrivalEvent(_Strict):
    type: Literal["arrival"]
    cp_id: str
    ts: datetime
    wait_min: float
    moved_from: str | None = None
    queued: bool = False


class DepartureEvent(_Strict):
    type: Literal["departure"]
    cp_id: str
    ts: datetime


Event = Annotated[ArrivalEvent | DepartureEvent, Field(discriminator="type")]


class TickMessage(_Strict):
    type: Literal["tick"]
    run_id: str
    sim_time: datetime
    slot: int
    kpis: dict[PolicyName, Kpis]
    focus_policy: PolicyName
    changes: list[StateChange]
    events: list[Event]
    recommendations: list[Recommendation]


class SnapshotMessage(TickMessage):
    type: Literal["snapshot"]  # type: ignore[assignment]


class StatusMessage(_Strict):
    type: Literal["status"]
    run_id: str
    status: RunStatusName
    paused_reason: PausedReason | None
    speed: int
    focus_policy: PolicyName
    slot: int
    sim_time: datetime


class DoneMessage(_Strict):
    type: Literal["done"]
    kpis: dict[PolicyName, Kpis]


class TimelineState(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    state: State


class TimelineArrival(_Strict):
    ts: datetime
    wait_min: float


class TimelineIntent(_Strict):
    ts: datetime
    eta_ts: datetime


class TimelineForecast(_Strict):
    ts: datetime
    p_arrival_30: float


class TimelineCommand(_Strict):
    ts: datetime
    target_state: TargetState
    reason: str
    status: CommandStatus


class Timeline(_Strict):
    cp_id: str
    policy: PolicyName
    states: list[TimelineState]
    arrivals: list[TimelineArrival]
    intents: list[TimelineIntent]
    forecast: list[TimelineForecast]
    commands: list[TimelineCommand]


class EvalRow(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    policy: PolicyName
    lambda_: float | None = Field(alias="lambda")
    overhead_kwh: float
    saved_kwh: float
    saved_pct: float
    wait_min_total: float
    wait_min_mean: float
    wait_min_p95: float
    sessions: int
    waited_sessions: int
    sleep_share: float = Field(ge=0, le=1)


class EvalResults(BaseModel):
    """`artifacts/eval_results.json`; extra keys (e.g. `fixture`) are allowed."""

    generated_at: datetime
    test_days: list[Date]
    results: list[EvalRow]
