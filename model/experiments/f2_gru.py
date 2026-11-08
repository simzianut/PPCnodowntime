"""F2: GRU over the last 36 slots with a discrete-time survival head (decision 0052)."""

from __future__ import annotations

import time

import numpy as np
import torch
import torch.nn.functional as F  # noqa: N812
from torch import nn
from torch.utils.data import BatchSampler, DataLoader, Dataset, RandomSampler

from model import forecast_data as fd
from model import forecast_eval as fe
from model.experiments import forecast_common as fc
from model.experiments.registry import register
from model.experiments.runner import RunContext
from model.repro import seed_worker, torch_generator

DEFAULTS = {
    "window_slots": 36,
    "hidden": 64,
    "layers": 1,
    "dropout": 0.1,
    "mlp_hidden": 64,
    "poisson_weight": 0.2,
    "lr": 2e-3,
    "weight_decay": 1e-4,
    "epochs": 15,
    "batch_size": 2048,
    "samples_per_epoch": 2_000_000,
    "val_max_rows": 500_000,
    "patience": 3,
    "grad_clip": 1.0,
    "num_workers": 0,
    "num_threads": 8,
    "amp": False,
    "calibration": "isotonic",  # isotonic | temp_bias | auto | none; old calibrate: bool works
    "lr_schedule": "none",  # none | onecycle (warm-up then cosine decay, per batch)
    "history_dropout": 0.0,  # share of training windows whose history features are cold-start
}
# Arrival-history features that live mode starts without; their cold-start raw values.
HISTORY_COLUMNS = [
    "arrivals_cp_1h", "arrivals_cp_3h", "arrivals_same_slot_4w", "arrivals_cp_7d",
    "arrivals_cp_28d", "mins_since_last_arrival", "loc_arrivals_1h", "loc_arrivals_3h",
    "loc_same_slot_4w", "loc_rate_7d",
]  # fmt: skip
COLD_RAW = {"mins_since_last_arrival": float(fd.MINS_CAP)}


class Windows(Dataset):
    """Batched window gather: item = array of row indices -> (x, valid, static, targets)."""

    def __init__(self, dyn, stat, bucket, n60, window: int):
        self.dyn, self.stat, self.bucket, self.n60, self.w = dyn, stat, bucket, n60, window

    def __len__(self) -> int:
        return len(self.dyn)

    def __getitem__(self, idx):
        idx = np.asarray(idx)
        slot = idx % fd.SLOTS
        off = np.arange(-self.w + 1, 1)
        valid = (slot[:, None] + off[None, :]) >= 0
        rows = np.where(valid, idx[:, None] + off[None, :], 0)
        x = self.dyn[rows] * valid[..., None]
        out = [x, valid.astype(np.float32), self.stat[idx]]
        out += [self.bucket[idx].astype(np.int64), self.n60[idx]]
        return tuple(torch.from_numpy(np.ascontiguousarray(a)) for a in out)


class Net(nn.Module):
    def __init__(self, n_dyn: int, n_scalar: int, c: dict):
        super().__init__()
        self.gru = nn.GRU(
            n_dyn + 1,
            c["hidden"],
            c["layers"],
            batch_first=True,
            dropout=c["dropout"] if c["layers"] > 1 else 0.0,
        )
        self.pc_emb = nn.Linear(3, 4, bias=False)  # one-hot @ W (no gather/scatter)
        self.ctx_emb = nn.Linear(5, 4, bias=False)
        self.mlp = nn.Sequential(
            nn.Linear(c["hidden"] + 8 + n_scalar, c["mlp_hidden"]),
            nn.ReLU(),
            nn.Dropout(c["dropout"]),
            nn.Linear(c["mlp_hidden"], fd.N_BUCKETS + 1),
        )

    def forward(self, x, valid, stat):
        h, _ = self.gru(torch.cat([x, valid.unsqueeze(-1)], -1))
        pc = (stat[:, :1].long() == torch.arange(3, device=stat.device)).float()
        cx = (stat[:, 1:2].long() == torch.arange(5, device=stat.device)).float()
        z = torch.cat([h[:, -1], self.pc_emb(pc), self.ctx_emb(cx), stat[:, 2:]], -1)
        out = self.mlp(z)
        return out[:, : fd.N_BUCKETS], out[:, fd.N_BUCKETS].clamp(-10.0, 6.0)


