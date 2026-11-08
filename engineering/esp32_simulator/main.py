# ruff: noqa: E501
"""ESP32 Standalone Physical EV Charger Simulator.

Features:
1. Wi-Fi Access Point (SSID: 'PPC-Blue-Charger-Sim', IP: 192.168.4.1).
2. Embedded responsive webapp for live state cycling.
3. Blue LED (PPC Logo): Always 100% ON.
4. Red LED 1 (Important Components): State-dependent PWM (0% to 100%).
5. Red LED 2 (Rest of Components): State-dependent PWM (0% to 100%).
"""

import socket
import time

import network
import ujson
from machine import PWM, Pin

# --- Hardware Pin Assignments ---
PIN_BLUE_LOGO = 23  # Always 100% ON (PPC Blue Logo Backlight)
PIN_RED_IMPORTANT = 18  # Red LED 1: HMI Screen, RFID, Holster
PIN_RED_REST = 19  # Red LED 2: AC/DC Power Modules, Cooling

# --- Hardware PWM Setup (1 kHz frequency, 10-bit resolution: 0-1023) ---
blue_led = Pin(PIN_BLUE_LOGO, Pin.OUT)
blue_led.value(1)  # Locked 100% ON in all states

pwm_red1 = PWM(Pin(PIN_RED_IMPORTANT), freq=1000, duty=0)
pwm_red2 = PWM(Pin(PIN_RED_REST), freq=1000, duty=0)

# --- State-to-Duty Mapping (10-bit: 0 to 1023) ---
# CHARGING: Both Bright (100% = 1023)
# IDLE: Both Dimmed a little (~65% = 665)
# STANDBY: Red 1 Dimmed more (~25% = 256), Red 2 OFF (0% = 0)
# DEEP_SLEEP: Both OFF (0% = 0)
DUTY_MAP = {
    "CHARGING": (1023, 1023),
    "IDLE": (665, 665),
    "STANDBY": (256, 0),
    "DEEP_SLEEP": (0, 0),
    "OFFLINE": (0, 0),
}

# --- State Machine Globals ---
current_state = "IDLE"
wake_target = "IDLE"
wake_start_ms = 0
wake_duration_s = 0.0

STANDBY_WAKE_S = 1.5
DEEP_SLEEP_WAKE_S = 5.5


def set_led_pwm(duty1: int, duty2: int):
    """Updates hardware PWM duty cycles for both Red LEDs."""
    pwm_red1.duty(duty1)
    pwm_red2.duty(duty2)


def apply_state(new_state: str, cause: str = "webapp") -> dict:
    """Applies a state transition adhering to stations/state.py rules."""
    global current_state, wake_target, wake_start_ms, wake_duration_s
    new_state = new_state.upper()

    if new_state not in ("IDLE", "STANDBY", "DEEP_SLEEP", "CHARGING", "OFFLINE"):
        return {"ok": False, "reason": "invalid_state"}

    prev = current_state

    # Guard rules
    if prev == "CHARGING" and new_state in ("STANDBY", "DEEP_SLEEP"):
        return {"ok": False, "reason": "busy_charging", "state": prev}
    if prev == "WAKING" and new_state in ("STANDBY", "DEEP_SLEEP"):
        return {"ok": False, "reason": "currently_waking", "state": prev}
    if prev == new_state:
        return {"ok": False, "reason": "already_in_state", "state": prev}

    # Transitions to wake
    if prev in ("STANDBY", "DEEP_SLEEP") and new_state in ("IDLE", "CHARGING"):
        wake_duration_s = DEEP_SLEEP_WAKE_S if prev == "DEEP_SLEEP" else STANDBY_WAKE_S
        wake_target = new_state
        wake_start_ms = time.ticks_ms()
        current_state = "WAKING"
        print(f"[{cause.upper()}] {prev} -> WAKING (target={wake_target}, {wake_duration_s}s)")
        return {
            "ok": True,
            "state": "WAKING",
            "wake_target": wake_target,
            "duration_s": wake_duration_s,
        }

    current_state = new_state
    d1, d2 = DUTY_MAP.get(current_state, (0, 0))
    set_led_pwm(d1, d2)
    print(f"[{cause.upper()}] {prev} -> {current_state} | Red1: {d1}/1023, Red2: {d2}/1023")
    return {"ok": True, "state": current_state}


# Apply initial state (IDLE)
set_led_pwm(*DUTY_MAP["IDLE"])

