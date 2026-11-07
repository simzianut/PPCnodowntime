# Engineering Project Plan: Cost-Effective Smart Deep Sleep Mode for EV Charging Infrastructure

**Author Role:** Senior Electrical Systems Engineer  
**Target Infrastructure:** PPC blue Romania Charging Network (Single-Station Unit Economics Across 13 Observed Capacities)  
**Operational Duty Cycle:** $4\text{ hours/day}$ Active Charging ($120\text{ h/month}$) $\mid$ $20\text{ hours/day}$ Unoccupied ($600\text{ h/month}$)  
**Energy Price Benchmark (Romania, November 2026):**  
* **Net B2B Commercial Cost (Excl. 21% VAT):** $1.15\text{ RON/kWh}$ ($0.685\text{ RON/kWh}$ PPC active energy + $0.465\text{ RON/kWh}$ ANRE regulated distribution, transport, system services, green certificates, and CfD/excise taxes in Rețele Electrice zones).  
* **Gross Retail Benchmark (Incl. 21% VAT):** $1.39\text{ RON/kWh}$ (PPC Fix Online standard tariff).

---

## 1. Executive Engineering Summary & Cost-Effectiveness Thesis

From an electrical systems engineering standpoint, operating an EV charging station during its $20\text{ unoccupied hours/day}$ in **Standard Idle Mode** creates three compounding inefficiencies:
1. **Parasitic Active Power Loss ($P_{\text{idle}}$):** Continuous energization of outdoor high-nit HMI displays ($15\text{--}35\text{ W}$), POS/RFID RF polling fields ($8\text{--}15\text{ W}$), full-brightness RGB status strips ($12\text{--}20\text{ W}$), narrow-band anti-condensation heaters ($25\text{--}80\text{ W}$), and quiescent AC/DC power module control buses.
2. **Capacitive Reactive Power Penalties ($Q_c$ & Low $\cos \varphi$):** When high-power DC chargers ($50\text{--}300\text{ kW}$) sit in standard idle with their AC input EMI/LCL filters connected to the $400\text{ V}$ three-phase grid, they exhibit a highly leading (capacitive) power factor ($\cos \varphi \approx 0.15\text{--}0.35$). Under ANRE billing regulations in Romania, a monthly power factor below $\cos \varphi = 0.90$ incurs reactive energy penalties on the commercial smart meter.
3. **Accelerated Component Thermal & Optical Degradation:** Running $1000+\text{ nit}$ LCD backlights and auxiliary cooling fans for $8,760\text{ hours/year}$ instead of the $1,460\text{ hours/year}$ of actual vehicle charging reduces Mean Time Between Failures (MTBF) by up to $75\%$.

**The Engineering Solution (Smart Deep Sleep Mode):**  
By leveraging external EV approach telemetry (Waze, Google Maps, PPC blue app, and online charger maps) to trigger wake-up remotely over OCPP, the station can physically isolate its power stages and shut down non-essential human-machine interfaces during the $20\text{ hours/day}$ it is unoccupied—while keeping the **4G LTE / OCPP controller** and the **main PPC blue branding logo LEDs** at $100\%$ operation.

---

## 2. Subsystem State & Power Architecture (Idle vs. Smart Deep Sleep)

### 2.1 Component-Level Power Allocation Matrix (Reference DC Fast Charger)