class Ensemble(nn.Module):
    """Mean of the members' hazard logits and log-rates (members share features and norm)."""

    def __init__(self, nets: list[nn.Module]):
        super().__init__()
        self.nets = nn.ModuleList(nets)

    def forward(self, x, valid, stat):
        outs = [n(x, valid, stat) for n in self.nets]
        return torch.stack([o[0] for o in outs]).mean(0), torch.stack([o[1] for o in outs]).mean(0)


def net_from_checkpoint(ck: dict) -> nn.Module:
    """A Net, or an Ensemble when the checkpoint holds `members` (a list of state dicts)."""
    n_dyn = len(ck["dynamic_features"])
    if ck.get("members"):
        nets = []
        for sd in ck["members"]:
            n = Net(n_dyn, 2, ck["config"])
            n.load_state_dict(sd)
            nets.append(n)
        return Ensemble(nets)
    net = Net(n_dyn, 2, ck["config"])
    net.load_state_dict(ck["state_dict"])
    return net


def checkpoint(net: nn.Module, c: dict, names, dyn_names, norm, cals: dict, cal: dict) -> dict:
    ck = {
        "config": dict(c),
        "features": list(names),
        "dynamic_features": list(dyn_names),
        "norm": norm,
        "calibrators": {k: v.to_dict() for k, v in cals.items()},
        "calibration": {"kind": cal["kind"], "a": cal["a"], "b": cal["b"]},
    }
    if isinstance(net, Ensemble):
        ck["members"] = [n.state_dict() for n in net.nets]
        ck["state_dict"] = ck["members"][0]
    else:
        ck["state_dict"] = net.state_dict()
    return ck


def cold_start(dyn_names: list[str], norm: dict):
    """(values, mask) over dynamic columns: normalised cold-start value of each history feature."""
    vals = np.zeros(len(dyn_names), np.float32)
    mask = np.zeros(len(dyn_names), bool)
    for k, c in enumerate(dyn_names):
        if c not in HISTORY_COLUMNS:
            continue
        raw = COLD_RAW.get(c, 0.0)
        v = np.log1p(raw) if c in fd.COUNT_COLUMNS else raw
        vals[k] = (v - norm["mu"][k]) / norm["sd"][k]
        mask[k] = True
    return torch.from_numpy(vals), torch.from_numpy(mask)


def drop_history(batch, p: float, cold: torch.Tensor, mask: torch.Tensor, gen):
    """Replace the history features of a random share p of windows with cold-start values."""
    x, valid, *rest = batch
    drop = torch.rand(x.shape[0], generator=gen) < p
    sel = drop[:, None, None] & mask[None, None, :]
    x = torch.where(sel, cold[None, None, :].to(x.dtype), x) * valid.unsqueeze(-1)
    return (x, valid, *rest)


def survival_nll(logits, bucket):
    """Discrete-time survival NLL; bucket 0..11 = first arrival, 12 = censored (survives all)."""
    j = torch.arange(fd.N_BUCKETS, device=logits.device)[None, :]
    before = (j < bucket[:, None]).float()
    event = (j == bucket[:, None]).float()
    return -(before * F.logsigmoid(-logits) + event * F.logsigmoid(logits)).sum(1)


def probs_from(logits, lograte):
    """p15, p30, p60 from hazards by explicit sums (monotone by construction); n60 = exp."""
    ls = F.logsigmoid(-logits)
    p = [1.0 - torch.exp(ls[:, :h].sum(1)) for h in (3, 6, 12)]
    return p[0], p[1], p[2], torch.exp(lograte)


def _prep(X, names):
    """Split columns into dynamic (log1p counts) and static blocks."""
    dyn_names = [c for c in names if c not in fd.STATIC_COLUMNS]
    dyn = X[:, [names.index(c) for c in dyn_names]].astype(np.float32)
    for k, c in enumerate(dyn_names):
        if c in fd.COUNT_COLUMNS:
            dyn[:, k] = np.log1p(dyn[:, k])
    stat = np.stack(
        [
            X[:, names.index("power_class_code")],
            X[:, names.index("context_type_code")],
            X[:, names.index("county_ev_per_1000")],
            np.log1p(X[:, names.index("cps_at_location")]),
        ],
        1,
    ).astype(np.float32)
    return dyn_names, dyn, stat


