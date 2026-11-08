# 0085: Replay model `f2_gru.pt` replaced by the F2 v2 three-seed ensemble, trained on a Colab A100

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: 0071 (deployed replay model `f2_gru-c75aafc1-s2`); completes 0084 §6 (promotion rule), whose comparison was "not reached" that morning
- Affects: `artifacts/f2_gru.pt`, `artifacts/f2_gru_provenance.json`, `artifacts/eval_results.json`, `data/processed/forecasts.parquet` (private, regenerated); spec 02 §8; `docs/TRAINING_GPU.md`

## Context
Decision 0084 defined the v2 recipe (`config/train/f2_gru_v2.yaml`: site_level + cp_level features, cold-start day augmentation, one-cycle LR, 12 epochs x 3M windows) and the rule for replacing the replay model, but the full suite never ran: the laptop needs about 5 minutes per epoch on MPS and no cloud box was available. The live model (`f2_gru_live.pt`, 0084) is a different, shorter recipe selected on the real AmpWhere window and is not touched here.

Today the suite ran on Google Colab (A100-SXM4-40GB, Vlad's Colab Pro) from the `docker/pack_cloud.sh` tarball (`ppc_cloud_045e9c2.tar.gz`, HEAD of main after 0084 plus the seven private parquet files). The Colab terminal was used to run the three seeds in parallel after seed 0 showed the GPU only a third busy.

## Decision
1. `artifacts/f2_gru.pt` is the ensemble `f2_gru_v2_ens-11fd8353-s0` (mean of the hazard logits of seeds 0, 1, 2 of `f2_gru_v2`, calibration `auto` on val, 0084 §4). `artifacts/f2_gru_provenance.json` names the run; the run dirs (`runs/f2_gru_v2-aa199aa4-s0`, `runs/f2_gru_v2-913f16ad-s1`, `runs/f2_gru_v2-f5e01579-s2`, `runs/f2_gru_v2_ens-11fd8353-s0`) are kept on tudor's laptop and in the Colab results tarball, not in git.
2. `data/processed/forecasts.parquet` (2025-02-28 to 2025-04-29) and `artifacts/eval_results.json` (the eight demo days, `RL_AGENT=gru`) were regenerated with the new model, on the same A100, with the commands of decision 0084 §6.
3. The baseline for the rule is `runs/f2_gru-97ea9614-s0`: the deployed config retrained once on the regenerated data (0084), so both models see the same split.
4. The live model and its real-data calibration (0084) stay as they are. This decision changes replay, the evaluator page and the agent's forecasts only.

## Measurements (synthetic test split, regenerated data, same windows for every row)

| Run | y15 AUC / PR-AUC / Brier | y30 AUC / PR-AUC / Brier | y60 AUC / PR-AUC / Brier |
|---|---|---|---|
| baseline `f2_gru-97ea9614-s0` (deployed config, isotonic) | 0.9665 / 0.6036 / 0.0054 | 0.9449 / 0.5836 / 0.0110 | 0.9010 / 0.4612 / 0.0261 |
| v2 seed 0 | 0.9707 / 0.6468 / 0.0050 | 0.9516 / 0.6223 / 0.0103 | 0.9144 / 0.5031 / 0.0244 |
| v2 seed 1 | 0.9708 / 0.6474 / 0.0050 | 0.9519 / 0.6236 / 0.0103 | 0.9149 / 0.5061 / 0.0244 |
| v2 seed 2 | 0.9706 / 0.6475 / 0.0050 | 0.9514 / 0.6228 / 0.0103 | 0.9136 / 0.5042 / 0.0244 |
| **v2 ensemble (promoted)** | 0.9712 / 0.6514 / 0.0049 | 0.9523 / 0.6266 / 0.0102 | 0.9153 / 0.5084 / 0.0243 |

Training time on the A100: 36 s per epoch for one seed alone, about 45 s per epoch with three seeds in parallel; one seed end to end (feature cache shared, test metrics, forecasts, calibration) about 13 minutes.

Evaluator (eight demo days, 3,679 sessions, cost rule served as `rl_ppo`, 0069), before and after:

| Policy | saved % | waited sessions | mean wait (min) |
|---|---|---|---|
| naive quiet hours | 9.62 | 165 | 0.126 |
| cost rule on deployed `f2_gru-c75aafc1-s2`, main's `eval_results.json` (0069, original forecasts), λ 0.1 | 55.25 | 83 | 0.029 |
| cost rule on the deployed model, forecasts regenerated on the rebuilt data (same data as below), λ 0.1 | 54.44 | 102 | 0.032 |
| **cost rule on the v2 ensemble, λ 0.1 (new `eval_results.json`)** | **56.46** | **96** | **0.035** |
| v2 ensemble, λ 0.01 / 0.03 / 0.3 / 1.0 | 56.65 / 56.61 / 55.87 / 54.09 | 95 / 94 / 95 / 92 | 0.037 / 0.038 / 0.028 / 0.017 |
| oracle | 78.84 | 0 | 0 |

On the same data the v2 ensemble saves 2.0 points more energy than the deployed model and makes 6 fewer sessions wait. Against the numbers main carried (older forecasts file) it saves 1.2 points more with 13 more waits out of 3,679 sessions.

## Alternatives considered
- Keep `f2_gru-c75aafc1-s2` (0071). Rejected: the v2 ensemble beats the regenerated baseline on all nine test metrics, which is the rule of 0084 §6.
- Promote seed 0 alone (ready 10 minutes earlier). Rejected: the ensemble is better than every member on all nine metrics (y30 PR-AUC 0.6266 vs 0.6223 to 0.6236) and the three seeds agree to the third decimal, so the averaged model is the safer one to ship.
- Train on the laptop (MPS). Rejected: 12 epochs take about 40 minutes per seed; the three seeds would have missed the deadline.

## Consequences
- The Docker demo and the evaluator page show the new forecasts after `docker compose restart ppc-api` (artifacts and data are bind mounts). The server (0062/0063) needs `artifacts/` from git and the regenerated `forecasts.parquet` copied by hand, because `data/` is not in git.
- The replay metrics keep the mock-signal caveat of 0052: the replay model still uses `ppcblue_route_intent` and `maps_ev_count` from `signals.parquet`.
- Training recipe for the next retrain: pack with `docker/pack_cloud.sh`, drag the tarball into a Colab Pro A100 runtime, `bash docker/train_cloud.sh setup && check`, then the three seeds in parallel from the Colab terminal; see `docs/TRAINING_GPU.md`.
