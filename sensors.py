"""Shared sensor helpers for SmartSake Pi hardware."""
import glob
import board
import adafruit_sht31d

# Base directory for 1-Wire devices
W1_BASE = "/sys/bus/w1/devices"
# Maximum number of thermocouples to read
MAX_THERMOCOUPLES = 6

def init_sht30():
    """Initialize the SHT30 sensor over I2C."""
    i2c = board.I2C()
    return adafruit_sht31d.SHT31D(i2c)

def read_sht30(sensor):
    """Read temperature and humidity from SHT30."""
    return sensor.temperature, sensor.relative_humidity

def discover_devices():
    """
    Discover MAX31850K devices.
    They usually appear as '3b-xxxxxxxxxxxx'
    """
    return sorted(glob.glob(f"{W1_BASE}/3b-*"))[:MAX_THERMOCOUPLES]

def read_temp_c(device_folder):
    """
    Read temperature in Celsius from a MAX31850K device.
    """
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
