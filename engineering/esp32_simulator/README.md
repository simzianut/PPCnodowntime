# ESP32 Physical EV Charger Simulator (`DC_50` / `DC_HPC`)

Physical hardware simulation of a PPC blue DC Fast / HPC charging station using an **ESP32 microcontroller board** (ESP32-WROOM-32 / NodeMCU-32S / DevKit V1) powered via standard $5.0\text{ V}$ USB.

The simulator hosts its own Wi-Fi Access Point (`PPC-Blue-Charger-Sim`) and serves an embedded single-page web application (`http://192.168.4.1`) allowing operators to toggle between the 6 charging station states implemented in [`stations/state.py`](file:///E:/Side%20Projects/PPCnodowntime/stations/state.py).

---

## 1. Subsystem Architecture & LED Representation

The hardware setup models the three operational component categories defined in [`engineering/engineering_plan.md`](file:///E:/Side%20Projects/PPCnodowntime/engineering/engineering_plan.md):

1. **Crucial Baseline (Always ON across all states):**
   * **Blue LED (`GPIO 23`):** Represents the **Main PPC blue Logo Backlight** ($24\text{ V DC}$ fixture). Driven **$100\%$ ON unconditionally** across `IDLE`, `STANDBY`, `DEEP_SLEEP`, `WAKING`, `CHARGING`, and `OFFLINE` so motorists can locate the station at night even during faults.
   * **SBC Controller (ESP32):** Represents the central Linux SBC running the state machine, wake timers, and local webapp.
   * **Safety Watchdog (USB LDO Regulator):** Represents the continuous power-good / safety monitoring loop, converting incoming $5.0\text{ V}$ USB down to $3.3\text{ V}$ with hardware Brownout Detection (BOD).
2. **Important but Not Crucial Components (Red LED 1 on `GPIO 18`):**
   * Represents the outdoor high-nit HMI touch display, holster status RGB lights, and RFID/POS terminal.
   * Driven via hardware PWM at $1\text{ kHz}$.
3. **Rest of the Components (Red LED 2 on `GPIO 19`):**
   * Represents the AC/DC SiC power stacks, LCL input filters, liquid cooling pump, and cabinet heaters.
   * Driven via hardware PWM at $1\text{ kHz}$.

---

## 2. State-to-PWM Truth Table

| Charger State ([`stations/state.py`](file:///E:/Side%20Projects/PPCnodowntime/stations/state.py)) | Blue LED (PPC Logo, `GPIO 23`) | Red LED 1 (Important, `GPIO 18`) | Red LED 2 (Rest, `GPIO 19`) | Physical System Condition |
| :--- | :---: | :---: | :---: | :--- |
| **`CHARGING`** | **100% ON** | **BRIGHT ($100\%$ PWM)** | **BRIGHT ($100\%$ PWM)** | Active EV session; full screen brightness, cooling pump active, SiC power conversion active. |
| **`IDLE`** | **100% ON** | **DIMMED ($65\%$ PWM)** | **DIMMED ($65\%$ PWM)** | Hot-standby; screen ready, contactors closed, DC bus energized ($800\text{ V}$). Immediate plug-in ($0\text{ s}$). |
| **`STANDBY`** | **100% ON** | **DIMMED MORE ($25\%$ PWM)** | **OFF ($0\%$ PWM)** | Fast-wake low power; screen/holster dimmed to night levels, power stack auxiliary rails cut. |
| **`DEEP_SLEEP`** | **100% ON** | **OFF ($0\%$ PWM)** | **OFF ($0\%$ PWM)** | Maximum cost-saving mode; contactors OPEN, screen OFF, zero reactive power ($Q_c$) injection. |
| **`WAKING`** *(Transient)* | **100% ON** | **PULSING ($10\%\leftrightarrow 80\%$)** | **$0\%$ for $4.0\text{ s} \rightarrow 65\%$** | Soft-start pre-charge sequence ($5.5\text{ s}$ from Deep Sleep, $1.5\text{ s}$ from Standby). |
| **`OFFLINE`** *(Fault)* | **100% ON** | **RAPID BLINK ($10\text{ Hz}$)** | **OFF ($0\%$ PWM)** | Safety RCD/E-Stop trip; power isolated, logo stays ON for site safety. |

---

## 3. Bill of Materials (BOM) & Wiring

### Components List
* **1× ESP32 Development Board** (ESP32-WROOM-32 / NodeMCU-32S / DevKit V1)
* **1× 5 mm Blue LED** (PPC Logo Backlight)
* **2× 5 mm Red LEDs** (Red 1: Important Components; Red 2: Rest of Components)
* **3× $330\text{ }\Omega$ Resistors** (or $470\text{ }\Omega$, $0.25\text{ W}$)
* **1× Breadboard + male-to-male DuPont jumper wires**
* **1× USB Cable** (for $5\text{ V}$ power from laptop or USB adapter)

### Wiring Connections
* **ESP32 `GND`** $\rightarrow$ Breadboard Negative rail (Common Ground).
* **ESP32 `GPIO 23`** $\rightarrow 330\text{ }\Omega$ Resistor $\rightarrow$ **Blue LED Anode (+)** $\rightarrow$ Cathode (-) to `GND`.
* **ESP32 `GPIO 18`** $\rightarrow 330\text{ }\Omega$ Resistor $\rightarrow$ **Red LED 1 Anode (+)** $\rightarrow$ Cathode (-) to `GND`.
* **ESP32 `GPIO 19`** $\rightarrow 330\text{ }\Omega$ Resistor $\rightarrow$ **Red LED 2 Anode (+)** $\rightarrow$ Cathode (-) to `GND`.

---

## 4. How to Flash & Run

1. **Install tools:**
   ```bash
   pip install esptool mpremote
   ```
2. **Flash MicroPython:**
   Download the standard ESP32 generic firmware from [micropython.org](https://micropython.org/download/ESP32_GENERIC/):
   ```bash
   esptool.py --chip esp32 erase_flash
   esptool.py --chip esp32 write_flash -z 0x1000 ESP32_GENERIC-*.bin
   ```
3. **Upload the firmware:**
   ```bash
   mpremote cp engineering/esp32_simulator/main.py :main.py
   mpremote reset
   ```
4. **Operate:**
   * On your smartphone or laptop, connect to the Wi-Fi network: **`PPC-Blue-Charger-Sim`**.
   * Open your browser and go to:
     ```
     http://192.168.4.1
     ```
   * Click **IDLE**, **STANDBY**, **DEEP SLEEP**, **CHARGING**, or **OFFLINE** to toggle states and observe the hardware LEDs.