def apply_norm(dyn, stat, norm: dict):
    """Standardise dynamic columns and the non-categorical static ones (shared with inference)."""
    stat = stat.copy()
    stat[:, 2:] = (stat[:, 2:] - norm["static_mu"]) / norm["static_sd"]
    return (dyn - norm["mu"]) / norm["sd"], stat


def _loss(net, batch, dev, w_pois):
    x, valid, stat, bucket, n60 = (t.to(dev, non_blocking=True) for t in batch)
    logits, lograte = net(x, valid, stat)
    ll = survival_nll(logits, bucket).mean()
    pois = (torch.exp(lograte) - n60 * lograte).mean()
    return ll + w_pois * pois


@torch.no_grad()
def predict(net, ds: Windows, dev: str, bs: int = 8192) -> dict:
    net.eval()
    outs: list[list] = [[], [], [], []]
    lg: list = []
    for a in range(0, len(ds), bs):
        x, valid, stat, _, _ = ds[np.arange(a, min(a + bs, len(ds)))]
        logits, lograte = net(x.to(dev), valid.to(dev), stat.to(dev))
        for o, r in zip(outs, probs_from(logits, lograte), strict=True):
            o.append(r.cpu().double().numpy())
        lg.append(logits.cpu().float().numpy())
    res = {k: np.concatenate(o) for k, o in zip(("p15", "p30", "p60", "n60"), outs, strict=True)}
    res["logits"] = np.concatenate(lg)
    return res


def affine_probs(logits: np.ndarray, a: float, b: float) -> dict:
    """p15/p30/p60 from hazards sigmoid(a*l + b); monotone across horizons by construction."""
    z = a * np.asarray(logits, np.float64) + b
    cum = np.cumsum(-np.logaddexp(0.0, z), axis=1)  # log survival
    return {f"p{h}": -np.expm1(cum[:, k - 1]) for h, k in ((15, 3), (30, 6), (60, 12))}


def fit_temp_bias(logits: np.ndarray, bucket: np.ndarray) -> tuple[float, float]:
    """(a, b) minimising the survival NLL of l' = a*l + b on the given rows (LBFGS)."""
    lg = torch.from_numpy(np.ascontiguousarray(logits, np.float32))
    bk = torch.from_numpy(np.asarray(bucket).astype(np.int64))
    ab = torch.tensor([1.0, 0.0], requires_grad=True)
    opt = torch.optim.LBFGS([ab], lr=1.0, max_iter=100, line_search_fn="strong_wolfe")

    def closure():
        opt.zero_grad()
        loss = survival_nll(ab[0] * lg + ab[1], bk).mean()
        loss.backward()
        return loss

    opt.step(closure)
    a, b = ab.detach().tolist()
    return float(a), float(b)


def fit_calibration(kind: str, raw: dict, y: np.ndarray, bucket: np.ndarray) -> dict:
    """Calibration spec {kind, a, b, calibrators} fitted on raw predictions of one split."""
    cal: dict = {"kind": kind, "a": 1.0, "b": 0.0, "calibrators": {}}
    if kind == "isotonic":
        cal["calibrators"] = {
            f"y{h}": fe.fit_isotonic(raw[f"p{h}"], y[:, i]) for i, h in enumerate((15, 30, 60))
        }
    elif kind == "temp_bias":
        cal["a"], cal["b"] = fit_temp_bias(raw["logits"], bucket)
    elif kind != "none":
        raise ValueError(f"unknown calibration {kind!r}")
    return cal


