# Engineering Project Plan: Cost-Effective Smart Low-Power Modes for EV Charging Infrastructure

**Author Role:** Senior Electrical Systems Engineer  
**Target Infrastructure:** PPC blue Romania Charging Network (Single-Station Unit Economics Across 13 Observed Capacities)  
**State Machine Reference:** [`stations/state.py`](file:///E:/Side%20Projects/PPCnodowntime/stations/state.py) (`IDLE`, `STANDBY`, `DEEP_SLEEP`, `WAKING`, `CHARGING`, `OFFLINE`)  
**Operational Duty Cycle:** $4\text{ hours/day}$ Active Charging ($120\text{ h/month}$) $\mid$ $20\text{ hours/day}$ Unoccupied ($600\text{ h/month}$)  
**Energy Price Benchmark (Romania, November 2026):**  
* **Net B2B Commercial Cost (Excl. 21% VAT):** $1.15\text{ RON/kWh}$ ($0.685\text{ RON/kWh}$ PPC active energy + $0.465\text{ RON/kWh}$ ANRE regulated distribution, transport, system services, green certificates, and CfD/excise taxes in Rețele Electrice zones).  
* **Gross Retail Benchmark (Incl. 21% VAT):** $1.39\text{ RON/kWh}$ (PPC Fix Online standard tariff).

---

## 1. Executive Engineering Summary & Cost-Effectiveness Thesis

From an electrical systems engineering standpoint, operating an EV charging station during its $20\text{ unoccupied hours/day}$ in **Standard Idle Mode** creates three compounding inefficiencies:
1. **Parasitic Active Power Loss ($P_{\text{idle}}$):** Continuous energization of outdoor high-nit HMI displays ($15\text{--}35\text{ W}$), POS/RFID continuous RF polling fields ($8\text{--}15\text{ W}$), full-brightness RGB status strips ($12\text{--}25\text{ W}$), narrow-band anti-condensation heaters ($20\text{--}65\text{ W}$), and quiescent AC/DC power module control buses ($15\text{--}60\text{ W}$).
2. **Capacitive Reactive Power Penalties ($Q_c$ & Low $\cos \varphi$):** When high-power DC chargers ($50\text{--}300\text{ kW}$) sit in standard idle with their AC input EMI/LCL filters connected to the $400\text{ V}$ three-phase grid, they exhibit a highly leading (capacitive) power factor ($\cos \varphi \approx 0.15\text{--}0.35$). Under ANRE billing regulations in Romania, a monthly power factor below $\cos \varphi = 0.90$ incurs reactive energy penalties on the commercial smart meter.
3. **Accelerated Component Thermal & Optical Degradation:** Running $1000+\text{ nit}$ LCD backlights and auxiliary cooling fans for $8,760\text{ hours/year}$ instead of the $1,460\text{ hours/year}$ of actual vehicle charging reduces Mean Time Between Failures (MTBF) by up to $75\%$.

### The Dual Low-Power Architecture (`STANDBY` vs. `DEEP_SLEEP`)

To balance energy savings against wake latency and customer satisfaction, the system implements two distinct low-power states:

1. **Smart Standby Mode (`STANDBY`): Fast Wake / Medium Savings**
   * **Electrical Strategy:** Auxiliary components are dimmed or duty-cycled (HMI display at $10\text{--}20\%$ brightness, holster LEDs at $30\%$ PWM, RFID polling duty-cycled), but **the main AC input contactors remain CLOSED** and the SiC power stacks remain in hot-standby with their DC-link capacitors energized.
   * **Engineering Trade-off:** Yields **$54\%\text{ to }64\%$ active power reduction** ($41.40\text{ to }100.05\text{ RON/month}$ Net savings on DC chargers). Because the power modules and capacitors remain energized, waking to `IDLE` or `CHARGING` is near-instantaneous ($<1.5\text{ s}$ hardware latency, $10\text{ s}$ CSMS/OCPP window) with **zero capacitor inrush current and zero contactor wear**.
   * **AC Infrastructure Role:** For Tier 1 AC chargers ($11\text{--}22\text{ kW}$), which have no internal AC/DC converters, `STANDBY` is the primary low-power state.

2. **Smart Deep Sleep Mode (`DEEP_SLEEP`): Maximum Efficiency / Remote-Triggered Wake**
   * **Electrical Strategy:** In addition to dimming auxiliaries (HMI display shut off / RAM suspend, holster LEDs at $12\%$ PWM, POS/RFID cut), the station **physically opens the main AC contactors**, isolating the AC/DC SiC power stacks and LCL input filters completely. The 4G LTE/OCPP controller, safety watchdog, and PPC blue logo LED remain $100\%$ operational.
   * **Engineering Trade-off:** Maximizes active power reduction (**$80\%\text{ to }86\%$**, saving $52.44\text{ to }136.62\text{ RON/month}$ Net per station) and completely eliminates off-peak capacitive reactive power ($Q_c$) injection. Waking requires a soft-start pre-charge sequence ($3\text{--}8\text{ s}$ hardware latency, $180\text{ s}$ CSMS arrival window) to charge the DC-link capacitor bank before re-closing the main contactor.
   * **AC Infrastructure Parity:** In accordance with [`stations/state.py`](file:///E:/Side%20Projects/PPCnodowntime/stations/state.py), AC chargers do not support `DEEP_SLEEP`; any command requesting `DEEP_SLEEP` on an AC charger is automatically clamped to `STANDBY`.

---

## 2. Subsystem State & Power Architecture

### 2.1 Component-Level Power Allocation Matrix (Reference DC Fast Charger)

| Subsystem / Component | Standard Idle (`IDLE`) | Smart Standby (`STANDBY`) | Smart Deep Sleep (`DEEP_SLEEP`) | Idle Draw ($P_{\text{idle}}$) | Standby Draw ($P_{\text{standby}}$) | Deep Sleep Draw ($P_{\text{sleep}}$) | Electrical Engineering Justification |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **4G LTE Router & Linux SBC (OCPP Controller)** | **100% Operational** | **100% Operational** | **100% Operational** | $8.0\text{--}12.0\text{ W}$ | $8.0\text{--}12.0\text{ W}$ | $6.0\text{--}8.0\text{ W}$ | Mandatory continuous TLS WebSocket (`wss://`) connection to CSMS across all modes for remote wake-up telemetry. |
| **Main PPC blue Logo Backlight (24V DC LED)** | **100% Operational** | **100% Operational** | **100% Operational** | $6.0\text{--}12.0\text{ W}$ | $6.0\text{--}12.0\text{ W}$ | $6.0\text{--}12.0\text{ W}$ | Mandatory constant illumination so drivers visually locate the station at night; scales with cabinet size. |
| **Safety Watchdog (RCD, Surge, Door/Tilt, E-Stop)** | **100% Operational** | **100% Operational** | **100% Operational** | $1.5\text{--}2.5\text{ W}$ | $1.5\text{--}2.5\text{ W}$ | $1.5\text{--}2.5\text{ W}$ | IEC 61851-1 / IEC 61851-23 mandatory safety monitoring cannot be bypassed in any operational mode. |
| **Connector Holster RGB LEDs & Status Strips** | **100% Brightness** | **Dimmed (30% PWM)** | **Low Beacon (10–15% PWM)** | $12.0\text{--}25.0\text{ W}$ | $3.5\text{--}6.0\text{ W}$ | $1.5\text{--}3.5\text{ W}$ | Standby dims holster LEDs to save power while keeping visual orientation; Deep Sleep drops to a low beacon. |
| **Cabinet Anti-Condensation Heaters & Thermals** | **Narrow Hysteresis** | **Dew-Point Guard** | **Dew-Point Guard** | $20.0\text{--}65.0\text{ W}$ | $0.0\text{--}5.0\text{ W}$ | $0.0\text{--}5.0\text{ W}$ | Thermostat deadband widened in both modes; heater only energizes if internal humidity approaches $85\%\text{ RH}$. |
| **10.1" / 15.6" Outdoor HMI Touch Display** | **On (50–100% Nit)** | **Dimmed (10–20% Nit)** | **Turned Off (RAM Suspend)** | $15.0\text{--}35.0\text{ W}$ | $3.0\text{--}7.0\text{ W}$ | $0.5\text{ W}$ | Standby preserves immediate readability at reduced power; Deep Sleep cuts backlight rail via MOSFET. |
| **AC/DC SiC Power Stacks & LCL Input Filters** | **Hot-Standby / Pre-Charged** | **Hot-Standby / Pre-Charged** | **Turned Off (AC Contactor Open)** | $15.0\text{--}60.0\text{ W}$ | $15.0\text{--}60.0\text{ W}$ | $0.0\text{ W}$ | **Core Differentiator:** Standby leaves main contactor closed for instant wake; Deep Sleep isolates stacks to eliminate active & reactive loss. |
| **RFID (ISO 14443) & Contactless POS Terminal** | **Continuous RF Polling** | **Duty-Cycled Polling** | **Turned Off (Passive Wake)** | $8.0\text{--}15.0\text{ W}$ | $1.5\text{--}3.0\text{ W}$ | $0.5\text{--}1.0\text{ W}$ | Standby polls RF field intermittently ($200\text{ ms}$ pulse / $2\text{ s}$); Deep Sleep disables RF carrier wave. |
| **Liquid-Cooling Pump (300 kW HPC Units Only)** | **Periodic Circulation** | **Low-Speed Sleep Mode** | **Turned Off** | $25.0\text{--}40.0\text{ W}$ | $8.0\text{--}15.0\text{ W}$ | $0.0\text{ W}$ | Standby runs pump at minimal idle tick; Deep Sleep shuts pump off completely when $I_{\text{DC}} = 0\text{ A}$. |

### 2.2 Engineering Limitations & Operational Trade-Offs

1. **Irreducible Auxiliary Power Floor:** Because the 4G modem, OCPP SBC, safety loop, and main PPC blue logo illumination remain continuously energized, power draw cannot drop to $0\text{ W}$. The auxiliary floor ranges from $5.5\text{ W}$ on $11\text{ kW}$ AC to $32.0\text{ W}$ on $300\text{ kW}$ DC in `DEEP_SLEEP`, and $5.5\text{ W}$ to $85.0\text{ W}$ in `STANDBY`.
2. **Wake Latency Comparison:**
   * **`STANDBY -> IDLE`:** Waking requires only ramping up display nit brightness and holster PWM. Physical hardware latency is $<1.5\text{ s}$ ($10\text{ s}$ OCPP/CSMS safety window). **No pre-charge sequence, no inrush current, and zero contactor cycle fatigue.**
   * **`DEEP_SLEEP -> IDLE`:** Requires closing the soft-start pre-charge relay, charging the DC-link capacitors to the rectified AC peak, boosting the bus voltage, closing the main AC contactor, and booting the ISO 15118 PLC modem. Physical hardware latency is $3\text{--}8\text{ s}$ ($180\text{ s}$ driver travel/arrival window in the simulator).
3. **Reactive Power Penalty Difference:**
   * In `STANDBY`, the AC input EMI/LCL filter capacitors remain tied to the grid, drawing small capacitive vars during off-peak hours.
   * In `DEEP_SLEEP`, the open main contactor physically disconnects the LCL filters, completely eliminating capacitive reactive power surcharges ($Q_c$) under ANRE regulations.

---

## 3. Reanalysis Across All 13 Observed Charging Capacities in Romania

In Romania's public charging network, the 13 observed capacities ($11, 17, 22, 43, 50, 60, 75, 85, 100, 150, 160, 200, 300\text{ kW}$) span three distinct hardware topologies:
* **Tier 1 — AC Three-Phase EVSE ($11\text{ kW}, 17\text{ kW}, 22\text{ kW}$):** No internal AC/DC power conversion. Supports `IDLE` and `STANDBY` only. In [`stations/state.py`](file:///E:/Side%20Projects/PPCnodowntime/stations/state.py), `DEEP_SLEEP` is clamped to `STANDBY` (`deep_sleep_w: null` in [`config/power_profiles.yaml`](file:///E:/Side%20Projects/PPCnodowntime/config/power_profiles.yaml)).
* **Tier 2 — High-Power AC & Mid-Range Modular DC ($43\text{ kW}, 50\text{ kW}, 60\text{ kW}, 75\text{ kW}, 85\text{ kW}, 100\text{ kW}$):** Floor-standing enclosures with $1\text{ to }3$ SiC rectifier modules. Supports both `STANDBY` (hot stack, dimmed auxiliary) and `DEEP_SLEEP` (isolated stack).
* **Tier 3 — Ultra-Fast High-Power DC / HPC ($150\text{ kW}, 160\text{ kW}, 200\text{ kW}, 300\text{ kW}$):** Multi-stack architectures ($2\text{ to }4$ stacks), larger thermal loops, and active liquid cooling on $300\text{ kW}$ units.

### 3.1 Single-Station Power, Energy & Monthly Cost Table ($600\text{ Unoccupied Hours/Month}$)

**Formulas applied per single station over a $30\text{-day}$ month ($20\text{ unoccupied hours/day} = 600\text{ h/month}$):**
$$E_{\text{state}} = \frac{P_{\text{state}} \times 600\text{ h}}{1000} \quad (\text{kWh/month})$$
$$\text{Cost}_{\text{Net}} = E_{\text{state}} \times 1.15\text{ RON/kWh} \qquad \text{Cost}_{\text{Gross}} = E_{\text{state}} \times 1.39\text{ RON/kWh}$$
$$\text{Savings}_{\text{Net}} = (E_{\text{idle}} - E_{\text{state}}) \times 1.15\text{ RON/kWh} \qquad \text{Power Reduction} = \frac{P_{\text{idle}} - P_{\text{state}}}{P_{\text{idle}}} \times 100\%$$

| Capacity | Hardware Topology | Idle Power ($P_{\text{idle}}$) | Standby Power ($P_{\text{standby}}$) | Deep Sleep ($P_{\text{sleep}}$) | Monthly Energy Idle | Monthly Energy Standby | Monthly Energy Deep Sleep | Monthly Cost Idle (Net / Gross)* | Monthly Cost Standby (Net / Gross)* | Monthly Cost Deep Sleep (Net / Gross)* | Standby Monthly Savings (Net / Gross)* | Deep Sleep Monthly Savings (Net / Gross)* | Power Red. Standby | Power Red. Deep Sleep |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **$11\text{ kW}$** | AC $3\phi$ Wallbox | $12.0\text{ W}$ | $5.5\text{ W}$ | *N/A* | $7.20\text{ kWh}$ | $3.30\text{ kWh}$ | *N/A* | $8.28$ / $10.01$ | $3.80$ / $4.59$ | *N/A* | **$4.49$ / $5.42\text{ RON}$** | *N/A (Clamped)* | $-54.2\%$ | *N/A* |
| **$17\text{ kW}$** | AC $3\phi$ Mini-Post | $18.0\text{ W}$ | $7.5\text{ W}$ | *N/A* | $10.80\text{ kWh}$ | $4.50\text{ kWh}$ | *N/A* | $12.42$ / $15.01$ | $5.18$ / $6.26$ | *N/A* | **$7.25$ / $8.76\text{ RON}$** | *N/A (Clamped)* | $-58.3\%$ | *N/A* |
| **$22\text{ kW}$** | AC $3\phi$ Pedestal | $25.0\text{ W}$ | $9.0\text{ W}$ | *N/A* | $15.00\text{ kWh}$ | $5.40\text{ kWh}$ | *N/A* | $17.25$ / $20.85$ | $6.21$ / $7.51$ | *N/A* | **$11.04$ / $13.34\text{ RON}$** | *N/A (Clamped)* | $-64.0\%$ | *N/A* |
| **$43\text{ kW}$** | AC/DC Multi-Cab | $65.0\text{ W}$ | $30.0\text{ W}$ | $16.0\text{ W}$ | $39.00\text{ kWh}$ | $18.00\text{ kWh}$ | $9.60\text{ kWh}$ | $44.85$ / $54.21$ | $20.70$ / $25.02$ | $11.04$ / $13.34$ | **$24.15$ / $29.19\text{ RON}$** | **$33.81$ / $40.87\text{ RON}$** | $-53.8\%$ | $-75.4\%$ |
| **$50\text{ kW}$** | DC Fast (HYC50) | $95.0\text{ W}$ | $35.0\text{ W}$ | $19.0\text{ W}$ | $57.00\text{ kWh}$ | $21.00\text{ kWh}$ | $11.40\text{ kWh}$ | $65.55$ / $79.23$ | $24.15$ / $29.19$ | $13.11$ / $15.85$ | **$41.40$ / $50.04\text{ RON}$** | **$52.44$ / $63.38\text{ RON}$** | $-63.2\%$ | $-80.0\%$ |
| **$60\text{ kW}$** | DC Fast ($2\times 30\text{k}$) | $105.0\text{ W}$ | $38.0\text{ W}$ | $20.0\text{ W}$ | $63.00\text{ kWh}$ | $22.80\text{ kWh}$ | $12.00\text{ kWh}$ | $72.45$ / $87.57$ | $26.22$ / $31.69$ | $13.80$ / $16.68$ | **$46.23$ / $55.88\text{ RON}$** | **$58.65$ / $70.89\text{ RON}$** | $-63.8\%$ | $-81.0\%$ |
| **$75\text{ kW}$** | DC Fast (Hyperch.) | $115.0\text{ W}$ | $42.0\text{ W}$ | $21.5\text{ W}$ | $69.00\text{ kWh}$ | $25.20\text{ kWh}$ | $12.90\text{ kWh}$ | $79.35$ / $95.91$ | $28.98$ / $35.03$ | $14.84$ / $17.93$ | **$50.37$ / $60.88\text{ RON}$** | **$64.52$ / $77.98\text{ RON}$** | $-63.5\%$ | $-81.3\%$ |
| **$85\text{ kW}$** | DC Fast (Derated) | $120.0\text{ W}$ | $45.0\text{ W}$ | $22.0\text{ W}$ | $72.00\text{ kWh}$ | $27.00\text{ kWh}$ | $13.20\text{ kWh}$ | $82.80$ / $100.08$ | $31.05$ / $37.53$ | $15.18$ / $18.35$ | **$51.75$ / $62.55\text{ RON}$** | **$67.62$ / $81.73\text{ RON}$** | $-62.5\%$ | $-81.7\%$ |
| **$100\text{ kW}$** | DC Fast ($2\times 50\text{k}$) | $130.0\text{ W}$ | $48.0\text{ W}$ | $23.0\text{ W}$ | $78.00\text{ kWh}$ | $28.80\text{ kWh}$ | $13.80\text{ kWh}$ | $89.70$ / $108.42$ | $33.12$ / $40.03$ | $15.87$ / $19.18$ | **$56.58$ / $68.39\text{ RON}$** | **$73.83$ / $89.24\text{ RON}$** | $-63.1\%$ | $-82.3\%$ |
| **$150\text{ kW}$** | DC HPC (HYC150) | $150.0\text{ W}$ | $55.0\text{ W}$ | $24.5\text{ W}$ | $90.00\text{ kWh}$ | $33.00\text{ kWh}$ | $14.70\text{ kWh}$ | $103.50$ / $125.10$ | $37.95$ / $45.87$ | $16.91$ / $20.43$ | **$65.55$ / $79.23\text{ RON}$** | **$86.60$ / $104.67\text{ RON}$** | $-63.3\%$ | $-83.7\%$ |
| **$160\text{ kW}$** | DC HPC ($4\times 40\text{k}$) | $160.0\text{ W}$ | $58.0\text{ W}$ | $25.5\text{ W}$ | $96.00\text{ kWh}$ | $34.80\text{ kWh}$ | $15.30\text{ kWh}$ | $110.40$ / $133.44$ | $40.02$ / $48.37$ | $17.60$ / $21.27$ | **$70.38$ / $85.07\text{ RON}$** | **$92.81$ / $112.17\text{ RON}$** | $-63.8\%$ | $-84.1\%$ |
| **$200\text{ kW}$** | DC HPC (HYC200) | $175.0\text{ W}$ | $65.0\text{ W}$ | $27.0\text{ W}$ | $105.00\text{ kWh}$ | $39.00\text{ kWh}$ | $16.20\text{ kWh}$ | $120.75$ / $145.95$ | $44.85$ / $54.21$ | $18.63$ / $22.52$ | **$75.90$ / $91.74\text{ RON}$** | **$102.12$ / $123.43\text{ RON}$** | $-62.9\%$ | $-84.6\%$ |
| **$300\text{ kW}$** | DC HPC (Liquid) | $230.0\text{ W}$ | $85.0\text{ W}$ | $32.0\text{ W}$ | $138.00\text{ kWh}$ | $51.00\text{ kWh}$ | $19.20\text{ kWh}$ | $158.70$ / $191.82$ | $58.65$ / $70.89$ | $22.08$ / $26.69$ | **$100.05$ / $120.93\text{ RON}$** | **$136.62$ / $165.13\text{ RON}$** | $-63.0\%$ | $-86.1\%$ |

*\*Note: **Net** = $1.15\text{ RON/kWh}$ (B2B commercial cost excluding $21\%$ VAT); **Gross** = $1.39\text{ RON/kWh}$ (including $21\%$ VAT).*

### 3.2 Engineering Observations from the Recalculation

1. **The Standby "Fast-Wake" Baseline:** Across all DC stations ($50\text{--}300\text{ kW}$), entering `STANDBY` cuts active idle power by **$\sim 63\%$**, securing **$41.40\text{ to }100.05\text{ RON/month}$ (Net)** in savings while maintaining zero wake penalty.
2. **The Deep Sleep Incremental Margin:** Moving from `STANDBY` to `DEEP_SLEEP` captures an additional **$11.04\text{ to }36.57\text{ RON/month}$ (Net)** per DC unit ($132\text{ to }439\text{ RON/year}$ per station). This extra margin is generated specifically by isolating the power stacks ($15\text{--}60\text{ W}$ quiescent draw) and cutting the display rail.
3. **Parity with Codebase Reference Profiles:** The recalculated table grounds the representative power classes in [`config/power_profiles.yaml`](file:///E:/Side%20Projects/PPCnodowntime/config/power_profiles.yaml):
   * `AC_22`: $P_{\text{idle}} = 25\text{ W}, P_{\text{standby}} = 9\text{ W}, P_{\text{sleep}} = \text{null}$
   * `DC_50`: $P_{\text{idle}} = 105\text{ W}, P_{\text{standby}} = 35\text{ W}, P_{\text{sleep}} = 20\text{ W}$
   * `DC_HPC`: $P_{\text{idle}} = 165\text{ W}, P_{\text{standby}} = 50\text{ W}, P_{\text{sleep}} = 25.5\text{ W}$

---

## 4. Electrical Systems Engineer's Cost-Effectiveness Decision Matrix

### Decision Rule 1: Zero-CAPEX Software Standby for $11\text{ kW} \text{--} 22\text{ kW}$ AC Stations
* **Financial Reality:** AC stations save between **$4.49\text{ RON}$ and $11.04\text{ RON/month}$ (Net)** in `STANDBY`.
* **Engineering Verdict:** **Do not install physical proximity hardware or attempt contactor isolation on AC wallboxes.** `STANDBY` must be deployed strictly as a **$0\text{ RON}$ CAPEX OTA configuration** that dims LED rings and duty-cycles RFID polling. In accordance with [`stations/state.py`](file:///E:/Side%20Projects/PPCnodowntime/stations/state.py), AC units never enter `DEEP_SLEEP`.

### Decision Rule 2: Dynamic Two-Tier Policy for $50\text{ kW} \text{--} 300\text{ kW}$ DC Stations
For DC fast and ultra-fast charging hubs, the choice between `STANDBY` and `DEEP_SLEEP` should follow an automated dispatch policy based on arrival forecast horizons:

```mermaid
flowchart TD
    Unoccupied["Station Unoccupied (No Vehicle Plugged In)"] --> CheckClass{"Charger Power Class"}
    CheckClass -->|AC_22| SetStandby["Command STANDBY\n(Dim LED / RFID duty-cycle)"]
    CheckClass -->|DC_50 / DC_HPC| CheckHorizon{"Predicted Arrival Horizon\n(p_arrival telemetry)"}
    CheckHorizon -->|Arrival Expected < 15-30 min| CmdStandby["Command STANDBY\n- Contactors remain CLOSED\n- Display & LEDs dimmed\n- 0s precharge / <1.5s wake"]
    CheckHorizon -->|Quiet Hours / Overnight > 30-60 min| CmdDeepSleep["Command DEEP_SLEEP\n- Contactors OPEN (0W stacks)\n- Eliminates Qc reactive vars\n- Max savings (-80% to -86%)\n- 3-8s pre-charge on wake"]
    
    CmdStandby --> ApproachEv{"Approach Signal Detected\n(Waze / Google Maps / App)"}
    CmdDeepSleep --> ApproachEv
    ApproachEv -->|Vehicle Approaches (<60s ETA)| WakeStation["Command IDLE / WAKING\n(Pre-charge stacks / brighten HMI)"]
```

* **Near-Term Horizon ($< 15\text{--}30\text{ min}$ to next arrival):** Dispatch **`STANDBY`**. Secures $63\%$ energy savings without cycling mechanical AC contactors or risking pre-charge latency for walk-up drivers.
* **Extended Quiet Periods ($> 30\text{--}60\text{ min}$, e.g. Night Hours):** Dispatch **`DEEP_SLEEP`**. Isolates the power modules to capture maximum savings ($80\text{--}86\%$) and eliminates off-peak reactive power surcharges ($Q_c$).

---

## 5. Phased Implementation Plan

### Phase 1: Dual-Mode OCPP Firmware Mapping (Weeks 1–2)
* **Objective:** Map vendor registers on Alpitronic, StarCharge, and Enel X units for both `STANDBY` and `DEEP_SLEEP` without dropping standard OCPP `Available` status.
* **Actions:**
  * **Standby Mode Command:**
    * `DisplayBacklightBrightness` $\rightarrow 15\%$ (Dimmed)
    * `HolsterLEDStripPWM` $\rightarrow 30\%$
    * `PowerModuleACContactor` $\rightarrow \text{CLOSED}$ (Keep hot)
    * `RFIDPollingInterval` $\rightarrow 2000\text{ ms}$ ($200\text{ ms}$ active duty cycle)
  * **Deep Sleep Mode Command:**
    * `DisplayBacklightBrightness` $\rightarrow 0\%$ (Screen OFF / RAM Suspend)
    * `HolsterLEDStripPWM` $\rightarrow 12\%$ (Low Beacon)
    * `PowerModuleACContactor` $\rightarrow \text{OPEN}$ (Physical isolation)
    * `RFIDPollingInterval` $\rightarrow \text{OFF}$ (Passive wake / holster switch only)
    * `LogoLEDOutput` $\rightarrow \text{LOCKED\_ON\_100\%}$ (Always active)

### Phase 2: Telemetry-Triggered Multi-Stage Wake Integration (Weeks 3–4)
* **Objective:** Use approach telemetry to escalate chargers from `DEEP_SLEEP` $\rightarrow$ `STANDBY` $\rightarrow$ `IDLE` before the vehicle arrives.
* **Actions:** Connect geofence triggers ($300\text{--}500\text{ m}$ radius or $<60\text{ s}$ ETA) to the CSMS to initiate pre-charging while the driver is still navigating the parking lot.

### Phase 3: Thermal & Local Walk-Up Validation (Weeks 5–6)
* **Actions:** Ensure local cabinet PLCs override both low-power modes if internal humidity reaches $\ge 85\%\text{ RH}$, and verify that tapping the screen or lifting the charging holster wakes the unit locally in $<500\text{ ms}$.

---

## 6. Wake Cost & Inrush Current Analysis: `STANDBY` vs. `DEEP_SLEEP`

Source: §2.1, §2.2 #2, §3.1 above. Scope: electrical **energy per wake** in Wh and RON across the two transitions for DC chargers ($50\text{--}300\text{ kW}$).

### 6.1 Transition 1: `STANDBY -> IDLE` (Zero Inrush)
* **Electromechanical State:** The main AC contactor remains closed throughout `STANDBY`. The DC-link capacitors ($2.0\text{--}12.0\text{ mF}$) remain fully energized at the $800\text{ V}$ bus potential.
* **Capacitor Inrush Energy ($E_{\text{cap}}$):** **$0.0\text{ Wh}$** (No pre-charging required).
* **Wake Window Energy ($E_{\text{win}}$):**
  $$E_{\text{win}} = (P_{\text{idle}} - P_{\text{standby}}) \times t_{\text{wake}}$$
  With $t_{\text{wake}} \approx 1.5\text{ s}$ ($0.000417\text{ h}$) on hardware, $E_{\text{win}} \approx 0.02\text{--}0.06\text{ Wh}$ ($< 0.0001\text{ RON}$).
* **Engineering Verdict:** **`STANDBY -> IDLE` transitions are electrically negligible in cost and cause zero contactor or capacitor wear.**

### 6.2 Transition 2: `DEEP_SLEEP -> IDLE` (DC-Link Pre-Charge Inrush)

> [!WARNING]
> Sections 1–5 give the **3–8 s** wake window and the idle/sleep draws. Inputs marked **(est.)** below are engineering estimates for 1000V-class DC-link capacitor banks.

When the AC contactor re-closes from `DEEP_SLEEP`, the grid supplies:
1. **DC-link charging ($E_{\text{cap}}$):** The pre-charge resistor charges capacitors to the rectified peak ($V_r = 565\text{ V}$ from $400\text{ V}$ 3-phase AC), then the PFC boosts to bus voltage ($V_b = 800\text{ V}$):
   $$E_{\text{cap}} = C \cdot V_r^2 + \frac{1}{2} C (V_b^2 - V_r^2)$$
2. **Wake window power draw ($E_{\text{win}}$):** Drawing intermediate power over the $3\text{--}8\text{ s}$ ($t_{\text{typ}} = 5.5\text{ s}$) wake window:
   $$E_{\text{win}} = (P_{\text{idle}} - P_{\text{sleep}}) \times t_{\text{wake}}$$

#### Results Per `DEEP_SLEEP -> IDLE` Wake Event:

| Capacity | C (mF) typ | E_cap Wh (low / typ / high) | E_win Wh (typ) | **Total E_wake Wh (low / typ / high)** | RON net (typ) | RON gross (typ) |
|---|---|---|---|---|---|---|
| 50 kW | 2.0 | 0.13 / 0.27 / 0.47 | 0.12 | **0.20 / 0.38 / 0.64** | 0.00044 | 0.00053 |
| 60 kW | 2.4 | 0.16 / 0.32 / 0.56 | 0.13 | **0.23 / 0.45 / 0.75** | 0.00052 | 0.00063 |
| 75 kW | 3.0 | 0.20 / 0.40 / 0.70 | 0.14 | **0.28 / 0.54 / 0.91** | 0.00062 | 0.00075 |
| 85 kW | 3.4 | 0.23 / 0.45 / 0.79 | 0.15 | **0.31 / 0.60 / 1.01** | 0.00069 | 0.00084 |
| 100 kW | 4.0 | 0.27 / 0.53 / 0.93 | 0.16 | **0.36 / 0.70 / 1.17** | 0.00080 | 0.00097 |
| 150 kW | 6.0 | 0.40 / 0.80 / 1.40 | 0.19 | **0.51 / 0.99 / 1.68** | 0.00114 | 0.00138 |
| 160 kW | 6.4 | 0.43 / 0.85 / 1.49 | 0.21 | **0.54 / 1.06 / 1.79** | 0.00122 | 0.00147 |
| 200 kW | 8.0 | 0.53 / 1.07 / 1.87 | 0.23 | **0.66 / 1.29 / 2.20** | 0.00149 | 0.00180 |
| 300 kW | 12.0 | 0.80 / 1.60 / 2.80 | 0.30 | **0.97 / 1.90 / 3.24** | 0.00219 | 0.00264 |

### 6.3 Strategic Summary
* **Energy Cost per Deep Sleep Wake:** Costs between **$0.2\text{ and }3.2\text{ Wh}$** ($0.00044\text{ to }0.00264\text{ RON}$ gross). Even $50$ wakes per day add $< 0.15\text{ RON/day}$ on a $300\text{ kW}$ HPC unit.
* **Operational Recommendation:** Use `STANDBY` during daytime operating hours to prevent contactor cycling and avoid pre-charge latency. Transition into `DEEP_SLEEP` during quiet hours (overnight periods with $>30\text{--}60\text{ min}$ of predicted zero demand) to harvest maximum active energy savings and avoid off-peak capacitive reactive power billing.
