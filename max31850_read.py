import glob
import time

# Base directory for 1-Wire devices
W1_BASE = "/sys/bus/w1/devices"

# Maximum number of thermocouples to read
MAX_THERMOCOUPLES = 6

def discover_devices():
    """
    Discover MAX31850K devices.
    They usually appear as '3b-xxxxxxxxxxxx'
    """
    return sorted(glob.glob(f"{W1_BASE}/3b-*"))

def read_temp_c(device_folder):
    """
    Read temperature in Celsius from a MAX31850K device.
    """
    device_file = f"{device_folder}/w1_slave"

    with open(device_file, "r") as f:
        lines = f.readlines()

    # First line should end with YES if CRC is valid
    if not lines[0].strip().endswith("YES"):
        raise RuntimeError("CRC check failed")

    # Temperature is on second line after 't='
    temp_pos = lines[1].find("t=")
    if temp_pos == -1:
        raise RuntimeError("Temperature data not found")

    temp_milli_c = int(lines[1][temp_pos + 2 :])
    return temp_milli_c / 1000.0

def format_device_id(device_folder: str) -> str:
    return device_folder.split("/")[-1]

if __name__ == "__main__":
    print(f"Looking for up to {MAX_THERMOCOUPLES} MAX31850K thermocouples...")

    # Keep a stable mapping from device_id -> TC#
    device_id_to_channel = {}
    next_channel = 1

    while True:
        devices = discover_devices()

        # Assign channels to newly discovered devices (stable across loops)
        for d in devices:
            device_id = format_device_id(d)
            if device_id not in device_id_to_channel:
                if next_channel <= MAX_THERMOCOUPLES:
                    device_id_to_channel[device_id] = next_channel
                    next_channel += 1

        # Choose devices that have a channel assigned, sorted by channel number
        assigned = []
        for d in devices:
            device_id = format_device_id(d)
            ch = device_id_to_channel.get(device_id)
            if ch is not None and ch <= MAX_THERMOCOUPLES:
                assigned.append((ch, d))
        assigned.sort(key=lambda x: x[0])

        if not assigned:
            print("No MAX31850K devices found (or none assigned).")
            print("-" * 40)
            time.sleep(2)
            continue

        # Display mapping occasionally (every loop is fine; change if noisy)
        print("Thermocouple mapping:")
        for ch, d in assigned:
            print(f" - TC{ch}: {format_device_id(d)}")

        print("\nReading temperatures...\n")
        for ch, d in assigned:
            device_id = format_device_id(d)
            try:
                temp_c = read_temp_c(d)
                print(f"TC{ch} ({device_id}) : {temp_c:.2f} °C")
            except Exception as e:
                print(f"TC{ch} ({device_id}) : ERROR ({e})")

        print("-" * 40)
        time.sleep(2)