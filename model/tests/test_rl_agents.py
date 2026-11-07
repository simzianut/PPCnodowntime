"""MaskablePPO, masked Double DQN, oracle and policy adapters on small synthetic data."""

import json
import time

import numpy as np
import pytest
import torch

from model import rl_common
from model.ddqn import MaskedDoubleDQN, mask_from_obs
from model.experiments import runner
from model.oracle import HindsightOraclePolicy
from model.rl_common import EpisodeEnv, EpisodeSampler, evaluate_episodes, split_days
from model.rl_policies import DDQNPolicy, MaskablePPOPolicy
from sim.config import sim_config
from sim.engine import Engine
from sim.obs import OBS_DIM, action_mask, build_obs
from sim.stations_seed import load_seed_stations
from sim.synthetic import generate
from sim.types import CpView, SimState, State, TargetState

LOCS = ("L10023", "L10068", "L10039")  # solo DC, solo AC, mixed 4 CPs


@pytest.fixture(scope="module")
def data():
    seed_locs, _ = load_seed_stations()
    sel = seed_locs[seed_locs["location_id"].isin(LOCS)]
    return generate(seed=1, train_days=6, test_days=2, locations=sel)


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch, data):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(rl_common, "_DATA_OVERRIDE", data)


def _env(data, track="none", dco=1.0, n_val=2):
    _, val = split_days(data, n_val)
    sampler = EpisodeSampler(data, val, None, dco)
    return EpisodeEnv(sampler, val, data, 0.1, seed=0, track=track), sampler


def test_split_and_sampler(data):
    train, val = split_days(data, 2)
    assert len(val) == 2 and train[-1] < val[0]
    _, sampler = _env(data, dco=5.0)
    eps = sampler.fixed_episodes(10, seed=7)
    assert eps == sampler.fixed_episodes(10, seed=7) and len(set(eps)) == 10
    assert all(day in val for _, day in eps)


def test_sampler_skips_inactive(data):
    import pandas as pd

    _, val = split_days(data, 2)
    cps = data.cps.drop_duplicates("cp_id")["cp_id"].astype(str).tolist()
    rows = [(c, d, c == cps[0]) for c in cps for d in val]
    active = pd.DataFrame(rows, columns=["cp_id", "date", "active"])
    s = EpisodeSampler(data, val, active)
    assert set(s.cp_ids) == {cps[0]}


def test_mask_from_obs_equals_action_mask(data):
    env, _ = _env(data)
    n = 0
    for ep in range(6):
        obs, _ = env.reset(seed=ep)
        for _ in range(env.engine.n_slots):
            want = env.action_masks()
            got = mask_from_obs(torch.as_tensor(obs)[None]).numpy()[0]
            assert (got == want).all()
            n += 1
            obs, _, done, _, _ = env.step(n % 3)
            if done:
                break
    assert n > 500


def _tiny_ddqn(data):
    env, _ = _env(data)
    from stable_baselines3.common.vec_env import DummyVecEnv

    return MaskedDoubleDQN(
        "MlpPolicy", DummyVecEnv([lambda: env]), learning_starts=50, buffer_size=2000, seed=0
    )


def test_ddqn_never_selects_or_bootstraps_invalid(data):
    model = _tiny_ddqn(data)
    obs = np.zeros((4, OBS_DIM), dtype=np.float32)
    obs[:, 19] = 1  # charging -> only IDLE
    obs[2, 19], obs[2, 20], obs[2, 28] = 0, 1, 1  # idle AC -> no deep sleep
    for eps in (0.0, 1.0):
        model.exploration_rate = eps
        for _ in range(30):
            a, _ = model.predict(obs, deterministic=(eps == 0.0))
            assert (a[[0, 1, 3]] == 0).all() and a[2] in (0, 1)
    # bootstrap: make the invalid action the largest target Q; it must not be chosen
    t = torch.as_tensor(obs)
    with torch.no_grad():
        for prm in model.q_net.parameters():
            prm.zero_()
        model.q_net.q_net[-1].bias.copy_(torch.tensor([0.0, 1.0, 5.0]))
        q = model._masked_q(model.q_net, t)
    assert (q.argmax(dim=1)[[0, 1, 3]] == 0).all() and q.argmax(dim=1)[2] == 1


def test_ddqn_train_step_runs(data):
    model = _tiny_ddqn(data)
    model.learn(300)
    assert model.num_timesteps >= 300 and model._n_updates > 0


def _oracle_eval(data, n=12):
    env, sampler = _env(data, dco=3.0)
    eps = sampler.fixed_episodes(n, seed=3)
    return evaluate_episodes(lambda obs, mask: 0, eps, env, refs=True)


