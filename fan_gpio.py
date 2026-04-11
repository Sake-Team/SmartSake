"""
fan_gpio.py — GPIO fan control abstraction for SmartSake.

GPIO pin numbers are BCM mode. Set FAN_PINS to match physical wiring.
When RPi.GPIO is unavailable (dev machine), all functions are no-ops.
"""

import logging

log = logging.getLogger(__name__)

# Map zone number (1-6) to BCM GPIO pin.
# Update these to match the actual hardware wiring.
FAN_PINS = {
    1: None,
    2: None,
    3: None,
    4: None,
    5: None,
    6: None,
}

_gpio_available = False
_GPIO = None


def _try_import():
    global _gpio_available, _GPIO
    try:
        import RPi.GPIO as GPIO
        _GPIO = GPIO
        _gpio_available = True
    except (ImportError, RuntimeError):
        log.warning("RPi.GPIO not available — fan GPIO control is disabled (no-op mode)")
        _gpio_available = False


def init_fans():
    """Set up GPIO output pins for all configured fan zones."""
    _try_import()
    if not _gpio_available:
        return
    _GPIO.setmode(_GPIO.BCM)
    _GPIO.setwarnings(False)
    for zone, pin in FAN_PINS.items():
        if pin is not None:
            _GPIO.setup(pin, _GPIO.OUT, initial=_GPIO.LOW)
            log.info("Fan zone %d — GPIO pin %d initialized", zone, pin)


def set_fan(zone, on):
    """Drive the GPIO pin for a zone HIGH (on) or LOW (off).

    Args:
        zone: int 1-6
        on: bool — True = fan on, False = fan off
    """
    if not _gpio_available:
        return
    pin = FAN_PINS.get(zone)
    if pin is None:
        return
    _GPIO.output(pin, _GPIO.HIGH if on else _GPIO.LOW)


def cleanup():
    """Release GPIO resources on shutdown."""
    if _gpio_available and _GPIO is not None:
        _GPIO.cleanup()
