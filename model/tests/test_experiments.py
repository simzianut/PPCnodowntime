"""Tests for model.experiments (CPU only, tmp dirs)."""

import csv
import json

import pytest
import yaml

from model.experiments import registry, runner, suite
from model.experiments.__main__ import main

REPO_SMOKE = "config/train/smoke.yaml"
CONTRACT_KEYS = {
    "run_id", "config_sha256", "seed", "deterministic", "git_sha", "git_dirty", "data",
    "packages", "python", "torch", "cuda", "cudnn", "gpu_name", "driver", "host",
    "started_at", "finished_at", "duration_s", "status",
}  # fmt: skip


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def _cfg(seed=0, **over):
    cfg = runner.resolve_config(REPO_SMOKE, seed, over)
    cfg["device"] = "cpu"
    return cfg


def _metrics(run_dir):
    return (run_dir / "metrics.json").read_text()


def test_hash_stable_and_changes_with_override():
    a, b = _cfg(), _cfg()
    assert runner.config_sha256(a) == runner.config_sha256(b)
    assert runner.config_sha256(a) != runner.config_sha256(_cfg(**{"params.epochs": 2}))


def test_override_parsing():
    assert runner.parse_override("params.lr=0.5") == ("params.lr", 0.5)
    assert runner.parse_override("a.b=[1, 2]") == ("a.b", [1, 2])
    assert runner.parse_override("x=true") == ("x", True)
    with pytest.raises(ValueError):
        runner.parse_override("novalue")
    cfg = runner.apply_overrides({"params": {"lr": 1}}, {"params.lr": 2, "new.deep.key": "s"})
    assert cfg == {"params": {"lr": 2}, "new": {"deep": {"key": "s"}}}


def test_run_id_format():
    cfg = _cfg(seed=3)
    assert cfg["deterministic"] is True
    assert runner.make_run_id(cfg) == f"smoke-{runner.config_sha256(cfg)[:8]}-s3"


def test_same_seed_identical_different_seed_differs():
    d1 = runner.run_config(_cfg(0))
    d2 = runner.run_config(_cfg(0), overwrite=True)
    assert d1 == d2
    first = _metrics(d1)
    runner.run_config(_cfg(0), overwrite=True)
    assert _metrics(d1) == first
    d3 = runner.run_config(_cfg(1))
    assert _metrics(d3) != first


def test_manifest_keys_and_status():
    d = runner.run_config(_cfg())
    m = json.loads((d / "manifest.json").read_text())
    assert CONTRACT_KEYS <= set(m)
    assert m["status"] == "ok"
    assert (d / "config.yaml").exists() and (d / "train.log").exists()
    assert (d / "model" / "smoke.pt").exists()


def test_failing_trainer_marks_failed():
    def boom(ctx):
        raise RuntimeError("kaboom")

    registry._REGISTERED["boom"] = registry.TrainerSpec(boom, "forecaster")
    cfg = _cfg()
    cfg["name"], cfg["model"] = "boom", "boom"
    with pytest.raises(RuntimeError):
        runner.run_config(cfg)
    m = json.loads(
        (runner.repro.runs_dir() / runner.make_run_id(cfg) / "manifest.json").read_text()
    )
    assert m["status"] == "failed" and "kaboom" in m["error"]


def test_refuse_rerun_without_overwrite():
    runner.run_config(_cfg())
    with pytest.raises(FileExistsError):
        runner.run_config(_cfg())


def test_data_files_hashed(dirs):
    (dirs / "data" / "processed").mkdir(parents=True)
    (dirs / "data" / "processed" / "x.bin").write_bytes(b"abc")
    cfg = _cfg(**{"data": {"signals": "x.bin"}})
    d = runner.run_config(cfg)
    m = json.loads((d / "manifest.json").read_text())
    assert m["data"]["x.bin"]["bytes"] == 3 and len(m["data"]["x.bin"]["sha256"]) == 64


def test_suite_summary_and_skip(dirs, capsys):
    s = dirs / "s.yaml"
    s.write_text(
        yaml.safe_dump(
            {
                "name": "t",
                "runs": [{"config": REPO_SMOKE, "seeds": [0, 1], "set": {"device": "cpu"}}],
                "summary_metrics": ["loss", "accuracy"],
            }
        )
    )
    out = suite.run_suite(s)
    rows = list(csv.DictReader((out / "summary.csv").open()))
    assert len(rows) == 2 and rows[0]["seed"] == "0" and float(rows[0]["loss"]) > 0
    assert len(json.loads((out / "suite.json").read_text())["run_ids"]) == 2
    assert (out / "summary.md").exists()
    suite.run_suite(s)
    assert "skip" in capsys.readouterr().out


def test_promote_copies_and_provenance(dirs):
    d = runner.run_config(_cfg())
    written = suite.promote(d)
    art = dirs / "artifacts"
    assert (art / "smoke.pt").exists() and written == [art / "smoke.pt"]
    prov = json.loads((art / "smoke_provenance.json").read_text())
    assert prov["run_id"] == d.name and "config_sha256" in prov and "git_sha" in prov


def test_promote_refuses_failed(dirs):
    d = runner.run_config(_cfg())
    m = json.loads((d / "manifest.json").read_text())
    m["status"] = "failed"
    (d / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(RuntimeError):
        suite.promote(d)


def test_cli_run(dirs):
    main(["run", REPO_SMOKE, "--seed", "2", "--set", "device=cpu", "--set", "params.epochs=1"])
    assert len(list((dirs / "runs").glob("smoke-*-s2"))) == 1
