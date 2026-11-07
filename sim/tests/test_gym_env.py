import warnings
from datetime import date

import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env

from sim.baselines import AlwaysOn
from sim.config import power_profiles, sim_config
from sim.data import SimData
from sim.engine import Engine
from sim.gym_env import ChargePointEnv
from sim.obs import OBS_DIM, build_obs
from sim.stations_seed import load_seed_stations
from sim.synthetic import generate

IDLE_A, STANDBY_A, DEEP_A = 0, 1, 2
SOLO_DC, SOLO_AC, MIXED4 = "L10023", "L10068", "L10039"
N_SLOTS = sim_config().slots_per_day


def _gen(*locs: str) -> SimData:
    seed_locs, _ = load_seed_stations()
    sel = seed_locs[seed_locs["location_id"].isin(locs)]
    return generate(seed=1, train_days=3, test_days=2, locations=sel)


@pytest.fixture(scope="module")
def solo_dc() -> SimData:
    return _gen(SOLO_DC)


@pytest.fixture(scope="module")
def solo_ac() -> SimData:
    return _gen(SOLO_AC)


@pytest.fixture(scope="module")
def mixed() -> SimData:
    return _gen(SOLO_DC, SOLO_AC, MIXED4)


def _norm(pc: str) -> float:
    return power_profiles()[pc].idle_w * sim_config().slot_s / 3.6e6


def _rollout(env: ChargePointEnv, policy, seed: int | None = None):
    obs, info = env.reset(seed=seed)
    rows = [(obs, 0.0, info)]
    done = False
    while not done:
        obs, r, done, trunc, info = env.step(policy(env, info))
        assert not trunc
        rows.append((obs, r, info))
    return rows


def _const(a: int):
    return lambda env, info: a


def _busy_day(data: SimData) -> date:
    """A train day with sessions on the (only) CP."""
    return data.days("train")[0]


def test_check_env(mixed):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        check_env(ChargePointEnv(data=mixed, seed=3), skip_render_check=True)


def test_episode_shape_and_rewards(mixed):
    env = ChargePointEnv(data=mixed, seed=0)
    rng = np.random.default_rng(0)
    rows = _rollout(env, lambda e, i: int(rng.choice(np.flatnonzero(e.action_masks()))), 1)
    assert len(rows) == N_SLOTS + 1
    for obs, r, info in rows:
        assert obs.shape == (OBS_DIM,) and obs.dtype == np.float32
        assert env.observation_space.contains(obs)
        assert r <= 0
        assert info["action_mask"].dtype == bool and info["action_mask"][0]
    assert [i["slot"] for _, _, i in rows[1:]] == list(range(N_SLOTS))
    assert env.engine.done


def test_obs_matches_build_obs(mixed):
    env = ChargePointEnv(data=mixed, seed=0)
    rng = np.random.default_rng(1)
    for _ in range(2):  # the second pass uses the cached location-day columns
        env.reset(seed=4)
        done = False
        while True:
            ref = build_obs(env.engine.sim_state("agent"), [env.cp_id])[0]
            np.testing.assert_array_equal(env._obs, ref)
            if done:
                break
            _, _, done, _, _ = env.step(int(rng.choice(np.flatnonzero(env.action_masks()))))


def test_always_idle_matches_always_on(solo_dc):
    day = _busy_day(solo_dc)
    env = ChargePointEnv(data=solo_dc, days=[day], lambda_wait=1.0)
    rows = _rollout(env, _const(IDLE_A), 0)
    cp = env.cp_id
    eng = Engine(solo_dc, day, [AlwaysOn()], cp_ids=[cp])
    norm = _norm("DC_50")
    charging_slots = 0
    for _, r, info in rows[1:]:
        ref = eng.step().overhead_kwh["always_on"][0]
        assert info["wait_min"] == 0
        assert r == pytest.approx(-ref / norm)
        assert -1 - 1e-9 <= r <= 0
        charging_slots += r > -1 + 1e-9
    assert sum(r == pytest.approx(-1) for _, r, _ in rows[1:]) > N_SLOTS / 2
    assert charging_slots > 0


def test_always_deep_sleep_dc_saves_energy_and_causes_wait(solo_dc):
    day = _busy_day(solo_dc)
    kw = {"data": solo_dc, "days": [day], "lambda_wait": 0.0}
    idle = _rollout(ChargePointEnv(**kw), _const(IDLE_A), 0)
    deep = _rollout(ChargePointEnv(**kw), lambda e, i: DEEP_A if i["action_mask"][2] else 0, 0)
    assert sum(r for _, r, _ in deep) > sum(r for _, r, _ in idle)
    assert any(i["state"] == "DEEP_SLEEP" for _, _, i in deep)
    waits = [i["wait_min"] for _, _, i in deep[1:]]
    assert sum(waits) > 0
    deep_wake_min = power_profiles()["DC_50"].deep_sleep_wake_s / 60
    assert all(w == pytest.approx(0) or w >= deep_wake_min - 1e-6 for w in waits)


