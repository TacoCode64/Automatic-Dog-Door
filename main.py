import time
import threading
import logging
import json
import os
import subprocess
from collections import deque
from datetime import datetime

import RPi.GPIO as GPIO
from bluepy.btle import Scanner, DefaultDelegate
from flask import Flask, render_template, jsonify, send_from_directory
import requests

CONFIG = {
    "BEACON_MAC": "06:05:04:03:02:01",

    "RSSI_A": -55,
    "RSSI_N": 2.8,

    "TRIGGER_DISTANCE_M": 1.5,

    "DOOR_OPEN_HOLD_S": 5,

    "ACTUATOR_TRAVEL_S": 7.0,

    "STEPPER_STEPS": 200,

    "COOLDOWN_S": 15,

    "NTFY_TOPIC": "my-pet-door-12345",

    "VIDEO_DIR": "/home/pi/petdoor_videos",

    "VIDEO_CLIP_S": 15,

    "LOG_LEVEL": "INFO",
}

RELAY_EXTEND_PIN  = 14
RELAY_RETRACT_PIN = 15

STEPPER_PINS = [17, 27, 22, 23]

RELAY_DEAD_TIME_S = 0.1

FULL_STEP_SEQ = [
    [1, 0, 1, 0],
    [0, 1, 1, 0],
    [0, 1, 0, 1],
    [1, 0, 0, 1],
]

logging.basicConfig(
    level=getattr(logging, CONFIG["LOG_LEVEL"]),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/vickypi/petdoor.log"),
    ],
)
log = logging.getLogger("petdoor")

state = {
    "door_open": False,
    "last_open": None,
    "dog_detected": False,
    "dog_distance_m": None,
    "events": [],
    "video_clips": [],
}
state_lock = threading.Lock()

os.makedirs(CONFIG["VIDEO_DIR"], exist_ok=True)

def gpio_setup():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(RELAY_EXTEND_PIN,  GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(RELAY_RETRACT_PIN, GPIO.OUT, initial=GPIO.LOW)
    for pin in STEPPER_PINS:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    log.info("GPIO initialised.")


def gpio_cleanup():
    GPIO.cleanup()
    log.info("GPIO cleaned up.")

def _step(step_index: int):
    """Apply one half-step."""
    for i, pin in enumerate(STEPPER_PINS):
        GPIO.output(pin, FULL_STEP_SEQ[step_index % 4][i])


def stepper_rotate(steps: int, delay_s: float = 0.002, reverse: bool = False):
    """
    Rotate the stepper motor a given number of half-steps.
    Positive steps = clockwise (tightens wire = turns handle down).
    Set reverse=True to return the handle to neutral.
    """
    direction = -1 if reverse else 1
    for i in range(steps):
        _step(i * direction)
        time.sleep(delay_s)
    for pin in STEPPER_PINS:
        GPIO.output(pin, GPIO.LOW)

def _relays_off():
    """De-energise both relays. Safe idle state — actuator unpowered."""
    GPIO.output(RELAY_EXTEND_PIN,  GPIO.LOW)
    GPIO.output(RELAY_RETRACT_PIN, GPIO.LOW)


def actuator_extend():
    """
    Extend the linear actuator (open the door).
    Energises RELAY_EXTEND only. RELAY_RETRACT must already be LOW.
    """
    _relays_off()
    time.sleep(RELAY_DEAD_TIME_S)
    GPIO.output(RELAY_EXTEND_PIN, GPIO.HIGH)
    log.debug("Relay EXTEND ON — actuator extending.")


def actuator_retract():
    """
    Retract the linear actuator (close the door).
    De-energises RELAY_EXTEND, waits for dead-time, then energises
    RELAY_RETRACT, reversing polarity to the actuator.
    """
    _relays_off()
    time.sleep(RELAY_DEAD_TIME_S)
    GPIO.output(RELAY_RETRACT_PIN, GPIO.HIGH)
    log.debug("Relay RETRACT ON — actuator retracting.")


def actuator_stop():
    """De-energise both relays — actuator coasts to stop."""
    _relays_off()
    log.debug("Both relays OFF — actuator stopped.")

def record_clip(filename: str, duration_s: int = CONFIG["VIDEO_CLIP_S"]):
    """Record a video clip using libcamera-vid (Raspberry Pi camera)."""
    path = os.path.join(CONFIG["VIDEO_DIR"], filename)
    cmd = [
        "libcamera-vid",
        "-t", str(duration_s * 1000),
        "-o", path,
        "--width", "1280",
        "--height", "720",
        "--framerate", "15",
        "--nopreview",
    ]
    try:
        subprocess.Popen(cmd)
        log.info(f"Recording clip: {path}")
        with state_lock:
            state["video_clips"].append(filename)
            if len(state["video_clips"]) > 50:
                state["video_clips"].pop(0)
    except Exception as e:
        log.error(f"Camera error: {e}")

def send_notification(message: str):
    topic = CONFIG.get("NTFY_TOPIC", "")
    if not topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": "Pet Door", "Priority": "default"},
            timeout=5,
        )
        log.info(f"Notification sent: {message}")
    except Exception as e:
        log.warning(f"Notification failed: {e}")

