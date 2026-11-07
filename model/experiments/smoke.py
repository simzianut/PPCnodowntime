"""Smoke trainer: tiny seeded MLP on synthetic data to check GPU access and determinism."""

from __future__ import annotations

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from model.experiments.registry import register
from model.experiments.runner import RunContext
from model.repro import seed_worker, torch_generator


@register("smoke", kind="forecaster", promote={"model/smoke.pt": "smoke.pt"})
def train(ctx: RunContext) -> dict:
    p = ctx.config.get("params", {})
    n, d = int(p.get("n_samples", 2048)), int(p.get("n_features", 16))
    hidden = int(p.get("hidden", 32))
    rng = np.random.default_rng(ctx.seed)
    x = rng.normal(size=(n, d)).astype(np.float32)
    w = rng.normal(size=d).astype(np.float32)
    y = ((x @ w + 0.3 * rng.normal(size=n)) > 0).astype(np.float32)
    ds = TensorDataset(torch.from_numpy(x), torch.from_numpy(y))
    loader = DataLoader(
        ds,
        batch_size=int(p.get("batch_size", 128)),
        shuffle=True,
        generator=torch_generator(ctx.seed),
        worker_init_fn=seed_worker,
    )
    net = nn.Sequential(nn.Linear(d, hidden), nn.ReLU(), nn.Linear(hidden, 1)).to(ctx.device)
    opt = torch.optim.Adam(net.parameters(), lr=float(p.get("lr", 0.01)))
    loss_fn = nn.BCEWithLogitsLoss()
    loss = float("nan")
    for epoch in range(int(p.get("epochs", 5))):
        total = 0.0
        for xb, yb in loader:
            xb, yb = xb.to(ctx.device), yb.to(ctx.device)
            opt.zero_grad()
            out = loss_fn(net(xb).squeeze(1), yb)
            out.backward()
            opt.step()
            total += out.item() * len(xb)
        loss = total / n
        ctx.log.info("epoch %d loss %.6f", epoch, loss)
    with torch.no_grad():
        pred = (net(ds.tensors[0].to(ctx.device)).squeeze(1) > 0).float().cpu()
    acc = float((pred == ds.tensors[1]).float().mean())
    ctx.path("model").mkdir(exist_ok=True)
    torch.save(net.state_dict(), ctx.path("model", "smoke.pt"))
    return {"loss": loss, "accuracy": acc, "device": ctx.device}
