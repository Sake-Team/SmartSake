import glob
import time
import csv
import os
import json
import threading
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

from sensors import init_sht30, read_sht30, discover_devices, read_temp_c, format_device_id, MAX_THERMOCOUPLES
from load_cell_hx711 import HX711, HX711_DAT_PIN, HX711_CLK_PIN, TARE_OFFSET, CALIBRATION_FACTOR, UNITS, SAMPLES_PER_READ, log_weight
from pid_controller import RelayController

CSV_FILE = "sensor_data.csv"
JSON_FILE = "sensor_latest.json"
MAX_CSV_ROWS = 43200  # ~24 hrs at 2-second interval

# Shared state updated by background threads
weight_state = {'kg': None, 'raw': None}
relay_ctrl = None  # set in __main__ before threads start


# -----------------------------------------------
# HTTP SERVER (GET static files + POST zone config)
# -----------------------------------------------
class SakeHTTPHandler(SimpleHTTPRequestHandler):
    """Serves static files and handles zone config POST requests."""

    def log_message(self, format, *args):
        pass  # suppress per-request console noise

    def do_POST(self):
        if urlparse(self.path).path == '/update_zone':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                data = json.loads(body)

                zone_num = int(data.get('zone', 0))
                setpoint_c = data.get('setpoint_c')
                mode = data.get('mode')
                manual_state = data.get('manual_state')

                if relay_ctrl and zone_num in range(1, 7):
                    relay_ctrl.update_zone(
                        zone_num,
                        setpoint_c=float(setpoint_c) if setpoint_c is not None else None,
                        mode=mode,
                        manual_state=bool(manual_state) if manual_state is not None else None
                    )
                    relay_ctrl.save_config()
                    self._respond(200, {'ok': True})
                else:
                    self._respond(400, {'error': 'invalid zone or relay not initialized'})
            except Exception as e:
                self._respond(500, {'error': str(e)})
        else:
            self._respond(404, {'error': 'not found'})

    def _respond(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)


def start_web_server(port=8080):
    """Serve the current directory over HTTP in a background thread."""
    httpd = HTTPServer(("", port), SakeHTTPHandler)
    print(f"Web server running at http://<pi-ip>:{port}")
    httpd.serve_forever()


# -----------------------------------------------
# HX711 LOAD CELL THREAD
# -----------------------------------------------
def run_hx711_thread():
    """Background thread: reads HX711 every 0.5 s, updates weight_state."""
    try:
        hx = HX711(HX711_DAT_PIN, HX711_CLK_PIN, gain=128)
        hx._offset = TARE_OFFSET
        hx.set_scale(CALIBRATION_FACTOR)
        print("HX711 initialized.")

        while True:
            try:
                weight, raw_avg = hx.get_weight(samples=SAMPLES_PER_READ, units=UNITS)
                weight_state['kg'] = weight
                weight_state['raw'] = raw_avg
                log_weight(weight, UNITS)
            except Exception as e:
                print(f"HX711 read error: {e}")
            time.sleep(0.5)
    except Exception as e:
        print(f"HX711 thread failed to initialize: {e} -- running without scale")


# -----------------------------------------------
# DATA WRITERS
# -----------------------------------------------
def write_csv(timestamp, sht_temp, sht_humidity, tc_readings):
    """Append a row to the CSV file, rotating when MAX_CSV_ROWS is exceeded."""
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            headers = ["timestamp", "sht30_temp_c", "sht30_humidity_rh"]
            headers += [f"TC{ch}_temp_c" for ch, _ in tc_readings]
            writer.writerow(headers)
        row = [timestamp, sht_temp, sht_humidity]
        row += [f"{temp:.2f}" if temp is not None else "ERROR" for _, temp in tc_readings]
        writer.writerow(row)

    try:
        with open(CSV_FILE, "r") as f:
            line_count = sum(1 for _ in f)
        if line_count > MAX_CSV_ROWS:
            datestr = datetime.now().strftime("%Y%m%d_%H%M%S")
            os.rename(CSV_FILE, f"{CSV_FILE}.{datestr}.bak")
    except Exception as e:
        print(f"CSV rotation error: {e}")


