/*
 * ESP32 Physical EV Charger Simulator - Arduino IDE Sketch (.ino)
 * 
 * Hardware Setup:
 * - ESP32 DevKit / NodeMCU-32S (Powered via 5V USB)
 * - GPIO 23 -> 330 Ohm -> Blue LED (PPC Logo: Always 100% ON)
 * - GPIO 18 -> 330 Ohm -> Red LED 1 (Important Components: HMI/RFID/Holster)
 * - GPIO 19 -> 330 Ohm -> Red LED 2 (Rest of Components: Power Stacks/Cooling)
 * 
 * How to Upload in Arduino IDE:
 * 1. Tools -> Board -> esp32 -> "ESP32 Dev Module" (or your specific ESP32 board).
 * 2. Tools -> Port -> Select your ESP32 COM port.
 * 3. Click Upload.
 * 4. Connect phone/laptop to Wi-Fi: "PPC-Blue-Charger-Sim" and go to:
 *    http://192.168.4.1
 */

#include <WiFi.h>
#include <WebServer.h>

// --- Pin Assignments ---
const int PIN_BLUE_LOGO = 23;      // Always 100% ON
const int PIN_RED_IMPORTANT = 18;  // Red LED 1: HMI Screen, RFID, Holster
const int PIN_RED_REST = 19;       // Red LED 2: AC/DC Power Modules, Cooling

// --- Web Server on Port 80 ---
WebServer server(80);

// --- State Machine ---
enum State { CHARGING, IDLE, STANDBY, DEEP_SLEEP, WAKING, OFFLINE };
State currentState = IDLE;
State wakeTarget = IDLE;
unsigned long wakeStartMs = 0;
float wakeDurationS = 0.0;

const float STANDBY_WAKE_S = 1.5;
const float DEEP_SLEEP_WAKE_S = 5.5;

// --- PWM Duty Cycles (0 - 255 for analogWrite) ---
// CHARGING: Both Bright (255, 255)
// IDLE: Both Dimmed a little (~65% = 166, 166)
// STANDBY: Red 1 Dimmed more (~25% = 64), Red 2 OFF (0)
// DEEP_SLEEP: Both OFF (0, 0)
// OFFLINE: Both OFF (0, 0)
int red1Duty = 166;
int red2Duty = 166;

String stateToString(State s) {
  switch (s) {
    case CHARGING:   return "CHARGING";
    case IDLE:       return "IDLE";
    case STANDBY:    return "STANDBY";
    case DEEP_SLEEP: return "DEEP_SLEEP";
    case WAKING:     return "WAKING";
    case OFFLINE:    return "OFFLINE";
    default:         return "UNKNOWN";
  }
}

void updateOutputs() {
  // Blue LED is ALWAYS 100% ON across all states
  digitalWrite(PIN_BLUE_LOGO, HIGH);

  switch (currentState) {
    case CHARGING:
      red1Duty = 255;
      red2Duty = 255;
      break;
    case IDLE:
      red1Duty = 166;
      red2Duty = 166;
      break;
    case STANDBY:
      red1Duty = 64;
      red2Duty = 0;
      break;
    case DEEP_SLEEP:
      red1Duty = 0;
      red2Duty = 0;
      break;
    case OFFLINE:
      red1Duty = 0;
      red2Duty = 0;
      break;
    case WAKING:
      // Handled dynamically in loop()
      return;
  }

  analogWrite(PIN_RED_IMPORTANT, red1Duty);
  analogWrite(PIN_RED_REST, red2Duty);
}

bool applyState(String targetStr) {
  targetStr.toUpperCase();
  State target;
  if (targetStr == "CHARGING") target = CHARGING;
  else if (targetStr == "IDLE") target = IDLE;
  else if (targetStr == "STANDBY") target = STANDBY;
  else if (targetStr == "DEEP_SLEEP") target = DEEP_SLEEP;
  else if (targetStr == "OFFLINE") target = OFFLINE;
  else return false;

  if (currentState == CHARGING && (target == STANDBY || target == DEEP_SLEEP)) return false;
  if (currentState == WAKING && (target == STANDBY || target == DEEP_SLEEP)) return false;
  if (currentState == target) return false;

  // Wake transitions
  if ((currentState == STANDBY || currentState == DEEP_SLEEP) && (target == IDLE || target == CHARGING)) {
    wakeDurationS = (currentState == DEEP_SLEEP) ? DEEP_SLEEP_WAKE_S : STANDBY_WAKE_S;
    wakeTarget = target;
    wakeStartMs = millis();
    currentState = WAKING;
    Serial.println("[TRANSITION] -> WAKING (" + String(wakeDurationS) + "s)");
    return true;
  }

  currentState = target;
  updateOutputs();
  Serial.println("[STATE] -> " + stateToString(currentState));
  return true;
}

// --- Embedded Webapp HTML ---
const char HTML_PAGE[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
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
            Arduino IDE Firmware
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
)rawliteral";

void handleRoot() {
  server.send(200, "text/html", HTML_PAGE);
}

void handleState() {
  if (server.hasArg("target")) {
    String target = server.arg("target");
    bool ok = applyState(target);
    String json = "{\"ok\":" + String(ok ? "true" : "false") + ",\"state\":\"" + stateToString(currentState) + "\"}";
    server.send(200, "application/json", json);
  } else {
    server.send(400, "application/json", "{\"ok\":false,\"reason\":\"missing_target\"}");
  }
}

void handleStatus() {
  String json = "{\"state\":\"" + stateToString(currentState) + "\",\"red1_duty\":" + String(red1Duty) + ",\"red2_duty\":" + String(red2Duty) + ",\"blue_logo\":\"100%\"}";
  server.send(200, "application/json", json);
}

void setup() {
  Serial.begin(115200);

  // Setup GPIO pins
  pinMode(PIN_BLUE_LOGO, OUTPUT);
  pinMode(PIN_RED_IMPORTANT, OUTPUT);
  pinMode(PIN_RED_REST, OUTPUT);

  // Initial State: IDLE
  updateOutputs();

  // Setup Wi-Fi Access Point (SSID: PPC-Blue-Charger-Sim, Open)
  WiFi.softAP("PPC-Blue-Charger-Sim");
  IPAddress IP = WiFi.softAPIP();
  Serial.println("");
  Serial.println("[AP ONLINE] SSID: 'PPC-Blue-Charger-Sim'");
  Serial.print("[AP IP] http://");
  Serial.println(IP);

  // Setup Web Server Routes
  server.on("/", handleRoot);
  server.on("/state", handleState);
  server.on("/status", handleStatus);
  server.begin();
  Serial.println("[SERVER] HTTP server started on port 80");
}

void loop() {
  server.handleClient();

  // Handle WAKING progression
  if (currentState == WAKING) {
    float elapsedS = (millis() - wakeStartMs) / 1000.0;
    
    // Breathing pulse on Red LED 1 (approx 50 to 200 duty)
    int pulseDuty = (int)(128 + 100 * sin(elapsedS * 6.28));
    analogWrite(PIN_RED_IMPORTANT, pulseDuty);

    // Red LED 2 stays OFF until pre-charge is near complete
    if (elapsedS >= (wakeDurationS - 1.5)) {
      analogWrite(PIN_RED_REST, 100);
    } else {
      analogWrite(PIN_RED_REST, 0);
    }

    if (elapsedS >= wakeDurationS) {
      currentState = wakeTarget;
      updateOutputs();
      Serial.println("[WAKE COMPLETE] -> " + stateToString(currentState));
    }
  }

  delay(10);
}
