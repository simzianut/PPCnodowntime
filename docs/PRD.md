# PRD: PPCnodowntime

Status: v1, 2026-11-07. Owner: tudor. Built for the PPC hackathon challenge (under 24h).

## 1. Problem

PPC Blue runs a public EV charging network in Romania: 450+ locations and 1,400+ charge points (press figures, Aug 2026). Every charge point stays powered on around the clock. A point with no car plugged in still draws idle power: about 10 W for an AC point and about 150 W for a DC fast charger (placeholders, to be confirmed). Many points sit idle for hours at night, on quiet highway stretches and at retail sites outside opening hours. That energy is wasted.

Turning points off on a fixed schedule is crude. It ignores holidays, weather and drivers who are already on their way. If a driver reaches a point that is asleep, they have to wait for it to wake up.

## 2. Product

PPCnodowntime is an **AI agent that puts idle PPC charge points into standby or deep sleep, and wakes them before an electric car arrives**.

The core idea is to predict demand from **electric** vehicles only, not general traffic:
- **PPC Blue route intents** match arrivals almost 1:1. A user who starts navigating to a PPC station is very likely to plug in there.
- **Waze and Google Maps traffic** counts cars tagged as EVs at full weight and combustion-engine cars at a very low weight. Most users never set a vehicle type, so EV counts there are partial and noisy.
- **Context:** time of day, weekday, holidays, weather, the station's own usage history, and how many EVs are registered in its county.

The agent has two parts:
1. **Demand forecaster.** Estimates the probability that an EV arrives at each charge point within the next 15, 30 and 60 minutes.
2. **RL decision agent.** A reinforcement-learning policy (PPO) that chooses, every 5 minutes, whether each charge point stays IDLE, goes to STANDBY or goes to DEEP_SLEEP. It balances energy saved against the waiting time it causes drivers.

A single **global autonomy switch** controls what the agent does with its decisions:
- **ACT:** the agent applies its commands to the charge points.
- **RECOMMEND:** the agent's commands go to an operator to approve or reject.

## 3. Users

| User | Need |
|---|---|
| PPC network operator | Cut idle energy without hurting drivers. See and control what the agent does. |
| PPC management / hackathon judges | Clear evidence of how much energy is saved and at what cost to drivers, compared with today and with a naive schedule. |
| EV driver (indirect) | Arrive and charge with little or no added wait. The PPC Blue app does not change. |

## 4. Goals and metrics

Headline metrics. Each one is computed on the same test days for every policy.

| Metric | Definition |
|---|---|
| **Energy saved** | Overhead energy (all consumption except energy delivered to cars) compared with always-on, in kWh and as a percentage |
| **Added wait minutes** | Extra time drivers wait because a charge point was asleep, reported as total, mean per session and p95 |

Drivers are never "lost": a driver who finds a sleeping point waits for it to wake up. See [decision 0006](decisions/0006-metrics-and-no-lost-sessions.md).

Policies compared:
1. **Always-on:** today's situation, where every point stays IDLE when not charging.
2. **Naive baseline:** each point sleeps during its historically quietest hours, on a fixed schedule.
3. **Cost rule:** sleep when the forecast arrival probability × the cost of a wait is lower than the energy saved. This is also the fallback if RL underperforms.
4. **RL agent (PPO):** the main product.

Pitch chart: **energy saved vs added wait**. It plots one curve for the RL agent and one for the cost rule, each traced by sweeping λ, the weight on waiting time. The two baselines appear as single points.

> Update: the app no longer shows this curve. It shows idle energy at night and by day, before (Always on) vs after (RL agent) as a bar chart, and added wait only as the RL agent's average wait per charge; the λ curve stays in `GET /api/eval`. See [decision 0065](decisions/0065-sidebar-two-policies-separate-wait.md).

## 5. Scope

### In scope (must-have)
- A national network of PPC charge points with real coordinates, built from free online sources: 155 locations in 34 counties, about a third of the network. Stations are not padded with synthetic ones. See [0014](decisions/0014-station-list-from-open-sources.md).
- A usage history built from real Dundee (UK) charging sessions, mapped onto Romanian charge points. See [0002](decisions/0002-dundee-donor-sessions.md).
- Mocked PPC Blue and Waze/Google signals, derived from those mapped sessions.
- A demand forecaster, a PPO agent, a cost-rule fallback and two baselines.
- A simulator that replays test days in 5-minute slots, with all policies running side by side on the same data.
- A live mode in which the agent runs on live charge-point statuses collected during the hackathon.
- A web app with:
  - a Romania map showing each charge point's state
  - a panel comparing policies on the headline metrics
  - replay controls
  - the autonomy switch and a feed of recommendations to approve or reject
  - a station detail panel