def test_oracle_saves_more_than_cost_rule_with_no_wait(data):
    res = _oracle_eval(data)
    o, c = res["oracle"]["all"], res["cost_rule"]["all"]
    assert o["saved_kwh"] >= c["saved_kwh"] > -1e-6
    assert o["saved_kwh"] > 0
    assert o["wait_min_total"] <= 0.02 * max(res["always_on"]["all"]["sessions"], 1)
    assert res["always_on"]["all"]["saved_kwh"] == 0
    assert res["pct_of_oracle_savings"]["all"] is not None


def test_oracle_engine_policy_valid(data):
    day = data.days("train")[0]
    eng = Engine(data, day, [HindsightOraclePolicy(data.sessions)])
    res = eng.run()
    assert res["oracle"]["saved_pct"] > 0


def _tiny_cfg(model, seed=0, **params):
    cfg = {
        "name": f"t_{model}", "kind": "rl", "model": model, "seed": seed, "device": "cpu",
        "deterministic": True, "total_timesteps": 400, "data": {},
        "params": {
            "n_envs": 1, "n_val_days": 2, "n_val_episodes": 4, "eval_every_steps": 200,
            "n_steps": 64, "batch_size": 32, "n_epochs": 1, "learning_starts": 50,
            "buffer_size": 1000, **params,
        },
    }  # fmt: skip
    return cfg


def _metrics(run_dir):
    m = json.loads((run_dir / "metrics.json").read_text())
    m.pop("perf")
    return json.dumps(m, sort_keys=True)


@pytest.mark.parametrize("model", ["rl_maskppo", "rl_ddqn"])
def test_tiny_run_deterministic(model, tmp_path):
    cfg = _tiny_cfg(model)
    d1 = runner.run_config(cfg)
    for f in ("checkpoints/best.zip", "checkpoints/last.zip", "metrics.json"):
        assert (d1 / f).exists()
    stem = "maskppo" if model == "rl_maskppo" else "ddqn"
    assert (d1 / "model" / f"{stem}_policy.zip").exists()
    meta = json.loads((d1 / "model" / f"{stem}_meta.json").read_text())
    assert meta["obs_fields"] and meta["lambda_wait"] == sim_config().default_lambda
    m = json.loads((d1 / "metrics.json").read_text())
    assert {"agent", "always_on", "cost_rule", "oracle"} <= set(m["val"])
    assert m["perf"]["train_env_steps_per_s"] > 0
    first = _metrics(d1)
    d2 = runner.run_config(cfg, overwrite=True)
    assert _metrics(d2) == first


def test_oracle_run(tmp_path):
    cfg = _tiny_cfg("oracle")
    cfg["kind"] = "bound"
    d = runner.run_config(cfg)
    m = json.loads((d / "metrics.json").read_text())
    assert m["val"]["oracle"]["all"]["saved_kwh"] > 0


def _big_state(n=1500):
    cps = [
        CpView(f"c{i}", f"L{i // 3}", "DC_50" if i % 2 else "AC_22", [], State.IDLE, 600)
        for i in range(n)
    ]
    cps[0].state = State.CHARGING
    t = np.datetime64("2023-10-03T03:00")
    import pandas as pd

    return SimState(pd.Timestamp(t, tz="Europe/Bucharest"), cps, pd.DataFrame(), {}, None)


def test_policy_adapters(data, tmp_path):
    from sb3_contrib import MaskablePPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    env, _ = _env(data)
    ppo = MaskablePPO("MlpPolicy", DummyVecEnv([lambda: env]), seed=0, n_steps=32, batch_size=32)
    ddqn = _tiny_ddqn(data)
    ppo.save(tmp_path / "p.zip")
    ddqn.save(tmp_path / "d.zip")
    state = _big_state()
    for pol in (MaskablePPOPolicy.load(tmp_path / "p.zip"), DDQNPolicy.load(tmp_path / "d.zip")):
        pol.decide(state)
        t0 = time.perf_counter()
        cmds = pol.decide(state)
        assert time.perf_counter() - t0 < 0.25  # spec target 50 ms; slack for slow CI laptops
        by_id = {c.cp_id: c for c in state.cps}
        for c in cmds:
            cp = by_id[c.cp_id]
            assert cp.state not in (State.CHARGING, State.WAKING, State.OFFLINE)
            assert isinstance(c.target_state, TargetState)
            assert action_mask(cp)[list(TargetState).index(c.target_state)]
    obs = build_obs(state, [c.cp_id for c in state.cps])
    assert obs.shape == (1500, OBS_DIM)
