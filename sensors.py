"""Thermocouple and SHT30 sensor helpers for SmartSake Pi hardware."""
import glob

# Base directory for 1-Wire devices
W1_BASE = "/sys/bus/w1/devices"
# Maximum number of thermocouples to read
MAX_THERMOCOUPLES = 6

# ── SHT30 support (requires adafruit-blinka + adafruit-circuitpython-sht31d) ──
try:
    import board as _board
    import adafruit_sht31d as _adafruit_sht31d
    _SHT_LIBS = True
except ImportError as _e:
    _SHT_LIBS = False
    print(f"[sensors] SHT30 libs not available ({_e})")


def init_sht30():
    """Initialize the SHT30/SHT31D sensor over I2C. Returns None if unavailable."""
    if not _SHT_LIBS:
        return None
    i2c = _board.I2C()
    return _adafruit_sht31d.SHT31D(i2c)


def read_sht30(sensor):
    """Read (temp_c, humidity_rh) from SHT30."""
    return sensor.temperature, sensor.relative_humidity


_cached_devices = None
_cache_time = 0
_CACHE_TTL = 120  # re-scan 1-Wire bus every 2 minutes, not every cycle

def discover_devices():
    """Discover MAX31850K devices on the 1-Wire bus (appear as '3b-*').
    Caches the result for _CACHE_TTL seconds since devices only change on plug/unplug.
    Does NOT cache empty results — retries every call until probes appear."""
    import time as _time
    global _cached_devices, _cache_time
    now = _time.monotonic()
    if _cached_devices and (now - _cache_time) < _CACHE_TTL:
        return _cached_devices
    _cached_devices = sorted(glob.glob(f"{W1_BASE}/3b-*"))[:MAX_THERMOCOUPLES]
    _cache_time = now
    return _cached_devices


def read_temp_c(device_folder, timeout_s=3):
    """Read temperature in Celsius from a MAX31850K device.

    Uses a timeout to prevent the sensor loop from hanging if the 1-Wire bus locks up.
    """
    import signal

    device_file = f"{device_folder}/w1_slave"

    def _alarm_handler(signum, frame):
        raise TimeoutError(f"1-Wire read timed out ({device_file})")

    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(timeout_s)
    try:
        with open(device_file, "r") as f:
            lines = f.readlines()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    if not lines[0].strip().endswith("YES"):
        raise RuntimeError("CRC check failed")
    temp_pos = lines[1].find("t=")
    if temp_pos == -1:
        raise RuntimeError("Temperature data not found")
    temp_milli_c = int(lines[1][temp_pos + 2:])
    return temp_milli_c / 1000.0


def format_device_id(device_folder: str) -> str:
    return device_folder.split("/")[-1]