def apply_calibration(cal: dict, raw: dict) -> dict:
    """Calibrated p15/p30/p60 (+ n60) from a raw prediction dict."""
    if cal["kind"] == "temp_bias":
        return {**affine_probs(raw["logits"], cal["a"], cal["b"]), "n60": raw["n60"]}
    if cal["kind"] == "isotonic":
        p = {f"p{h}": cal["calibrators"][f"y{h}"].predict(raw[f"p{h}"]) for h in (15, 30, 60)}
        return {**p, "n60": raw["n60"]}
    return {k: raw[k] for k in ("p15", "p30", "p60", "n60")}


def _calibration_kind(params: dict) -> str:
    if "calibration" in params:
        return str(params["calibration"])
    if "calibrate" in params:
        return "isotonic" if params["calibrate"] else "none"
    return str(DEFAULTS["calibration"])


def _mean_log_loss(pred: dict, y: np.ndarray) -> float:
    fixed, _ = fc.fix_monotone(pred)
    losses = [fe.log_loss(y[:, i], fixed[f"p{h}"]) for i, h in enumerate((15, 30, 60))]
    return float(np.mean(losses))


def choose_calibration(raw: dict, y: np.ndarray, bucket: np.ndarray, n_cpdays: int, seed: int):
    """Fit on half the val CP-days, score on the other half; returns (kind, {kind: score})."""
    perm = np.random.default_rng([seed, 4242]).permutation(n_cpdays)
    in_a = np.zeros(n_cpdays, bool)
    in_a[perm[: n_cpdays // 2]] = True
    a = np.repeat(in_a, fd.SLOTS)
    ra = {k: v[a] for k, v in raw.items()}
    rb = {k: v[~a] for k, v in raw.items()}
    scores = {}
    for kind in ("isotonic", "temp_bias"):
        cal = fit_calibration(kind, ra, y[a], bucket[a])
        scores[kind] = _mean_log_loss(apply_calibration(cal, rb), y[~a])
    return min(scores, key=lambda k: scores[k]), scores


@torch.no_grad()
def eval_loss(net, ds: Windows, idx: np.ndarray, dev: str, w: float, bs: int = 8192) -> float:
    net.eval()
    tot = 0.0
    for a in range(0, len(idx), bs):
        batch = ds[idx[a : a + bs]]
        tot += float(_loss(net, batch, dev, w)) * len(batch[0])
    return tot / len(idx)


@register("f2_gru", kind="forecaster", promote={"model/f2_gru.pt": "f2_gru.pt"})
def train(ctx: RunContext) -> dict:
    t0 = time.monotonic()
    c = {**DEFAULTS, **(ctx.config.get("params") or {})}
    torch.set_num_threads(int(c["num_threads"]))
    dev = ctx.device
    names = fd.feature_names(fd.data_params(ctx.config)["feature_groups"])
    data = fd.get_dataset(ctx.config, ctx.log)
    prep = {s: _prep(data.X(s, names), names) for s in fd.SPLITS}
    dyn_names = prep["train"][0]
    mu = prep["train"][1].mean(0)
    sd = prep["train"][1].std(0) + 1e-6
    smu = prep["train"][2][:, 2:].mean(0)
    ssd = prep["train"][2][:, 2:].std(0) + 1e-6
    norm = {"mu": mu, "sd": sd, "static_mu": smu, "static_sd": ssd}
    ds = {}
    for s in fd.SPLITS:
        _, dyn, stat = prep[s]
        dyn, stat = apply_norm(dyn, stat, norm)
        ds[s] = Windows(dyn, stat, data.bucket(s), data.n60(s), int(c["window_slots"]))
    del prep
    net = Net(len(dyn_names), 2, c).to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=c["lr"], weight_decay=c["weight_decay"])
    scaler = torch.amp.GradScaler(enabled=bool(c["amp"]) and dev == "cuda")
    n_tr = len(ds["train"])
    spe = min(int(c["samples_per_epoch"]), n_tr)
    rng = np.random.default_rng(int(fd.data_params(ctx.config)["sample_seed"]))
    n_val = len(ds["val"])
    val_idx = np.sort(rng.choice(n_val, size=min(int(c["val_max_rows"]), n_val), replace=False))
    cold, cold_mask = cold_start(dyn_names, norm)
    p_hist = float(c["history_dropout"])
    sched = None
    if c["lr_schedule"] == "onecycle":
        steps = int(c["epochs"]) * -(-spe // int(c["batch_size"]))
        sched = torch.optim.lr_scheduler.OneCycleLR(
            opt, max_lr=float(c["lr"]), total_steps=steps, pct_start=0.1
        )
    elif c["lr_schedule"] != "none":
        raise ValueError(f"unknown lr_schedule {c['lr_schedule']!r}")
    best, best_state, bad = float("inf"), None, 0
    for epoch in range(int(c["epochs"])):
        hgen = torch_generator(ctx.seed + 7000 + epoch)
        sampler = BatchSampler(
            RandomSampler(
                range(n_tr), num_samples=spe, generator=torch_generator(ctx.seed + epoch)
            ),
            int(c["batch_size"]),
            False,
        )
        loader = DataLoader(
            ds["train"], batch_size=None, sampler=sampler, num_workers=int(c["num_workers"]),
            worker_init_fn=seed_worker, generator=torch_generator(ctx.seed + 1000 + epoch),
        )  # fmt: skip
        net.train()
        tot, n = 0.0, 0
        for batch in loader:
            if p_hist > 0:
                batch = drop_history(batch, p_hist, cold, cold_mask, hgen)
            opt.zero_grad(set_to_none=True)
            with torch.autocast(dev, enabled=bool(c["amp"])):
                loss = _loss(net, batch, dev, c["poisson_weight"])
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            nn.utils.clip_grad_norm_(net.parameters(), c["grad_clip"])
            scaler.step(opt)
            scaler.update()
            if sched is not None:
                sched.step()
            tot += float(loss) * len(batch[0])
            n += len(batch[0])
        vl = eval_loss(net, ds["val"], val_idx, dev, c["poisson_weight"])
        ctx.log.info("epoch %d train_loss %.5f val_loss %.5f", epoch, tot / n, vl)
        if vl < best - 1e-6:
            best, bad = vl, 0
            best_state = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
        else:
            bad += 1
            if bad >= int(c["patience"]):
                break
    net.load_state_dict(best_state)
    raw = {s: predict(net, ds[s], dev) for s in ("val", "test")}
    return finish_run(
        ctx, c, data, names, dyn_names, norm, net, raw, time.monotonic() - t0,
        {"best_val_loss": round(best, 6)},
    )  # fmt: skip


def finish_run(ctx, c, data, names, dyn_names, norm, net, raw, train_time_s, extra=None) -> dict:
    """Calibrate on val, evaluate val/test, write forecasts.parquet and model/f2_gru.pt."""
    yva, bva = data.y("val"), data.bucket("val")
    kind = _calibration_kind(ctx.config.get("params") or {})
    selection = None
    if kind == "auto":
        n_cd = int((data.cpdays["split"] == "val").sum())
        seed = int(fd.data_params(ctx.config)["sample_seed"])
        kind, selection = choose_calibration(raw["val"], yva, bva, n_cd, seed)
    fits = {k: fit_calibration(k, raw["val"], yva, bva) for k in ("none", "isotonic", "temp_bias")}
    cal = fits[kind]
    cals = cal["calibrators"]
    final = {s: apply_calibration(cal, r) for s, r in raw.items()}
    metrics = fc.finish(
        ctx, data, "f2_gru", names, final, train_time_s,
        {
            **(extra or {}),
            "calibrated": kind != "none",
            "calibration_chosen": kind,
            "calibration_selection": selection,
            "dynamic_features": dyn_names,
            "n_params": int(sum(p.numel() for p in net.parameters())),
        },
    )  # fmt: skip
    unc, _ = fc.fix_monotone(raw["test"])
    tgt, meta = fc.targets_of(data, "test"), data.meta("test")
    metrics["test_uncalibrated"] = fe.evaluate(unc, tgt, meta)
    metrics["test_by_calibration"] = {
        k: fe.evaluate(fc.fix_monotone(apply_calibration(f, raw["test"]))[0], tgt, meta)
        for k, f in fits.items()
    }
    ctx.path("model").mkdir(exist_ok=True)
    torch.save(
        checkpoint(net, c, names, dyn_names, norm, cals, cal), ctx.path("model", "f2_gru.pt")
    )
    return metrics