### Stretch
- An LLM layer that explains the agent's decisions in plain language.
- Real power-draw numbers from charger datasheets or from PPC.

### Non-goals
- Changing the PPC Blue app or its user experience.
- Controlling real hardware. Commands only change the simulated or recorded state.
- Load balancing the grid, pricing, or managing charging power during a session.
- Covering stations of other operators.

## 6. Demo story (about 3 minutes)
1. Show the Romania map with every PPC charge point. Explain where the data comes from.
2. Pick a test day and press play at high speed. Points turn grey (asleep) overnight and on quiet highways, then wake up before cars arrive, triggered by PPC Blue route intents.
3. The panel shows the agent against always-on and the naive schedule: energy saved and added wait.
4. Open a station: forecast arrivals vs actual arrivals, its state over time, and the agent's decisions.
5. Flip the switch to RECOMMEND and approve a couple of recommendations as an operator.
6. Switch to live mode: the agent acts on real statuses collected today.
7. Show the energy-vs-wait curve, then extrapolate the savings to all 1,400+ points per year. (Now the before/after energy bars and the average wait per charge, see [decision 0065](decisions/0065-sidebar-two-policies-separate-wait.md).)

## 7. Data sources (summary)

| Need | Source |
|---|---|
| Station list | OpenStreetMap, the OpenChargeMap export, the archived Hermes Flow network page, and press releases. 155 locations (spec 03 §1). |
| Usage history | Dundee City Council session data, 2021–2025 |
| EV density per county | DRPCIV vehicle registrations (data.gov.ro), end of 2025 |
| Weather | Open-Meteo historical and forecast APIs |
| Holidays | Python `holidays` package (GB-SCT for training, RO at run time) |
| Live status | AmpWhere poller, plus an official PPC feed if provided |
| PPC Blue / Waze / Google signals | Mocked. See spec 01. |

## 8. Constraints and risks

| Risk | Mitigation |
|---|---|
| Less than 24h to build | Each module has must-have and stretch tiers. Shared fixtures let modules work in parallel. The cost-rule fallback is ready if RL fails. |
| PPO doesn't converge in time | Cost rule using the same forecaster. Both are always shown. |
| AmpWhere's terms forbid scraping | A team decision with the risk recorded ([0003](decisions/0003-live-status-collection.md)). Raw scraped data is never committed. The collector is pluggable so PPC's official feed can replace it. |
| PPC Blue site is behind a bot wall, and PPC won't provide a feed | We do not bypass it. We use free sources (155 of 450+ locations) and scale results up. |
| Dundee data licence not stated | Raw files are not redistributed. The licence is recorded as a known gap. |
| Power numbers are placeholders | Kept in one config file. The pitch states them as assumptions. |
| UK behaviour ≠ Romanian behaviour | Demand is scaled by county EV density and shifted onto the Romanian calendar. The live data is used to check the mapping. |

## 9. Modules

| # | Module | Spec |
|---|---|---|
| 0 | Foundation: repo, conventions, contribution rules | [specs/00-foundation.md](specs/00-foundation.md) |
| 1 | Dataset curation | [specs/01-dataset.md](specs/01-dataset.md) |
| 2 | EV demand model + decision agent | [specs/02-model-agent.md](specs/02-model-agent.md) |
| 3 | PPC station network + states | [specs/03-stations.md](specs/03-stations.md) |
| 4 | Simulator backend | [specs/04-sim-backend.md](specs/04-sim-backend.md) |
| 5 | Simulator frontend | [specs/05-frontend.md](specs/05-frontend.md) |

## 10. Glossary
- **Location:** a physical site (e.g. one Penny car park). It has one or more charge points.
- **Charge point (CP):** one controllable charging unit with one or more connectors. The agent acts at this level.
- **Overhead energy:** electricity a CP consumes that is not delivered to a car.
- **Wake time:** how long a CP takes to go from a sleep state to ready to charge.
- **Slot:** a 5-minute simulation step.
- **Donor charger:** a Dundee charger whose real session history is mapped onto a Romanian CP.