def write_json(timestamp, sht_temp, sht_humidity, tc_readings):
    """Write latest readings to sensor_latest.json atomically."""
    data = {
        "timestamp": timestamp,
        "sht30": {
            "temp_c": round(sht_temp, 2) if sht_temp is not None else None,
            "humidity_rh": round(sht_humidity, 2) if sht_humidity is not None else None
        },
        "thermocouples": {
            f"TC{ch}": round(temp, 2) if temp is not None else None
            for ch, temp in tc_readings
        },
        "weight_kg": round(weight_state['kg'], 4) if weight_state['kg'] is not None else None,
        "zones": relay_ctrl.get_zone_states() if relay_ctrl else {}
    }
    tmp_file = JSON_FILE + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump(data, f)
    os.replace(tmp_file, JSON_FILE)


# -----------------------------------------------
# MAIN
# -----------------------------------------------
if __name__ == "__main__":
    # Start web server
    server_thread = threading.Thread(target=start_web_server, daemon=True)
    server_thread.start()

    # Start HX711 weight thread (fails gracefully if scale not attached)
    hx_thread = threading.Thread(target=run_hx711_thread, daemon=True)
    hx_thread.start()

    # Start PID / relay controller
    try:
        relay_ctrl = RelayController()
        relay_ctrl.load_config()
        relay_thread = threading.Thread(target=relay_ctrl.run_relay_thread, daemon=True)
        relay_thread.start()
        print("Relay/PID controller started.")
    except Exception as e:
        print(f"Relay/PID init failed: {e} -- running without relay control")

    # Initialize SHT30
    try:
        sht30 = init_sht30()
        print("SHT30 initialized.")
    except Exception as e:
        sht30 = None
        print(f"SHT30 init failed: {e}")

    device_id_to_channel = {}
    next_channel = 1

    while True:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        devices = discover_devices()

        for d in devices:
            device_id = format_device_id(d)
            if device_id not in device_id_to_channel:
                if next_channel <= MAX_THERMOCOUPLES:
                    device_id_to_channel[device_id] = next_channel
                    next_channel += 1

        assigned = []
        for d in devices:
            device_id = format_device_id(d)
            ch = device_id_to_channel.get(device_id)
            if ch is not None and ch <= MAX_THERMOCOUPLES:
                assigned.append((ch, d))
        assigned.sort(key=lambda x: x[0])

        # Read SHT30
        sht_temp, sht_humidity = None, None
        if sht30:
            try:
                sht_temp, sht_humidity = read_sht30(sht30)
                print(f"SHT30 -- Temp: {sht_temp:.2f} °C | Humidity: {sht_humidity:.2f} %RH")
            except Exception as e:
                print(f"SHT30 -- ERROR ({e})")

        # Read thermocouples
        tc_readings = []
        for ch, d in assigned:
            try:
                temp_c = read_temp_c(d)
                tc_readings.append((ch, temp_c))
                print(f"TC{ch}: {temp_c:.2f} °C")
            except Exception as e:
                tc_readings.append((ch, None))
                print(f"TC{ch}: ERROR ({e})")

        # Update PID with latest readings
        if relay_ctrl:
            relay_ctrl.update_all(tc_readings)

        if weight_state['kg'] is not None:
            print(f"Weight: {weight_state['kg']:.3f} {UNITS}  (raw: {weight_state['raw']:.0f})")

        write_csv(timestamp, sht_temp, sht_humidity, tc_readings)
        write_json(timestamp, sht_temp, sht_humidity, tc_readings)

        if relay_ctrl:
            relay_ctrl.save_config()

        print("-" * 40)
        time.sleep(2)
