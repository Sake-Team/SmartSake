"""PID controller and relay management for SmartSake zones.

Relay: SunFounder TS0012 (active LOW — GPIO.LOW = relay ON / fan runs).
Fans cool the zones: relay ON when temperature exceeds setpoint.
Time-proportional control converts continuous PID output (0-100%) to
relay duty cycle within a fixed PID_WINDOW_SEC period.
"""

import json
import os
import time
import threading
import RPi.GPIO as GPIO
from simple_pid import PID

# -----------------------------------------------
# RELAY GPIO PIN MAPPING (BCM numbering)
# Avoids: 2/3 (I2C), 4 (1-Wire), 5/6 (HX711)
# -----------------------------------------------
RELAY_PINS = {1: 17, 2: 27, 3: 22, 4: 23, 5: 24, 6: 25}

# -----------------------------------------------
# PID DEFAULTS
# -----------------------------------------------
DEFAULT_SETPOINT_C = 38.0
PID_WINDOW_SEC = 10        # time-proportional relay window (seconds)
PID_KP = 2.0               # proportional gain (negated internally for cooling)
PID_KI = 0.1               # integral gain
PID_KD = 0.5               # derivative gain


class ZoneController:
    """Manages PID and relay state for a single zone."""

    def __init__(self, zone_num: int, relay_pin: int, setpoint: float = DEFAULT_SETPOINT_C):
        self.zone_num = zone_num
        self.relay_pin = relay_pin
        self.setpoint = setpoint
        self.mode = 'auto'          # 'auto' or 'manual'
        self.manual_state = False   # relay state when mode == 'manual'
        self.pid_output = 0.0       # 0–100 (% duty cycle)
        self.relay_state = False    # current physical relay state

        # Negative gains: for cooling, output must be positive when temp > setpoint.
        # simple-pid computes: output = Kp*(setpoint - measurement) + ...
        # Negating Kp flips this so output > 0 when measurement > setpoint.
        self.pid = PID(
            -PID_KP, -PID_KI, -PID_KD,
            setpoint=self.setpoint,
            output_limits=(0, 100),
            sample_time=None   # we call manually from the main loop
        )

    def update(self, temp_c):
        """Called from the main sensor loop with the latest thermocouple reading."""
        if self.mode == 'auto' and temp_c is not None:
            self.pid_output = self.pid(temp_c)

    def set_setpoint(self, sp: float):
        self.setpoint = sp
        self.pid.setpoint = sp

    def set_mode(self, mode: str):
        if mode in ('auto', 'manual'):
            self.mode = mode

    def set_manual(self, state: bool):
        self.manual_state = state

    def to_dict(self) -> dict:
        return {
            'setpoint_c': self.setpoint,
            'mode': self.mode,
            'relay_state': self.relay_state,
            'pid_output': round(self.pid_output, 1)
        }


class RelayController:
    """Manages all 6 zone PID controllers and their relay outputs."""

    def __init__(self):
        self.zones = {z: ZoneController(z, pin) for z, pin in RELAY_PINS.items()}
        self._stop = threading.Event()
        self._init_gpio()

    def _init_gpio(self):
        GPIO.setmode(GPIO.BCM)
        for pin in RELAY_PINS.values():
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)  # HIGH = relay OFF at startup

    def update_all(self, tc_readings: list):
        """Call from the main sensor loop after thermocouple readings are taken."""
        for zone_num, temp in tc_readings:
            if zone_num in self.zones:
                self.zones[zone_num].update(temp)

    def update_zone(self, zone_num: int, setpoint_c: float = None, mode: str = None, manual_state: bool = None):
        """Update a single zone's config (called from HTTP POST handler)."""
        zone = self.zones.get(zone_num)
        if zone is None:
            return False
        if setpoint_c is not None:
            zone.set_setpoint(setpoint_c)
        if mode is not None:
            zone.set_mode(mode)
        if manual_state is not None:
            zone.set_manual(manual_state)
        return True

    def run_relay_thread(self):
        """Time-proportional relay control loop. Run as a daemon thread.

        Each PID_WINDOW_SEC window: relay is ON for (pid_output% of window),
        then OFF for the remainder. Checked every 100 ms.
        Active LOW: GPIO.LOW = relay ON (fan runs).
        """
        while not self._stop.is_set():
            window_start = time.time()

            while not self._stop.is_set():
                elapsed = time.time() - window_start
                if elapsed >= PID_WINDOW_SEC:
                    break

                for zone in self.zones.values():
                    if zone.mode == 'manual':
                        zone.relay_state = zone.manual_state
                    else:
                        on_duration = (zone.pid_output / 100.0) * PID_WINDOW_SEC
                        zone.relay_state = (elapsed < on_duration)

                    # Active LOW: LOW = fan ON, HIGH = fan OFF
                    GPIO.output(zone.relay_pin, GPIO.LOW if zone.relay_state else GPIO.HIGH)

                time.sleep(0.1)

    def get_zone_states(self) -> dict:
        return {z: zone.to_dict() for z, zone in self.zones.items()}

    def load_config(self, path='zone_config.json'):
        """Load persisted setpoints and modes. Silent if file missing."""
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for z_str, cfg in data.items():
                z = int(z_str)
                if z in self.zones:
                    if 'setpoint_c' in cfg:
                        self.zones[z].set_setpoint(float(cfg['setpoint_c']))
                    if 'mode' in cfg:
                        self.zones[z].set_mode(cfg['mode'])
        except Exception as e:
            print(f"[PID] Config load failed: {e}")

    def save_config(self, path='zone_config.json'):
        """Persist setpoints and modes atomically."""
        data = {z: {'setpoint_c': zone.setpoint, 'mode': zone.mode}
                for z, zone in self.zones.items()}
        tmp = path + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[PID] Config save failed: {e}")

    def cleanup(self):
        self._stop.set()
        # Turn all relays OFF before cleanup
        for pin in RELAY_PINS.values():
            try:
                GPIO.output(pin, GPIO.HIGH)
            except Exception:
                pass
        GPIO.cleanup()
