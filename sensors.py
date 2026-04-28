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


def discover_devices():
    """Discover MAX31850K devices on the 1-Wire bus (appear as '3b-*')."""
    return sorted(glob.glob(f"{W1_BASE}/3b-*"))[:MAX_THERMOCOUPLES]


def read_temp_c(device_folder):
    """Read temperature in Celsius from a MAX31850K device."""
    device_file = f"{device_folder}/w1_slave"
    with open(device_file, "r") as f:
        lines = f.readlines()
    if not lines[0].strip().endswith("YES"):
        raise RuntimeError("CRC check failed")
    temp_pos = lines[1].find("t=")
    if temp_pos == -1:
        raise RuntimeError("Temperature data not found")
    temp_milli_c = int(lines[1][temp_pos + 2:])
    return temp_milli_c / 1000.0


def format_device_id(device_folder: str) -> str:
    return device_folder.split("/")[-1]