# --- Embedded HTML5/CSS Web Application ---
HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>PPC blue Station Simulator</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: #0b132b; color: #fff; display: flex; flex-direction: column; align-items: center; padding: 20px; min-height: 100vh; }
        .card { background: #1c2541; width: 100%; max-width: 440px; border-radius: 16px; padding: 24px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
        .brand { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
        .brand h2 { color: #48cae4; font-size: 22px; font-weight: 700; }
        .badge { background: #0077b6; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: bold; }
        .led-panel { background: #0e1626; border-radius: 12px; padding: 16px; margin-bottom: 24px; border: 1px solid #283759; }
        .led-row { display: flex; align-items: center; justify-content: space-between; margin: 10px 0; font-size: 14px; }
        .indicator { width: 18px; height: 18px; border-radius: 50%; display: inline-block; box-shadow: 0 0 10px currentColor; }
        .blue-ind { color: #00b4d8; background: #00b4d8; }
        .red-ind { color: #ef233c; background: #ef233c; }
        .btn-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }
        button { border: none; padding: 14px 16px; border-radius: 10px; font-size: 14px; font-weight: 600; cursor: pointer; transition: 0.2s; color: #fff; }
        .btn-idle { background: #3a86ff; }
        .btn-standby { background: #ffb703; color: #000; }
        .btn-deep { background: #4a5568; }
        .btn-charge { background: #38b000; }
        .btn-fault { background: #d90429; grid-column: span 2; }
        button:active { transform: scale(0.97); opacity: 0.85; }
        .active-btn { outline: 3px solid #fff; box-shadow: 0 0 15px currentColor; }
        .status-box { text-align: center; margin-top: 10px; font-size: 13px; color: #8d99ae; }
    </style>
</head>
<body>
    <div class="card">
        <div class="brand">
            <h2>PPC blue Station</h2>
            <span class="badge" id="state-badge">IDLE</span>
        </div>
        
        <div class="led-panel">
            <div class="led-row">
                <span>PPC Logo (Always ON)</span>
                <span class="indicator blue-ind" style="opacity: 1.0;"></span>
            </div>
            <div class="led-row">
                <span>Red LED 1 (Important: HMI/RFID)</span>
                <span class="indicator red-ind" id="ind-red1" style="opacity: 0.65;"></span>
            </div>
            <div class="led-row">
                <span>Red LED 2 (Rest: Power Stacks)</span>
                <span class="indicator red-ind" id="ind-red2" style="opacity: 0.65;"></span>
            </div>
        </div>

        <div class="btn-grid">
            <button class="btn-idle" onclick="setState('IDLE')">IDLE</button>
            <button class="btn-standby" onclick="setState('STANDBY')">STANDBY</button>
            <button class="btn-deep" onclick="setState('DEEP_SLEEP')">DEEP SLEEP</button>
            <button class="btn-charge" onclick="setState('CHARGING')">CHARGING</button>
            <button class="btn-fault" onclick="setState('OFFLINE')">TRIP E-STOP (OFFLINE)</button>
        </div>

        <div class="status-box">
            Connected to ESP32 Station Simulator AP<br>
            State updates in real time on hardware LEDs
        </div>
    </div>

    <script>
        function setState(state) {
            fetch('/state?target=' + state)
                .then(r => r.json())
                .then(data => updateUI(data.state))
                .catch(e => console.error(e));
        }

        function updateUI(state) {
            document.getElementById('state-badge').innerText = state;
            let r1 = document.getElementById('ind-red1');
            let r2 = document.getElementById('ind-red2');
            
            if (state === 'CHARGING') { r1.style.opacity = '1.0'; r2.style.opacity = '1.0'; }
            else if (state === 'IDLE') { r1.style.opacity = '0.65'; r2.style.opacity = '0.65'; }
            else if (state === 'STANDBY') { r1.style.opacity = '0.25'; r2.style.opacity = '0.0'; }
            else if (state === 'DEEP_SLEEP') { r1.style.opacity = '0.0'; r2.style.opacity = '0.0'; }
            else if (state === 'OFFLINE') { r1.style.opacity = '0.1'; r2.style.opacity = '0.0'; }
        }

        setInterval(() => {
            fetch('/status').then(r => r.json()).then(d => updateUI(d.state)).catch(() => {});
        }, 1000);
    </script>
</body>
</html>
"""


# --- Wi-Fi Access Point Initialization ---
def init_access_point():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    # Configure open Wi-Fi network with IP 192.168.4.1
    ap.config(essid="PPC-Blue-Charger-Sim", authmode=network.AUTH_OPEN)
    while not ap.active():
        time.sleep(0.1)
    print(f"[AP ONLINE] SSID: 'PPC-Blue-Charger-Sim' | IP: {ap.ifconfig()[0]}")


# --- Non-Blocking Web Server ---
def main():
    global current_state, wake_target, wake_start_ms, wake_duration_s

    init_access_point()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", 80))
    s.listen(3)
    s.setblocking(False)

    print("[SERVER] Webapp available at http://192.168.4.1")

    while True:
        now = time.ticks_ms()

        # Handle WAKING transition progression
        if current_state == "WAKING":
            elapsed = time.ticks_diff(now, wake_start_ms) / 1000.0
            # Breathing pulse on Red LED 1 during wake
            pulse_duty = int(200 + 400 * (0.5 + 0.5 * (time.ticks_ms() % 1000) / 1000.0))
            pwm_red1.duty(pulse_duty)

            # Red LED 2 stays OFF until pre-charge complete
            if elapsed >= (wake_duration_s - 1.5):
                pwm_red2.duty(400)
            else:
                pwm_red2.duty(0)

            if elapsed >= wake_duration_s:
                current_state = wake_target
                d1, d2 = DUTY_MAP.get(current_state, (0, 0))
                set_led_pwm(d1, d2)
                print(f"[WAKE COMPLETE] -> {current_state}")

        # Accept incoming HTTP requests
        try:
            client, addr = s.accept()
            client.settimeout(0.5)
            req = client.recv(1024).decode("utf-8")

            if "GET /state?target=" in req:
                target = req.split("GET /state?target=")[1].split(" ")[0].split("&")[0]
                res = apply_state(target, cause="webapp")
                resp_body = ujson.dumps(res)
                client.send("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + resp_body)
            elif "GET /status" in req:
                d1 = pwm_red1.duty()
                d2 = pwm_red2.duty()
                res = {
                    "state": current_state,
                    "red1_duty": d1,
                    "red2_duty": d2,
                    "blue_logo": "100%",
                }
                client.send(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + ujson.dumps(res)
                )
            else:
                # Serve the Webapp UI
                client.send("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + HTML_PAGE)
            client.close()
        except OSError:
            pass  # No request waiting

        time.sleep(0.02)  # 50 Hz loop


if __name__ == "__main__":
    main()