| Subsystem / Component | Standard Idle Mode | Smart Deep Sleep Mode | Idle Draw ($P_{\text{idle}}$) | Deep Sleep Draw ($P_{\text{sleep}}$) | Electrical Engineering Justification |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **4G LTE Router & Linux SBC (OCPP Controller)** | **100% Operational** | **100% Operational** | $8.0\text{--}12.0\text{ W}$ | $6.0\text{--}8.0\text{ W}$ | Mandatory continuous TLS WebSocket (`wss://`) connection to CSMS for instant remote wake-up interrupts. |
| **Main PPC blue Logo Backlight (24V DC LED)** | **100% Operational** | **100% Operational** | $6.0\text{--}12.0\text{ W}$ | $6.0\text{--}12.0\text{ W}$ | Mandatory constant current feed so drivers visually locate the station at night; scales with cabinet size. |
| **Safety Watchdog (RCD, Surge, Door/Tilt, E-Stop)** | **100% Operational** | **100% Operational** | $1.5\text{--}2.5\text{ W}$ | $1.5\text{--}2.5\text{ W}$ | IEC 61851-1 / IEC 61851-23 mandatory safety monitoring cannot be bypassed. |
| **Connector Holster RGB LEDs & Status Strips** | **100% Brightness** | **Dimmed (10–15% PWM)** | $12.0\text{--}25.0\text{ W}$ | $1.5\text{--}3.5\text{ W}$ | PWM duty cycle reduced to a low beacon state; cuts 24V auxiliary PSU load by $>85\%$. |
| **Cabinet Anti-Condensation Heaters & Thermals** | **Narrow Hysteresis** | **Dimmed / Dew-Point Guard** | $20.0\text{--}65.0\text{ W}$ | $0.0\text{--}5.0\text{ W}$ | Thermostat deadband widened; heater only activates if internal relative humidity approaches $85\%$ condensation threshold. |
| **10.1" / 15.6" Outdoor HMI Touch Display** | **On (50–100% Nit)** | **Turned Off (RAM Suspend)** | $15.0\text{--}35.0\text{ W}$ | $0.5\text{ W}$ | LED backlight rail cut via MOSFET switch; frame buffer retained in RAM for $<500\text{ ms}$ wake-up. |
| **AC/DC SiC Power Stacks & LCL Input Filters** | **Hot-Standby / Pre-Charged** | **Turned Off (AC Contactor Open)** | $15.0\text{--}60.0\text{ W}$ | $0.0\text{ W}$ | Opening the main AC input contactor eliminates both active standby loss and capacitive reactive power ($Q_c$). |
| **RFID (ISO 14443) & Contactless POS Terminal** | **Continuous RF Polling** | **Turned Off (Passive Wake)** | $8.0\text{--}15.0\text{ W}$ | $0.5\text{--}1.0\text{ W}$ | RF carrier wave disabled until remote wake signal or physical faceplate tap interrupt occurs. |
| **Liquid-Cooling Pump (300 kW HPC Units Only)** | **Periodic Circulation** | **Turned Off** | $25.0\text{--}40.0\text{ W}$ | $0.0\text{ W}$ | Coolant circulation unnecessary when $I_{\text{DC}} = 0\text{ A}$ and ambient temperature is within safe glycol limits. |

### 2.2 Engineering Limitations & Design Constraints
1. **Irreducible Auxiliary Power Floor ($5.5\text{ W}$ on AC to $32.0\text{ W}$ on $300\text{ kW}$ DC):** Because the 4G modem, OCPP SBC, safety loop, and main PPC blue logo illumination remain continuously energized, plus the quiescent inefficiency of the $400\text{ V AC} \rightarrow 24\text{ V DC}$ auxiliary switched-mode power supply ($\eta \approx 82\%$ at light load), power draw cannot drop to $0\text{ W}$.
2. **Pre-Charge & Contactor Inrush Delay ($3\text{--}8\text{ s}$ on DC Chargers):** Re-energizing de-energized SiC power stacks requires closing the soft-start pre-charge relay to charge the DC-link capacitors before closing the main AC contactor, plus booting the ISO 15118 PLC modem. Because wake-up is triggered remotely while the EV is still approaching (via Waze/Maps/App), this $3\text{--}8\text{ s}$ delay occurs before the driver parks and plugs in.
3. **Ad-Hoc Walk-Up Edge Case:** If a driver arrives without using any navigation or EV app, the HMI screen is off. A low-cost capacitive touch wake interrupt on the screen or a holster microswitch must immediately trigger local wake-up.
4. **Humidity Override Interlock:** In sub-zero or high-humidity Romanian weather, the local thermal PLC must override Smart Deep Sleep if internal cabinet humidity exceeds $85\%\text{ RH}$ to prevent high-voltage creepage or dielectric breakdown.

---

## 3. Reanalysis Across All 13 Observed Charging Capacities in Romania