def open_door_sequence():
    """Full open-then-close sequence. Runs in its own thread."""
    now = datetime.now()
    with state_lock:
        state["door_open"] = True
        state["last_open"] = now.isoformat()
        state["events"].insert(0, {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "event": "Door opened",
        })
        if len(state["events"]) > 100:
            state["events"].pop()

    log.info("--- Opening sequence START ---")

    log.info("Stepper: rotating to open latch...")
    stepper_rotate(CONFIG["STEPPER_STEPS"])
    time.sleep(0.3)

    log.info("Actuator EXTENDING — door opening...")
    actuator_extend()

    time.sleep(CONFIG["ACTUATOR_TRAVEL_S"])

    actuator_stop()

    clip_name = now.strftime("clip_%Y%m%d_%H%M%S.h264")
    record_clip(clip_name)

    send_notification("🐾 Your pet is at the door — door is opening!")

    log.info(f"Holding door open for {CONFIG['DOOR_OPEN_HOLD_S']}s...")
    time.sleep(CONFIG["DOOR_OPEN_HOLD_S"])

    log.info("Actuator RETRACTING — door closing...")
    actuator_retract()

    time.sleep(CONFIG["ACTUATOR_TRAVEL_S"])

    actuator_stop()
    time.sleep(0.2)

    log.info("Stepper: returning handle to neutral...")
    stepper_rotate(CONFIG["STEPPER_STEPS"], reverse=True)

    with state_lock:
        state["door_open"] = False
        state["events"].insert(0, {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": "Door closed",
        })

    log.info("--- Opening sequence END ---")


class BLEDelegate(DefaultDelegate):
    def __init__(self):
        super().__init__()

    def handleDiscovery(self, dev, isNewDev, isNewData):
        pass


def rssi_to_distance(rssi: int) -> float:
    """
    Convert RSSI (dBm) to distance (metres).
    Formula: d = 10 ^ ((A - RSSI) / (10 * N))
    where A = RSSI at 1 m, N = path-loss exponent.
    """
    A = CONFIG["RSSI_A"]
    N = CONFIG["RSSI_N"]
    return 10 ** ((A - rssi) / (10 * N))

def ble_scan_loop():
    scanner = Scanner().withDelegate(BLEDelegate())
    target_mac = CONFIG["BEACON_MAC"].lower()
    trigger_dist = CONFIG["TRIGGER_DISTANCE_M"]
    cooldown = CONFIG["COOLDOWN_S"]

    rssi_window = deque(maxlen=3)

    last_trigger_time = 0.0
    door_thread = None

    log.info(f"BLE scan loop started. Target MAC: {target_mac}")

    while True:
        try:
            devices = scanner.scan(1.0)
            beacon_found = False
            
            for dev in devices:
                if dev.addr.lower() == target_mac:
                    beacon_found = True
                    rssi_window.append(dev.rssi)

                    if len(rssi_window) < 3:
                        log.debug(f"Beacon RSSI={dev.rssi} dBm — building average ({len(rssi_window)}/3)")
                        continue

                    avg_rssi = sum(rssi_window) / len(rssi_window)
                    dist = rssi_to_distance(avg_rssi)

                    with state_lock:
                        state["dog_detected"] = True
                        state["dog_distance_m"] = round(dist, 2)

                    log.debug(f"Beacon avg RSSI={avg_rssi:.1f} dBm  dist≈{dist:.2f}m")

                    now = time.time()
                    door_busy = door_thread and door_thread.is_alive()
                    since_last = now - last_trigger_time

                    if dist <= trigger_dist and not door_busy and since_last >= cooldown:
                        log.info(f"Dog within {dist:.2f}m — triggering door.")
                        last_trigger_time = now
                        door_thread = threading.Thread(target=open_door_sequence, daemon=True)
                        door_thread.start()

            if not beacon_found:
                with state_lock:
                    state["dog_detected"] = False
                    state["dog_distance_m"] = None

        except Exception as e:
            log.error(f"BLE scan error: {e}")
            time.sleep(2)
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pet Door Dashboard</title>
<style>
  body { font-family: Arial, sans-serif; background:#f4f4f4; margin:0; padding:20px; }
  h1   { color:#333; }
  .card { background:#fff; border-radius:8px; padding:16px; margin-bottom:16px;
          box-shadow:0 2px 4px rgba(0,0,0,.1); }
  .status-open   { color:#e74c3c; font-weight:bold; }
  .status-closed { color:#27ae60; font-weight:bold; }
  table { border-collapse:collapse; width:100%; }
  th,td { text-align:left; padding:8px; border-bottom:1px solid #ddd; }
  th    { background:#f0f0f0; }
</style>
<script>
  function refresh() {
    fetch('/api/state').then(r=>r.json()).then(data=>{
      document.getElementById('door-status').textContent =
        data.door_open ? 'OPEN' : 'CLOSED';
      document.getElementById('door-status').className =
        data.door_open ? 'status-open' : 'status-closed';
      document.getElementById('dog-dist').textContent =
        data.dog_distance_m !== null ? data.dog_distance_m + ' m' : 'Not detected';
      let rows = data.events.slice(0,20).map(e=>
        `<tr><td>${e.time}</td><td>${e.event}</td></tr>`).join('');
      document.getElementById('events-body').innerHTML = rows;
    });
  }
  setInterval(refresh, 2000);
  window.onload = refresh;
</script>
</head>
<body>
<h1>🐾 Pet Door Dashboard</h1>
<div class="card">
  <h2>Door Status: <span id="door-status">—</span></h2>
  <p>Dog distance: <strong><span id="dog-dist">—</span></strong></p>
</div>
<div class="card">
  <h2>Recent Events</h2>
  <table>
    <thead><tr><th>Time</th><th>Event</th></tr></thead>
    <tbody id="events-body"></tbody>
  </table>
</div>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return DASHBOARD_HTML

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify(dict(state))

@app.route("/videos/<path:filename>")
def serve_video(filename):
    return send_from_directory(CONFIG["VIDEO_DIR"], filename)

def main():
    gpio_setup()

    ble_thread = threading.Thread(target=ble_scan_loop, daemon=True)
    ble_thread.start()

    log.info("Pet Door controller running. Dashboard at http://localhost:5000")

    try:
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        actuator_stop()
        gpio_cleanup()


if __name__ == "__main__":
    main()
