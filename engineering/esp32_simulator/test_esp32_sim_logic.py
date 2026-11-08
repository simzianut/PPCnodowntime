"""Unit tests for the ESP32 physical simulator state and PWM logic."""

DUTY_MAP = {
    "CHARGING": (1023, 1023),
    "IDLE": (665, 665),
    "STANDBY": (256, 0),
    "DEEP_SLEEP": (0, 0),
    "OFFLINE": (0, 0),
}


class SimLogic:
    def __init__(self, initial_state: str = "IDLE"):
        self.current_state = initial_state
        self.wake_target = initial_state
        self.duty1, self.duty2 = DUTY_MAP[initial_state]
        self.chime_played_count = 0

    def play_nordic_chime(self):
        self.chime_played_count += 1

    def apply_state(self, new_state: str) -> dict:
        new_state = new_state.upper()
        if new_state not in ("IDLE", "STANDBY", "DEEP_SLEEP", "CHARGING", "OFFLINE"):
            return {"ok": False, "reason": "invalid_state"}

        prev = self.current_state
        if prev == "CHARGING" and new_state in ("STANDBY", "DEEP_SLEEP"):
            return {"ok": False, "reason": "busy_charging", "state": prev}
        if prev == "WAKING" and new_state in ("STANDBY", "DEEP_SLEEP"):
            return {"ok": False, "reason": "currently_waking", "state": prev}
        if prev == new_state:
            return {"ok": False, "reason": "already_in_state", "state": prev}

        if prev in ("STANDBY", "DEEP_SLEEP") and new_state in ("IDLE", "CHARGING"):
            self.wake_target = new_state
            self.current_state = "WAKING"
            return {"ok": True, "state": "WAKING", "wake_target": new_state}

        self.current_state = new_state
        self.duty1, self.duty2 = DUTY_MAP.get(new_state, (0, 0))
        if self.current_state == "DEEP_SLEEP":
            self.play_nordic_chime()
        return {"ok": True, "state": self.current_state}

    def complete_wake(self):
        if self.current_state == "WAKING":
            self.current_state = self.wake_target
            self.duty1, self.duty2 = DUTY_MAP.get(self.current_state, (0, 0))


def test_charging_both_bright():
    sim = SimLogic("IDLE")
    res = sim.apply_state("CHARGING")
    assert res["ok"] is True
    assert sim.current_state == "CHARGING"
    assert sim.duty1 == 1023  # Red LED 1 100%
    assert sim.duty2 == 1023  # Red LED 2 100%


def test_idle_both_dimmed():
    sim = SimLogic("CHARGING")
    res = sim.apply_state("IDLE")
    assert res["ok"] is True
    assert sim.current_state == "IDLE"
    assert sim.duty1 == 665  # ~65%
    assert sim.duty2 == 665  # ~65%


def test_standby_one_dimmed_one_off():
    sim = SimLogic("IDLE")
    res = sim.apply_state("STANDBY")
    assert res["ok"] is True
    assert sim.current_state == "STANDBY"
    assert sim.duty1 == 256  # Red LED 1 ~25%
    assert sim.duty2 == 0  # Red LED 2 OFF


def test_deep_sleep_both_off():
    sim = SimLogic("STANDBY")
    res = sim.apply_state("DEEP_SLEEP")
    assert res["ok"] is True
    assert sim.current_state == "DEEP_SLEEP"
    assert sim.duty1 == 0  # Red LED 1 OFF
    assert sim.duty2 == 0  # Red LED 2 OFF


def test_wake_from_deep_sleep_goes_to_waking():
    sim = SimLogic("DEEP_SLEEP")
    res = sim.apply_state("IDLE")
    assert res["ok"] is True
    assert sim.current_state == "WAKING"
    assert sim.wake_target == "IDLE"

    sim.complete_wake()
    assert sim.current_state == "IDLE"
    assert sim.duty1 == 665
    assert sim.duty2 == 665


def test_cannot_sleep_while_charging():
    sim = SimLogic("CHARGING")
    res = sim.apply_state("DEEP_SLEEP")
    assert res["ok"] is False
    assert res["reason"] == "busy_charging"
    assert sim.current_state == "CHARGING"
    assert sim.chime_played_count == 0


def test_deep_sleep_triggers_nordic_chime():
    sim = SimLogic("IDLE")
    res = sim.apply_state("DEEP_SLEEP")
    assert res["ok"] is True
    assert sim.current_state == "DEEP_SLEEP"
    assert sim.chime_played_count == 1