In Romania's public charging network, the 13 observed capacities ($11, 17, 22, 43, 50, 60, 75, 85, 100, 150, 160, 200, 300\text{ kW}$) span three distinct electrical hardware topologies:
* **Tier 1 — AC Three-Phase EVSE ($11\text{ kW}, 17\text{ kW}, 22\text{ kW}$):** No internal AC/DC power conversion (relies on the EV's onboard charger).Note that $17\text{ kW}$ represents either a $22\text{ kW}$ AC post grid-limited by a $3 \times 25\text{ A}$ ($17.3\text{ kW}$) ATR breaker or a compact wall-mounted mini-DC unit.
* **Tier 2 — High-Power AC & Entry/Mid-Range Modular DC ($43\text{ kW}, 50\text{ kW}, 60\text{ kW}, 75\text{ kW}, 85\text{ kW}, 100\text{ kW}$):** Floor-standing enclosures with active thermal management, $10.1\text{--}15.6\text{ inch}$ displays, POS/RFID, and $1\text{ to }3$ SiC rectifier stacks (note that $43\text{ kW}$ is high-current $3 \times 63\text{ A}$ AC integrated into multi-standard cabinets, and $85\text{ kW}$ is a $90\text{--}120\text{ kW}$ modular DC charger derated to match a $100\text{ kVA}$ site transformer limit).
* **Tier 3 — Ultra-Fast High-Power DC / HPC ($150\text{ kW}, 160\text{ kW}, 200\text{ kW}, 300\text{ kW}$):** Multi-stack SiC architectures ($2\text{ to }4\text{ stacks of }75\text{--}100\text{ kW}$ or $4\text{--}5\text{ modules of }30\text{--}40\text{ kW}$), larger cabinets, dual/triple high-current holsters, and—for $300\text{ kW}$—active liquid-cooled cable loops.

### 3.1 Single-Station Power & Monthly Cost Table ($600\text{ Unoccupied Hours/Month}$)

Formulas applied per single station over a $30\text{-day}$ month ($4\text{ h/day}$ active charging = $120\text{ h}$; $20\text{ h/day}$ unoccupied = $600\text{ h}$):
$$E_{\text{idle}} = \frac{P_{\text{idle}} \times 600\text{ h}}{1000} \quad (\text{kWh/month}) \qquad \text{and} \qquad E_{\text{sleep}} = \frac{P_{\text{sleep}} \times 600\text{ h}}{1000} \quad (\text{kWh/month})$$

| Observed Capacity | Hardware Topology & Typical Romanian Deployment | Idle Power ($P_{\text{idle}}$) | Deep Sleep ($P_{\text{sleep}}$) | Idle Energy ($600\text{ h}$) | Sleep Energy ($600\text{ h}$) | Monthly Cost Idle (Net / Gross)* | Monthly Cost Sleep (Net / Gross)* | Monthly Savings per 1 Station (Net / Gross)* | Power Reduction ($\%$) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **$11\text{ kW}$** | AC $3\phi$ ($3\times 16\text{ A}$) Wallbox / Compact Post | $12.0\text{ W}$ | $5.5\text{ W}$ | $7.20\text{ kWh}$ | $3.30\text{ kWh}$ | $8.28$ / $10.01\text{ RON}$ | $3.80$ / $4.59\text{ RON}$ | **$4.49$ / $5.42\text{ RON}$** | $-54.2\%$ |
| **$17\text{ kW}$** | AC $3\phi$ Grid-Limited ($3\times 25\text{ A}$) / Mini-Post | $18.0\text{ W}$ | $7.5\text{ W}$ | $10.80\text{ kWh}$ | $4.50\text{ kWh}$ | $12.42$ / $15.01\text{ RON}$ | $5.18$ / $6.26\text{ RON}$ | **$7.25$ / $8.76\text{ RON}$** | $-58.3\%$ |
| **$22\text{ kW}$** | AC $3\phi$ ($3\times 32\text{ A}$) Dual-Socket Street Pedestal | $25.0\text{ W}$ | $9.0\text{ W}$ | $15.00\text{ kWh}$ | $5.40\text{ kWh}$ | $17.25$ / $20.85\text{ RON}$ | $6.21$ / $7.51\text{ RON}$ | **$11.04$ / $13.34\text{ RON}$** | $-64.0\%$ |
| **$43\text{ kW}$** | AC $3\phi$ ($3\times 63\text{ A}$) Multi-Standard Cabinet | $65.0\text{ W}$ | $16.0\text{ W}$ | $39.00\text{ kWh}$ | $9.60\text{ kWh}$ | $44.85$ / $54.21\text{ RON}$ | $11.04$ / $13.34\text{ RON}$ | **$33.81$ / $40.87\text{ RON}$** | $-75.4\%$ |
| **$50\text{ kW}$** | DC Fast ($1\times 50\text{ kW}$ SiC Stack, e.g., HYC50) | $95.0\text{ W}$ | $19.0\text{ W}$ | $57.00\text{ kWh}$ | $11.40\text{ kWh}$ | $65.55$ / $79.23\text{ RON}$ | $13.11$ / $15.85\text{ RON}$ | **$52.44$ / $63.38\text{ RON}$** | $-80.0\%$ |
| **$60\text{ kW}$** | DC Fast ($2\times 30\text{ kW}$ Modules, e.g., Jupiter 60) | $105.0\text{ W}$ | $20.0\text{ W}$ | $63.00\text{ kWh}$ | $12.00\text{ kWh}$ | $72.45$ / $87.57\text{ RON}$ | $13.80$ / $16.68\text{ RON}$ | **$58.65$ / $70.89\text{ RON}$** | $-81.0\%$ |
| **$75\text{ kW}$** | DC Fast ($1\times 75\text{ kW}$ SiC Stack, Hypercharger) | $115.0\text{ W}$ | $21.5\text{ W}$ | $69.00\text{ kWh}$ | $12.90\text{ kWh}$ | $79.35$ / $95.91\text{ RON}$ | $14.84$ / $17.93\text{ RON}$ | **$64.52$ / $77.98\text{ RON}$** | $-81.3\%$ |
| **$85\text{ kW}$** | DC Fast ($3\times 30\text{ kW}$ ATR-Derated to $85\text{ kW}$) | $120.0\text{ W}$ | $22.0\text{ W}$ | $72.00\text{ kWh}$ | $13.20\text{ kWh}$ | $82.80$ / $100.08\text{ RON}$ | $15.18$ / $18.35\text{ RON}$ | **$67.62$ / $81.73\text{ RON}$** | $-81.7\%$ |
| **$100\text{ kW}$** | DC Fast ($2\times 50\text{ kW}$ or $1\times 100\text{ kW}$ SiC Stack) | $130.0\text{ W}$ | $23.0\text{ W}$ | $78.00\text{ kWh}$ | $13.80\text{ kWh}$ | $89.70$ / $108.42\text{ RON}$ | $15.87$ / $19.18\text{ RON}$ | **$73.83$ / $89.24\text{ RON}$** | $-82.3\%$ |
| **$150\text{ kW}$** | DC Ultra-Fast ($2\times 75\text{ kW}$ SiC Stacks, HYC150) | $150.0\text{ W}$ | $24.5\text{ W}$ | $90.00\text{ kWh}$ | $14.70\text{ kWh}$ | $103.50$ / $125.10\text{ RON}$ | $16.91$ / $20.43\text{ RON}$ | **$86.60$ / $104.67\text{ RON}$** | $-83.7\%$ |
| **$160\text{ kW}$** | DC Ultra-Fast ($4\times 40\text{ kW}$ Modular Cabinet) | $160.0\text{ W}$ | $25.5\text{ W}$ | $96.00\text{ kWh}$ | $15.30\text{ kWh}$ | $110.40$ / $133.44\text{ RON}$ | $17.60$ / $21.27\text{ RON}$ | **$92.81$ / $112.17\text{ RON}$** | $-84.1\%$ |
| **$200\text{ kW}$** | DC Ultra-Fast ($2\times 100\text{ kW}$ SiC Stacks, HYC200) | $175.0\text{ W}$ | $27.0\text{ W}$ | $105.00\text{ kWh}$ | $16.20\text{ kWh}$ | $120.75$ / $145.95\text{ RON}$ | $18.63$ / $22.52\text{ RON}$ | **$102.12$ / $123.43\text{ RON}$** | $-84.6\%$ |
| **$300\text{ kW}$** | DC HPC ($3\times 100\text{ kW}$ or $4\times 75\text{ kW}$ + Liquid Cool) | $230.0\text{ W}$ | $32.0\text{ W}$ | $138.00\text{ kWh}$ | $19.20\text{ kWh}$ | $158.70$ / $191.82\text{ RON}$ | $22.08$ / $26.69\text{ RON}$ | **$136.62$ / $165.13\text{ RON}$** | $-86.1\%$ |

*\*Note: **Net** = $1.15\text{ RON/kWh}$ (B2B cost excluding $21\%$ deductible VAT); **Gross** = $1.39\text{ RON/kWh}$ (including $21\%$ VAT).*

---

## 4. Electrical Systems Engineer's Cost-Effectiveness Decision Matrix

An engineer must never deploy hardware or firmware blindly across heterogeneous assets without evaluating **Capital Expenditure (CAPEX) vs. Operational Expenditure (OPEX) Payback**:

### Decision Rule 1: Zero-CAPEX Software Rollout Only for $11\text{ kW} \text{--} 22\text{ kW}$ AC Stations
* **Financial Reality:** A single $11\text{ kW}$ to $22\text{ kW}$ AC charger saves between **$4.49\text{ RON}$ and $11.04\text{ RON/month}$ (Net)** ($53.88\text{ to }132.48\text{ RON/year}$).
* **Engineering Verdict:** **Do not dispatch field technicians or install physical proximity sensors on $11\text{--}22\text{ kW}$ stations.** A single technician truck roll + hardware sensor ($>750\text{ RON}$) would require $5.6\text{ to }14\text{ years}$ to break even. For AC chargers, Smart Deep Sleep must be implemented strictly as a **$0\text{ RON}$ CAPEX Over-The-Air (OTA) OCPP configuration script** that dims the LED ring and disables RFID polling remotely.

### Decision Rule 2: High-Priority Rollout on $50\text{ kW} \text{--} 300\text{ kW}$ DC Fast & HPC Stations
* **Financial Reality:** A single $50\text{ kW}$ to $300\text{ kW}$ DC station saves **$52.44\text{ RON}$ to $136.62\text{ RON/month}$ (Net)** ($629\text{ to }1,639\text{ RON/year}$ in direct active electricity alone, or $760\text{ to }1,981\text{ RON/year}$ Gross).
* **Hidden Engineering Savings (Reactive Power & Component MTBF):**
  1. **Elimination of Capacitive Reactive Power ($Q_c$):** Opening the AC input contactors on $50\text{--}300\text{ kW}$ cabinets prevents the LCL/EMI filters from injecting reactive vars into the grid for $600\text{ h/month}$, eliminating ANRE reactive power tariff surcharges during off-peak hours.
  2. **HMI Display & Fan Lifespan Extension:** Turning off the $15.6\text{-inch}$ high-brightness outdoor display and auxiliary fans for $600\text{ h/month}$ ($7,200\text{ h/year}$) defers display replacement costs ($\sim 3,500\text{--}6,000\text{ RON}$ per outdoor HMI assembly) by $3\times\text{ to }4\times$.

---

## 5. Phased Implementation Plan

### Phase 1: OCPP Protocol & Firmware Mapping (Weeks 1–2)
* **Objective:** Map internal vendor register addresses on Alpitronic (HYC50/150/200/300), StarCharge (Jupiter/Titan), and Enel X units without altering standard OCPP `Available` status.
* **Engineering Action:** Use OCPP 1.6J `DataTransfer` / OCPP 2.0.1 `SetVariables` commands to control:
  * `DisplayBacklightBrightness` $\rightarrow 0\%$ (Sleep) / $80\%$ (Wake)
  * `HolsterLEDStripPWM` $\rightarrow 15\%$ (Sleep) / $100\%$ (Wake)
  * `PowerModuleACContactor` $\rightarrow \text{OPEN}$ (Sleep) / $\text{PRECHARGE\_READY}$ (Wake)
  * `LogoLEDOutput` $\rightarrow \text{LOCKED\_ON\_100\%}$ (Always active)

### Phase 2: Telemetry-Triggered Pre-Wake Integration (Weeks 3–4)
* **Objective:** Mask the $3\text{--}8\text{ s}$ DC contactor pre-charge latency using external EV approach signals.
* **Engineering Action:** Connect the geospatial geofencing webhook (triggered when an EV routes to or approaches a PPC blue station on Waze, Google Maps, or the PPC blue web/mobile app within a $300\text{--}500\text{ m}$ radius or $<60\text{ s}$ ETA) to the CSMS to dispatch the OCPP wake packet before physical arrival.

### Phase 3: Thermal & Walk-Up Fail-Safe Validation (Weeks 5–6)
* **Objective:** Ensure zero condensation damage in winter and zero walk-up customer friction.
* **Engineering Action:**
  * Program local cabinet PLC override: if internal humidity sensor reads $\ge 85\%\text{ RH}$ or $T_{\text{cabinet}} \le T_{\text{dew}} + 3^\circ\text{C}$, force anti-condensation heater ON regardless of sleep state.
  * Enable screen-touch interrupt and CCS2 holster reed-switch interrupt so any unannounced walk-up driver wakes the station locally within $<500\text{ ms}$ by lifting the nozzle or tapping the screen.

---

## 6. Wake Cost Estimate: `DEEP_SLEEP -> IDLE` (DC $50\text{--}300\text{ kW}$)

Source: §2.1, §2.2 #2, §3.1 above. Scope: electrical **energy per wake** in Wh and RON, DC only, typical published ranges flagged as unverified.

> [!WARNING]
> Sections 1–5 give only the **3–8 s** wake window and the idle/sleep draws. Every input marked **(est.)** below is an engineering estimate, not a datasheet or plan value. Replace them with Alpitronic/StarCharge numbers before using the result in `wake_extra_wh`.

### 6.1 Model

When the AC contactor re-closes, the grid supplies two things:

1. **DC-link charging.** The pre-charge resistor charges the capacitors to the rectified peak, then the PFC boosts to the bus voltage.
   - Pre-charge through a resistor draws `C·V_r²` from the grid (half stored, half lost in the resistor).
   - Boost to the bus voltage adds `½·C·(V_b² − V_r²)`.
   - `E_cap = C·V_r² + ½·C·(V_b² − V_r²)`
2. **Wake window.** During the 3–8 s, the station draws more than its sleep floor.
   - `E_win = (P_idle − P_sleep) · t_wake`, using the §3.1 table.

| Input | Low | Typical | High | Basis |
|---|---|---|---|---|
| DC-link capacitance, µF per kW | 20 | 40 | 70 | **(est.)** 10 ms PFC hold-up sizing gives about 70 µF/kW |
| Rectified peak `V_r` | 565 V | 565 V | 565 V | 400 V three-phase × √2 (the plan assumes 400 V) |
| Bus voltage `V_b` | 800 V | 800 V | 800 V | **(est.)** 1000 V-class module |
| `t_wake` | 3 s | 5.5 s | 8 s | plan range; the midpoint is the typical choice |

### 6.2 Results Per Wake Event

| Capacity | C (mF) typ | E_cap Wh (low / typ / high) | E_win Wh (typ) | **E_wake Wh (low / typ / high)** | RON net (typ) | RON gross (typ) |
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

### 6.3 Reading the Numbers

- **One wake costs about 0.2–3.2 Wh, i.e. under 0.005 RON.** At 1.15 RON/kWh, even the 300 kW high case is 0.0037 RON.
- **Even 100 wakes per day add under 0.4 RON/day on the 300 kW unit** (typical case: 0.22 RON/day).
- **Scaling.** `E_cap` grows linearly with capacity because the DC-link capacitance scales with kW. The largest uncertainty is the capacitance per kW, which moves the result by about 3.5× between low and high.
- **Peak current is a separate question.** The pre-charge resistor is there to limit the inrush. Its peak current (`V_r / R`) depends on a resistor value the plan does not give. It affects demand/peak billing, which is not modeled here.

### 6.4 Other Subsystems

Rough spike energies, all **(est.)**:

| Subsystem | Estimated spike energy per wake | Verdict |
|---|---|---|
| HMI backlight (35 W, about 0.5 s) | about 0.015 Wh | insignificant |
| Liquid-cooling pump (300 kW only, 6× inrush for 0.3 s) | about 0.02 Wh | insignificant |
| Cooling fans (4 × 30 W, 3× surge for 1 s) | about 0.1 Wh | **cannot decide:** the plan gives no fan power; the figure is a guess |

The fan guess is up to about 26% of the 50 kW typical wake energy. It is excluded from the table because it has no basis in the plan.

### 6.5 Caveats

- LCL/EMI filter capacitor inrush is only a few joules per wake (the plan gives no capacitor values) and is not included.
- Contactor and relay wear is out of scope.
- The model treats the wake as electrically independent of the sim's 180 s `deep_sleep_wake_s`. These are different quantities: this is energy per wake, that is driver wait.
