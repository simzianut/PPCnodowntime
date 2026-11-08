# 0084: F2 v2: site-level features, cold-start augmentation, seed ensemble, an honest live model and a calibration fitted on real AmpWhere arrivals

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: part of 0070 (the live forecaster is no longer the full-feature `f2_gru.pt`)
- Affects: spec 02 §2, §5, §8; CONTRACTS (env vars, training commands); model/forecast_data.py, model/features.py, model/gru_forecaster.py, model/experiments/{f2_gru,ensemble}.py, model/live_calibration.py, api/live_mode.py, config/train/, docker/train_cloud.sh, docker/pack_cloud.sh

## Context
The deployed model (`f2_gru-c75aafc1-s2`, 0071) was measured on the real PPC Blue statuses collected overnight 2026-11-07/08 (6.5 h, 671 CPs, 44 arrivals), driven exactly as live mode drives it:

| y30, real data | AUC | PR-AUC | Brier | mean p / observed |
|---|---|---|---|---|
| deployed, with the mocked live signals | 0.756 | 0.011 | 0.0118 | 0.020 / 0.005 |
| same network, signals removed | 0.819 | 0.023 | 0.0049 | 0.003 / 0.005 |
| F0 seasonal | 0.717 | 0.013 | 0.0062 | 0.026 / 0.005 |

- In training, `ppcblue_route_intent` and `maps_ev_count` are drawn from the true future arrivals (0039, 0052), so the network learned "intent means a near-certain arrival". Live mode samples them from the F0 rate (0070), so they carry no information (0.65% of mocked intents coincided with a real arrival), and the model emitted p30 > 0.4 on 441 slots with an observed rate of 0.9%. Live `maps_ice_count` is also emitted every slot instead of hourly, which puts `weighted_flow_15m` 4.5 standard deviations above its training mean, and `traffic_index` is a constant 0.5 without the private Mapbox file.
- With the signals removed the network under-predicts at 15 and 30 minutes because its arrival-history features start at zero in live mode (no session history, 0071) and such rows were rare in training.
- The cost rule deep-sleeps a DC charge point whenever p15 < 0.44% (λ 0.1, current power profiles), so this under-prediction turns into 3-minute waits for 32–43% of real arrivals.
- Decision 0079 added `cp_level` and the calibration choice, but the `f2_improve` suite was never run, so nothing of it reached `artifacts/`.
- The private training files were not on this laptop. They were rebuilt with the dataset pipeline from the public Dundee files and Open-Meteo (981,486 sessions on 671 CPs, identical to 0038), with the synthetic traffic curve instead of Mapbox (no token).

## Decision
1. **Feature group `site_level`** (5 features, all from data at or before t): arrivals at the location in the last 1 h and 3 h, the location's same-slot ±30 min mean over the last 4 weeks, its 7-day arrival rate normalised by the history available (as `cp_level`), and the number of charge points at the location that are charging now. Batch and online builders have parity (test). `FEATURE_VERSION` is 3.
2. **Training recipe v2** (`config/train/f2_gru_v2.yaml`): hidden 96, one-cycle learning rate (warm-up 10%, cosine decay), AMP, 12 epochs of 3M windows, patience 4, calibration `auto` (0079), and **cold-start augmentation**: 10% of training windows get their arrival-history features replaced by the live cold-start values (0 counts, 28 days since the last arrival). The network therefore sees the state a live run starts in.
3. **Two models.** `artifacts/f2_gru.pt` (full features, mocked signals included) serves replay and the evaluator, where the signals exist in `signals.parquet`. `artifacts/f2_gru_live.pt` (`config/train/f2_gru_live.yaml`: `calendar_history + cp_level + site_level`, no traffic and no mocked signals) serves live mode; `api.live_mode` prefers it when present, `LIVE_F2_MODEL` still overrides. Live mode keeps sampling signals for the policy's intent override and the station chart unless `LIVE_SIGNALS=off`; the live model does not read them either way. On the real window the mocked intents only cost energy (sleep share 70% instead of 83%) and moved waits by chance, so the honest demo runs with `LIVE_SIGNALS=off`.
4. **Seed ensemble.** `python -m model.experiments ensemble` averages the hazard logits of the three seeds of a config, calibrates the average on val and writes a run dir, so `promote` and provenance work unchanged. Members must share features and normalisation.
5. **Real-data calibration.** `python -m model.live_calibration` replays the collected statuses through the online forecaster as a live run would, fits one affine map `a·l + b` on the hazard logits against the observed first-arrival buckets (2 parameters, the only thing that is safe with tens of positives), writes `artifacts/f2_live_calibration.json` with before/after metrics and a second-half hold-out, and live mode applies it when the `model` field matches the live model's file name. On the deployed model the hold-out y30 Brier goes from 0.0116 to 0.0026 and the mean prediction from 0.022 to 0.005 against 0.0026 observed.
6. **Promotion rule.** `f2_gru.pt` is replaced by the v2 ensemble only if it beats the deployed model on test PR-AUC and Brier for y15, y30 and y60 on the same (regenerated) data; the baseline config is rerun once in the suite for that comparison. `f2_gru_live.pt` is promoted from the live ensemble, then calibrated on the real statuses and re-measured with the live evaluation. `forecasts.parquet` and `eval_results.json` are regenerated after a promotion.
7. **Training runs** on a rented cloud GPU without Docker: `docker/pack_cloud.sh` packs HEAD plus the seven private data files, `docker/train_cloud.sh all` runs setup, check, smoke, the `f2_v2` suite, the ensembles and packs the results. `device: mps` is accepted for Apple-silicon fallback runs (never chosen by `auto`; run with `deterministic=false`).

## Alternatives considered
- Keep one model and zero the signal features live: in training a missing intent lowers the probability, so the model would under-predict everywhere.
- Fix the live sampler to be consistent with training: impossible, the training signals know the future.
- A transformer or a neural point process: no published gain on per-charger data this sparse (research note in the task); the hazard head stays.
- Isotonic calibration on the real data: too many knots for 44 positives.
- Class reweighting or focal loss: hurts calibration, which is what the cost rule consumes.
- Fine-tune the GRU on the real statuses: hours of data, unsafe; only the 2-parameter map is fitted.

## Consequences
- Live forecasts become honest: AUC around 0.8 on real data instead of a model that is confidently wrong whenever a mocked intent appears; fewer unnecessary deep sleeps once calibrated.
- The replay metrics on the full feature set keep the mock-signal caveat of 0052.
- `FEATURE_VERSION` 3 rebuilds the feature cache (about 2 GB, 7 minutes on the laptop).
- Open: traffic from Mapbox is not in the regenerated signals (synthetic curve); a traffic feature is therefore left out of the live model.