def test_invalid_deep_sleep_on_ac_keeps_state(solo_ac):
    env = ChargePointEnv(data=solo_ac, seed=0)
    _, info = env.reset(seed=0)
    assert list(info["action_mask"]) == [True, True, False]
    rows = _rollout(env, _const(DEEP_A), 0)
    assert all(i["state"] in ("IDLE", "CHARGING") for _, _, i in rows)
    assert env.engine.timeline("agent", env.cp_id)["commands"] == []


def test_actions_ignored_while_charging(solo_dc):
    env = ChargePointEnv(data=solo_dc, days=[_busy_day(solo_dc)])
    seen = []

    def policy(e, info):
        if info["state"] == "CHARGING":
            assert list(e.action_masks()) == [True, False, False]
            seen.append(info["slot"])
            return STANDBY_A if len(seen) % 2 else DEEP_A
        return IDLE_A

    rows = _rollout(env, policy, 0)
    assert seen
    assert env.engine.timeline("agent", env.cp_id)["commands"] == []
    assert not any(i["state"] in ("STANDBY", "DEEP_SLEEP") for _, _, i in rows)


def test_valid_standby_is_applied(solo_ac):
    env = ChargePointEnv(data=solo_ac)
    env.reset(seed=0)
    _, _, _, _, info = env.step(STANDBY_A)
    cmds = env.engine.timeline("agent", env.cp_id)["commands"]
    assert [c["target_state"] for c in cmds] == ["STANDBY"]
    assert cmds[0]["reason"] == "rl"


def test_days_and_split_restrict_sampling(mixed):
    day = mixed.days("train")[1]
    env = ChargePointEnv(data=mixed, days=[day])
    assert {env.reset(seed=s)[1]["day"] for s in range(8)} == {day}
    test_days = set(mixed.days("test"))
    env = ChargePointEnv(data=mixed, split="test")
    seen = {env.reset(seed=s)[1]["day"] for s in range(20)}
    assert seen <= test_days and len(seen) == len(test_days)
    cps = {env.reset(seed=s)[1]["cp_id"] for s in range(40)}
    assert cps == set(mixed.cps["cp_id"])


def test_seeded_reset_is_deterministic(mixed):
    def run(env):
        rng = np.random.default_rng(5)
        rows = _rollout(env, lambda e, i: int(rng.choice(np.flatnonzero(i["action_mask"]))), 7)
        return (
            rows[0][2]["cp_id"],
            rows[0][2]["day"],
            np.stack([o for o, _, _ in rows]),
            [r for _, r, _ in rows],
        )

    a = run(ChargePointEnv(data=mixed, seed=0))
    b = run(ChargePointEnv(data=mixed, seed=99))
    assert a[:2] == b[:2]
    np.testing.assert_array_equal(a[2], b[2])
    assert a[3] == b[3]


def test_lambda_scales_wait_term(solo_dc):
    day = _busy_day(solo_dc)

    def run(lam):
        env = ChargePointEnv(data=solo_dc, days=[day], lambda_wait=lam)
        return _rollout(env, lambda e, i: DEEP_A if i["action_mask"][2] else 0, 0)

    norm = _norm("DC_50")
    base = run(0.0)
    for lam in (0.5, 2.0):
        rows = run(lam)
        for (_, r0, i0), (_, r, i) in zip(base[1:], rows[1:], strict=True):
            assert i["wait_min"] == i0["wait_min"]
            assert r0 - r == pytest.approx(lam * i["wait_min"] / norm)
    assert sum(i["wait_min"] for _, _, i in base) > 0


def test_sibling_policy_choice(mixed):
    try:
        import model.cost_rule  # noqa: F401

        expected = "cost_rule"
    except ImportError:
        expected = "naive_quiet_hours"
    assert ChargePointEnv(data=mixed).sibling_policy_name == expected
    assert ChargePointEnv(data=mixed, sibling_policy=AlwaysOn()).sibling_policy_name == "always_on"


def test_dst_day_episode_has_300_slots():
    seed_locs, _ = load_seed_stations()
    sel = seed_locs[seed_locs["location_id"] == SOLO_DC]
    data = generate(
        seed=2, train_days=2, test_days=1, train_start=date(2023, 10, 28), locations=sel
    )
    env = ChargePointEnv(split="train", days=[date(2023, 10, 29)], data=data)
    env.reset(seed=0)
    steps, done = 0, False
    while not done:
        _, _, done, _, _ = env.step(IDLE_A)
        steps += 1
    assert steps == env.engine.n_slots == 300
