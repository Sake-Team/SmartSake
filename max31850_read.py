import glob
import time

# Base directory for 1-Wire devices
W1_BASE = "/sys/bus/w1/devices"

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

    temp_milli_c = int(lines[1][temp_pos+2:])
    return temp_milli_c / 1000.0


if __name__ == "__main__":
    devices = discover_devices()

    if not devices:
        print("No MAX31850K devices found.")
        exit()

    print("Discovered devices:")
    for d in devices:
        print(" -", d.split("/")[-1])

    print("\nReading temperatures...\n")

    while True:
        for d in devices:
            try:
                temp_c = read_temp_c(d)
                print(f"{d.split('/')[-1]} : {temp_c:.2f} °C")
            except Exception as e:
                print(f"{d.split('/')[-1]} : ERROR ({e})")

        print("-" * 40)
        time.sleep(2)
